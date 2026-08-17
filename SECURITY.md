# Security and privacy boundary

HushBoard is a **centralized, custodial, testnet-only hackathon prototype**. It is not a
production custody service, trustless escrow, mixer, or anonymity service. Never use real ZEC
with this build.

## Trust model

- The operator wallet has custody of every bond and can refuse/delay a refund.
- Moderation is central and censorable. SQLite records policy execution; it does not
  enforce policy on-chain.
- A shielded transaction protects ledger-visible address/amount/memo data from passive
  public-chain observers. It does not hide data already known by HushBoard.

HushBoard sees feedback plaintext, invoice association, arrival time, refund UA and the
relationship between its own incoming/outgoing wallet records. IP, transport observers,
browser fingerprints, timing correlation, writing style, endpoint compromise and screenshots
remain outside the Zcash payment-layer guarantee.

## Local secret handling

- Zallet RPCs, Zebra RPC, Zebra health, and the web app are host-loopback-only. Zebra P2P
  `18233` remains public intentionally so the node can participate in testnet.
- Both `START_DEMO.sh` and `python -m app` reject a non-loopback HTTP bind.
- Cookies, wallet DBs, seed material, runtime receipts and application DB are Git-ignored.
  Startup uses umask `077`; SQLite DB/WAL/SHM files are repaired to owner-only `0600`.
- API responses mask refund UAs and never return cookies/seeds. Raw wallet bodies are not
  proxied to the browser.
- Any disposable testnet bootstrap material must be treated as compromised and must never be
  reused for mainnet, production, or privacy-sensitive activity.
- The demo wallets and published testnet txids are intentionally linkable to HushBoard's
  staged evidence. Never reuse those wallets for a real user or privacy-sensitive activity.

## Transaction safety

- Amount is fixed at integer `1,000,000` zatoshi and serialized as `0.01000000`; no binary
  floating point is used.
- Invoices use a fresh Orchard-only Unified Address and `HB1:<12-char-id>` memo.
- Refund UAs are collected separately and verified through Zallet; they are not inferred from
  the payer or ZIP-321.
- Normal sends use `FullPrivacy`. There is no automatic privacy-policy downgrade.
- `refund_broadcast` requires a successful persisted operation result with a real txid.
  Unknown/crashed operations fail closed and require reconciliation; retries are not automatic.
- Incoming outputs are idempotent on `(txid, pool, output_index)`.

## Reporting

Do not include wallet cookies, mnemonics, full refund UAs or runtime archives in reports.
For this local prototype, document a finding privately to the repository owner with a minimal
reproduction and sanitized logs.
