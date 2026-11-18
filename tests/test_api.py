"""Tests of the API client against the fake robot (no Home Assistant needed)."""

from __future__ import annotations

import asyncio
from contextlib import aclosing
import json
import socket

import pytest

from fake_robot import CATEGORIES, JPEG, RECIPE, ROBOT_ID, STATUS, TOKEN, sse


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


async def _take(stream, count: int, timeout: float = 5.0) -> list:
    """Read ``count`` events from an event iterator."""
    events = []

    async def read() -> None:
        async for event in stream:
            events.append(event)
            if len(events) == count:
                return

    await asyncio.wait_for(read(), timeout)
    return events


async def _start_reading(robot, stream, count: int) -> asyncio.Task:
    """Start reading (which opens the connection) and wait until it is open."""
    task = asyncio.ensure_future(_take(stream, count))
    for _ in range(500):
        if robot.stream_count:
            break
        await asyncio.sleep(0.01)
    assert robot.stream_count == 1
    return task


# ------------------------------------------------------------------- info


async def test_info_needs_no_token(client):
    info = await client.info()
    assert info["id"] == ROBOT_ID
    assert info["name"] == "Toque"
    assert info["api"] == 1
    assert info["model"] == "Monsieur Cuisine connect"
    assert info["pairing_open"] is False


async def test_cannot_connect(api, session):
    client = api.ToqueClient(session, "127.0.0.1", _free_port(), TOKEN)
    with pytest.raises(api.ToqueConnectionError):
        await client.info()
    with pytest.raises(api.ToqueConnectionError):
        await client.status()
    with pytest.raises(api.ToqueConnectionError):
        async for _ in client.events():
            pass


async def test_not_a_toque_robot(api, robot, session):
    """An answer that is not the expected JSON is an error, not a crash."""
    client = api.ToqueClient(session, "127.0.0.1", robot.port)
    # /api/nothing does not exist: aiohttp answers 404 with a text body.
    with pytest.raises(api.ToqueNotFoundError):
        await client._request("GET", "/api/nothing", auth=False)


def test_base_url_ipv6(api):
    client = api.ToqueClient(None, "fe80::1", 8730)
    assert client.base_url == "http://[fe80::1]:8730"


# ---------------------------------------------------------------- pairing


async def test_pair_success(robot, client):
    robot.open_pairing("482913")
    assert (await client.info())["pairing_open"] is True

    result = await client.pair("482913", "Home Assistant")

    assert result == {"token": TOKEN, "id": ROBOT_ID, "name": "Toque"}
    assert len(result["token"]) == 64
    assert robot.paired_names == ["Home Assistant"]
    # The token is kept and used from now on.
    assert client.token == TOKEN
    assert (await client.status())["state"] == "running"


async def test_pair_bad_code(api, robot, client):
    robot.open_pairing("482913")
    with pytest.raises(api.ToquePairingError) as raised:
        await client.pair("000000")
    assert raised.value.code == "bad_code"
    assert raised.value.status == 403
    assert client.token is None


async def test_pair_closed(api, client):
    with pytest.raises(api.ToquePairingError) as raised:
        await client.pair("482913")
    assert raised.value.code == "pairing_closed"
    assert raised.value.status == 403


async def test_pair_too_many_attempts(api, robot, client):
    robot.open_pairing("482913")
    for _ in range(4):
        with pytest.raises(api.ToquePairingError) as raised:
            await client.pair("000000")
        assert raised.value.code == "bad_code"
    with pytest.raises(api.ToquePairingError) as raised:
        await client.pair("000000")
    assert raised.value.code == "too_many_attempts"
    assert raised.value.status == 429
    # The code was cancelled: even the right one is refused now.
    with pytest.raises(api.ToquePairingError) as raised:
        await client.pair("482913")
    assert raised.value.code == "pairing_closed"


async def test_pair_cannot_connect(api, session):
    client = api.ToqueClient(session, "127.0.0.1", _free_port())
    with pytest.raises(api.ToqueConnectionError):
        await client.pair("482913")


