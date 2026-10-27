"""`python -m app.mirror <udid> <port> <allowed origins,…>`: live iPhone screen + touch/keyboard/buttons.

This is Xcode's screen mirroring, as pymobiledevice3 reimplements it (`ScreenStreamServer`): the
iPhone streams HEVC video over CoreDevice's DisplayService, and the same session authenticates
touch, keyboard and hardware-button HID events (no WebDriverAgent, no signing). Needs Developer
Mode and the Developer Disk Image (see screen.py).

pymobiledevice3's own viewer decodes the video in the browser with WebCodecs, which can't decode
this HEVC on Windows (Edge included). So this process subscribes to the same stream internally,
decodes it with PyAV (libavcodec) and serves MJPEG, which any <img> shows. Its own HTTP server is
the only one exposed:

    GET  /mjpeg      multipart/x-mixed-replace JPEG stream
    GET  /frame.jpg  the latest frame
    GET  /info       {width, height, fps, clients}
    POST /touch {type: contact|release|tap, x, y}   x, y normalised 0..65535 over the frame
    POST /key {usages: [HID keyboard usages held down]}
    POST /button {name: home|lock|volume-up|volume-down|mute|siri, state?}
    POST /quit       orderly shutdown (frees the iPhone's screen-sharing session)

POSTs must come from the app's origin (a page on another site can't drive the phone).
pymobiledevice3's server runs on the next port, bound to loopback, and is only used internally.
"""

import asyncio
import contextlib
import io
import json
import logging
import queue
import sys
import threading
import time
from typing import Any, Optional

import av

from pymobiledevice3.remote.core_device.screen_stream import ScreenStreamServer, _SubState
from pymobiledevice3.remote.userspace_tunnel import UserspaceRsdTunnel

log = logging.getLogger("mirror")

JPEG_FPS = 15
MAX_HEIGHT = 1280
JPEG_QUALITY = 70
INPUT_PATHS = ("/touch", "/key", "/button")


class Mirror(ScreenStreamServer):
    def __init__(self, rsd: Any, internal_port: int) -> None:
        super().__init__(rsd, bind="127.0.0.1", http_port=internal_port, audio_default_on=False)
        self.jpeg: Optional[bytes] = None
        self.size = (0, 0)
        self.clients = 0
        self._frame_event = asyncio.Event()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._decode_q: "queue.Queue[Optional[tuple[int, bytes]]]" = queue.Queue(maxsize=120)
        self._fps_times: list[float] = []

    # ---------- video: internal subscriber -> PyAV -> JPEG ----------

    async def run_decoder(self) -> None:
        """Subscribe to the AU broadcast exactly like the viewer's /stream.bin does."""
        self._loop = asyncio.get_running_loop()
        threading.Thread(target=self._decode_worker, name="mirror-decode", daemon=True).start()
        while True:
            if self._active_service is None or self._init_sequence is None:
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(self._ensure_fresh_stream(force=False), timeout=10.0)
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._stream_ready.wait(), timeout=3.0)
            if self._init_sequence is None:
                await asyncio.sleep(1)
                continue
            q: asyncio.Queue[bytes] = asyncio.Queue(maxsize=64)
            state = _SubState()
            state.mark_needs_key(self._loop.time())
            state.reset_on_key = True
            self._subscribers[q] = state
            self._request_recovery_idr(reason="mirror-subscribe")
            try:
                while True:
                    msg = await q.get()
                    kind = msg[4]
                    au = _to_annexb(msg[5:])
                    try:
                        self._decode_q.put_nowait((kind, au))
                    except queue.Full:  # decoder behind: resync from the next keyframe
                        state.mark_needs_key(self._loop.time())
                        self._request_recovery_idr(reason="mirror-decoder-behind")
            finally:
                self._subscribers.pop(q, None)

    def _decode_worker(self) -> None:
        codec = None
        last_jpeg = 0.0
        while True:
            item = self._decode_q.get()
            if item is None:
                return
            kind, au = item
            if codec is None or kind == 2:  # 2 = keyframe after a reset: start a fresh decoder
                codec = av.codec.CodecContext.create("hevc", "r")
            try:
                frames = list(codec.decode(av.Packet(au)))
            except Exception as e:
                log.debug("decode error: %s", e)
                codec = None
                self._loop.call_soon_threadsafe(lambda: self._request_recovery_idr(reason="mirror-decode-error"))
                continue
            if not frames or not self.clients:
                continue
            now = time.monotonic()
            if now - last_jpeg < 1 / JPEG_FPS:
                continue  # every AU is decoded (P-frames need it); only some become JPEGs
            last_jpeg = now
            try:
                jpeg, size = _encode(frames[-1])
            except Exception as e:
                log.warning("jpeg encode failed: %s", e)
                continue
            self._loop.call_soon_threadsafe(self._publish, jpeg, size)

    def _publish(self, jpeg: bytes, size: tuple[int, int]) -> None:
        self.jpeg, self.size = jpeg, size
        now = time.monotonic()
        self._fps_times = [t for t in self._fps_times if now - t < 2] + [now]
        self._frame_event.set()
        self._frame_event = asyncio.Event()

    # ---------- front HTTP server ----------

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, origins_ok: set[str],
                     stop: asyncio.Event) -> None:
        try:
            request = await reader.readline()
            method, path = request.decode("latin-1").split(" ")[:2]
            headers: dict[str, str] = {}
            while (line := await reader.readline()) not in (b"\r\n", b"\n", b""):
                k, _sep, v = line.decode("latin-1").partition(":")
                headers[k.strip().lower()] = v.strip()
            path = path.split("?")[0]
            if method == "POST":
                origin = headers.get("origin")
                if origin is not None and origin not in origins_ok:
                    return _reply(writer, 403, b"forbidden origin")
                body = await reader.readexactly(int(headers.get("content-length", "0") or 0))
                if path in INPUT_PATHS:
                    self._hid_queue.put_nowait((path, body))
                    return _reply(writer, 200, b"ok")
                if path == "/quit":
                    stop.set()
                    return _reply(writer, 200, b"bye")
                return _reply(writer, 404, b"not found")
            if path == "/info":
                fps = len(self._fps_times) / 2
                body = json.dumps({"width": self.size[0], "height": self.size[1], "fps": fps,
                                   "clients": self.clients, "streaming": self._active_service is not None}).encode()
                cors = headers.get("origin") if headers.get("origin") in origins_ok else None
                return _reply(writer, 200, body, b"application/json", cors)
            if path == "/frame.jpg":
                self.clients += 1
                try:
                    if self.jpeg is None:
                        with contextlib.suppress(asyncio.TimeoutError):
                            await asyncio.wait_for(self._frame_event.wait(), 10)
                finally:
                    self.clients -= 1
                if self.jpeg is None:
                    return _reply(writer, 503, b"no frame yet")
                return _reply(writer, 200, self.jpeg, b"image/jpeg")
            if path == "/mjpeg":
                await self._mjpeg(writer)
                return
            _reply(writer, 404, b"not found")
        except (ConnectionError, asyncio.IncompleteReadError, ValueError):
            pass
        finally:
            with contextlib.suppress(Exception):
                await writer.drain()
                writer.close()

    async def _mjpeg(self, writer: asyncio.StreamWriter) -> None:
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: multipart/x-mixed-replace; boundary=frame\r\n"
                     b"Cache-Control: no-store\r\nConnection: close\r\n\r\n")
        self.clients += 1
        try:
            sent = None
            while True:
                if self.jpeg is None or self.jpeg is sent:
                    with contextlib.suppress(asyncio.TimeoutError):
                        await asyncio.wait_for(self._frame_event.wait(), 5)
                    if self.jpeg is None or self.jpeg is sent:
                        if sent is not None:  # static screen: resend so the browser keeps the connection
                            sent = None
                        continue
                sent = self.jpeg
                writer.write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: %d\r\n\r\n" % len(sent)
                             + sent + b"\r\n")
                await writer.drain()
        finally:
            self.clients -= 1


