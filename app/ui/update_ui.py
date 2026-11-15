"""
Advanced WhatsApp - Interfaz de actualizaciones
===============================================
- UpdateController: busca (al abrir con demora y cada 12 h, en hilo aparte), baja el Setup y lo lanza.
- UpdateBar: aviso fino arriba de la ventana ("Hay una versión nueva: 1.1.0 · Ver novedades · Actualizar").
- UpdateAboutBox: bloque de Ajustes > Acerca de (casilla, botón "Buscar actualizaciones", estado y notas).
"""

import threading
import time

from PySide6.QtCore import QObject, QTimer, Signal, Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QDialog, QFrame, QGroupBox, QHBoxLayout, QLabel, QPlainTextEdit, QProgressBar,
    QPushButton, QVBoxLayout,
)

from app import updater
from app.config import APP_VERSION, FROZEN, get_pref, set_pref, get_setting
from app.ui.theme import C


def configured_repo() -> str:
    """Repo de los releases: config.ini [GENERAL] update_repo (dueño/repo), por defecto el oficial."""
    return updater.normalize_repo(get_setting("update_repo", updater.DEFAULT_REPO)) or updater.DEFAULT_REPO


class UpdateController(QObject):
    update_found = Signal(object)        # UpdateInfo
    up_to_date = Signal()
    check_failed = Signal(str)
    checking = Signal(bool)
    progress = Signal(int, int)          # bytes bajados, total
    download_failed = Signal(str)
    state_changed = Signal()
    install_ready = Signal(str)

    def __init__(self, window, parent=None):
        super().__init__(parent or window)
        self._window = window
        self.info = None                 # última versión nueva encontrada
        self.busy = False                # buscando
        self.downloading = False
        self._cancel = False
        self.last_error = ""
        self.last_check = 0.0
        self.update_found.connect(self._remember)
        self.install_ready.connect(self._install)
        self._timer = QTimer(self)
        self._timer.setInterval(updater.CHECK_INTERVAL_S * 1000)
        self._timer.timeout.connect(lambda: self.check(manual=False))

    def start(self):
        """Primera búsqueda con demora (no frena el arranque) y luego cada 12 h."""
        self._timer.start()
        QTimer.singleShot(updater.STARTUP_DELAY_MS, lambda: self.check(manual=False))

    def _remember(self, info):
        self.info = info
        self.state_changed.emit()

    # ── Búsqueda ──
    def check(self, manual: bool = True):
        if self.busy or self.downloading:
            return
        if not manual and not get_pref("auto_update"):
            return
        self.busy = True
        self.last_error = ""
        self.checking.emit(True)
        threading.Thread(target=self._check_worker, args=(manual,), daemon=True, name="advancedwhatsapp-update-check").start()

    def _check_worker(self, manual: bool):
        try:
            info = updater.check_for_update(configured_repo(), APP_VERSION)
        except updater.UpdateError as e:
            self._finish_check()
            self.last_error = str(e)
            if manual:
                self.check_failed.emit(str(e))
            return
        except Exception as e:
            self._finish_check()
            self.last_error = f"Error inesperado al buscar: {e}"
            if manual:
                self.check_failed.emit(self.last_error)
            return
        self._finish_check()
        if info:
            self.update_found.emit(info)
        else:
            self.info = None
            self.up_to_date.emit()

    def _finish_check(self):
        self.busy = False
        self.last_check = time.time()
        self.checking.emit(False)

    # ── Instalación ──
    @property
    def can_install(self) -> bool:
        return FROZEN

    def show_notes(self):
        if self.info:
            NotesDialog(self.info, self._window).exec()

    def start_update(self):
        """Empaquetada: baja el Setup y lo lanza. En desarrollo solo abre la página del release."""
        info = self.info
        if not info or self.downloading:
            return
        if not self.can_install:
            if info.page_url:
                QDesktopServices.openUrl(QUrl(info.page_url))
            return
        self.downloading = True
        self._cancel = False
        self.state_changed.emit()
        threading.Thread(target=self._download_worker, args=(info,), daemon=True, name="advancedwhatsapp-update-download").start()

    def cancel_download(self):
        self._cancel = True

    def _download_worker(self, info):
        last = [0.0]

        def prog(done, total):
            now = time.monotonic()
            if now - last[0] > 0.1 or done == total:
                last[0] = now
                self.progress.emit(done, total)
        try:
            path = updater.download_installer(info, prog, lambda: self._cancel)
        except updater.UpdateError as e:
            self.downloading = False
            self.state_changed.emit()
            self.download_failed.emit(str(e))
            return
        except Exception as e:
            self.downloading = False
            self.state_changed.emit()
            self.download_failed.emit(f"No se pudo bajar la actualización: {e}")
            return
        self.install_ready.emit(path)   # de vuelta en el hilo de Qt

    def _install(self, path: str):
        """Cierra la app (y el gateway) con orden y lanza el instalador en silencio."""
        w = self._window
        try:
            w._quitting = True
            w._save_geometry()
            w.shutdown()
            try:   # un gateway que no arrancó esta app también traba los archivos: pedirle que salga
                from app import gateway_manager as gm
                gm._http.post(f"{gm.GATEWAY_URL}/shutdown", timeout=2)
            except Exception:
                pass
            updater.launch_installer(path)
        except Exception as e:
            self.downloading = False
            self.state_changed.emit()
            self.download_failed.emit(f"No se pudo iniciar el instalador: {e}")
            return
        QApplication.instance().quit()


