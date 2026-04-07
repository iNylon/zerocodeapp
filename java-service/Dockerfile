FROM maven:3.9-eclipse-temurin-21 AS build

WORKDIR /app
COPY pom.xml /app/pom.xml
COPY src /app/src
RUN mvn -q -DskipTests package

FROM eclipse-temurin:21-jre

WORKDIR /app
ARG OTEL_JAVA_AGENT_VERSION=2.23.0
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*
RUN mkdir -p /otel && \
    curl -fsSL -o /otel/opentelemetry-javaagent.jar "https://github.com/open-telemetry/opentelemetry-java-instrumentation/releases/download/v${OTEL_JAVA_AGENT_VERSION}/opentelemetry-javaagent.jar"
COPY --from=build /app/target/java-checkout-0.0.1.jar /app/app.jar

EXPOSE 8081

CMD ["java", "-jar", "/app/app.jar"]
