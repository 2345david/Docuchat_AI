"""Lectura de documentos: convierte cada archivo en una lista de páginas de texto."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Page:
    number: int  # 1, 2, 3... (en Excel, cada hoja cuenta como una "página")
    text: str
    label: str = ""  # p. ej. "Hoja Ventas" para Excel


SUPPORTED_EXTENSIONS = {
    ".pdf", ".docx", ".xlsx", ".xlsm", ".csv", ".txt", ".md",
    ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp",
}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"}


class DocumentLoadError(Exception):
    pass


# --------------------------------------------------------------------------- OCR

def ocr_available() -> bool:
    """El OCR es opcional: requiere pytesseract y el programa Tesseract instalado."""
    try:
        import pytesseract

        pytesseract.get_tesseract_version()
        return True
    except Exception:
        return False


def _ocr_image(image) -> str:
    import pytesseract

    try:
        return pytesseract.image_to_string(image, lang="spa+eng")
    except pytesseract.TesseractError:
        # Si no está instalado el paquete de idioma español, usa el inglés.
        return pytesseract.image_to_string(image)


# ----------------------------------------------------------------------- lectores

def _load_pdf(path: Path) -> list[Page]:
    import fitz  # PyMuPDF

    pages: list[Page] = []
    use_ocr = None
    with fitz.open(path) as doc:
        for i, page in enumerate(doc, start=1):
            text = page.get_text("text") or ""
            if len(text.strip()) < 20:
                # Página escaneada (es una imagen): intenta OCR si está disponible.
                if use_ocr is None:
                    use_ocr = ocr_available()
                if use_ocr:
                    from PIL import Image

                    pix = page.get_pixmap(dpi=300)
                    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
                    text = _ocr_image(img)
            pages.append(Page(i, text))
    return pages


def _load_docx(path: Path) -> list[Page]:
    import docx

    document = docx.Document(str(path))
    parts = [p.text for p in document.paragraphs if p.text.strip()]
    for table in document.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells]
            if any(cells):
                parts.append(" | ".join(cells))
    return [Page(1, "\n".join(parts))]


def _load_excel(path: Path) -> list[Page]:
    import openpyxl

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    pages: list[Page] = []
    try:
        for i, ws in enumerate(wb.worksheets, start=1):
            lines = []
            for row in ws.iter_rows(values_only=True):
                values = ["" if v is None else str(v) for v in row]
                if any(v.strip() for v in values):
                    lines.append(" | ".join(values).rstrip(" |"))
            if lines:
                pages.append(Page(i, "\n".join(lines), label=f"Hoja {ws.title}"))
    finally:
        wb.close()
    return pages


def _load_csv(path: Path) -> list[Page]:
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
        delimiter = dialect.delimiter
    except csv.Error:
        delimiter = ";" if text.count(";") > text.count(",") else ","
    rows = csv.reader(text.splitlines(), delimiter=delimiter)
    lines = [" | ".join(r) for r in rows if any(c.strip() for c in r)]
    return [Page(1, "\n".join(lines))]


def _load_text(path: Path) -> list[Page]:
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return [Page(1, raw.decode(encoding))]
        except UnicodeDecodeError:
            continue
    return [Page(1, raw.decode("utf-8", errors="ignore"))]


def _load_image(path: Path) -> list[Page]:
    if not ocr_available():
        raise DocumentLoadError(
            "Para leer imágenes hace falta el OCR (Tesseract). Consulta el README."
        )
    from PIL import Image

    with Image.open(path) as img:
        return [Page(1, _ocr_image(img))]


def load_document(path: str | Path) -> list[Page]:
    """Devuelve las páginas de texto del documento. Lanza DocumentLoadError si falla."""
    path = Path(path)
    ext = path.suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise DocumentLoadError(f"Formato no soportado: {ext or 'sin extensión'}")
    loaders = {
        ".pdf": _load_pdf,
        ".docx": _load_docx,
        ".xlsx": _load_excel,
        ".xlsm": _load_excel,
        ".csv": _load_csv,
        ".txt": _load_text,
        ".md": _load_text,
    }
    loader = loaders.get(ext, _load_image if ext in IMAGE_EXTENSIONS else None)
    try:
        pages = loader(path)
    except DocumentLoadError:
        raise
    except Exception as exc:  # archivo corrupto, protegido con contraseña, etc.
        raise DocumentLoadError(f"No se pudo leer el archivo: {exc}") from exc

    pages = [Page(p.number, _clean(p.text), p.label) for p in pages]
    pages = [p for p in pages if p.text.strip()]
    if not pages:
        hint = ""
        if ext == ".pdf" or ext in IMAGE_EXTENSIONS:
            hint = " Parece un documento escaneado: instala Tesseract para activar el OCR."
        raise DocumentLoadError("El documento no contiene texto legible." + hint)
    return pages


def _clean(text: str) -> str:
    lines = [line.rstrip() for line in text.replace("\x00", "").splitlines()]
    out: list[str] = []
    blank = 0
    for line in lines:
        if line.strip():
            blank = 0
            out.append(line)
        else:
            blank += 1
            if blank == 1:
                out.append("")
    return "\n".join(out).strip()
