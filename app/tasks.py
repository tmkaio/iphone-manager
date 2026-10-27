"""Background tasks that can't be garbage-collected mid-run.

The event loop only keeps a weak reference to a task: a bare ``asyncio.create_task(...)`` whose
result isn't stored can be destroyed while pending ("Task was destroyed but it is pending!").
"""

import asyncio
from typing import Any, Coroutine

_running: set[asyncio.Task] = set()


def spawn(coro: Coroutine[Any, Any, Any]) -> asyncio.Task:
    task = asyncio.create_task(coro)
    _running.add(task)
    task.add_done_callback(_running.discard)
    return task
