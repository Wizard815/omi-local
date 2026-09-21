# HTTP Basic Auth gate in front of the Firestore Emulator UI (see
# omi-host/entrypoint.sh — the UI itself has no authentication).
FROM nginx:alpine

RUN apk add --no-cache apache2-utils

COPY nginx.conf /etc/nginx/nginx.conf
COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh

ENTRYPOINT ["/docker-entrypoint.sh"]
