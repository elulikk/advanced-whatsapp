"""
Advanced WhatsApp - Mensajería extra: ventanas de respuestas automáticas, plantillas, encuestas, destacados,
envío a varios y «ver original» de mensajes eliminados / editados.
"""

import os
from datetime import datetime
from typing import Dict, List, Optional

from PySide6.QtCore import Qt, QTime, Signal
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMessageBox, QPlainTextEdit, QPushButton, QSpinBox, QTimeEdit, QVBoxLayout, QWidget
)

from app import auto_reply, quick_replies
from app.db import (
    get_chats, get_message_edits, get_revoked_original, get_starred_messages, set_message_starred, _preview_for
)
from app.ui.chat_list import resolve_display_name
from app.ui.icons import icon
from app.ui.theme import C

MAX_BULK_TARGETS = 100


def _buttons(dialog: QDialog, ok_text: str = "Guardar", ok_object: str = "btnSave") -> tuple:
    row = QHBoxLayout()
    row.addStretch()
    cancel = QPushButton("Cancelar")
    cancel.clicked.connect(dialog.reject)
    ok = QPushButton(ok_text)
    ok.setObjectName(ok_object)
    ok.setDefault(True)
    for b in (cancel, ok):
        b.setCursor(Qt.PointingHandCursor)
        row.addWidget(b)
    return row, ok


