package nl.dylan.openobserve;

import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import java.io.IOException;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Random;
import java.util.UUID;
import java.util.concurrent.Executors;
import redis.clients.jedis.Jedis;

public final class App {
  private static final String SERVICE_NAME = System.getenv().getOrDefault("APP_SERVICE_NAME", "java-checkout");
  private static final String LOG_FILE = System.getenv().getOrDefault("APP_LOG_FILE", "/tmp/java-checkout.log");
  private static final String REDIS_HOST = System.getenv().getOrDefault("REDIS_HOST", "redis");
  private static final int REDIS_PORT = Integer.parseInt(System.getenv().getOrDefault("REDIS_PORT", "6379"));
  private static final int PORT = Integer.parseInt(System.getenv().getOrDefault("APP_PORT", "8081"));
  private static final int FAILURE_RATE_PERCENT = Integer.parseInt(System.getenv().getOrDefault("APP_SYNTHETIC_FAILURE_RATE_PERCENT", "26"));
  private static final Random RANDOM = new Random();

  private App() {
  }

  public static void main(String[] args) throws IOException {
    HttpServer server = HttpServer.create(new InetSocketAddress(PORT), 0);
    server.setExecutor(Executors.newFixedThreadPool(8));

    server.createContext("/healthz", exchange -> {
      String requestId = getRequestId(exchange);
      exchange.getResponseHeaders().set("x-request-id", requestId);
      writeJson(exchange, 200, "{\"ok\":true,\"service\":\"" + SERVICE_NAME + "\",\"request_id\":\"" + requestId + "\"}");
      log("INFO", "java health served", Map.of("request_id", requestId, "status", 200));
    });

    server.createContext("/quote", exchange -> {
      String requestId = getRequestId(exchange);
      exchange.getResponseHeaders().set("x-request-id", requestId);

      try (Jedis jedis = new Jedis(REDIS_HOST, REDIS_PORT)) {
        jedis.set("java:last_quote", Instant.now().toString());

        double quote = 29.99 + RANDOM.nextInt(60);
        boolean failure = RANDOM.nextInt(100) < Math.max(0, Math.min(FAILURE_RATE_PERCENT, 100));
        boolean forceFailure = "fail=1".equals(exchange.getRequestURI().getQuery());

        if (forceFailure || failure) {
          throw new RuntimeException("java checkout quote computation failed");
        }

        String redisMarker = jedis.get("java:last_quote");
        String body = "{\"service\":\"" + SERVICE_NAME + "\",\"request_id\":\"" + requestId + "\",\"quote\":" + quote + ",\"redis_marker\":\"" + redisMarker + "\"}";
        writeJson(exchange, 200, body);
        log("INFO", "java quote served", Map.of("quote", quote, "request_id", requestId, "status", 200));
      } catch (Exception error) {
        log("ERROR", "java quote failed", errorContextForLog(error, requestId));
        writeJson(exchange, 503, "{\"error\":\"" + error.getMessage() + "\",\"service\":\"" + SERVICE_NAME + "\",\"request_id\":\"" + requestId + "\"}");
      }
    });

    log("INFO", "starting java checkout service", Map.of(
        "port", PORT,
        "failure_rate_percent", FAILURE_RATE_PERCENT));
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

  private static void log(String severity, String message, Map<String, Object> context) throws IOException {
    String entry = String.format(
        "{\"timestamp\":\"%s\",\"severity\":\"%s\",\"service.name\":\"%s\",\"message\":\"%s\",\"context\":%s}%n",
        Instant.now(),
        severity,
        SERVICE_NAME,
        message.replace("\"", "'"),
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

  private static Map<String, Object> errorContextForLog(Throwable error, String requestId) {
    Map<String, Object> context = new LinkedHashMap<>();
    context.put("error", error.getMessage());
    context.put("request_id", requestId);
    context.put("error_type", error.getClass().getSimpleName());
    return context;
  }
}
