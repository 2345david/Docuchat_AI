"""Estilos de la interfaz."""

ACCENT = "#2f6fed"
ACCENT_DARK = "#2459c4"
BG = "#f5f6f8"
PANEL = "#ffffff"
BORDER = "#e1e4ea"
TEXT = "#1d2433"
MUTED = "#6b7385"

APP_QSS = f"""
QMainWindow, QDialog {{ background: {BG}; }}
QWidget {{ color: {TEXT}; font-size: 10pt; }}
#Sidebar {{ background: {PANEL}; border-right: 1px solid {BORDER}; }}
#SidebarTitle {{ font-size: 13pt; font-weight: 600; }}
#Muted {{ color: {MUTED}; }}
#TopBar {{ background: {PANEL}; border-bottom: 1px solid {BORDER}; }}
#AppTitle {{ font-size: 14pt; font-weight: 700; }}
QListWidget {{
    background: {PANEL}; border: none; outline: none;
}}
QListWidget::item {{
    padding: 8px 6px; border-bottom: 1px solid #eef0f4;
}}
QListWidget::item:selected {{ background: #e8f0fe; color: {TEXT}; }}
QListWidget::item:hover {{ background: #f3f6fb; }}
QTextBrowser#Chat {{ background: {BG}; border: none; padding: 8px; }}
#InputFrame {{ background: {PANEL}; border: 1px solid {BORDER}; border-radius: 10px; }}
QPlainTextEdit#Input {{ background: transparent; border: none; font-size: 11pt; }}
QPushButton {{
    background: {PANEL}; border: 1px solid {BORDER}; border-radius: 6px; padding: 6px 12px;
}}
QPushButton:hover {{ background: #eef2f8; }}
QPushButton:disabled {{ color: #a0a6b3; }}
QPushButton#Primary {{
    background: {ACCENT}; color: white; border: none; font-weight: 600; padding: 7px 16px;
}}
QPushButton#Primary:hover {{ background: {ACCENT_DARK}; }}
QPushButton#Primary:disabled {{ background: #9db7ef; }}
QProgressBar {{ border: 1px solid {BORDER}; border-radius: 4px; text-align: center; height: 14px; }}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 3px; }}
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
    background: {PANEL}; border: 1px solid {BORDER}; border-radius: 5px; padding: 4px 6px;
}}
QTableWidget {{ background: {PANEL}; gridline-color: {BORDER}; border: 1px solid {BORDER}; }}
QStatusBar {{ background: {PANEL}; border-top: 1px solid {BORDER}; }}
"""

# HTML del chat (QTextBrowser solo soporta un subconjunto de CSS: por eso se usan tablas).
CHAT_CSS = f"""
body {{ font-size: 10.5pt; color: {TEXT}; }}
p {{ margin: 4px 0; }}
table.md {{ border-collapse: collapse; margin: 6px 0; }}
table.md th {{ background: #eef1f6; padding: 4px 8px; border: 1px solid {BORDER}; }}
table.md td {{ padding: 4px 8px; border: 1px solid {BORDER}; }}
code {{ background: #eef1f6; font-family: Consolas, monospace; }}
.src {{ color: {MUTED}; font-size: 9pt; }}
.who {{ color: {MUTED}; font-size: 9pt; font-weight: 600; }}
a {{ color: {ACCENT}; text-decoration: none; }}
"""
