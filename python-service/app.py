import json
import os
import random
import threading
import time
import traceback
import uuid
from datetime import datetime, timezone
from typing import Optional

import psycopg2
import redis
import requests
from flask import Flask, jsonify, request
from opentelemetry import context as otel_context
from opentelemetry import metrics, propagate, trace
from opentelemetry.trace import SpanKind, Status, StatusCode

SERVICE_NAME = os.getenv("APP_SERVICE_NAME", "python-recommendation")
LOG_FILE = os.getenv("APP_LOG_FILE", "/tmp/python-recommendation.log")
DB_BOTTLENECK_MODE = os.getenv("APP_DB_BOTTLENECK_MODE", "true").lower() != "false"
DB_BOTTLENECK_LOOPS = max(1, int(os.getenv("APP_DB_BOTTLENECK_LOOPS", "10")))
SERVICE_FAILURE_RATE_PERCENT = max(0, min(100, int(os.getenv("APP_SYNTHETIC_FAILURE_RATE_PERCENT", "28"))))
KEEPALIVE_ENABLED = os.getenv("APP_METRIC_KEEPALIVE_ENABLED", "true").lower() != "false"
KEEPALIVE_INTERVAL_SECONDS = max(10, int(os.getenv("APP_METRIC_KEEPALIVE_INTERVAL_SECONDS", "30")))
KEEPALIVE_START_DELAY_SECONDS = max(1, int(os.getenv("APP_METRIC_KEEPALIVE_START_DELAY_SECONDS", "12")))
KEEPALIVE_ROUNDS_PER_CYCLE = max(1, int(os.getenv("APP_METRIC_KEEPALIVE_ROUNDS_PER_CYCLE", "2")))
KEEPALIVE_BETWEEN_REQUESTS_MS = max(0, int(os.getenv("APP_METRIC_KEEPALIVE_BETWEEN_REQUESTS_MS", "200")))
RESOURCE_SAMPLE_INTERVAL_SECONDS = max(5, int(os.getenv("APP_RESOURCE_SAMPLE_INTERVAL_SECONDS", "10")))
RESOURCE_WARN_CPU_PERCENT = max(1, int(os.getenv("APP_RESOURCE_WARN_CPU_PERCENT", "35")))
RESOURCE_WARN_MEMORY_MB = max(32, int(os.getenv("APP_RESOURCE_WARN_MEMORY_MB", "180")))
PHP_STOREFRONT_URL = os.getenv("PHP_STOREFRONT_URL", "http://php-storefront:8080").rstrip("/")
PYTHON_PUBLIC_URL = os.getenv("PYTHON_PUBLIC_URL", "http://python-recommendation:8000").rstrip("/")
NODE_SERVICE_URL = os.getenv("NODE_SERVICE_URL", "http://node-catalog:3000").rstrip("/")
JAVA_SERVICE_URL = os.getenv("JAVA_SERVICE_URL", "http://java-checkout:8081").rstrip("/")
SYNTHETIC_USER_EMAIL = os.getenv("APP_SYNTHETIC_USER_EMAIL", "telemetry-bot@example.com").strip().lower()
SYNTHETIC_USER_PASSWORD = os.getenv("APP_SYNTHETIC_USER_PASSWORD", "TelemetryBot!2026")

tracer = trace.get_tracer(SERVICE_NAME)
meter = metrics.get_meter(SERVICE_NAME)
request_counter = meter.create_counter("python_requests_total")
error_counter = meter.create_counter("python_errors_total")
latency_histogram = meter.create_histogram("python_request_duration_ms", unit="ms")
resource_cpu_histogram = meter.create_histogram("python_process_cpu_percent", unit="percent")
resource_memory_histogram = meter.create_histogram("python_process_memory_rss_mb", unit="MB")
resource_virtual_memory_histogram = meter.create_histogram("python_process_memory_virtual_mb", unit="MB")

_resource_lock = threading.Lock()
_last_resource_wall = time.perf_counter()
_last_resource_cpu = time.process_time()

app = Flask(__name__)


def log(severity: str, message: str, **context):
    span_context = trace.get_current_span().get_span_context()
    trace_id = f"{span_context.trace_id:032x}" if span_context and span_context.is_valid else ""
    span_id = f"{span_context.span_id:016x}" if span_context and span_context.is_valid else ""

    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "severity": severity,
        "service.name": SERVICE_NAME,
        "message": message,
        "trace_id": trace_id,
        "span_id": span_id,
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


