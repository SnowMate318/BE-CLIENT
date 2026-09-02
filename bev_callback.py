"""Receive one BEV PNG callback over HTTP."""

from __future__ import annotations

import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from queue import Empty, Full, Queue
from threading import Thread
from typing import Optional


PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
MAX_PNG_BYTES = 16 * 1024 * 1024


class CallbackError(RuntimeError):
    """The callback listener failed or timed out."""


def validate_png(payload: bytes) -> tuple[int, int]:
    """Validate the minimal PNG structure needed before passing it to the display."""
    if len(payload) < 45 or not payload.startswith(PNG_SIGNATURE):
        raise CallbackError("callback body is not a PNG image")
    if int.from_bytes(payload[8:12], "big") != 13 or payload[12:16] != b"IHDR":
        raise CallbackError("callback PNG has no valid IHDR")
    width = int.from_bytes(payload[16:20], "big")
    height = int.from_bytes(payload[20:24], "big")
    if width < 1 or height < 1 or width > 16384 or height > 16384:
        raise CallbackError("callback PNG dimensions are invalid")
    if payload[-12:-8] != b"\x00\x00\x00\x00" or payload[-8:-4] != b"IEND":
        raise CallbackError("callback PNG is incomplete")
    return width, height


def _make_handler(result_queue: Queue, callback_path: str, max_bytes: int):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _respond(self, status: int) -> None:
            self.send_response(status)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_POST(self) -> None:
            if self.path != callback_path:
                self._respond(404)
                return
            content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
            if content_type != "image/png":
                self._respond(415)
                return
            raw_length = self.headers.get("Content-Length")
            if raw_length is None:
                self._respond(411)
                return
            try:
                length = int(raw_length)
            except ValueError:
                self._respond(400)
                return
            if length < 1:
                self._respond(400)
                return
            if length > max_bytes:
                self._respond(413)
                return
            payload = self.rfile.read(length)
            if len(payload) != length:
                self._respond(400)
                return
            try:
                validate_png(payload)
            except CallbackError:
                self._respond(400)
                return
            try:
                result_queue.put_nowait(payload)
            except Full:
                self._respond(409)
                return
            self._respond(204)

        def do_GET(self) -> None:
            self._respond(405)

        def log_message(self, _format: str, *args) -> None:
            pass

    return Handler


class CallbackReceiver:
    """A one-result callback listener with an unguessable URL path."""

    def __init__(
        self,
        bind_host: str = "0.0.0.0",
        port: int = 8765,
        max_bytes: int = MAX_PNG_BYTES,
    ) -> None:
        if not 0 <= port <= 65535:
            raise CallbackError("callback port must be between 0 and 65535")
        if max_bytes < 1:
            raise CallbackError("callback size limit must be positive")
        self.path = f"/bev-callback/{secrets.token_urlsafe(24)}"
        self._queue: Queue = Queue(maxsize=1)
        handler = _make_handler(self._queue, self.path, max_bytes)
        self._server = ThreadingHTTPServer((bind_host, port), handler)
        self._server.daemon_threads = True
        self._thread: Optional[Thread] = None

    @property
    def port(self) -> int:
        return int(self._server.server_address[1])

    def callback_url(self, advertise_host: str) -> str:
        host = advertise_host.strip()
        if not host or any(character in host for character in "/?#@"):
            raise CallbackError("callback advertise host is invalid")
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        return f"http://{host}:{self.port}{self.path}"

    def start(self) -> None:
        if self._thread is not None:
            raise CallbackError("callback listener is already running")
        self._thread = Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def wait(self, timeout: float) -> bytes:
        if timeout <= 0:
            raise CallbackError("callback timeout must be positive")
        try:
            return self._queue.get(timeout=timeout)
        except Empty as exc:
            raise CallbackError(f"BEV callback did not arrive within {timeout:g} seconds") from exc

    def close(self) -> None:
        if self._thread is not None:
            self._server.shutdown()
        self._server.server_close()
        if self._thread is not None:
            self._thread.join()
            self._thread = None

    def __enter__(self) -> "CallbackReceiver":
        self.start()
        return self

    def __exit__(self, _type, _value, _traceback) -> None:
        self.close()
