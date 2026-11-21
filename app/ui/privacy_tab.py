"""
Advanced WhatsApp - Pestaña «Privacidad y llamadas» de Configuración
====================================================================
Modo fantasma, quién ve tu última vez y tu «en línea» (ajuste de la cuenta, vale también en el celular), filtro de
llamadas, estados que el contacto elimina y avisos de mensajes borrados y de vistas de tus estados.
El filtro de llamadas y los estados eliminados los aplica el gateway solo: siguen andando con la ventana cerrada.
"""

import threading

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QGroupBox, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton,
    QVBoxLayout, QWidget,
)

from app.config import get_pref, set_pref

LAST_SEEN = [("all", "Todos"), ("contacts", "Mis contactos"),
             ("contact_blacklist", "Mis contactos, excepto… (la lista se arma en el celular)"), ("none", "Nadie")]
ONLINE = [("all", "Todos"), ("match_last_seen", "Los mismos que ven mi última vez")]
CALL_MODES = [("off", "No rechazar ninguna"),
              ("unknown", "Rechazar las de números que no tengo agendados"),
              ("blocklist", "Rechazar solo las de los números de la lista"),
              ("allowlist", "Rechazar todas, menos las de los números de la lista")]
CALL_LIST_HINT = {
    "off": "",
    "unknown": "Los números de la lista suenan aunque no estén agendados.",
    "blocklist": "Estos números no te pueden llamar.",
    "allowlist": "Solo estos números te pueden llamar.",
}


class _Bridge(QObject):
    """Resultados de los hilos de red, de vuelta al hilo de la interfaz."""
    gateway_prefs = Signal(dict)
    privacy = Signal(dict)
    privacy_saved = Signal(dict)
    calls_saved = Signal(dict)