def trace_step(name: str, attributes: dict, operation):
    with tracer.start_as_current_span(name, kind=SpanKind.INTERNAL) as span:
        for key, value in attributes.items():
            span.set_attribute(key, value)
        try:
            return operation()
        except Exception as error:
            apply_error_attributes(span, error)
            span.set_status(Status(StatusCode.ERROR, str(error)))
            raise


def attach_error_context(error: Exception, **context):
    existing = getattr(error, "observability_context", {})
    setattr(error, "observability_context", {**existing, **context})
    return error


def extract_error_context(error: Exception):
    return getattr(error, "observability_context", {})


def error_location(error: Exception):
    frames = traceback.extract_tb(error.__traceback__)
    for frame in reversed(frames):
        if frame.filename.endswith("app.py"):
            return {
                "code.file.path": frame.filename,
                "code.function.name": frame.name,
                "code.line.number": frame.lineno,
            }
    if frames:
        frame = frames[-1]
        return {
            "code.file.path": frame.filename,
            "code.function.name": frame.name,
            "code.line.number": frame.lineno,
        }
    return {
        "code.file.path": __file__,
        "code.function.name": "{unknown}",
        "code.line.number": 0,
    }


def apply_error_attributes(span, error: Exception):
    location = error_location(error)
    context = extract_error_context(error)
    span.set_attribute("exception.type", error.__class__.__name__)
    span.set_attribute("exception.message", str(error))
    span.set_attribute("exception.stacktrace", "".join(traceback.format_exception(type(error), error, error.__traceback__)))
    for key, value in location.items():
        span.set_attribute(key, value)
    for key, value in context.items():
        span.set_attribute(key, value)


def pause_between_keepalive_calls():
    if KEEPALIVE_BETWEEN_REQUESTS_MS > 0:
        time.sleep(KEEPALIVE_BETWEEN_REQUESTS_MS / 1000)


