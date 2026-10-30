"""Conexión con Ollama: comprobar el servidor, descargar modelos, embeddings y chat."""

from __future__ import annotations

from typing import Callable, Iterable

import ollama


class OllamaUnavailable(Exception):
    pass


class ToolsNotSupported(Exception):
    pass


class OllamaService:
    def __init__(self, host: str, chat_model: str, embed_model: str, temperature: float = 0.1):
        self.host = host
        self.chat_model = chat_model
        self.embed_model = embed_model
        self.temperature = temperature
        self.client = ollama.Client(host=host)

    # ----------------------------------------------------------------- estado
    def installed_models(self) -> set[str]:
        try:
            response = self.client.list()
        except Exception as exc:
            raise OllamaUnavailable(
                f"No se puede conectar con Ollama en {self.host}. "
                "Comprueba que Ollama está instalado y abierto."
            ) from exc
        names = set()
        for m in response.models:
            name = getattr(m, "model", None) or getattr(m, "name", "")
            names.add(name)
        return names

    @staticmethod
    def _has(installed: set[str], model: str) -> bool:
        if model in installed:
            return True
        if ":" not in model and f"{model}:latest" in installed:
            return True
        return False

    def missing_models(self) -> list[str]:
        installed = self.installed_models()
        return [m for m in (self.chat_model, self.embed_model) if not self._has(installed, m)]

    def pull(self, model: str, on_progress: Callable[[str, float | None], None] | None = None) -> None:
        """Descarga un modelo informando del progreso (0..1, o None si es desconocido)."""
        for part in self.client.pull(model, stream=True):
            if on_progress is None:
                continue
            total = getattr(part, "total", None)
            completed = getattr(part, "completed", None)
            fraction = completed / total if total and completed is not None else None
            on_progress(getattr(part, "status", "") or "", fraction)

    # ------------------------------------------------------------- embeddings
    def embed(self, texts: Iterable[str], batch_size: int = 16) -> list[list[float]]:
        texts = list(texts)
        vectors: list[list[float]] = []
        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            try:
                response = self.client.embed(model=self.embed_model, input=batch)
            except ollama.ResponseError as exc:
                raise RuntimeError(f"Error al generar embeddings con '{self.embed_model}': {exc.error}") from exc
            except Exception as exc:
                raise OllamaUnavailable(f"Ollama no responde: {exc}") from exc
            vectors.extend([list(v) for v in response.embeddings])
        return vectors

    # ------------------------------------------------------------------- chat
    def chat(self, messages: list, tools: list | None = None, format=None, num_ctx: int = 8192):
        """Devuelve el mensaje de respuesta del modelo (con .content y .tool_calls)."""
        kwargs = {
            "model": self.chat_model,
            "messages": messages,
            "options": {"temperature": self.temperature, "num_ctx": num_ctx},
        }
        if tools:
            kwargs["tools"] = tools
        if format is not None:
            kwargs["format"] = format
        try:
            response = self.client.chat(**kwargs)
        except ollama.ResponseError as exc:
            if tools and "does not support tools" in str(exc.error).lower():
                raise ToolsNotSupported(self.chat_model) from exc
            raise RuntimeError(f"Error del modelo '{self.chat_model}': {exc.error}") from exc
        except Exception as exc:
            raise OllamaUnavailable(f"Ollama no responde: {exc}") from exc
        return response.message
