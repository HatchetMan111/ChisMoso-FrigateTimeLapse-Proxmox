#!/usr/bin/env bash
# Chismoso-Server Proxmox Einzeiler (Community-Scripts-Stil)
# Upstream-App: https://github.com/DanielGarc/chismoso (kein Teil dieses Repos)
# Dieses Repo: Installer + FastAPI-Wrapper + systemd-Unit
set -euo pipefail

# ---------- Variablen (oben, anpassbar) ----------
APP="chismoso-server"
HOSTNAME="chismoso-server"
DEFAULT_CORES=2
DEFAULT_RAM=2048
DEFAULT_DISK=8
DEFAULT_PORT=8080
TEMPLATE_STORE="local"
STORAGE="local-lvm"
BRIDGE="vmbr0"
UNPRIVILEGED=1
UPSTREAM="https://github.com/DanielGarc/chismoso"
REPO_RAW="https://raw.githubusercontent.com/HatchetMan111/ChisMoso-FrigateTimeLapse-Proxmox/main"
FRIGATE_URL_DEFAULT="http://frigate:5000"

CT_ID="${CT_ID:-}"
CORES="${CORES:-$DEFAULT_CORES}"
RAM="${RAM:-$DEFAULT_RAM}"
DISK="${DISK:-$DEFAULT_DISK}"
DEBUG=0

LOG="/tmp/chismoso-server-install-$(date +%F-%H%M%S).log"
exec > >(tee -a "$LOG") 2>&1
trap 'echo "[ERR] Befehl: $BASH_COMMAND | Zeile: $LINENO | Exit: $? | Log: $LOG" >&2' ERR

usage() {
  echo "Usage: bash chismoso-server.sh [--ctid ID] [--cores N] [--memory MB] [--disk GB] [--bridge vmbr0] [--storage local-lvm] [--debug]"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --ctid) CT_ID="$2"; shift 2 ;;
    --cores) CORES="$2"; shift 2 ;;
    --memory) RAM="$2"; shift 2 ;;
    --disk) DISK="$2"; shift 2 ;;
    --bridge) BRIDGE="$2"; shift 2 ;;
    --storage) STORAGE="$2"; shift 2 ;;
    --debug) DEBUG=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unbekannte Option: $1" >&2; usage; exit 2 ;;
  esac
done

[[ "$DEBUG" == "1" ]] && set -x

echo "[INFO] Log: $LOG"

# ---------- Host-Checks ----------
command -v pveversion >/dev/null 2>&1 || { echo "[ERR] Kein Proxmox-Host (pveversion fehlt). Exit: 1" >&2; exit 1; }
command -v pct >/dev/null 2>&1 || { echo "[ERR] pct fehlt. Exit: 1" >&2; exit 1; }
command -v pvesh >/dev/null 2>&1 || { echo "[ERR] pvesh fehlt. Exit: 1" >&2; exit 1; }

# ---------- CT-ID: naechste freie ----------
if [[ -z "$CT_ID" ]]; then
  CT_ID="$(pvesh get /cluster/nextid)"
  echo "[INFO] Naechste freie CT-ID: $CT_ID"
else
  echo "[INFO] CT-ID vorgegeben: $CT_ID"
fi

# ---------- Idempotenz: CT existiert -> nur App-Update ----------
if pct status "$CT_ID" >/dev/null 2>&1; then
  echo "[INFO] CT $CT_ID existiert bereits -> Update-Modus (kein Neubau)."
  pct exec "$CT_ID" -- bash -c "cd /opt/chismoso-server 2>/dev/null && git pull --ff-only || true; .venv/bin/pip install -U -r app/requirements.txt; systemctl restart $APP; systemctl is-active $APP"
  IP="$(pct exec "$CT_ID" -- bash -c "ip -4 -o addr show eth0 | awk '{print \$4}' | cut -d/ -f1" || true)"
  echo ""
  echo "================ INSTALLATION ERFOLGREICH (UPDATE) ================"
  echo "  App       : $APP (Frigate-Wrapper)"
  echo "  Container : CT $CT_ID (Hostname: $HOSTNAME)"
  echo "  Web UI    : http://$IP:$DEFAULT_PORT"
  echo "  Health    : http://$IP:$DEFAULT_PORT/health"
  echo "  Log       : $LOG"
  echo "=================================================================="
  exit 0