def _hint(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setObjectName("hint")
    lbl.setWordWrap(True)
    return lbl


# ──────────────────────────────────────────────────────────────────────────
# Elegir chats (lista con tildes y buscador)
# ──────────────────────────────────────────────────────────────────────────
class ChatPickerDialog(QDialog):
    """Elige varios chats. `individual_only` deja afuera los grupos."""

    def __init__(self, title: str, preselected: Optional[List[str]] = None, individual_only: bool = True,
                 limit: int = MAX_BULK_TARGETS, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(400, 520)
        self._individual_only = individual_only
        self._limit = limit
        self._checked: Dict[str, dict] = {}
        self._preselected = set(preselected or [])
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(10)
        self.txt_search = QLineEdit()
        self.txt_search.setObjectName("searchField")
        self.txt_search.setPlaceholderText("Buscar contacto")
        self.txt_search.addAction(icon("search", C["text_3"]), QLineEdit.LeadingPosition)
        self.txt_search.setClearButtonEnabled(True)
        self.txt_search.textChanged.connect(self._render)
        lay.addWidget(self.txt_search)
        self.list = QListWidget()
        self.list.setObjectName("framedList")
        self.list.itemChanged.connect(self._on_item_changed)
        lay.addWidget(self.list, stretch=1)
        self.lbl_count = QLabel("")
        self.lbl_count.setObjectName("subtle")
        lay.addWidget(self.lbl_count)
        row, self.btn_ok = _buttons(self, "Elegir", "btnSend")
        self.btn_ok.clicked.connect(self.accept)
        lay.addLayout(row)
        for chat in get_chats(limit=2000):   # los preseleccionados cuentan aunque no estén en pantalla
            if chat.get("jid") in self._preselected:
                self._checked[chat["jid"]] = chat
        self._render()

    def _render(self, *_):
        term = self.txt_search.text().strip()
        self.list.blockSignals(True)
        self.list.clear()
        for chat in get_chats(search=term or None, limit=300):
            jid = chat.get("jid") or ""
            if not jid or (self._individual_only and jid.endswith("@g.us")):
                continue
            item = QListWidgetItem(resolve_display_name(chat))
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if jid in self._checked else Qt.Unchecked)
            item.setData(Qt.UserRole, chat)
            self.list.addItem(item)
        self.list.blockSignals(False)
        self._update_state()

    def _on_item_changed(self, item: QListWidgetItem):
        chat = item.data(Qt.UserRole)
        if item.checkState() == Qt.Checked:
            if len(self._checked) >= self._limit:
                self.list.blockSignals(True)
                item.setCheckState(Qt.Unchecked)
                self.list.blockSignals(False)
                QMessageBox.information(self, "Elegir contactos", f"Podés elegir hasta {self._limit}.")
                return
            self._checked[chat["jid"]] = chat
        else:
            self._checked.pop(chat["jid"], None)
        self._update_state()

    def _update_state(self):
        n = len(self._checked)
        self.lbl_count.setText(f"{n} seleccionado{'s' if n != 1 else ''}")

    def selected_chats(self) -> List[dict]:
        return list(self._checked.values())


# ──────────────────────────────────────────────────────────────────────────
# Respuestas automáticas
# ──────────────────────────────────────────────────────────────────────────
class RuleEditDialog(QDialog):
    def __init__(self, rule: dict, parent=None):
        super().__init__(parent)
        self.rule = dict(rule)
        self._exclude = list(rule.get("exclude") or [])
        self._only = list(rule.get("only") or [])
        manual = rule.get("kind") == "manual"
        self.setWindowTitle("Modo manual" if manual else "Respuesta por horario")
        self.setMinimumWidth(460)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(10)

        lay.addWidget(QLabel("Nombre"))
        self.txt_name = QLineEdit(rule.get("name", ""))
        lay.addWidget(self.txt_name)
        lay.addWidget(QLabel("Mensaje que se responde"))
        self.txt_text = QPlainTextEdit(rule.get("text", ""))
        self.txt_text.setFixedHeight(90)
        lay.addWidget(self.txt_text)

        self.day_checks: List[QCheckBox] = []
        self.t_from = self.t_to = None
        if not manual:
            lay.addWidget(QLabel("Días (sin marcar = todos)"))
            days = QHBoxLayout()
            for i, name in enumerate(auto_reply.DAY_NAMES):
                cb = QCheckBox(name[:3])
                cb.setChecked(i in (rule.get("days") or []))
                self.day_checks.append(cb)
                days.addWidget(cb)
            lay.addLayout(days)
            times = QHBoxLayout()
            times.addWidget(QLabel("Desde"))
            self.t_from = QTimeEdit(QTime.fromString(rule.get("from", "22:00"), "H:mm"))
            self.t_to = QTimeEdit(QTime.fromString(rule.get("to", "08:00"), "H:mm"))
            for t in (self.t_from, self.t_to):
                t.setDisplayFormat("HH:mm")
            times.addWidget(self.t_from)
            times.addWidget(QLabel("hasta"))
            times.addWidget(self.t_to)
            times.addStretch()
            lay.addLayout(times)
            lay.addWidget(_hint("Si «hasta» es menor que «desde», la franja cruza la medianoche (por ejemplo 22:00 a 08:00)."))
        else:
            lay.addWidget(_hint("Un modo manual se enciende y se apaga a mano desde la lista (por ejemplo «Estoy manejando»)."))

        self.chk_individual = QCheckBox("Responder solo en chats individuales (no en grupos)")
        self.chk_individual.setChecked(rule.get("individual_only", True))
        lay.addWidget(self.chk_individual)
        cd = QHBoxLayout()
        cd.addWidget(QLabel("No responder dos veces al mismo contacto en"))
        self.spin_cd = QSpinBox()
        self.spin_cd.setRange(0, 720)
        self.spin_cd.setSuffix(" h")
        self.spin_cd.setValue(int(rule.get("cooldown_hours", 12)))
        cd.addWidget(self.spin_cd)
        cd.addStretch()
        lay.addLayout(cd)

        self.btn_exclude = QPushButton()
        self.btn_exclude.clicked.connect(self._pick_exclude)
        self.btn_only = QPushButton()
        self.btn_only.clicked.connect(self._pick_only)
        for b in (self.btn_exclude, self.btn_only):
            b.setCursor(Qt.PointingHandCursor)
            lay.addWidget(b)
        lay.addWidget(_hint("Excepciones: contactos a los que nunca se responde. Solo a: si elegís contactos, se responde "
                            "únicamente a ellos (lista blanca)."))
        self._refresh_buttons()

        row, ok = _buttons(self)
        ok.clicked.connect(self._accept)
        lay.addLayout(row)

    def _refresh_buttons(self):
        self.btn_exclude.setText(f"Excepciones: {len(self._exclude)} contacto(s)…")
        self.btn_only.setText(f"Responder solo a: {len(self._only) or 'todos'}…")

    def _pick_exclude(self):
        dlg = ChatPickerDialog("Excepciones (no responder)", self._exclude, parent=self)
        if dlg.exec() == QDialog.Accepted:
            self._exclude = [c["jid"] for c in dlg.selected_chats()]
            self._refresh_buttons()

    def _pick_only(self):
        dlg = ChatPickerDialog("Responder solo a", self._only, parent=self)
        if dlg.exec() == QDialog.Accepted:
            self._only = [c["jid"] for c in dlg.selected_chats()]
            self._refresh_buttons()

    def _accept(self):
        r = self.rule
        r.update(name=self.txt_name.text().strip() or "Respuesta", text=self.txt_text.toPlainText().strip(),
                 individual_only=self.chk_individual.isChecked(), cooldown_hours=self.spin_cd.value(),
                 exclude=self._exclude, only=self._only)
        if self.t_from:
            r.update(days=[i for i, cb in enumerate(self.day_checks) if cb.isChecked()],
                     **{"from": self.t_from.time().toString("HH:mm"), "to": self.t_to.time().toString("HH:mm")})
        problem = auto_reply.validate_rule(r)
        if problem:
            QMessageBox.warning(self, "Respuesta automática", problem)
            return
        self.accept()


class AutoReplyDialog(QDialog):
    """Reglas de respuesta automática. Las ejecuta el gateway: siguen andando con la ventana cerrada."""

    def __init__(self, gateway, parent=None):
        super().__init__(parent)
        self.gateway = gateway
        self.setWindowTitle("Respuestas automáticas")
        self.resize(560, 520)
        self.cfg = {"enabled": False, "active_rule": None, "rules": []}
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(10)
        self.chk_enabled = QCheckBox("Responder automáticamente los mensajes que lleguen")
        lay.addWidget(self.chk_enabled)
        lay.addWidget(_hint("Se responde solo a mensajes nuevos, con una pausa corta, aunque Advanced WhatsApp esté en la "
                            "bandeja. Cada contacto recibe como máximo una respuesta por regla en el tiempo que elijas."))
        self.list = QListWidget()
        self.list.setObjectName("framedList")
        self.list.itemDoubleClicked.connect(lambda _: self._edit())
        lay.addWidget(self.list, stretch=1)

        row = QHBoxLayout()
        for text, fn in (("Agregar horario", lambda: self._add("schedule")), ("Agregar modo manual", lambda: self._add("manual")),
                         ("Editar", self._edit), ("Quitar", self._remove)):
            b = QPushButton(text)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(fn)
            row.addWidget(b)
        row.addStretch()
        lay.addLayout(row)
        self.btn_mode = QPushButton()
        self.btn_mode.setCursor(Qt.PointingHandCursor)
        self.btn_mode.clicked.connect(self._toggle_mode)
        lay.addWidget(self.btn_mode)
        self.list.currentRowChanged.connect(lambda _: self._refresh_mode_button())

        brow, ok = _buttons(self)
        ok.clicked.connect(self._save)
        lay.addLayout(brow)
        self._load()

    def _load(self):
        res = self.gateway.get_auto_reply()
        if res.get("success"):
            self.cfg = {"enabled": bool(res.get("enabled")), "active_rule": res.get("active_rule"), "rules": res.get("rules") or []}
        else:
            QMessageBox.warning(self, "Respuestas automáticas", "No se pudo leer la configuración del gateway.\n" + (res.get("error") or ""))
        self.chk_enabled.setChecked(self.cfg["enabled"])
        self._render()

    def _render(self, select: int = -1):
        self.list.clear()
        for r in self.cfg["rules"]:
            on = self.cfg["active_rule"] == r["id"]
            state = " · ACTIVADO" if on else ""
            item = QListWidgetItem(f"{r['name']}{state}\n{auto_reply.describe_rule(r)}")
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if r.get("enabled", True) else Qt.Unchecked)
            self.list.addItem(item)
        if self.cfg["rules"]:
            self.list.setCurrentRow(select if 0 <= select < len(self.cfg["rules"]) else 0)
        self._refresh_mode_button()

    def _current(self) -> int:
        return self.list.currentRow()

    def _refresh_mode_button(self):
        i = self._current()
        rule = self.cfg["rules"][i] if 0 <= i < len(self.cfg["rules"]) else None
        show = bool(rule and rule.get("kind") == "manual")
        self.btn_mode.setVisible(show)
        if show:
            self.btn_mode.setText("Apagar este modo" if self.cfg["active_rule"] == rule["id"] else "Encender este modo ahora")

    def _toggle_mode(self):
        i = self._current()
        if i < 0:
            return
        rid = self.cfg["rules"][i]["id"]
        self.cfg["active_rule"] = None if self.cfg["active_rule"] == rid else rid
        if self.cfg["active_rule"]:
            self.chk_enabled.setChecked(True)
        self._render(i)

    def _add(self, kind: str):
        dlg = RuleEditDialog(auto_reply.new_rule(kind), self)
        if dlg.exec() == QDialog.Accepted:
            self.cfg["rules"].append(dlg.rule)
            self._render(len(self.cfg["rules"]) - 1)

    def _edit(self):
        i = self._current()
        if i < 0:
            return
        dlg = RuleEditDialog(self.cfg["rules"][i], self)
        if dlg.exec() == QDialog.Accepted:
            self.cfg["rules"][i] = dlg.rule
            self._render(i)

    def _remove(self):
        i = self._current()
        if i >= 0:
            if self.cfg["active_rule"] == self.cfg["rules"][i]["id"]:
                self.cfg["active_rule"] = None
            del self.cfg["rules"][i]
            self._render()

    def _save(self):
        for i, r in enumerate(self.cfg["rules"]):   # los tildes de la lista son el «activada» de cada regla
            r["enabled"] = self.list.item(i).checkState() == Qt.Checked
        self.cfg["enabled"] = self.chk_enabled.isChecked()
        res = self.gateway.save_auto_reply(self.cfg)
        if not res.get("success"):
            QMessageBox.warning(self, "Respuestas automáticas", res.get("error") or "No se pudo guardar.")
            return
        self.accept()