def read_proc_memory_stats():
    stats = {"rss_mb": 0.0, "virtual_mb": 0.0}
    try:
        with open("/proc/self/status", "r", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("VmRSS:"):
                    stats["rss_mb"] = round(int(line.split()[1]) / 1024, 2)
                elif line.startswith("VmSize:"):
                    stats["virtual_mb"] = round(int(line.split()[1]) / 1024, 2)
    except FileNotFoundError:
        pass
    return stats


def sample_process_resources():
    global _last_resource_wall, _last_resource_cpu

    with _resource_lock:
        now_wall = time.perf_counter()
        now_cpu = time.process_time()
        wall_delta = max(now_wall - _last_resource_wall, 1e-9)
        cpu_delta = max(now_cpu - _last_resource_cpu, 0.0)
        _last_resource_wall = now_wall
        _last_resource_cpu = now_cpu

    memory_stats = read_proc_memory_stats()
    return {
        "pid": os.getpid(),
        "cpu_percent": round((cpu_delta / wall_delta) * 100, 2),
        "memory_rss_mb": memory_stats["rss_mb"],
        "memory_virtual_mb": memory_stats["virtual_mb"],
    }


def record_process_resource_metrics(scope: str, route: str = "", status: Optional[int] = None, component: str = "runtime", emit_log: bool = False):
    snapshot = sample_process_resources()
    attrs = {
        "scope": scope,
        "component": component,
        "pid": snapshot["pid"],
    }
    if route:
        attrs["route"] = route
    if status is not None:
        attrs["status"] = status

    resource_cpu_histogram.record(snapshot["cpu_percent"], attrs)
    resource_memory_histogram.record(snapshot["memory_rss_mb"], attrs)
    resource_virtual_memory_histogram.record(snapshot["memory_virtual_mb"], attrs)

    threshold_exceeded = snapshot["cpu_percent"] >= RESOURCE_WARN_CPU_PERCENT or snapshot["memory_rss_mb"] >= RESOURCE_WARN_MEMORY_MB
    if emit_log or threshold_exceeded:
        severity = "WARN" if threshold_exceeded else "INFO"
        log(
            severity,
            "python resource snapshot" if not threshold_exceeded else "python resource threshold exceeded",
            scope=scope,
            component=component,
            pid=snapshot["pid"],
            route=route,
            status=status if status is not None else 0,
            cpu_percent=snapshot["cpu_percent"],
            memory_rss_mb=snapshot["memory_rss_mb"],
            memory_virtual_mb=snapshot["memory_virtual_mb"],
            cpu_warn_percent=RESOURCE_WARN_CPU_PERCENT,
            memory_warn_mb=RESOURCE_WARN_MEMORY_MB,
        )

    return snapshot


def http_request(method: str, url: str, session: Optional[requests.Session] = None, expected_statuses: tuple[int, ...] = (200,), **kwargs):
    client = session or requests
    response = client.request(method=method, url=url, timeout=8, **kwargs)
    if response.status_code not in expected_statuses:
        raise RuntimeError(f"unexpected status {response.status_code} from {url}")
    return response


def http_json(method: str, url: str, session: Optional[requests.Session] = None, expected_statuses: tuple[int, ...] = (200,), **kwargs):
    response = http_request(method, url, session=session, expected_statuses=expected_statuses, **kwargs)
    payload = {}
    if response.content:
        payload = response.json()
    return payload, response.status_code


def ensure_storefront_user(session: requests.Session):
    payload = {"email": SYNTHETIC_USER_EMAIL, "password": SYNTHETIC_USER_PASSWORD}
    register_response, register_status = http_json(
        "POST",
        f"{PHP_STOREFRONT_URL}/api/register",
        session=session,
        expected_statuses=(201, 409),
        json=payload,
    )
    if register_status == 409:
        log("INFO", "synthetic user already exists", email=SYNTHETIC_USER_EMAIL)

    login_response, _ = http_json(
        "POST",
        f"{PHP_STOREFRONT_URL}/api/login",
        session=session,
        json=payload,
    )
    return {
        "register_status": register_status,
        "login_user_id": int(((login_response or {}).get("user") or {}).get("id") or 0),
        "register_response": register_response,
    }


def hit_fault_endpoints(session: requests.Session):
    results = {}
    for target in ("mysql", "postgres", "redis", "php", "nodejs", "java", "python"):
        payload, status = http_json(
            "POST",
            f"{PHP_STOREFRONT_URL}/api/fault/{target}",
            session=session,
        )
        results[target] = {"status": status, "ok": bool(payload.get("ok")), "error": str(payload.get("error", ""))}
        pause_between_keepalive_calls()
    return results


def hit_direct_service_endpoints(session: requests.Session):
    node_health, _ = http_json("GET", f"{NODE_SERVICE_URL}/healthz", session=session)
    pause_between_keepalive_calls()
    node_ok, node_status = http_json("GET", f"{NODE_SERVICE_URL}/inventory", session=session, expected_statuses=(200, 503))
    pause_between_keepalive_calls()
    node_fail, node_fail_status = http_json("GET", f"{NODE_SERVICE_URL}/inventory?fail=1", session=session, expected_statuses=(503,))
    pause_between_keepalive_calls()

    java_health, _ = http_json("GET", f"{JAVA_SERVICE_URL}/healthz", session=session)
    pause_between_keepalive_calls()
    java_ok, java_status = http_json("GET", f"{JAVA_SERVICE_URL}/quote", session=session, expected_statuses=(200, 503))
    pause_between_keepalive_calls()
    java_fail, java_fail_status = http_json("GET", f"{JAVA_SERVICE_URL}/quote?fail=1", session=session, expected_statuses=(503,))
    pause_between_keepalive_calls()

    python_health, _ = http_json("GET", f"{PYTHON_PUBLIC_URL}/healthz", session=session)
    pause_between_keepalive_calls()
    python_ok, python_status = http_json("GET", f"{PYTHON_PUBLIC_URL}/recommendations?user_id=1", session=session, expected_statuses=(200, 503))
    pause_between_keepalive_calls()
    python_fail, python_fail_status = http_json("GET", f"{PYTHON_PUBLIC_URL}/recommendations?user_id=1&fail=1", session=session, expected_statuses=(503,))

    return {
        "node": {
            "health_ok": bool(node_health.get("ok")),
            "status": node_status,
            "items": len(node_ok.get("items", [])),
            "fail_status": node_fail_status,
            "fail_error": str(node_fail.get("error", "")),
        },
        "java": {
            "health_ok": bool(java_health.get("ok")),
            "status": java_status,
            "quote": float(java_ok.get("quote") or 0.0) if java_status == 200 else 0.0,
            "fail_status": java_fail_status,
            "fail_error": str(java_fail.get("error", "")),
        },
        "python": {
            "health_ok": bool(python_health.get("ok")),
            "status": python_status,
            "items": len(python_ok.get("items", [])),
            "fail_status": python_fail_status,
            "fail_error": str(python_fail.get("error", "")),
        },
    }


def run_dashboard_keepalive_cycle():
    cycle_id = f"keepalive-{uuid.uuid4().hex[:12]}"

    with requests.Session() as session:
        session.headers.update({"x-telemetry-source": "python-dashboard-keepalive"})

        with tracer.start_as_current_span(
            "python.dashboard_keepalive",
            kind=SpanKind.INTERNAL,
            attributes={
                "keepalive.cycle_id": cycle_id,
                "keepalive.interval_seconds": KEEPALIVE_INTERVAL_SECONDS,
                "keepalive.rounds_per_cycle": KEEPALIVE_ROUNDS_PER_CYCLE,
            },
        ) as span:
            try:
                span.set_attribute("keepalive.php_base_url", PHP_STOREFRONT_URL)
                span.set_attribute("keepalive.synthetic_user", SYNTHETIC_USER_EMAIL)

                user_state = ensure_storefront_user(session)
                login_failure_payload, login_failure_status = http_json(
                    "POST",
                    f"{PHP_STOREFRONT_URL}/api/login",
                    session=session,
                    expected_statuses=(401,),
                    json={"email": SYNTHETIC_USER_EMAIL, "password": SYNTHETIC_USER_PASSWORD + "-wrong"},
                )
                pause_between_keepalive_calls()

                summary_statuses = []
                checkout_statuses = []
                checkout_totals = []
                component_error_counts = []
                invalid_checkout_statuses = []
                empty_checkout_statuses = []
                logout_statuses = []
                not_found_statuses = []
                direct_statuses = []
                all_faults = {}

                http_request("GET", f"{PHP_STOREFRONT_URL}/", session=session)
                pause_between_keepalive_calls()
                http_request("GET", f"{PHP_STOREFRONT_URL}/auth", session=session)
                pause_between_keepalive_calls()
                http_json("GET", f"{PHP_STOREFRONT_URL}/healthz", session=session)
                pause_between_keepalive_calls()
                http_json("GET", f"{PHP_STOREFRONT_URL}/api/me", session=session)
                pause_between_keepalive_calls()

                for round_index in range(KEEPALIVE_ROUNDS_PER_CYCLE):
                    summary_payload, summary_status = http_json(
                        "GET",
                        f"{PHP_STOREFRONT_URL}/api/summary?round={round_index}",
                        session=session,
                        expected_statuses=(200, 206),
                    )
                    summary_statuses.append(summary_status)
                    component_error_counts.append(int(summary_payload.get("component_errors", 0)))
                    pause_between_keepalive_calls()

                    checkout_payload, checkout_status = http_json(
                        "POST",
                        f"{PHP_STOREFRONT_URL}/api/checkout",
                        session=session,
                        json={"items": [{"sku": "SKU-100", "quantity": 1}, {"sku": "SKU-101", "quantity": 2}, {"sku": "SKU-102", "quantity": 1}]},
                    )
                    checkout_statuses.append(checkout_status)
                    checkout_totals.append(float(checkout_payload.get("order_total") or 0.0))
                    pause_between_keepalive_calls()

                    _, orders_status = http_json("GET", f"{PHP_STOREFRONT_URL}/api/orders", session=session)
                    pause_between_keepalive_calls()

                    invalid_checkout_payload, invalid_checkout_status = http_json(
                        "POST",
                        f"{PHP_STOREFRONT_URL}/api/checkout",
                        session=session,
                        expected_statuses=(500,),
                        json={"items": [{"sku": f"SKU-DOES-NOT-EXIST-{round_index}", "quantity": 1}]},
                    )
                    invalid_checkout_statuses.append(invalid_checkout_status)
                    pause_between_keepalive_calls()

                    _, empty_checkout_status = http_json(
                        "POST",
                        f"{PHP_STOREFRONT_URL}/api/checkout",
                        session=session,
                        expected_statuses=(422,),
                        json={"items": []},
                    )
                    empty_checkout_statuses.append(empty_checkout_status)
                    pause_between_keepalive_calls()

                    http_json(
                        "GET",
                        f"{PHP_STOREFRONT_URL}/api/does-not-exist-{round_index}-{random.randint(100, 999)}",
                        session=session,
                        expected_statuses=(404,),
                    )
                    not_found_statuses.append(404)
                    pause_between_keepalive_calls()

                    http_json("GET", f"{PHP_STOREFRONT_URL}/api/error?round={round_index}", session=session, expected_statuses=(500,))
                    pause_between_keepalive_calls()

                    fault_results = hit_fault_endpoints(session)
                    all_faults.update(fault_results)
                    pause_between_keepalive_calls()

                    direct_results = hit_direct_service_endpoints(session)
                    direct_statuses.extend([
                        direct_results["node"]["status"],
                        direct_results["node"]["fail_status"],
                        direct_results["java"]["status"],
                        direct_results["java"]["fail_status"],
                        direct_results["python"]["status"],
                        direct_results["python"]["fail_status"],
                    ])
                    pause_between_keepalive_calls()

                    _, orders_status_after = http_json("GET", f"{PHP_STOREFRONT_URL}/api/orders", session=session)
                    span.set_attribute(f"keepalive.orders_status_round_{round_index}", orders_status_after)
                    pause_between_keepalive_calls()

                _, logout_status = http_json("POST", f"{PHP_STOREFRONT_URL}/api/logout", session=session)
                logout_statuses.append(logout_status)

                recommendation_payload, recommendation_status = http_json(
                    "GET",
                    f"{PYTHON_PUBLIC_URL}/recommendations?user_id=1",
                    session=session,
                    expected_statuses=(200, 503),
                )

                span.set_attribute("keepalive.login_failure_status", login_failure_status)
                span.set_attribute("keepalive.summary_status", max(summary_statuses) if summary_statuses else 0)
                span.set_attribute("keepalive.checkout_status", max(checkout_statuses) if checkout_statuses else 0)
                span.set_attribute("keepalive.orders_status", 200)
                span.set_attribute("keepalive.invalid_checkout_status", max(invalid_checkout_statuses) if invalid_checkout_statuses else 0)
                span.set_attribute("keepalive.empty_checkout_status", max(empty_checkout_statuses) if empty_checkout_statuses else 0)
                span.set_attribute("keepalive.logout_status", max(logout_statuses) if logout_statuses else 0)
                span.set_attribute("keepalive.not_found_status", max(not_found_statuses) if not_found_statuses else 0)
                span.set_attribute("keepalive.summary_degraded", any(status == 206 for status in summary_statuses))
                span.set_attribute("keepalive.checkout_success", any(status == 200 for status in checkout_statuses))
                span.set_attribute("keepalive.order_total", round(sum(checkout_totals), 2))
                span.set_attribute("keepalive.recommendation_items", len(recommendation_payload.get("items", [])))
                span.set_attribute("keepalive.recommendation_status", recommendation_status)
                span.set_attribute("keepalive.register_status", int(user_state["register_status"]))
                span.set_attribute("keepalive.synthetic_user_id", int(user_state["login_user_id"]))
                span.set_attribute("keepalive.round_count", KEEPALIVE_ROUNDS_PER_CYCLE)
                span.set_attribute("keepalive.component_errors", sum(component_error_counts))
                span.set_attribute("keepalive.direct_error_count", len([status for status in direct_statuses if status >= 500]))
                span.set_attribute("keepalive.fault_target_count", len(all_faults))

                log(
                    "INFO",
                    "dashboard keepalive cycle completed",
                    cycle_id=cycle_id,
                    synthetic_user=SYNTHETIC_USER_EMAIL,
                    register_status=user_state["register_status"],
                    summary_status=max(summary_statuses) if summary_statuses else 0,
                    checkout_status=max(checkout_statuses) if checkout_statuses else 0,
                    orders_status=200,
                    invalid_checkout_status=max(invalid_checkout_statuses) if invalid_checkout_statuses else 0,
                    logout_status=max(logout_statuses) if logout_statuses else 0,
                    order_total=round(sum(checkout_totals), 2),
                    summary_degraded=any(status == 206 for status in summary_statuses),
                    component_errors=sum(component_error_counts),
                    recommendation_items=len(recommendation_payload.get("items", [])),
                    interval_seconds=KEEPALIVE_INTERVAL_SECONDS,
                    round_count=KEEPALIVE_ROUNDS_PER_CYCLE,
                    login_failure_status=login_failure_status,
                    empty_checkout_status=max(empty_checkout_statuses) if empty_checkout_statuses else 0,
                    not_found_status=max(not_found_statuses) if not_found_statuses else 0,
                    fault_targets=",".join(sorted(all_faults.keys())),
                    direct_error_count=len([status for status in direct_statuses if status >= 500]),
                    invalid_checkout_error=str(invalid_checkout_payload.get("error", "")),
                    login_failure_error=str(login_failure_payload.get("error", "")),
                )
            except Exception as error:
                span.record_exception(error)
                apply_error_attributes(span, attach_error_context(error, keepalive_cycle_id=cycle_id, keepalive_component="dashboard_keepalive"))
                span.set_status(Status(StatusCode.ERROR, str(error)))
                log("ERROR", "dashboard keepalive cycle failed", cycle_id=cycle_id, error=str(error), error_type=error.__class__.__name__)


def start_keepalive_loop():
    def loop():
        time.sleep(KEEPALIVE_START_DELAY_SECONDS)
        while True:
            run_dashboard_keepalive_cycle()
            time.sleep(KEEPALIVE_INTERVAL_SECONDS)

    worker = threading.Thread(target=loop, name="dashboard-keepalive", daemon=True)
    worker.start()
    return worker


def start_resource_sampler_loop():
    def loop():
        time.sleep(RESOURCE_SAMPLE_INTERVAL_SECONDS)
        while True:
            record_process_resource_metrics("background", component="resource_sampler", emit_log=True)
            time.sleep(RESOURCE_SAMPLE_INTERVAL_SECONDS)

    worker = threading.Thread(target=loop, name="resource-sampler", daemon=True)
    worker.start()
    return worker


@app.before_request
def before_request():
    request._start_time = time.perf_counter()
    extracted = propagate.extract(dict(request.headers))
    request._otel_token = otel_context.attach(extracted)
    request._request_id = request.headers.get("x-request-id") or f"req-{uuid.uuid4().hex[:16]}"


@app.after_request
def after_request(response):
    duration = (time.perf_counter() - request._start_time) * 1000
    attrs = {"route": request.path, "status": response.status_code}
    request_counter.add(1, attrs)
    latency_histogram.record(duration, attrs)
    record_process_resource_metrics("request", route=request.path, status=response.status_code)
    response.headers["x-request-id"] = request._request_id
    log("INFO", "python request complete", path=request.path, status=response.status_code, duration_ms=round(duration, 2), request_id=request._request_id)
    token = getattr(request, "_otel_token", None)
    if token is not None:
        otel_context.detach(token)
    return response


@app.route("/healthz")
def healthz():
    return jsonify({"ok": True, "service": SERVICE_NAME})


@app.route("/recommendations")
def recommendations():
    user_id = int(request.args.get("user_id", "1"))
    with tracer.start_as_current_span("python.recommendations", kind=SpanKind.SERVER, attributes={"user.id": user_id, "http.route": "/recommendations", "http.method": "GET"}):
        span = trace.get_current_span()
        span.set_attribute("request.id", request._request_id)
        try:
            cache = get_redis()
            cache_key = f"recommendations:{user_id}"
            cached = trace_step(
                "python.redis.cache_get",
                {
                    "component.layer": "infrastructure",
                    "infra.kind": "cache",
                    "db.system": "redis",
                    "db.operation": "GET",
                    "db.redis.key": cache_key,
                    "server.address": os.getenv("REDIS_HOST", "redis"),
                    "server.port": int(os.getenv("REDIS_PORT", "6379")),
                    "bottleneck.active": False,
                },
                lambda: cache.get(cache_key),
            )
            if cached and not DB_BOTTLENECK_MODE:
                payload = json.loads(cached)
                payload["request_id"] = request._request_id
                log("INFO", "served recommendations from cache", user_id=user_id, request_id=request._request_id)
                return jsonify(payload)

            with trace_step(
                "python.postgres.connect",
                {
                    "component.layer": "infrastructure",
                    "infra.kind": "database",
                    "db.system": "postgresql",
                    "db.operation": "CONNECT",
                    "server.address": os.getenv("POSTGRES_HOST", "postgres"),
                    "server.port": int(os.getenv("POSTGRES_PORT", "5432")),
                    "bottleneck.active": DB_BOTTLENECK_MODE,
                },
                get_pg_connection,
            ) as conn, conn.cursor() as cur:
                waste_queries = 0
                transaction_id = f"python-pg-{uuid.uuid4().hex[:10]}"
                operation_sequence = []
                last_query_type = "read"

                if DB_BOTTLENECK_MODE:
                    trace_step(
                        "python.postgres.select_for_update",
                        {
                            "component.layer": "infrastructure",
                            "infra.kind": "database",
                            "db.system": "postgresql",
                            "db.operation": "SELECT",
                            "db.query_type": "select_for_update",
                            "db.sql.table": "users",
                            "server.address": os.getenv("POSTGRES_HOST", "postgres"),
                            "server.port": int(os.getenv("POSTGRES_PORT", "5432")),
                            "bottleneck.active": True,
                        },
                        lambda: cur.execute("SELECT id FROM users WHERE id = 1 FOR UPDATE"),
                    )
                    operation_sequence.append("lock_user_row")
                    last_query_type = "select_for_update"
                    trace_step(
                        "python.postgres.lock_wait",
                        {
                            "component.layer": "infrastructure",
                            "infra.kind": "database",
                            "db.system": "postgresql",
                            "db.operation": "SELECT",
                            "db.query_type": "sleep",
                            "db.sql.table": "users",
                            "server.address": os.getenv("POSTGRES_HOST", "postgres"),
                            "server.port": int(os.getenv("POSTGRES_PORT", "5432")),
                            "bottleneck.active": True,
                        },
                        lambda: cur.execute("SELECT pg_sleep(0.15)"),
                    )
                    operation_sequence.append("hold_lock")
                    last_query_type = "sleep"
                    waste_queries += 2

                    def run_recommendation_count_loop():
                        nonlocal waste_queries, last_query_type
                        for _ in range(DB_BOTTLENECK_LOOPS):
                            cur.execute("SELECT COUNT(*) FROM recommendations WHERE user_id = %s", (user_id,))
                            cur.fetchone()
                            operation_sequence.append("count_recommendations")
                            last_query_type = "select_count"
                            waste_queries += 1

                    trace_step(
                        "python.postgres.recommendation_count_loop",
                        {
                            "component.layer": "infrastructure",
                            "infra.kind": "database",
                            "db.system": "postgresql",
                            "db.operation": "SELECT",
                            "db.query_type": "select_count",
                            "db.sql.table": "recommendations",
                            "user.id": user_id,
                            "db.operation_count": DB_BOTTLENECK_LOOPS,
                            "server.address": os.getenv("POSTGRES_HOST", "postgres"),
                            "server.port": int(os.getenv("POSTGRES_PORT", "5432")),
                            "bottleneck.active": True,
                        },
                        run_recommendation_count_loop,
                    )

                trace_step(
                    "python.postgres.recommendation_query",
                    {
                        "component.layer": "infrastructure",
                        "infra.kind": "database",
                        "db.system": "postgresql",
                        "db.operation": "SELECT",
                        "db.query_type": "join_recommendations",
                        "db.sql.table": "users,recommendations",
                        "user.id": user_id,
                        "server.address": os.getenv("POSTGRES_HOST", "postgres"),
                        "server.port": int(os.getenv("POSTGRES_PORT", "5432")),
                        "bottleneck.active": DB_BOTTLENECK_MODE,
                    },
                    lambda: cur.execute(
                        """
                        SELECT u.email, u.tier, r.sku, r.score
                        FROM users u
                        JOIN recommendations r ON r.user_id = u.id
                        WHERE u.id = %s
                        ORDER BY r.score DESC
                        """,
                        (user_id,),
                    ),
                )
                rows = cur.fetchall()

                tier_rows = [row[1] for row in rows]
                if DB_BOTTLENECK_MODE and rows:
                    def run_user_tier_lookup_loop():
                        nonlocal waste_queries, last_query_type, tier_rows
                        looked_up = []
                        for _index, _row in enumerate(rows):
                            cur.execute("SELECT tier FROM users WHERE id = %s", (user_id,))
                            tier_row = cur.fetchone()
                            looked_up.append(tier_row[0] if tier_row else _row[1])
                            operation_sequence.append("fetch_user_tier")
                            last_query_type = "select_tier"
                            waste_queries += 1
                        tier_rows = looked_up

                    trace_step(
                        "python.postgres.user_tier_lookup_loop",
                        {
                            "component.layer": "infrastructure",
                            "infra.kind": "database",
                            "db.system": "postgresql",
                            "db.operation": "SELECT",
                            "db.query_type": "select_tier",
                            "db.sql.table": "users",
                            "user.id": user_id,
                            "db.operation_count": len(rows),
                            "server.address": os.getenv("POSTGRES_HOST", "postgres"),
                            "server.port": int(os.getenv("POSTGRES_PORT", "5432")),
                            "bottleneck.active": True,
                        },
                        run_user_tier_lookup_loop,
                    )

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
            trace_step(
                "python.redis.cache_set",
                {
                    "component.layer": "infrastructure",
                    "infra.kind": "cache",
                    "db.system": "redis",
                    "db.operation": "SETEX",
                    "db.redis.key": cache_key,
                    "server.address": os.getenv("REDIS_HOST", "redis"),
                    "server.port": int(os.getenv("REDIS_PORT", "6379")),
                    "bottleneck.active": DB_BOTTLENECK_MODE,
                },
                lambda: cache.setex(cache_key, 5 if DB_BOTTLENECK_MODE else 20, json.dumps(payload)),
            )

            if request.args.get("fail") == "1":
                raise attach_error_context(
                    RuntimeError("python recommendation ranking failed while composing response"),
                    **{
                        "db.system": "postgresql",
                        "db.query_type": last_query_type,
                        "db.transaction_id": transaction_id,
                        "db.lock_target": "users.id=1",
                        "db.operation_sequence": " > ".join(operation_sequence),
                    },
                )

            if random.randint(1, 100) <= SERVICE_FAILURE_RATE_PERCENT:
                raise attach_error_context(
                    RuntimeError("python recommendation ranking failed while composing response"),
                    **{
                        "db.system": "postgresql",
                        "db.query_type": last_query_type,
                        "db.transaction_id": transaction_id,
                        "db.lock_target": "users.id=1",
                        "db.operation_sequence": " > ".join(operation_sequence),
                    },
                )

            log("INFO", "served recommendations from postgres", user_id=user_id, count=len(payload["items"]), request_id=request._request_id)
            return jsonify(payload)
        except Exception as error:
            error_counter.add(1, {"route": request.path})
            span.record_exception(error)
            apply_error_attributes(span, error)
            span.set_status(Status(StatusCode.ERROR, str(error)))
            log(
                "ERROR",
                "python recommendations failed",
                error=str(error),
                request_id=request._request_id,
                error_type=error.__class__.__name__,
                **extract_error_context(error),
            )
            return jsonify({"error": str(error), "service": SERVICE_NAME, "request_id": request._request_id}), 503


if __name__ == "__main__":
    log(
        "INFO",
        "starting python recommendation service",
        failure_rate_percent=SERVICE_FAILURE_RATE_PERCENT,
        db_bottleneck_loops=DB_BOTTLENECK_LOOPS,
        resource_sample_interval_seconds=RESOURCE_SAMPLE_INTERVAL_SECONDS,
        resource_warn_cpu_percent=RESOURCE_WARN_CPU_PERCENT,
        resource_warn_memory_mb=RESOURCE_WARN_MEMORY_MB,
    )
    if KEEPALIVE_ENABLED:
        start_keepalive_loop()
        log(
            "INFO",
            "dashboard keepalive enabled",
            interval_seconds=KEEPALIVE_INTERVAL_SECONDS,
            start_delay_seconds=KEEPALIVE_START_DELAY_SECONDS,
            round_count=KEEPALIVE_ROUNDS_PER_CYCLE,
            php_storefront_url=PHP_STOREFRONT_URL,
        )
    start_resource_sampler_loop()
    app.run(host="0.0.0.0", port=int(os.getenv("APP_PORT", "8000")), threaded=True)
