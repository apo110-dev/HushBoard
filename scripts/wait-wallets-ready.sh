#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
TIMEOUT="${1:-240}"
case "$TIMEOUT" in ''|*[!0-9]*) printf '[hushboard] wallet wait timeout sayisal olmali.
' >&2; exit 2;; esac

for ((elapsed=0; elapsed<=TIMEOUT; elapsed++)); do
  all_ready=1
  for role in operator participant; do
    response="$(./scripts/wallet-rpc.sh "$role" getwalletstatus '[]' 2>/dev/null || true)"
    if ! STATUS_JSON="$response" python3 - <<'PY'
import json, os, sys
try:
    result = (json.loads(os.environ["STATUS_JSON"]).get("result") or {})
except (json.JSONDecodeError, TypeError):
    raise SystemExit(1)
node = (result.get("node_tip") or {}).get("height")
wallet = (result.get("wallet_tip") or {}).get("height")
fully = result.get("fully_synced_height")
ok = node is not None and wallet == node and (fully is None or fully >= node)
raise SystemExit(0 if ok else 1)
PY
    then
      all_ready=0
      break
    fi
  done
  if [ "$all_ready" -eq 1 ]; then
    printf '[hushboard] operator + participant wallet tip ile senkron.
'
    exit 0
  fi
  if (( elapsed > 0 && elapsed % 10 == 0 )); then
    printf '[hushboard] wallet sync bekleniyor... (%ss/%ss)
' "$elapsed" "$TIMEOUT"
  fi
  sleep 1
done
printf '[hushboard] walletlar %ss icinde tip ile senkron olmadi.
' "$TIMEOUT" >&2
exit 1
