FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir setuptools && \
    pip install --no-cache-dir -r /app/requirements.txt
RUN opentelemetry-bootstrap -a install

COPY app.py /app/app.py

EXPOSE 8000

CMD ["opentelemetry-instrument", "python", "/app/app.py"]
