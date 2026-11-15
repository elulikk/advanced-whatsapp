"""
Advanced WhatsApp - Pestaña «Almacenamiento» de Configuración
================================================================
Cuánto ocupa la multimedia bajada (por tipo y por chat), liberar espacio (más viejo que X días o por encima de X GB),
limpieza automática y caché de miniaturas. Los archivos borrados se vuelven a bajar al abrir el mensaje.
"""

import threading

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QDoubleSpinBox, QGroupBox, QHBoxLayout, QLabel, QMessageBox, QPushButton, QSpinBox, QVBoxLayout, QWidget
)

from app import media_cleanup, thumb_cache
from app.config import get_pref, set_pref
from app.ui.theme import C


class StorageTab(QWidget):
    _usage_ready = Signal(object)
    _done = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._usage_ready.connect(self._show_usage)
        self._done.connect(self._cleanup_done)
        lay = QVBoxLayout(self)
        lay.setSpacing(12)

        g_use = QGroupBox("Multimedia bajada")
        lu = QVBoxLayout(g_use)
        self.lbl_usage = QLabel("Calculando…")
        self.lbl_usage.setTextFormat(Qt.RichText)
        self.lbl_usage.setWordWrap(True)
        lu.addWidget(self.lbl_usage)
        row = QHBoxLayout()
        btn = QPushButton("Actualizar")
        btn.clicked.connect(self.refresh)
        row.addWidget(btn)
        row.addStretch()
        lu.addLayout(row)
        lay.addWidget(g_use)

        g_clean = QGroupBox("Liberar espacio")
        lc = QVBoxLayout(g_clean)
        hint = QLabel("Se borran los archivos bajados más viejos. Los mensajes quedan: al abrir una foto, un video o un audio "
                      "se vuelve a bajar. Lo que enviaste vos desde esta computadora no se toca.")
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {C['text_2']};")
        lc.addWidget(hint)
        r1 = QHBoxLayout()
        r1.addWidget(QLabel("Borrar lo más viejo que"))
        self.spin_days = QSpinBox()
        self.spin_days.setRange(0, 3650)
        self.spin_days.setSuffix(" días")
        self.spin_days.setSpecialValueText("—")
        r1.addWidget(self.spin_days)
        r1.addWidget(QLabel("o lo que supere"))
        self.spin_gb = QDoubleSpinBox()
        self.spin_gb.setRange(0, 500)
        self.spin_gb.setDecimals(1)
        self.spin_gb.setSuffix(" GB")
        self.spin_gb.setSpecialValueText("—")
        r1.addWidget(self.spin_gb)
        r1.addStretch()
        lc.addLayout(r1)
        r2 = QHBoxLayout()
        self.btn_clean = QPushButton("Liberar ahora…")
        self.btn_clean.clicked.connect(self._clean_now)
        r2.addWidget(self.btn_clean)
        r2.addStretch()
        lc.addLayout(r2)
        self.check_auto = QCheckBox("Hacerlo solo, una vez por día, con estos valores")
        s = media_cleanup.get_auto_settings()
        self.spin_days.setValue(int(s["days"]))
        self.spin_gb.setValue(float(s["gb"]))
        self.check_auto.setChecked(bool(s["days"] or s["gb"]))
        self.check_auto.toggled.connect(self._save_auto)
        self.spin_days.valueChanged.connect(self._save_auto)
        self.spin_gb.valueChanged.connect(self._save_auto)
        lc.addWidget(self.check_auto)
        self.lbl_result = QLabel("")
        self.lbl_result.setWordWrap(True)
        lc.addWidget(self.lbl_result)
        lay.addWidget(g_clean)

        btn_stats = QPushButton("Ver estadísticas de mis chats…   (Ctrl+Shift+E)")
        btn_stats.clicked.connect(self._open_stats)
        lay.addWidget(btn_stats)

        g_misc = QGroupBox("Chat")
        lm = QVBoxLayout(g_misc)
        self.check_autoplay = QCheckBox("Reproducir solo la siguiente nota de voz al terminar una")
        self.check_autoplay.setChecked(get_pref("audio_autoplay"))
        self.check_autoplay.toggled.connect(lambda on: set_pref("audio_autoplay", on))
        lm.addWidget(self.check_autoplay)
        self.check_previews = QCheckBox("Mostrar vista previa de los links (título, descripción e imagen)")
        self.check_previews.setChecked(get_pref("link_previews"))
        self.check_previews.toggled.connect(lambda on: set_pref("link_previews", on))
        lm.addWidget(self.check_previews)
        self.lbl_thumbs = QLabel("")
        self.lbl_thumbs.setStyleSheet(f"color: {C['text_2']};")
        lm.addWidget(self.lbl_thumbs)
        lay.addWidget(g_misc)
        lay.addStretch()

    def _open_stats(self):
        from app.ui.stats_dialog import StatsDialog
        StatsDialog(self.window()).exec()

    def showEvent(self, e):
        super().showEvent(e)
        self.refresh()

    def refresh(self):
        self.lbl_usage.setText("Calculando…")
        self.lbl_thumbs.setText(f"Caché de miniaturas: {media_cleanup.format_size(thumb_cache.disk_usage())} "
                                f"(se poda sola a {media_cleanup.format_size(thumb_cache.DISK_LIMIT_BYTES)}).")

        def work():
            try:
                self._usage_ready.emit(media_cleanup.usage())
            except Exception as ex:
                self._usage_ready.emit({"error": str(ex)})
        threading.Thread(target=work, daemon=True, name="advancedwhatsapp-usage").start()

    def _show_usage(self, u: dict):
        if u.get("error"):
            self.lbl_usage.setText(f"No se pudo calcular: {u['error']}")
            return
        fmt = media_cleanup.format_size
        types = " · ".join(f"{media_cleanup.TYPE_LABELS.get(t, t)} {fmt(v['bytes'])} ({v['count']})"
                           for t, v in sorted(u["by_type"].items(), key=lambda kv: kv[1]["bytes"], reverse=True))
        chats = "<br>".join(f"{c['name']} — {fmt(c['bytes'])} ({c['count']} archivos)" for c in u["by_chat"][:8])
        self.lbl_usage.setText(
            f"<b>{fmt(u['total_bytes'])}</b> en {u['total_files']} archivos "
            f"(se pueden liberar {fmt(u['removable_bytes'])}).<br>{types or 'Nada bajado todavía.'}"
            + (f"<br><br><b>Chats que más ocupan</b><br>{chats}" if chats else ""))

    def _save_auto(self, *_):
        on = self.check_auto.isChecked()
        media_cleanup.set_auto_settings(self.spin_days.value() if on else 0, self.spin_gb.value() if on else 0.0)

    def _clean_now(self):
        days, gb = self.spin_days.value(), self.spin_gb.value()
        if not days and not gb:
            QMessageBox.information(self, "Liberar espacio", "Elegí una antigüedad en días o un tope en GB.")
            return
        cands = media_cleanup.plan(days, gb)
        if not cands:
            self.lbl_result.setText("No hay nada para borrar con esos valores.")
            return
        size = media_cleanup.format_size(sum(c["size"] for c in cands))
        if QMessageBox.question(self, "Liberar espacio",
                                f"Se van a borrar {len(cands)} archivos ({size}). Se vuelven a bajar al abrirlos. ¿Seguir?") \
                != QMessageBox.Yes:
            return
        self.btn_clean.setEnabled(False)
        self.lbl_result.setText("Borrando…")
        threading.Thread(target=lambda: self._done.emit(media_cleanup.apply(cands)), daemon=True,
                         name="advancedwhatsapp-cleanup").start()

    def _cleanup_done(self, res: dict):
        self.btn_clean.setEnabled(True)
        extra = f" ({res['errors']} no se pudieron borrar)" if res.get("errors") else ""
        self.lbl_result.setText(f"Listo: {res['files']} archivos, {media_cleanup.format_size(res['bytes'])} liberados.{extra}")
        self.refresh()
