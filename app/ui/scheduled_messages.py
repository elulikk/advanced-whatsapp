"""
Advanced WhatsApp - Mensajes programados
========================================
Elegir la fecha y hora de un envío y ver, editar o cancelar lo que está en cola.
La cola vive en el gateway: los mensajes salen mientras Advanced WhatsApp siga abierto o en la bandeja.
"""

import time
from datetime import datetime
from typing import Optional

from PySide6.QtCore import Qt, QDateTime, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFrame, QHBoxLayout, QLabel, QMessageBox, QPlainTextEdit, QPushButton, QScrollArea, QVBoxLayout,
    QWidget
)

from app.recurrence import REPEAT_LABELS, DAY_SHORT, describe_repeat, normalize_repeat
from app.ui.chat_list import ElidedLabel
from app.ui.icons import pixmap, tool_button
from app.ui.status_view import StatusThumb, _datetime_edit, _day_label, _default_start
from app.ui.theme import C
from app.webhook_server import signals

ACTIVE_STATES = ("pending", "sending")

STATE_INFO = {
    "pending":  ("Programado", C["accent_hi"]),
    "sending":  ("Enviando…", C["warn"]),
    "sent":     ("Enviado", C["text_2"]),
    "failed":   ("Falló", C["danger_text"]),
    "expired":  ("Vencido", C["warn"]),
    "canceled": ("Cancelado", C["text_3"]),
}

KIND_INFO = {
    "text": ("chats", "Texto"),
    "image": ("image", "Foto"),
    "video": ("video", "Video"),
    "document": ("file", "Documento"),
    "audio": ("mic", "Nota de voz"),
    "sticker": ("smile", "Sticker"),
}


def when_text(at: int) -> str:
    """Hoy 09:00 · Mañana 09:00 · Lunes 10/11 09:00"""
    return f"{_day_label(at)} {datetime.fromtimestamp(at).strftime('%H:%M')}"


class ScheduleTimeDialog(QDialog):
    """Elige día y hora de un envío; con `text` también deja corregir el texto del mensaje."""

    def __init__(self, at: Optional[int] = None, text: Optional[str] = None, parent=None, repeat: Optional[dict] = None):
        super().__init__(parent)
        self.setWindowTitle("Programar envío")
        self.setMinimumWidth(400)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(12)

        start = QDateTime.fromSecsSinceEpoch(at) if at and at > time.time() else _default_start()
        row = QHBoxLayout()
        row.addWidget(QLabel("Enviar el:"))
        self.dt = _datetime_edit(start)
        row.addWidget(self.dt, stretch=1)
        layout.addLayout(row)

        # Repetición: diaria, semanal en los días elegidos, mensual o anual (cumpleaños)
        rrow = QHBoxLayout()
        rrow.addWidget(QLabel("Repetir:"))
        self.cmb_repeat = QComboBox()
        self.cmb_repeat.addItem("No repetir", "")
        for key, label in REPEAT_LABELS.items():
            self.cmb_repeat.addItem(label, key)
        rrow.addWidget(self.cmb_repeat, stretch=1)
        layout.addLayout(rrow)
        self.day_row = QWidget()
        drow = QHBoxLayout(self.day_row)
        drow.setContentsMargins(0, 0, 0, 0)
        self.day_checks = []
        for i, name in enumerate(DAY_SHORT):
            cb = QCheckBox(name)
            self.day_checks.append(cb)
            drow.addWidget(cb)
        layout.addWidget(self.day_row)
        cur = normalize_repeat(repeat)
        if cur:
            self.cmb_repeat.setCurrentIndex(max(0, self.cmb_repeat.findData(cur["type"])))
            for d in cur.get("days", []):
                self.day_checks[d].setChecked(True)
        self.cmb_repeat.currentIndexChanged.connect(self._sync_days)
        self._sync_days()

        self.txt = None
        if text is not None:
            self.txt = QPlainTextEdit(text)
            self.txt.setPlaceholderText("Texto del mensaje")
            self.txt.setFixedHeight(90)
            layout.addWidget(self.txt)

        btns = QHBoxLayout()
        btns.addStretch()
        b_cancel = QPushButton("Cancelar")
        b_cancel.clicked.connect(self.reject)
        btns.addWidget(b_cancel)
        b_ok = QPushButton("Guardar" if text is not None else "Elegir")
        b_ok.setObjectName("btnSave")
        b_ok.setDefault(True)
        b_ok.clicked.connect(self.accept)
        btns.addWidget(b_ok)
        layout.addLayout(btns)

    def _sync_days(self):
        self.day_row.setVisible(self.cmb_repeat.currentData() == "weekly")

    def repeat(self) -> Optional[dict]:
        """Repetición elegida ({type, days}) o None si no se repite."""
        kind = self.cmb_repeat.currentData()
        if not kind:
            return None
        r = {"type": kind}
        if kind == "weekly":
            r["days"] = [i for i, cb in enumerate(self.day_checks) if cb.isChecked()]
        return r

    def values(self):
        return self.dt.dateTime().toSecsSinceEpoch(), (self.txt.toPlainText().strip() if self.txt else None)


