"""Test fixtures: the API client loaded on its own, and a fake robot.

``custom_components/toque/api.py`` imports nothing from Home Assistant. It is
loaded here straight from its file, without importing the package (whose
``__init__`` does need Home Assistant), so these tests run without it.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import aiohttp
from aiohttp.test_utils import TestServer
import pytest
import pytest_asyncio

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

_spec = importlib.util.spec_from_file_location(
    "toque_api", ROOT / "custom_components" / "toque" / "api.py"
)
assert _spec is not None and _spec.loader is not None
toque_api = importlib.util.module_from_spec(_spec)
sys.modules["toque_api"] = toque_api
_spec.loader.exec_module(toque_api)

from fake_robot import TOKEN, FakeRobot  # noqa: E402


@pytest.fixture
def api():
    """The module under test."""
    return toque_api


@pytest_asyncio.fixture
async def robot():
    """A fake robot listening on a free local port."""
    fake = FakeRobot()
    server = TestServer(fake.app, host="127.0.0.1")
    await server.start_server()
    fake.port = server.port
    try:
        yield fake
    finally:
        fake.close_streams()
        await server.close()


@pytest_asyncio.fixture
async def session():
    async with aiohttp.ClientSession() as client_session:
        yield client_session


@pytest.fixture
def client(robot, session):
    """A client that is not paired yet."""
    return toque_api.ToqueClient(session, "127.0.0.1", robot.port)


@pytest.fixture
def paired(robot, session):
    """A client that holds a valid token."""
    robot.tokens.add(TOKEN)
    return toque_api.ToqueClient(session, "127.0.0.1", robot.port, TOKEN)
