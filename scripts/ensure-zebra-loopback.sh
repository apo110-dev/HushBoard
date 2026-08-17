#!/usr/bin/env bash
set -euo pipefail
umask 077
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
Z3_DIR="${HUSHBOARD_Z3_DIR:-$ROOT/.runtime/z3}"
Z3_COMMIT="${HUSHBOARD_Z3_COMMIT:-e84ce9fd8e864ff0b2a8a62f6ce14392145db0fb}"
export Z3_ZEBRA_IMAGE="${HUSHBOARD_ZEBRA_IMAGE:-zfnd/zebra:6.2.3@sha256:bb2a6029db277ee3a10e951dcc0ddd36b4cbcbe0fad684746d695ee21d53fde2}"

command -v docker >/dev/null || { echo '[hushboard] Docker bulunamadi.' >&2; exit 1; }
command -v git >/dev/null || { echo '[hushboard] git bulunamadi.' >&2; exit 1; }
command -v curl >/dev/null || { echo '[hushboard] curl bulunamadi.' >&2; exit 1; }
command -v python3 >/dev/null || { echo '[hushboard] python3 bulunamadi.' >&2; exit 1; }
mkdir -p "$ROOT/.runtime"
chmod 700 "$ROOT/.runtime"
fresh_clone=0
if [ ! -d "$Z3_DIR/.git" ]; then
  git clone --filter=blob:none --no-checkout https://github.com/ZcashFoundation/z3 "$Z3_DIR"
  fresh_clone=1
fi
if [ "$fresh_clone" -ne 1 ] \
  && { ! git -C "$Z3_DIR" diff --quiet || ! git -C "$Z3_DIR" diff --cached --quiet; }; then
  echo '[hushboard] z3 runtime checkout degistirilmis; pinned kodu calistirmak reddedildi.' >&2
  exit 1
fi
if [ "$fresh_clone" -eq 1 ] \
  || [ "$(git -C "$Z3_DIR" rev-parse HEAD 2>/dev/null || true)" != "$Z3_COMMIT" ]; then
  git -C "$Z3_DIR" fetch --depth 1 origin "$Z3_COMMIT"
  git -C "$Z3_DIR" checkout --detach "$Z3_COMMIT"
fi
(
  cd "$Z3_DIR"
  ./scripts/setup-network.sh testnet >/dev/null
  docker compose \
    --env-file .env.testnet \
    -f docker-compose.yml \
    -f "$ROOT/infra/compose.zebra-loopback.yml" \
    up -d zebra
)
python3 - <<'PY'
import ipaddress, json, subprocess
inspection = subprocess.run(
    ["docker", "inspect", "z3-testnet-zebra-1"],
    text=True, capture_output=True, check=True,
)
container = json.loads(inspection.stdout)[0]
ports = (container.get("NetworkSettings") or {}).get("Ports") or {}
for internal in ("18232/tcp", "8080/tcp"):
    bindings = ports.get(internal) or []
    if not bindings:
        raise SystemExit(f"[hushboard] Zebra {internal} host bind bulunamadi")
    for binding in bindings:
        host = binding.get("HostIp", "")
        try:
            loopback = ipaddress.ip_address(host).is_loopback
        except ValueError:
            loopback = False
        if not loopback:
            raise SystemExit(f"[hushboard] Zebra {internal} loopback disina acik: {host}")
print("[hushboard] Zebra RPC + health host bind loopback-only; P2P ayri.")
PY

READY_TIMEOUT="${HUSHBOARD_ZEBRA_READY_TIMEOUT:-240}"
case "$READY_TIMEOUT" in
  ''|*[!0-9]*) echo '[hushboard] Zebra ready timeout sayisal olmali.' >&2; exit 2;;
esac
zebra_ready=0
for ((elapsed=0; elapsed<=READY_TIMEOUT; elapsed++)); do
  if curl -fsS --max-time 3 http://127.0.0.1:18080/ready >/dev/null 2>&1; then
    echo '[hushboard] Zebra testnet ready endpoint hazir.'
    zebra_ready=1
    break
  fi
  if (( elapsed > 0 && elapsed % 15 == 0 )); then
    printf '[hushboard] Zebra ready bekleniyor... (%ss/%ss)\n' "$elapsed" "$READY_TIMEOUT"
  fi
  sleep 1
done
if [ "$zebra_ready" -ne 1 ]; then
  echo '[hushboard] Zebra warm restart sonrasi ready olmadi; cold sync sahne startup kapsaminda degil.' >&2
  exit 1
fi
ALPINE_IMAGE="${HUSHBOARD_ALPINE_IMAGE:-alpine:3@sha256:28bd5fe8b56d1bd048e5babf5b10710ebe0bae67db86916198a6eec434943f8b}"
tmp_cookie="$ROOT/.runtime/zebra.cookie.new"
docker run --rm -v z3-testnet-cookie:/source:ro "$ALPINE_IMAGE" cat /source/.cookie > "$tmp_cookie"
[ -s "$tmp_cookie" ] || { rm -f "$tmp_cookie"; echo '[hushboard] Zebra cookie okunamadi.' >&2; exit 1; }
# Preserve the bind-mounted file inode for already-running wallet containers.
touch "$ROOT/.runtime/zebra.cookie"
cat "$tmp_cookie" > "$ROOT/.runtime/zebra.cookie"
rm -f "$tmp_cookie"
chmod 600 "$ROOT/.runtime/zebra.cookie"
