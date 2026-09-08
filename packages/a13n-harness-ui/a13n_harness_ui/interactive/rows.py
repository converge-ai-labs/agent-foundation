"""Ephemeral paged row cache: scrolling does not discard semantic content."""

from __future__ import annotations

import json
import tempfile
from collections import OrderedDict
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import overload

from prompt_toolkit.formatted_text import StyleAndTextTuples


class RowStore(Sequence[StyleAndTextTuples]):
    """Disk-backed render cache, not durable conversation history.

    Pages are decoded only when the viewport asks for them. The temporary file is
    private, removed with the transcript, and never used to resume an App.
    Handles are open only during a write or page read, not for each retained block.
    """

    def __init__(self, rows: Iterator[StyleAndTextTuples], *, directory: str, page_size: int = 64) -> None:
        self.offsets: list[int] = []
        self._length = 0
        self.page_size = page_size
        self.closed = False
        self.pages: OrderedDict[int, list[StyleAndTextTuples]] = OrderedDict()
        stream = tempfile.NamedTemporaryFile(mode="w+b", dir=directory, delete=False)
        self.path = Path(stream.name)
        try:
            with stream:

                def write_page(page: list[StyleAndTextTuples]) -> None:
                    self.offsets.append(stream.tell())
                    stream.write(json.dumps(page, ensure_ascii=False).encode() + b"\n")

                page: list[StyleAndTextTuples] = []
                for row in rows:
                    page.append(row)
                    self._length += 1
                    if len(page) == page_size:
                        write_page(page)
                        page = []
                if page:
                    write_page(page)
        except BaseException:
            self.close()
            raise

    def __len__(self) -> int:
        return self._length

    @overload
    def __getitem__(self, index: int) -> StyleAndTextTuples: ...

    @overload
    def __getitem__(self, index: slice) -> list[StyleAndTextTuples]: ...

    def __getitem__(self, index: int | slice) -> StyleAndTextTuples | list[StyleAndTextTuples]:
        if isinstance(index, slice):
            return [self[i] for i in range(*index.indices(self._length))]
        if index < 0:
            index += self._length
        if not 0 <= index < self._length:
            raise IndexError(index)
        number, offset = divmod(index, self.page_size)
        page = self.pages.get(number)
        if page is None:
            with self.path.open("rb") as stream:
                stream.seek(self.offsets[number])
                decoded: list[StyleAndTextTuples] = [
                    [(style, text) for style, text in row] for row in json.loads(stream.readline())
                ]
            page = decoded
            self.pages[number] = page
        self.pages.move_to_end(number)
        while len(self.pages) > 2:
            self.pages.popitem(last=False)
        return page[offset]

    def close(self) -> None:
        self.pages.clear()
        self.path.unlink(missing_ok=True)
        self.closed = True
