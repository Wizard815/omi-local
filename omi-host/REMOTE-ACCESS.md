# Remote access: NGINX + Cloudflare Tunnel (or Tailscale)

Use Omi away from home without opening a port on your router. Everything here is opt-in
and off by default; the LAN setup keeps working as before.

```
phone / desktop ──HTTPS──> Cloudflare ──tunnel (outbound from your server)──> cloudflared
                                                                                 │
                    omi-local:8000  (API, dashboard, /v4/listen)  <── omi-nginx:8080
                    omi-local:5232  (Radicale CalDAV, at /caldav/) <──┘
```

| Piece | What it does | Where |
|---|---|---|
| `omi-nginx` | Reverse proxy tuned for Omi: unbuffered chat streams, WebSocket upgrades, 200 MB uploads, CalDAV methods, real client IPs, a login rate limit | `proxy/nginx/omi.conf`, compose profiles `proxy`, `tunnel`, `tailscale` |
| `cloudflared` | A named Cloudflare Tunnel from a token. Outbound only | compose profile `tunnel` |
| `tailscale` | The alternative: your tailnet only (or Funnel), HTTPS by `tailscale serve` | compose profile `tailscale` |
| `proxy/check_remote.sh` | Checks a hostname from outside: TLS, auth, chat stream timing, WebSocket, CalDAV, upload limit | run it at home |
| `demo/proxy_demo.sh` | The same checks against a real nginx and backend on one machine, no Cloudflare needed | `demo/proxy_demo.sh` |

## 1. Cloudflare Tunnel, step by step

You need a domain whose DNS is on Cloudflare (a free plan works). Below it is `example.com`.

1. **Create the tunnel.** In the Cloudflare dashboard open **Zero Trust → Networks → Tunnels →
   Create a tunnel**, pick **Cloudflared**, name it `omi`. On the install page, copy the long
   token after `--token` (any of the OS tabs shows it). Don't run the install command: the
   compose service is the connector.
2. **Put the token in `omi-host/.env`** (never commit it; `.env` is not in git):
   ```
   OMI_REMOTE_ACCESS=tunnel
   CLOUDFLARE_TUNNEL_TOKEN=eyJ...           # the token from step 1
   PUBLIC_URL=https://omi.example.com
   DASHBOARD_USERNAME=admin                 # the dashboard at / has no login otherwise
   DASHBOARD_PASSWORD=<a long password>
   ```
3. **Add the hostnames** on the tunnel's **Public Hostname** tab (or **Published application
   routes**, depending on the dashboard version):

   | Subdomain | Domain | Service |
   |---|---|---|
   | `omi` | `example.com` | `HTTP` → `omi-nginx:8080` |
   | `caldav` (optional) | `example.com` | `HTTP` → `omi-nginx:8080` |
   | `studio` (optional, Hermes Studio) | `example.com` | `HTTP` → `omi-nginx:8080` |

   All of them go to nginx; it routes by hostname and path. CalDAV also works on the main
   hostname at `https://omi.example.com/caldav/`, so the `caldav` name is only for apps that
   insist on their own. For Studio, copy `proxy/nginx/extra/studio.conf.example` to
   `studio.conf` in the same directory and set its address.
4. **Start it:** `bash omi-host/run-omi-local-host.sh` (it reads `OMI_REMOTE_ACCESS`), or by
   hand: `cd omi-host && docker compose --profile tunnel up -d`. The tunnel shows **Healthy**
   in the dashboard within a few seconds; `docker logs omi-cloudflared` shows
   `Registered tunnel connection` four times.
5. **Let the login limiter see real callers.** Find nginx's address and put it in `.env`,
   then restart `omi-local`:
   ```
   docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}' omi-nginx
   OMI_TRUSTED_PROXY_NETWORKS=172.18.0.12/32      # what that printed
   ```
   nginx takes the caller from Cloudflare's `CF-Connecting-IP` (only from Docker networks,
   never from a LAN client) and hands it to the backend. Without this line every remote
   caller shares nginx's address, so a stranger's bad passwords could lock everyone out for
   a while. The address can change when `omi-nginx` is recreated; the compose subnet
   (e.g. `172.18.0.0/16`) also works, at the cost of trusting every container on it.
6. **Check it from outside** (a phone hotspot, not your Wi-Fi):
   ```
   OMI_USER=you OMI_PASSWORD=... CALDAV_USER=omi CALDAV_PASSWORD=... \
     bash omi-host/proxy/check_remote.sh https://omi.example.com
   ```
7. **Point the apps at it.**
   - **Phone:** on the login screen, **Server URL** `https://omi.example.com/`, then your
     username and password. Live audio follows on `wss://omi.example.com/v4/listen`.
     An installed app: sign out, then sign in with the new URL.
   - **Desktop (Windows/Linux):** **Server address** `https://omi.example.com` on the sign-in
     screen, or **Settings → Account → Server address → Test → Switch server**.
   - **Phone calendars** (DAVx5, Tasks.org): base URL `https://omi.example.com/caldav/`
     (or `https://caldav.example.com/`), user `CALDAV_USER`, password from
     `docker exec omi-local cat /data/secrets/caldav_password`.
   - The same URL works at home too (through Cloudflare); the LAN URL
     `http://<omi-host>:8000/` keeps working for anything that stays home.

