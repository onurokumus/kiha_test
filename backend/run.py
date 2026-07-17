"""Single-process backend launcher for Python 3.11+.

Uses SelectorEventLoop on Windows as a defensive workaround for observed
native crashes. Linux uses asyncio's default event loop.
"""
import asyncio
import logging
import os
import sys

import uvicorn


def main() -> None:
    # kiha.* loggers (upload/ingest progress) have no handler of their own;
    # uvicorn's dictConfig only wires its uvicorn.* loggers, so without a
    # root handler every INFO line from the app is silently dropped.
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    host = os.environ.get("KIHA_HOST", "127.0.0.1")
    port = int(os.environ.get("KIHA_PORT", "8000"))
    config = uvicorn.Config("app.main:app", host=host, port=port, workers=1)
    server = uvicorn.Server(config)
    loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    # asyncio.run(loop_factory=...) only exists on Python 3.12+.  Runner has
    # supported loop_factory since Python 3.11, our deployment baseline.
    with asyncio.Runner(loop_factory=loop_factory) as runner:
        runner.run(server.serve())


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