# ----------------------------------------------------------------- status


async def test_status(paired):
    status = await paired.status()
    assert status == STATUS
    assert status["job"]["ends_at"] == 1794996145
    assert status["guided"]["step_text"] == "Mixer 30 s à vitesse 8."


async def test_status_null_fields(robot, paired):
    robot.status.update(temperature=None, temperature_below_range=True, job=None, guided=None,
                        bowl_present=None, bowl_sensor_reliable=False)
    status = await paired.status()
    assert status["temperature"] is None
    assert status["temperature_below_range"] is True
    assert status["job"] is None and status["guided"] is None


# -------------------------------------------------------------------- 401


async def test_no_token_is_an_auth_error(api, client):
    with pytest.raises(api.ToqueAuthError):
        await client.status()


async def test_revoked_token_everywhere(api, robot, paired):
    assert (await paired.status())["id"] == ROBOT_ID
    robot.unpair_all()

    with pytest.raises(api.ToqueAuthError) as raised:
        await paired.status()
    assert raised.value.status == 401
    with pytest.raises(api.ToqueAuthError):
        await paired.recipes("beurre")
    with pytest.raises(api.ToqueAuthError):
        await paired.recipe("web-250008")
    with pytest.raises(api.ToqueAuthError):
        await paired.categories()
    with pytest.raises(api.ToqueAuthError):
        await paired.image("web-250008")
    with pytest.raises(api.ToqueAuthError):
        async for _ in paired.events():
            pass
    # /api/info still answers.
    assert (await paired.info())["id"] == ROBOT_ID


async def test_auth_error_is_not_a_connection_error(api):
    assert not issubclass(api.ToqueAuthError, api.ToqueConnectionError)
    assert issubclass(api.ToqueAuthError, api.ToqueError)
    assert issubclass(api.ToqueConnectionError, api.ToqueError)


# ---------------------------------------------------------------- recipes


async def test_recipes_search(robot, paired):
    result = await paired.recipes("beurre")
    assert result["total"] == 1
    assert result["items"][0]["id"] == "web-250008"
    assert robot.requests[-1] == ("GET", "/api/recipes", {"q": "beurre"})


async def test_recipes_all_favourites_first(paired):
    result = await paired.recipes()
    assert [item["id"] for item in result["items"]] == ["web-250011", "web-250008"]
    assert result["limit"] == 50 and result["offset"] == 0


async def test_recipes_parameters(robot, paired):
    result = await paired.recipes(
        "crème", category="Desserts", favorites=True, limit=10, offset=0
    )
    assert [item["id"] for item in result["items"]] == ["web-250011"]
    assert robot.requests[-1] == (
        "GET",
        "/api/recipes",
        {"q": "crème", "category": "Desserts", "favorites": "1", "limit": "10"},
    )
    result = await paired.recipes(limit=1, offset=1)
    assert [item["id"] for item in result["items"]] == ["web-250008"]
    assert robot.requests[-1][2] == {"limit": "1", "offset": "1"}


async def test_recipe_detail(paired):
    recipe = await paired.recipe("web-250008")
    assert recipe == RECIPE
    texts = [item["text"] for group in recipe["ingredients"] for item in group["items"]]
    assert texts == ["300 g cacahuètes grillées, non salées", "1/2 c. à c. sel"]


async def test_recipe_not_found(api, paired):
    with pytest.raises(api.ToqueNotFoundError) as raised:
        await paired.recipe("nope")
    assert raised.value.status == 404
    assert raised.value.code == "not_found"


async def test_recipe_id_is_escaped(api, robot, paired):
    with pytest.raises(api.ToqueNotFoundError):
        await paired.recipe("a/b ?c")
    # One path segment, not a sub-path or a query string.
    assert robot.recipe_ids == ["a/b ?c"]
    assert robot.requests[-1][2] == {}


async def test_categories(paired):
    assert await paired.categories() == CATEGORIES