class ScheduledCard(QFrame):
    action = Signal(str, dict)   # (acción, item)

    def __init__(self, item: dict, show_chat: bool, parent=None):
        super().__init__(parent)
        self.item = item
        self.setObjectName("card")
        h = QHBoxLayout(self)
        h.setContentsMargins(10, 10, 10, 10)
        h.setSpacing(12)

        kind = item.get("type", "text")
        icon_name, kind_txt = KIND_INFO.get(kind, KIND_INFO["text"])
        if kind == "image":
            h.addWidget(StatusThumb(item, size=40))
        else:
            ic = QLabel()
            ic.setFixedSize(40, 40)
            ic.setAlignment(Qt.AlignCenter)
            ic.setPixmap(pixmap(icon_name, C["text_2"], 24))
            h.addWidget(ic)

        info = QVBoxLayout()
        info.setSpacing(3)
        top = QHBoxLayout()
        top.setSpacing(8)
        rep_txt = describe_repeat(item.get("repeat"))
        lbl_time = QLabel(when_text(int(item.get("at") or 0)) + (f"  ↻ {rep_txt}" if rep_txt else ""))
        lbl_time.setStyleSheet("font-weight: 600; font-size: 14px;")
        top.addWidget(lbl_time)
        state = item.get("state", "pending")
        label, color = STATE_INFO.get(state, (state, C["text_2"]))
        lbl_state = QLabel(label)
        lbl_state.setStyleSheet(f"color: {color}; font-size: 12px;")
        top.addWidget(lbl_state)
        top.addStretch()
        info.addLayout(top)

        body = (item.get("text") or "").replace("\n", " ")
        if kind != "text":
            name = kind_txt if kind == "audio" else (item.get("source_name") or kind_txt)
            body = name + (f" · {body}" if body else "")
        if show_chat and item.get("chat_name"):
            body = f"{item['chat_name']}: {body}"
        lbl_body = ElidedLabel(body)
        lbl_body.setObjectName("subtle")
        info.addWidget(lbl_body)
        if item.get("error") and state in ("failed", "expired", "pending"):
            lbl_err = ElidedLabel(item["error"])
            lbl_err.setObjectName("hint")
            lbl_err.setToolTip(item["error"])
            info.addWidget(lbl_err)
        h.addLayout(info, stretch=1)

        def btn(name, tip, act):
            b = tool_button(name, tip)
            b.clicked.connect(lambda: self.action.emit(act, self.item))
            h.addWidget(b)

        if state == "pending":
            btn("send", "Enviar ahora", "now")
            btn("edit", "Cambiar fecha, hora o texto", "edit")
            btn("close", "Cancelar", "cancel")
        elif state in ("failed", "expired", "canceled"):
            btn("refresh", "Reprogramar", "edit")
            btn("trash", "Quitar de la lista", "remove")
        elif state == "sent":
            btn("trash", "Quitar del historial (el mensaje sigue enviado)", "remove")


