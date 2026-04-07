package nl.dylan.openobserve;

import com.sun.management.OperatingSystemMXBean;
import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import com.sun.net.httpserver.Headers;
import io.opentelemetry.api.GlobalOpenTelemetry;
import io.opentelemetry.api.common.AttributeKey;
import io.opentelemetry.api.common.Attributes;
import io.opentelemetry.api.metrics.DoubleHistogram;
import io.opentelemetry.api.metrics.LongCounter;
import io.opentelemetry.api.metrics.Meter;
import io.opentelemetry.api.trace.Span;
import io.opentelemetry.api.trace.SpanContext;
import io.opentelemetry.api.trace.SpanKind;
import io.opentelemetry.api.trace.StatusCode;
import io.opentelemetry.api.trace.Tracer;
import io.opentelemetry.api.trace.propagation.W3CTraceContextPropagator;
import io.opentelemetry.context.Context;
import io.opentelemetry.context.Scope;
import io.opentelemetry.context.propagation.TextMapGetter;
import java.io.IOException;
import java.io.OutputStream;
import java.lang.management.ManagementFactory;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Random;
import java.util.UUID;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;
import redis.clients.jedis.Jedis;

public final class App {
  private static final String SERVICE_NAME = System.getenv().getOrDefault("APP_SERVICE_NAME", "java-checkout");
  private static final String LOG_FILE = System.getenv().getOrDefault("APP_LOG_FILE", "/tmp/java-checkout.log");
  private static final String REDIS_HOST = System.getenv().getOrDefault("REDIS_HOST", "redis");
  private static final int REDIS_PORT = Integer.parseInt(System.getenv().getOrDefault("REDIS_PORT", "6379"));
  private static final int PORT = Integer.parseInt(System.getenv().getOrDefault("APP_PORT", "8081"));
  private static final int FAILURE_RATE_PERCENT = Integer.parseInt(System.getenv().getOrDefault("APP_SYNTHETIC_FAILURE_RATE_PERCENT", "26"));
  private static final long SLOW_LOG_THRESHOLD_MS = Long.parseLong(System.getenv().getOrDefault("APP_SLOW_LOG_THRESHOLD_MS", "110"));
  private static final long RESOURCE_SAMPLE_INTERVAL_MS = Long.parseLong(System.getenv().getOrDefault("APP_RESOURCE_SAMPLE_INTERVAL_MS", "10000"));
  private static final double RESOURCE_WARN_CPU_PERCENT = Double.parseDouble(System.getenv().getOrDefault("APP_RESOURCE_WARN_CPU_PERCENT", "35"));
  private static final double RESOURCE_WARN_MEMORY_MB = Double.parseDouble(System.getenv().getOrDefault("APP_RESOURCE_WARN_MEMORY_MB", "180"));
  private static final Random RANDOM = new Random();
  private static final OperatingSystemMXBean OS_BEAN = ManagementFactory.getPlatformMXBean(OperatingSystemMXBean.class);
  private static final TextMapGetter<Headers> HEADER_GETTER = new TextMapGetter<>() {
    @Override
    public Iterable<String> keys(Headers carrier) {
      return carrier.keySet();
    }

    @Override
    public String get(Headers carrier, String key) {
      if (carrier == null) {
        return null;
      }
      List<String> values = carrier.get(key);
      if (values == null || values.isEmpty()) {
        return null;
      }
      return values.get(0);
    }
  };

  private App() {
  }