async def test_image(api, paired):
    assert await paired.image("web-250008") == JPEG
    with pytest.raises(api.ToqueNotFoundError):
        await paired.image("web-250011")


# ------------------------------------------------------------- SSE parser


def test_parser_single_event(api):
    parser = api.SSEParser()
    assert parser.feed(b'event: lid_opened\ndata: {"time": 1}\n\n') == [
        api.SSEMessage("lid_opened", '{"time": 1}')
    ]


def test_parser_keep_alive_comments(api):
    parser = api.SSEParser()
    assert parser.feed(b": keep-alive\n\n") == []
    assert parser.feed(b": keep-alive\n\n: keep-alive\n\n") == []
    # A comment in the middle of an event does not break it.
    assert parser.feed(b'event: fault\n: keep-alive\ndata: {"code": "E1"}\n\n') == [
        api.SSEMessage("fault", '{"code": "E1"}')
    ]


def test_parser_several_events_in_one_chunk(api):
    parser = api.SSEParser()
    chunk = sse("lid_opened", {"time": 1}) + b": keep-alive\n\n" + sse("lid_closed", {"time": 2})
    assert [message.event for message in parser.feed(chunk)] == ["lid_opened", "lid_closed"]


@pytest.mark.parametrize("size", [1, 2, 3, 5, 7, 11, 64])
def test_parser_any_chunking(api, size):
    """Whatever the cut points (lines, UTF-8 characters), the result is the same."""
    stream = (
        sse("status", STATUS)
        + b": keep-alive\n\n"
        + sse("job_started", {"time": 3, "kind": "manual", "label": "Pétrissage", "total": 90,
                              "guided": None})
        + sse("waiting_for_user", {"time": 4, "guided": STATUS["guided"]})
    )
    parser = api.SSEParser()
    messages = []
    for start in range(0, len(stream), size):
        messages.extend(parser.feed(stream[start : start + size]))
    assert [message.event for message in messages] == ["status", "job_started", "waiting_for_user"]
    assert json.loads(messages[0].data) == STATUS
    assert json.loads(messages[1].data)["label"] == "Pétrissage"
    assert json.loads(messages[2].data)["guided"]["recipe_title"] == "Beurre de cacahuètes"


def test_parser_incomplete_event_waits(api):
    parser = api.SSEParser()
    assert parser.feed(b"event: lid_op") == []
    assert parser.feed(b'ened\ndata: {"time"') == []
    assert parser.feed(b": 1}\n") == []
    assert parser.feed(b"\n") == [api.SSEMessage("lid_opened", '{"time": 1}')]


def test_parser_crlf_and_cr(api):
    parser = api.SSEParser()
    assert parser.feed(b'event: a\r\ndata: 1\r\n\r\nevent: b\rdata: 2\r\r') == [
        api.SSEMessage("a", "1")
    ]
    # The last CR could have been half of a CRLF: decided by the next byte.
    assert parser.feed(b"event: c\ndata: 3\n\n") == [
        api.SSEMessage("b", "2"),
        api.SSEMessage("c", "3"),
    ]
    parser = api.SSEParser()
    assert parser.feed(b"data: 1\r") == []
    assert parser.feed(b"\n\r") == []
    assert parser.feed(b"\n") == [api.SSEMessage("message", "1")]


def test_parser_details(api):
    parser = api.SSEParser()
    # No space after the colon, several data lines, unknown fields, no event name.
    assert parser.feed(b"id: 7\nretry: 1000\ndata:one\ndata: two\n\n") == [
        api.SSEMessage("message", "one\ntwo")
    ]
    # An event without data is not dispatched, and its name does not leak.
    assert parser.feed(b"event: lost\n\ndata: 1\n\n") == [api.SSEMessage("message", "1")]


def test_parser_refuses_endless_line(api):
    parser = api.SSEParser()
    with pytest.raises(api.ToqueResponseError):
        parser.feed(b"x" * (api.MAX_SSE_BUFFER + 1))


