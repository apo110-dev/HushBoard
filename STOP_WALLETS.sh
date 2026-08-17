#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
printf '[hushboard] Wallet servisleri durduruluyor; Zebra senkron kalacak.
'
docker compose -f infra/compose.wallets.yml stop operator-wallet participant-wallet
