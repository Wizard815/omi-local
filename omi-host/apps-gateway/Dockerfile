# Build context is the repo root (see docker-compose.yml's omi-apps-gateway
# service) so this can COPY each standalone app's main.py in directly,
# instead of maintaining a separate image per app. Add a new app by adding
# one COPY line here and mounting it in gateway_main.py.
FROM python:3.11-slim

WORKDIR /app
COPY omi-host/apps-gateway/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY omi-host/apps-gateway/gateway_main.py .
COPY plugins/omi-wikipedia-app/main.py apps/wikipedia/main.py
COPY plugins/omi-open-library-app/main.py apps/open_library/main.py
COPY plugins/omi-open-meteo-app/main.py apps/open_meteo/main.py
COPY plugins/omi-openfoodfacts-app/main.py apps/openfoodfacts/main.py

USER 65532:65532
EXPOSE 8080
CMD ["uvicorn", "gateway_main:app", "--host", "0.0.0.0", "--port", "8080"]
