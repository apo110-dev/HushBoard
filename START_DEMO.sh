#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
umask 077
mkdir -p .runtime data
chmod 700 .runtime data
export PYTHONUNBUFFERED=1
export HOST="${HOST:-127.0.0.1}"
export PORT="${PORT:-4173}"

say() { printf '
[1;33m[hushboard][0m %s
' "$*"; }
die() { printf '
[hushboard] ERROR: %s
' "$*" >&2; exit 1; }
command -v uv >/dev/null || die 'uv bulunamadi: https://docs.astral.sh/uv/'
command -v curl >/dev/null || die 'curl bulunamadi.'
command -v python3 >/dev/null || die 'python3 bulunamadi.'
command -v git >/dev/null || die 'git bulunamadi.'
ALLOW_DEGRADED="${HUSHBOARD_ALLOW_DEGRADED:-0}"
FORCE_OFFLINE=0
case "${HUSHBOARD_MODE:-}" in
  mock|offline) FORCE_OFFLINE=1; ALLOW_DEGRADED=1;;
esac
case "$HOST" in
  127.0.0.1|localhost|::1) ;;
  *) die 'Guvenlik icin HOST yalniz loopback olabilir (127.0.0.1/localhost/::1).';;
esac
case "$PORT" in
  ''|*[!0-9]*) die 'PORT sayisal olmali.';;
esac
[ "$PORT" -ge 1 ] 2>/dev/null && [ "$PORT" -le 65535 ] 2>/dev/null || die 'PORT 1..65535 araliginda olmali.'

if [ ! -f .env ]; then
  cp .env.example .env
  chmod 600 .env
fi

LIVE_DB_PATH="$(python3 - <<'PY'
from app.config import Settings
print(Settings.from_env().database_path)
PY
)"

wallet_start_ok=1
if [ "$FORCE_OFFLINE" = 1 ]; then
  wallet_start_ok=0
  say 'HUSHBOARD_MODE ile OFFLINE REPLAY acikca istendi; wallet servisleri atlandi.'
elif ! command -v docker >/dev/null 2>&1; then
  if [ "$ALLOW_DEGRADED" = 1 ]; then
    wallet_start_ok=0
    say 'Docker yok; wallet baslatilmadan OFFLINE REPLAY kullanilacak.'
  else
    die 'Docker bulunamadi.'
  fi
elif [ ! -f .runtime/wallets.json ]; then
  if [ "$ALLOW_DEGRADED" = 1 ]; then
    wallet_start_ok=0
    say 'Wallet kurulumu yok; cold sync yerine OFFLINE REPLAY kullanilacak.'
  else
    say 'Ilk kurulum: Zcash testnet wallet altyapisi hazirlaniyor.'
    ./scripts/bootstrap-testnet-wallets.sh
  fi
else
  say 'Zebra control-plane loopback bind dogrulaniyor; Zallet servisleri baslatiliyor.'
  if ! ./scripts/ensure-zebra-loopback.sh \
    || ! docker compose -f infra/compose.wallets.yml up -d \
    || ! ./scripts/refresh-wallet-cookies.sh \
    || ! ./scripts/wait-wallets-ready.sh 240; then
    if [ "$ALLOW_DEGRADED" = 1 ]; then
      wallet_start_ok=0
      say 'Wallet baslatilamadi; OFFLINE REPLAY kullanilacak.'
    else
      die 'Wallet servisleri baslatilamadi.'
    fi
  fi
fi

if [ "$wallet_start_ok" != 1 ] || ! ./scripts/preflight.sh; then
  if [ "$ALLOW_DEGRADED" = 1 ]; then
    export HUSHBOARD_MODE=mock
    export HUSHBOARD_ENABLE_LIVE_SENDS=0
    export HUSHBOARD_DB="${HUSHBOARD_OFFLINE_DB:-./data/offline-replay.db}"
    say 'Preflight eksik; ayri snapshot DB ile acikca OFFLINE REPLAY moduna gecildi.'
  else
    die 'Preflight basarisiz. Offline sunum icin: HUSHBOARD_ALLOW_DEGRADED=1 ./START_DEMO.sh'
  fi
fi

if [ "${HUSHBOARD_MODE:-}" != mock ]; then
  # A later wallet outage must be unhealthy/fail-closed, never a mutable mock fallback.
  export HUSHBOARD_MODE=live
fi

say 'Python bagimliliklari dogrulaniyor.'
uv sync --locked --quiet

if [ "${HUSHBOARD_MODE:-}" = mock ]; then
  export HUSHBOARD_WATCH_INTERVAL=0
  SNAPSHOT="${HUSHBOARD_OFFLINE_SNAPSHOT:-./fixtures/offline-replay.json}"
  OFFLINE_DB="${HUSHBOARD_DB:-./data/offline-replay.db}"
  [ -f "$SNAPSHOT" ] || die "Offline snapshot bulunamadi: $SNAPSHOT"
  if ! OFFLINE_DB="$(python3 - "$OFFLINE_DB" "$LIVE_DB_PATH" <<'PY'
from pathlib import Path
import sys
root = Path.cwd().resolve()
data = (root / "data").resolve()
offline = Path(sys.argv[1]).resolve()
live = Path(sys.argv[2]).resolve()
if not offline.is_relative_to(data):
    raise SystemExit("offline DB must stay under HushBoard/data")
if offline == live:
    raise SystemExit("offline DB cannot equal the live DB")
print(offline)
PY
)"; then
    die 'Offline DB ayri data/ yolu olmali; live DB asla degistirilmez.'
  fi
  export HUSHBOARD_DB="$OFFLINE_DB"
  say 'Immutable snapshot ayri offline DB kopyasina yukleniyor; wallet RPC kullanilmayacak.'
  uv run python scripts/load-offline-snapshot.py \
    --snapshot "$SNAPSHOT" --db "$OFFLINE_DB" --protect-db "$LIVE_DB_PATH" >/dev/null
fi

if [ "$HOST" = "::1" ]; then URL="http://[::1]:$PORT"; else URL="http://$HOST:$PORT"; fi
if [ "${HUSHBOARD_NO_BROWSER:-0}" != 1 ]; then
  (
    for _ in $(seq 1 60); do
      if curl -fsS "$URL/api/health" >/dev/null 2>&1; then
        command -v xdg-open >/dev/null && xdg-open "$URL" >/dev/null 2>&1 || true
        exit 0
      fi
      sleep 1
    done
  ) &
fi
say "Demo aciliyor: $URL  (kapatmak icin Ctrl+C)"
exec uv run uvicorn app.main:app --host "$HOST" --port "$PORT"
