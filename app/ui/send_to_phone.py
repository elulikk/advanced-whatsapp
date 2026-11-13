"""Diálogo «Enviar al celular»: arrastrá archivos (o elegilos) y se mandan a tu chat «Mi número»."""
import os
import threading

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog, QFileDialog, QHBoxLayout, QLabel, QListWidget, QPushButton, QVBoxLayout,
)

from app import sync
from app.ui.theme import C


class SendToPhoneDialog(QDialog):
    progress = Signal(int, int, str, bool, str)
    finished_all = Signal(dict)

    def __init__(self, gateway, paths, parent=None):
        super().__init__(parent)
        self.gateway = gateway
        self.setWindowTitle("Enviar al celular")
        self.setAcceptDrops(True)
        self.resize(460, 380)
        lay = QVBoxLayout(self)
        self.lbl = QLabel("Arrastrá acá los archivos que quieras pasar al celular.\n"
                          "Llegan a tu chat «Mi número» y en la app de Android se guardan en Descargas con un toque.")
        self.lbl.setWordWrap(True)
        lay.addWidget(self.lbl)
        self.list = QListWidget()
        self.list.setStyleSheet(f"QListWidget {{ border: 2px dashed {C['border']}; border-radius: 8px; }}")
        lay.addWidget(self.list, 1)
        self.status = QLabel("")
        self.status.setObjectName("subtle")
        self.status.setWordWrap(True)
        lay.addWidget(self.status)
        row = QHBoxLayout()
        self.btn_add = QPushButton("Agregar archivos…")
        self.btn_add.clicked.connect(self._pick)
        self.btn_clear = QPushButton("Vaciar lista")
        self.btn_clear.clicked.connect(self.list.clear)
        self.btn_send = QPushButton("Enviar")
        self.btn_send.setDefault(True)
        self.btn_send.clicked.connect(self._send)
        row.addWidget(self.btn_add)
        row.addWidget(self.btn_clear)
        row.addStretch()
        row.addWidget(self.btn_send)
        lay.addLayout(row)
        self.progress.connect(self._on_progress)
        self.finished_all.connect(self._on_finished)
        self.add_paths(paths)

    def add_paths(self, paths):
        have = {self.list.item(i).data(Qt.UserRole) for i in range(self.list.count())}
        for p in paths:
            if os.path.isfile(p) and p not in have:
                self.list.addItem(f"{os.path.basename(p)}  ({os.path.getsize(p) / 1024 / 1024:.1f} MB)")
                self.list.item(self.list.count() - 1).setData(Qt.UserRole, p)
                have.add(p)

    def _pick(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Elegir archivos para el celular")
        self.add_paths(files)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dragMoveEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        self.add_paths([u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()])
        e.acceptProposedAction()

    def _send(self):
        paths = [self.list.item(i).data(Qt.UserRole) for i in range(self.list.count())]
        if not paths:
            self.status.setText("Agregá al menos un archivo.")
            return
        for b in (self.btn_send, self.btn_add, self.btn_clear):
            b.setEnabled(False)
        self.status.setText("Enviando…")

        def run():
            res = sync.send_files_to_phone(self.gateway, paths, lambda *a: self.progress.emit(*a))
            self.finished_all.emit(res)
        threading.Thread(target=run, daemon=True).start()

    def _on_progress(self, i, total, name, ok, err):
        self.status.setText(f"Enviando {i}/{total}: {name}" + ("" if ok else f" — falló: {err}"))

    def _on_finished(self, res):
        for b in (self.btn_send, self.btn_add, self.btn_clear):
            b.setEnabled(True)
        if res.get("error"):
            self.status.setText(res["error"])
            return
        self.status.setText(f"Listo: {res['sent']} enviado(s)" + (f", {res['failed']} con error." if res["failed"] else "."))
        if not res["failed"]:
            self.list.clear()