class NotesDialog(QDialog):
    def __init__(self, info, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Novedades de la versión {info.version}")
        self.resize(520, 420)
        lay = QVBoxLayout(self)
        box = QPlainTextEdit()
        box.setReadOnly(True)
        box.setPlainText(info.notes or "Esta versión no trae notas.")
        lay.addWidget(box)
        row = QHBoxLayout()
        if info.page_url:
            b = QPushButton("Ver en GitHub")
            b.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(info.page_url)))
            row.addWidget(b)
        row.addStretch()
        close = QPushButton("Cerrar")
        close.clicked.connect(self.accept)
        row.addWidget(close)
        lay.addLayout(row)


def _link_button(text: str) -> QPushButton:
    b = QPushButton(text)
    b.setCursor(Qt.PointingHandCursor)
    b.setFlat(True)
    b.setStyleSheet(f"QPushButton {{ color: {C['link']}; background: transparent; border: none; padding: 2px 6px; }}"
                    f"QPushButton:hover {{ text-decoration: underline; }}")
    return b


class UpdateBar(QFrame):
    """Aviso no intrusivo sobre la ventana; se puede descartar hasta la próxima versión."""

    def __init__(self, ctl: UpdateController, parent=None):
        super().__init__(parent)
        self.ctl = ctl
        self._dismissed_version = ""
        self.setObjectName("updateBar")
        self.setStyleSheet(f"#updateBar {{ background: {C['accent_dim']}; border-bottom: 1px solid {C['border']}; }}"
                           f"#updateBar QLabel {{ color: {C['text']}; }}")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 5, 8, 5)
        lay.setSpacing(6)
        self.label = QLabel()
        lay.addWidget(self.label)
        self.progress = QProgressBar()
        self.progress.setFixedWidth(160)
        self.progress.setFixedHeight(10)
        self.progress.setTextVisible(False)
        lay.addWidget(self.progress)
        self.btn_notes = _link_button("Ver novedades")
        self.btn_notes.clicked.connect(ctl.show_notes)
        lay.addWidget(self.btn_notes)
        self.btn_update = _link_button("Actualizar")
        self.btn_update.setStyleSheet(self.btn_update.styleSheet() + "QPushButton { font-weight: 600; }")
        self.btn_update.clicked.connect(ctl.start_update)
        lay.addWidget(self.btn_update)
        lay.addStretch()
        self.btn_close = _link_button("✕")
        self.btn_close.setToolTip("Ocultar este aviso")
        self.btn_close.clicked.connect(self._dismiss)
        lay.addWidget(self.btn_close)
        ctl.state_changed.connect(self.refresh)
        ctl.progress.connect(self._on_progress)
        ctl.download_failed.connect(lambda m: self.label.setText(m))
        self.refresh()

    def _dismiss(self):
        if self.ctl.info:
            self._dismissed_version = self.ctl.info.version
        self.refresh()

    def _on_progress(self, done: int, total: int):
        if total:
            self.progress.setRange(0, total)
            self.progress.setValue(done)
            self.label.setText(f"Bajando la actualización… {done * 100 // total}%")

    def refresh(self):
        info = self.ctl.info
        if not info or info.version == self._dismissed_version and not self.ctl.downloading:
            self.hide()
            return
        busy = self.ctl.downloading
        self.progress.setVisible(busy)
        self.btn_notes.setVisible(not busy)
        self.btn_update.setVisible(not busy)
        self.btn_close.setVisible(not busy)
        if busy:
            self.progress.setRange(0, 0)
            self.label.setText("Bajando la actualización…")
        else:
            self.label.setText(f"Hay una versión nueva: {info.version} ·")
            self.btn_update.setText("Actualizar" if self.ctl.can_install else "Ver en GitHub")
        self.show()


