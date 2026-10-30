"""Proceso de añadir documentos: leer → trocear → embeddings → guardar → extraer datos."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Callable

from .chunker import chunk_pages
from .config import Settings
from .database import Database
from .extractor import extract_structured
from .llm import OllamaService
from .loaders import Page, load_document
from .vector_store import VectorStore

Progress = Callable[[str], None]


class DuplicateDocument(Exception):
    pass


def file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _chunk_texts(filename: str, chunks) -> list[str]:
    # Se añade el nombre del archivo al texto que se vectoriza para que preguntas como
    # "en la factura de Endesa..." encuentren el documento aunque el texto no lo diga.
    return [f"[{filename} · pág. {c.page}]\n{c.text}" for c in chunks]


class Ingestor:
    def __init__(self, settings: Settings, db: Database, store: VectorStore, llm: OllamaService):
        self.settings = settings
        self.db = db
        self.store = store
        self.llm = llm

    def ingest(self, path: str | Path, progress: Progress = lambda _msg: None) -> int:
        path = Path(path)
        name = path.name

        digest = file_hash(path)
        existing = self.db.find_by_hash(digest)
        if existing:
            raise DuplicateDocument(f"«{name}» ya estaba añadido como «{existing['archivo']}».")

        progress(f"Leyendo {name}…")
        pages = load_document(path)

        chunks = chunk_pages(pages, self.settings.chunk_size, self.settings.chunk_overlap)
        progress(f"Analizando {name} ({len(chunks)} fragmentos)…")
        embeddings = self.llm.embed(_chunk_texts(name, chunks))

        doc_id = self.db.add_document(name, str(path), digest, pages)
        try:
            self.store.add_chunks(doc_id, name, chunks, embeddings)
        except Exception:
            self.db.delete_document(doc_id)
            raise

        if self.settings.extract_structured:
            self.extract(doc_id, name, "\n\n".join(p.text for p in pages), progress)
        else:
            self.db.set_extraction_status(doc_id, "desactivada")
        return doc_id

    def extract(self, doc_id: int, name: str, text: str, progress: Progress = lambda _m: None) -> bool:
        progress(f"Extrayendo fechas e importes de {name}…")
        try:
            data = extract_structured(self.llm, name, text)
            self.db.save_extraction(doc_id, data)
            return True
        except Exception:
            # La extracción es un extra: si falla, el documento sigue disponible para el chat.
            self.db.set_extraction_status(doc_id, "error")
            return False

    def reindex_document(self, doc_id: int, name: str) -> None:
        """Vuelve a vectorizar un documento a partir del texto guardado (no necesita el archivo)."""
        pages = [Page(p["number"], p["text"], p.get("label", "")) for p in self.db.get_pages(doc_id)]
        chunks = chunk_pages(pages, self.settings.chunk_size, self.settings.chunk_overlap)
        embeddings = self.llm.embed(_chunk_texts(name, chunks))
        self.store.delete_document(doc_id)
        self.store.add_chunks(doc_id, name, chunks, embeddings)

    def remove(self, doc_id: int) -> None:
        self.store.delete_document(doc_id)
        self.db.delete_document(doc_id)
