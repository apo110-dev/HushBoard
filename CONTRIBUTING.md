# Contributing to HushBoard

Thanks for helping improve HushBoard. This repository is a **loopback-only, Zcash testnet
prototype**; changes must preserve that boundary.

## Local setup

```bash
uv sync --locked --all-groups
uv run pytest
uv run ruff check app tests scripts VIDEO_PAKETI/source
# The release audit intentionally requires a clean worktree; run it after committing.
python3 scripts/audit-public-release.py
```

A wallet-free UI smoke test is available with:

```bash
HUSHBOARD_MODE=mock ./START_DEMO.sh
```

Tests must mock wallet RPC and must never broadcast a transaction or spend TAZ.

## Pull requests

- Keep changes focused and add regression tests for behavior changes.
- Preserve integer zatoshi arithmetic, idempotency, fail-closed state transitions, loopback
  binding, and the explicit distinction between live, replay, and synthetic evidence.
- Update `uv.lock` whenever `pyproject.toml` changes.
- Run the commands above and `git diff --check` before opening a PR.
- Do not commit generated video output; `VIDEO_PAKETI/generated/` stays local.

## Never include sensitive material

Do **not** put wallet cookies, seeds/mnemonics, wallet or application databases, full private
refund/invoice addresses, runtime receipts, backups, access tokens, or raw wallet responses in
commits, issues, logs, screenshots, or PR descriptions. Use sanitized placeholders and create
release archives only with `git archive HEAD`.

For a suspected vulnerability, do not open a public issue. Use GitHub's private
**Security → Report a vulnerability** flow and follow [`SECURITY.md`](SECURITY.md).
