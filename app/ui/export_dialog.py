"""Exportar chat (PC): diálogo de opciones y exportación en segundo plano con progreso. La lógica está en app/chat_export.py."""
import os
import threading
from datetime import datetime, time as dtime
from typing import Optional

from PySide6.QtCore import QDate, QObject, QStandardPaths, Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QComboBox, QDateEdit, QDialog, QFileDialog, QHBoxLayout, QLabel, QMessageBox,
    QProgressDialog, QPushButton, QRadioButton, QVBoxLayout, QWidget
)

from app import chat_export, chat_lock, db
from app.ui import lock_dialogs
from app.ui.chat_list import resolve_display_name

_FILTERS = {"txt": "Texto (*.txt)", "html": "Página web (*.html)", "pdf": "PDF (*.pdf)"}


class ExportChatDialog(QDialog):
    def __init__(self, chat_name: str, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("Exportar chat")
        self.setMinimumWidth(440)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(8)
        lay.addWidget(QLabel(f"Exportar el chat con <b>{chat_name}</b>"))

        lay.addWidget(QLabel("Formato"))
        row = QHBoxLayout()
        self._fmt = QButtonGroup(self)
        for key, text in (("html", "HTML (burbujas)"), ("pdf", "PDF"), ("txt", "TXT (como WhatsApp)")):
            rb = QRadioButton(text)
            rb.setProperty("fmt", key)
            rb.setChecked(key == "html")
            self._fmt.addButton(rb)
            row.addWidget(rb)
        lay.addLayout(row)

        lay.addWidget(QLabel("Mensajes"))
        self.cmb_range = QComboBox()
        self.cmb_range.addItems(["Todo el chat", "Último mes", "Entre fechas…"])
        lay.addWidget(self.cmb_range)
        dates = QHBoxLayout()
        today = QDate.currentDate()
        self.dt_from, self.dt_to = QDateEdit(today.addMonths(-1)), QDateEdit(today)
        for w in (self.dt_from, self.dt_to):
            w.setCalendarPopup(True)
            w.setDisplayFormat("dd/MM/yyyy")
        dates.addWidget(QLabel("Desde"))
        dates.addWidget(self.dt_from)
        dates.addWidget(QLabel("hasta"))
        dates.addWidget(self.dt_to)
        self.dates_box = QWidget()
        self.dates_box.setLayout(dates)
        dates.setContentsMargins(0, 0, 0, 0)
        self.dates_box.setVisible(False)
        lay.addWidget(self.dates_box)
        self.cmb_range.currentIndexChanged.connect(lambda i: self.dates_box.setVisible(i == 2))

        self.chk_media = QCheckBox("Incluir multimedia ya descargada")
        self.chk_media.setChecked(True)
        self.chk_embed = QCheckBox("HTML: incrustar las imágenes chicas (si no, van a una carpeta al lado)")
        self.chk_embed.setChecked(True)
        self.chk_download = QCheckBox("Descargar lo que falte (usa internet y puede tardar)")
        self.chk_trans = QCheckBox("Incluir transcripciones de audios")
        self.chk_trans.setChecked(True)
        self.chk_summ = QCheckBox("Incluir resúmenes de IA")
        self.chk_deleted = QCheckBox("Incluir mensajes eliminados y ediciones conservados")
        self.chk_deleted.setChecked(True)
        for w in (self.chk_media, self.chk_embed, self.chk_download, self.chk_trans, self.chk_summ, self.chk_deleted):
            lay.addWidget(w)
        self.chk_media.toggled.connect(lambda on: (self.chk_embed.setEnabled(on), self.chk_download.setEnabled(on)))
        self._fmt.buttonToggled.connect(lambda *_: self.chk_embed.setEnabled(self.fmt() == "html" and self.chk_media.isChecked()))

        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel = QPushButton("Cancelar")
        cancel.clicked.connect(self.reject)
        ok = QPushButton("Exportar…")
        ok.setObjectName("btnSend")
        ok.setDefault(True)
        ok.clicked.connect(self.accept)
        for b in (cancel, ok):
            b.setCursor(Qt.PointingHandCursor)
            buttons.addWidget(b)
        lay.addLayout(buttons)

    def fmt(self) -> str:
        return self._fmt.checkedButton().property("fmt")

    def options(self) -> chat_export.ExportOptions:
        idx = self.cmb_range.currentIndex()
        if idx == 2:
            d, h = self.dt_from.date(), self.dt_to.date()
            since, until = chat_export.range_for(
                "custom", desde=datetime.combine(d.toPython(), dtime.min).timestamp(),
                hasta=datetime.combine(h.toPython(), dtime.min).timestamp())
        else:
            since, until = chat_export.range_for("month" if idx == 1 else "all")
        media = self.chk_media.isChecked()
        return chat_export.ExportOptions(
            fmt=self.fmt(), since=since, until=until, include_media=media,
            embed_images=self.chk_embed.isChecked(), download_missing=media and self.chk_download.isChecked(),
            include_transcripts=self.chk_trans.isChecked(), include_summaries=self.chk_summ.isChecked(),
            include_deleted=self.chk_deleted.isChecked())


class _Signals(QObject):
    progress = Signal(int, int)
    done = Signal(object, str)   # resultado, error


def _download_one(msg: dict) -> Optional[str]:
    from app.gateway_manager import GatewayManager
    res = GatewayManager.get_instance().download_media(msg["id"], msg.get("media_type", ""), msg["media_raw"])
    if res.get("success") and res.get("media_path"):
        db.set_message_media_path(msg["id"], res["media_path"])
        return res["media_path"]
    return None


def export_chat_flow(parent: QWidget, chat: dict) -> None:
    """Flujo completo: contraseña si el chat está bloqueado → opciones → destino → exportación en segundo plano."""
    jid = chat.get("jid")
    if not jid:
        return
    if chat_lock.is_locked(jid) and not lock_dialogs.ask_current_password(
            parent, "Este chat está bloqueado. Ingresá la contraseña para exportarlo:"):
        return
    name = resolve_display_name(chat)
    dlg = ExportChatDialog(name, parent)
    if dlg.exec() != QDialog.Accepted:
        return
    opts = dlg.options()
    docs = QStandardPaths.writableLocation(QStandardPaths.DocumentsLocation) or os.path.expanduser("~")
    dest, _ = QFileDialog.getSaveFileName(
        parent, "Guardar exportación", os.path.join(docs, chat_export.default_filename(name, opts.fmt)), _FILTERS[opts.fmt])
    if not dest:
        return
    if not dest.lower().endswith("." + opts.fmt):
        dest += "." + opts.fmt

    stop = threading.Event()
    sig = _Signals(parent)
    bar = QProgressDialog("Exportando…", "Cancelar", 0, 100, parent)
    bar.setWindowTitle("Exportar chat")
    bar.setWindowModality(Qt.WindowModal)
    bar.setMinimumDuration(0)
    bar.setAutoClose(False)
    bar.setAutoReset(False)
    bar.canceled.connect(stop.set)

    def on_progress(done: int, total: int):
        bar.setMaximum(max(total, 1))
        bar.setValue(min(done, max(total, 1)))
        bar.setLabelText(f"Exportando… {done} de {total} mensajes")

    def on_done(res, error: str):
        bar.close()
        sig.deleteLater()
        if error:
            QMessageBox.warning(parent, "Exportar chat", f"No se pudo exportar: {error}")
        elif res.cancelled:
            QMessageBox.information(parent, "Exportar chat", "Exportación cancelada.")
        else:
            extra = f"\n{res.media_missing} archivo(s) no estaban descargados y quedaron afuera." if res.media_missing else ""
            box = QMessageBox(QMessageBox.Information, "Exportar chat",
                              f"Listo: {res.messages} mensajes exportados.{extra}", QMessageBox.Ok, parent)
            open_btn = box.addButton("Mostrar en la carpeta", QMessageBox.ActionRole)
            box.exec()
            if box.clickedButton() is open_btn:
                import subprocess
                subprocess.Popen(["explorer", "/select,", os.path.normpath(res.path)])

    sig.progress.connect(on_progress)
    sig.done.connect(on_done)

    def work():
        try:
            res = chat_export.export_chat(
                jid, dest, opts, progress=sig.progress.emit, cancelled=stop.is_set,
                downloader=_download_one if opts.download_missing else None)
            sig.done.emit(res, "")
        except Exception as e:   # noqa: BLE001 — se informa al usuario
            sig.done.emit(None, str(e))

    threading.Thread(target=work, daemon=True).start()
    bar.show()
