"""Índice vectorial local con ChromaDB."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import chromadb
from chromadb.config import Settings as ChromaSettings


@dataclass
class SearchHit:
    text: str
    doc_id: int
    filename: str
    page: int
    score: float  # 1 = idéntico, 0 = nada que ver


class VectorStore:
    def __init__(self, path: Path, embed_model: str):
        path.mkdir(parents=True, exist_ok=True)
        self.client = chromadb.PersistentClient(
            path=str(path), settings=ChromaSettings(anonymized_telemetry=False)
        )
        self.collection = self._collection_for(embed_model)

    def _collection_for(self, embed_model: str):
        # Una colección por modelo de embeddings: los vectores de modelos distintos no se mezclan.
        safe = re.sub(r"[^a-zA-Z0-9_-]", "_", embed_model).strip("_-") or "default"
        name = f"chunks_{safe}"[:60]
        return self.client.get_or_create_collection(name=name, metadata={"hnsw:space": "cosine"})

    def switch_model(self, embed_model: str) -> None:
        self.collection = self._collection_for(embed_model)

    def count(self) -> int:
        return self.collection.count()

    def indexed_doc_ids(self) -> set[int]:
        result = self.collection.get(include=["metadatas"])
        return {int(m["doc_id"]) for m in result["metadatas"] or []}

    def add_chunks(self, doc_id: int, filename: str, chunks, embeddings: list[list[float]]) -> None:
        if not chunks:
            return
        self.collection.add(
            ids=[f"{doc_id}-{c.index}" for c in chunks],
            embeddings=embeddings,
            documents=[c.text for c in chunks],
            metadatas=[
                {"doc_id": doc_id, "filename": filename, "page": c.page, "chunk": c.index}
                for c in chunks
            ],
        )

    def delete_document(self, doc_id: int) -> None:
        self.collection.delete(where={"doc_id": doc_id})

    def search(self, embedding: list[float], k: int = 6, doc_ids: list[int] | None = None) -> list[SearchHit]:
        total = self.count()
        if total == 0:
            return []
        where = None
        if doc_ids:
            where = {"doc_id": doc_ids[0]} if len(doc_ids) == 1 else {"doc_id": {"$in": doc_ids}}
        result = self.collection.query(
            query_embeddings=[embedding],
            n_results=min(k, total),
            where=where,
            include=["documents", "metadatas", "distances"],
        )
        hits: list[SearchHit] = []
        for text, meta, dist in zip(
            result["documents"][0], result["metadatas"][0], result["distances"][0]
        ):
            hits.append(
                SearchHit(
                    text=text,
                    doc_id=int(meta["doc_id"]),
                    filename=str(meta["filename"]),
                    page=int(meta.get("page", 1)),
                    score=round(1 - float(dist), 3),
                )
            )
        return hits
