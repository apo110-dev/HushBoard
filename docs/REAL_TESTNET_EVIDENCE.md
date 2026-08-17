# Sanitized real testnet evidence

The machine-readable read-only replay is
[`fixtures/offline-replay.json`](../fixtures/offline-replay.json). It was captured on
`2026-08-17T10:58:27Z` at local Zebra height `4278936`; it is a historical snapshot, not the
current stage state. Its txids, heights and demo memos are intentionally public testnet evidence.
All message text is staged demo content. Original invoice/refund UAs, wallet operation
identifiers, seeds, cookies, account UUIDs and wallet backups are excluded. The valid address
retained for the first offline ZIP-321 URI is an official public ZIP-316 test vector, not a
receiver from these transactions.

> Shielded amounts and memos below come from the wallets' decrypted local receipts. A passive
> public explorer can verify tx inclusion, but is not expected to reveal those fields.

## 1. Wallet funding — infrastructure only

- Txid: `9bd12efa4edd04187ffc8d8f37bc82886a558ff35590eb6e8da3f2bd72a8e524`
- Mined height: `4278614`
- Purpose: fund the isolated operator and participant test wallets.
- Classification: **real**, but **not a HushBoard bond**.

## 2. Current one-shot stage bond — confirmed and unused

- Submission: `jyng6sxyztsx`
- Txid: `9219433aa02abcc90191492478575940b56495307ce2390a3b9f1019a978d5c0`
- Mined height: `4280072`
- Confirmations when the stage fixture was captured: `1`
- Operator receipt: `ironwood` output `0`, exactly `1,000,000 zat`, memo
  `HB1:jyng6sxyztsx`, receiver/amount/memo match.
- Raw transaction receipt: `0` transparent inputs, `0` transparent outputs and `2` Ironwood
  actions.
- Persisted state at capture: `moderation`, no moderation decision and no refund txid.

This is the case reserved for the stage choreography. It is one-shot: verify with
`python3 scripts/verify-stage-fixture.py`, then click refund at most once and only during the
real presentation.

`static/hushboard-proof.png` is a cropped operator-view capture of this record. It contains
staged message text, a masked public submission code and block/status data; the refund address
hint and full txid are not rendered in the image.

## 3. Former stage case — completed after its one-shot decision

| Evidence | Value |
|---|---|
| Submission | `g9825ru2tr4f` |
| Incoming bond txid | `0cd263f97de54c0c028930d8a2b1f50b84bb58733de7b277df7847477d8a297f` |
| Bond mined height | `4278887` |
| Refund txid | `546d9df7e2a530bdad4595d21dfb7547c8ce4bf46422bb9032f8f250886c5bfd` |
| Refund mined height | `4280037` |
| Final persisted state | `refunded` |

This record was the earlier one-shot fixture. It was consumed by a successful refund decision,
so it is retained only as completed historical evidence. It must not be described as the current
unused stage bond. The fixture in `.runtime/stage-fixture.json` now points to section 2.

## 4. Separate end-to-end FullPrivacy refund — completed

| Evidence | Value |
|---|---|
| Submission | `yfqyv6vsm48e` |
| Exact incoming bond txid | `74c7c557ed6bbbd654111bfc44f1ef0dee8b6fe158623591c8e06367bd30660f` |
| Bond mined height | `4278895` |
| Refund txid | `2dba94e0ece283106c4f2dfc88c34576357fd860d01716d5fc51bb6bdf61e5ad` |
| Refund mined height | `4278906` |
| Refund confirmations at replay capture | `31` |
| Final persisted state | `refunded` |

The operator operation first remained `moderation + decision=refund` while building. It moved
to `refund_broadcast` only after Zallet returned operation success and the concrete refund txid,
then to `refunded` after a mined receipt. The participant wallet decrypted an incoming
`1,000,000 zat` output with memo `HB1:yfqyv6vsm48e`.

Both the bond and refund were launched through the adapter's `FullPrivacy` policy. Their raw
receipts have `0` transparent inputs and `0` transparent outputs; the bond has `2` Ironwood
actions and the refund has `3`. The app was restarted while the refund was pending and
reconciled the persisted txid without launching a duplicate.

Explorer routes below prove public inclusion only:

- <https://blockexplorer.one/zcash/testnet/tx/9219433aa02abcc90191492478575940b56495307ce2390a3b9f1019a978d5c0>
- <https://blockexplorer.one/zcash/testnet/tx/0cd263f97de54c0c028930d8a2b1f50b84bb58733de7b277df7847477d8a297f>
- <https://blockexplorer.one/zcash/testnet/tx/546d9df7e2a530bdad4595d21dfb7547c8ce4bf46422bb9032f8f250886c5bfd>
- <https://blockexplorer.one/zcash/testnet/tx/74c7c557ed6bbbd654111bfc44f1ef0dee8b6fe158623591c8e06367bd30660f>
- <https://blockexplorer.one/zcash/testnet/tx/2dba94e0ece283106c4f2dfc88c34576357fd860d01716d5fc51bb6bdf61e5ad>

## Claim boundary

These receipts prove this laptop executed the described Zcash testnet workflow. They do not
make moderation trustless, turn custody into escrow, guarantee anonymity, or give TAZ monetary
value. HushBoard still knows the plaintext feedback, timing, invoice match, refund UA, and its
own wallet relationship.
