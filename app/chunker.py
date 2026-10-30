"""División del texto en fragmentos (chunks) solapados para la búsqueda semántica."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .loaders import Page


@dataclass
class Chunk:
    text: str
    page: int
    index: int


_SEPARATORS = ["\n\n", "\n", ". ", "; ", ", ", " "]


def _split_point(text: str, limit: int) -> int:
    """Busca el mejor punto de corte antes de `limit` (fin de párrafo, línea, frase...)."""
    if len(text) <= limit:
        return len(text)
    window = text[:limit]
    for sep in _SEPARATORS:
        pos = window.rfind(sep)
        if pos > limit * 0.5:
            return pos + len(sep)
    return limit


def split_text(text: str, size: int = 1000, overlap: int = 200) -> list[str]:
    text = re.sub(r"[ \t]+", " ", text).strip()
    if not text:
        return []
    overlap = max(0, min(overlap, size // 2))
    pieces: list[str] = []
    start = 0
    while start < len(text):
        end = start + _split_point(text[start:], size)
        piece = text[start:end].strip()
        if piece:
            pieces.append(piece)
        if end >= len(text):
            break
        # Retrocede `overlap` caracteres, empezando en un límite de palabra.
        next_start = max(end - overlap, start + 1)
        space = text.find(" ", next_start)
        start = space + 1 if 0 <= space < end else next_start
    return pieces


def chunk_pages(pages: list[Page], size: int = 1000, overlap: int = 200) -> list[Chunk]:
    chunks: list[Chunk] = []
    for page in pages:
        for piece in split_text(page.text, size, overlap):
            chunks.append(Chunk(text=piece, page=page.number, index=len(chunks)))
    return chunks
