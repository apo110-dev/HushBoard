"""Optional non-destructive background wallet watcher."""
from __future__ import annotations

import asyncio
from contextlib import suppress

from .service import HushBoardService, _safe_error


class BackgroundWatcher:
    def __init__(self, service: HushBoardService, interval: float) -> None:
        self.service = service
        self.interval = interval
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self.interval <= 0 or self._task is not None:
            return
        self.service.watcher_running = True
        self._task = asyncio.create_task(self._run(), name="hushboard-wallet-watcher")

    async def _run(self) -> None:
        try:
            while not self._stop.is_set():
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=self.interval)
                    break
                except TimeoutError:
                    pass
                try:
                    await asyncio.to_thread(self.service.sync)
                except Exception as exc:  # noqa: BLE001 - watcher must not take down the API
                    self.service.watcher_error = _safe_error(exc)
        finally:
            self.service.watcher_running = False

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        self.service.watcher_running = False