def _to_annexb(au: bytes) -> bytes:
    """The broadcast carries 4-byte length-prefixed NALs; libav wants Annex-B start codes."""
    out = bytearray()
    i = 0
    while i + 4 <= len(au):
        n = int.from_bytes(au[i:i + 4], "big")
        out += b"\x00\x00\x00\x01" + au[i + 4:i + 4 + n]
        i += 4 + n
    return bytes(out)


def _encode(frame: Any) -> tuple[bytes, tuple[int, int]]:
    h = min(frame.height, MAX_HEIGHT)
    w = round(frame.width * h / frame.height) & ~1
    img = frame.reformat(width=w, height=h, format="rgb24").to_image()
    out = io.BytesIO()
    img.save(out, "JPEG", quality=JPEG_QUALITY)
    return out.getvalue(), (w, h)


def _reply(writer: asyncio.StreamWriter, status: int, body: bytes, ctype: bytes = b"text/plain",
           cors_origin: Optional[str] = None) -> None:
    reason = {200: b"OK", 403: b"Forbidden", 404: b"Not Found", 503: b"Service Unavailable"}.get(status, b"Error")
    cors = b"Access-Control-Allow-Origin: %s\r\n" % cors_origin.encode() if cors_origin else b""
    writer.write(b"HTTP/1.1 %d %s\r\nContent-Type: %s\r\nContent-Length: %d\r\nCache-Control: no-store\r\n"
                 b"%sConnection: close\r\n\r\n" % (status, reason, ctype, len(body), cors) + body)


async def main(udid: str, port: int, origins: str) -> None:
    origins_ok = set(origins.split(","))
    async with UserspaceRsdTunnel(serial=udid) as rsd:
        mirror = Mirror(rsd, internal_port=port + 1)
        stop = asyncio.Event()
        front = await asyncio.start_server(lambda r, w: mirror.handle(r, w, origins_ok, stop), "127.0.0.1", port)
        serve = asyncio.create_task(mirror.serve())
        decoder = asyncio.create_task(mirror.run_decoder())
        print(f"mirror ready on 127.0.0.1:{port}", flush=True)
        try:
            await asyncio.wait([asyncio.create_task(stop.wait()), serve], return_when=asyncio.FIRST_COMPLETED)
        finally:
            front.close()
            decoder.cancel()
            mirror._decode_q.put(None)
            # cancelling serve() runs its orderly shutdown: stops the stream and releases the
            # iPhone's screen-sharing session
            serve.cancel()
            with contextlib.suppress(BaseException):
                await asyncio.wait_for(serve, 20)
    print("mirror stopped", flush=True)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    logging.getLogger("pymobiledevice3.remote.userspace_tunnel").setLevel(logging.WARNING)
    try:
        asyncio.run(main(sys.argv[1], int(sys.argv[2]), sys.argv[3]))
    except KeyboardInterrupt:
        pass
