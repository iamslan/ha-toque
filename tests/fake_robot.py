"""A fake Toque robot: a small aiohttp server that implements the home link API.

Used by the tests, and runnable on its own to try the integration in a real
Home Assistant without a robot::

    python tests/fake_robot.py --port 8730 --code 123456

It then plays a short cooking scenario in a loop on ``/api/events``.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import json
import time
from typing import Any

from aiohttp import web

ROBOT_ID = "toque-1a2b3c4d"
TOKEN = "9f2c" + "0" * 60  # 64 hex characters
JPEG = b"\xff\xd8\xff\xe0fake-jpeg\xff\xd9"

INFO = {
    "id": ROBOT_ID,
    "name": "Toque",
    "version": "0.6.0-iamslan",
    "api": 1,
    "model": "Monsieur Cuisine connect",
    "pairing_open": False,
}

GUIDED = {
    "recipe_id": "web-250008",
    "recipe_title": "Beurre de cacahuètes",
    "step": 3,
    "steps": 7,
    "step_text": "Mixer 30 s à vitesse 8.",
    "step_kind": "machine",
    "step_setting": "00:30 · vitesse 8",
    "weigh_target": None,
    "waiting_for_user": False,
}

STATUS = {
    "id": ROBOT_ID,
    "version": "0.6.0-iamslan",
    "time": 1794996000,
    "link": "ready",
    "mode": "hardware",
    "state": "running",
    "fault": None,
    "lid_open": False,
    "bowl_present": True,
    "bowl_sensor_reliable": True,
    "motor_on": True,
    "heater_on": True,
    "temperature": 37,
    "temperature_below_range": False,
    "target_temperature": 60,
    "speed": 2,
    "rpm": 190,
    "weight": 512,
    "weight_stable": True,
    "firmware": 4922,
    "job": {
        "kind": "manual",
        "label": "Cuisson manuelle",
        "state": "running",
        "pause_reason": None,
        "elapsed": 35,
        "total": 180,
        "remaining": 145,
        "ends_at": 1794996145,
        "phase": 1,
        "phases": 1,
        "speed": 2,
        "target_temperature": 60,
        "reverse": False,
        "accessory": "blade",
    },
    "guided": GUIDED,
}

RECIPE = {
    "id": "web-250008",
    "title": "Beurre de cacahuètes",
    "category": "Sauces et dips",
    "tags": ["Végan"],
    "servings": 2,
    "prep_minutes": 3,
    "minutes": 3,
    "difficulty": 1,
    "author": "Monsieur Cuisine Official",
    "rating": 4.8,
    "rating_count": 1610,
    "favorite": False,
    "notes": "…",
    "has_image": True,
    "ingredients": [
        {
            "group": "",
            "items": [
                {
                    "quantity": "300",
                    "unit": "g",
                    "name": "cacahuètes grillées",
                    "note": "non salées",
                    "text": "300 g cacahuètes grillées, non salées",
                },
                {"quantity": "1/2", "unit": "c. à c.", "name": "sel", "note": "", "text": "1/2 c. à c. sel"},
            ],
        }
    ],
    "steps": [
        {"text": "Mettre les cacahuètes dans le bol.", "kind": "weigh", "setting": None, "weigh_target": 300},
        {"text": "Mixer 30 s à vitesse 8.", "kind": "machine", "setting": "00:30 · vitesse 8", "weigh_target": None},
    ],
}

RECIPE_ITEMS = [
    {
        "id": "web-250008",
        "title": "Beurre de cacahuètes",
        "category": "Sauces et dips",
        "tags": ["Végan", "Végétarien"],
        "minutes": 3,
        "rating": 4.8,
        "rating_count": 1610,
        "favorite": False,
        "machine_steps": 2,
        "has_image": True,
    },
    {
        "id": "web-250011",
        "title": "Crème brûlée",
        "category": "Desserts",
        "tags": [],
        "minutes": 45,
        "rating": 4.5,
        "rating_count": 320,
        "favorite": True,
        "machine_steps": 3,
        "has_image": False,
    },
]

CATEGORIES = ["Desserts", "Entrées", "Plats principaux", "Sauces et dips"]

MAX_WRONG_CODES = 5

_CLOSE = object()


def sse(event: str, data: Any) -> bytes:
    """One server-sent event, as the contract writes it."""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode()


def _error(status: int, code: str, message: str = "") -> web.Response:
    return web.json_response({"error": code, "message": message or code}, status=status)


class FakeRobot:
    """The state of the fake robot, and its aiohttp application."""

    def __init__(self) -> None:
        self.status: dict[str, Any] = copy.deepcopy(STATUS)
        self.pairing_code: str | None = None
        self.wrong_codes = 0
        self.tokens: set[str] = set()
        self.paired_names: list[str] = []
        self.requests: list[tuple[str, str, dict[str, str]]] = []
        self.recipe_ids: list[str] = []
        self.port = 0
        self.send_initial_status = True
        self._streams: list[asyncio.Queue[Any]] = []
        self.app = web.Application()
        self.app.add_routes(
            [
                web.get("/api/info", self._info),
                web.post("/api/pair", self._pair),
                web.get("/api/status", self._status),
                web.get("/api/events", self._events),
                web.get("/api/recipes", self._recipes),
                web.get("/api/categories", self._categories),
                web.get("/api/recipes/{id}", self._recipe),
                web.get("/api/recipes/{id}/image", self._image),
            ]
        )

    # -------------------------------------------------- driving it from tests

    def open_pairing(self, code: str = "482913") -> None:
        """Show a pairing code on the (imaginary) screen."""
        self.pairing_code = code
        self.wrong_codes = 0

    def unpair_all(self) -> None:
        """"Unpair all devices": every token is revoked."""
        self.tokens.clear()

    @property
    def stream_count(self) -> int:
        return len(self._streams)

    def push_raw(self, chunk: bytes, pause: float = 0.0) -> None:
        """Write these exact bytes on every open event stream, then pause."""
        for queue in self._streams:
            queue.put_nowait((chunk, pause))

    def push_event(self, event: str, data: Any) -> None:
        self.push_raw(sse(event, data))

    def close_streams(self) -> None:
        """End every open event stream, as when Toque is closed."""
        for queue in self._streams:
            queue.put_nowait(_CLOSE)

    # ----------------------------------------------------------------- routes

    def _authorized(self, request: web.Request) -> bool:
        self.requests.append((request.method, request.path, dict(request.query)))
        header = request.headers.get("Authorization", "")
        return header.startswith("Bearer ") and header[7:] in self.tokens

    async def _info(self, request: web.Request) -> web.Response:
        return web.json_response({**INFO, "pairing_open": self.pairing_code is not None})

    async def _pair(self, request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except ValueError:
            return _error(400, "bad_request")
        if not isinstance(body, dict) or not isinstance(body.get("code"), str):
            return _error(400, "bad_request")
        if self.pairing_code is None:
            return _error(403, "pairing_closed")
        if body["code"] != self.pairing_code:
            self.wrong_codes += 1
            if self.wrong_codes >= MAX_WRONG_CODES:
                self.pairing_code = None
                return _error(429, "too_many_attempts")
            return _error(403, "bad_code")
        self.pairing_code = None
        self.tokens.add(TOKEN)
        self.paired_names.append(str(body.get("name", "")))
        return web.json_response({"token": TOKEN, "id": ROBOT_ID, "name": INFO["name"]})

    async def _status(self, request: web.Request) -> web.Response:
        if not self._authorized(request):
            return _error(401, "unauthorized")
        return web.json_response(self.status)

    async def _events(self, request: web.Request) -> web.StreamResponse:
        if not self._authorized(request):
            return _error(401, "unauthorized")
        response = web.StreamResponse(
            headers={"Content-Type": "text/event-stream", "Cache-Control": "no-cache"}
        )
        await response.prepare(request)
        queue: asyncio.Queue[Any] = asyncio.Queue()
        self._streams.append(queue)
        try:
            if self.send_initial_status:
                await response.write(sse("status", self.status))
            while True:
                item = await queue.get()
                if item is _CLOSE:
                    break
                chunk, pause = item
                await response.write(chunk)
                if pause:
                    await asyncio.sleep(pause)
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        finally:
            self._streams.remove(queue)
        return response

    async def _recipes(self, request: web.Request) -> web.Response:
        if not self._authorized(request):
            return _error(401, "unauthorized")
        query = request.query.get("q", "").lower()
        category = request.query.get("category", "")
        favorites = request.query.get("favorites") == "1"
        try:
            limit = min(int(request.query.get("limit", 50)), 200)
            offset = int(request.query.get("offset", 0))
        except ValueError:
            return _error(400, "bad_request")
        items = [
            item
            for item in RECIPE_ITEMS
            if query in item["title"].lower()
            and (not category or item["category"] == category)
            and (not favorites or item["favorite"])
        ]
        items.sort(key=lambda item: (not item["favorite"], item["title"]))
        return web.json_response(
            {"total": len(items), "offset": offset, "limit": limit, "items": items[offset : offset + limit]}
        )

    async def _categories(self, request: web.Request) -> web.Response:
        if not self._authorized(request):
            return _error(401, "unauthorized")
        return web.json_response({"categories": CATEGORIES})

    async def _recipe(self, request: web.Request) -> web.Response:
        if not self._authorized(request):
            return _error(401, "unauthorized")
        self.recipe_ids.append(request.match_info["id"])
        if request.match_info["id"] != RECIPE["id"]:
            return _error(404, "not_found")
        return web.json_response(RECIPE)

    async def _image(self, request: web.Request) -> web.Response:
        if not self._authorized(request):
            return _error(401, "unauthorized")
        if request.match_info["id"] != RECIPE["id"]:
            return _error(404, "not_found")
        return web.Response(body=JPEG, content_type="image/jpeg")


async def _scenario(robot: FakeRobot) -> None:
    """A short guided-recipe scenario, played in a loop."""
    idle = {**STATUS, "state": "idle", "motor_on": False, "heater_on": False, "speed": 0,
            "rpm": 0, "target_temperature": None, "job": None, "guided": None}
    while True:
        now = int(time.time())
        robot.status = {**idle, "time": now}
        robot.push_event("status", robot.status)
        await asyncio.sleep(10)

        now = int(time.time())
        job = {**STATUS["job"], "elapsed": 0, "total": 30, "remaining": 30, "ends_at": now + 30}
        robot.status = {**STATUS, "time": now, "job": job}
        robot.push_event("job_started", {"time": now, "kind": "manual", "label": job["label"],
                                         "total": 30, "guided": GUIDED})
        robot.push_event("status", robot.status)
        await asyncio.sleep(15)

        now = int(time.time())
        robot.push_event("lid_opened", {"time": now})
        robot.push_event("job_paused", {"time": now, "kind": "manual", "label": job["label"], "reason": "lid"})
        robot.status = {**robot.status, "time": now, "state": "paused", "lid_open": True, "motor_on": False}
        robot.push_event("status", robot.status)
        await asyncio.sleep(5)

        now = int(time.time())
        robot.push_event("lid_closed", {"time": now})
        robot.push_event("job_resumed", {"time": now, "kind": "manual", "label": job["label"]})
        robot.status = {**robot.status, "time": now, "state": "running", "lid_open": False, "motor_on": True}
        robot.push_event("status", robot.status)
        await asyncio.sleep(15)

        now = int(time.time())
        waiting = {**GUIDED, "step": 4, "step_text": "Racler les parois du bol avec la spatule.",
                   "step_kind": "text", "step_setting": None, "waiting_for_user": True}
        robot.push_event("job_finished", {"time": now, "kind": "manual", "label": job["label"], "guided": GUIDED})
        robot.status = {**idle, "time": now, "state": "waiting_for_user", "guided": waiting}
        robot.push_event("status", robot.status)
        await asyncio.sleep(3)
        robot.push_event("waiting_for_user", {"time": int(time.time()), "guided": waiting})
        await asyncio.sleep(15)
        robot.push_event("recipe_finished", {"time": int(time.time()), "recipe_id": GUIDED["recipe_id"],
                                             "recipe_title": GUIDED["recipe_title"]})


async def _keep_alive(robot: FakeRobot) -> None:
    while True:
        await asyncio.sleep(15)
        robot.push_raw(b": keep-alive\n\n")


async def _main(port: int, code: str) -> None:
    robot = FakeRobot()

    # The standalone robot accepts the same code again and again.
    async def reopen() -> None:
        while True:
            if robot.pairing_code is None:
                robot.open_pairing(code)
            await asyncio.sleep(1)

    runner = web.AppRunner(robot.app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", port).start()
    print(f"Fake Toque robot on port {port}, pairing code {code}. Ctrl+C to stop.")
    await asyncio.gather(reopen(), _scenario(robot), _keep_alive(robot))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fake Toque robot (home link API v1)")
    parser.add_argument("--port", type=int, default=8730)
    parser.add_argument("--code", default="123456")
    args = parser.parse_args()
    try:
        asyncio.run(_main(args.port, args.code))
    except KeyboardInterrupt:
        pass
