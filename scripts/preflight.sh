#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

ok=0; fail=0
pass() { printf '[32m  ✓[0m %s
' "$*"; ok=$((ok+1)); }
warn() { printf '[33m  ![0m %s
' "$*"; }
bad() { printf '[31m  ✗[0m %s
' "$*"; fail=$((fail+1)); }

printf '
[1mHushBoard demo preflight[0m
'
for cmd in docker curl git python3 uv; do
  command -v "$cmd" >/dev/null 2>&1 && pass "$cmd bulundu" || bad "$cmd bulunamadi"
done
if ! docker info >/dev/null 2>&1; then bad 'Docker servisi calismiyor'; fi
if compose_version="$(docker compose version --short 2>/dev/null)" \
  && python3 - "$compose_version" <<'PY'
import re, sys
parts = tuple(int(value) for value in re.findall(r"\d+", sys.argv[1])[:3])
raise SystemExit(0 if parts >= (2, 24, 6) else 1)
PY
then
  pass "Docker Compose $compose_version (!override destekli)"
else
  bad 'Docker Compose 2.24.6+ gerekli'
fi

if curl -fsS --max-time 4 http://127.0.0.1:18080/ready >/dev/null 2>&1; then
  pass 'Zebra testnet node hazir'
else
  bad 'Zebra hazir degil (http://127.0.0.1:18080/ready)'
fi

if zebra_ports="$(docker inspect z3-testnet-zebra-1 2>/dev/null | python3 -c 'import ipaddress,json,sys; data=json.load(sys.stdin); ports=((data[0].get("NetworkSettings") or {}).get("Ports") or {}) if data else {}; checked=[]
for internal in ("18232/tcp","8080/tcp"):
 bindings=ports.get(internal) or []; checked.append(bool(bindings) and all(ipaddress.ip_address(x.get("HostIp","")).is_loopback for x in bindings))
print("Zebra RPC + health loopback-only"); raise SystemExit(0 if all(checked) else 1)' 2>/dev/null)"; then
  pass "$zebra_ports"
else
  bad 'Zebra RPC/health host portu loopback-only degil'
fi

if [ -s .runtime/zebra.cookie ] && chain_json="$(curl -fsS --max-time 8 --user "$(cat .runtime/zebra.cookie)"   -H 'content-type: application/json'   --data '{"jsonrpc":"2.0","id":1,"method":"getblockchaininfo","params":[]}'   http://127.0.0.1:18232 2>/dev/null)"; then
  if chain_summary="$(CHAIN_JSON="$chain_json" python3 -c 'import json,os; r=json.loads(os.environ["CHAIN_JSON"]).get("result") or {}; chain=r.get("chain"); blocks=r.get("blocks"); headers=r.get("headers"); estimated=r.get("estimatedheight"); print(f"Zebra chain={chain} height={blocks} headers={headers} estimated={estimated}"); ready=chain=="test" and isinstance(blocks,int) and blocks==headers and isinstance(estimated,int) and estimated-blocks<=10; raise SystemExit(0 if ready else 1)')"; then
    pass "$chain_summary"
  else
    bad "${chain_summary:-Zebra network dogrulanamadi}"
  fi
else
  bad 'Zebra RPC/cookie yanit vermiyor'
fi

if network_json="$(curl -fsS --max-time 8 --user "$(cat .runtime/zebra.cookie 2>/dev/null)"   -H 'content-type: application/json'   --data '{"jsonrpc":"2.0","id":1,"method":"getnetworkinfo","params":[]}'   http://127.0.0.1:18232 2>/dev/null)"; then
  if peer_summary="$(NETWORK_JSON="$network_json" python3 -c 'import json,os; r=json.loads(os.environ["NETWORK_JSON"]).get("result") or {}; n=r.get("connections",0); print(f"Zebra peers={n}"); raise SystemExit(0 if isinstance(n,int) and n>0 else 1)')"; then
    pass "$peer_summary"
  else
    bad "${peer_summary:-Zebra peer yok}"
  fi
else
  bad 'Zebra peer durumu okunamadi'
fi

if [ -f .runtime/wallets.json ]; then pass 'wallet metadata mevcut'; else bad '.runtime/wallets.json eksik'; fi

for who in operator participant; do
  if response="$(./scripts/wallet-rpc.sh "$who" getwalletstatus '[]' 2>/dev/null)"; then
    if summary="$(STATUS_JSON="$response" WHO="$who" python3 -c 'import json,os; r=json.loads(os.environ["STATUS_JSON"]).get("result") or {}; n=(r.get("node_tip") or {}).get("height"); w=(r.get("wallet_tip") or {}).get("height"); print(f"{os.environ[chr(87)+chr(72)+chr(79)]}: wallet={w} node={n} synced={bool(n is not None and w == n)}"); raise SystemExit(0 if n is not None and w == n else 3)')"; then
      pass "$summary"
    else
      bad "${summary:-$who wallet sync olmadi}"
    fi
  else
    bad "$who wallet RPC yanit vermiyor"
  fi
done

if [ -f .runtime/wallets.json ]; then
  python3 - <<'PY' || fail=$((fail+1))
import json, pathlib, subprocess
root=pathlib.Path.cwd()
meta=json.loads((root/'.runtime/wallets.json').read_text())['wallets']
underfunded=False
for who in ('operator','participant'):
    account=meta[who]['account_uuid']
    p=subprocess.run([str(root/'scripts/wallet-rpc.sh'),who,'z_getbalanceforaccount',json.dumps([account,1])],text=True,capture_output=True)
    if p.returncode:
        print(f'[31m  ✗[0m {who} balance okunamadi')
        raise SystemExit(1)
    pools=(json.loads(p.stdout).get('result') or {}).get('pools') or {}
    zats=sum(int((v or {}).get('valueZat',0)) for v in pools.values())
    ready=zats >= 1_020_000
    color='32' if ready else '31'
    mark='✓' if ready else '✗'
    whole,fractional=divmod(zats,100_000_000)
    print(f'[{color}m  {mark}[0m {who} spendable: {whole}.{fractional:08d} TAZ')
    underfunded |= not ready
raise SystemExit(1 if underfunded else 0)
PY
fi

if [ -f .runtime/stage-fixture.json ]; then
  if stage_summary="$(python3 scripts/verify-stage-fixture.py 2>&1)"; then
    pass "$stage_summary"
  else
    bad "${stage_summary:-stage fixture dogrulanamadi}"
  fi
else
  warn 'confirmed stage fixture henuz yok; hazirlanana kadar golden-path sunumu yapma'
fi

printf '
Sonuc: %d kontrol basarili, %d kritik hata.

' "$ok" "$fail"
[ "$fail" -eq 0 ]
