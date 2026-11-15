"""
Advanced WhatsApp - Pantalla de estadísticas
================================================
Con quién hablás más, mensajes por día y por hora, y tiempos de respuesta, por período.
Las consultas corren en un hilo (ver app/stats.py) y los gráficos se dibujan a mano, sin librerías.
"""

import threading
from typing import List, Optional, Tuple

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import (
    QComboBox, QDialog, QGridLayout, QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget
)

from app import stats
from app.ui.theme import C


class BarChart(QWidget):
    """Barras verticales (enviados + recibidos apilados) con etiquetas al pie."""

    def __init__(self, height: int = 150, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(height)
        self._items: List[Tuple[str, int, int]] = []     # (etiqueta, enviados, recibidos)
        self._label_every = 1

    def set_data(self, items: List[Tuple[str, int, int]], label_every: int = 1):
        self._items = items
        self._label_every = max(1, label_every)
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(4, 8, -4, -20)
        if not self._items or max((s + r) for _l, s, r in self._items) == 0:
            p.setPen(QColor(C["text_3"]))
            p.drawText(self.rect(), Qt.AlignCenter, "Sin mensajes en este período")
            return
        top = max(s + r for _l, s, r in self._items)
        n = len(self._items)
        slot = rect.width() / n
        bw = max(2.0, slot * 0.7)
        small = QFont(self.font())
        small.setPixelSize(10)
        p.setFont(small)
        for i, (label, sent, recv) in enumerate(self._items):
            x = rect.left() + i * slot + (slot - bw) / 2
            h_recv = rect.height() * recv / top
            h_sent = rect.height() * sent / top
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(C["bubble_in"]).lighter(170))
            p.drawRoundedRect(QRectF(x, rect.bottom() - h_recv, bw, h_recv), 1.5, 1.5)
            p.setBrush(QColor(C["accent"]))
            p.drawRoundedRect(QRectF(x, rect.bottom() - h_recv - h_sent, bw, h_sent), 1.5, 1.5)
            if i % self._label_every == 0:
                p.setPen(QColor(C["text_2"]))
                p.drawText(QRectF(x - slot, rect.bottom() + 3, bw + 2 * slot, 14), Qt.AlignHCenter | Qt.AlignTop, label)


class RankBars(QWidget):
    """Ranking horizontal: nombre, barra proporcional y cantidad."""
    ROW_H = 26

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows: List[Tuple[str, int, int]] = []     # (nombre, enviados, recibidos)

    def set_rows(self, rows: List[Tuple[str, int, int]]):
        self._rows = rows
        self.setMinimumHeight(max(1, len(rows)) * self.ROW_H + 4)
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if not self._rows:
            p.setPen(QColor(C["text_3"]))
            p.drawText(self.rect(), Qt.AlignCenter, "Sin mensajes en este período")
            return
        top = max(s + r for _n, s, r in self._rows) or 1
        name_w = min(190, int(self.width() * 0.34))
        num_w = 60
        bar_w = max(20, self.width() - name_w - num_w - 16)
        for i, (name, sent, recv) in enumerate(self._rows):
            y = i * self.ROW_H + 2
            p.setPen(QColor(C["text"]))
            fm = p.fontMetrics()
            p.drawText(QRectF(0, y, name_w, self.ROW_H), Qt.AlignVCenter | Qt.AlignLeft,
                       fm.elidedText(name, Qt.ElideRight, name_w - 6))
            x0 = name_w + 4
            wr = bar_w * recv / top
            ws = bar_w * sent / top
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(C["accent"]))
            p.drawRoundedRect(QRectF(x0, y + 6, ws, self.ROW_H - 12), 3, 3)
            p.setBrush(QColor(C["bubble_in"]).lighter(170))
            p.drawRoundedRect(QRectF(x0 + ws, y + 6, wr, self.ROW_H - 12), 3, 3)
            p.setPen(QColor(C["text_2"]))
            p.drawText(QRectF(x0 + bar_w + 6, y, num_w, self.ROW_H), Qt.AlignVCenter | Qt.AlignLeft, str(sent + recv))


