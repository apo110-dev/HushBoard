#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNTIME="$ROOT/.runtime"
ALPINE_IMAGE="${HUSHBOARD_ALPINE_IMAGE:-alpine:3@sha256:28bd5fe8b56d1bd048e5babf5b10710ebe0bae67db86916198a6eec434943f8b}"
mkdir -p "$RUNTIME"
chmod 700 "$RUNTIME"

copy_cookie() {
  local volume="$1" output="$2"
  for _ in $(seq 1 60); do
    if docker run --rm -v "$volume:/data:ro" "$ALPINE_IMAGE" cat /data/.cookie > "$output.tmp" 2>/dev/null       && [ -s "$output.tmp" ]; then
      mv "$output.tmp" "$output"
      chmod 600 "$output"
      return 0
    fi
    rm -f "$output.tmp"
    sleep 1
  done
  printf '[hushboard] %s RPC cookie okunamadi.
' "$volume" >&2
  return 1
}

copy_cookie hushboard-operator-wallet "$RUNTIME/operator.cookie"
copy_cookie hushboard-participant-wallet "$RUNTIME/participant.cookie"
