FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir locust==2.29.1

COPY locustfile.py /app/locustfile.py

EXPOSE 8089
