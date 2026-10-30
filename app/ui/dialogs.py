"""Ventanas secundarias: ajustes, descarga de modelos y detalle de un documento."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QThreadPool, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QMessageBox, QProgressBar, QPushButton, QSpinBox,
    QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from ..config import Settings
from ..llm import OllamaService
from .workers import Task

SUGGESTED_CHAT_MODELS = ["qwen2.5:7b", "qwen2.5:3b", "llama3.1:8b", "mistral-nemo", "qwen2.5:14b"]
SUGGESTED_EMBED_MODELS = ["bge-m3", "nomic-embed-text", "mxbai-embed-large"]


def _money(value, currency=None) -> str:
    if value is None:
        return ""
    s = f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{s} {currency or ''}".strip()


# ============================================================== ajustes
class SettingsDialog(QDialog):
    def __init__(self, settings: Settings, installed_models: set[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Ajustes")
        self.setMinimumWidth(480)
        self.settings = settings

        installed = sorted(installed_models)
        chat_options = list(dict.fromkeys(installed + SUGGESTED_CHAT_MODELS))
        embed_options = list(dict.fromkeys(SUGGESTED_EMBED_MODELS + installed))

        self.host = QLineEdit(settings.ollama_host)
        self.chat_model = self._combo(chat_options, settings.chat_model)
        self.embed_model = self._combo(embed_options, settings.embed_model)
        self.user_name = QLineEdit(settings.user_name)
        self.user_name.setPlaceholderText("Tu nombre o el de tu empresa (opcional)")
        self.top_k = QSpinBox()
        self.top_k.setRange(2, 20)
        self.top_k.setValue(settings.top_k)
        self.temperature = QDoubleSpinBox()
        self.temperature.setRange(0.0, 1.0)
        self.temperature.setSingleStep(0.1)
        self.temperature.setValue(settings.temperature)
        self.extract = QCheckBox("Extraer fechas, importes y conceptos al añadir documentos")
        self.extract.setChecked(settings.extract_structured)

        form = QFormLayout()
        form.addRow("Servidor de Ollama:", self.host)
        form.addRow("Modelo de chat:", self.chat_model)
        form.addRow("Modelo de embeddings:", self.embed_model)
        form.addRow("Tu nombre / empresa:", self.user_name)
        form.addRow("Fragmentos por búsqueda:", self.top_k)
        form.addRow("Temperatura:", self.temperature)
        form.addRow("", self.extract)

        hint = QLabel(
            "Tu nombre ayuda a distinguir lo que has facturado (eres el emisor) de lo que has "
            "pagado (eres el receptor). Si cambias el modelo de embeddings, los documentos se "
            "volverán a indexar."
        )
        hint.setObjectName("Muted")
        hint.setWordWrap(True)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("Guardar")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Cancelar")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(hint)
        layout.addWidget(buttons)

    @staticmethod
    def _combo(options: list[str], current: str) -> QComboBox:
        combo = QComboBox()
        combo.setEditable(True)
        combo.addItems(options)
        combo.setCurrentText(current)
        return combo

    def apply(self) -> None:
        s = self.settings
        s.ollama_host = self.host.text().strip() or "http://localhost:11434"
        s.chat_model = self.chat_model.currentText().strip() or s.chat_model
        s.embed_model = self.embed_model.currentText().strip() or s.embed_model
        s.user_name = self.user_name.text().strip()
        s.top_k = self.top_k.value()
        s.temperature = round(self.temperature.value(), 2)
        s.extract_structured = self.extract.isChecked()
        s.save()


# ==================================================== descarga de modelos
class ModelDownloadDialog(QDialog):
    """Descarga con Ollama los modelos que faltan, mostrando el progreso."""

    def __init__(self, llm: OllamaService, models: list[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Descargar modelos")
        self.setMinimumWidth(460)
        self.llm = llm
        self.models = list(models)
        self._task = None

        intro = QLabel(
            "Para funcionar, DocuChat necesita estos modelos de Ollama:\n\n"
            + "\n".join(f"   •  {m}" for m in self.models)
            + "\n\nSe descargan una sola vez (pueden ocupar varios GB) y después todo funciona sin internet."
        )
        intro.setWordWrap(True)
        self.status = QLabel("")
        self.status.setObjectName("Muted")
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.hide()

        self.download_btn = QPushButton("Descargar")
        self.download_btn.setObjectName("Primary")
        self.download_btn.clicked.connect(self._start)
        self.cancel_btn = QPushButton("Ahora no")
        self.cancel_btn.clicked.connect(self.reject)

        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(self.cancel_btn)
        row.addWidget(self.download_btn)

        layout = QVBoxLayout(self)
        layout.addWidget(intro)
        layout.addWidget(self.bar)
        layout.addWidget(self.status)
        layout.addLayout(row)

    def _start(self) -> None:
        self.download_btn.setEnabled(False)
        self.cancel_btn.setEnabled(False)
        self.bar.show()

        def work(signals, llm, models):
            for i, model in enumerate(models, start=1):
                def report(status, fraction, model=model, i=i):
                    signals.progress.emit(f"[{i}/{len(models)}] {model}: {status}")
                    if fraction is not None:
                        signals.fraction.emit(fraction)
                llm.pull(model, report)
            return True

        self._task = Task(work, self.llm, self.models)
        self._task.signals.progress.connect(self.status.setText)
        self._task.signals.fraction.connect(lambda f: self.bar.setValue(int(f * 1000)))
        self._task.signals.done.connect(lambda _: self.accept())
        self._task.signals.failed.connect(self._failed)
        QThreadPool.globalInstance().start(self._task)

    def _failed(self, message: str) -> None:
        QMessageBox.critical(self, "Error en la descarga", message)
        self.download_btn.setEnabled(True)
        self.cancel_btn.setEnabled(True)


# =================================================== detalle de documento
class DocumentDialog(QDialog):
    FIELDS = [
        ("archivo", "Archivo"),
        ("tipo_documento", "Tipo"),
        ("fecha", "Fecha"),
        ("numero_documento", "Número"),
        ("emisor", "Emisor"),
        ("receptor", "Receptor"),
        ("base_imponible", "Base imponible"),
        ("impuestos", "Impuestos"),
        ("importe_total", "Importe total"),
        ("resumen", "Resumen"),
        ("estado_extraccion", "Estado de la extracción"),
    ]

    def __init__(self, doc: dict, on_reextract=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(doc["archivo"])
        self.resize(720, 560)
        currency = doc.get("moneda")

        form = QFormLayout()
        for key, label in self.FIELDS:
            value = doc.get(key)
            if key in ("base_imponible", "impuestos", "importe_total"):
                value = _money(value, currency)
            lab = QLabel("" if value is None else str(value))
            lab.setWordWrap(True)
            lab.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            form.addRow(f"<b>{label}:</b>", lab)

        lines = doc.get("lineas", [])
        table = QTableWidget(len(lines), 5)
        table.setHorizontalHeaderLabels(["Fecha", "Descripción", "Cantidad", "Precio unit.", "Importe"])
        table.verticalHeader().hide()
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        for r, line in enumerate(lines):
            values = [
                line.get("fecha") or "",
                line.get("descripcion") or "",
                "" if line.get("cantidad") is None else f"{line['cantidad']:g}",
                _money(line.get("precio_unitario")),
                _money(line.get("importe")),
            ]
            for c, v in enumerate(values):
                item = QTableWidgetItem(v)
                if c >= 2:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                table.setItem(r, c, item)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)

        buttons = QHBoxLayout()
        path = doc.get("ruta")
        if path and Path(path).exists():
            open_btn = QPushButton("Abrir archivo original")
            open_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(path)))
            buttons.addWidget(open_btn)
        if on_reextract:
            again = QPushButton("Volver a extraer datos")
            again.clicked.connect(lambda: (on_reextract(doc["id"]), self.accept()))
            buttons.addWidget(again)
        buttons.addStretch()
        close = QPushButton("Cerrar")
        close.clicked.connect(self.accept)
        buttons.addWidget(close)

        note = QLabel(
            "Estos datos los ha extraído el modelo automáticamente y se usan para responder "
            "preguntas con cálculos (totales, sumas, fechas). Revísalos si una respuesta no cuadra."
        )
        note.setObjectName("Muted")
        note.setWordWrap(True)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(QLabel(f"<b>Líneas / conceptos ({len(lines)})</b>"))
        layout.addWidget(table, 1)
        layout.addWidget(note)
        layout.addLayout(buttons)