  public static void main(String[] args) throws IOException {
    Tracer tracer = GlobalOpenTelemetry.getTracer(SERVICE_NAME);
    Meter meter = GlobalOpenTelemetry.getMeter(SERVICE_NAME);
    LongCounter requestCounter = meter.counterBuilder("java_requests_total").build();
    LongCounter errorCounter = meter.counterBuilder("java_errors_total").build();
    DoubleHistogram latencyHistogram = meter.histogramBuilder("java_request_duration_ms").setUnit("ms").build();
    DoubleHistogram resourceCpuHistogram = meter.histogramBuilder("java_process_cpu_percent").setUnit("percent").build();
    DoubleHistogram resourceMemoryHistogram = meter.histogramBuilder("java_process_memory_used_mb").setUnit("MB").build();
    DoubleHistogram resourceCommittedMemoryHistogram = meter.histogramBuilder("java_process_memory_committed_mb").setUnit("MB").build();

    HttpServer server = HttpServer.create(new InetSocketAddress(PORT), 0);
    server.setExecutor(Executors.newFixedThreadPool(8));
    ScheduledExecutorService resourceSampler = Executors.newSingleThreadScheduledExecutor();

    server.createContext("/healthz", exchange -> {
      String requestId = getRequestId(exchange);
      long start = System.nanoTime();
      exchange.getResponseHeaders().set("x-request-id", requestId);
      requestCounter.add(1, requestAttributes("/healthz", 200));
      writeJson(exchange, 200, "{\"ok\":true,\"service\":\"" + SERVICE_NAME + "\",\"request_id\":\"" + requestId + "\"}");
      double durationMs = (System.nanoTime() - start) / 1_000_000.0;
      latencyHistogram.record(durationMs, requestAttributes("/healthz", 200));
      recordResourceMetrics(resourceCpuHistogram, resourceMemoryHistogram, resourceCommittedMemoryHistogram, "request", "/healthz", 200, false);
      log("INFO", "java health served", Map.of("request_id", requestId, "status", 200, "duration_ms", roundDuration(durationMs)));
    });

    server.createContext("/quote", exchange -> {
      String requestId = getRequestId(exchange);
      exchange.getResponseHeaders().set("x-request-id", requestId);
      long start = System.nanoTime();
      int[] statusCode = {200};
      Context parentContext = W3CTraceContextPropagator.getInstance().extract(Context.root(), exchange.getRequestHeaders(), HEADER_GETTER);
      Span span = tracer.spanBuilder("java.quote").setParent(parentContext).setSpanKind(SpanKind.SERVER).startSpan();
      try (Scope scope = span.makeCurrent(); Jedis jedis = new Jedis(REDIS_HOST, REDIS_PORT)) {
        span.setAttribute("request.id", requestId);
        span.setAttribute("peer.service", "redis");
        runTracedStep(tracer, "java.redis.marker_set", Map.of(
            "component.layer", "infrastructure",
            "infra.kind", "cache",
            "db.system", "redis",
            "db.operation", "SET",
            "db.redis.key", "java:last_quote",
            "server.address", REDIS_HOST,
            "server.port", REDIS_PORT,
            "bottleneck.active", false
        ), () -> {
          jedis.set("java:last_quote", Instant.now().toString());
          return null;
        });
        double quote = 29.99 + RANDOM.nextInt(60);
        boolean failure = RANDOM.nextInt(100) < Math.max(0, Math.min(FAILURE_RATE_PERCENT, 100));
        boolean forceFailure = "fail=1".equals(exchange.getRequestURI().getQuery());

        if (forceFailure || failure) {
          throw new RuntimeException("java checkout quote computation failed");
        }

        String redisMarker = runTracedStep(tracer, "java.redis.marker_get", Map.of(
            "component.layer", "infrastructure",
            "infra.kind", "cache",
            "db.system", "redis",
            "db.operation", "GET",
            "db.redis.key", "java:last_quote",
            "server.address", REDIS_HOST,
            "server.port", REDIS_PORT,
            "bottleneck.active", false
        ), () -> jedis.get("java:last_quote"));
        String body = "{\"service\":\"" + SERVICE_NAME + "\",\"request_id\":\"" + requestId + "\",\"quote\":" + quote + ",\"redis_marker\":\"" + redisMarker + "\"}";
        writeJson(exchange, 200, body);
        log("INFO", "java quote served", Map.of("quote", quote, "request_id", requestId, "status", 200));
      } catch (Exception error) {
        statusCode[0] = 503;
        span.recordException(error);
        applyErrorAttributes(span, error, "java-checkout", "application");
        span.setStatus(StatusCode.ERROR, error.getMessage());
        errorCounter.add(1, requestAttributes("/quote", statusCode[0]));
        Map<String, Object> errorContext = errorContextForLog(error, requestId);
        errorContext.put("duration_ms", roundDuration((System.nanoTime() - start) / 1_000_000.0));
        errorContext.put("status", statusCode[0]);
        log("ERROR", "java quote failed", errorContext);
        writeJson(exchange, 503, "{\"error\":\"" + error.getMessage() + "\",\"service\":\"" + SERVICE_NAME + "\",\"request_id\":\"" + requestId + "\"}");
      } finally {
        double durationMs = (System.nanoTime() - start) / 1_000_000.0;
        requestCounter.add(1, requestAttributes("/quote", statusCode[0]));
        latencyHistogram.record(durationMs, requestAttributes("/quote", statusCode[0]));
        recordResourceMetrics(resourceCpuHistogram, resourceMemoryHistogram, resourceCommittedMemoryHistogram, "request", "/quote", statusCode[0], false);
        log("INFO", "java request complete", Map.of("path", "/quote", "status", statusCode[0], "duration_ms", roundDuration(durationMs), "request_id", requestId));
        if (durationMs >= SLOW_LOG_THRESHOLD_MS) {
          log("WARN", "java request exceeded slow threshold", Map.of("path", "/quote", "status", statusCode[0], "duration_ms", roundDuration(durationMs), "request_id", requestId));
        }
        span.end();
      }
    });

    log("INFO", "starting java checkout service", Map.of(
        "port", PORT,
        "failure_rate_percent", FAILURE_RATE_PERCENT,
        "slow_log_threshold_ms", SLOW_LOG_THRESHOLD_MS,
        "resource_sample_interval_ms", RESOURCE_SAMPLE_INTERVAL_MS,
        "resource_warn_cpu_percent", RESOURCE_WARN_CPU_PERCENT,
        "resource_warn_memory_mb", RESOURCE_WARN_MEMORY_MB));
    resourceSampler.scheduleAtFixedRate(() -> {
      try {
        recordResourceMetrics(resourceCpuHistogram, resourceMemoryHistogram, resourceCommittedMemoryHistogram, "background", "", 0, true);
      } catch (IOException error) {
        error.printStackTrace(System.err);
      }
    }, RESOURCE_SAMPLE_INTERVAL_MS, RESOURCE_SAMPLE_INTERVAL_MS, TimeUnit.MILLISECONDS);
    server.start();
  }