def _hint(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setObjectName("hint")
    lbl.setWordWrap(True)
    return lbl


def _run(fn, signal, *args, **kwargs):
    def worker():
        try:
            res = fn(*args, **kwargs)
        except Exception as e:   # nunca tumbar la interfaz por un error de red
            res = {"success": False, "error": str(e)}
        signal.emit(res if isinstance(res, dict) else {"success": False})
    threading.Thread(target=worker, daemon=True, name="advancedwhatsapp-privacy").start()


class PrivacyTab(QWidget):
    def __init__(self, settings, gateway, parent=None):
        super().__init__(parent)
        self.gateway = gateway
        self._bridge = _Bridge(self)
        self._bridge.gateway_prefs.connect(self._on_gateway_prefs)
        self._bridge.privacy.connect(self._on_privacy)
        self._bridge.privacy_saved.connect(self._on_privacy_saved)
        self._bridge.calls_saved.connect(self._on_calls_saved)
        self._checks = {}
        self._loading = False
        self._privacy_note = ""   # resultado del último cambio, a mostrar cuando llega la relectura

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        page, layout = settings._page()
        outer.addWidget(page)

        # ── Modo fantasma y lo que se manda desde esta PC ──
        g1 = QGroupBox("Modo fantasma")
        l1 = QVBoxLayout(g1)
        l1.setSpacing(6)
        self._check(l1, "ghost_mode", "Activar el modo fantasma",
                    "Desde esta PC no se mandan tildes azules, ni «escribiendo…», ni figurás «en línea», y los estados "
                    "que mires no le avisan al contacto. Lo que hagas en el celular sigue su propio ajuste.")
        self._check(l1, "status_receipts", "Al ver un estado, avisar al contacto que lo viste",
                    "Igual que en el celular. Si lo desactivás, los estados se marcan como vistos solo en esta PC.")
        self._check(l1, "typing_indicator", "Mostrar \"escribiendo…\" a la otra persona mientras escribo")
        l1.addWidget(_hint("Sin el modo fantasma, Advanced WhatsApp figura \"en línea\" solo mientras la ventana está al "
                           "frente, como WhatsApp Desktop. Para no mandar tildes azules en un solo chat: clic derecho sobre "
                           "el chat › Lectura invisible."))
        layout.addWidget(g1)

        # ── Privacidad de la cuenta ──
        g2 = QGroupBox("Privacidad de la cuenta (también en el celular)")
        l2 = QVBoxLayout(g2)
        l2.setSpacing(6)
        self.combo_last = self._combo(l2, "Quién ve mi última vez", LAST_SEEN, "last")
        self.combo_online = self._combo(l2, "Quién ve cuándo estoy en línea", ONLINE, "online")
        self.chk_read_account = QCheckBox("Confirmaciones de lectura (tildes azules) en toda la cuenta")
        self.chk_read_account.clicked.connect(
            lambda on: self._save_privacy(readreceipts="all" if on else "none"))
        l2.addWidget(self.chk_read_account)
        l2.addWidget(_hint("Es el mismo ajuste del celular (Ajustes › Privacidad) y WhatsApp lo hace recíproco: si ocultás "
                           "tu última vez o tus tildes azules, tampoco ves las de los demás. Con «Nadie» en la última vez "
                           "y «Los mismos…» en línea, nadie te ve conectado."))
        self.lbl_privacy = _hint("")
        l2.addWidget(self.lbl_privacy)
        layout.addWidget(g2)

        # ── Llamadas ──
        g3 = QGroupBox("Filtro de llamadas")
        l3 = QVBoxLayout(g3)
        l3.setSpacing(6)
        self.combo_calls = QComboBox()
        for value, text in CALL_MODES:
            self.combo_calls.addItem(text, value)
        self.combo_calls.currentIndexChanged.connect(self._sync_call_widgets)
        l3.addWidget(self.combo_calls)
        self.lbl_call_list = _hint("")
        l3.addWidget(self.lbl_call_list)
        self.txt_call_list = QPlainTextEdit()
        self.txt_call_list.setPlaceholderText("Un número por línea, con código de país y de área (ej: 5492944123456)")
        self.txt_call_list.setFixedHeight(90)
        l3.addWidget(self.txt_call_list)
        row = QHBoxLayout()
        self.btn_calls = QPushButton("Guardar filtro")
        self.btn_calls.setCursor(Qt.PointingHandCursor)
        self.btn_calls.clicked.connect(self._save_calls)
        row.addWidget(self.btn_calls)
        self.lbl_calls = QLabel("")
        self.lbl_calls.setObjectName("subtle")
        row.addWidget(self.lbl_calls, 1)
        l3.addLayout(row)
        l3.addWidget(_hint("Una llamada filtrada se corta sola en todos tus dispositivos (como tocar «Rechazar» en el celular) "
                           "y queda anotada en el chat. Funciona con la ventana cerrada, mientras Advanced WhatsApp esté "
                           "abierto en la bandeja."))
        layout.addWidget(g3)

        # ── Estados y avisos ──
        g4 = QGroupBox("Borrados y estados")
        l4 = QVBoxLayout(g4)
        l4.setSpacing(6)
        self.chk_keep_statuses = QCheckBox("Conservar los estados que el contacto elimina")
        self.chk_keep_statuses.clicked.connect(
            lambda on: _run(self.gateway.set_gateway_prefs, self._bridge.gateway_prefs, {"keep_deleted_statuses": on}))
        l4.addWidget(self.chk_keep_statuses)
        l4.addWidget(_hint("Siguen en Estados hasta que se cumplen las 24 h, marcados como eliminados, y se pueden guardar."))
        self._check(l4, "notify_deleted", "Avisar cuando alguien elimina un mensaje que me mandó",
                    "La notificación muestra lo que decía (si está activado «Conservar lo que se elimina», en Mensajería).")
        self._check(l4, "notify_status_views", "Avisar cuando alguien ve uno de mis estados",
                    "Si lo ven varios seguidos llega un solo aviso con todos los nombres.")
        layout.addWidget(g4)
        layout.addStretch()

        self._sync_pref_enabled()
        self._sync_call_widgets()

    # ── Construcción ──

    def _check(self, lay, key, text, hint=None):
        cb = QCheckBox(text)
        cb.setChecked(get_pref(key))
        cb.toggled.connect(lambda on, k=key: self._on_pref(k, on))
        lay.addWidget(cb)
        if hint:
            lay.addWidget(_hint(hint))
        self._checks[key] = cb
        return cb

    def _combo(self, lay, label, items, field):
        row = QHBoxLayout()
        row.addWidget(QLabel(label))
        combo = QComboBox()
        for value, text in items:
            combo.addItem(text, value)
        combo.setEnabled(False)
        combo.activated.connect(lambda _i, c=combo, f=field: self._save_privacy(**{f: c.currentData()}))
        row.addWidget(combo, 1)
        lay.addLayout(row)
        return combo

    # ── Preferencias locales ──

    def _on_pref(self, key: str, on: bool):
        set_pref(key, on)
        if key == "ghost_mode":
            self._sync_pref_enabled()
            # Se aplica ya: fantasma = fuera de línea; al salir, en línea si la ventana está al frente
            self.gateway.set_presence(QApplication.activeWindow() is not None)

    def _sync_pref_enabled(self):
        ghost = get_pref("ghost_mode")
        for key in ("status_receipts", "typing_indicator"):
            self._checks[key].setEnabled(not ghost)

    def showEvent(self, e):
        super().showEvent(e)
        for key, cb in self._checks.items():
            cb.blockSignals(True)
            cb.setChecked(get_pref(key))
            cb.blockSignals(False)
        self._sync_pref_enabled()
        if not self._loading:
            self._loading = True
            self.lbl_privacy.setText("Leyendo la privacidad de la cuenta…")
            _run(self.gateway.get_gateway_prefs, self._bridge.gateway_prefs)
            _run(self.gateway.get_privacy, self._bridge.privacy)

    # ── Ajustes del gateway ──

    def _on_gateway_prefs(self, res: dict):
        if not res.get("success"):
            self.lbl_calls.setText("El gateway no responde: no se pudo leer el filtro.")
            return
        self.chk_keep_statuses.setChecked(bool(res.get("keep_deleted_statuses", True)))
        calls = res.get("calls") or {}
        idx = self.combo_calls.findData(calls.get("mode") or "off")
        self.combo_calls.setCurrentIndex(max(0, idx))
        if not self.txt_call_list.hasFocus():
            self.txt_call_list.setPlainText("\n".join(calls.get("list") or []))
        self._sync_call_widgets()

    def _sync_call_widgets(self, *_):
        mode = self.combo_calls.currentData() or "off"
        self.lbl_call_list.setText(CALL_LIST_HINT.get(mode, ""))
        self.lbl_call_list.setVisible(mode != "off")
        self.txt_call_list.setVisible(mode != "off")

    def _save_calls(self):
        numbers = []
        for line in self.txt_call_list.toPlainText().splitlines():
            digits = "".join(ch for ch in line if ch.isdigit())
            if len(digits) >= 6:
                numbers.append(digits)
        self.btn_calls.setEnabled(False)
        self.lbl_calls.setText("Guardando…")
        _run(self.gateway.set_gateway_prefs, self._bridge.calls_saved,
             {"calls": {"mode": self.combo_calls.currentData() or "off", "list": numbers}})

    def _on_calls_saved(self, res: dict):
        self.btn_calls.setEnabled(True)
        if res.get("success"):
            self.lbl_calls.setText("Filtro guardado.")
            self._on_gateway_prefs(res)
        else:
            self.lbl_calls.setText("No se pudo guardar: " + str(res.get("error") or "el gateway no responde"))

    # ── Privacidad de la cuenta ──

    def _on_privacy(self, res: dict):
        self._loading = False
        ok = bool(res.get("success"))
        for combo, field in ((self.combo_last, "last"), (self.combo_online, "online")):
            combo.setEnabled(ok)
            idx = combo.findData(res.get(field)) if ok else -1
            if idx >= 0:
                combo.setCurrentIndex(idx)
        self.chk_read_account.setEnabled(ok)
        note, self._privacy_note = self._privacy_note, ""
        if ok:
            self.chk_read_account.setChecked(res.get("readreceipts") != "none")
            self.lbl_privacy.setText(note)
        else:
            self.lbl_privacy.setText(note or "Conectá WhatsApp para ver y cambiar estos ajustes.")

    def _save_privacy(self, **values):
        for w in (self.combo_last, self.combo_online, self.chk_read_account):
            w.setEnabled(False)
        self.lbl_privacy.setText("Guardando en WhatsApp…")
        _run(self.gateway.set_privacy, self._bridge.privacy_saved, **values)

    def _on_privacy_saved(self, res: dict):
        # Se relee siempre: WhatsApp devuelve el valor real (y si falló, los combos vuelven a lo que había)
        self._privacy_note = ("Guardado." if res.get("success")
                              else "No se pudo cambiar: " + str(res.get("error") or "sin respuesta"))
        _run(self.gateway.get_privacy, self._bridge.privacy)