# ──────────────────────────────────────────────────────────────────────────
# Plantillas / atajos
# ──────────────────────────────────────────────────────────────────────────
class TemplatesDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Plantillas y atajos")
        self.resize(560, 420)
        self.items = quick_replies.load_templates()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(10)
        lay.addWidget(_hint("Escribí /atajo en cualquier chat y pulsá Espacio, Tab o Enter: se reemplaza por el texto guardado."))
        body = QHBoxLayout()
        left = QVBoxLayout()
        self.list = QListWidget()
        self.list.setObjectName("framedList")
        self.list.setMaximumWidth(190)
        self.list.currentRowChanged.connect(self._show)
        left.addWidget(self.list, stretch=1)
        b_add = QPushButton("Nueva")
        b_add.clicked.connect(self._add)
        b_del = QPushButton("Quitar")
        b_del.clicked.connect(self._remove)
        row = QHBoxLayout()
        row.addWidget(b_add)
        row.addWidget(b_del)
        left.addLayout(row)
        body.addLayout(left)
        right = QVBoxLayout()
        right.addWidget(QLabel("Atajo"))
        self.txt_shortcut = QLineEdit()
        self.txt_shortcut.setPlaceholderText("saludo")
        self.txt_shortcut.editingFinished.connect(self._apply)
        right.addWidget(self.txt_shortcut)
        right.addWidget(QLabel("Texto"))
        self.txt_text = QPlainTextEdit()
        self.txt_text.textChanged.connect(self._apply)
        right.addWidget(self.txt_text, stretch=1)
        body.addLayout(right, stretch=1)
        lay.addLayout(body, stretch=1)
        brow, ok = _buttons(self)
        ok.clicked.connect(self._save)
        lay.addLayout(brow)
        self._loading = False
        self._render()

    def _render(self, select: int = 0):
        self.list.blockSignals(True)
        self.list.clear()
        for it in self.items:
            self.list.addItem(f"/{it['shortcut']}")
        self.list.blockSignals(False)
        if self.items:
            self.list.setCurrentRow(min(select, len(self.items) - 1))
        self._show(self.list.currentRow())

    def _show(self, i: int):
        self._loading = True
        has = 0 <= i < len(self.items)
        self.txt_shortcut.setEnabled(has)
        self.txt_text.setEnabled(has)
        self.txt_shortcut.setText(self.items[i]["shortcut"] if has else "")
        self.txt_text.setPlainText(self.items[i]["text"] if has else "")
        self._loading = False

    def _apply(self):
        i = self.list.currentRow()
        if self._loading or not (0 <= i < len(self.items)):
            return
        self.items[i]["text"] = self.txt_text.toPlainText()
        sc = quick_replies.clean_shortcut(self.txt_shortcut.text())
        if sc and sc != self.items[i]["shortcut"] and not any(o["shortcut"] == sc for o in self.items):
            self.items[i]["shortcut"] = sc
            self.list.item(i).setText(f"/{sc}")

    def _add(self):
        n = 1
        while any(o["shortcut"] == f"atajo{n}" for o in self.items):
            n += 1
        self.items.append({"shortcut": f"atajo{n}", "text": ""})
        self._render(len(self.items) - 1)
        self.txt_shortcut.setFocus()
        self.txt_shortcut.selectAll()

    def _remove(self):
        i = self.list.currentRow()
        if 0 <= i < len(self.items):
            del self.items[i]
            self._render(i)

    def _save(self):
        self._apply()
        if not quick_replies.save_templates(self.items):
            QMessageBox.warning(self, "Plantillas", "No se pudieron guardar las plantillas.")
            return
        self.accept()


