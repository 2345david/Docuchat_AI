"""Base de datos SQLite: documentos, su texto y los datos estructurados extraídos."""

from __future__ import annotations

import json
import re
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS documentos (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    archivo          TEXT NOT NULL,
    ruta             TEXT,
    extension        TEXT,
    hash             TEXT UNIQUE,
    fecha_alta       TEXT,
    num_paginas      INTEGER,
    paginas_json     TEXT,
    -- Datos extraídos por el modelo (pueden ser NULL)
    tipo_documento   TEXT,
    fecha            TEXT,      -- formato AAAA-MM-DD
    numero_documento TEXT,
    emisor           TEXT,
    receptor         TEXT,
    base_imponible   REAL,
    impuestos        REAL,
    importe_total    REAL,
    moneda           TEXT,
    resumen          TEXT,
    estado_extraccion TEXT DEFAULT 'pendiente'
);
CREATE TABLE IF NOT EXISTS lineas (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    documento_id    INTEGER NOT NULL REFERENCES documentos(id) ON DELETE CASCADE,
    fecha           TEXT,
    descripcion     TEXT,
    cantidad        REAL,
    precio_unitario REAL,
    importe         REAL
);
CREATE INDEX IF NOT EXISTS idx_lineas_doc ON lineas(documento_id);
"""

# Descripción para el modelo (herramienta de consultas SQL).
SCHEMA_FOR_LLM = """Tabla documentos(id, archivo, extension, fecha_alta, tipo_documento, fecha, numero_documento,
  emisor, receptor, base_imponible, impuestos, importe_total, moneda, resumen)
  - fecha está en formato 'AAAA-MM-DD' (usa strftime('%Y', fecha) o fecha BETWEEN ...).
  - tipo_documento: factura, ticket, recibo, extracto_bancario, presupuesto, nomina, contrato u otro.
Tabla lineas(id, documento_id, fecha, descripcion, cantidad, precio_unitario, importe)
  - documento_id referencia documentos.id. Son las líneas/conceptos/movimientos de cada documento."""

_DOC_FIELDS = (
    "tipo_documento", "fecha", "numero_documento", "emisor", "receptor",
    "base_imponible", "impuestos", "importe_total", "moneda", "resumen",
)


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # --------------------------------------------------------------- escritura
    def add_document(self, filename: str, path: str, file_hash: str, pages: list) -> int:
        pages_json = json.dumps(
            [{"number": p.number, "text": p.text, "label": p.label} for p in pages],
            ensure_ascii=False,
        )
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                """INSERT INTO documentos (archivo, ruta, extension, hash, fecha_alta, num_paginas, paginas_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    filename, path, Path(filename).suffix.lower(), file_hash,
                    datetime.now().isoformat(timespec="seconds"), len(pages), pages_json,
                ),
            )
            return int(cur.lastrowid)

    def save_extraction(self, doc_id: int, data: dict) -> None:
        values = [data.get(f) for f in _DOC_FIELDS]
        with self._lock, self._connect() as conn:
            conn.execute(
                f"""UPDATE documentos SET {', '.join(f'{f} = ?' for f in _DOC_FIELDS)},
                    estado_extraccion = 'ok' WHERE id = ?""",
                (*values, doc_id),
            )
            conn.execute("DELETE FROM lineas WHERE documento_id = ?", (doc_id,))
            conn.executemany(
                """INSERT INTO lineas (documento_id, fecha, descripcion, cantidad, precio_unitario, importe)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                [
                    (doc_id, l.get("fecha"), l.get("descripcion"), l.get("cantidad"),
                     l.get("precio_unitario"), l.get("importe"))
                    for l in data.get("lineas", [])
                ],
            )

    def set_extraction_status(self, doc_id: int, status: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("UPDATE documentos SET estado_extraccion = ? WHERE id = ?", (status, doc_id))

    def delete_document(self, doc_id: int) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM documentos WHERE id = ?", (doc_id,))

    # ---------------------------------------------------------------- lectura
    def find_by_hash(self, file_hash: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id, archivo FROM documentos WHERE hash = ?", (file_hash,)
            ).fetchone()
        return dict(row) if row else None

    def list_documents(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT id, archivo, extension, fecha_alta, num_paginas, tipo_documento, fecha,
                          numero_documento, emisor, receptor, importe_total, moneda, resumen,
                          estado_extraccion
                   FROM documentos ORDER BY archivo COLLATE NOCASE"""
            ).fetchall()
        return [dict(r) for r in rows]

    def get_document(self, doc_id: int) -> dict | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM documentos WHERE id = ?", (doc_id,)).fetchone()
            if not row:
                return None
            doc = dict(row)
            doc["lineas"] = [
                dict(r)
                for r in conn.execute(
                    "SELECT fecha, descripcion, cantidad, precio_unitario, importe FROM lineas "
                    "WHERE documento_id = ? ORDER BY id",
                    (doc_id,),
                ).fetchall()
            ]
        return doc

    def get_pages(self, doc_id: int) -> list[dict]:
        with self._connect() as conn:
            row = conn.execute("SELECT paginas_json FROM documentos WHERE id = ?", (doc_id,)).fetchone()
        return json.loads(row["paginas_json"]) if row and row["paginas_json"] else []

    def get_text(self, doc_id: int) -> str:
        parts = []
        for p in self.get_pages(doc_id):
            header = p.get("label") or f"Página {p['number']}"
            parts.append(f"--- {header} ---\n{p['text']}")
        return "\n\n".join(parts)

    # -------------------------------------------------------- consultas del LLM
    def run_readonly_query(self, sql: str, max_rows: int = 200) -> tuple[list[str], list[tuple]]:
        """Ejecuta una consulta SELECT en modo solo lectura (el modelo no puede modificar nada)."""
        sql = sql.strip().rstrip(";").strip()
        sql = re.sub(r"^```(?:sql)?|```$", "", sql, flags=re.IGNORECASE).strip()
        if ";" in sql:
            raise ValueError("Solo se permite una consulta a la vez.")
        if not re.match(r"^(select|with)\b", sql, re.IGNORECASE):
            raise ValueError("Solo se permiten consultas SELECT.")
        if re.search(r"\bpaginas_json\b", sql, re.IGNORECASE):
            raise ValueError("La columna paginas_json no se puede consultar; usa leer_documento.")
        uri = f"{self.path.resolve().as_uri()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=30)
        try:
            cur = conn.execute(sql)
            columns = [d[0] for d in cur.description or []]
            rows = cur.fetchmany(max_rows)
        finally:
            conn.close()
        return columns, rows
