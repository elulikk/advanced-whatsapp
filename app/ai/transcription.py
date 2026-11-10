"""
Advanced WhatsApp - Transcripción de notas de voz
=======================================
Usa Whisper vía API (Groq primero, OpenAI como respaldo) con las keys ya configuradas en la app.
"""

import os
from typing import Optional

import requests

from app.config import load_app_config

GROQ_TRANSCRIPTION_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
OPENAI_TRANSCRIPTION_URL = "https://api.openai.com/v1/audio/transcriptions"


def transcribe_audio(path: str, config=None) -> str:
    """Devuelve el texto de un audio. Lanza RuntimeError si ningún proveedor pudo transcribirlo."""
    if not os.path.isfile(path):
        raise RuntimeError(f"No se encontró el archivo de audio: {path}")
    cfg = config if config is not None else load_app_config()

    providers = []
    groq_key = cfg.get("GROQ", "api_key", fallback="").strip()
    if groq_key:
        providers.append(("Groq", GROQ_TRANSCRIPTION_URL, groq_key, "whisper-large-v3-turbo"))
    openai_key = cfg.get("OPENAI", "api_key", fallback="").strip()
    if openai_key:
        providers.append(("OpenAI", OPENAI_TRANSCRIPTION_URL, openai_key, "whisper-1"))

    if not providers:
        raise RuntimeError("Configura una API key de Groq u OpenAI en Ajustes para transcribir audios.")

    last_error: Optional[str] = None
    for name, url, key, model in providers:
        try:
            with open(path, "rb") as f:
                res = requests.post(
                    url,
                    headers={"Authorization": f"Bearer {key}"},
                    files={"file": (os.path.basename(path), f, "audio/ogg")},
                    data={"model": model, "response_format": "json"},
                    timeout=120,
                )
            if res.status_code == 200:
                return (res.json().get("text") or "").strip()
            last_error = f"{name} respondió HTTP {res.status_code}: {res.text[:200]}"
        except Exception as e:
            last_error = f"{name}: {e}"

    raise RuntimeError(last_error or "No se pudo transcribir el audio")