fi

# ---------- Template sicherstellen ----------
echo "[INFO] Suche debian-12-standard Template auf Storage $TEMPLATE_STORE ..."
TPL="$(pveam list "$TEMPLATE_STORE" 2>/dev/null | grep -o 'debian-12-standard[^ ]*' | sort -V | tail -1 || true)"
if [[ -z "$TPL" ]]; then
  echo "[INFO] Kein Template gefunden -> pveam update + download ..."
  pveam update
  TPL_AVAIL="$(pveam available 2>/dev/null | grep -o 'debian-12-standard[^ ]*' | sort -V | tail -1)"
  [[ -z "$TPL_AVAIL" ]] && { echo "[ERR] Kein debian-12 Template verfuegbar. Exit: 1" >&2; exit 1; }
  pveam download "$TEMPLATE_STORE" "$TPL_AVAIL"
  TPL="$TPL_AVAIL"
fi
echo "[INFO] Template: $TEMPLATE_STORE:vztmpl/$TPL"

# ---------- Container erstellen ----------
echo "[INFO] Erstelle CT $CT_ID ($HOSTNAME, $CORES vCPU / $RAM MB / $DISK GB, unpriv=$UNPRIVILEGED, onboot=1) ..."
pct create "$CT_ID" "$TEMPLATE_STORE:vztmpl/$TPL" \
  --hostname "$HOSTNAME" \
  --cores "$CORES" --memory "$RAM" \
  --rootfs "$STORAGE:$DISK" \
  --net0 "name=eth0,bridge=$BRIDGE,ip=dhcp" \
  --unprivileged "$UNPRIVILEGED" \
  --features nesting=1 \
  --onboot 1 \
  --start 1

echo "[INFO] Warte auf Netzwerk ..."
for _ in $(seq 1 30); do
  pct exec "$CT_ID" -- true 2>/dev/null && break
  sleep 2
done

# ---------- Provisionierung im Container ----------
echo "[INFO] Provisioniere App im Container ..."
pct exec "$CT_ID" -- bash -c "set -euo pipefail
  export DEBIAN_FRONTEND=noninteractive
  apt-get update && apt-get install -y python3 python3-venv git curl iproute2
  id chismoso >/dev/null 2>&1 || useradd -m -s /bin/bash chismoso
  mkdir -p /opt/chismoso-server /var/lib/chismoso/exports
  chown -R chismoso:chismoso /opt/chismoso-server /var/lib/chismoso
"

