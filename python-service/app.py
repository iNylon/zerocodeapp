import json
import os
import random
import uuid
from datetime import datetime, timezone

import psycopg2
import redis
from flask import Flask, jsonify, request

SERVICE_NAME = os.getenv("APP_SERVICE_NAME", "python-recommendation")
LOG_FILE = os.getenv("APP_LOG_FILE", "/tmp/python-recommendation.log")
DB_BOTTLENECK_MODE = os.getenv("APP_DB_BOTTLENECK_MODE", "true").lower() != "false"
DB_BOTTLENECK_LOOPS = max(1, int(os.getenv("APP_DB_BOTTLENECK_LOOPS", "10")))
SERVICE_FAILURE_RATE_PERCENT = max(0, min(100, int(os.getenv("APP_SYNTHETIC_FAILURE_RATE_PERCENT", "28"))))

app = Flask(__name__)


def log(severity: str, message: str, **context):
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "severity": severity,
        "service.name": SERVICE_NAME,
        "message": message,
        "context": context,
    }
    line = json.dumps(entry)
    with open(LOG_FILE, "a", encoding="utf-8") as handle:
        handle.write(line + "\n")
    print(line, flush=True)


def get_pg_connection():
    return psycopg2.connect(
        host=os.getenv("POSTGRES_HOST", "postgres"),
        port=int(os.getenv("POSTGRES_PORT", "5432")),
        dbname=os.getenv("POSTGRES_DB", "recommendations"),
        user=os.getenv("POSTGRES_USER", "app"),
        password=os.getenv("POSTGRES_PASSWORD", "app"),
    )


def get_redis():
    return redis.Redis(
        host=os.getenv("REDIS_HOST", "redis"),
        port=int(os.getenv("REDIS_PORT", "6379")),
        decode_responses=True,
    )


@app.before_request
def before_request():
    request._request_id = request.headers.get("x-request-id") or f"req-{uuid.uuid4().hex[:16]}"


@app.after_request
def after_request(response):
    response.headers["x-request-id"] = request._request_id
    log("INFO", "python request complete", path=request.path, status=response.status_code, request_id=request._request_id)
    return response


@app.route("/healthz")
def healthz():
    return jsonify({"ok": True, "service": SERVICE_NAME, "request_id": request._request_id})


@app.route("/recommendations")
def recommendations():
    user_id = max(1, int(request.args.get("user_id", "1")))
    cache = get_redis()
    cache_key = f"recommendations:{user_id}"

    try:
        cached = cache.get(cache_key)
        if cached and not DB_BOTTLENECK_MODE:
            payload = json.loads(cached)
            payload["request_id"] = request._request_id
            log("INFO", "served recommendations from cache", user_id=user_id, request_id=request._request_id)
            return jsonify(payload)

        with get_pg_connection() as conn, conn.cursor() as cur:
            waste_queries = 0

            if DB_BOTTLENECK_MODE:
                cur.execute("SELECT id FROM users WHERE id = 1 FOR UPDATE")
                cur.execute("SELECT pg_sleep(0.15)")
                waste_queries += 2

                for _ in range(DB_BOTTLENECK_LOOPS):
                    cur.execute("SELECT COUNT(*) FROM recommendations WHERE user_id = %s", (user_id,))
                    cur.fetchone()
                    waste_queries += 1

            cur.execute(
                """
                SELECT u.email, u.tier, r.sku, r.score
                FROM users u
                JOIN recommendations r ON r.user_id = u.id
                WHERE u.id = %s
                ORDER BY r.score DESC
                """,
                (user_id,),
            )
            rows = cur.fetchall()

            tier_rows = [row[1] for row in rows]
            if DB_BOTTLENECK_MODE and rows:
                looked_up = []
                for _row in rows:
                    cur.execute("SELECT tier FROM users WHERE id = %s", (user_id,))
                    tier_row = cur.fetchone()
                    looked_up.append(tier_row[0] if tier_row else _row[1])
                    waste_queries += 1
                tier_rows = looked_up

        items = []
        for index, row in enumerate(rows):
            items.append({"email": row[0], "tier": tier_rows[index], "sku": row[2], "score": float(row[3])})

        payload = {
            "service": SERVICE_NAME,
            "request_id": request._request_id,
            "user_id": user_id,
            "items": items,
            "cache": False,
            "waste_queries": waste_queries,
        }
        cache.setex(cache_key, 5 if DB_BOTTLENECK_MODE else 20, json.dumps(payload))

        if request.args.get("fail") == "1":
            raise RuntimeError("python recommendation ranking failed while composing response")

        if random.randint(1, 100) <= SERVICE_FAILURE_RATE_PERCENT:
            raise RuntimeError("python recommendation ranking failed while composing response")

        log("INFO", "served recommendations from postgres", user_id=user_id, count=len(items), request_id=request._request_id)
        return jsonify(payload)
    except Exception as error:
        log(
            "ERROR",
            "python recommendations failed",
            error=str(error),
            request_id=request._request_id,
            error_type=error.__class__.__name__,
        )
        return jsonify({"error": str(error), "service": SERVICE_NAME, "request_id": request._request_id}), 503


if __name__ == "__main__":
    log(
        "INFO",
        "starting python recommendation service",
        failure_rate_percent=SERVICE_FAILURE_RATE_PERCENT,
        db_bottleneck_loops=DB_BOTTLENECK_LOOPS,
    )
    app.run(host="0.0.0.0", port=int(os.getenv("APP_PORT", "8000")), threaded=True)
