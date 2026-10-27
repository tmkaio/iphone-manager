"""Read a few tables of a SQLite database that lives on the iPhone, without copying the file.

The Photos database is 1.6 GB, but the tables the album view needs are ~14 MB (most of the file is
iCloud change history, scene classification, OCR text…). AFC can read any byte range of a file, so
this walks the SQLite b-trees of just the wanted tables page by page (file format:
https://www.sqlite.org/fileformat2.html), overlaying the newest committed pages from the -wal file
like SQLite does. Read-only; nothing on the iPhone is touched.

The database is live (photolibraryd writes it), so a page can change between reads: a read that
hits an inconsistent page raises CorruptRead and the caller simply retries.
"""

import asyncio
import re
import struct
from typing import Any, AsyncIterator, Optional

from .i18n import _

WAL_HEADER = 32
WAL_FRAME_HEADER = 24
PARALLEL_READS = 16  # AFC file handles reading at the same time (24k-item library: 20 s -> 4 s)
MAX_RUN = 32  # pages per read (128 KB)
MAX_GAP = 2  # unneeded pages tolerated between two needed ones in a single read


class CorruptRead(Exception):
    pass


class RemoteSqlite:
    def __init__(self, afc: Any, path: str) -> None:
        self.afc = afc
        self.path = path
        self._db: Optional[int] = None
        self._wal: Optional[int] = None
        self._wal_pages: dict[int, int] = {}  # page number -> offset of its newest committed frame in -wal
        self._cache: dict[int, bytes] = {}
        self.page_size = 4096
        self.usable = 4096
        self.pages_read = 0
        self._schema: Optional[dict[str, tuple[int, str]]] = None
        self._handles: list[int] = []  # extra handles for parallel reads

    async def __aenter__(self) -> "RemoteSqlite":
        self._db = await self.afc.fopen(self.path, "r")
        header = await self._read(self._db, 0, 100)
        if header[:16] != b"SQLite format 3\x00":
            raise CorruptRead(_("not a SQLite database"))
        ps = struct.unpack(">H", header[16:18])[0]
        self.page_size = 65536 if ps == 1 else ps
        self.usable = self.page_size - header[20]
        await self._load_wal()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        for h in (self._db, self._wal, *self._handles):
            if h is not None:
                try:
                    await self.afc.fclose(h)
                except Exception:
                    pass

    async def _read(self, handle: int, offset: int, size: int) -> bytes:
        await self.afc.fseek(handle, offset)
        data = b""
        while len(data) < size:
            chunk = await self.afc.fread(handle, size - len(data))
            if not chunk:
                break
            data += chunk
        return data

    async def _load_wal(self) -> None:
        """Index the committed frames of the write-ahead log (newest wins)."""
        try:
            self._wal = await self.afc.fopen(self.path + "-wal", "r")
        except Exception:
            return  # no WAL: the main file is current
        st = await self.afc.stat(self.path + "-wal")
        size = int(st.get("st_size", 0))
        if size < WAL_HEADER:
            return
        data = await self._read(self._wal, 0, size)
        magic, _version, page_size, _seq, salt1, salt2 = struct.unpack(">IIIIII", data[:24])
        if magic not in (0x377F0682, 0x377F0683) or page_size != self.page_size:
            return
        frame = WAL_FRAME_HEADER + page_size
        pending: dict[int, int] = {}
        off = WAL_HEADER
        while off + frame <= len(data):
            pgno, commit, s1, s2 = struct.unpack(">IIII", data[off:off + 16])
            if (s1, s2) != (salt1, salt2):
                break  # frames from an older generation of the log
            pending[pgno] = off + WAL_FRAME_HEADER
            if commit:  # end of a transaction: its pages are now visible
                self._wal_pages.update(pending)
                pending.clear()
            off += frame

    async def page(self, n: int) -> bytes:
        if n in self._cache:
            return self._cache[n]
        if n in self._wal_pages:
            data = await self._read(self._wal, self._wal_pages[n], self.page_size)
            if len(data) != self.page_size:
                raise CorruptRead(_("page {n} incomplete", n=n))
            self.pages_read += 1
            self._cache[n] = data
            return data
        data = await self._read(self._db, (n - 1) * self.page_size, self.page_size)
        if len(data) != self.page_size:
            raise CorruptRead(_("page {n} incomplete", n=n))
        self.pages_read += 1
        self._cache[n] = data
        return data

    async def prefetch(self, pages: list[int]) -> None:
        """Fetch these pages ahead: neighbours in the file in one read, several reads in flight.

        Each AFC read is a ~5 ms round trip; a b-tree level's children are known up front, so this
        turns thousands of sequential round trips into a few batched, parallel ones."""
        need = sorted({p for p in pages if p not in self._cache and p not in self._wal_pages})
        runs: list[list[int]] = []
        for p in need:
            if runs and p - runs[-1][1] <= MAX_GAP and runs[-1][1] - runs[-1][0] < MAX_RUN:
                runs[-1][1] = p  # a small gap costs less than another round trip
            else:
                runs.append([p, p])
        while len(self._handles) < PARALLEL_READS:
            self._handles.append(await self.afc.fopen(self.path, "r"))
        queue = list(runs)

        async def worker(handle: int) -> None:
            while queue:
                first, last = queue.pop()
                data = await self._read(handle, (first - 1) * self.page_size, (last - first + 1) * self.page_size)
                for i in range(len(data) // self.page_size):
                    p = first + i
                    if p not in self._cache and p not in self._wal_pages:  # the WAL copy is the newer one
                        self._cache[p] = data[i * self.page_size:(i + 1) * self.page_size]
                self.pages_read += len(data) // self.page_size

        await asyncio.gather(*(worker(h) for h in self._handles))

    # ---------- b-tree ----------

    async def rows(self, root: int) -> AsyncIterator[tuple[int, list[Any]]]:
        """(rowid, column values) of every row of the table whose b-tree starts at root."""
        stack = [root]
        while stack:
            n = stack.pop()
            b = await self.page(n)
            h = 100 if n == 1 else 0
            kind = b[h]
            ncells = struct.unpack(">H", b[h + 3:h + 5])[0]
            if kind == 0x05:  # table interior: children in key order
                ptrs = _cell_pointers(b, h + 12, ncells)
                children = [struct.unpack(">I", b[p:p + 4])[0] for p in ptrs]
                children.append(struct.unpack(">I", b[h + 8:h + 12])[0])
                await self.prefetch(children)
                stack.extend(reversed(children))
            elif kind == 0x0D:  # table leaf
                for p in _cell_pointers(b, h + 8, ncells):
                    size, i = _varint(b, p)
                    rowid, i = _varint(b, i)
                    payload = await self._payload(b, i, size)
                    yield rowid, _record(payload)
            else:
                raise CorruptRead(_("page {n}: unexpected type {kind}", n=n, kind=f"{kind:#x}"))

    async def _payload(self, b: bytes, start: int, size: int) -> bytes:
        u = self.usable
        x = u - 35
        if size <= x:
            return b[start:start + size]
        m = ((u - 12) * 32 // 255) - 23
        k = m + (size - m) % (u - 4)
        local = k if k <= x else m
        data = bytearray(b[start:start + local])
        nxt = struct.unpack(">I", b[start + local:start + local + 4])[0]
        while nxt and len(data) < size:
            ov = await self.page(nxt)
            nxt = struct.unpack(">I", ov[:4])[0]
            data += ov[4:min(u, 4 + size - len(data))]
        return bytes(data)

    async def schema(self) -> dict[str, tuple[int, str]]:
        """table name -> (root page, CREATE TABLE sql)."""
        result = {}
        async for _rowid, (kind, name, _tbl, root, sql) in self.rows(1):
            if kind == "table" and root:
                result[name] = (root, sql or "")
        return result

    async def columns(self, name: str) -> list[str]:
        if self._schema is None:
            self._schema = await self.schema()
        return _columns(self._schema[name][1])[0]

    async def table(self, name: str, columns: list[str]) -> list[tuple]:
        """Rows of a table, restricted to these columns (an INTEGER PRIMARY KEY reads as the rowid)."""
        if self._schema is None:
            self._schema = await self.schema()
        if name not in self._schema:
            raise KeyError(name)
        root, sql = self._schema[name]
        cols, pk = _columns(sql)
        idx = [cols.index(c) for c in columns]
        out = []
        async for rowid, values in self.rows(root):
            row = []
            for c, i in zip(columns, idx):
                v = values[i] if i < len(values) else None  # columns added later read as NULL
                row.append(rowid if c == pk and v is None else v)
            out.append(tuple(row))
        return out


def _cell_pointers(b: bytes, start: int, n: int) -> list[int]:
    return [struct.unpack(">H", b[start + 2 * i:start + 2 * i + 2])[0] for i in range(n)]


def _varint(b: bytes, i: int) -> tuple[int, int]:
    v = 0
    for k in range(8):
        c = b[i + k]
        v = (v << 7) | (c & 0x7F)
        if not c & 0x80:
            return v, i + k + 1
    return (v << 8) | b[i + 8], i + 9


def _record(p: bytes) -> list[Any]:
    hsize, i = _varint(p, 0)
    types = []
    while i < hsize:
        t, i = _varint(p, i)
        types.append(t)
    pos = hsize
    values: list[Any] = []
    for t in types:
        if t == 0:
            values.append(None)
        elif 1 <= t <= 6:
            n = (1, 2, 3, 4, 6, 8)[t - 1]
            values.append(int.from_bytes(p[pos:pos + n], "big", signed=True))
            pos += n
        elif t == 7:
            values.append(struct.unpack(">d", p[pos:pos + 8])[0])
            pos += 8
        elif t in (8, 9):
            values.append(t - 8)
        elif t >= 12:
            n = (t - 12) // 2 if t % 2 == 0 else (t - 13) // 2
            raw = p[pos:pos + n]
            values.append(raw if t % 2 == 0 else raw.decode("utf-8", "replace"))
            pos += n
        else:
            raise CorruptRead(_("invalid column type {type}", type=t))
    return values


def _columns(sql: str) -> tuple[list[str], Optional[str]]:
    """Column names (in record order) and the INTEGER PRIMARY KEY column of a CREATE TABLE."""
    body = sql[sql.index("(") + 1:sql.rindex(")")]
    parts, depth, cur = [], 0, ""
    for ch in body:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append(cur)
            cur = ""
        else:
            cur += ch
    parts.append(cur)
    cols, pk = [], None
    for part in parts:
        part = part.strip()
        if re.match(r"(?i)(PRIMARY|UNIQUE|CHECK|FOREIGN|CONSTRAINT)\b", part):
            continue  # table constraint, not a column
        name = part.split()[0].strip('"`[]')
        cols.append(name)
        if re.search(r"(?i)\bINTEGER\s+PRIMARY\s+KEY\b", part):
            pk = name
    return cols, pk