  private static String getRequestId(HttpExchange exchange) {
    String headerValue = exchange.getRequestHeaders().getFirst("x-request-id");
    if (headerValue != null && !headerValue.isBlank()) {
      return headerValue;
    }
    return "req-" + UUID.randomUUID().toString().replace("-", "").substring(0, 16);
  }

  private static void writeJson(HttpExchange exchange, int statusCode, String body) throws IOException {
    byte[] bytes = body.getBytes(StandardCharsets.UTF_8);
    exchange.getResponseHeaders().set("Content-Type", "application/json");
    exchange.sendResponseHeaders(statusCode, bytes.length);
    try (OutputStream outputStream = exchange.getResponseBody()) {
      outputStream.write(bytes);
    }
  }

  private static Attributes requestAttributes(String route, int statusCode) {
    return Attributes.builder()
        .put(AttributeKey.stringKey("route"), route)
        .put(AttributeKey.longKey("status"), statusCode)
        .build();
  }

  @FunctionalInterface
  private interface TracedSupplier<T> {
    T run() throws Exception;
  }

  private static <T> T runTracedStep(Tracer tracer, String name, Map<String, ?> attributes, TracedSupplier<T> supplier) throws Exception {
    Span span = tracer.spanBuilder(name).setSpanKind(SpanKind.INTERNAL).startSpan();
    try (Scope scope = span.makeCurrent()) {
      for (Map.Entry<String, ?> entry : attributes.entrySet()) {
        putSpanAttribute(span, entry.getKey(), entry.getValue());
      }
      T result = supplier.run();
      span.end();
      return result;
    } catch (Exception error) {
      applyErrorAttributes(span, error, SERVICE_NAME, "infrastructure");
      span.setStatus(StatusCode.ERROR, error.getMessage());
      span.end();
      throw error;
    }
  }

