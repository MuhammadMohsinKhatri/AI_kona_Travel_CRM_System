"""Test environment defaults — applied before any test module is collected.

pytest imports conftest.py before collecting test_*.py files, so this wins the
race against any test module that (directly, or transitively via an import
like ``app.konaos.client``, which calls ``load_dotenv()`` at module level)
would otherwise populate os.environ from backend/.env — including its
production-shaped DATABASE_URL (postgresql://.../db, a Docker-internal
hostname unreachable outside compose). Without this file, whichever test
module happens to be collected first decides what every other test's
``os.environ.setdefault(...)`` calls actually do, making the suite's outcome
depend on alphabetical filename order rather than being deterministic.
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_konaice.db")
os.environ.setdefault("PIPELINE_RUN_INLINE", "true")
os.environ.setdefault("MOCK_LATENCY_S", "0")
# Tests must never touch production integrations regardless of what .env says.
os.environ["CRM_PROVIDER"] = "mock"
os.environ["SQUARE_PROVIDER"] = "mock"
os.environ["OPENAI_PROVIDER"] = "mock"
os.environ["TELEGRAM_PROVIDER"] = "mock"
os.environ.setdefault("PIPELINE_DRY_RUN", "false")