class StatsDialog(QDialog):
    _loaded = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Estadísticas")
        self.resize(760, 640)
        self.setStyleSheet(f"QDialog {{ background: {C['panel']}; }} QLabel {{ color: {C['text']}; }}")
        self._token = 0
        self._loaded.connect(self._show_report)

        root = QVBoxLayout(self)
        top = QHBoxLayout()
        title = QLabel("Estadísticas de tus chats")
        title.setStyleSheet("font-size: 16px; font-weight: 600;")
        top.addWidget(title)
        top.addStretch()
        self.combo = QComboBox()
        for key, (label, _d) in stats.PERIODS.items():
            self.combo.addItem(label, key)
        self.combo.setCurrentIndex(1)
        self.combo.currentIndexChanged.connect(self.reload)
        top.addWidget(self.combo)
        root.addLayout(top)

        self.summary = QLabel("Calculando…")
        self.summary.setWordWrap(True)
        self.summary.setStyleSheet(f"color: {C['text_2']};")
        root.addWidget(self.summary)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        body = QWidget()
        lay = QVBoxLayout(body)
        lay.setSpacing(10)
        scroll.setWidget(body)
        root.addWidget(scroll, 1)

        def section(text, widget):
            lbl = QLabel(text)
            lbl.setStyleSheet(f"font-weight: 600; color: {C['accent_hi']}; margin-top: 6px;")
            lay.addWidget(lbl)
            lay.addWidget(widget)

        self.response = QLabel("")
        self.response.setWordWrap(True)
        section("Tiempo de respuesta promedio", self.response)
        self.ranking = RankBars()
        section("Con quién hablás más  (verde: enviados · gris: recibidos)", self.ranking)
        self.chart_day = BarChart()
        section("Mensajes por día", self.chart_day)
        self.chart_hour = BarChart()
        section("Mensajes por hora del día", self.chart_hour)
        self.speed = QLabel("")
        self.speed.setWordWrap(True)
        self.speed.setTextFormat(Qt.RichText)
        section("Dónde contestás más rápido y más lento", self.speed)
        lay.addStretch()

        self.reload()

    def reload(self):
        self._token += 1
        token = self._token
        period = self.combo.currentData()
        self.summary.setText("Calculando…")

        def work():
            try:
                rep = stats.report(period)
            except Exception as e:
                rep = {"error": str(e)}
            rep["_token"] = token
            self._loaded.emit(rep)
        threading.Thread(target=work, daemon=True, name="advancedwhatsapp-stats").start()

    def _show_report(self, rep: dict):
        if rep.get("_token") != self._token:
            return    # llegó una respuesta de un período que ya cambiaste
        if rep.get("error"):
            self.summary.setText(f"No se pudieron calcular las estadísticas: {rep['error']}")
            return
        t = rep["totals"]
        self.summary.setText(f"{t['total']:,} mensajes en {t['chats']} chats · {t['sent']:,} enviados · "
                             f"{t['received']:,} recibidos".replace(",", "."))
        r = rep["response"]
        self.response.setText(
            f"Vos contestás en  <b>{stats.format_duration(r['mine'])}</b>  ({r['mine_count']} respuestas)  ·  "
            f"los demás en  <b>{stats.format_duration(r['theirs'])}</b>  ({r['theirs_count']} respuestas).<br>"
            f"<span style='color:{C['text_3']}'>Solo chats individuales; las pausas de más de 24 h no cuentan.</span>")
        self.response.setTextFormat(Qt.RichText)
        self.ranking.set_rows([(c["name"], c["sent"], c["received"]) for c in rep["top_chats"]])
        days = rep["by_day"]
        if len(days) > 60:       # muchos días: se agrupan por mes para que se lea
            months = {}
            for d in days:
                m = d["day"][:7]
                s, rcv = months.get(m, (0, 0))
                months[m] = (s + d["sent"], rcv + d["received"])
            items = [(k[2:], s, rcv) for k, (s, rcv) in sorted(months.items())]
            self.chart_day.set_data(items, max(1, len(items) // 12))
        else:
            items = [(d["day"][5:], d["sent"], d["received"]) for d in days]
            self.chart_day.set_data(items, max(1, len(items) // 10))
        self.chart_hour.set_data([(f"{h['hour']}", h["sent"], h["received"]) for h in rep["by_hour"]], 2)

        def lines(items):
            return "<br>".join(f"{i['name']} — {stats.format_duration(i['avg_seconds'])} ({i['count']})" for i in items) \
                or "<span style='color:%s'>Sin datos suficientes</span>" % C["text_3"]
        sp = rep["speed"]
        self.speed.setText(f"<b>Más rápido</b><br>{lines(sp['fastest'])}<br><br><b>Más lento</b><br>{lines(sp['slowest'])}")