# ──────────────────────────────────────────────────────────────────────────
# Encuesta
# ──────────────────────────────────────────────────────────────────────────
class PollDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Crear encuesta")
        self.setMinimumWidth(420)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(10)
        lay.addWidget(QLabel("Pregunta"))
        self.txt_q = QLineEdit()
        self.txt_q.setMaxLength(250)
        lay.addWidget(self.txt_q)
        lay.addWidget(QLabel("Opciones (una por línea, de 2 a 12)"))
        self.txt_opts = QPlainTextEdit()
        self.txt_opts.setFixedHeight(130)
        lay.addWidget(self.txt_opts)
        self.chk_multi = QCheckBox("Permitir elegir varias opciones")
        lay.addWidget(self.chk_multi)
        row, ok = _buttons(self, "Enviar encuesta", "btnSend")
        ok.clicked.connect(self._accept)
        lay.addLayout(row)

    def options(self) -> List[str]:
        seen, out = set(), []
        for line in self.txt_opts.toPlainText().splitlines():
            line = line.strip()[:100]
            if line and line not in seen:
                seen.add(line)
                out.append(line)
        return out

    def _accept(self):
        if not self.txt_q.text().strip():
            QMessageBox.warning(self, "Encuesta", "Escribí la pregunta.")
        elif not 2 <= len(self.options()) <= 12:
            QMessageBox.warning(self, "Encuesta", "Poné entre 2 y 12 opciones distintas.")
        else:
            self.accept()

    def values(self) -> tuple:
        return self.txt_q.text().strip(), self.options(), self.chk_multi.isChecked()


