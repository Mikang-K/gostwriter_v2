import asyncio
import sys

from .config import Settings
from .store import Store


async def main():
    settings = Settings()
    if not settings.database_url:
        raise SystemExit("DATABASE_URL을 backend/.env.local에 설정하세요.")
    store = Store(settings.database_url)
    await store.open()
    try:
        await store.migrate()
        print("Ghostwriter v2 schema ready")
    finally:
        await store.close()


if __name__ == "__main__":
    factory = asyncio.SelectorEventLoop if sys.platform == "win32" else asyncio.new_event_loop
    with asyncio.Runner(loop_factory=factory) as runner:
        runner.run(main())
