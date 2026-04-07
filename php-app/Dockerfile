FROM composer:2 AS laravel-builder

WORKDIR /build

RUN composer create-project laravel/laravel:^10.0 /build/app --prefer-dist --no-interaction

WORKDIR /build/app

RUN composer require --no-interaction \
    open-telemetry/sdk \
    open-telemetry/exporter-otlp \
    open-telemetry/opentelemetry-auto-laravel \
    guzzlehttp/guzzle \
    nyholm/psr7 \
    symfony/http-client

COPY laravel-overlay /build/overlay

RUN cp -R /build/overlay/. /build/app

FROM php:8.3-cli

RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        $PHPIZE_DEPS \
        git \
        unzip \
        curl \
        libcurl4-openssl-dev \
        libpq-dev \
        libzip-dev; \
    docker-php-ext-install \
        curl \
        pdo \
        pdo_mysql \
        pdo_pgsql; \
    pecl install redis opentelemetry; \
    docker-php-ext-enable redis opentelemetry; \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY --from=laravel-builder /build/app /app

RUN mkdir -p /app/storage/framework/cache /app/storage/framework/sessions /app/storage/framework/views /app/storage/logs && \
    chmod -R 777 /app/storage /app/bootstrap/cache

EXPOSE 8080

CMD ["php", "artisan", "serve", "--host=0.0.0.0", "--port=8080"]
