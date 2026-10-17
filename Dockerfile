FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

RUN groupadd --gid 1000 appuser \
    && useradd --uid 1000 --gid appuser --create-home \
       --shell /usr/sbin/nologin appuser

COPY app ./app
COPY config.yaml ./

RUN mkdir -p /app/data /app/logs \
    && chown -R appuser:appuser /app

USER appuser

CMD ["python", "-m", "app", "--daemon"]
