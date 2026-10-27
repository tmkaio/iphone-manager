"""Server messages in the user's language.

Messages are written in English in the code: `_("Not found: {name}", name=x)`. Other languages are
dictionaries from that English text to the translation (lang_pt.py), so a missing entry stays in
English. The language comes from the `lang` cookie the UI sets; a middleware puts it in a context
variable, which asyncio tasks and asyncio.to_thread inherit. Plain threads don't: start them with
`run_in_lang()` so messages they produce later are still translated.
"""

import contextvars
from typing import Any, Callable

from .lang_pt import PT

LANGUAGES = {"pt": PT}
current = contextvars.ContextVar("lang", default="en")


def _(text: str, **values: Any) -> str:
    s = LANGUAGES.get(current.get(), {}).get(text, text)
    return s.format(**values) if values else s


def run_in_lang(fn: Callable[..., Any]) -> Callable[..., Any]:
    """fn wrapped to run in the caller's language (for threading.Thread targets)."""
    ctx = contextvars.copy_context()
    return lambda *a, **kw: ctx.run(fn, *a, **kw)
