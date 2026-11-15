"""Exportar un chat a TXT (formato de WhatsApp), HTML (burbujas) o PDF.

Pensado para chats enormes: los mensajes se leen por páginas (paginación por (timestamp, rowid), sin OFFSET) y se
escriben a medida que llegan, así que la memoria no crece con el tamaño del chat. No baja nada de la red salvo que se pida
(`download_missing` + un `downloader` que pone la interfaz). Todo el armado es puro (sin Qt) salvo el PDF, que importa Qt
recién al exportar. Se usa desde un hilo aparte: `export_chat` informa el avance con un callback y se puede cancelar.
"""
import base64
import html
import mimetypes
import os
import re
import shutil
import time
from dataclasses import dataclass
from typing import Callable, Dict, Iterator, List, Optional, Tuple

from app import db

PAGE_SIZE = 500
EMBED_MAX_BYTES = 1_500_000        # imágenes más grandes que esto van a la carpeta aunque se pida incrustar
FORMATS = ("txt", "html", "pdf")
MEDIA_TYPES = ("image", "video", "audio", "document", "sticker")
MEDIA_LABEL = {"image": "imagen", "video": "video", "audio": "nota de voz", "document": "documento", "sticker": "sticker"}
DAY = 86400


@dataclass
class ExportOptions:
    fmt: str = "txt"
    since: Optional[int] = None            # epoch en segundos (inclusive)
    until: Optional[int] = None            # epoch en segundos (inclusive)
    include_media: bool = True             # solo lo que ya está descargado
    embed_images: bool = True              # HTML: incrusta imágenes chicas; si no, todo va a la carpeta de al lado
    download_missing: bool = False         # bajar lo que falte (necesita un downloader)
    include_transcripts: bool = True
    include_summaries: bool = False
    include_deleted: bool = True           # eliminados y ediciones conservados
    my_name: str = "Yo"


@dataclass
class ExportResult:
    path: str = ""
    messages: int = 0
    media_files: int = 0
    media_missing: int = 0
    cancelled: bool = False


# ── Rango de fechas ──

def range_for(kind: str, now: Optional[float] = None, desde: Optional[float] = None, hasta: Optional[float] = None):
    """(since, until) en segundos para 'all' | 'month' | 'custom'. `desde`/`hasta` son el inicio del día y se
    amplían hasta el final del día de `hasta`."""
    now = time.time() if now is None else now
    if kind == "month":
        return int(now) - 30 * DAY, None
    if kind == "custom":
        since = int(desde) if desde is not None else None
        until = int(hasta) + DAY - 1 if hasta is not None else None
        return since, until
    return None, None


# ── Lectura por páginas ──

def _where(jid: str, since: Optional[int], until: Optional[int]) -> Tuple[str, list]:
    sql, params = "chat_jid = ?", [jid]
    if since is not None:
        sql += " AND timestamp >= ?"
        params.append(int(since))
    if until is not None:
        sql += " AND timestamp <= ?"
        params.append(int(until))
    return sql, params


def count_messages(jid: str, since: Optional[int] = None, until: Optional[int] = None) -> int:
    where, params = _where(jid, since, until)
    with db.db_conn() as conn:
        return conn.execute(f"SELECT COUNT(*) FROM messages WHERE {where}", params).fetchone()[0]


def iter_pages(jid: str, since: Optional[int] = None, until: Optional[int] = None,
               page_size: int = PAGE_SIZE) -> Iterator[List[dict]]:
    """Páginas de mensajes en orden cronológico. Pagina por clave (timestamp, rowid): barato aunque haya 100 mil."""
    where, params = _where(jid, since, until)
    last_ts, last_rid = -1 << 62, -1
    while True:
        with db.db_conn() as conn:
            rows = conn.execute(
                f"SELECT rowid AS _rid, * FROM messages WHERE {where} AND (timestamp > ? OR (timestamp = ? AND rowid > ?)) "
                "ORDER BY timestamp, rowid LIMIT ?", params + [last_ts, last_ts, last_rid, page_size]).fetchall()
        if not rows:
            return
        page = [dict(r) for r in rows]
        last_ts, last_rid = page[-1]["timestamp"], page[-1]["_rid"]
        yield page
        if len(rows) < page_size:
            return


