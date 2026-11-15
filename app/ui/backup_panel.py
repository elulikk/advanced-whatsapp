"""Pestaña «Copias y sincronización» de Configuración: copia cifrada .awbackup, copia automática, sincronización con el
celular y envío de archivos al celular. La lógica está en app/backup.py y app/sync.py; acá solo hay pantalla e hilos."""
import os
import threading
import time

from PySide6.QtCore import QObject, QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QFileDialog, QGroupBox, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QMessageBox, QPushButton, QSpinBox,
    QVBoxLayout, QWidget, QScrollArea, QFrame,
)

from app import backup, secret_store, sync
from app.awcrypto import BackupError
from app.ui.theme import C

AUTO_SECRET = "backup_auto"


def ask_password(parent, title: str, label: str, confirm: bool = False) -> str:
    """Pide una contraseña (dos veces si `confirm`). Devuelve '' si se cancela o no coinciden."""
    pw, ok = QInputDialog.getText(parent, title, label, QLineEdit.Password)
    if not ok or not pw:
        return ""
    if confirm:
        if len(pw) < 6:
            QMessageBox.warning(parent, title, "Usá al menos 6 caracteres.")
            return ""
        again, ok = QInputDialog.getText(parent, title, "Repetí la contraseña:", QLineEdit.Password)
        if not ok or again != pw:
            if ok:
                QMessageBox.warning(parent, title, "Las contraseñas no coinciden.")
            return ""
    return pw


class _Worker(QObject):
    """Corre una función en un hilo y devuelve el resultado al hilo de la interfaz."""
    done = Signal(object, object)   # resultado, excepción

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self._fn = fn

    def start(self):
        def run():
            try:
                self.done.emit(self._fn(), None)
            except Exception as e:   # noqa: BLE001
                self.done.emit(None, e)
        threading.Thread(target=run, daemon=True).start()


