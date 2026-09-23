# Build context is the repo root (see docker-compose.yml's omi-apps-gateway
# service). This image only has the gateway's own code baked in —
# plugins/ (the actual apps) is bind-mounted at runtime (see
# docker-compose.yml's volumes:), not COPYed here, so enabling another
# already-present app is an OMI_APPS_LIST + restart, not a rebuild.
FROM python:3.11-slim

WORKDIR /app
COPY omi-host/apps-gateway/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY omi-host/apps-gateway/gateway_main.py .

USER 65532:65532
EXPOSE 8080
CMD ["uvicorn", "gateway_main:app", "--host", "0.0.0.0", "--port", "8080"]