  private static void putSpanAttribute(Span span, String key, Object value) {
    if (value instanceof String) {
      span.setAttribute(key, (String) value);
    } else if (value instanceof Integer) {
      span.setAttribute(key, (Integer) value);
    } else if (value instanceof Long) {
      span.setAttribute(key, (Long) value);
    } else if (value instanceof Double) {
      span.setAttribute(key, (Double) value);
    } else if (value instanceof Boolean) {
      span.setAttribute(key, (Boolean) value);
    } else if (value != null) {
      span.setAttribute(key, String.valueOf(value));
    }
  }

  private static void recordResourceMetrics(
      DoubleHistogram cpuHistogram,
      DoubleHistogram memoryHistogram,
      DoubleHistogram committedMemoryHistogram,
      String scope,
      String route,
      int statusCode,
      boolean emitLog) throws IOException {
    Map<String, Double> memoryStats = memoryStatsMb();
    double cpuPercent = processCpuPercent();
    long pid = ProcessHandle.current().pid();

    var attrs = Attributes.builder()
        .put(AttributeKey.stringKey("scope"), scope)
        .put(AttributeKey.stringKey("component"), "runtime")
        .put(AttributeKey.longKey("pid"), pid);
    if (!route.isBlank()) {
      attrs.put(AttributeKey.stringKey("route"), route);
    }
    if (statusCode > 0) {
      attrs.put(AttributeKey.longKey("status"), statusCode);
    }

    Attributes attributes = attrs.build();
    cpuHistogram.record(cpuPercent, attributes);
    memoryHistogram.record(memoryStats.get("used_mb"), attributes);
    committedMemoryHistogram.record(memoryStats.get("committed_mb"), attributes);

    boolean thresholdExceeded = cpuPercent >= RESOURCE_WARN_CPU_PERCENT || memoryStats.get("used_mb") >= RESOURCE_WARN_MEMORY_MB;
    if (emitLog || thresholdExceeded) {
      Map<String, Object> context = new LinkedHashMap<>();
      context.put("scope", scope);
      context.put("component", "runtime");
      context.put("pid", pid);
      context.put("route", route);
      context.put("status", statusCode);
      context.put("cpu_percent", cpuPercent);
      context.put("memory_used_mb", memoryStats.get("used_mb"));
      context.put("memory_committed_mb", memoryStats.get("committed_mb"));
      context.put("memory_max_mb", memoryStats.get("max_mb"));
      context.put("cpu_warn_percent", RESOURCE_WARN_CPU_PERCENT);
      context.put("memory_warn_mb", RESOURCE_WARN_MEMORY_MB);
      log(thresholdExceeded ? "WARN" : "INFO", thresholdExceeded ? "java resource threshold exceeded" : "java resource snapshot", context);
    }
  }

  private static double processCpuPercent() {
    if (OS_BEAN == null) {
      return 0.0;
    }
    double cpuLoad = OS_BEAN.getProcessCpuLoad();
    if (cpuLoad < 0) {
      return 0.0;
    }
    return roundDuration(cpuLoad * 100.0);
  }

  private static Map<String, Double> memoryStatsMb() {
    Runtime runtime = Runtime.getRuntime();
    double usedMb = roundDuration((runtime.totalMemory() - runtime.freeMemory()) / (1024.0 * 1024.0));
    double committedMb = roundDuration(runtime.totalMemory() / (1024.0 * 1024.0));
    double maxMb = roundDuration(runtime.maxMemory() / (1024.0 * 1024.0));
    Map<String, Double> stats = new LinkedHashMap<>();
    stats.put("used_mb", usedMb);
    stats.put("committed_mb", committedMb);
    stats.put("max_mb", maxMb);
    return stats;
  }

