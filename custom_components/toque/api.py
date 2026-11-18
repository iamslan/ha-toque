"""Async client for the Toque home link API (version 1).

This module deliberately imports nothing from Home Assistant, so it can be
used and tested on its own. The API is local, read-only and documented in
Toque's ``docs/HOME_LINK_API.md``.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
import json
import logging
from typing import Any
from urllib.parse import quote

import aiohttp
from yarl import URL

_LOGGER = logging.getLogger(__name__)

DEFAULT_PORT = 8730

# Ordinary requests: the robot is on the local network, answers are small.
REQUEST_TIMEOUT = 10
# The event stream sends a keep-alive comment every 15 seconds; three missed
# keep-alives mean the connection is dead.
STREAM_READ_TIMEOUT = 45
# A single SSE line never comes close to this; it only guards memory against
# a peer that is not a Toque robot.
MAX_SSE_BUFFER = 1024 * 1024

PAIRING_ERROR_CODES = ("bad_code", "pairing_closed", "too_many_attempts")


class ToqueError(Exception):
    """Base class for every error raised by this client."""


class ToqueConnectionError(ToqueError):
    """The robot could not be reached (switched off, wrong address, timeout)."""


class ToqueResponseError(ToqueError):
    """The robot answered with an error status or an unusable body."""

    def __init__(self, status: int, code: str | None = None, message: str | None = None) -> None:
        self.status = status
        self.code = code
        self.message = message
        super().__init__(f"HTTP {status}: {code or 'error'}" + (f" ({message})" if message else ""))


class ToqueAuthError(ToqueResponseError):
    """HTTP 401: the token is missing, unknown or was revoked on the robot."""


class ToquePairingError(ToqueResponseError):
    """Pairing was refused: ``code`` is one of ``PAIRING_ERROR_CODES``."""


class ToqueNotFoundError(ToqueResponseError):
    """HTTP 404: unknown recipe, or a recipe without a photo."""


@dataclass(frozen=True, slots=True)
class ToqueEvent:
    """One message of the ``/api/events`` stream, with its JSON data decoded."""

    event: str
    data: Any


@dataclass(frozen=True, slots=True)
class SSEMessage:
    """One raw server-sent event: its name and its undecoded data."""

    event: str
    data: str


class SSEParser:
    """Incremental parser for a ``text/event-stream`` body.

    Feed it the bytes as they arrive, in chunks of any size: a chunk may hold
    several events, half an event, or stop in the middle of a line or of a
    multi-byte character. Comment lines (``: keep-alive``) are ignored.
    """

    def __init__(self) -> None:
        self._buffer = bytearray()
        self._event = ""
        self._data: list[str] = []

    def feed(self, chunk: bytes) -> list[SSEMessage]:
        """Add received bytes and return the events they completed."""
        self._buffer.extend(chunk)
        messages: list[SSEMessage] = []
        while True:
            line = self._next_line()
            if line is None:
                break
            message = self._handle_line(line)
            if message is not None:
                messages.append(message)
        if len(self._buffer) > MAX_SSE_BUFFER:
            raise ToqueResponseError(200, "invalid_stream", "event stream line too long")
        return messages

    def _next_line(self) -> str | None:
        """Pop one complete line off the buffer (LF, CRLF or CR terminated)."""
        buffer = self._buffer
        lf = buffer.find(b"\n")
        cr = buffer.find(b"\r")
        if lf == -1 and cr == -1:
            return None
        if cr != -1 and (lf == -1 or cr < lf):
            if cr + 1 == len(buffer):
                # A CR at the very end may be the first half of a CRLF.
                return None
            end = cr
            skip = 2 if buffer[cr + 1] == 0x0A else 1
        else:
            end = lf
            skip = 1
        line = bytes(buffer[:end]).decode("utf-8", errors="replace")
        del buffer[: end + skip]
        return line

    def _handle_line(self, line: str) -> SSEMessage | None:
        if line == "":
            # Blank line: dispatch what was collected, if anything.
            message = None
            if self._data:
                message = SSEMessage(self._event or "message", "\n".join(self._data))
            self._event = ""
            self._data = []
            return message
        if line.startswith(":"):
            return None
        field, _, value = line.partition(":")
        if value.startswith(" "):
            value = value[1:]
        if field == "event":
            self._event = value
        elif field == "data":
            self._data.append(value)
        # "id" and "retry" are not used by the Toque API.
        return None


class ToqueClient:
    """Client for one robot. The ``aiohttp`` session is owned by the caller."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        host: str,
        port: int = DEFAULT_PORT,
        token: str | None = None,
    ) -> None:
        self._session = session
        self.host = host
        self.port = port
        self.token = token
        self._base = str(URL.build(scheme="http", host=host, port=port)).rstrip("/")

    @property
    def base_url(self) -> str:
        """Root URL of the robot, without a trailing slash."""
        return self._base

    # ----------------------------------------------------------------- plumbing

    def _headers(self, auth: bool, accept: str = "application/json") -> dict[str, str]:
        headers = {"Accept": accept}
        if auth:
            if not self.token:
                raise ToqueAuthError(401, "unauthorized", "no token: pair with the robot first")
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    @staticmethod
    async def _error_from(response: aiohttp.ClientResponse, pairing: bool = False) -> ToqueResponseError:
        """Build the exception for an HTTP error answer."""
        code: str | None = None
        message: str | None = None
        try:
            body = json.loads(await response.text())
        except (ValueError, aiohttp.ClientError, asyncio.TimeoutError):
            body = None
        if isinstance(body, dict):
            if isinstance(body.get("error"), str):
                code = body["error"]
            if isinstance(body.get("message"), str):
                message = body["message"]
        status = response.status
        if status == 401:
            return ToqueAuthError(status, code or "unauthorized", message)
        if pairing and (status in (403, 429) or code in PAIRING_ERROR_CODES):
            if code is None:
                code = "too_many_attempts" if status == 429 else "pairing_closed"
            return ToquePairingError(status, code, message)
        if status == 404:
            return ToqueNotFoundError(status, code or "not_found", message)
        return ToqueResponseError(status, code, message)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        auth: bool = True,
        params: dict[str, str] | None = None,
        body: dict[str, Any] | None = None,
        pairing: bool = False,
    ) -> dict[str, Any]:
        """Send one request and return the decoded JSON object."""
        headers = self._headers(auth)
        try:
            async with self._session.request(
                method,
                URL(self._base + path, encoded=True),
                params=params,
                json=body,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
            ) as response:
                if response.status >= 400:
                    raise await self._error_from(response, pairing)
                text = await response.text(encoding="utf-8")
                status = response.status
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as err:
            raise ToqueConnectionError(f"cannot reach {self._base}: {err!r}") from err
        try:
            data = json.loads(text)
        except ValueError as err:
            raise ToqueResponseError(status, "invalid_response", "body is not JSON") from err
        if not isinstance(data, dict):
            raise ToqueResponseError(status, "invalid_response", "body is not a JSON object")
        return data

    # ---------------------------------------------------------------- endpoints

    async def info(self) -> dict[str, Any]:
        """``GET /api/info`` (no token needed): id, name, version, model."""
        data = await self._request("GET", "/api/info", auth=False)
        if not isinstance(data.get("id"), str) or not data["id"]:
            raise ToqueResponseError(200, "invalid_response", "no id in /api/info")
        return data

    async def pair(self, code: str, name: str = "Home Assistant") -> dict[str, Any]:
        """``POST /api/pair``: exchange the 6-digit code for a token.

        On success the token is kept on this client and the whole answer
        (``token``, ``id``, ``name``) is returned. A refusal raises
        ``ToquePairingError`` whose ``code`` says why.
        """
        data = await self._request(
            "POST", "/api/pair", auth=False, body={"code": code, "name": name}, pairing=True
        )
        token = data.get("token")
        if not isinstance(token, str) or not token:
            raise ToqueResponseError(200, "invalid_response", "no token in /api/pair")
        self.token = token
        return data

    async def status(self) -> dict[str, Any]:
        """``GET /api/status``: the whole state of the robot."""
        return await self._request("GET", "/api/status")

    async def recipes(
        self,
        query: str | None = None,
        *,
        category: str | None = None,
        favorites: bool = False,
        limit: int | None = None,
        offset: int | None = None,
    ) -> dict[str, Any]:
        """``GET /api/recipes``: search; returns total, offset, limit, items."""
        params: dict[str, str] = {}
        if query:
            params["q"] = query
        if category:
            params["category"] = category
        if favorites:
            params["favorites"] = "1"
        if limit is not None:
            params["limit"] = str(limit)
        if offset:
            params["offset"] = str(offset)
        return await self._request("GET", "/api/recipes", params=params)

    async def recipe(self, recipe_id: str) -> dict[str, Any]:
        """``GET /api/recipes/{id}``: one whole recipe."""
        return await self._request("GET", f"/api/recipes/{quote(recipe_id, safe='')}")

    async def categories(self) -> list[str]:
        """``GET /api/categories``: the category names."""
        data = await self._request("GET", "/api/categories")
        categories = data.get("categories")
        if not isinstance(categories, list):
            raise ToqueResponseError(200, "invalid_response", "no categories list")
        return [str(category) for category in categories]

    async def image(self, recipe_id: str) -> bytes:
        """``GET /api/recipes/{id}/image``: the photo as JPEG bytes.

        Raises ``ToqueNotFoundError`` when the robot has no photo.
        """
        headers = self._headers(True, accept="image/jpeg")
        url = URL(f"{self._base}/api/recipes/{quote(recipe_id, safe='')}/image", encoded=True)
        try:
            async with self._session.get(
                url, headers=headers, timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
            ) as response:
                if response.status >= 400:
                    raise await self._error_from(response)
                return await response.read()
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as err:
            raise ToqueConnectionError(f"cannot reach {self._base}: {err!r}") from err

    async def events(self) -> AsyncIterator[ToqueEvent]:
        """``GET /api/events``: iterate over the server-sent events.

        The iterator ends when the robot closes the stream and raises
        ``ToqueConnectionError`` when the connection fails or goes silent
        (no data and no keep-alive for ``STREAM_READ_TIMEOUT`` seconds). The
        caller reconnects. Close the iterator (``aclose``) to disconnect.
        """
        headers = self._headers(True, accept="text/event-stream")
        headers["Cache-Control"] = "no-cache"
        parser = SSEParser()
        timeout = aiohttp.ClientTimeout(
            total=None, sock_connect=REQUEST_TIMEOUT, sock_read=STREAM_READ_TIMEOUT
        )
        try:
            async with self._session.get(
                URL(self._base + "/api/events", encoded=True), headers=headers, timeout=timeout
            ) as response:
                if response.status >= 400:
                    raise await self._error_from(response)
                async for chunk in response.content.iter_any():
                    for message in parser.feed(chunk):
                        try:
                            data = json.loads(message.data)
                        except ValueError:
                            _LOGGER.debug(
                                "Ignoring %s event with invalid JSON: %.200s",
                                message.event,
                                message.data,
                            )
                            continue
                        yield ToqueEvent(message.event, data)
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as err:
            raise ToqueConnectionError(f"event stream of {self._base} lost: {err!r}") from err
