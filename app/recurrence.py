"""
Repetición de mensajes programados (cumpleaños, recordatorios semanales, etc.).

El gateway calcula la próxima fecha (gateway/extras.js, `nextRepeatAt`); esta es la misma cuenta para mostrarla en la UI
y probarla. repeat = {"type": "daily"|"weekly"|"monthly"|"yearly", "days": [0..6] (0 = domingo), "every": n}.
"""

import calendar
from datetime import datetime, timedelta
from typing import List, Optional

REPEAT_TYPES = ("daily", "weekly", "monthly", "yearly")
REPEAT_LABELS = {"daily": "Todos los días", "weekly": "Semanal", "monthly": "Todos los meses", "yearly": "Todos los años"}
# Domingo = 0, igual que Date.getDay() del gateway
DAY_SHORT = ["dom", "lun", "mar", "mié", "jue", "vie", "sáb"]


def normalize_repeat(r) -> Optional[dict]:
    if not isinstance(r, dict) or r.get("type") not in REPEAT_TYPES:
        return None
    try:
        every = max(1, min(365, int(r.get("every") or 1)))
    except (TypeError, ValueError):
        every = 1
    out = {"type": r["type"], "every": every}
    if r["type"] == "weekly":
        days = r.get("days") if isinstance(r.get("days"), list) else []
        out["days"] = sorted({int(d) for d in days if str(d).lstrip("-").isdigit() and 0 <= int(d) <= 6})
    return out


def _js_weekday(d: datetime) -> int:
    return (d.weekday() + 1) % 7


def _clamp_day(year: int, month: int, day: int) -> int:
    return min(day, calendar.monthrange(year, month)[1])


def next_repeat_at(at: int, repeat, after: float) -> Optional[int]:
    """Próxima fecha (epoch s) estrictamente posterior a `after`, con la hora de `at`; None si no hay repetición válida."""
    r = normalize_repeat(repeat)
    if not r:
        return None
    base = datetime.fromtimestamp(at)
    limit = max(at, after)

    if r["type"] == "daily":
        cand = base
        while cand.timestamp() <= limit:
            cand = cand + timedelta(days=r["every"])
        return int(cand.timestamp())
    if r["type"] == "weekly":
        days = r["days"] or [_js_weekday(base)]
        start_week = (base - timedelta(days=_js_weekday(base))).date()
        d = datetime.fromtimestamp(limit).replace(hour=base.hour, minute=base.minute, second=base.second, microsecond=0)
        for _ in range(800):
            if d.timestamp() > limit and _js_weekday(d) in days:
                week = (d - timedelta(days=_js_weekday(d))).date()
                if ((week - start_week).days // 7) % r["every"] == 0:
                    return int(d.timestamp())
            d += timedelta(days=1)
        return None
    n = 0
    while n < 200 * 12:
        n += r["every"]
        if r["type"] == "monthly":
            total = base.month - 1 + n
            y, mo = base.year + total // 12, total % 12 + 1
        else:
            y, mo = base.year + n, base.month
        cand = base.replace(year=y, month=mo, day=_clamp_day(y, mo, base.day))
        if cand.timestamp() > limit:
            return int(cand.timestamp())
    return None


def describe_repeat(repeat) -> str:
    r = normalize_repeat(repeat)
    if not r:
        return ""
    every = r["every"]
    if r["type"] == "daily":
        return "Todos los días" if every == 1 else f"Cada {every} días"
    if r["type"] == "weekly":
        days = ", ".join(DAY_SHORT[d] for d in r["days"]) if r["days"] else ""
        head = "Cada semana" if every == 1 else f"Cada {every} semanas"
        return f"{head} ({days})" if days else head
    if r["type"] == "monthly":
        return "Todos los meses" if every == 1 else f"Cada {every} meses"
    return "Todos los años" if every == 1 else f"Cada {every} años"


def weekday_list(days: List[int]) -> str:
    return ", ".join(DAY_SHORT[d] for d in sorted(set(days)) if 0 <= d <= 6)
