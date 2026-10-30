"""Tareas en segundo plano para que la ventana no se congele mientras trabaja el modelo."""

from __future__ import annotations

import traceback

from PySide6.QtCore import QObject, QRunnable, Signal


class TaskSignals(QObject):
    progress = Signal(str)          # mensaje para la barra de estado
    fraction = Signal(float)        # progreso 0..1 (descargas)
    done = Signal(object)           # resultado
    failed = Signal(str)            # mensaje de error


class Task(QRunnable):
    """Ejecuta fn(signals, *args) en un hilo del QThreadPool."""

    def __init__(self, fn, *args):
        super().__init__()
        self.fn = fn
        self.args = args
        self.signals = TaskSignals()

    def run(self) -> None:
        try:
            result = self.fn(self.signals, *self.args)
        except Exception as exc:  # se muestra al usuario, nunca rompe la app
            traceback.print_exc()
            self.signals.failed.emit(str(exc) or exc.__class__.__name__)
        else:
            self.signals.done.emit(result)