# ── Contenido de un mensaje (común a los tres formatos) ──

def _fmt_txt_date(ts: int) -> str:
    return time.strftime("%d/%m/%y %H:%M", time.localtime(ts))


def _fmt_day(ts: int) -> str:
    return time.strftime("%d/%m/%Y", time.localtime(ts))


def _fmt_hour(ts: int) -> str:
    return time.strftime("%H:%M", time.localtime(ts))


def _original(msg: dict, opts: ExportOptions) -> Optional[dict]:
    """Contenido conservado de un mensaje eliminado (None si no se pide o no se guardó)."""
    if msg.get("media_type") != "revoked" or not opts.include_deleted or not msg.get("revoked_orig"):
        return None
    import json
    try:
        data = json.loads(msg["revoked_orig"])
        return data if isinstance(data, dict) else None
    except ValueError:
        return None


class Body:
    """Un mensaje ya interpretado: texto principal, notas extra y el archivo (si hay y se puede incluir)."""
    __slots__ = ("kind", "text", "notes", "file", "label", "deleted")

    def __init__(self, kind="text", text="", notes=None, file=None, label="", deleted=False):
        self.kind, self.text, self.notes, self.file, self.label, self.deleted = kind, text, notes or [], file, label, deleted


def build_body(msg: dict, opts: ExportOptions, file: Optional[str] = None, edits: Optional[List[str]] = None) -> Body:
    """Interpreta un mensaje. `file` es el archivo que se va a incluir (ya resuelto); `edits`, las versiones anteriores."""
    orig = _original(msg, opts)
    source = {**msg, **orig} if orig else msg
    kind = source.get("media_type") or "text"
    text = (source.get("text") or "").strip()
    notes: List[str] = []
    label = ""
    if kind == "revoked":
        text = "🚫 Mensaje eliminado"
    elif kind in MEDIA_LABEL:
        label = MEDIA_LABEL[kind]
    elif kind == "location":
        label = "ubicación"
    elif kind == "contact":
        label = "contacto"
    poll = db.parse_poll(msg)
    if poll:
        opts_txt = []
        for o in poll.get("options") or []:
            if isinstance(o, dict):
                n = o.get("name") or o.get("text") or ""
                v = o.get("votes")
                opts_txt.append(f"{n} ({len(v) if isinstance(v, list) else v})" if v not in (None, "", 0, []) else str(n))
            else:
                opts_txt.append(str(o))
        text = f"📊 {poll.get('name') or text}: " + " · ".join(opts_txt)
        kind = "poll"
    if msg.get("quoted_text"):
        who = (msg.get("quoted_sender") or "").strip()
        notes.append(f"↪ {who + ': ' if who else ''}{msg['quoted_text'].strip()[:200]}")
    if opts.include_transcripts and source.get("transcript"):
        notes.append("🎙 Transcripción: " + source["transcript"].strip())
    if opts.include_summaries and msg.get("summary"):
        notes.append("✨ Resumen IA: " + msg["summary"].strip())
    deleted = bool(orig)
    if deleted:
        notes.insert(0, "🚫 Eliminado por el remitente (se conservó el contenido original)")
    if opts.include_deleted and edits:
        for i, v in enumerate(edits, 1):
            notes.append(f"✏ Versión anterior {i}: {v.strip()}")
    if msg.get("edited"):
        notes.append("✏ Mensaje editado")
    return Body(kind, text, notes, file, label, deleted)


def _safe_name(name: str) -> str:
    return re.sub(r"[^\w.\- ]", "_", name or "archivo").strip(" .")[:80] or "archivo"


