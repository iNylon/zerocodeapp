const fs = require('fs');
const express = require('express');
const mysql = require('mysql2/promise');
const { createClient } = require('redis');

const serviceName = process.env.APP_SERVICE_NAME || 'node-catalog';
const logFile = process.env.APP_LOG_FILE || '/tmp/node-catalog.log';
const dbBottleneckMode = (process.env.APP_DB_BOTTLENECK_MODE || 'true').toLowerCase() !== 'false';
const dbBottleneckLoops = Math.max(1, Number(process.env.APP_DB_BOTTLENECK_LOOPS || '12'));
const failureRatePercent = Math.max(0, Math.min(100, Number(process.env.APP_SYNTHETIC_FAILURE_RATE_PERCENT || '26')));

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

function generateRequestId() {
  return `req-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

function log(severity, message, context = {}) {
  const entry = JSON.stringify({
    timestamp: new Date().toISOString(),
    severity,
    'service.name': serviceName,
    message,
    context,
  });
  fs.appendFileSync(logFile, entry + '\n');
  process.stdout.write(entry + '\n');
}

app.use((req, res, next) => {
  req.requestId = req.get('x-request-id') || generateRequestId();
  res.setHeader('x-request-id', req.requestId);
  res.on('finish', () => {
    log('INFO', 'node request complete', {
      path: req.path,
      status: res.statusCode,
      request_id: req.requestId,
    });
  });
  next();
});

app.get('/healthz', (_req, res) => {
  res.json({ ok: true, service: serviceName });
});

app.get('/inventory', async (req, res) => {
  try {
    const [rows] = await mysqlPool.query('SELECT sku, name, category, price, inventory FROM products ORDER BY id LIMIT 25');
    const wasteQueryCount = dbBottleneckMode ? await induceMysqlBottleneck(rows) : 0;
    const inventoryTotal = rows.reduce((total, row) => total + Number(row.inventory || 0), 0);

    await redis.set('node:last_inventory_fetch', new Date().toISOString());
    const redisMarker = await redis.get('node:last_inventory_fetch');

    const forceFailure = req.query.fail === '1';
    const syntheticFailure = Math.random() * 100 < failureRatePercent;

    if (forceFailure || syntheticFailure) {
      throw new Error('node inventory lookup failed during catalog processing');
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
      redis_marker: redisMarker,
    });
  } catch (error) {
    log('ERROR', 'node inventory failed', {
      error: error.message,
      request_id: req.requestId,
      error_type: error.name || 'Error',
    });
    res.status(503).json({ error: error.message, service: serviceName, request_id: req.requestId });
  }
});

async function induceMysqlBottleneck(rows) {
  const connection = await mysqlPool.getConnection();
  const products = rows.length > 0 ? rows : [{ sku: 'SKU-100' }];
  let totalQueries = 0;

  try {
    await connection.beginTransaction();
    await connection.query('SELECT id FROM products WHERE id = 1 FOR UPDATE');
    await connection.query('SELECT SLEEP(0.12)');
    totalQueries += 2;

    const loopCount = Math.max(dbBottleneckLoops, products.length);
    for (let i = 0; i < loopCount; i += 1) {
      const sku = products[i % products.length].sku;
      await connection.query('SELECT inventory, price FROM products WHERE sku = ?', [sku]);
      totalQueries += 1;
    }

    await connection.commit();
    return totalQueries;
  } catch (error) {
    await connection.rollback();
    throw error;
  } finally {
    connection.release();
  }
}

app.listen(Number(process.env.APP_PORT || '3000'), '0.0.0.0', () => {
  log('INFO', 'starting node catalog service', {
    failure_rate_percent: failureRatePercent,
    db_bottleneck_loops: dbBottleneckLoops,
  });
});
