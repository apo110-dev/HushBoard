#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE=(docker compose -f "$ROOT/infra/compose.wallets.yml")
RUNTIME="$ROOT/.runtime"
ALPINE_IMAGE="${HUSHBOARD_ALPINE_IMAGE:-alpine:3@sha256:28bd5fe8b56d1bd048e5babf5b10710ebe0bae67db86916198a6eec434943f8b}"
mkdir -p "$RUNTIME"
chmod 700 "$RUNTIME"

say() { printf '\n\033[1;33m[hushboard]\033[0m %s\n' "$*"; }
die() { printf '\n[hushboard] ERROR: %s\n' "$*" >&2; exit 1; }

command -v docker >/dev/null || die "Docker bulunamadi."
docker info >/dev/null 2>&1 || die "Docker servisi calismiyor."

if ! docker network inspect z3-testnet >/dev/null 2>&1; then
  say "Pinned Z3 testnet node kuruluyor. Ilk senkron 2-12 saat surebilir."
fi
"$ROOT/scripts/ensure-zebra-loopback.sh"

if ! curl -fsS http://127.0.0.1:18080/ready >/dev/null 2>&1; then
  die "Zcash testnet node henuz hazir degil. Durum: curl http://127.0.0.1:18080/ready"
fi

say "Zebra testnet hazir; RPC kimligi uygulamaya guvenli bicimde kopyalaniyor."
docker run --rm -v z3-testnet-cookie:/source:ro "$ALPINE_IMAGE" \
  cat /source/.cookie > "$RUNTIME/zebra.cookie"
chmod 600 "$RUNTIME/zebra.cookie"

prepare_volume() {
  local volume="$1"
  docker volume create "$volume" >/dev/null
  docker run --rm -v "$volume":/data "$ALPINE_IMAGE" sh -c 'chown -R 1000:1000 /data && chmod 700 /data'
}
prepare_volume hushboard-operator-wallet
prepare_volume hushboard-participant-wallet

volume_has() {
  local volume="$1" path="$2"
  docker run --rm -v "$volume":/data:ro "$ALPINE_IMAGE" test -s "/data/$path" >/dev/null 2>&1
}

initialize_wallet() {
  local service="$1" volume="$2" marker="$3"
  if ! volume_has "$volume" identity.txt; then
    say "$service: encryption identity olusturuluyor."
    "${COMPOSE[@]}" run --rm --no-deps "$service" \
      --datadir /var/lib/zallet --config /etc/zallet/zallet.toml \
      generate-encryption-identity
  fi
  if ! volume_has "$volume" .hushboard-initialized; then
    say "$service: TAZ-only demo wallet hazirlaniyor."
    "${COMPOSE[@]}" run --rm --no-deps "$service" \
      --datadir /var/lib/zallet --config /etc/zallet/zallet.toml \
      init-wallet-encryption
    "${COMPOSE[@]}" run --rm --no-deps "$service" \
      --datadir /var/lib/zallet --config /etc/zallet/zallet.toml \
      generate-mnemonic | tee "$RUNTIME/$marker.seedfp"
    docker run --rm -v "$volume":/data "$ALPINE_IMAGE" sh -c \
      'touch /data/.hushboard-initialized && chown 1000:1000 /data/.hushboard-initialized'
  fi
}
initialize_wallet operator-wallet hushboard-operator-wallet operator
initialize_wallet participant-wallet hushboard-participant-wallet participant

say "Iki ayri Zallet testnet wallet baslatiliyor."
"${COMPOSE[@]}" up -d

wait_for_file() {
  local volume="$1" path="$2"
  for _ in $(seq 1 90); do
    if volume_has "$volume" "$path"; then return 0; fi
    sleep 1
  done
  return 1
}
wait_for_file hushboard-operator-wallet .cookie || die "Operator wallet RPC cookie olusmadi."
wait_for_file hushboard-participant-wallet .cookie || die "Participant wallet RPC cookie olusmadi."

docker run --rm -v hushboard-operator-wallet:/data:ro "$ALPINE_IMAGE" cat /data/.cookie > "$RUNTIME/operator.cookie"
docker run --rm -v hushboard-participant-wallet:/data:ro "$ALPINE_IMAGE" cat /data/.cookie > "$RUNTIME/participant.cookie"
chmod 600 "$RUNTIME/operator.cookie" "$RUNTIME/participant.cookie"

rpc() {
  local url="$1" cookie="$2" method="$3" params="${4:-[]}" id="${5:-1}"
  curl -fsS --user "$(cat "$cookie")" -H 'content-type: application/json' \
    --data "{\"jsonrpc\":\"2.0\",\"id\":$id,\"method\":\"$method\",\"params\":$params}" "$url"
}

wait_rpc() {
  local label="$1" url="$2" cookie="$3"
  say "$label wallet sync bekleniyor (ilk acilista birkac dakika surebilir)."
  for i in $(seq 1 240); do
    local response
    response="$(rpc "$url" "$cookie" getwalletstatus 2>/dev/null || true)"
    if [ -n "$response" ] && python3 -c 'import json,sys; r=json.load(sys.stdin).get("result",{}); nt=r.get("node_tip",{}).get("height"); wt=r.get("wallet_tip",{}).get("height"); ready=(nt is not None and nt == wt and r.get("locked") is not True); sys.exit(0 if ready else 1)' <<<"$response"; then
      printf '[hushboard] %s wallet hazir.\n' "$label"
      return 0
    fi
    if (( i % 10 == 0 )); then printf '[hushboard] %s sync... (%ss)\n' "$label" "$i"; fi
    sleep 1
  done
  die "$label wallet 4 dakika icinde sync olmadi."
}
wait_rpc operator http://127.0.0.1:41232 "$RUNTIME/operator.cookie"
wait_rpc participant http://127.0.0.1:41233 "$RUNTIME/participant.cookie"

python3 "$ROOT/scripts/ensure-wallet-accounts.py"

say "Testnet wallet altyapisi hazir. Bilgiler: $RUNTIME/wallets.json"