class _Media:
    """Resuelve el archivo de cada mensaje: usa lo descargado y, solo si se pidió, intenta bajar lo que falta."""

    def __init__(self, opts: ExportOptions, downloader: Optional[Callable[[dict], Optional[str]]]):
        self.opts, self.downloader = opts, downloader
        self.missing = 0

    def resolve(self, msg: dict) -> Optional[str]:
        o = self.opts
        if not o.include_media or msg.get("media_type") not in MEDIA_TYPES:
            return None
        path = msg.get("media_path")
        if path and os.path.exists(path):
            return path
        if o.download_missing and self.downloader and msg.get("media_raw"):
            try:
                got = self.downloader(msg)
                if got and os.path.exists(got):
                    return got
            except Exception:
                pass
        self.missing += 1
        return None


def sender_of(msg: dict, my_name: str, chat_name: str) -> str:
    if msg.get("from_me"):
        return my_name
    return (msg.get("sender_name") or "").strip() or chat_name or (msg.get("sender_jid") or "").split("@")[0] or "?"


# ── TXT ──

def txt_lines(msg: dict, body: Body, who: str) -> List[str]:
    """Líneas de un mensaje al estilo del export de WhatsApp: `dd/mm/aa hh:mm - Nombre: texto`."""
    head = f"{_fmt_txt_date(msg['timestamp'])} - {who}: "
    if body.file:
        main = f"{os.path.basename(body.file)} (archivo adjunto)"
        if body.text:
            main += "\n" + body.text
    elif body.kind in MEDIA_LABEL and not body.deleted and body.kind != "revoked":
        main = "<Multimedia omitida>" + ("\n" + body.text if body.text else "")
    else:
        main = body.text
    out = (head + main).split("\n")
    for n in body.notes:
        out.extend(["    " + ln for ln in n.split("\n")])
    return out


# ── HTML ──

_CSS = """
body{margin:0;background:#0b141a;color:#e9edef;font:15px/1.4 'Segoe UI',system-ui,sans-serif}
header{position:sticky;top:0;background:#202c33;padding:14px 20px;box-shadow:0 1px 4px #0006}
header h1{margin:0;font-size:18px}header small{color:#8696a0}
main{max-width:860px;margin:0 auto;padding:12px 16px 40px}
.day{text-align:center;margin:18px 0 8px}.day span{background:#182229;color:#8696a0;border-radius:8px;padding:4px 12px;font-size:12px}
.b{max-width:75%;margin:3px 0;padding:6px 9px 4px;border-radius:9px;background:#202c33;clear:both;word-wrap:break-word;overflow-wrap:anywhere}
.b.me{background:#005c4b;margin-left:auto}.b .who{font-size:12.5px;font-weight:600;color:#53bdeb}
.b .t{white-space:pre-wrap}.b .n{font-size:12.5px;color:#aebac1;border-left:3px solid #8696a0;padding-left:7px;margin:4px 0;white-space:pre-wrap}
.b .n.del{border-color:#f15c6d;color:#f6a1ab}.b time{display:block;text-align:right;font-size:11px;color:#8696a0}
.b img,.b video{max-width:100%;border-radius:6px;display:block;margin:2px 0}.b audio{width:260px}.b a{color:#53bdeb}
.ph{color:#8696a0;font-style:italic}
"""


def html_head(title: str, subtitle: str) -> str:
    return ("<!doctype html>\n<html lang=\"es\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            f"<title>{html.escape(title)}</title><style>{_CSS}</style></head><body>"
            f"<header><h1>{html.escape(title)}</h1><small>{html.escape(subtitle)}</small></header><main>\n")


def html_day(ts: int) -> str:
    return f"<div class=\"day\"><span>{_fmt_day(ts)}</span></div>\n"


