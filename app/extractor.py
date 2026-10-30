"""Extracción de datos estructurados (fecha, importes, emisor, líneas...) con el LLM."""

from __future__ import annotations

import json
import re
from datetime import date

from .llm import OllamaService

MAX_CHARS = 12000  # texto máximo que se envía al modelo para extraer

DOC_TYPES = ["factura", "ticket", "recibo", "extracto_bancario", "presupuesto", "nomina", "contrato", "otro"]

_NULLABLE_STR = {"type": ["string", "null"]}
_NULLABLE_NUM = {"type": ["number", "null"]}

EXTRACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "tipo_documento": {"type": "string", "enum": DOC_TYPES},
        "fecha": _NULLABLE_STR,
        "numero_documento": _NULLABLE_STR,
        "emisor": _NULLABLE_STR,
        "receptor": _NULLABLE_STR,
        "base_imponible": _NULLABLE_NUM,
        "impuestos": _NULLABLE_NUM,
        "importe_total": _NULLABLE_NUM,
        "moneda": _NULLABLE_STR,
        "resumen": {"type": "string"},
        "lineas": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "fecha": _NULLABLE_STR,
                    "descripcion": {"type": "string"},
                    "cantidad": _NULLABLE_NUM,
                    "precio_unitario": _NULLABLE_NUM,
                    "importe": _NULLABLE_NUM,
                },
                "required": ["descripcion", "importe"],
            },
        },
    },
    "required": ["tipo_documento", "fecha", "importe_total", "resumen", "lineas"],
}

PROMPT = """Eres un sistema de extracción de datos de documentos (facturas, tickets, recibos, extractos...).
Lee el documento y devuelve SOLO un JSON con estos campos:
- tipo_documento: uno de {types}
- fecha: fecha principal del documento en formato AAAA-MM-DD (null si no hay)
- numero_documento: número de factura/ticket (null si no hay)
- emisor: quién emite el documento (empresa o persona que vende/cobra)
- receptor: a quién va dirigido (cliente/comprador)
- base_imponible, impuestos, importe_total: números con punto decimal (ej. 1234.56), sin símbolo de moneda
- moneda: código como EUR, USD...
- resumen: una frase describiendo el documento
- lineas: cada concepto, producto o movimiento con fecha (AAAA-MM-DD o null), descripcion, cantidad, precio_unitario e importe
Si un dato no aparece, usa null. No inventes datos. Como máximo 100 líneas.

Nombre del archivo: {filename}

DOCUMENTO:
{text}"""


def parse_amount(value):
    """Convierte '1.234,56 €', '1,234.56', '-12,5' o 12.5 en float. Devuelve None si no puede."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = re.sub(r"[^\d,.\-]", "", str(value))
    if not s or not re.search(r"\d", s):
        return None
    if "," in s and "." in s:
        # El separador decimal es el que aparece más a la derecha.
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        # Varias comas = separador de miles (1,234,567); una sola = decimal español (12,50).
        s = s.replace(",", "") if s.count(",") > 1 else s.replace(",", ".")
    elif s.count(".") > 1:
        s = s.replace(".", "")
    try:
        return float(s)
    except ValueError:
        return None


def parse_date(value):
    """Normaliza fechas a AAAA-MM-DD. Acepta 2024-03-05, 05/03/2024, 5-3-24..."""
    if not value or not isinstance(value, str):
        return None
    value = value.strip()
    m = re.match(r"^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", value)
    if m:
        y, mo, d = map(int, m.groups())
    else:
        m = re.match(r"^(\d{1,2})[-/.](\d{1,2})[-/.](\d{2,4})", value)
        if not m:
            return None
        d, mo, y = map(int, m.groups())  # formato español: día/mes/año
        if y < 100:
            y += 2000
    try:
        return date(y, mo, d).isoformat()
    except ValueError:
        return None


def _clean_str(value):
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def normalize(raw: dict) -> dict:
    tipo = str(raw.get("tipo_documento") or "otro").lower().strip()
    data = {
        "tipo_documento": tipo if tipo in DOC_TYPES else "otro",
        "fecha": parse_date(raw.get("fecha")),
        "numero_documento": _clean_str(raw.get("numero_documento")),
        "emisor": _clean_str(raw.get("emisor")),
        "receptor": _clean_str(raw.get("receptor")),
        "base_imponible": parse_amount(raw.get("base_imponible")),
        "impuestos": parse_amount(raw.get("impuestos")),
        "importe_total": parse_amount(raw.get("importe_total")),
        "moneda": (_clean_str(raw.get("moneda")) or "EUR").upper()[:5],
        "resumen": _clean_str(raw.get("resumen")) or "",
        "lineas": [],
    }
    for item in (raw.get("lineas") or [])[:100]:
        if not isinstance(item, dict):
            continue
        desc = _clean_str(item.get("descripcion"))
        amount = parse_amount(item.get("importe"))
        if not desc and amount is None:
            continue
        data["lineas"].append(
            {
                "fecha": parse_date(item.get("fecha")),
                "descripcion": desc or "",
                "cantidad": parse_amount(item.get("cantidad")),
                "precio_unitario": parse_amount(item.get("precio_unitario")),
                "importe": amount,
            }
        )
    return data


def _parse_json(content: str) -> dict:
    content = content.strip()
    content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content)
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        start, end = content.find("{"), content.rfind("}")
        if start >= 0 and end > start:
            return json.loads(content[start : end + 1])
        raise


def extract_structured(llm: OllamaService, filename: str, text: str) -> dict:
    if len(text) > MAX_CHARS:
        text = text[: MAX_CHARS * 2 // 3] + "\n[...]\n" + text[-MAX_CHARS // 3 :]
    prompt = PROMPT.format(types=", ".join(DOC_TYPES), filename=filename, text=text)
    messages = [{"role": "user", "content": prompt}]
    try:
        # Salida estructurada con esquema JSON (Ollama >= 0.5)
        message = llm.chat(messages, format=EXTRACTION_SCHEMA, num_ctx=16384)
    except RuntimeError:
        # Versiones antiguas de Ollama: solo modo "json"
        message = llm.chat(messages, format="json", num_ctx=16384)
    return normalize(_parse_json(message.content or "{}"))
