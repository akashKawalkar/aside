import asyncio
import os
import selectors
from urllib.parse import urlparse

import pytest
from dotenv import load_dotenv

load_dotenv()

# DB tests must never touch the live database (it will be Supabase): they run against DATABASE_URL_TEST,
# and without it only a local database is accepted.
_test_url = os.environ.get("DATABASE_URL_TEST", "")
if _test_url:
    os.environ["DATABASE_URL"] = _test_url
_host = urlparse(os.environ.get("DATABASE_URL", "")).hostname or ""
DB_SAFE = bool(_test_url) or _host in ("localhost", "127.0.0.1", "::1")


def pytest_collection_modifyitems(config, items):
    if DB_SAFE:
        return
    skip = pytest.mark.skip(reason="DATABASE_URL is not local and DATABASE_URL_TEST is not set")
    for item in items:
        if item.module.__name__.endswith("_db"):
            item.add_marker(skip)


def pytest_asyncio_loop_factories(config, item):
    """psycopg's async mode cannot run on Windows' default Proactor loop (run_server.py uses the selector loop),
    while the supervisor tests need the Proactor loop to spawn subprocesses. So only the database tests get the
    selector loop."""
    if item.module.__name__.endswith(("test_data_quality_db", "_db")):
        return {"selector": lambda: asyncio.SelectorEventLoop(selectors.SelectSelector())}
    return {"default": asyncio.new_event_loop}
