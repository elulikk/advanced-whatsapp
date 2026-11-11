import os
import sys

# Versión portable (AdvancedWhatsapp.exe hecho con PyInstaller): los archivos de la app están en sys._MEIPASS
# (carpeta _internal) y Node.js y FFmpeg vienen incluidos en _internal/runtime.
FROZEN = bool(getattr(sys, "frozen", False))
BASE_DIR = getattr(sys, "_MEIPASS", None) if FROZEN else None
BASE_DIR = BASE_DIR or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNTIME_DIR = os.path.join(BASE_DIR, "runtime")
GATEWAY_DIR = os.path.join(BASE_DIR, "gateway")

# Directorio de datos locales en el perfil de usuario para evitar bloqueos de Google Drive
USER_HOME = os.path.expanduser("~")
DATA_DIR = os.path.join(USER_HOME, ".magsapp")
os.makedirs(DATA_DIR, exist_ok=True)

# Rutas de base de datos y sesión
DB_PATH = os.path.join(DATA_DIR, "magsapp.db")
MEDIA_DIR = os.path.join(DATA_DIR, "media")
os.makedirs(MEDIA_DIR, exist_ok=True)

AUTH_DIR = os.path.join(DATA_DIR, "auth_info_baileys")
os.makedirs(AUTH_DIR, exist_ok=True)

# Puertos de comunicación interna (elegidos para no colisionar con MAGNETO 5005/5006)
# (ADVANCEDWHATSAPP_GATEWAY_PORT / ADVANCEDWHATSAPP_WEBHOOK_PORT permiten correr una copia de prueba al lado de la real)
GATEWAY_PORT = int(os.environ.get("ADVANCEDWHATSAPP_GATEWAY_PORT") or 5016)
GATEWAY_URL = f"http://127.0.0.1:{GATEWAY_PORT}"

WEBHOOK_PORT = int(os.environ.get("ADVANCEDWHATSAPP_WEBHOOK_PORT") or 5015)
# La copia de prueba no compite con la real por la instancia única ni toca accesos directos ni registro
TEST_COPY = WEBHOOK_PORT != 5015
WEBHOOK_URL = f"http://127.0.0.1:{WEBHOOK_PORT}/api/whatsapp/incoming"
CONNECTION_WEBHOOK_URL = f"http://127.0.0.1:{WEBHOOK_PORT}/api/whatsapp/connection"

TOKEN_HEADER = "X-AdvancedWhatsapp-Token"
TOKEN_PATH = os.path.join(DATA_DIR, "gateway.token")


def get_gateway_token() -> str:
    """Token compartido entre la app y el gateway; se genera una vez y se reutiliza."""
    import secrets
    try:
        with open(TOKEN_PATH, "r", encoding="utf-8") as f:
            tok = f.read().strip()
        if len(tok) >= 32:
            return tok
    except OSError:
        pass
    tok = secrets.token_urlsafe(32)
    # Escritura atómica: un corte a mitad de camino no deja un token truncado
    tmp = TOKEN_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(tok)
    os.replace(tmp, TOKEN_PATH)
    return tok


APP_NAME = "Advanced WhatsApp - Desktop"
APP_VERSION = "1.0.0"

CONFIG_PATH = os.path.join(DATA_DIR, "config.ini")
MAGNETO_CONFIG_PATH = os.path.abspath(os.path.join(BASE_DIR, "..", "MAGNETO", "config.ini"))


def load_app_config():
    """
    Carga la configuración de Advanced WhatsApp desde ~/.magsapp/config.ini.
    Si alguna sección o API Key falta, intenta heredarla de MAGNETO/config.ini.
    """
    import configparser
    config = configparser.ConfigParser(interpolation=None)

    # 1. Intentar cargar configuración propia de Advanced WhatsApp
    if os.path.exists(CONFIG_PATH):
        try:
            config.read(CONFIG_PATH, encoding="utf-8")
        except Exception:
            try:
                config.read(CONFIG_PATH, encoding="latin-1")
            except Exception:
                pass

    # Asegurar secciones básicas
    for section in ["IA", "GEMINI", "GROQ", "OPENAI", "ANTHROPIC", "GENERAL"]:
        if not config.has_section(section):
            config.add_section(section)

    # 2. Cargar fallback de MAGNETO si no están configuradas las keys
    if os.path.exists(MAGNETO_CONFIG_PATH):
        try:
            magneto_cfg = configparser.ConfigParser(interpolation=None)
            magneto_cfg.read(MAGNETO_CONFIG_PATH, encoding="utf-8")

            # Fallback para Gemini
            if not config.get("GEMINI", "api_key", fallback=""):
                k = magneto_cfg.get("GEMINI", "api_key", fallback="")
                if k:
                    config.set("GEMINI", "api_key", k)
                    config.set("GEMINI", "model", magneto_cfg.get("GEMINI", "model", fallback="gemini-2.5-flash"))

            # Fallback para Groq
            if not config.get("GROQ", "api_key", fallback=""):
                k = magneto_cfg.get("GROQ", "api_key", fallback="")
                if k:
                    config.set("GROQ", "api_key", k)
                    config.set("GROQ", "model", magneto_cfg.get("GROQ", "model", fallback="qwen/qwen3.8-27b"))

            # Fallback para OpenAI
            if not config.get("OPENAI", "api_key", fallback=""):
                k = magneto_cfg.get("OPENAI", "api_key", fallback="")
                if k:
                    config.set("OPENAI", "api_key", k)
                    config.set("OPENAI", "model", magneto_cfg.get("OPENAI", "model", fallback="gpt-4o-mini"))

            # Fallback para IA general
            if not config.has_option("IA", "enabled"):
                val = magneto_cfg.get("IA", "enabled", fallback="true")
                config.set("IA", "enabled", val)
            if not config.has_option("IA", "provider"):
                prov = magneto_cfg.get("ERIK", "provider", fallback="Gemini")
                config.set("IA", "provider", prov)
            if not config.has_option("IA", "model"):
                mod = magneto_cfg.get("ERIK", "model", fallback="gemini-2.5-flash")
                config.set("IA", "model", mod)
        except Exception as e:
            print(f"[Advanced WhatsApp Config] Error leyendo fallback de MAGNETO: {e}")

    # Valores por defecto seguros si aún no existen
    if not config.has_option("IA", "enabled"):
        config.set("IA", "enabled", "true")
    if not config.has_option("IA", "provider"):
        config.set("IA", "provider", "Gemini")
    if not config.has_option("IA", "model"):
        config.set("IA", "model", "gemini-2.5-flash")

    return config


