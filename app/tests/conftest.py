from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.config import Settings

# Importing app.main creates the conventional ASGI `app`; keep that import-time DB
# outside the repository during tests.
os.environ.setdefault("HUSHBOARD_MODE", "mock")
os.environ.setdefault("HUSHBOARD_DB", f"/tmp/hushboard-import-{os.getpid()}.db")


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings.from_env(
        {
            "HUSHBOARD_MODE": "mock",
            "HUSHBOARD_DB": str(tmp_path / "hushboard.db"),
            "ADMIN_KEY": "unit-test-admin-key",
            "HUSHBOARD_DEMO_OPEN_ADMIN": "1",
            "HUSHBOARD_WATCH_INTERVAL": "0",
        },
        root=tmp_path,
    )


@pytest.fixture
def refund_address() -> str:
    return "utest1" + "q" * 104