# Wrapper-Code + Unit aus diesem Repo holen (Fallback: nur Health-Skelett)
pct push "$CT_ID" /dev/null /tmp/probe 2>/dev/null || true
pct exec "$CT_ID" -- bash -c "set -euo pipefail
  cd /opt/chismoso-server
  if [[ ! -d app ]]; then mkdir -p app systemd; fi
  curl -fsSL '$REPO_RAW/app/main.py' -o app/main.py || echo '[WARN] app/main.py konnte nicht geladen werden'
  curl -fsSL '$REPO_RAW/app/requirements.txt' -o app/requirements.txt || printf 'fastapi>=0.110\nuvicorn[standard]>=0.29\nhttpx>=0.27\n' > app/requirements.txt
  curl -fsSL '$REPO_RAW/systemd/chismoso-server.service' -o /etc/systemd/system/chismoso-server.service || {
    cat > /etc/systemd/system/chismoso-server.service <<'UNIT'
[Unit]
Description=Chismoso Server Wrapper
After=network-online.target
Wants=network-online.target
[Service]
User=chismoso
EnvironmentFile=/etc/chismoso-server.env
WorkingDirectory=/opt/chismoso-server
ExecStart=/opt/chismoso-server/.venv/bin/uvicorn main:app --host 0.0.0.0 --port 8080
Restart=always
RestartSec=5
[Install]
WantedBy=multi-user.target
UNIT
  }
  # Upstream-UI (nur ui/, shallow)
  if [[ ! -d upstream-ui ]]; then git clone --depth 1 --filter=blob:none --sparse '$UPSTREAM' /tmp/up 2>/dev/null && (cd /tmp/up && git sparse-checkout set ui && cp -r ui /opt/chismoso-server/upstream-ui) || echo '[WARN] Upstream-ui Clone uebersprungen'; fi
  # Chismoso-CLI aus Latest-Release (best effort)
  (curl -fsSL https://api.github.com/repos/DanielGarc/chismoso/releases/latest | grep -o 'https://[^\"]*linux[^\"]*x86_64[^\"]*' | head -1 | xargs -I{} curl -fsSL {} -o /tmp/chismoso-bin && install -m0755 /tmp/chismoso-bin /usr/local/bin/chismoso && echo '[INFO] chismoso-CLI installiert') || echo '[WARN] chismoso-CLI Download uebersprungen (503-Fallback aktiv)'
  python3 -m venv .venv
  .venv/bin/pip install -U pip
  .venv/bin/pip install -r app/requirements.txt
  echo 'FRIGATE_URL=${FRIGATE_URL:-$FRIGATE_URL_DEFAULT}' > /etc/chismoso-server.env
  echo 'PORT=$DEFAULT_PORT' >> /etc/chismoso-server.env
  chown -R chismoso:chismoso /opt/chismoso-server
  systemctl daemon-reload
  systemctl enable --now $APP
"

# ---------- Verifikation ----------
echo "[INFO] Verifiziere Service ..."
pct exec "$CT_ID" -- systemctl is-active "$APP" || {
  echo "[ERR] Service $APP nicht aktiv. Journal:" >&2
  pct exec "$CT_ID" -- journalctl -u "$APP" -n 100 --no-pager >&2 || true
  exit 1
}
echo "[OK] Service laeuft (systemctl is-active $APP = active)."

echo "[INFO] Verifiziere Web UI (localhost:$DEFAULT_PORT) ..."
pct exec "$CT_ID" -- bash -c "curl -fs http://localhost:$DEFAULT_PORT/health" || {
  echo "[ERR] Web UI antwortet nicht auf localhost:$DEFAULT_PORT/health" >&2
  pct exec "$CT_ID" -- journalctl -u "$APP" -n 100 --no-pager >&2 || true
  exit 1
}
pct exec "$CT_ID" -- bash -c "curl -fs http://localhost:$DEFAULT_PORT/ | grep -qi chismoso" || {
  echo "[ERR] / enthaelt kein chismoso-Marker" >&2
  exit 1
}
echo "[OK] Web UI antwortet (HTTP 200 auf localhost:$DEFAULT_PORT/health)."

IP="$(pct exec "$CT_ID" -- bash -c "ip -4 -o addr show eth0 | awk '{print \$4}' | cut -d/ -f1")"
echo ""
echo "================ INSTALLATION ERFOLGREICH ================"
echo "  App       : $APP - Frigate Timelapse/Live Wrapper"
echo "  Container : CT $CT_ID (Hostname: $HOSTNAME, unprivilegiert, onboot=1)"
echo "  Ressourcen: $CORES vCPU / $RAM MB RAM / $DISK GB Disk"
echo "  Web UI    : http://$IP:$DEFAULT_PORT"
echo "  Health    : http://$IP:$DEFAULT_PORT/health"
echo "  Upstream  : $UPSTREAM"
echo "  Log       : $LOG"
echo "=========================================================="
