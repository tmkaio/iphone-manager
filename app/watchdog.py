"""Logs where every thread is when the event loop stops answering.

A blocked loop leaves the whole app unresponsive with no error anywhere; this pings the loop from a
thread and, after STALL_SECONDS without an answer, dumps all thread stacks to the log (once per stall).
"""

import asyncio
import faulthandler
import logging
import sys
import threading
import time

log = logging.getLogger(__name__)

STALL_SECONDS = 20
PING_EVERY = 5


def start(loop: asyncio.AbstractEventLoop) -> None:
    last = {"t": time.monotonic()}

    def pong() -> None:
        last["t"] = time.monotonic()

    def run() -> None:
        reported = False
        while True:
            time.sleep(PING_EVERY)
            try:
                loop.call_soon_threadsafe(pong)
            except RuntimeError:  # loop closed
                return
            stalled = time.monotonic() - last["t"]
            if stalled > STALL_SECONDS and not reported:
                log.error("event loop blocked for %.0f s; thread stacks follow", stalled)
                faulthandler.dump_traceback(file=sys.stderr, all_threads=True)
                reported = True
            elif stalled <= STALL_SECONDS:
                reported = False

    threading.Thread(target=run, name="loop-watchdog", daemon=True).start()