class ScheduledMessagesDialog(QDialog):
    """Lista de mensajes programados: de un chat (con opción de ver todos) o de todos."""

    def __init__(self, gateway, chat_jid: Optional[str] = None, parent=None):
        super().__init__(parent)
        self.gateway = gateway
        self.chat_jid = chat_jid
        self.setWindowTitle("Mensajes programados")
        self.resize(560, 560)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        self.chk_all = None
        if chat_jid:
            self.chk_all = QCheckBox("Ver todos los chats")
            self.chk_all.toggled.connect(lambda _: self.refresh())
            layout.addWidget(self.chk_all)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        board = QWidget()
        self.board_layout = QVBoxLayout(board)
        self.board_layout.setContentsMargins(0, 0, 6, 0)
        self.board_layout.setSpacing(8)
        scroll.setWidget(board)
        layout.addWidget(scroll, stretch=1)

        self.refresh()
        signals.scheduled_changed.connect(self._on_changed)

    def _on_changed(self, _data: dict):
        self.refresh()

    def _showing_all(self) -> bool:
        return not self.chat_jid or (self.chk_all is not None and self.chk_all.isChecked())

    def _clear(self):
        while self.board_layout.count():
            it = self.board_layout.takeAt(0)
            if it.widget():
                it.widget().hide()   # deleteLater espera al bucle de eventos: que no se vea encimada con la nueva
                it.widget().deleteLater()

    def _message(self, text: str):
        self._clear()
        lbl = QLabel(text)
        lbl.setObjectName("subtle")
        lbl.setWordWrap(True)
        lbl.setAlignment(Qt.AlignCenter)
        self.board_layout.addSpacing(60)
        self.board_layout.addWidget(lbl)
        self.board_layout.addStretch()

    def _section(self, title: str):
        lbl = QLabel(title.upper())
        lbl.setObjectName("sectionLabel")
        lbl.setContentsMargins(2, 8, 0, 2)
        self.board_layout.addWidget(lbl)

    def _add_card(self, item: dict, show_chat: bool):
        card = ScheduledCard(item, show_chat)
        card.action.connect(self._on_action)
        self.board_layout.addWidget(card)

    def refresh(self):
        res = self.gateway.list_scheduled_messages()
        if not res.get("success"):
            self._message(f"No se pudo leer la lista de programados.\n{res.get('error') or ''}")
            return
        show_all = self._showing_all()
        items = [m for m in res.get("items", []) if show_all or m.get("to") == self.chat_jid]
        if not items:
            self._message("No hay mensajes programados. Tocá el reloj junto al campo de texto de un chat para programar uno.")
            return
        self._clear()
        upcoming = sorted((m for m in items if m.get("state") in ACTIVE_STATES), key=lambda m: m["at"])
        history = sorted((m for m in items if m.get("state") not in ACTIVE_STATES),
                         key=lambda m: m.get("sent_at") or m["at"], reverse=True)
        if upcoming:
            self._section("Próximos")
            for m in upcoming:
                self._add_card(m, show_all)
        if history:
            self._section("Historial")
            for m in history:
                self._add_card(m, show_all)
        self.board_layout.addStretch()

    def _on_action(self, act: str, item: dict):
        res = None
        if act == "now":
            res = self.gateway.update_scheduled_message(item["id"], at=int(time.time()))
        elif act == "edit":
            dlg = ScheduleTimeDialog(int(item.get("at") or 0), item.get("text") or "", self, repeat=item.get("repeat"))
            if dlg.exec() != QDialog.Accepted:
                return
            at, text = dlg.values()
            if at <= time.time():
                QMessageBox.warning(self, "Fecha pasada", "Elegí una fecha y hora futuras.")
                return
            res = self.gateway.update_scheduled_message(item["id"], at=at, text=text, repeat=dlg.repeat())
        elif act == "cancel":
            if QMessageBox.question(self, "Cancelar envío", "¿Cancelar este mensaje programado?") != QMessageBox.Yes:
                return
            res = self.gateway.cancel_scheduled_message(item["id"])
        elif act == "remove":
            res = self.gateway.remove_scheduled_message(item["id"])
        if res is not None and not res.get("success"):
            QMessageBox.warning(self, "Mensajes programados", res.get("error") or "No se pudo completar la acción.")
        self.refresh()
        if act == "now":
            QTimer.singleShot(1500, self.refresh)
