# OpenObserve OTEL PoC

Deze repo bevat een multi-service demo-app voor een OpenObserve PoC. De stack is bewust opgezet als een kleine observability-speeltuin met:

- `php-storefront` als hoofdapp
- `python-recommendation` voor PostgreSQL-gedreven aanbevelingen
- `node-catalog` voor MySQL-gedreven catalogusdata
- `java-checkout` voor checkout- en Redis-gerelateerde flows
- `mysql`, `postgres`, `redis`
- `otel-collector`
- `locust` als load generator

De app genereert bewust veel telemetrie:

- traces voor elke request en downstream call
- metrics voor requests, errors en latency
- JSON-logs via de collector
- foutpaden via chaos-endpoints en willekeurige errors

## Streams in OpenObserve

De collector stuurt signalen naar aparte streams:

- traces: `poc-traces`
- metrics: `poc-metrics`
- logs: `poc-logs`

Je kunt deze namen aanpassen in [`otel-collector-config.yaml`](/Users/dylan/CustomApp/otel-collector-config.yaml).

## Monitoring-intensiteit centraal regelen

De intensiteit van telemetrie kun je centraal aanpassen in [`otel-collector-config.yaml`](/Users/dylan/zerocodeapp/otel-collector-config.yaml), zonder de app-services opnieuw te deployen:

- Traces: pas `processors.probabilistic_sampler/traces.sampling_percentage` aan.
- Logs: voeg regels toe onder `processors.filter/logs.logs.log_record` om logrecords centraal te filteren.
- Metrics: voeg regels toe onder `processors.filter/metrics.metrics.metric` om ruisende metrics centraal te droppen.
- Batching: tune `batch/traces`, `batch/logs` en `batch/metrics` per signaaltype.
- Collector-loglevel: pas `service.telemetry.logs.level` aan.

Voorbeelden:

- Meer trace-detail tijdens incidenten: zet `sampling_percentage: 100`
- Minder trace-volume in steady state: zet `sampling_percentage: 10`
- Minder log-volume: filter bijvoorbeeld `INFO`- of `DEBUG`-achtige records centraal
- Minder metric-volume: drop proces- of resource-metrics die je tijdelijk niet nodig hebt

Let op: in deze repo worden metrics nu via OTLP push verstuurd, niet via scrape. Daardoor regel je metric-intensiteit hier centraal via filters en batching, niet via een scrape interval. Als je specifiek het scrape interval wilt kunnen aanpassen, moeten de services eerst scrape-bare Prometheus metrics exposen en moet de collector of Prometheus die endpoints scrapen.

## Zero-Code Instrumentation

In deze map is de observability-config niet langer primair in `docker-compose.yml` of in runtime-bootstrapcode per klantomgeving opgenomen, maar verplaatst naar een algemene platformlaag onder [`observability/`](/Users/dylan/zerocodeapp/observability).

Belangrijkste principe:

- klantapplicaties houden hun functionele code
- OpenTelemetry-config staat in gedeelde env-bestanden
- auto-instrumentation of agents worden in de container-runtime gestart
- per klantomgeving pas je deployment-config aan, niet de applicatiecode

Inrichting in deze repo:

- algemene OTEL-config in [`otel-common.env`](/Users/dylan/zerocodeapp/observability/otel-common.env)
- service-specifieke OTEL-runtimeconfig in [`php.env`](/Users/dylan/zerocodeapp/observability/php.env), [`python.env`](/Users/dylan/zerocodeapp/observability/python.env), [`node.env`](/Users/dylan/zerocodeapp/observability/node.env) en [`java.env`](/Users/dylan/zerocodeapp/observability/java.env)
- Python zero-code startup via `opentelemetry-instrument` in [`python-service/Dockerfile`](/Users/dylan/zerocodeapp/python-service/Dockerfile)
- Node zero-code startup via `NODE_OPTIONS=--require @opentelemetry/auto-instrumentations-node/register` in [`node.env`](/Users/dylan/zerocodeapp/observability/node.env)
- Java zero-code startup via de OpenTelemetry Java agent in [`java-service/Dockerfile`](/Users/dylan/zerocodeapp/java-service/Dockerfile) en [`java.env`](/Users/dylan/zerocodeapp/observability/java.env)

Praktische invulling in deze repo:

- Node, Python en Java gebruiken runtime-managed auto-instrumentation zonder handmatige SDK-bootstrap in de applicatiecode.
- De PHP-service is omgezet naar een Laravel-gebaseerde runtime, zodat ook daar auto-instrumentation via de OpenTelemetry PHP extension en Laravel auto-instrumentation op platformniveau kan worden gestart.
- De OTEL-config staat daarmee voor alle vier runtimes buiten de applicatiecode en wordt via env-bestanden en container-runtime geïnjecteerd.

## Starten

```bash
docker compose up --build
```

Beschikbare endpoints:

- app: `http://localhost:8080`
- locust UI: `http://localhost:8089`
- collector health: `http://localhost:13133`

## Belangrijkste flows

- De PHP-app roept Node, Python en Java aan.
- Node leest productdata uit MySQL.
- Python leest user/recommendation data uit PostgreSQL en gebruikt Redis caching.
- Python draait ook een keepalive-flow die de storefront periodiek door auth, summary, checkout en fault-paden laat lopen zodat dashboards continu metrics blijven ontvangen.
- Java simuleert checkout- en payment-achtige flows met Redis en foutinjectie.
- Alle runtimes sturen nu ook CPU- en geheugengebruik uit, inclusief process/service-context en threshold-logs voor resource-afwijkingen.
- Alle services schrijven structured logs naar `/var/telemetry-logs/*.log`, die door de collector worden ingelezen.
- Een Grafana-dashboard voor deze opzet staat in [`grafana/customapp-observability-deep-dive.json`](/Users/dylan/zerocodeapp/grafana/customapp-observability-deep-dive.json).

## Scope

Deze repo is opgeschoond naar een zero-code backend observability testopzet:

- geen handmatige OpenTelemetry bootstrap in de PHP-, Python-, NodeJS- of Java-applicatiecode
- geen browser-RUM of Playwright-runner in deze testvariant
- alle OTEL-configuratie op platformniveau via [`observability/`](/Users/dylan/zerocodeapp/observability) en [`docker-compose.yml`](/Users/dylan/zerocodeapp/docker-compose.yml)

## O2 verificatie (trace, logs, infra)

Na deployment van de laatste wijzigingen kun je in OpenObserve snel verifiëren:

1. `trace_id` en `request_id` aanwezig in logs.
2. Correlatie van trace naar logs.
3. Infrastructuurcomponenten zichtbaar in traces via `component.layer` en `infra.kind`.

Voorbeeld queries:

```sql
SELECT _timestamp, "service.name", trace_id, request_id, message
FROM "poc-logs"
WHERE _timestamp >= now() - INTERVAL '30 minutes'
ORDER BY _timestamp DESC
LIMIT 200;
```

```sql
SELECT _timestamp, trace_id, service_name, span_name, status_code, duration_ms, attributes
FROM "poc-traces"
WHERE _timestamp >= now() - INTERVAL '30 minutes'
	AND (
		attributes.component.layer = 'infrastructure'
		OR attributes.infra.kind IS NOT NULL
	)
ORDER BY _timestamp DESC
LIMIT 300;
```

```sql
SELECT _timestamp, "service.name", severity, message, trace_id, request_id, context
FROM "poc-logs"
WHERE _timestamp >= now() - INTERVAL '30 minutes'
	AND trace_id = '<trace_id_uit_traces>'
ORDER BY _timestamp DESC;
```