class BackupTab(QWidget):
    sync_applied = Signal(int)       # cuántos cambios entraron desde el celular (refrescar lista de chats y tags)

    def __init__(self, gateway, parent=None):
        super().__init__(parent)
        self.gateway = gateway
        self._workers = []
        self._build()
        self._load()
        # Sincronización y copia automática: se revisan solas, con la app abierta o en la bandeja
        self._timer = QTimer(self)
        self._timer.setInterval(60_000)
        self._timer.timeout.connect(self._periodic)
        self._timer.start()
        QTimer.singleShot(45_000, self._periodic)

    # ── Armado ──
    def _build(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        body = QWidget()
        lay = QVBoxLayout(body)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(12)
        scroll.setWidget(body)
        outer.addWidget(scroll)

        def hint(t):
            lb = QLabel(t)
            lb.setObjectName("hint")
            lb.setWordWrap(True)
            return lb

        def button(text, slot):
            b = QPushButton(text)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(slot)
            return b

        # Copia manual
        g = QGroupBox("Copia de seguridad")
        gl = QVBoxLayout(g)
        gl.addWidget(hint("Guarda en un solo archivo cifrado (.awbackup) tus chats, tags, chats bloqueados, nombres y notas de "
                          "contactos, stickers, mensajes programados y configuración. Sirve para pasar todo a otra PC o al "
                          "celular: el mismo archivo se restaura en Advanced WhatsApp de Android. Las claves de IA solo se "
                          "incluyen si lo elegís."))
        self.chk_secrets = QCheckBox("Incluir las claves de API de IA (quedan cifradas dentro del archivo)")
        gl.addWidget(self.chk_secrets)
        row = QHBoxLayout()
        row.addWidget(button("Crear copia…", self._create))
        row.addWidget(button("Restaurar copia…", self._restore))
        row.addStretch()
        gl.addLayout(row)
        self.lbl_manual = QLabel("")
        self.lbl_manual.setObjectName("subtle")
        self.lbl_manual.setWordWrap(True)
        gl.addWidget(self.lbl_manual)
        lay.addWidget(g)

        # Copia automática
        g = QGroupBox("Copia automática")
        gl = QVBoxLayout(g)
        self.chk_auto = QCheckBox("Hacer una copia automática cada")
        self.spin_days = QSpinBox()
        self.spin_days.setRange(1, 90)
        self.spin_days.setSuffix(" días")
        self.spin_keep = QSpinBox()
        self.spin_keep.setRange(1, 50)
        r1 = QHBoxLayout()
        r1.addWidget(self.chk_auto)
        r1.addWidget(self.spin_days)
        r1.addWidget(QLabel("y guardar las últimas"))
        r1.addWidget(self.spin_keep)
        r1.addStretch()
        gl.addLayout(r1)
        r2 = QHBoxLayout()
        self.edit_folder = QLineEdit()
        self.edit_folder.setReadOnly(True)
        r2.addWidget(QLabel("Carpeta:"))
        r2.addWidget(self.edit_folder, 1)
        r2.addWidget(button("Cambiar…", self._pick_folder))
        gl.addLayout(r2)
        self.chk_auto_secrets = QCheckBox("Incluir las claves de API de IA")
        gl.addWidget(self.chk_auto_secrets)
        r3 = QHBoxLayout()
        r3.addWidget(button("Contraseña de la copia automática…", self._set_auto_password))
        r3.addWidget(button("Hacer una copia ahora", self._auto_now))
        r3.addStretch()
        gl.addLayout(r3)
        self.lbl_auto = QLabel("")
        self.lbl_auto.setObjectName("subtle")
        self.lbl_auto.setWordWrap(True)
        gl.addWidget(self.lbl_auto)
        lay.addWidget(g)
        for w in (self.chk_auto, self.chk_auto_secrets):
            w.toggled.connect(self._save_auto)
        for w in (self.spin_days, self.spin_keep):
            w.valueChanged.connect(self._save_auto)

        # Sincronización
        g = QGroupBox("Sincronizar con el celular")
        gl = QVBoxLayout(g)
        gl.addWidget(hint("Mantiene iguales en la PC y en el celular los tags, los chats bloqueados, los nombres y notas de "
                          "contactos, los stickers y algunas preferencias (gana el último cambio). Viaja cifrado por tu chat "
                          "«Mi número» de WhatsApp, sin servidores de terceros: en las dos apps hay que activarlo y poner la "
                          "misma clave. Los mensajes y la configuración sensible no se sincronizan."))
        self.chk_sync = QCheckBox("Sincronizar automáticamente")
        self.chk_sync.toggled.connect(self._toggle_sync)
        gl.addWidget(self.chk_sync)
        r = QHBoxLayout()
        r.addWidget(button("Clave de sincronización…", self._set_sync_password))
        r.addWidget(button("Sincronizar ahora", self._sync_now))
        r.addStretch()
        gl.addLayout(r)
        self.lbl_sync = QLabel("")
        self.lbl_sync.setObjectName("subtle")
        self.lbl_sync.setWordWrap(True)
        gl.addWidget(self.lbl_sync)
        lay.addWidget(g)

        # Enviar archivos
        g = QGroupBox("Enviar archivos al celular")
        gl = QVBoxLayout(g)
        gl.addWidget(hint("Manda archivos de la PC a tu chat «Mi número»; en el celular aparecen con el botón «Guardar en "
                          "Descargas»."))
        gl.addWidget(button("Enviar al celular…", self.open_send_dialog), 0, Qt.AlignLeft)
        lay.addWidget(g)
        lay.addStretch()

    # ── Estado guardado ──
    def _load(self):
        self._loading = True
        self.chk_auto.setChecked(backup.get_setting("BACKUP", "auto", "false") == "true")
        self.spin_days.setValue(int(backup.get_setting("BACKUP", "every_days", "7") or 7))
        self.spin_keep.setValue(int(backup.get_setting("BACKUP", "keep", "5") or 5))
        self.edit_folder.setText(backup.get_setting("BACKUP", "folder", "") or backup.default_backup_dir())
        self.chk_auto_secrets.setChecked(backup.get_setting("BACKUP", "auto_secrets", "false") == "true")
        self.chk_sync.setChecked(backup.get_setting("SYNC", "enabled", "false") == "true")
        self._loading = False
        self._refresh_labels()

    def _refresh_labels(self):
        last = float(backup.get_setting("BACKUP", "last_auto", "0") or 0)
        pw = "con contraseña" if secret_store.get(AUTO_SECRET) else "falta elegir la contraseña"
        self.lbl_auto.setText(
            f"Última copia automática: {time.strftime('%d/%m/%Y %H:%M', time.localtime(last)) if last else 'todavía ninguna'} "
            f"· {pw}.")
        info = sync.last_sync_info()
        fmt = lambda ts: time.strftime("%d/%m/%Y %H:%M", time.localtime(ts)) if ts else "nunca"  # noqa: E731
        clave = "" if sync.has_password() else " Falta elegir la clave de sincronización."
        self.lbl_sync.setText(f"Último envío: {fmt(info['sent_at'])} · última recepción: {fmt(info['received_at'])}.{clave}")

    def _save_auto(self, *_):
        if self._loading:
            return
        backup.set_setting("BACKUP", "auto", "true" if self.chk_auto.isChecked() else "false")
        backup.set_setting("BACKUP", "every_days", self.spin_days.value())
        backup.set_setting("BACKUP", "keep", self.spin_keep.value())
        backup.set_setting("BACKUP", "auto_secrets", "true" if self.chk_auto_secrets.isChecked() else "false")
        if self.chk_auto.isChecked() and not secret_store.get(AUTO_SECRET):
            self._set_auto_password()

    # ── Copia manual ──
    def _run(self, fn, on_done):
        w = _Worker(fn, self)
        self._workers.append(w)

        def finish(res, err):
            self._workers.remove(w)
            on_done(res, err)
        w.done.connect(finish)
        w.start()

    def _create(self):
        default = os.path.join(os.path.expanduser("~"), "Documents", time.strftime("AdvancedWhatsapp-%Y%m%d.awbackup"))
        path, _ = QFileDialog.getSaveFileName(self, "Guardar copia de seguridad", default, "Copia de Advanced WhatsApp (*.awbackup)")
        if not path:
            return
        if not path.lower().endswith(backup.BACKUP_EXT):
            path += backup.BACKUP_EXT
        pw = ask_password(self, "Contraseña de la copia", "Elegí una contraseña (sin ella no se puede restaurar):", confirm=True)
        if not pw:
            return
        self.lbl_manual.setText("Creando la copia…")
        secrets = self.chk_secrets.isChecked()

        def done(res, err):
            if err:
                self.lbl_manual.setText(f"No se pudo crear la copia: {err}")
            else:
                self.lbl_manual.setText(f"Copia creada: {res['path']} ({res['size'] / 1024 / 1024:.1f} MB).")
        self._run(lambda: backup.create_backup(path, pw, include_secrets=secrets), done)

    def _restore(self):
        path, _ = QFileDialog.getOpenFileName(self, "Elegir copia de seguridad", os.path.expanduser("~"),
                                              "Copia de Advanced WhatsApp (*.awbackup)")
        if not path:
            return
        pw = ask_password(self, "Restaurar copia", "Contraseña de la copia:")
        if not pw:
            return
        try:
            info = backup.inspect_backup(path, pw)
        except BackupError as e:
            QMessageBox.warning(self, "Restaurar copia", str(e))
            return
        when = time.strftime("%d/%m/%Y %H:%M", time.localtime((info.get("created_at") or 0) / 1000))
        origin = "PC" if (info.get("device") or {}).get("platform") == "pc" else "celular"
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Question)
        box.setWindowTitle("Restaurar copia")
        box.setText(f"Copia del {when} (hecha en el {origin}).\n\n"
                    "«Combinar» agrega lo que falta y se queda con lo más nuevo de cada dato.\n"
                    "«Reemplazar todo» deja la app idéntica a la copia: se pierde lo que hayas hecho después.")
        b_merge = box.addButton("Combinar (recomendado)", QMessageBox.AcceptRole)
        b_repl = box.addButton("Reemplazar todo", QMessageBox.DestructiveRole)
        box.addButton("Cancelar", QMessageBox.RejectRole)
        box.exec()
        clicked = box.clickedButton()
        if clicked not in (b_merge, b_repl):
            return
        mode = "merge" if clicked is b_merge else "replace"
        if mode == "replace" and QMessageBox.question(
                self, "Reemplazar todo", "Se van a borrar los chats y mensajes actuales de esta PC y quedarán los de la copia. "
                "¿Seguro?") != QMessageBox.Yes:
            return
        self.lbl_manual.setText("Restaurando…")

        def done(res, err):
            if err:
                self.lbl_manual.setText(f"No se pudo restaurar: {err}")
                return
            db = res.get("db") or {}
            self.lbl_manual.setText(f"Listo: {db.get('messages', 0)} mensajes y {db.get('chats', 0)} chats agregados; "
                                    "las secciones se combinaron. Conviene reabrir la app.")
            self.sync_applied.emit(1)
        self._run(lambda: backup.restore_backup(path, pw, mode=mode), done)

    # ── Copia automática ──
    def _pick_folder(self):
        d = QFileDialog.getExistingDirectory(self, "Carpeta de las copias automáticas", self.edit_folder.text())
        if d:
            self.edit_folder.setText(d)
            backup.set_setting("BACKUP", "folder", d)

    def _set_auto_password(self):
        pw = ask_password(self, "Copia automática", "Contraseña de las copias automáticas:", confirm=True)
        if pw:
            secret_store.set(AUTO_SECRET, pw)
            self._refresh_labels()
        elif not secret_store.get(AUTO_SECRET) and self.chk_auto.isChecked():
            self.chk_auto.setChecked(False)

    def _auto_now(self):
        self._auto_backup(force=True)

    def _auto_backup(self, force: bool = False):
        pw = secret_store.get(AUTO_SECRET)
        if not pw:
            if force:
                self.lbl_auto.setText("Primero elegí la contraseña de la copia automática.")
            return
        folder = self.edit_folder.text() or backup.default_backup_dir()
        keep = self.spin_keep.value()
        secrets = self.chk_auto_secrets.isChecked()

        def done(res, err):
            if err:
                self.lbl_auto.setText(f"La copia automática falló: {err}")
                return
            backup.set_setting("BACKUP", "last_auto", str(time.time()))
            self._refresh_labels()
        self._run(lambda: backup.run_auto_backup(pw, folder, keep, secrets), done)

    # ── Sincronización ──
    def _toggle_sync(self, on: bool):
        if self._loading:
            return
        if on and not sync.has_password():
            if not self._set_sync_password():
                self._loading = True
                self.chk_sync.setChecked(False)
                self._loading = False
                return
        sync.set_enabled(on)
        if on:
            self._sync_now()

    def _set_sync_password(self) -> bool:
        pw = ask_password(self, "Clave de sincronización",
                          "Elegí la clave (tiene que ser la misma en la app del celular):", confirm=True)
        if not pw:
            return False
        sync.set_password(pw)
        self._refresh_labels()
        return True

    def _sync_now(self):
        if not sync.has_password() and not self._set_sync_password():
            return
        sync.set_enabled(True)
        self._loading = True
        self.chk_sync.setChecked(True)
        self._loading = False
        self.lbl_sync.setText("Sincronizando…")

        def done(res, err):
            if err or not res.get("sent") and res.get("error"):
                self.lbl_sync.setText(f"No se pudo sincronizar: {err or res.get('error')}")
            elif res.get("reason") == "offline":
                self.lbl_sync.setText("WhatsApp no está conectado todavía.")
            else:
                self._refresh_labels()
        self._run(lambda: sync.send_now(self.gateway, full=True), done)

    def on_sync_received(self, msg: dict):
        """Llegó un documento de sincronización del celular (señal del webhook)."""
        def done(res, err):
            self._refresh_labels()
            if not err and res and res.get("applied"):
                self.sync_applied.emit(res["applied"])
        self._run(lambda: sync.handle_incoming(self.gateway, msg), done)

    def _periodic(self):
        if self._workers:
            return
        if backup.get_setting("BACKUP", "auto", "false") == "true":
            last = float(backup.get_setting("BACKUP", "last_auto", "0") or 0)
            if backup.auto_backup_due(last, self.spin_days.value()):
                self._auto_backup()
                return
        if sync.enabled():
            self._run(lambda: sync.tick(self.gateway), lambda r, e: self._refresh_labels())

    # ── Enviar al celular ──
    def open_send_dialog(self, paths=None):
        from app.ui.send_to_phone import SendToPhoneDialog
        dlg = SendToPhoneDialog(self.gateway, paths or [], self)
        dlg.exec()
