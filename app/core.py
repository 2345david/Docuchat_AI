"""Une todas las piezas (base de datos, índice, Ollama, ingesta y asistente)."""

from __future__ import annotations

from .agent import Assistant
from .config import DB_PATH, VECTOR_DIR, Settings
from .database import Database
from .ingest import Ingestor
from .llm import OllamaService
from .vector_store import VectorStore


class Core:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.db = Database(DB_PATH)
        self.store = VectorStore(VECTOR_DIR, settings.embed_model)
        self.assistant: Assistant | None = None
        self.rebuild()

    def rebuild(self) -> None:
        """Recrea los servicios tras cambiar los ajustes (conserva la conversación)."""
        s = self.settings
        history = self.assistant.history if self.assistant else []
        self.llm = OllamaService(s.ollama_host, s.chat_model, s.embed_model, s.temperature)
        self.store.switch_model(s.embed_model)
        self.ingestor = Ingestor(s, self.db, self.store, self.llm)
        self.assistant = Assistant(s, self.db, self.store, self.llm)
        self.assistant.history = history

    def documents_missing_from_index(self) -> list[dict]:
        """Documentos sin vectores para el modelo de embeddings actual (p. ej. tras cambiarlo)."""
        indexed = self.store.indexed_doc_ids()
        return [d for d in self.db.list_documents() if d["id"] not in indexed]
