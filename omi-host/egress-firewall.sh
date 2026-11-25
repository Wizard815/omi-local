#!/usr/bin/env bash
# Host firewall: the omi-local containers may reach the LAN, never the internet.
#
# Run on the Docker host as root (Unraid: from the shell, or the User Scripts
# plugin "At startup of array" after Docker is up). run-omi-local-host.sh calls
# it after `docker compose up`.
#
#   bash omi-host/egress-firewall.sh            # apply (idempotent; rebuilds the rules)
#   bash omi-host/egress-firewall.sh status     # rules, with packet counters
#   bash omi-host/egress-firewall.sh remove     # take the rules out
#   bash omi-host/egress-firewall.sh --dry-run  # print the commands, change nothing
#
# How: Docker sends container traffic through the FORWARD chain, and its
# DOCKER-USER chain is the place for your own rules (Docker never flushes it).
# This adds one jump to an OMI-EGRESS chain, rebuilt on every run, with four
# rules per container address:
#   replies to connections made *to* the container (the phone, the dashboard)  -> allow
#   anything to a private LAN range (llama.cpp, the NUC, the vault)            -> allow
#   everything else                                                            -> log "omi-egress-drop", drop
# Rules match each container's own address, so other containers on a shared
# network keep their internet. Traffic to the Docker host itself (e.g. llama.cpp
# on <server-ip>) goes through INPUT, not FORWARD, and is not affected.
#
# Container addresses change when a container is recreated: rerun this after
# `docker compose up` (the run script does) and after a reboot.
#
# What it can't stop: DNS lookups. Docker's resolver answers containers from
# the host, so a lookup of a public name still reaches your DNS server, even
# though the connection that would follow is dropped. Point the host at a
# resolver that only knows LAN names if lookups must not leave either.
#
# Env:
#   OMI_EGRESS_CONTAINERS  containers to lock down (default: the stack's own)
#   OMI_EGRESS_LAN         destinations to allow (default: RFC 1918 ranges)
#   IPTABLES, DOCKER       the binaries to use (tests swap them)
set -euo pipefail

ACTION=apply DRY=0
for a in "$@"; do
  case "$a" in
    apply|remove|status) ACTION=$a ;;
    --dry-run) DRY=1 ;;
    -h|--help) sed -n '2,/^set -euo/p' "$0" | sed 's/^# \{0,1\}//; /^set -euo/d'; exit 0 ;;
    *) echo "unknown argument: $a (apply | status | remove | --dry-run)" >&2; exit 2 ;;
  esac
done

IPT="${IPTABLES:-iptables}"
DOCKER="${DOCKER:-docker}"
CHAIN=OMI-EGRESS
# omi-searxng is left out on purpose: web search is its whole job. Add it to
# lock it down too (search then stops working).
CONTAINERS="${OMI_EGRESS_CONTAINERS:-omi-local omi-proxy omi-audio omi-apps-gateway omi-app-hermes-agent omi-firestore-ui-proxy omi-backup omi-health}"
LAN="${OMI_EGRESS_LAN:-10.0.0.0/8 172.16.0.0/12 192.168.0.0/16}"

run() {
  if [ "$DRY" = 1 ]; then echo "$IPT $*"; else "$IPT" "$@"; fi
}

if ! command -v "$IPT" >/dev/null 2>&1; then
  echo "egress-firewall: $IPT not found; run this on the Docker host" >&2
  exit 1
fi

case "$ACTION" in
  status)
    "$IPT" -S DOCKER-USER 2>/dev/null | grep -- "-j $CHAIN" || echo "not applied: DOCKER-USER has no jump to $CHAIN"
    "$IPT" -L "$CHAIN" -n -v 2>/dev/null || true
    exit 0 ;;
  remove)
    while "$IPT" -C DOCKER-USER -j "$CHAIN" 2>/dev/null; do run -D DOCKER-USER -j "$CHAIN"; [ "$DRY" = 1 ] && break; done
    if "$IPT" -L "$CHAIN" -n >/dev/null 2>&1; then run -F "$CHAIN"; run -X "$CHAIN"; fi
    echo "egress-firewall: removed"
    exit 0 ;;
esac

# Container -> its IPv4 addresses (one per attached network).
declare -a IPS=()
for c in $CONTAINERS; do
  addrs=$("$DOCKER" inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}' "$c" 2>/dev/null || true)
  [ -z "${addrs// /}" ] && continue
  for ip in $addrs; do IPS+=("$c=$ip"); done
  v6=$("$DOCKER" inspect -f '{{range .NetworkSettings.Networks}}{{.GlobalIPv6Address}}{{end}}' "$c" 2>/dev/null || true)
  [ -n "${v6// /}" ] && echo "egress-firewall: WARNING $c has an IPv6 address ($v6); these rules are IPv4 only" >&2
done
if [ "${#IPS[@]}" = 0 ]; then
  echo "egress-firewall: none of [$CONTAINERS] is running; start the stack first" >&2
  exit 1
fi

"$IPT" -L DOCKER-USER -n >/dev/null 2>&1 || run -N DOCKER-USER
if "$IPT" -L "$CHAIN" -n >/dev/null 2>&1; then run -F "$CHAIN"; else run -N "$CHAIN"; fi
for entry in "${IPS[@]}"; do
  ip=${entry#*=}
  run -A "$CHAIN" -s "$ip" -m conntrack --ctstate ESTABLISHED,RELATED -j RETURN
  for net in $LAN; do run -A "$CHAIN" -s "$ip" -d "$net" -j RETURN; done
  run -A "$CHAIN" -s "$ip" -m limit --limit 6/min -j LOG --log-prefix "omi-egress-drop ${entry%%=*} "
  run -A "$CHAIN" -s "$ip" -j DROP
done
# One jump, at the top of DOCKER-USER, whatever ran before.
while "$IPT" -C DOCKER-USER -j "$CHAIN" 2>/dev/null; do run -D DOCKER-USER -j "$CHAIN"; [ "$DRY" = 1 ] && break; done
run -I DOCKER-USER 1 -j "$CHAIN"

echo "egress-firewall: ${#IPS[@]} container address(es) may reach only [$LAN]: ${IPS[*]}"
echo "  dropped attempts are logged with the prefix 'omi-egress-drop' (dmesg / syslog)"