# ──────────────────────────────────────────────────────────────────────────
# Destacados
# ──────────────────────────────────────────────────────────────────────────
class StarredDialog(QDialog):
    open_chat = Signal(str, str)   # (chat_jid, message_id)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Mensajes destacados")
        self.resize(520, 560)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.setSpacing(10)
        self.txt_search = QLineEdit()
        self.txt_search.setObjectName("searchField")
        self.txt_search.setPlaceholderText("Buscar en los destacados")
        self.txt_search.addAction(icon("search", C["text_3"]), QLineEdit.LeadingPosition)
        self.txt_search.setClearButtonEnabled(True)
        self.txt_search.textChanged.connect(self._render)
        lay.addWidget(self.txt_search)
        self.list = QListWidget()
        self.list.setObjectName("framedList")
        self.list.setWordWrap(True)
        self.list.itemDoubleClicked.connect(self._open)
        lay.addWidget(self.list, stretch=1)
        row = QHBoxLayout()
        self.lbl = QLabel("")
        self.lbl.setObjectName("subtle")
        row.addWidget(self.lbl)
        row.addStretch()
        b_open = QPushButton("Ir al mensaje")
        b_open.clicked.connect(lambda: self._open(self.list.currentItem()))
        b_un = QPushButton("Quitar destacado")
        b_un.clicked.connect(self._unstar)
        for b in (b_open, b_un):
            b.setCursor(Qt.PointingHandCursor)
            row.addWidget(b)
        lay.addLayout(row)
        self._render()

    def _render(self, *_):
        self.list.clear()
        msgs = get_starred_messages(self.txt_search.text())
        for m in msgs:
            when = datetime.fromtimestamp(m.get("timestamp") or 0).strftime("%d/%m/%Y %H:%M")
            who = "Tú" if m.get("from_me") else (m.get("sender_name") or m.get("chat_name") or "")
            body = (m.get("text") or m.get("transcript") or "").strip() or _preview_for(m.get("media_type") or "text", "")
            item = QListWidgetItem(f"⭐ {m.get('chat_name')} · {who} · {when}\n{body[:240]}")
            item.setData(Qt.UserRole, m)
            self.list.addItem(item)
        self.lbl.setText(f"{len(msgs)} destacado{'s' if len(msgs) != 1 else ''}" if msgs else
                         "Sin destacados. Clic derecho en un mensaje › Destacar.")

    def _open(self, item):
        if item:
            m = item.data(Qt.UserRole)
            self.open_chat.emit(m["chat_jid"], m["id"])

    def _unstar(self):
        item = self.list.currentItem()
        if item:
            set_message_starred(item.data(Qt.UserRole)["id"], False)
            self._render()


