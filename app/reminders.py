"""
Advanced WhatsApp - Recordatorios locales
=========================================
Recordatorios creados desde una fecha/hora detectada en un mensaje. Se guardan en ~/.magsapp/recordatorios.json
(escritura atómica) y un temporizador de Qt revisa cada 30 s cuáles vencieron para avisar con una notificación.
Un recordatorio que venció con la app cerrada se avisa al abrir si no pasaron más de 12 horas.
"""

import json
import os
import threading
import time
import uuid
from typing import Callable, Dict, List, Optional

from PySide6.QtCore import QObject, QTimer, Signal

from app.config import DATA_DIR

REMINDERS_PATH = os.path.join(DATA_DIR, "recordatorios.json")
STALE_AFTER = 12 * 3600      # un recordatorio vencido hace más que esto se descarta sin avisar
_lock = threading.Lock()


def _load(path: Optional[str] = None) -> List[Dict]:
    try:
        with open(path or REMINDERS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return [r for r in data if isinstance(r, dict) and r.get("at")] if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def _save(items: List[Dict], path: Optional[str] = None) -> None:
    path = path or REMINDERS_PATH
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def add_reminder(at: int, text: str, chat_jid: str = "", chat_name: str = "", path: Optional[str] = None) -> Dict:
    item = {"id": uuid.uuid4().hex[:12], "at": int(at), "text": (text or "").strip()[:300],
            "chat_jid": chat_jid or "", "chat_name": chat_name or ""}
    with _lock:
        items = _load(path)
        items.append(item)
        _save(items, path)
    return item


def list_reminders(path: Optional[str] = None) -> List[Dict]:
    with _lock:
        return sorted(_load(path), key=lambda r: r["at"])


def remove_reminder(rid: str, path: Optional[str] = None) -> None:
    with _lock:
        _save([r for r in _load(path) if r.get("id") != rid], path)


def pop_due(now: Optional[float] = None, path: Optional[str] = None) -> List[Dict]:
    """Saca de la lista y devuelve los recordatorios vencidos que todavía vale la pena avisar (<= 12 h de atraso)."""
    now = time.time() if now is None else now
    with _lock:
        items = _load(path)
        due = [r for r in items if r["at"] <= now]
        if not due:
            return []
        _save([r for r in items if r["at"] > now], path)
    return [r for r in due if now - r["at"] <= STALE_AFTER]


class ReminderService(QObject):
    """Revisa los recordatorios cada 30 s y avisa con `notify(title, body, uri)` (p. ej. NotificationCenter.notify_simple)."""
    changed = Signal()

    def __init__(self, notify: Callable[..., None], parent=None):
        super().__init__(parent)
        self._notify = notify
        self._timer = QTimer(self)
        self._timer.setInterval(30000)
        self._timer.timeout.connect(self.check)
        self._timer.start()
        QTimer.singleShot(4000, self.check)

    def add(self, at: int, text: str, chat_jid: str = "", chat_name: str = "") -> Dict:
        item = add_reminder(at, text, chat_jid, chat_name)
        self.changed.emit()
        return item

    def check(self) -> None:
        try:
            due = pop_due()
        except Exception as e:   # un archivo ilegible no debe romper la app
            print(f"[Advanced WhatsApp Recordatorios] {e}")
            return
        for r in due:
            title = "⏰ Recordatorio" + (f" · {r['chat_name']}" if r.get("chat_name") else "")
            uri = f"advancedwhatsapp://open?chat={r['chat_jid']}" if r.get("chat_jid") else ""
            try:
                self._notify(title, r.get("text") or "Tenés un recordatorio", uri=uri, tag=f"remind-{r['id']}", sound=True)
            except Exception as e:
                print(f"[Advanced WhatsApp Recordatorios] {e}")
        if due:
            self.changed.emit()
