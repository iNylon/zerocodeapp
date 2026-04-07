const fs = require('fs');
const express = require('express');
const mysql = require('mysql2/promise');
const { createClient } = require('redis');
const { trace, metrics } = require('@opentelemetry/api');

const serviceName = process.env.APP_SERVICE_NAME || 'node-catalog';
const logFile = process.env.APP_LOG_FILE || '/tmp/node-catalog.log';
const dbBottleneckMode = (process.env.APP_DB_BOTTLENECK_MODE || 'true').toLowerCase() !== 'false';
const dbBottleneckLoops = Math.max(1, Number(process.env.APP_DB_BOTTLENECK_LOOPS || '12'));
const failureRatePercent = Math.max(0, Math.min(100, Number(process.env.APP_SYNTHETIC_FAILURE_RATE_PERCENT || '26')));
const slowLogThresholdMs = Math.max(50, Number(process.env.APP_SLOW_LOG_THRESHOLD_MS || '110'));
const resourceSampleIntervalMs = Math.max(5000, Number(process.env.APP_RESOURCE_SAMPLE_INTERVAL_MS || '10000'));
const resourceWarnCpuPercent = Math.max(1, Number(process.env.APP_RESOURCE_WARN_CPU_PERCENT || '35'));
const resourceWarnMemoryMb = Math.max(32, Number(process.env.APP_RESOURCE_WARN_MEMORY_MB || '180'));

const tracer = trace.getTracer(serviceName);
const meter = metrics.getMeter(serviceName);
const requestCounter = meter.createCounter('node_requests_total');
const errorCounter = meter.createCounter('node_errors_total');
const latencyHistogram = meter.createHistogram('node_request_duration_ms', { unit: 'ms' });
const resourceCpuHistogram = meter.createHistogram('node_process_cpu_percent', { unit: 'percent' });
const resourceMemoryHistogram = meter.createHistogram('node_process_memory_rss_mb', { unit: 'MB' });
const resourceHeapHistogram = meter.createHistogram('node_process_heap_used_mb', { unit: 'MB' });

let lastResourceWall = process.hrtime.bigint();
let lastResourceCpu = process.cpuUsage();

const mysqlPool = mysql.createPool({
  host: process.env.MYSQL_HOST || 'mysql',
  port: Number(process.env.MYSQL_PORT || '3306'),
  user: process.env.MYSQL_USER || 'app',
  password: process.env.MYSQL_PASSWORD || 'app',
  database: process.env.MYSQL_DATABASE || 'catalog',
  connectionLimit: 5,
});

const redis = createClient({
  socket: {
    host: process.env.REDIS_HOST || 'redis',
    port: Number(process.env.REDIS_PORT || '6379'),
  },
});

redis.connect().catch((error) => {
  log('ERROR', 'redis connection failed', { error: error.message });
});

const app = express();

function errorLocation(error) {
  const stack = typeof error?.stack === 'string' ? error.stack.split('\n') : [];
  for (const line of stack) {
    const match = line.match(/\s*at\s+(.*?)\s+\((.*):(\d+):(\d+)\)/) || line.match(/\s*at\s+(.*):(\d+):(\d+)/);
    if (!match) {
      continue;
    }
    if (match.length === 5) {
      return { functionName: match[1], filePath: match[2], lineNumber: Number(match[3]) };
    }
    return { functionName: '{anonymous}', filePath: match[1], lineNumber: Number(match[2]) };
  }
  return { functionName: '{unknown}', filePath: __filename, lineNumber: 0 };
}

function attachErrorContext(error, context = {}) {
  error.observabilityContext = { ...(error.observabilityContext || {}), ...context };
  return error;
}

function applyErrorAttributes(span, error) {
  const location = errorLocation(error);
  const context = error?.observabilityContext || {};
  span.setAttribute('exception.type', error?.name || 'Error');
  span.setAttribute('exception.message', error?.message || 'Unknown error');
  span.setAttribute('exception.stacktrace', String(error?.stack || ''));
  span.setAttribute('code.file.path', location.filePath);
  span.setAttribute('code.function.name', location.functionName);
  span.setAttribute('code.line.number', location.lineNumber);
  Object.entries(context).forEach(([key, value]) => span.setAttribute(key, value));
}

