"""Configuración de la aplicación: rutas de datos y ajustes del usuario."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path

APP_NAME = "DocuChat"
APP_VERSION = "1.0.0"

# Carpeta donde se guardan la base de datos, el índice vectorial y los ajustes.
# Se puede cambiar con la variable de entorno DOCUCHAT_DATA.
DATA_DIR = Path(os.environ.get("DOCUCHAT_DATA", Path.home() / ".docuchat"))
DB_PATH = DATA_DIR / "docuchat.sqlite3"
VECTOR_DIR = DATA_DIR / "vectores"
SETTINGS_PATH = DATA_DIR / "ajustes.json"


@dataclass
class Settings:
    ollama_host: str = "http://localhost:11434"
    chat_model: str = "qwen2.5:7b"
    embed_model: str = "bge-m3"
    chunk_size: int = 1000
    chunk_overlap: int = 200
    top_k: int = 6
    temperature: float = 0.1
    extract_structured: bool = True
    # Nombre del usuario o su empresa: ayuda a distinguir facturas emitidas de recibidas.
    user_name: str = ""

    @classmethod
    def load(cls) -> "Settings":
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        if SETTINGS_PATH.exists():
            try:
                raw = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
                known = {f.name for f in fields(cls)}
                return cls(**{k: v for k, v in raw.items() if k in known})
            except (json.JSONDecodeError, TypeError):
                pass
        settings = cls()
        settings.save()
        return settings

    def save(self) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        SETTINGS_PATH.write_text(
            json.dumps(asdict(self), indent=2, ensure_ascii=False), encoding="utf-8"
        )