  private static double roundDuration(double durationMs) {
    return Math.round(durationMs * 100.0) / 100.0;
  }

  private static void log(String severity, String message, Map<String, Object> context) throws IOException {
    SpanContext spanContext = Span.current().getSpanContext();
    String traceId = spanContext.isValid() ? spanContext.getTraceId() : "";
    String spanId = spanContext.isValid() ? spanContext.getSpanId() : "";
    String entry = String.format(
      "{\"timestamp\":\"%s\",\"severity\":\"%s\",\"service.name\":\"%s\",\"message\":\"%s\",\"trace_id\":\"%s\",\"span_id\":\"%s\",\"context\":%s}%n",
        Instant.now(),
        severity,
        SERVICE_NAME,
        message.replace("\"", "'"),
        traceId,
        spanId,
        mapToJson(context));
    java.nio.file.Files.writeString(
        java.nio.file.Path.of(LOG_FILE),
        entry,
        java.nio.file.StandardOpenOption.CREATE,
        java.nio.file.StandardOpenOption.APPEND);
    System.out.print(entry);
  }

  private static String mapToJson(Map<String, Object> context) {
    StringBuilder builder = new StringBuilder("{");
    boolean first = true;
    for (Map.Entry<String, Object> entry : context.entrySet()) {
      if (!first) {
        builder.append(",");
      }
      first = false;
      builder.append("\"").append(entry.getKey()).append("\":");
      Object value = entry.getValue();
      if (value instanceof Number || value instanceof Boolean) {
        builder.append(value);
      } else {
        builder.append("\"").append(String.valueOf(value).replace("\"", "'")).append("\"");
      }
    }
    builder.append("}");
    return builder.toString();
  }

  private static void applyErrorAttributes(Span span, Throwable error, String symptomComponent, String symptomLayer) {
    StackTraceElement frame = firstApplicationFrame(error);
    span.setAttribute("exception.type", error.getClass().getSimpleName());
    span.setAttribute("exception.message", String.valueOf(error.getMessage()));
    span.setAttribute("exception.stacktrace", stackTraceAsString(error));
    span.setAttribute("code.file.path", frame.getFileName() == null ? "App.java" : frame.getFileName());
    span.setAttribute("code.function.name", frame.getClassName() + "." + frame.getMethodName());
    span.setAttribute("code.line.number", frame.getLineNumber());
  }

  private static Map<String, Object> errorContextForLog(Throwable error, String requestId) {
    StackTraceElement frame = firstApplicationFrame(error);
    Map<String, Object> context = new LinkedHashMap<>();
    context.put("error", error.getMessage());
    context.put("request_id", requestId);
    context.put("error_type", error.getClass().getSimpleName());
    context.put("code_file_path", frame.getFileName() == null ? "App.java" : frame.getFileName());
    context.put("code_function_name", frame.getClassName() + "." + frame.getMethodName());
    context.put("code_line_number", frame.getLineNumber());
    return context;
  }

  private static StackTraceElement firstApplicationFrame(Throwable error) {
    for (StackTraceElement frame : error.getStackTrace()) {
      if (frame.getClassName().startsWith("nl.dylan.openobserve")) {
        return frame;
      }
    }
    return error.getStackTrace().length > 0 ? error.getStackTrace()[0] : new StackTraceElement("nl.dylan.openobserve.App", "{unknown}", "App.java", 0);
  }

  private static String stackTraceAsString(Throwable error) {
    StringBuilder builder = new StringBuilder();
    builder.append(error.toString()).append("\n");
    for (StackTraceElement frame : error.getStackTrace()) {
      builder.append("at ").append(frame).append("\n");
    }
    return builder.toString();
  }
}