function generateRequestId() {
  return `req-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

async function traceStep(name, attributes, operation) {
  return tracer.startActiveSpan(name, async (span) => {
    Object.entries(attributes || {}).forEach(([key, value]) => span.setAttribute(key, value));
    try {
      const result = await operation(span);
      span.end();
      return result;
    } catch (error) {
      applyErrorAttributes(span, error);
      span.setStatus({ code: 2, message: error.message });
      span.end();
      throw error;
    }
  });
}

function log(severity, message, context = {}) {
  const activeSpan = trace.getActiveSpan();
  const spanContext = activeSpan ? activeSpan.spanContext() : undefined;
  const entry = JSON.stringify({
    timestamp: new Date().toISOString(),
    severity,
    'service.name': serviceName,
    message,
    trace_id: spanContext ? spanContext.traceId : '',
    span_id: spanContext ? spanContext.spanId : '',
    context,
  });
  fs.appendFileSync(logFile, entry + '\n');
  process.stdout.write(entry + '\n');
}

function sampleProcessResources() {
  const nowWall = process.hrtime.bigint();
  const nowCpu = process.cpuUsage();
  const wallMicros = Math.max(Number(nowWall - lastResourceWall) / 1000, 1);
  const cpuMicros = Math.max((nowCpu.user - lastResourceCpu.user) + (nowCpu.system - lastResourceCpu.system), 0);
  lastResourceWall = nowWall;
  lastResourceCpu = nowCpu;

  const memory = process.memoryUsage();
  return {
    pid: process.pid,
    cpuPercent: Number(((cpuMicros / wallMicros) * 100).toFixed(2)),
    memoryRssMb: Number((memory.rss / (1024 * 1024)).toFixed(2)),
    heapUsedMb: Number((memory.heapUsed / (1024 * 1024)).toFixed(2)),
  };
}

function recordProcessResources(scope, extra = {}, emitLog = false) {
  const snapshot = sampleProcessResources();
  const attrs = {
    scope,
    component: 'runtime',
    pid: snapshot.pid,
    ...extra,
  };

  resourceCpuHistogram.record(snapshot.cpuPercent, attrs);
  resourceMemoryHistogram.record(snapshot.memoryRssMb, attrs);
  resourceHeapHistogram.record(snapshot.heapUsedMb, attrs);

  const thresholdExceeded = snapshot.cpuPercent >= resourceWarnCpuPercent || snapshot.memoryRssMb >= resourceWarnMemoryMb;
  if (emitLog || thresholdExceeded) {
    log(thresholdExceeded ? 'WARN' : 'INFO', thresholdExceeded ? 'node resource threshold exceeded' : 'node resource snapshot', {
      scope,
      component: 'runtime',
      pid: snapshot.pid,
      cpu_percent: snapshot.cpuPercent,
      memory_rss_mb: snapshot.memoryRssMb,
      heap_used_mb: snapshot.heapUsedMb,
      cpu_warn_percent: resourceWarnCpuPercent,
      memory_warn_mb: resourceWarnMemoryMb,
      ...extra,
    });
  }

  return snapshot;
}

app.use((req, res, next) => {
  req.requestId = req.get('x-request-id') || generateRequestId();
  res.setHeader('x-request-id', req.requestId);

  const start = performance.now();
  res.on('finish', () => {
    const duration = performance.now() - start;
    const attrs = { route: req.path, status: res.statusCode };
    requestCounter.add(1, attrs);
    latencyHistogram.record(duration, attrs);
    recordProcessResources('request', { route: req.path, status: res.statusCode });
    log('INFO', 'node request complete', { path: req.path, status: res.statusCode, duration_ms: Number(duration.toFixed(2)), request_id: req.requestId });
    if (duration >= slowLogThresholdMs) {
      log('WARN', 'node request exceeded slow threshold', {
        path: req.path,
        status: res.statusCode,
        duration_ms: Number(duration.toFixed(2)),
        request_id: req.requestId,
      });
    }
  });
  next();
});

app.get('/healthz', (_req, res) => {
  res.json({ ok: true, service: serviceName });
});

app.get('/inventory', async (req, res) => {
  return tracer.startActiveSpan('node.inventory', async (span) => {
    try {
      span.setAttribute('request.id', req.requestId);
      span.setAttribute('peer.service', 'mysql');
      const [rows] = await traceStep('node.mysql.inventory_query', {
        'component.layer': 'infrastructure',
        'infra.kind': 'database',
        'db.system': 'mysql',
        'db.operation': 'SELECT',
        'db.sql.table': 'products',
        'server.address': process.env.MYSQL_HOST || 'mysql',
        'server.port': Number(process.env.MYSQL_PORT || '3306'),
        'bottleneck.active': dbBottleneckMode,
      }, () => mysqlPool.query('SELECT sku, name, category, price, inventory FROM products ORDER BY id LIMIT 25'));
      const wasteQueryCount = dbBottleneckMode ? await induceMysqlBottleneck(rows) : 0;
      const inventoryTotal = rows.reduce((total, row) => total + Number(row.inventory || 0), 0);
      await traceStep('node.redis.marker_set', {
        'component.layer': 'infrastructure',
        'infra.kind': 'cache',
        'db.system': 'redis',
        'db.operation': 'SET',
        'db.redis.key': 'node:last_inventory_fetch',
        'server.address': process.env.REDIS_HOST || 'redis',
        'server.port': Number(process.env.REDIS_PORT || '6379'),
        'bottleneck.active': false,
      }, () => redis.set('node:last_inventory_fetch', new Date().toISOString()));
      const forceFailure = req.query.fail === '1';
      const syntheticFailure = Math.random() * 100 < failureRatePercent;

      if (forceFailure || syntheticFailure) {
        throw attachErrorContext(
          new Error('node inventory lookup failed during catalog processing'),
          {
            failure_mode: forceFailure ? 'forced' : 'synthetic',
            failure_rate_percent: failureRatePercent,
            catalog_item_count: rows.length,
            catalog_inventory_total: inventoryTotal,
            waste_queries: wasteQueryCount,
          },
        );
      }

      if (wasteQueryCount > 0) {
        log('WARN', 'node inventory bottleneck active', {
          waste_queries: wasteQueryCount,
          request_id: req.requestId,
          item_count: rows.length,
        });
      }

      log('INFO', 'node inventory served', {
        request_id: req.requestId,
        item_count: rows.length,
        inventory_total: inventoryTotal,
        waste_queries: wasteQueryCount,
        redis: true,
      });

      res.json({
        service: serviceName,
        request_id: req.requestId,
        items: rows,
        waste_queries: wasteQueryCount,
        redis_marker: await traceStep('node.redis.marker_get', {
          'component.layer': 'infrastructure',
          'infra.kind': 'cache',
          'db.system': 'redis',
          'db.operation': 'GET',
          'db.redis.key': 'node:last_inventory_fetch',
          'server.address': process.env.REDIS_HOST || 'redis',
          'server.port': Number(process.env.REDIS_PORT || '6379'),
          'bottleneck.active': false,
        }, () => redis.get('node:last_inventory_fetch')),
      });
      span.setAttribute('catalog.item_count', rows.length);
      span.setAttribute('catalog.waste_queries', wasteQueryCount);
      span.end();
    } catch (error) {
      errorCounter.add(1, { route: '/inventory' });
      span.recordException(error);
      const location = errorLocation(error);
      applyErrorAttributes(span, error);
      span.setStatus({ code: 2, message: error.message });
      log('ERROR', 'node inventory failed', {
        error: error.message,
        request_id: req.requestId,
        error_type: error.name || 'Error',
        error_file: location.filePath,
        error_function: location.functionName,
        error_line: location.lineNumber,
        ...(error.observabilityContext || {}),
      });
      res.status(503).json({ error: error.message, service: serviceName, request_id: req.requestId });
      span.end();
    }
  });
});

async function induceMysqlBottleneck(rows) {
  const connection = await traceStep('node.mysql.connect', {
    'component.layer': 'infrastructure',
    'infra.kind': 'database',
    'db.system': 'mysql',
    'db.operation': 'CONNECT',
    'server.address': process.env.MYSQL_HOST || 'mysql',
    'server.port': Number(process.env.MYSQL_PORT || '3306'),
    'bottleneck.active': true,
  }, () => mysqlPool.getConnection());
  const products = rows.length > 0 ? rows : [{ sku: 'SKU-100' }];
  let totalQueries = 0;
  const transactionId = `node-mysql-${Date.now().toString(36)}`;
  const operationSequence = [];
  let lastQueryType = 'read';

  try {
    await connection.beginTransaction();
    operationSequence.push('begin_transaction');
    await traceStep('node.mysql.select_for_update', {
      'component.layer': 'infrastructure',
      'infra.kind': 'database',
      'db.system': 'mysql',
      'db.operation': 'SELECT',
      'db.query_type': 'select_for_update',
      'db.sql.table': 'products',
      'server.address': process.env.MYSQL_HOST || 'mysql',
      'server.port': Number(process.env.MYSQL_PORT || '3306'),
      'bottleneck.active': true,
    }, () => connection.query('SELECT id FROM products WHERE id = 1 FOR UPDATE'));
    lastQueryType = 'select_for_update';
    operationSequence.push('lock_product_row');
    await traceStep('node.mysql.lock_wait', {
      'component.layer': 'infrastructure',
      'infra.kind': 'database',
      'db.system': 'mysql',
      'db.operation': 'SELECT',
      'db.query_type': 'sleep',
      'db.sql.table': 'products',
      'server.address': process.env.MYSQL_HOST || 'mysql',
      'server.port': Number(process.env.MYSQL_PORT || '3306'),
      'bottleneck.active': true,
    }, () => connection.query('SELECT SLEEP(0.12)'));
    lastQueryType = 'sleep';
    operationSequence.push('hold_lock');
    totalQueries += 2;

    const loopCount = Math.max(dbBottleneckLoops, products.length);
    await traceStep('node.mysql.product_lookup_loop', {
      'component.layer': 'infrastructure',
      'infra.kind': 'database',
      'db.system': 'mysql',
      'db.operation': 'SELECT',
      'db.query_type': 'select_inventory',
      'db.sql.table': 'products',
      'db.operation_count': loopCount,
      'server.address': process.env.MYSQL_HOST || 'mysql',
      'server.port': Number(process.env.MYSQL_PORT || '3306'),
      'bottleneck.active': true,
    }, async () => {
      for (let i = 0; i < loopCount; i += 1) {
        const sku = products[i % products.length].sku;
        await connection.query('SELECT inventory, price FROM products WHERE sku = ?', [sku]);
        lastQueryType = 'select_inventory';
        operationSequence.push(`read_product:${sku}`);
        totalQueries += 1;
      }
    });

    await connection.commit();
    return totalQueries;
  } catch (error) {
    await connection.rollback();
    operationSequence.push('rollback');
    throw attachErrorContext(error, {
      'db.system': 'mysql',
      'db.query_type': lastQueryType,
      'db.transaction_id': transactionId,
      'db.lock_target': 'products.id=1',
      'db.operation_sequence': operationSequence.join(' > '),
    });
  } finally {
    connection.release();
  }
}

app.listen(Number(process.env.APP_PORT || '3000'), '0.0.0.0', () => {
  log('INFO', 'starting node catalog service', {
    failure_rate_percent: failureRatePercent,
    slow_log_threshold_ms: slowLogThresholdMs,
    db_bottleneck_loops: dbBottleneckLoops,
    resource_sample_interval_ms: resourceSampleIntervalMs,
    resource_warn_cpu_percent: resourceWarnCpuPercent,
    resource_warn_memory_mb: resourceWarnMemoryMb,
  });
});

setInterval(() => {
  recordProcessResources('background', { source: 'resource_sampler' }, true);
}, resourceSampleIntervalMs).unref();