def save_app_config(config) -> bool:
    """Guarda la configuración en el archivo local de ~/.magsapp/config.ini."""
    try:
        # Escritura atómica: si la app se cierra a mitad de camino no queda un config.ini truncado
        tmp = CONFIG_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            config.write(f)
        os.replace(tmp, CONFIG_PATH)
        return True
    except Exception as e:
        print(f"[Advanced WhatsApp Config] Error guardando config.ini: {e}")
        return False

FONT_SCALE_MIN = 70
FONT_SCALE_MAX = 200
FONT_SCALE_DEFAULT = 100


def clamp_font_scale(value) -> int:
    try:
        return max(FONT_SCALE_MIN, min(FONT_SCALE_MAX, int(value)))
    except (TypeError, ValueError):
        return FONT_SCALE_DEFAULT


def get_chat_font_scale() -> int:
    """Tamaño de fuente de los chats en porcentaje (100 = tamaño original)."""
    cfg = load_app_config()
    return clamp_font_scale(cfg.get("GENERAL", "chat_font_scale", fallback=FONT_SCALE_DEFAULT))


def set_chat_font_scale(pct: int) -> bool:
    cfg = load_app_config()
    cfg.set("GENERAL", "chat_font_scale", str(clamp_font_scale(pct)))
    return save_app_config(cfg)


def get_polish_ai() -> tuple:
    """(proveedor, modelo) elegidos por última vez en el redactor de respuestas; '' si no hay."""
    cfg = load_app_config()
    return (cfg.get("GENERAL", "polish_provider", fallback="").strip(),
            cfg.get("GENERAL", "polish_model", fallback="").strip())


def set_polish_ai(provider: str, model: str) -> bool:
    cfg = load_app_config()
    cfg.set("GENERAL", "polish_provider", provider or "")
    cfg.set("GENERAL", "polish_model", model or "")
    return save_app_config(cfg)


def get_polish_profile() -> bool:
    """Si el redactor y «Sugerir respuesta» usan mis notas y el análisis del contacto como contexto."""
    try:
        return load_app_config().getboolean("GENERAL", "polish_use_profile", fallback=True)
    except ValueError:
        return True


def set_polish_profile(on: bool) -> bool:
    cfg = load_app_config()
    cfg.set("GENERAL", "polish_use_profile", "true" if on else "false")
    return save_app_config(cfg)


def get_polish_mode() -> str:
    return load_app_config().get("GENERAL", "polish_mode", fallback="").strip()


def set_polish_mode(mode: str) -> bool:
    cfg = load_app_config()
    cfg.set("GENERAL", "polish_mode", mode or "")
    return save_app_config(cfg)


# Preferencias de notificaciones y de Windows (sección [GENERAL] de config.ini)
PREF_DEFAULTS = {
    "notify_enabled": True,      # notificaciones de mensajes nuevos
    "notify_sound": True,        # sonido de la notificación (y aviso suave con la ventana abierta)
    "notify_preview": True,      # mostrar el texto del mensaje en la notificación
    "notify_calls": True,        # aviso de llamada entrante
    "close_to_tray": True,       # la X deja Advanced WhatsApp en la bandeja (sigue avisando)
    "status_receipts": True,     # al ver un estado, el contacto ve que lo viste
    "typing_indicator": True,    # mandar "escribiendo…" mientras escribo
    "auto_transcribe": True,     # transcribir solos los audios que llegan (y los que mando)
    "tags_palette_open": True,  # panel de tags abierto debajo de los filtros de la lista de chats
}

_pref_cache = {}


def get_pref(key: str) -> bool:
    if key in _pref_cache:
        return _pref_cache[key]
    default = PREF_DEFAULTS.get(key, False)
    try:
        val = load_app_config().getboolean("GENERAL", key, fallback=default)
    except ValueError:
        val = default   # valor mal escrito a mano en config.ini
    _pref_cache[key] = val
    return val


def set_pref(key: str, value: bool) -> bool:
    cfg = load_app_config()
    cfg.set("GENERAL", key, "true" if value else "false")
    _pref_cache[key] = bool(value)
    return save_app_config(cfg)


def get_tag_palette_view() -> str:
    """Vista del panel de tags: 'grid' (solo iconos) o 'list' (con nombres)."""
    v = load_app_config().get("GENERAL", "tags_palette_view", fallback="grid").strip()
    return v if v in ("grid", "list") else "grid"


def set_tag_palette_view(view: str) -> bool:
    cfg = load_app_config()
    cfg.set("GENERAL", "tags_palette_view", view if view in ("grid", "list") else "grid")
    return save_app_config(cfg)


# Análisis psicológicos guardados: un .txt por cliente
ANALYSIS_DIR = os.path.join(DATA_DIR, "analisis")
os.makedirs(ANALYSIS_DIR, exist_ok=True)