# ──────────────────────────────────────────────────────────────────────────
# Enviar a varios
# ──────────────────────────────────────────────────────────────────────────
class SendManyDialog(QDialog):
    def __init__(self, gateway, parent=None):
        super().__init__(parent)
        self.gateway = gateway
        self.setWindowTitle("Enviar a varios")
        self.setMinimumWidth(460)
        self._targets: List[dict] = []
        self._file: Optional[str] = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(10)
        lay.addWidget(_hint("El mismo mensaje llega a cada contacto por separado (no es un grupo ni una lista de difusión), "
                            "con una pausa entre envíos para no parecer spam. Sale aunque cierres la ventana."))
        self.btn_pick = QPushButton("Elegir contactos…")
        self.btn_pick.clicked.connect(self._pick)
        lay.addWidget(self.btn_pick)
        self.txt = QPlainTextEdit()
        self.txt.setPlaceholderText("Mensaje (o descripción del archivo)")
        self.txt.setFixedHeight(100)
        lay.addWidget(self.txt)
        frow = QHBoxLayout()
        self.btn_file = QPushButton("Adjuntar archivo…")
        self.btn_file.clicked.connect(self._choose_file)
        frow.addWidget(self.btn_file)
        self.lbl_file = QLabel("")
        self.lbl_file.setObjectName("subtle")
        frow.addWidget(self.lbl_file, stretch=1)
        lay.addLayout(frow)
        prow = QHBoxLayout()
        prow.addWidget(QLabel("Pausa entre envíos:"))
        self.spin_min = QSpinBox()
        self.spin_min.setRange(3, 300)
        self.spin_min.setValue(5)
        self.spin_min.setSuffix(" s")
        self.spin_max = QSpinBox()
        self.spin_max.setRange(3, 600)
        self.spin_max.setValue(12)
        self.spin_max.setSuffix(" s")
        prow.addWidget(self.spin_min)
        prow.addWidget(QLabel("a"))
        prow.addWidget(self.spin_max)
        prow.addStretch()
        lay.addLayout(prow)
        row, self.btn_send = _buttons(self, "Enviar", "btnSend")
        self.btn_send.clicked.connect(self._send)
        lay.addLayout(row)
        self._update()

    def preselect(self, chats: List[dict]):
        self._targets = chats
        self._update()

    def _update(self):
        n = len(self._targets)
        self.btn_pick.setText(f"Contactos elegidos: {n}" if n else "Elegir contactos…")

    def _pick(self):
        dlg = ChatPickerDialog("Enviar a…", [c["jid"] for c in self._targets], parent=self)
        if dlg.exec() == QDialog.Accepted:
            self._targets = dlg.selected_chats()
            self._update()

    def _choose_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Adjuntar archivo", "", "Todos los archivos (*.*)")
        if path:
            self._file = path
            self.lbl_file.setText(os.path.basename(path))

    def _send(self):
        text = self.txt.toPlainText().strip()
        if not self._targets:
            QMessageBox.warning(self, "Enviar a varios", "Elegí al menos un contacto.")
            return
        if not text and not self._file:
            QMessageBox.warning(self, "Enviar a varios", "Escribí un mensaje o adjuntá un archivo.")
            return
        media = {}
        if self._file:
            ext = os.path.splitext(self._file)[1].lower()
            if ext in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
                media["image_path"] = self._file
            elif ext in (".mp4", ".mov", ".mkv", ".avi"):
                media["video_path"] = self._file
            else:
                media["document_path"] = self._file
                media["document_name"] = os.path.basename(self._file)
        targets = [{"to": c["jid"], "chat_name": resolve_display_name(c)} for c in self._targets]
        res = self.gateway.send_to_many(targets, text, self.spin_min.value(), max(self.spin_min.value(), self.spin_max.value()), **media)
        if not res.get("success"):
            QMessageBox.warning(self, "Enviar a varios", res.get("error") or "No se pudo iniciar el envío.")
            return
        QMessageBox.information(self, "Enviar a varios",
                                f"Empezó el envío a {len(targets)} contacto(s). Va saliendo de a uno; podés seguir usando la app.")
        self.accept()