def html_bubble(msg: dict, body: Body, who: str, file_ref: Optional[Tuple[str, str]] = None,
                reactions: Optional[List[str]] = None) -> str:
    """`file_ref` = ('data'|'link', url) para el archivo ya resuelto (incrustado o en la carpeta)."""
    esc = html.escape
    me = bool(msg.get("from_me"))
    parts = [f"<div class=\"b{' me' if me else ''}\" id=\"m_{esc(str(msg.get('id', '')))}\">"]
    if not me:
        parts.append(f"<div class=\"who\">{esc(who)}</div>")
    if file_ref:
        url = esc(file_ref[1], quote=True)
        if body.kind in ("image", "sticker"):
            parts.append(f"<img src=\"{url}\" loading=\"lazy\" alt=\"{esc(body.label)}\">")
        elif body.kind == "video":
            parts.append(f"<video controls preload=\"none\" src=\"{url}\"></video>")
        elif body.kind == "audio":
            parts.append(f"<audio controls preload=\"none\" src=\"{url}\"></audio>")
        else:
            parts.append(f"<a href=\"{url}\">📎 {esc(os.path.basename(body.file or 'archivo'))}</a>")
    elif body.kind in MEDIA_LABEL and body.kind != "revoked":
        parts.append(f"<div class=\"ph\">[{esc(body.label)} no incluido]</div>")
    if body.text:
        parts.append(f"<div class=\"t\">{esc(body.text)}</div>")
    for n in body.notes:
        parts.append(f"<div class=\"n{' del' if n.startswith('🚫') else ''}\">{esc(n)}</div>")
    if reactions:
        parts.append(f"<div class=\"ph\">{esc(' '.join(reactions))}</div>")
    parts.append(f"<time>{_fmt_hour(msg['timestamp'])}</time></div>\n")
    return "".join(parts)


HTML_FOOT = "</main></body></html>\n"


# ── Exportación ──

def _chat_title(chat: Optional[dict], jid: str) -> str:
    if chat:
        for k in ("custom_name", "name", "phone"):
            if (chat.get(k) or "").strip():
                return chat[k].strip()
    return jid.split("@")[0]


def default_filename(chat_name: str, fmt: str) -> str:
    return f"Chat de WhatsApp con {_safe_name(chat_name)}.{fmt}"


def export_chat(jid: str, dest: str, opts: ExportOptions,
                progress: Optional[Callable[[int, int], None]] = None,
                cancelled: Optional[Callable[[], bool]] = None,
                downloader: Optional[Callable[[dict], Optional[str]]] = None) -> ExportResult:
    """Exporta el chat a `dest`. `progress(hechos, total)` se llama por página; `cancelled()` corta y borra lo parcial."""
    if opts.fmt not in FORMATS:
        raise ValueError(f"Formato desconocido: {opts.fmt}")
    chat = db.get_chat(jid)
    chat_name = _chat_title(chat, jid)
    total = count_messages(jid, opts.since, opts.until)
    media = _Media(opts, downloader)
    res = ExportResult(path=dest)
    dest_dir = os.path.dirname(os.path.abspath(dest))
    os.makedirs(dest_dir, exist_ok=True)
    tmp = dest + ".part"
    media_dir = os.path.splitext(dest)[0] + "_archivos"
    pdf = None
    try:
        if opts.fmt == "pdf":
            pdf = _PdfOut(tmp, chat_name, opts)
            out = None
        else:
            out = open(tmp, "w", encoding="utf-8", newline="\r\n" if opts.fmt == "txt" else "\n")
        try:
            if opts.fmt == "html":
                out.write(html_head(f"Chat con {chat_name}", _subtitle(opts, total)))
            last_day = None
            done = 0
            for page in iter_pages(jid, opts.since, opts.until):
                if cancelled and cancelled():
                    res.cancelled = True
                    break
                page = [m for m in page if not db.is_sync_doc(m.get("media_type"), m.get("text"))]
                reacts = db.get_reactions([m["id"] for m in page]) if opts.fmt != "txt" else {}
                for m in page:
                    who = sender_of(m, opts.my_name, chat_name)
                    path = media.resolve(m)
                    edits = [e["text"] or "" for e in db.get_message_edits(m["id"])] if (opts.include_deleted and m.get("edited")) else None
                    body = build_body(m, opts, file=path, edits=edits)
                    if opts.fmt == "txt":
                        out.write("\n".join(txt_lines(m, body, who)) + "\n")
                    elif opts.fmt == "html":
                        day = _fmt_day(m["timestamp"])
                        if day != last_day:
                            out.write(html_day(m["timestamp"]))
                            last_day = day
                        ref = _html_file_ref(path, body.kind, m, opts, media_dir, res) if path else None
                        out.write(html_bubble(m, body, who, ref, [e for e, _ in reacts.get(m["id"], [])]))
                    else:
                        pdf.add(m, body, who, [e for e, _ in reacts.get(m["id"], [])])
                    if path:
                        res.media_files += 1
                    res.messages += 1
                done += len(page)
                if progress:
                    progress(min(done, total), total)
        finally:
            if out:
                if opts.fmt == "html":
                    out.write(HTML_FOOT)
                out.close()
        if pdf:
            pdf.finish()
            pdf = None
        res.media_missing = media.missing
        if res.cancelled:
            _remove(tmp)
            if opts.fmt == "html" and os.path.isdir(media_dir):
                shutil.rmtree(media_dir, ignore_errors=True)
            return res
        os.replace(tmp, dest)
        return res
    except BaseException:
        if pdf:
            pdf.abort()
        _remove(tmp)
        raise


