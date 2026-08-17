#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WHO="${1:?usage: wallet-rpc.sh operator|participant method '[params]'}"
METHOD="${2:?method required}"
PARAMS="${3:-[]}"
case "$WHO" in
  operator) PORT=41232 ;;
  participant) PORT=41233 ;;
  *) echo "unknown wallet: $WHO" >&2; exit 2 ;;
esac
COOKIE="$ROOT/.runtime/$WHO.cookie"
curl -fsS --user "$(cat "$COOKIE")" -H 'content-type: application/json' \
  --data "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"$METHOD\",\"params\":$PARAMS}" \
  "http://127.0.0.1:$PORT" | python3 -m json.tool
