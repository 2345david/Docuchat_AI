"""El asistente: un agente que decide qué herramienta usar para responder.

Herramientas:
  - buscar_en_documentos: búsqueda semántica (RAG), opcionalmente dentro de un archivo concreto.
  - consultar_datos: consultas SQL sobre los datos extraídos (sumas, totales, fechas...).
  - leer_documento: devuelve el texto completo de un documento.
  - listar_documentos: catálogo de documentos con sus datos principales.
"""

from __future__ import annotations

import difflib
import json
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from .config import Settings
from .database import SCHEMA_FOR_LLM, Database
from .llm import OllamaService, ToolsNotSupported
from .vector_store import VectorStore

MAX_TOOL_ROUNDS = 6
MAX_DOC_CHARS = 9000
CATALOG_LIMIT = 60


@dataclass
class Source:
    filename: str
    page: int | None = None

    def label(self) -> str:
        return f"{self.filename} (pág. {self.page})" if self.page else self.filename


@dataclass
class Answer:
    text: str
    sources: list[Source] = field(default_factory=list)
    tools_used: list[str] = field(default_factory=list)


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "buscar_en_documentos",
            "description": (
                "Busca fragmentos relevantes en el texto de los documentos del usuario. "
                "Úsala para preguntas sobre el contenido. Si la pregunta menciona un archivo "
                "concreto, pasa su nombre en 'nombre_archivo' para buscar solo en él."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "consulta": {"type": "string", "description": "Qué buscar, con palabras clave."},
                    "nombre_archivo": {
                        "type": "string",
                        "description": "Opcional. Nombre (o parte del nombre) del archivo donde buscar.",
                    },
                },
                "required": ["consulta"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "consultar_datos",
            "description": (
                "Ejecuta una consulta SQL SELECT (SQLite) sobre los datos extraídos de los documentos. "
                "Úsala para importes, sumas, totales, medias, fechas, contar documentos o filtrar por "
                "emisor/periodo. Esquema:\n" + SCHEMA_FOR_LLM
            ),
            "parameters": {
                "type": "object",
                "properties": {"sql": {"type": "string", "description": "Consulta SELECT de SQLite."}},
                "required": ["sql"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "leer_documento",
            "description": (
                "Devuelve el texto completo de un documento. Úsala cuando la pregunta trata de un "
                "archivo concreto y necesitas leerlo entero (p. ej. resumirlo o buscar un dato exacto)."
            ),
            "parameters": {
                "type": "object",
                "properties": {"nombre_archivo": {"type": "string"}},
                "required": ["nombre_archivo"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "listar_documentos",
            "description": "Lista todos los documentos con tipo, fecha, emisor e importe total.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def _fmt_money(value, currency) -> str:
    if value is None:
        return "?"
    s = f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{s} {currency or ''}".strip()


class Assistant:
    def __init__(self, settings: Settings, db: Database, store: VectorStore, llm: OllamaService):
        self.settings = settings
        self.db = db
        self.store = store
        self.llm = llm
        self.history: list[dict] = []  # solo preguntas y respuestas finales
        self._sources: list[Source] = []
        self._tools_used: list[str] = []

    def reset(self) -> None:
        self.history.clear()

    # ============================================================ resolución
    def resolve_documents(self, name: str | None) -> list[dict]:
        """Encuentra documentos por nombre aproximado ('factura marzo' → 'Factura_Marzo_2024.pdf')."""
        if not name or not str(name).strip():
            return []
        docs = self.db.list_documents()
        q = _norm(Path(str(name)).stem if "." in str(name)[-6:] else str(name))
        if not q:
            return []

        def stem(d):
            return _norm(Path(d["archivo"]).stem)

        exact = [d for d in docs if stem(d) == q or _norm(d["archivo"]) == q]
        if exact:
            return exact
        contains = [d for d in docs if q in stem(d) or (len(stem(d)) > 3 and stem(d) in q)]
        if contains:
            return contains
        tokens = q.split()
        by_tokens = [d for d in docs if tokens and all(t in stem(d) for t in tokens)]
        if by_tokens:
            return by_tokens
        stems = {stem(d): d for d in docs}
        close = difflib.get_close_matches(q, list(stems), n=3, cutoff=0.55)
        if close:
            return [stems[c] for c in close]
        # Último recurso: el nombre puede ser el emisor o el número ("la factura de Endesa").
        meta = [
            d for d in docs
            if any(q in _norm(d.get(f) or "") for f in ("emisor", "receptor", "numero_documento"))
        ]
        return meta

    def _mentioned_documents(self, question: str) -> list[dict]:
        qn = f" {_norm(question)} "
        found = []
        for d in self.db.list_documents():
            s = _norm(Path(d["archivo"]).stem)
            if s and len(s) > 2 and f" {s} " in qn:
                found.append(d)
        return found

    # ============================================================ herramientas
    def _tool_buscar(self, consulta: str, nombre_archivo: str | None = None) -> str:
        doc_ids = None
        note = ""
        if nombre_archivo:
            docs = self.resolve_documents(nombre_archivo)
            if not docs:
                return (
                    f"No hay ningún documento que coincida con «{nombre_archivo}». "
                    "Usa listar_documentos para ver los nombres disponibles."
                )
            doc_ids = [d["id"] for d in docs]
            note = "Buscando en: " + ", ".join(d["archivo"] for d in docs) + "\n\n"
        vector = self.llm.embed([consulta])[0]
        hits = self.store.search(vector, k=self.settings.top_k, doc_ids=doc_ids)
        if not hits:
            return note + "No se encontraron fragmentos relevantes."
        lines = []
        for i, h in enumerate(hits, start=1):
            self._sources.append(Source(h.filename, h.page))
            lines.append(f"[{i}] Archivo: {h.filename} · pág. {h.page}\n{h.text}")
        return note + "\n\n".join(lines)

    def _tool_sql(self, sql: str) -> str:
        try:
            columns, rows = self.db.run_readonly_query(sql)
        except Exception as exc:
            return f"Error en la consulta: {exc}. Revisa el esquema y vuelve a intentarlo."
        if not rows:
            return "La consulta no devolvió resultados."
        if "archivo" in columns:
            idx = columns.index("archivo")
            for r in rows:
                if r[idx]:
                    self._sources.append(Source(str(r[idx])))
        out = [" | ".join(columns)]
        out += [" | ".join("" if v is None else str(v) for v in r) for r in rows]
        if len(rows) >= 200:
            out.append("(resultados truncados a 200 filas)")
        return "\n".join(out)

    def _tool_leer(self, nombre_archivo: str) -> str:
        docs = self.resolve_documents(nombre_archivo)
        if not docs:
            return f"No hay ningún documento que coincida con «{nombre_archivo}»."
        if len(docs) > 1:
            names = ", ".join(d["archivo"] for d in docs[:8])
            return f"Hay varios documentos que coinciden: {names}. Indica cuál."
        doc = docs[0]
        self._sources.append(Source(doc["archivo"]))
        text = self.db.get_text(doc["id"])
        if len(text) > MAX_DOC_CHARS:
            text = text[:MAX_DOC_CHARS] + (
                "\n[... documento truncado; usa buscar_en_documentos para el resto ...]"
            )
        return f"Contenido de {doc['archivo']}:\n{text}"

    def _catalog(self, limit: int | None = None) -> str:
        docs = self.db.list_documents()
        if not docs:
            return "(no hay documentos)"
        lines = []
        for d in docs[:limit] if limit else docs:
            parts = [d["archivo"]]
            if d.get("tipo_documento"):
                parts.append(d["tipo_documento"])
            if d.get("fecha"):
                parts.append(d["fecha"])
            if d.get("emisor"):
                parts.append(f"emisor: {d['emisor']}")
            if d.get("importe_total") is not None:
                parts.append(f"total: {_fmt_money(d['importe_total'], d.get('moneda'))}")
            lines.append("- " + " · ".join(parts))
        if limit and len(docs) > limit:
            lines.append(f"(... y {len(docs) - limit} más; usa listar_documentos)")
        return "\n".join(lines)

    def _run_tool(self, name: str, args: dict) -> str:
        self._tools_used.append(name)
        args = args or {}
        try:
            if name == "buscar_en_documentos":
                return self._tool_buscar(str(args.get("consulta") or ""), args.get("nombre_archivo") or None)
            if name == "consultar_datos":
                return self._tool_sql(str(args.get("sql") or ""))
            if name == "leer_documento":
                return self._tool_leer(str(args.get("nombre_archivo") or ""))
            if name == "listar_documentos":
                return self._catalog()
        except Exception as exc:
            return f"La herramienta falló: {exc}"
        return f"Herramienta desconocida: {name}"

    # ================================================================ prompt
    def _system_prompt(self) -> str:
        who = ""
        if self.settings.user_name.strip():
            who = (
                f"\nEl usuario es «{self.settings.user_name.strip()}». Las facturas cuyo emisor es el "
                "usuario son facturas EMITIDAS (ingresos, lo que ha facturado); si el usuario es el "
                "receptor, son gastos o compras.\n"
            )
        return f"""Eres DocuChat, un asistente que responde preguntas sobre los documentos personales del usuario.
Hoy es {date.today().isoformat()}.{who}
Documentos disponibles:
{self._catalog(CATALOG_LIMIT)}

Cómo trabajar:
- Usa SIEMPRE las herramientas para obtener la información; no respondas de memoria.
- Para importes, totales, sumas, fechas o comparaciones usa consultar_datos (SQL).
- Para preguntas sobre el contenido usa buscar_en_documentos; si mencionan un archivo, filtra por él.
- Si los datos de consultar_datos parecen incompletos, compruébalo con buscar_en_documentos o leer_documento.
- Responde en español, de forma breve y directa, indicando de qué archivo sale cada dato.
- Escribe los importes con formato español (1.234,56 €) y las fechas como 5 de marzo de 2024.
- Si la información no está en los documentos, dilo claramente. Nunca inventes datos."""

    # ============================================================== preguntar
    def ask(self, question: str) -> Answer:
        self._sources = []
        self._tools_used = []
        if not self.db.list_documents():
            return Answer("Todavía no has añadido ningún documento. Arrastra tus archivos al panel de la izquierda.")
        try:
            text = self._ask_with_tools(question)
        except ToolsNotSupported:
            text = self._ask_simple_rag(question)
        self.history += [
            {"role": "user", "content": question},
            {"role": "assistant", "content": text},
        ]
        self.history = self.history[-12:]
        return Answer(text, self._unique_sources(), list(dict.fromkeys(self._tools_used)))

    def _ask_with_tools(self, question: str) -> str:
        messages: list = [{"role": "system", "content": self._system_prompt()}]
        messages += self.history
        messages.append({"role": "user", "content": question})

        for _ in range(MAX_TOOL_ROUNDS):
            message = self.llm.chat(messages, tools=TOOLS)
            calls = self._extract_tool_calls(message)
            if not calls:
                return (message.content or "").strip() or "No he podido generar una respuesta."
            messages.append(
                {
                    "role": "assistant",
                    "content": message.content or "",
                    "tool_calls": [{"function": {"name": n, "arguments": a}} for n, a in calls],
                }
            )
            for name, args in calls:
                result = self._run_tool(name, args)
                messages.append({"role": "tool", "content": result, "tool_name": name})

        # Demasiadas rondas: pide una respuesta final con lo obtenido.
        messages.append(
            {"role": "user", "content": "Responde ya a la pregunta original con la información obtenida."}
        )
        return (self.llm.chat(messages).content or "").strip()

    @staticmethod
    def _extract_tool_calls(message) -> list[tuple[str, dict]]:
        calls = []
        for tc in getattr(message, "tool_calls", None) or []:
            args = tc.function.arguments
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {}
            calls.append((tc.function.name, dict(args or {})))
        if calls:
            return calls
        # Algunos modelos pequeños escriben la llamada como texto JSON en vez de usar tool_calls.
        content = (message.content or "").strip()
        content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content)
        if content.startswith("{") and '"name"' in content:
            try:
                data = json.loads(content)
                names = {t["function"]["name"] for t in TOOLS}
                if data.get("name") in names:
                    return [(data["name"], data.get("arguments") or data.get("parameters") or {})]
            except (json.JSONDecodeError, AttributeError):
                pass
        return []

    def _ask_simple_rag(self, question: str) -> str:
        """Plan B para modelos sin soporte de herramientas: RAG clásico."""
        mentioned = self._mentioned_documents(question)
        doc_ids = [d["id"] for d in mentioned] or None
        context = self._tool_buscar(question) if not doc_ids else ""
        if doc_ids:
            vector = self.llm.embed([question])[0]
            hits = self.store.search(vector, k=self.settings.top_k, doc_ids=doc_ids)
            for h in hits:
                self._sources.append(Source(h.filename, h.page))
            context = "\n\n".join(f"Archivo: {h.filename} · pág. {h.page}\n{h.text}" for h in hits)
        prompt = (
            f"{self._system_prompt()}\n\nFragmentos encontrados:\n{context}\n\n"
            "Responde usando solo esta información."
        )
        messages = [{"role": "system", "content": prompt}, *self.history, {"role": "user", "content": question}]
        return (self.llm.chat(messages).content or "").strip()

    def _unique_sources(self) -> list[Source]:
        seen, out = set(), []
        for s in self._sources:
            key = (s.filename, s.page)
            if key not in seen:
                seen.add(key)
                out.append(s)
        # Si un archivo aparece con página y sin ella, quédate con las entradas con página.
        with_page = {s.filename for s in out if s.page}
        return [s for s in out if s.page or s.filename not in with_page][:8]