# ------------------------------------------------------------ event stream


async def test_events_first_status_then_events(api, robot, paired):
    async with aclosing(paired.events()) as stream:
        first = await _take(stream, 1)
        assert first == [api.ToqueEvent("status", STATUS)]

        robot.push_raw(b": keep-alive\n\n")
        robot.push_event("job_started", {"time": 5, "kind": "manual", "label": "Cuisson manuelle",
                                         "total": 180, "guided": None})
        robot.push_raw(b": keep-alive\n\n")
        robot.push_event("lid_opened", {"time": 6})
        events = await _take(stream, 2)

    assert [event.event for event in events] == ["job_started", "lid_opened"]
    assert events[0].data["total"] == 180
    assert events[1].data == {"time": 6}


async def test_events_split_across_reads(api, robot, paired):
    """Two events in three network writes cut in the middle of a line and of a character."""
    robot.send_initial_status = False
    payload = sse("waiting_for_user", {"time": 7, "guided": STATUS["guided"]}) + sse(
        "recipe_finished",
        {"time": 8, "recipe_id": "web-250008", "recipe_title": "Beurre de cacahuètes"},
    )
    cut1 = payload.index("è".encode()) + 1  # inside the two bytes of "è"
    cut2 = len(payload) - 20
    async with aclosing(paired.events()) as stream:
        reading = await _start_reading(robot, stream, 2)
        robot.push_raw(payload[:cut1], pause=0.05)
        robot.push_raw(payload[cut1:cut2], pause=0.05)
        robot.push_raw(payload[cut2:])
        events = await reading

    assert events[0] == api.ToqueEvent("waiting_for_user", {"time": 7, "guided": STATUS["guided"]})
    assert events[1].event == "recipe_finished"
    assert events[1].data["recipe_title"] == "Beurre de cacahuètes"


async def test_events_several_in_one_write(robot, paired):
    robot.send_initial_status = False
    async with aclosing(paired.events()) as stream:
        reading = await _start_reading(robot, stream, 3)
        robot.push_raw(
            sse("job_paused", {"time": 1, "kind": "knead", "label": "Pétrir", "reason": "lid"})
            + b": keep-alive\n\n"
            + sse("job_resumed", {"time": 2, "kind": "knead", "label": "Pétrir"})
            + sse("job_finished", {"time": 3, "kind": "knead", "label": "Pétrir", "guided": None})
        )
        events = await reading
    assert [event.event for event in events] == ["job_paused", "job_resumed", "job_finished"]


async def test_events_invalid_json_is_skipped(robot, paired):
    robot.send_initial_status = False
    async with aclosing(paired.events()) as stream:
        reading = await _start_reading(robot, stream, 1)
        robot.push_raw(b"event: fault\ndata: {not json\n\n")
        robot.push_event("fault", {"time": 9, "code": "E12", "message": "Surchauffe"})
        events = await reading
    assert events[0].data == {"time": 9, "code": "E12", "message": "Surchauffe"}


async def test_events_end_when_robot_closes(robot, paired):
    names = []

    async def read() -> None:
        async for event in paired.events():
            names.append(event.event)
            robot.close_streams()

    await asyncio.wait_for(read(), 5)
    assert names == ["status"]


async def test_events_closing_the_iterator_disconnects(robot, paired):
    async with aclosing(paired.events()) as stream:
        await _take(stream, 1)
        assert robot.stream_count == 1
    for _ in range(200):
        if robot.stream_count == 0:
            break
        robot.push_raw(b": keep-alive\n\n")
        await asyncio.sleep(0.01)
    assert robot.stream_count == 0


async def test_events_silent_stream_times_out(api, robot, paired, monkeypatch):
    """No data and no keep-alive: the connection is given up, to be reopened."""
    monkeypatch.setattr(api, "STREAM_READ_TIMEOUT", 0.3)
    names = []
    with pytest.raises(api.ToqueConnectionError):
        async for event in paired.events():
            names.append(event.event)
    assert names == ["status"]