# ──────────────────────────────────────────────────────────────────────────
# Ver original (eliminado) / historial de ediciones
# ──────────────────────────────────────────────────────────────────────────
class OriginalDialog(QDialog):
    def __init__(self, msg: dict, mode: str, parent=None):
        """mode: 'revoked' (contenido original de un mensaje eliminado) o 'edits' (versiones de un mensaje editado)."""
        super().__init__(parent)
        self.setMinimumSize(420, 260)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(8)
        if mode == "revoked":
            self.setWindowTitle("Mensaje eliminado")
            orig = get_revoked_original(msg["id"])
            if not orig:
                lay.addWidget(_hint("No se guardó el contenido de este mensaje (se eliminó antes de activar el ajuste)."))
            else:
                kind = orig.get("media_type") or "text"
                lay.addWidget(QLabel("Contenido original:"))
                body = (orig.get("text") or orig.get("transcript") or "").strip() or _preview_for(kind, "")
                if kind not in ("text", "revoked") and orig.get("text"):
                    body = f"{_preview_for(kind, '')}\n{orig['text']}"
                view = QPlainTextEdit(body)
                view.setReadOnly(True)
                lay.addWidget(view, stretch=1)
                path = orig.get("media_path")
                if path and os.path.exists(path):
                    b = QPushButton("Abrir el archivo")
                    b.clicked.connect(lambda: os.startfile(path))
                    lay.addWidget(b)
        else:
            self.setWindowTitle("Historial de ediciones")
            versions = get_message_edits(msg["id"])
            lay.addWidget(QLabel("Versiones, de la más vieja a la actual:"))
            view = QPlainTextEdit()
            view.setReadOnly(True)
            chunks = []
            for i, v in enumerate(versions, 1):
                when = datetime.fromtimestamp(v.get("edited_at") or 0).strftime("%d/%m/%Y %H:%M")
                chunks.append(f"Versión {i} (cambiada el {when}):\n{v.get('text') or ''}")
            chunks.append(f"Actual:\n{msg.get('text') or ''}")
            view.setPlainText("\n\n".join(chunks) if versions else
                              "No se guardaron versiones anteriores de este mensaje.\n\nActual:\n" + (msg.get("text") or ""))
            lay.addWidget(view, stretch=1)
        close = QPushButton("Cerrar")
        close.clicked.connect(self.accept)
        lay.addWidget(close, alignment=Qt.AlignRight)


