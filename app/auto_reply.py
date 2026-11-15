"""
Respuestas automáticas: la configuración que edita la ventana. Las reglas las ejecuta el gateway (gateway/extras.js),
así siguen funcionando con la ventana cerrada. Acá solo hay valores por defecto, validación y descripciones.
"""

import uuid
from typing import Any, Dict, List

DAY_NAMES = ["Domingo", "Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado"]


def new_rule(kind: str = "schedule") -> Dict[str, Any]:
    manual = kind == "manual"
    return {
        "id": uuid.uuid4().hex[:8],
        "name": "Estoy manejando" if manual else "Fuera de horario",
        "enabled": True,
        "kind": "manual" if manual else "schedule",
        "text": "Estoy manejando, te respondo apenas llegue." if manual
                else "Hola, ahora no estoy disponible. Te respondo apenas pueda.",
        "days": [], "from": "22:00", "to": "08:00",
        "individual_only": True, "exclude": [], "only": [], "cooldown_hours": 12,
    }


def valid_time(s: str) -> bool:
    try:
        h, m = str(s).split(":")
        return 0 <= int(h) <= 23 and 0 <= int(m) <= 59
    except ValueError:
        return False


def describe_rule(rule: Dict[str, Any]) -> str:
    """Resumen de una línea para la lista de reglas."""
    if rule.get("kind") == "manual":
        when = "modo manual"
    else:
        days = rule.get("days") or []
        day_txt = "todos los días" if not days or len(days) == 7 else ", ".join(DAY_NAMES[d][:3] for d in sorted(days))
        when = f"{day_txt} de {rule.get('from', '00:00')} a {rule.get('to', '23:59')}"
    extra = []
    if rule.get("individual_only", True):
        extra.append("solo chats individuales")
    if rule.get("only"):
        extra.append(f"solo a {len(rule['only'])} contactos")
    if rule.get("exclude"):
        extra.append(f"{len(rule['exclude'])} excepciones")
    hours = rule.get("cooldown_hours", 12)
    extra.append(f"una vez cada {hours} h por contacto" if hours else "sin límite por contacto")
    return f"{when} · " + " · ".join(extra)


def validate_rule(rule: Dict[str, Any]) -> str:
    """Devuelve el motivo por el que la regla no sirve, o '' si está bien."""
    if not (rule.get("text") or "").strip():
        return "Escribí el texto de la respuesta."
    if rule.get("kind") != "manual" and not (valid_time(rule.get("from", "")) and valid_time(rule.get("to", ""))):
        return "El horario no es válido."
    return ""


def active_rules(cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [r for r in cfg.get("rules", []) if r.get("enabled", True)]
