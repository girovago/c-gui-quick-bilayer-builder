#!/usr/bin/env bash
# cgui-studio: avvio rapido (Linux / macOS).
# Il server gira sul TUO PC: nessun bisogno di altre macchine.
# Uso: ./run.sh            (server reale, porta 8756)
#      CGUI_PORT=9000 ./run.sh
#      CGUI_MOCK=1 ./run.sh   (demo senza login CHARMM-GUI né GROMACS)
set -euo pipefail
cd "$(dirname "$0")"

if ! command -v uv >/dev/null 2>&1; then
  echo "Manca 'uv' (gestore Python). Installa con:"
  echo "  curl -LsSf https://astral.sh/uv/install.sh | sh"
  echo "poi rilancia questo script."
  exit 1
fi

uv sync --quiet || { echo "uv sync fallito: controlla il messaggio sopra."; exit 1; }

# La GUI buildata (web/dist) non è nel repo: serve Node una volta sola.
if [ ! -f web/dist/index.html ]; then
  if ! command -v node >/dev/null 2>&1 || ! command -v npm >/dev/null 2>&1; then
    echo "Manca Node/npm (servono UNA volta sola per costruire la GUI)."
    echo "Installa Node LTS da https://nodejs.org e rilancia ./run.sh"
    exit 1
  fi
  echo "Prima esecuzione: costruisco la GUI con npm (pochi secondi)..."
  (cd web && (npm ci --no-audit --no-fund 2>/dev/null || npm install --no-audit --no-fund) && npm run build)
fi

PORT="${CGUI_PORT:-8756}"
URL="http://127.0.0.1:$PORT"
echo "cgui-studio su $URL  (Ctrl+C per fermare)"

# apre il browser dopo che il server è su
( sleep 5; xdg-open "$URL" >/dev/null 2>&1 || open "$URL" >/dev/null 2>&1; ) &

uv run cgui serve --port "$PORT"
