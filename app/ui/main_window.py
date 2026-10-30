"""Ventana principal: documentos a la izquierda, chat a la derecha."""

from __future__ import annotations

import html
from pathlib import Path

from PySide6.QtCore import Qt, QThreadPool, QTimer, QUrl
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QFileDialog, QFrame, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMainWindow, QMenu,
    QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QSplitter, QTextBrowser, QVBoxLayout,
    QWidget,
)

from ..config import APP_NAME
from ..core import Core
from ..llm import OllamaUnavailable
from ..loaders import SUPPORTED_EXTENSIONS
from .dialogs import DocumentDialog, ModelDownloadDialog, SettingsDialog
from .styles import CHAT_CSS
from .workers import Task

try:
    import markdown as _markdown
except ImportError:  # sin la librería se muestra el texto plano
    _markdown = None

TYPE_ICONS = {
    "factura": "🧾", "ticket": "🧾", "recibo": "🧾", "extracto_bancario": "🏦",
    "presupuesto": "📋", "nomina": "💼", "contrato": "📑",
}
EXT_ICONS = {".xlsx": "📊", ".xlsm": "📊", ".csv": "📊", ".png": "🖼️", ".jpg": "🖼️", ".jpeg": "🖼️"}

SUGGESTIONS = [
    "¿Qué documentos tengo?",
    "¿Cuánto he gastado en total este año?",
    "Resúmeme el último documento que añadí",
]


def md_to_html(text: str) -> str:
    if _markdown is None:
        return html.escape(text).replace("\n", "<br>")
    out = _markdown.markdown(text, extensions=["tables", "sane_lists"])
    return out.replace("<table>", '<table class="md" cellspacing="0">')


def money(value, currency=None) -> str:
    if value is None:
        return ""
    s = f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    symbol = "€" if (currency or "EUR").upper() == "EUR" else currency
    return f"{s} {symbol}"


# ------------------------------------------------------------------ widgets
class ChatInput(QPlainTextEdit):
    """Enter envía; Mayús+Enter hace salto de línea."""

    def __init__(self, on_send, parent=None):
        super().__init__(parent)
        self.on_send = on_send
        self.setObjectName("Input")
        self.setPlaceholderText("Pregunta sobre tus documentos…  (p. ej. «¿cuánto pagué en la factura de la luz de marzo?»)")
        self.setFixedHeight(64)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            self.on_send()
            return
        super().keyPressEvent(event)


