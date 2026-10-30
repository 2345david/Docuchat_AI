"""Pruebas rápidas que no necesitan Ollama.  Ejecutar:  python -m pytest tests"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.chunker import split_text  # noqa: E402
from app.database import Database  # noqa: E402
from app.extractor import normalize, parse_amount, parse_date  # noqa: E402
from app.loaders import Page, load_document  # noqa: E402


@pytest.mark.parametrize(
    "value, expected",
    [("1.234,56 €", 1234.56), ("1,234.56", 1234.56), ("99,59", 99.59), ("-12,5", -12.5),
     ("1.234.567", 1234567), (42, 42.0), ("sin importe", None), (None, None)],
)
def test_parse_amount(value, expected):
    assert parse_amount(value) == expected


def test_parse_date():
    assert parse_date("05/03/2024") == "2024-03-05"
    assert parse_date("2024-3-5") == "2024-03-05"
    assert parse_date("5-3-24") == "2024-03-05"
    assert parse_date("31/02/2024") is None


def test_normalize_extraction():
    data = normalize({"tipo_documento": "Factura", "fecha": "01/02/2024", "importe_total": "1.000,00",
                      "lineas": [{"descripcion": "Servicio", "importe": "1.000,00"}, {}]})
    assert data["tipo_documento"] == "factura"
    assert data["importe_total"] == 1000.0
    assert len(data["lineas"]) == 1


def test_split_text_respects_size():
    text = " ".join(f"Frase {i} del documento." for i in range(500))
    chunks = split_text(text, size=500, overlap=100)
    assert len(chunks) > 5
    assert all(len(c) <= 500 for c in chunks)


def test_load_txt_and_csv(tmp_path):
    (tmp_path / "a.txt").write_text("Hola mundo", encoding="utf-8")
    (tmp_path / "b.csv").write_text("producto;precio\nRatón;19,90\n", encoding="utf-8")
    assert load_document(tmp_path / "a.txt")[0].text == "Hola mundo"
    assert "Ratón | 19,90" in load_document(tmp_path / "b.csv")[0].text


def test_sql_is_read_only(tmp_path):
    db = Database(tmp_path / "test.sqlite3")
    doc_id = db.add_document("factura.pdf", "/x/factura.pdf", "abc", [Page(1, "texto")])
    db.save_extraction(doc_id, normalize({"tipo_documento": "factura", "importe_total": 10}))
    cols, rows = db.run_readonly_query("SELECT archivo, importe_total FROM documentos")
    assert rows == [("factura.pdf", 10.0)]
    for bad in ("DELETE FROM documentos", "SELECT 1; DROP TABLE documentos", "UPDATE documentos SET archivo='x'"):
        with pytest.raises(Exception):
            db.run_readonly_query(bad)
    assert len(db.list_documents()) == 1
