from __future__ import annotations

import ipaddress
import os

import uvicorn


def _loopback_host(value: str) -> str:
    host = value.strip()
    if host == "localhost":
        return host
    try:
        if ipaddress.ip_address(host).is_loopback:
            return host
    except ValueError:
        pass
    raise SystemExit("HushBoard HTTP host must be loopback (127.0.0.1, ::1, or localhost)")


def _port(value: str) -> int:
    try:
        port = int(value, 10)
    except ValueError as exc:
        raise SystemExit("HushBoard PORT must be an integer") from exc
    if not 1 <= port <= 65535:
        raise SystemExit("HushBoard PORT must be in 1..65535")
    return port


def main() -> None:
    uvicorn.run(
        "app.main:app",
        host=_loopback_host(os.environ.get("HOST", "127.0.0.1")),
        port=_port(os.environ.get("PORT", "4173")),
        reload=False,
    )


if __name__ == "__main__":
    main()
