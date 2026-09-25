import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Point the store and the poller lock at throwaway paths before anything imports
# config, so tests never touch a real database or fight the running dashboard.
_tmp = tempfile.mkdtemp(prefix="sdwan-tests-")
os.environ.setdefault("SDWAN_MODE", "mock")
os.environ["DB_PATH"] = os.path.join(_tmp, "test.db")
os.environ["POLLER_LOCK_PATH"] = os.path.join(_tmp, "poller.lock")
os.environ["ALERTS_ENABLED"] = "false"

import app as app_module  # noqa: E402
import poller  # noqa: E402
import store  # noqa: E402
from sdwan_client import MockSDWANClient  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def seeded_store():
    """Every API test needs one completed poll, since routes serve from the store."""
    store.init()
    poller.poll_once(app_module.get_client)
    yield


@pytest.fixture
def client():
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


@pytest.fixture
def mock_data():
    return MockSDWANClient()