class UpdateAboutBox(QGroupBox):
    """Bloque de Ajustes > Acerca de."""

    def __init__(self, ctl: UpdateController, parent=None):
        super().__init__("Actualizaciones", parent)
        self.ctl = ctl
        lay = QVBoxLayout(self)
        lay.setSpacing(8)
        self.chk_auto = QCheckBox("Buscar actualizaciones automáticamente")
        self.chk_auto.setChecked(get_pref("auto_update"))
        self.chk_auto.toggled.connect(lambda on: set_pref("auto_update", on))
        lay.addWidget(self.chk_auto)
        row = QHBoxLayout()
        self.btn_check = QPushButton("Buscar actualizaciones")
        self.btn_check.setCursor(Qt.PointingHandCursor)
        self.btn_check.clicked.connect(lambda: ctl.check(manual=True))
        row.addWidget(self.btn_check)
        self.btn_update = QPushButton("Actualizar")
        self.btn_update.setCursor(Qt.PointingHandCursor)
        self.btn_update.clicked.connect(ctl.start_update)
        row.addWidget(self.btn_update)
        row.addStretch()
        lay.addLayout(row)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setObjectName("hint")
        lay.addWidget(self.status)
        self.notes = QPlainTextEdit()
        self.notes.setReadOnly(True)
        self.notes.setMaximumHeight(160)
        lay.addWidget(self.notes)
        hint = QLabel(f"Las versiones salen de GitHub Releases ({configured_repo()}). "
                      "Se puede cambiar con update_repo en config.ini.")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        lay.addWidget(hint)

        ctl.checking.connect(self._on_checking)
        ctl.up_to_date.connect(lambda: self._set_status(f"Tenés la última versión ({APP_VERSION})."))
        ctl.check_failed.connect(lambda m: self._set_status(m, error=True))
        ctl.download_failed.connect(lambda m: self._set_status(m, error=True))
        ctl.progress.connect(lambda d, t: self._set_status(f"Bajando… {d * 100 // t}%") if t else None)
        ctl.state_changed.connect(self.refresh)
        self.refresh()

    def _set_status(self, text: str, error: bool = False):
        self.status.setStyleSheet(f"color: {C['danger_text'] if error else C['text_2']};")
        self.status.setText(text)

    def _on_checking(self, on: bool):
        self.btn_check.setEnabled(not on and not self.ctl.downloading)
        if on:
            self._set_status("Buscando…")

    def refresh(self):
        info = self.ctl.info
        busy = self.ctl.downloading
        self.btn_check.setEnabled(not busy and not self.ctl.busy)
        self.btn_update.setVisible(bool(info))
        self.btn_update.setEnabled(not busy)
        self.notes.setVisible(bool(info))
        if info:
            self.btn_update.setText("Actualizar ahora" if self.ctl.can_install else "Ver en GitHub")
            self.notes.setPlainText(f"Novedades de la versión {info.version}\n\n{info.notes or 'Sin notas.'}")
            extra = "" if self.ctl.can_install else " (en modo desarrollo solo se avisa)"
            self._set_status(f"Hay una versión nueva: {info.version}{extra}")