def _subtitle(opts: ExportOptions, total: int) -> str:
    rango = ""
    if opts.since or opts.until:
        rango = " · " + (_fmt_day(opts.since) if opts.since else "inicio") + " a " + (_fmt_day(opts.until) if opts.until else "hoy")
    return f"{total} mensajes{rango} · exportado el {_fmt_day(int(time.time()))}"


def _remove(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


def _html_file_ref(path: str, kind: str, msg: dict, opts: ExportOptions, media_dir: str, res: ExportResult):
    """Incrusta la imagen (si es chica y se pidió) o la copia a la carpeta de al lado y devuelve un link relativo."""
    try:
        if opts.embed_images and kind in ("image", "sticker") and os.path.getsize(path) <= EMBED_MAX_BYTES:
            mime = mimetypes.guess_type(path)[0] or "image/jpeg"
            with open(path, "rb") as f:
                return "data", f"data:{mime};base64," + base64.b64encode(f.read()).decode("ascii")
        os.makedirs(media_dir, exist_ok=True)
        name = f"{_safe_name(str(msg.get('id', ''))[:20])}_{_safe_name(os.path.basename(path))}"
        target = os.path.join(media_dir, name)
        if not os.path.exists(target):
            shutil.copyfile(path, target)
        return "link", os.path.basename(media_dir) + "/" + name.replace(" ", "%20")
    except OSError:
        return None


# ── PDF (Qt, import diferido) ──

class _PdfOut:
    """Dibuja los mensajes con QPainter sobre un QPdfWriter, salteando de página a medida: memoria constante."""

    def __init__(self, path: str, title: str, opts: ExportOptions):
        from PySide6.QtCore import QMarginsF, QRectF, Qt
        from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPageLayout, QPageSize, QPainter, QPdfWriter
        self.Qt, self.QRectF, self.QColor, self.QFont = Qt, QRectF, QColor, QFont
        self.opts = opts
        self.writer = QPdfWriter(path)
        self.writer.setResolution(96)
        self.writer.setTitle(f"Chat con {title}")
        self.writer.setPageLayout(QPageLayout(QPageSize(QPageSize.A4), QPageLayout.Portrait, QMarginsF(15, 15, 15, 15)))
        self.p = QPainter(self.writer)
        self.W, self.H = float(self.writer.width()), float(self.writer.height())
        self.font = QFont("Segoe UI", 10)
        self.small = QFont("Segoe UI", 8)
        self.bold = QFont("Segoe UI", 9, QFont.Bold)
        self.fm = QFontMetricsF(self.font)
        self.y = 0.0
        self.last_day = None
        self._line(f"Chat con {title}", QFont("Segoe UI", 15, QFont.Bold), "#111111")

    def _text_h(self, text: str, font, width: float) -> float:
        from PySide6.QtGui import QFontMetricsF
        return QFontMetricsF(font).boundingRect(self.QRectF(0, 0, width, 100000), self.Qt.TextWordWrap, text).height()

    def _room(self, h: float):
        if self.y + h > self.H and self.y > 0:
            self.writer.newPage()
            self.y = 0.0

    def _line(self, text: str, font, color: str, align=None):
        h = self._text_h(text, font, self.W) + 6
        self._room(h)
        self.p.setFont(font)
        self.p.setPen(self.QColor(color))
        self.p.drawText(self.QRectF(0, self.y, self.W, h), (align or self.Qt.AlignLeft) | self.Qt.TextWordWrap, text)
        self.y += h

    def add(self, msg: dict, body: Body, who: str, reactions: List[str]):
        day = _fmt_day(msg["timestamp"])
        if day != self.last_day:
            self.last_day = day
            self.y += 6
            self._line(day, self.bold, "#667781", self.Qt.AlignHCenter)
        me = bool(msg.get("from_me"))
        bw = self.W * 0.72
        inner = bw - 16
        image = self._image(body, inner) if body.file and body.kind in ("image", "sticker") else None
        text = body.text
        if body.file and image is None:
            text = (f"📎 {os.path.basename(body.file)}\n" + text).strip()
        elif body.kind in MEDIA_LABEL and not body.file and not body.deleted:
            text = (f"[{body.label} no incluido]\n" + text).strip()
        blocks = [(c, self.font, "#111111") for c in _chunks(text)]
        blocks += [(n, self.small, "#8a3b45" if n.startswith("🚫") else "#54656f") for n in body.notes]
        if reactions:
            blocks.append((" ".join(reactions), self.small, "#54656f"))
        who_h = self._text_h(who, self.bold, inner) if not me else 0
        x = self.W - bw if me else 0.0
        first = True
        pending = blocks[:] or [("", self.font, "#111111")]
        while pending or first:
            avail = self.H - self.y
            need = 12 + (who_h if first else 0) + (image.height() + 4 if image is not None and first else 0)
            used, take = need, []
            while pending:
                t, f, _c = pending[0]
                hh = self._text_h(t, f, inner) + 3
                if take and used + hh + 12 > avail:
                    break
                take.append(pending.pop(0))
                used += hh
            used += 12 if first else 0
            if self.y > 0 and used > avail:
                self.writer.newPage()
                self.y = 0.0
            self.p.setPen(self.Qt.NoPen)
            self.p.setBrush(self.QColor("#d9fdd3" if me else "#f0f2f5"))
            self.p.drawRoundedRect(self.QRectF(x, self.y, bw, used), 7, 7)
            cy = self.y + 5
            if first and who_h:
                self.p.setFont(self.bold)
                self.p.setPen(self.QColor("#027eb5"))
                self.p.drawText(self.QRectF(x + 8, cy, inner, who_h), self.Qt.TextWordWrap, who)
                cy += who_h
            if first and image is not None:
                self.p.drawImage(int(x + 8), int(cy), image)
                cy += image.height() + 4
            for t, f, c in take:
                hh = self._text_h(t, f, inner) + 3
                self.p.setFont(f)
                self.p.setPen(self.QColor(c))
                self.p.drawText(self.QRectF(x + 8, cy, inner, hh), self.Qt.TextWordWrap, t)
                cy += hh
            if not pending:
                self.p.setFont(self.small)
                self.p.setPen(self.QColor("#667781"))
                self.p.drawText(self.QRectF(x + 8, self.y + used - 14, inner, 12), self.Qt.AlignRight, _fmt_hour(msg["timestamp"]))
            self.y += used + 4
            first = False
            if not pending:
                break

    def _image(self, body: Body, width: float):
        from PySide6.QtCore import QSize
        from PySide6.QtGui import QImageReader
        try:
            r = QImageReader(body.file)
            r.setAutoTransform(True)
            size = r.size()
            if not size.isValid():
                return None
            w = min(width, 260.0, float(size.width()))
            r.setScaledSize(QSize(int(w), max(1, int(w * size.height() / size.width()))))
            img = r.read()
            return None if img.isNull() else img
        except Exception:
            return None

    def finish(self):
        self.p.end()

    def abort(self):
        try:
            self.p.end()
        except Exception:
            pass


def _chunks(text: str, size: int = 1200) -> List[str]:
    """Parte un texto muy largo en pedazos para que cada uno entre en una página del PDF."""
    if not text:
        return []
    return [text[i:i + size] for i in range(0, len(text), size)]
