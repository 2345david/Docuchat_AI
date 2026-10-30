"""DocuChat: chatea con tus documentos usando IA local (Ollama).

Ejecutar:  python main.py
"""

import sys

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QMessageBox

from app.config import APP_NAME, APP_VERSION, Settings
from app.core import Core
from app.ui.main_window import MainWindow
from app.ui.styles import APP_QSS


def light_palette() -> QPalette:
    # Tema claro fijo para que se vea igual en Windows, macOS y Linux (también en modo oscuro).
    p = QPalette()
    p.setColor(QPalette.ColorRole.Window, QColor("#f5f6f8"))
    p.setColor(QPalette.ColorRole.WindowText, QColor("#1d2433"))
    p.setColor(QPalette.ColorRole.Base, QColor("#ffffff"))
    p.setColor(QPalette.ColorRole.AlternateBase, QColor("#f3f5f9"))
    p.setColor(QPalette.ColorRole.Text, QColor("#1d2433"))
    p.setColor(QPalette.ColorRole.Button, QColor("#ffffff"))
    p.setColor(QPalette.ColorRole.ButtonText, QColor("#1d2433"))
    p.setColor(QPalette.ColorRole.Highlight, QColor("#2f6fed"))
    p.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    p.setColor(QPalette.ColorRole.ToolTipBase, QColor("#ffffff"))
    p.setColor(QPalette.ColorRole.ToolTipText, QColor("#1d2433"))
    p.setColor(QPalette.ColorRole.PlaceholderText, QColor("#8a91a0"))
    return p


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    app.setStyle("Fusion")
    app.setPalette(light_palette())
    app.setStyleSheet(APP_QSS)

    try:
        core = Core(Settings.load())
    except Exception as exc:
        QMessageBox.critical(None, APP_NAME, f"No se pudo iniciar la aplicación:\n\n{exc}")
        return 1

    window = MainWindow(core)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