**A locally-managed tunnel** (routes in a file instead of the dashboard):
`proxy/cloudflared/config.yml.example` has the ingress rules; its header says how to mount it.

## 2. Cloudflare's limits, and how each is handled

| Limit | What it means for Omi | Handled by |
|---|---|---|
| **~100 s without a response** (HTTP 524). Cloudflare gives up on an origin that sends nothing for about 100 s (Enterprise can raise it). | A chat answer from a slow local model can stay silent for minutes before its first word (`OMI_LOCAL_LLM_TIMEOUT_SECONDS`: the first event may take one model call, the whole turn four, 1200 s at the default 300). | The backend now sends its `think: Still working…` keep-alive every 20 s **from the start of the stream**, not only after the first model event (`backend/utils/retrieval/agentic.py`, `stream_wait`). Headers go out at once. The phone app already shows it as a status line and the desktop app drops it; neither puts it in the reply. *To verify on your account:* whether the 100 s also applies between chunks of a running stream or only before the first byte; the keep-alive covers both. |
| **Buffering / compression of streams** | A proxy that holds `text/event-stream` back would make the keep-alive useless. | nginx: `proxy_buffering off` and `X-Accel-Buffering: no` on every response. Cloudflare passes `text/event-stream` through as it arrives (*to verify*: keep the hostname's cache rules off the API; don't put a Worker in front of it). |
| **WebSockets** | Supported on every plan through tunnels (Network → WebSockets is on by default). An idle socket is closed after about 100 s (*to verify*), and Cloudflare restarts can drop any long socket. | `/v4/listen` sends a `ping` text frame every 10 s (`routers/listen/runtime.py`) and uvicorn sends a protocol-level ping every 20 s (its default); while recording, audio flows anyway. The app reconnects a dropped socket on its own. nginx keeps sockets up to 1300 s idle. |
| **Request body 100 MB** (Free and Pro; 200 MB Business, 500 MB Enterprise) | Larger uploads get a 413 from Cloudflare before they reach you. | Audio sync uploads in batches of five 60-second chunks (a few MB, `sync_upload_batch.dart`); chat files stop at 50 MB, speech profiles at 50 MB. **Can exceed it:** a transcript import of a file near 100 MB (the app's own cap is exactly 100 MB, multipart adds a little) and a **Limitless ZIP import** (no app-side cap; the server allows 100 MB). Import those on the LAN URL or over Tailscale, or split the export. nginx itself allows 210 MB, the server's own per-part limit (200 MB) plus overhead. |
| **Connections out** | cloudflared dials Cloudflare on port 7844 (QUIC/UDP, falling back to HTTP/2 over TCP). | `omi-cloudflared` is not on the egress firewall's list (it has to reach Cloudflare). If UDP is blocked upstream, add `TUNNEL_TRANSPORT_PROTOCOL: http2` to its environment. |

## 3. Cloudflare Access: optional, and not on the API hostname (for now)

Access puts a Cloudflare login page in front of a hostname. The phone and desktop apps
can't do that browser login, so there are two ways:

- **Service tokens.** An Access application with a *Service Auth* policy accepts requests that
  carry `CF-Access-Client-Id` and `CF-Access-Client-Secret` headers. The apps do **not** send
  these today; every request (including the `/v4/listen` upgrade and CalDAV clients) would
  need them. That is follow-up work in both apps.
- **No Access on the API hostname, relying on Omi's own login** (recommended for now). Every
  API route needs Omi's session token (argon2-checked password, signed token, rate-limited
  login); the dashboard needs `DASHBOARD_USERNAME`/`DASHBOARD_PASSWORD`; Radicale needs its
  own password. Add at Cloudflare:
  - a **rate limiting rule** (Security → WAF → Rate limiting rules): URI path equals
    `/v1/auth/local-login`, e.g. 10 requests per minute per IP, block for 10 minutes;
  - optionally a **custom WAF rule** that blocks countries you never travel to, or
    challenges requests to `/` (the dashboard) but **not** to `/v1/`, `/v2/`, `/v3/`, `/v4/`,
    `/caldav/` (the apps can't answer a challenge).

Do use Access for hostnames only a browser opens, such as `studio.example.com`.

## 4. Tailscale instead

Only your own devices (the tailnet) can reach the server; nothing is public unless you turn
on Funnel. Each device needs the Tailscale app.

1. In the Tailscale admin console: **DNS** → enable **MagicDNS** and **HTTPS Certificates**;
   **Settings → Keys** → generate an auth key.
2. `.env`: `OMI_REMOTE_ACCESS=tailscale`, `TS_AUTHKEY=tskey-auth-...`, `TS_HOSTNAME=omi`.
3. `docker compose --profile tailscale up -d` (or the run script). The container joins as
   `omi` and `tailscale serve` (`proxy/tailscale/serve.json`) publishes
   `https://omi.<your-tailnet>.ts.net/` → `omi-nginx:8080` with a real certificate
   (`${TS_CERT_DOMAIN}` in `serve.json` is filled in by the container). *To verify on first
   start:* `docker exec omi-tailscale tailscale serve status` lists the proxy; in userspace
   mode it reaches `omi-nginx` by its Docker name.
4. Apps: **Server URL** `https://omi.<your-tailnet>.ts.net/`; CalDAV at `.../caldav/`.
   `bash omi-host/proxy/check_remote.sh https://omi.<your-tailnet>.ts.net` from a device on the tailnet.
5. **Funnel** (public, optional): set `"AllowFunnel"` to `true` in `serve.json` and allow
   Funnel for the node in the tailnet policy. Then the security checklist below applies in
   full. *To verify:* Funnel's own limits on request size and idle time.

Tailscale has no 100 s cut and no 100 MB cap of its own (*to verify* for Funnel). The login
limiter sees every tailnet caller as the tailscale container; that is fine for your own devices.

## 5. Security checklist

- [ ] Omi's own login stays on: every remote device signs in with its username and password
      (`check_remote.sh` checks that the API refuses a caller without a token).
- [ ] `DASHBOARD_USERNAME` / `DASHBOARD_PASSWORD` set: the dashboard is served on the same
      hostname as the API.
- [ ] `OMI_TRUSTED_PROXY_NETWORKS` set to nginx's address (step 5), so login rate limits
      count real callers.
- [ ] Only nginx is behind the tunnel. Never route a hostname to the Firestore emulator (8085),
      the Auth emulator (9099), Redis (6380), the merge proxy (4000), omi-audio (8790) or the
      Firestore UI (8086): they have no login of their own. Compose already publishes those
      on loopback or not at all; keep it that way in `docker-compose.override.yml`.
- [ ] `omi-nginx` listens on the host's loopback only (`OMI_NGINX_PORT`); cloudflared and
      tailscale reach it on the Docker network.
- [ ] The tunnel token is only in `.env`. If it leaks, rotate it on the tunnel's page in the
      dashboard (Refresh token) and update `.env`.
- [ ] Cloudflare: a rate limiting rule on `/v1/auth/local-login`; Access on browser-only
      hostnames (Studio).
- [ ] Uploaded recordings stay behind signed links (`/_local/storage`); leave
      `OMI_LOCAL_STORAGE_ALLOW_UNSIGNED` unset.

**The egress guard is unchanged** ([NETWORK.md](NETWORK.md)). Remote access is *incoming*:
requests come in through the tunnel and answers go back on the same connection, so the
backend still reaches nothing outside the LAN. `omi-cloudflared` and `omi-tailscale` are the
only containers that talk to the internet, and only to Cloudflare / Tailscale; they are not
on `egress-firewall.sh`'s list, while `omi-nginx` is (it only needs the LAN). Rerun
`bash omi-host/egress-firewall.sh` after starting the profile (the run script does).

## 6. What the local demo proves

`omi-host/demo/proxy_demo.sh` runs the real backend (as `caldav_demo.sh` does) behind nginx
with `omi.conf`, plus a stand-in model that stays silent for 75 s before answering and a
second copy of the config whose idle limit is 30 s, standing in for Cloudflare's cut:

```
apt-get install nginx-light       # or OMI_NGINX_BIN=<nginx 1.27.3+> for the shipped-config row
OMI_RADICALE_BIN=<radicale> omi-host/demo/proxy_demo.sh
OMI_DEMO_IDLE_SECONDS=100 OMI_DEMO_LLM_DELAY=130 omi-host/demo/proxy_demo.sh   # Cloudflare's numbers
```

It checks: the shipped files pass `nginx -t`; login and Omi's own auth through nginx; a quick
chat streams unbuffered; the slow answer arrives through the idle limit, with keep-alives before
the first model event and no silence longer than the keep-alive interval, and the client-side
parse shows no keep-alive in the reply; the same silent model *without* keep-alive is cut
(504); `/v4/listen` upgrades (101) and its frames come back; CalDAV PROPFIND, MKCALENDAR, PUT,
REPORT and DELETE at `/caldav/` and on the `caldav.` hostname, and `/.well-known/caldav`; a
150 MB sync upload reaches the backend and 230 MB is refused by nginx.

## 7. Files

| File | |
|---|---|
| `proxy/nginx/omi.conf` | the nginx site (mounted as `conf.d/default.conf`) |
| `proxy/nginx/upstreams.conf` | where it sends traffic (`omi-local:8000`, `omi-local:5232`), re-resolved through Docker DNS |
| `proxy/nginx/extra/*.conf` | your own extra sites (`studio.conf.example`); not in git |
| `proxy/cloudflared/config.yml.example` | ingress rules for a locally-managed tunnel |
| `proxy/tailscale/serve.json` | `tailscale serve` config: HTTPS on 443 → nginx, Funnel off |
| `proxy/check_remote.sh` | the at-home check |
| `demo/proxy_demo.sh`, `demo/proxy_demo.py`, `demo/slow_llm_stub.py` | the local demo |
