import asyncio
import os
import sys

import uvicorn


def main():
    config = uvicorn.Config("app.main:app", host=os.getenv("GW_HOST", "127.0.0.1"), port=8000, loop="none")
    # Psycopg requires a selector loop on Windows.
    factory = asyncio.SelectorEventLoop if sys.platform == "win32" else asyncio.new_event_loop
    with asyncio.Runner(loop_factory=factory) as runner:
        runner.run(uvicorn.Server(config).serve())


if __name__ == "__main__":
    main()
