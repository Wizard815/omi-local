"""Remote access (REMOTE-ACCESS.md): the settings a tunnel depends on stay in place.

Static checks of proxy/nginx/omi.conf, the compose services and the firewall list. The
behaviour itself is proven end to end by demo/proxy_demo.sh (real nginx, real backend).
Run: python3 omi-host/test_remote_access.py
"""

import re
import subprocess
from pathlib import Path

HERE = Path(__file__).parent
CONF = (HERE / "proxy" / "nginx" / "omi.conf").read_text()
COMPOSE = (HERE / "docker-compose.yml").read_text()

# Longest chat turn on a slow local model: 4 model calls at the default 300 s, plus setup.
LONGEST_CHAT_SECONDS = 4 * 300 + 25


def seconds(directive: str) -> int:
    values = [int(v) for v in re.findall(rf"^\s*{directive}\s+(\d+)s;", CONF, re.M)]
    assert values, f"{directive} missing"
    return min(values)


def test_chat_streams_are_unbuffered_and_outlast_a_slow_turn():
    api = CONF.split("server_name ~^caldav")[0]
    assert re.search(r"^\s*proxy_buffering off;", api, re.M)
    assert "add_header X-Accel-Buffering no always;" in api
    assert int(re.search(r"proxy_read_timeout (\d+)s;", api).group(1)) >= LONGEST_CHAT_SECONDS + 60
    assert int(re.search(r"proxy_send_timeout (\d+)s;", api).group(1)) >= LONGEST_CHAT_SECONDS + 60
    assert seconds("proxy_read_timeout") >= 300


def test_websockets_upgrade_and_uploads_fit_the_servers_own_limit():
    assert re.search(r"map \$http_upgrade \$omi_connection \{\s*default upgrade;", CONF)
    assert "proxy_set_header Upgrade $http_upgrade;" in CONF
    assert "proxy_set_header Connection $omi_connection;" in CONF
    size = int(re.search(r"client_max_body_size (\d+)m;", CONF).group(1))
    assert size >= 200, "audio sync parts may be 200 MB (backend/utils/multipart.py)"


def test_forwarded_caller_is_believed_only_from_docker_networks():
    trusted = re.findall(r"set_real_ip_from ([^;]+);", CONF)
    assert trusted and all(net in ("172.16.0.0/12", "127.0.0.1") for net in trusted)
    assert "proxy_set_header CF-Connecting-IP $remote_addr;" in CONF
    assert "proxy_set_header X-Forwarded-For $remote_addr;" in CONF


def test_caldav_is_served_under_one_hostname():
    assert "location /caldav/ {" in CONF
    assert "proxy_set_header X-Script-Name /caldav;" in CONF


def test_tunnel_token_comes_from_env_and_nginx_stays_off_the_lan():
    assert "TUNNEL_TOKEN: ${CLOUDFLARE_TUNNEL_TOKEN:-}" in COMPOSE
    assert not re.search(r"eyJ[A-Za-z0-9_-]{20,}", COMPOSE), "a tunnel token is committed"
    nginx = COMPOSE.split("  omi-nginx:")[1].split("\n  cloudflared:")[0]
    assert '"127.0.0.1:${OMI_NGINX_PORT:-8088}:8080"' in nginx
    for profile in ("proxy", "tunnel", "tailscale"):
        assert profile in re.search(r"profiles: \[([^\]]+)\]", nginx).group(1)


def test_only_the_tunnel_containers_may_reach_the_internet():
    out = subprocess.run(["bash", "-c", f"grep -o 'OMI_EGRESS_CONTAINERS:-[^}}]*' {HERE / 'egress-firewall.sh'}"],
                         capture_output=True, text=True, check=True).stdout
    names = out.split(":-", 1)[1].split()
    assert "omi-nginx" in names
    assert "omi-cloudflared" not in names and "omi-tailscale" not in names


def test_scripts_parse():
    for script in ("proxy/check_remote.sh", "demo/proxy_demo.sh", "run-omi-local-host.sh"):
        subprocess.run(["bash", "-n", str(HERE / script)], check=True)


def main() -> None:
    tests = [
        test_chat_streams_are_unbuffered_and_outlast_a_slow_turn,
        test_websockets_upgrade_and_uploads_fit_the_servers_own_limit,
        test_forwarded_caller_is_believed_only_from_docker_networks,
        test_caldav_is_served_under_one_hostname,
        test_tunnel_token_comes_from_env_and_nginx_stays_off_the_lan,
        test_only_the_tunnel_containers_may_reach_the_internet,
        test_scripts_parse,
    ]
    for t in tests:
        t()
        print(f"PASS: {t.__name__}")
    print(f"\nAll {len(tests)} tests passed.")


if __name__ == "__main__":
    main()