class MainWindow(QMainWindow):
    def __init__(self, core: Core):
        super().__init__()
        self.core = core
        self.messages: list[tuple[str, str]] = []  # (rol, html)
        self._tasks: set[Task] = set()
        self._ingest_pool = QThreadPool(self)
        self._ingest_pool.setMaxThreadCount(1)  # los documentos se procesan de uno en uno
        self._pending = 0
        self._done_in_batch = 0
        self._busy_chat = False
        self._ollama_ok = False

        self.setWindowTitle(APP_NAME)
        self.resize(1200, 780)
        self.setAcceptDrops(True)
        self._build_ui()
        self.refresh_documents()
        self.render_chat()
        QTimer.singleShot(200, self.check_ollama)

    # ================================================================== UI
    def _build_ui(self) -> None:
        # ---------- panel lateral
        sidebar = QWidget()
        sidebar.setObjectName("Sidebar")
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(14, 14, 14, 14)
        title = QLabel("Mis documentos")
        title.setObjectName("SidebarTitle")
        self.doc_count = QLabel("")
        self.doc_count.setObjectName("Muted")
        self.doc_list = QListWidget()
        self.doc_list.setWordWrap(True)
        self.doc_list.itemDoubleClicked.connect(lambda _i: self.show_document())
        self.doc_list.itemSelectionChanged.connect(self._update_buttons)
        self.doc_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.doc_list.customContextMenuRequested.connect(self._doc_menu)

        drop_hint = QLabel("Arrastra aquí tus archivos\nPDF · Word · Excel · CSV · TXT")
        drop_hint.setObjectName("Muted")
        drop_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)

        add_btn = QPushButton("+  Añadir")
        add_btn.setObjectName("Primary")
        add_menu = QMenu(add_btn)
        add_menu.addAction("Archivos…", self.add_files_dialog)
        add_menu.addAction("Carpeta completa…", self.add_folder_dialog)
        add_btn.setMenu(add_menu)
        self.view_btn = QPushButton("Ver datos")
        self.view_btn.clicked.connect(self.show_document)
        self.del_btn = QPushButton("Eliminar")
        self.del_btn.clicked.connect(self.remove_selected)
        btns = QHBoxLayout()
        btns.addWidget(add_btn)
        btns.addWidget(self.view_btn)
        btns.addWidget(self.del_btn)

        side.addWidget(title)
        side.addWidget(self.doc_count)
        side.addWidget(self.doc_list, 1)
        side.addWidget(drop_hint)
        side.addLayout(btns)

        # ---------- barra superior
        top = QWidget()
        top.setObjectName("TopBar")
        top_l = QHBoxLayout(top)
        top_l.setContentsMargins(16, 10, 16, 10)
        app_title = QLabel(f"💬 {APP_NAME}")
        app_title.setObjectName("AppTitle")
        self.model_label = QLabel("")
        self.model_label.setObjectName("Muted")
        new_btn = QPushButton("Nueva conversación")
        new_btn.clicked.connect(self.new_conversation)
        settings_btn = QPushButton("⚙  Ajustes")
        settings_btn.clicked.connect(self.open_settings)
        top_l.addWidget(app_title)
        top_l.addSpacing(12)
        top_l.addWidget(self.model_label)
        top_l.addStretch()
        top_l.addWidget(new_btn)
        top_l.addWidget(settings_btn)

        # ---------- chat
        self.chat = QTextBrowser()
        self.chat.setObjectName("Chat")
        self.chat.setOpenLinks(False)
        self.chat.anchorClicked.connect(self._on_link)
        self.chat.document().setDefaultStyleSheet(CHAT_CSS)

        input_frame = QFrame()
        input_frame.setObjectName("InputFrame")
        in_l = QHBoxLayout(input_frame)
        in_l.setContentsMargins(10, 6, 8, 6)
        self.input = ChatInput(self.send_message)
        self.send_btn = QPushButton("Enviar")
        self.send_btn.setObjectName("Primary")
        self.send_btn.clicked.connect(self.send_message)
        in_l.addWidget(self.input, 1)
        in_l.addWidget(self.send_btn, 0, Qt.AlignmentFlag.AlignBottom)

        chat_area = QWidget()
        chat_l = QVBoxLayout(chat_area)
        chat_l.setContentsMargins(0, 0, 0, 0)
        chat_l.setSpacing(0)
        chat_l.addWidget(top)
        chat_l.addWidget(self.chat, 1)
        bottom = QWidget()
        bottom_l = QVBoxLayout(bottom)
        bottom_l.setContentsMargins(16, 8, 16, 14)
        bottom_l.addWidget(input_frame)
        chat_l.addWidget(bottom)

        splitter = QSplitter()
        splitter.addWidget(sidebar)
        splitter.addWidget(chat_area)
        splitter.setSizes([320, 880])
        splitter.setChildrenCollapsible(False)
        self.setCentralWidget(splitter)

        # ---------- barra de estado
        self.status_label = QLabel("")
        self.progress = QProgressBar()
        self.progress.setFixedWidth(180)
        self.progress.hide()
        self.statusBar().addWidget(self.status_label, 1)
        self.statusBar().addPermanentWidget(self.progress)
        self._update_model_label()

    def _update_model_label(self) -> None:
        s = self.core.settings
        state = "🟢" if self._ollama_ok else "⚪"
        self.model_label.setText(f"{state} {s.chat_model}  ·  {s.embed_model}")

    def _update_buttons(self) -> None:
        has = bool(self.doc_list.selectedItems())
        self.view_btn.setEnabled(has)
        self.del_btn.setEnabled(has)

    def _run(self, task: Task, pool: QThreadPool | None = None) -> Task:
        # Se guarda una referencia para que Python no destruya las señales antes de tiempo.
        self._tasks.add(task)
        task.signals.done.connect(lambda _r, t=task: self._tasks.discard(t))
        task.signals.failed.connect(lambda _m, t=task: self._tasks.discard(t))
        (pool or QThreadPool.globalInstance()).start(task)
        return task

    # ============================================================ Ollama
    def check_ollama(self) -> None:
        self.status_label.setText("Conectando con Ollama…")

        def work(_signals, llm):
            return llm.missing_models()

        task = Task(work, self.core.llm)
        task.signals.done.connect(self._on_ollama_checked)
        task.signals.failed.connect(self._on_ollama_failed)
        self._run(task)

    def _on_ollama_checked(self, missing: list[str]) -> None:
        if missing:
            self._ollama_ok = False
            self._update_model_label()
            dialog = ModelDownloadDialog(self.core.llm, missing, self)
            if dialog.exec():
                self.check_ollama()
            else:
                self.status_label.setText("Faltan modelos: " + ", ".join(missing))
            return
        self._ollama_ok = True
        self._update_model_label()
        self.status_label.setText("Listo")
        self._reindex_if_needed()

    def _on_ollama_failed(self, message: str) -> None:
        self._ollama_ok = False
        self._update_model_label()
        self.status_label.setText("Ollama no disponible")
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Ollama no está en marcha")
        box.setText(
            f"{message}\n\n1. Descarga Ollama desde https://ollama.com e instálalo.\n"
            "2. Ábrelo (se queda en segundo plano).\n3. Pulsa «Reintentar»."
        )
        retry = box.addButton("Reintentar", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("Cerrar", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() is retry:
            self.check_ollama()

    def _reindex_if_needed(self) -> None:
        missing = self.core.documents_missing_from_index()
        if not missing:
            return

        def work(signals, ingestor, docs):
            for i, d in enumerate(docs, start=1):
                signals.progress.emit(f"Reindexando {i}/{len(docs)}: {d['archivo']}")
                ingestor.reindex_document(d["id"], d["archivo"])
            return len(docs)

        task = Task(work, self.core.ingestor, missing)
        task.signals.progress.connect(self.status_label.setText)
        task.signals.done.connect(lambda n: self.status_label.setText(f"{n} documento(s) reindexados"))
        task.signals.failed.connect(lambda m: self.status_label.setText(f"Error al reindexar: {m}"))
        self._run(task, self._ingest_pool)

    # ======================================================= documentos
    def refresh_documents(self) -> None:
        selected = self._selected_id()
        self.doc_list.clear()
        docs = self.core.db.list_documents()
        for d in docs:
            icon = TYPE_ICONS.get(d.get("tipo_documento") or "", EXT_ICONS.get(d["extension"], "📄"))
            details = []
            if d.get("fecha"):
                details.append(d["fecha"])
            if d.get("emisor"):
                details.append(d["emisor"][:28])
            if d.get("importe_total") is not None:
                details.append(money(d["importe_total"], d.get("moneda")))
            if d.get("estado_extraccion") == "error":
                details.append("⚠ sin datos extraídos")
            text = f"{icon}  {d['archivo']}"
            if details:
                text += "\n      " + " · ".join(details)
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, d["id"])
            item.setToolTip(d.get("resumen") or d["archivo"])
            self.doc_list.addItem(item)
            if d["id"] == selected:
                item.setSelected(True)
        n = len(docs)
        self.doc_count.setText(f"{n} documento{'s' if n != 1 else ''}")
        self._update_buttons()

    def _selected_id(self) -> int | None:
        items = self.doc_list.selectedItems()
        return items[0].data(Qt.ItemDataRole.UserRole) if items else None

    def _doc_menu(self, pos) -> None:
        if not self.doc_list.itemAt(pos):
            return
        menu = QMenu(self)
        menu.addAction("Ver datos extraídos", self.show_document)
        menu.addAction("Volver a extraer datos", lambda: self.reextract(self._selected_id()))
        menu.addSeparator()
        menu.addAction("Eliminar", self.remove_selected)
        menu.exec(self.doc_list.mapToGlobal(pos))

    def add_files_dialog(self) -> None:
        patterns = " ".join(f"*{e}" for e in sorted(SUPPORTED_EXTENSIONS))
        files, _ = QFileDialog.getOpenFileNames(
            self, "Añadir documentos", str(Path.home()), f"Documentos ({patterns});;Todos (*)"
        )
        self.add_paths(files)

    def add_folder_dialog(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Añadir carpeta", str(Path.home()))
        if folder:
            self.add_paths([folder])

    def add_paths(self, paths) -> None:
        files: list[Path] = []
        for p in map(Path, paths):
            if p.is_dir():
                files += sorted(
                    f for f in p.rglob("*")
                    if f.is_file() and f.suffix.lower() in SUPPORTED_EXTENSIONS and not f.name.startswith("~$")
                )
            elif p.is_file():
                files.append(p)
        if not files:
            return
        if not self._ollama_ok:
            QMessageBox.warning(self, APP_NAME, "Ollama no está disponible. Ábrelo y pulsa «Reintentar».")
            self.check_ollama()
            return
        if self._pending == 0:
            self._done_in_batch = 0
        self._pending += len(files)
        self.progress.setRange(0, self._pending + self._done_in_batch)
        self.progress.show()
        for f in files:
            task = Task(lambda signals, ing, path: ing.ingest(path, signals.progress.emit), self.core.ingestor, f)
            task.signals.progress.connect(self.status_label.setText)
            task.signals.done.connect(lambda _id: self._ingest_finished(None))
            task.signals.failed.connect(lambda msg, f=f: self._ingest_finished(f"{f.name}: {msg}"))
            self._run(task, self._ingest_pool)

    def _ingest_finished(self, error: str | None) -> None:
        self._pending -= 1
        self._done_in_batch += 1
        self.progress.setValue(self._done_in_batch)
        self.refresh_documents()
        if error:
            self._errors = getattr(self, "_errors", []) + [error]
        if self._pending == 0:
            self.progress.hide()
            errors = getattr(self, "_errors", [])
            self._errors = []
            ok = self._done_in_batch - len(errors)
            self.status_label.setText(f"{ok} documento(s) añadidos" + (f", {len(errors)} con problemas" if errors else ""))
            if errors:
                QMessageBox.warning(self, "Algunos documentos no se añadieron", "\n\n".join(errors[:15]))

    def remove_selected(self) -> None:
        doc_id = self._selected_id()
        if doc_id is None:
            return
        doc = self.core.db.get_document(doc_id)
        answer = QMessageBox.question(
            self, "Eliminar documento",
            f"¿Quitar «{doc['archivo']}» de DocuChat?\n\nEl archivo original de tu ordenador no se borra.",
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.core.ingestor.remove(doc_id)
            self.refresh_documents()
            self.status_label.setText(f"«{doc['archivo']}» eliminado")

    def show_document(self) -> None:
        doc_id = self._selected_id()
        if doc_id is None:
            return
        doc = self.core.db.get_document(doc_id)
        if doc:
            DocumentDialog(doc, on_reextract=self.reextract, parent=self).exec()

    def reextract(self, doc_id: int | None) -> None:
        if doc_id is None:
            return
        doc = self.core.db.get_document(doc_id)

        def work(signals, ingestor, d):
            return ingestor.extract(d["id"], d["archivo"], ingestor.db.get_text(d["id"]), signals.progress.emit)

        task = Task(work, self.core.ingestor, doc)
        task.signals.progress.connect(self.status_label.setText)
        task.signals.done.connect(
            lambda ok: (self.refresh_documents(),
                        self.status_label.setText("Datos actualizados" if ok else "No se pudieron extraer los datos"))
        )
        self._run(task, self._ingest_pool)

    # ============================================================== chat
    def render_chat(self) -> None:
        if not self.messages:
            links = "".join(
                f'<p>👉 <a href="ask:{html.escape(s)}">{html.escape(s)}</a></p>' for s in SUGGESTIONS
            )
            self.chat.setHtml(
                f"""<div align="center"><br><br><br>
                <p style="font-size:22pt;">💬</p>
                <p style="font-size:15pt; font-weight:600;">Pregunta lo que quieras sobre tus documentos</p>
                <p class="src">Todo se procesa en tu ordenador con Ollama. Tus archivos no salen de aquí.</p>
                <br>{links}</div>"""
            )
            return
        parts = []
        for role, body in self.messages:
            if role == "user":
                parts.append(
                    f"""<table width="100%" cellspacing="0" cellpadding="0"><tr>
                    <td width="22%"></td>
                    <td bgcolor="#2f6fed" style="color:#ffffff; padding:10px 14px;">{body}</td>
                    </tr></table><br>"""
                )
            else:
                parts.append(
                    f"""<table width="100%" cellspacing="0" cellpadding="0"><tr>
                    <td bgcolor="#ffffff" style="padding:10px 14px; border:1px solid #e1e4ea;">
                    <span class="who">DocuChat</span><br>{body}</td>
                    <td width="12%"></td></tr></table><br>"""
                )
        self.chat.setHtml("".join(parts))
        bar = self.chat.verticalScrollBar()
        QTimer.singleShot(0, lambda: bar.setValue(bar.maximum()))

    def _on_link(self, url: QUrl) -> None:
        text = url.toString()
        if text.startswith("ask:"):
            self.input.setPlainText(QUrl.fromPercentEncoding(text[4:].encode()))
            self.send_message()

    def send_message(self) -> None:
        question = self.input.toPlainText().strip()
        if not question or self._busy_chat:
            return
        if not self._ollama_ok:
            self.check_ollama()
            return
        self.input.clear()
        self._busy_chat = True
        self.send_btn.setEnabled(False)
        self.messages.append(("user", html.escape(question).replace("\n", "<br>")))
        self.messages.append(("assistant", '<span class="src">Buscando en tus documentos…</span>'))
        self.render_chat()

        task = Task(lambda _s, assistant, q: assistant.ask(q), self.core.assistant, question)
        task.signals.done.connect(self._on_answer)
        task.signals.failed.connect(self._on_answer_failed)
        self._run(task)

    def _on_answer(self, answer) -> None:
        body = md_to_html(answer.text)
        if answer.sources:
            body += '<p class="src">📎 Fuentes: ' + " · ".join(html.escape(s.label()) for s in answer.sources) + "</p>"
        self.messages[-1] = ("assistant", body)
        self._chat_done()

    def _on_answer_failed(self, message: str) -> None:
        self.messages[-1] = ("assistant", f"⚠️ <b>Ha ocurrido un error:</b> {html.escape(message)}")
        if "Ollama no responde" in message:
            self._ollama_ok = False
            self._update_model_label()
        self._chat_done()

    def _chat_done(self) -> None:
        self._busy_chat = False
        self.send_btn.setEnabled(True)
        self.render_chat()
        self.input.setFocus()

    def new_conversation(self) -> None:
        if self._busy_chat:
            return
        self.core.assistant.reset()
        self.messages.clear()
        self.render_chat()

    # ========================================================= ajustes
    def open_settings(self) -> None:
        try:
            installed = self.core.llm.installed_models()
        except OllamaUnavailable:
            installed = set()
        dialog = SettingsDialog(self.core.settings, installed, self)
        if dialog.exec():
            dialog.apply()
            self.core.rebuild()
            self._update_model_label()
            self.check_ollama()

    # ============================================== arrastrar y soltar
    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dragMoveEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        paths = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
        self.add_paths(paths)
        event.acceptProposedAction()

    def closeEvent(self, event) -> None:
        if self._pending:
            answer = QMessageBox.question(
                self, APP_NAME, "Todavía se están procesando documentos. ¿Cerrar de todos modos?"
            )
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
        event.accept()
