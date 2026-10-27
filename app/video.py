"""Playable previews of iPhone videos.

iPhone videos are HEVC (often 4K/60 HDR); Edge/Chrome on Windows play their audio but not their
picture. ffmpeg (the installer's copy in <app>/ffmpeg, else the one on PATH or from Homebrew, which
apps opened from the macOS Finder don't have on their PATH) converts them on the
fly to H.264 720p/30 fragmented MP4, read from the app's own /file endpoint (which supports Range,
so seeking with -ss before -i is quick). Hardware decoding (QSV/D3D11) rejects the iPhone's 10-bit
HDR HEVC here, so decoding is on the CPU; the Intel encoder is tried first, then libx264.
"""

import asyncio
import json
import logging
import shutil
from pathlib import Path
from typing import AsyncIterator, Awaitable, Callable, Optional
from urllib.parse import quote

from .i18n import _

log = logging.getLogger(__name__)

BROWSER_CODECS = {"h264"}  # played as is (seekable, no conversion)
ENCODERS = [
    ["-c:v", "h264_qsv", "-preset", "veryfast", "-global_quality", "28"],
    ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "28"],
]
_encoder: Optional[list[str]] = None  # the first one that worked


# installer/iphone-manager.iss extracts the ffmpeg build to <app>/ffmpeg/<build name>/bin
BUNDLED = Path(__file__).resolve().parent.parent / "ffmpeg"


EXTRA_DIRS = ["/opt/homebrew/bin", "/usr/local/bin"]


def _exe(name: str) -> Optional[str]:
    for exe in sorted(BUNDLED.glob(f"*/bin/{name}.exe"), reverse=True):
        return str(exe)
    found = shutil.which(name)
    if found:
        return found
    for folder in EXTRA_DIRS:
        if (Path(folder) / name).is_file():
            return str(Path(folder) / name)
    return None


def available() -> bool:
    return _exe("ffmpeg") is not None and _exe("ffprobe") is not None


def _source(base: str, udid: str, path: str) -> str:
    return f"{base}/api/devices/{udid}/file?path={quote(path, safe='')}"


async def probe(base: str, udid: str, path: str) -> dict:
    return await probe_url(_source(base, udid, path))


async def probe_url(url: str) -> dict:
    proc = await asyncio.create_subprocess_exec(
        _exe("ffprobe") or "ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
        "stream=codec_name,width,height:format=duration", "-of", "json", url,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    out, _err = await asyncio.wait_for(proc.communicate(), 30)
    data = json.loads(out or b"{}")
    stream = (data.get("streams") or [{}])[0]
    codec = stream.get("codec_name")
    return {"codec": codec, "width": stream.get("width"), "height": stream.get("height"),
            "duration": float(data.get("format", {}).get("duration") or 0),
            "direct": codec in BROWSER_CODECS}


def stream(base: str, udid: str, path: str, start: float = 0,
           gone: Optional[Callable[[], Awaitable[bool]]] = None) -> AsyncIterator[bytes]:
    return stream_url(_source(base, udid, path), start, gone)


async def stream_url(url: str, start: float = 0,
                     gone: Optional[Callable[[], Awaitable[bool]]] = None) -> AsyncIterator[bytes]:
    """H.264/AAC fragmented MP4 from `start` seconds. ffmpeg is killed as soon as `gone()` says the
    browser left: a <video> removed from the page can keep its request open, and ffmpeg would
    otherwise convert the rest of a 40-minute 4K video for nobody."""
    global _encoder
    for encoder in ([_encoder] if _encoder else ENCODERS):
        args = [_exe("ffmpeg") or "ffmpeg", "-hide_banner", "-v", "error"]
        if start > 0:
            args += ["-ss", f"{start:.2f}"]
        args += ["-i", url, "-map", "0:v:0", "-map", "0:a:0?",
                 "-vf", "fps=30,scale=-2:'min(720,ih)':flags=fast_bilinear,format=nv12", *encoder,
                 "-c:a", "aac", "-b:a", "128k", "-f", "mp4", "-movflags", "frag_keyframe+empty_moov+default_base_moof",
                 "pipe:1"]
        proc = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE,
                                                    stderr=asyncio.subprocess.PIPE)
        assert proc.stdout is not None and proc.stderr is not None
        sent = False
        try:
            while chunk := await proc.stdout.read(256 * 1024):
                if gone is not None and await gone():
                    log.info("video preview: browser left, stopping ffmpeg")
                    return
                sent = True
                yield chunk
            if sent:
                _encoder = encoder
                return
            err = (await proc.stderr.read()).decode("utf-8", "replace").strip()
            log.warning("video preview with %s failed: %s", encoder[1], err[-300:])
        finally:
            if proc.returncode is None:
                proc.kill()
                await proc.wait()
    raise RuntimeError(_("ffmpeg couldn't convert this video"))