# ──────────────────────────────────────────────────────────────────────────
# Pestaña «Mensajería» de la configuración
# ──────────────────────────────────────────────────────────────────────────
def build_messaging_tab(settings, gateway) -> QWidget:
    """Página para Configuración: ajuste de conservar eliminados + accesos a las ventanas de arriba."""
    from PySide6.QtWidgets import QGroupBox
    from app.config import get_pref, set_pref
    tab, layout = settings._page()

    g1 = QGroupBox("Mensajes eliminados y editados")
    l1 = QVBoxLayout(g1)
    chk = QCheckBox("Conservar lo que se elimina y las versiones anteriores de lo que se edita")
    chk.setChecked(get_pref("keep_deleted"))
    chk.toggled.connect(lambda on: set_pref("keep_deleted", on))
    l1.addWidget(chk)
    l1.addWidget(_hint("En la burbuja aparece «Eliminado» con «Ver original», o «Editado» con el historial de versiones. "
                       "Solo se guarda en esta PC, desde que se activa el ajuste."))
    layout.addWidget(g1)

    g2 = QGroupBox("Herramientas de mensajería")
    l2 = QVBoxLayout(g2)

    def button(text, hint, fn):
        b = QPushButton(text)
        b.setCursor(Qt.PointingHandCursor)
        b.clicked.connect(fn)
        l2.addWidget(b, alignment=Qt.AlignLeft)
        l2.addWidget(_hint(hint))

    button("Respuestas automáticas…", "Por horario o con un modo manual («Estoy manejando»). Funcionan con la ventana cerrada.",
           lambda: AutoReplyDialog(gateway, settings).exec())
    button("Plantillas y atajos…", "Escribí /atajo en un chat para insertar un texto guardado.",
           lambda: TemplatesDialog(settings).exec())
    button("Mensajes destacados…", "Todos los mensajes que marcaste con la estrella, con buscador.",
           lambda: settings.starred_requested.emit())
    button("Enviar a varios…", "Un mismo mensaje o archivo a muchos contactos, por separado y con pausas.",
           lambda: SendManyDialog(gateway, settings).exec())
    layout.addWidget(g2)
    layout.addStretch()
    return tab


