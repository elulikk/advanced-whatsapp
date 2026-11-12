"""Encuestas: texto para mostrarlas en la burbuja. El conteo lo calcula el gateway (descifra los votos) y llega ya hecho."""

import html
from typing import Any, Dict, List

BAR_WIDTH = 12


def totals(poll: Dict[str, Any]) -> int:
    return sum(int(o.get("votes") or 0) for o in poll.get("options", []))


def bar(votes: int, maximum: int, width: int = BAR_WIDTH) -> str:
    """Barra de bloques proporcional al máximo de la encuesta."""
    if maximum <= 0 or votes <= 0:
        return "░" * width
    filled = max(1, round(width * votes / maximum))
    return "█" * filled + "░" * (width - filled)


def poll_lines(poll: Dict[str, Any]) -> List[Dict[str, Any]]:
    """[{name, votes, bar, mine}] en el orden en que se creó la encuesta."""
    options = poll.get("options", [])
    top = max([int(o.get("votes") or 0) for o in options] or [0])
    return [{"name": o.get("name", ""), "votes": int(o.get("votes") or 0), "mine": bool(o.get("mine")),
             "bar": bar(int(o.get("votes") or 0), top)} for o in options]


def poll_html(poll: Dict[str, Any], accent: str, muted: str) -> str:
    lines = poll_lines(poll)
    kind = "Elegí las opciones que quieras" if poll.get("multi") else "Elegí una opción"
    out = [f'📊 <b>{html.escape(poll.get("name", ""))}</b>',
           f'<span style="color: {muted}; font-size: 11px;">{kind}</span>']
    for ln in lines:
        mark = " ✓" if ln["mine"] else ""
        out.append(f'{html.escape(ln["name"])}{mark}<br>'
                   f'<span style="color: {accent};">{ln["bar"]}</span> <b>{ln["votes"]}</b>')
    voters = int(poll.get("voters") or 0)
    out.append(f'<span style="color: {muted}; font-size: 11px;">{voters} voto{"s" if voters != 1 else ""}'
               f'{"" if voters else " todavía"}</span>')
    return "<br>".join(out)


def poll_text(poll: Dict[str, Any]) -> str:
    """Versión en texto plano (para copiar o para la vista previa)."""
    rows = [poll.get("name", "")]
    rows += [f"- {ln['name']}: {ln['votes']}" for ln in poll_lines(poll)]
    return "\n".join(rows)
