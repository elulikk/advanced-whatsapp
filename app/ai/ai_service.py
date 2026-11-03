"""
Advanced WhatsApp - Servicio de Inteligencia Artificial (AIService)
=========================================================
Conexión con APIs de LLM: Google Gemini, Groq Cloud, OpenAI y Anthropic (Claude).
Arquitectura sincronizada y compatible con MAGNETO.
"""

import os
import time
import requests
from typing import List, Dict, Optional, Any

from app.config import load_app_config

ANTHROPIC_API = "https://api.anthropic.com/v1"
ANTHROPIC_VERSION = "2023-06-01"
ANTHROPIC_FALLBACK_MODELS = ["claude-sonnet-5-5", "claude-opus-5-5", "claude-haiku-5-5", "claude-fable-5-1"]


class AIService:
    """
    Servicio unificado de IA para Advanced WhatsApp.
    Maneja la conexión con las APIs de Google Gemini, Groq Cloud y OpenAI,
    descubrimiento dinámico de modelos, fallback automático y pruebas de conexión.
    """

    def __init__(self, config=None):
        self.config = config if config is not None else load_app_config()
        self._gemini_configured = False
        self._last_gemini_key = None

    def reload_config(self, new_config=None):
        """Recarga la configuración desde disco o desde un objeto provisto."""
        self.config = new_config if new_config is not None else load_app_config()
        self._gemini_configured = False

    def is_enabled(self) -> bool:
        """Indica si la IA está habilitada globalmente en la configuración."""
        return self.config.getboolean("IA", "enabled", fallback=True)

    def _ensure_gemini(self, custom_key: Optional[str] = None) -> bool:
        """
        Configura google.generativeai de manera lazy con la API Key configurada.
        Utiliza el transporte 'rest' para máxima compatibilidad en Windows.
        """
        api_key = (custom_key or self.config.get("GEMINI", "api_key", fallback="")).strip()
        if not api_key:
            return False

        if self._gemini_configured and self._last_gemini_key == api_key:
            return True

        try:
            import google.generativeai as genai
            genai.configure(api_key=api_key, transport="rest")
            self._gemini_configured = True
            self._last_gemini_key = api_key
            return True
        except Exception as e:
            print(f"[AIService] Error configurando Gemini: {e}")
            return False

    def _format_error(self, provider: str, error: Any, model_name: str) -> str:
        """Formatea los errores típicos de API de forma comprensible para el usuario."""
        err_str = str(error)

        low = err_str.lower()
        if "429" in err_str or "quota" in low or "rate limit" in low or "rate_limit" in low:
            msg = f"⚠️ Cuota excedida en {provider} ({model_name}).\n"
            msg += "Se ha alcanzado el límite de peticiones o tokens de tu plan."
            if "retry" in err_str.lower():
                import re
                match = re.search(r"after (\d+\.?\d*)s|in (\d+\.?\d*)s", err_str.lower())
                if match:
                    sec = match.group(1) or match.group(2)
                    msg += f"\nSugerencia: Reintenta en {float(sec):.0f} segundos o cambia de modelo/proveedor."
            return msg

        if "401" in err_str or "unauthorized" in low or "api key" in low or "api_key_invalid" in low:
            return f"❌ Error de autenticación en {provider}.\nLa API Key configurada es inválida o expiró. Verifícala en Configuración."

        if "404" in err_str or "not_found" in err_str.lower() or "not exist" in err_str.lower():
            return f"⚠️ Modelo no encontrado en {provider} ({model_name}).\nEl modelo puede haber sido discontinuado o no está disponible en tu cuenta."

        if "timeout" in err_str.lower():
            return f"⏳ Tiempo de espera agotado con {provider}. La conexión está lenta o el servicio está saturado."

        return f"Error conectando con {provider} ({model_name}):\n{err_str[:250]}"

    # ─── DESCUBRIMIENTO DINÁMICO DE MODELOS ────────────────────────────────────

    def get_gemini_models(self, custom_key: Optional[str] = None) -> List[str]:
        """Obtiene la lista de modelos activos en Google Gemini."""
        fallback_gemini = [
            "gemini-2.5-flash",
            "gemini-2.5-flash-lite",
            "gemini-3.5-flash-lite",
            "gemini-3.7-flash",
            "gemini-3.5-flash",
            "gemini-2.5-pro",
            "gemini-flash-latest"
        ]

        if not self._ensure_gemini(custom_key):
            return fallback_gemini

        try:
            import google.generativeai as genai
            raw_models = []
            for m in genai.list_models():
                if "generateContent" in getattr(m, "supported_generation_methods", []):
                    name = m.name.replace("models/", "")
                    raw_models.append(name)

            excluded = ["robotics", "lyria", "tts", "transcribe", "banana", "computer-use"]
            filtered = [m for m in raw_models if not any(ex in m.lower() for ex in excluded)]

            priority = [
                "gemini-2.5-flash",
                "gemini-2.5-flash-lite",
                "gemini-3.5-flash-lite",
                "gemini-3.7-flash",
                "gemini-3.5-flash",
                "gemini-2.5-pro",
                "gemini-flash-latest"
            ]

            sorted_models = []
            for p in priority:
                if p in filtered and p not in sorted_models:
                    sorted_models.append(p)
            for m in filtered:
                if m not in sorted_models:
                    sorted_models.append(m)

            return sorted_models or fallback_gemini
        except Exception as e:
            print(f"[AIService] Error obteniendo modelos de Gemini: {e}")
            return fallback_gemini

    def get_groq_models(self, custom_key: Optional[str] = None) -> List[str]:
        """Obtiene la lista de modelos activos en Groq Cloud."""
        fallback_groq = [
            "qwen/qwen3.8-27b",
            "openai/gpt-oss-120b",
            "openai/gpt-oss-20b",
            "llama-3.3-70b-versatile",
            "allam-2-7b"
        ]

        api_key = (custom_key or self.config.get("GROQ", "api_key", fallback="")).strip()
        if not api_key:
            return fallback_groq

        url = "https://api.groq.com/openai/v1/models"
        headers = {"Authorization": f"Bearer {api_key}"}

        try:
            res = requests.get(url, headers=headers, timeout=10)
            res.raise_for_status()
            models_raw = [m["id"] for m in res.json().get("data", [])]

            filtered = [
                m for m in models_raw
                if not any(ex in m.lower() for ex in ["whisper", "prompt-guard", "guard", "orpheus"])
            ]

            priority = [
                "qwen/qwen3.8-27b",
                "openai/gpt-oss-120b",
                "openai/gpt-oss-20b",
                "llama-3.3-70b-versatile",
                "allam-2-7b"
            ]

            sorted_models = []
            for p in priority:
                if p in filtered and p not in sorted_models:
                    sorted_models.append(p)
            for m in filtered:
                if m not in sorted_models:
                    sorted_models.append(m)

            return sorted_models or fallback_groq
        except Exception as e:
            print(f"[AIService] Error obteniendo modelos de Groq: {e}")
            return fallback_groq

    def get_openai_models(self, custom_key: Optional[str] = None) -> List[str]:
        """Obtiene la lista de modelos activos en OpenAI."""
        fallback_openai = [
            "gpt-4o-mini",
            "gpt-4o",
            "o3-mini",
            "gpt-5.6-luna",
            "gpt-5.6-terra",
            "gpt-4-turbo"
        ]

        api_key = (custom_key or self.config.get("OPENAI", "api_key", fallback="")).strip()
        if not api_key:
            return fallback_openai

        url = "https://api.openai.com/v1/models"
        headers = {"Authorization": f"Bearer {api_key}"}

        try:
            res = requests.get(url, headers=headers, timeout=10)
            res.raise_for_status()
            models_raw = [m["id"] for m in res.json().get("data", [])]

            filtered = [
                m for m in models_raw
                if any(k in m.lower() for k in ["gpt", "o1", "o3"])
                and not any(ex in m.lower() for ex in ["audio", "realtime", "whisper", "dall-e", "tts", "embedding"])
            ]

            priority = [
                "gpt-4o-mini",
                "gpt-4o",
                "o3-mini",
                "gpt-5.6-luna",
                "gpt-5.6-terra",
                "gpt-4-turbo"
            ]

            sorted_models = []
            for p in priority:
                if p in filtered and p not in sorted_models:
                    sorted_models.append(p)
            for m in sorted(filtered):
                if m not in sorted_models:
                    sorted_models.append(m)

            return sorted_models or fallback_openai
        except Exception as e:
            print(f"[AIService] Error obteniendo modelos de OpenAI: {e}")
            return fallback_openai

    def _anthropic_key(self, custom_key: Optional[str] = None) -> str:
        """Clave de Anthropic: la pasada, la de config.ini ([ANTHROPIC] api_key) o la variable ANTHROPIC_API_KEY."""
        return (custom_key or self.config.get("ANTHROPIC", "api_key", fallback="")
                or os.environ.get("ANTHROPIC_API_KEY", "")).strip()

    @staticmethod
    def _anthropic_headers(key: str) -> Dict[str, str]:
        return {"x-api-key": key, "anthropic-version": ANTHROPIC_VERSION, "content-type": "application/json"}

    def get_anthropic_models(self, custom_key: Optional[str] = None) -> List[str]:
        """Obtiene la lista de modelos Claude disponibles para la clave (o la lista conocida si no hay clave)."""
        key = self._anthropic_key(custom_key)
        if not key:
            return list(ANTHROPIC_FALLBACK_MODELS)
        try:
            res = requests.get(f"{ANTHROPIC_API}/models", headers=self._anthropic_headers(key),
                               params={"limit": 100}, timeout=10)
            res.raise_for_status()
            ids = [m["id"] for m in res.json().get("data", []) if m.get("id", "").startswith("claude")]
            ordered = ([m for m in ANTHROPIC_FALLBACK_MODELS if m in ids]
                       + [m for m in ids if m not in ANTHROPIC_FALLBACK_MODELS])
            return ordered or list(ANTHROPIC_FALLBACK_MODELS)
        except Exception as e:
            print(f"[AIService] Error obteniendo modelos de Anthropic: {e}")
            return list(ANTHROPIC_FALLBACK_MODELS)

    # ─── PETICIONES DIRECTAS POR PROVEEDOR ─────────────────────────────────────

    def get_gemini_response(self, prompt: str, model_name: Optional[str] = None, api_key: Optional[str] = None) -> str:
        """Ejecuta una petición de generación con Google Gemini."""
        if not model_name:
            model_name = self.config.get("GEMINI", "model", fallback="gemini-2.5-flash")

        if not self._ensure_gemini(api_key):
            return "Error: No se ha configurado la API Key de Google Gemini."

        try:
            import google.generativeai as genai
            model = genai.GenerativeModel(model_name)
            response = model.generate_content(prompt)
            return response.text or ""
        except Exception as e:
            return self._format_error("Gemini", e, model_name)

    def get_groq_response(self, prompt: str, model: Optional[str] = None, api_key: Optional[str] = None,
                          timeout: int = 30) -> str:
        """Ejecuta una petición de generación con Groq Cloud."""
        if not model:
            model = self.config.get("GROQ", "model", fallback="qwen/qwen3.8-27b")

        key = (api_key or self.config.get("GROQ", "api_key", fallback="")).strip()
        if not key:
            return "Error: No se ha configurado la API Key de Groq."

        url = "https://api.groq.com/openai/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json"
        }
        data = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.7
        }

        try:
            res = requests.post(url, headers=headers, json=data, timeout=timeout)
            res.raise_for_status()
            parsed = res.json()
            return parsed["choices"][0]["message"]["content"]
        except Exception as e:
            return self._format_error("Groq", e, model)

    def get_openai_response(self, prompt: str, model: Optional[str] = None, api_key: Optional[str] = None,
                            timeout: int = 30) -> str:
        """Ejecuta una petición de generación con OpenAI."""
        if not model:
            model = self.config.get("OPENAI", "model", fallback="gpt-4o-mini")

        key = (api_key or self.config.get("OPENAI", "api_key", fallback="")).strip()
        if not key:
            return "Error: No se ha configurado la API Key de OpenAI."

        url = "https://api.openai.com/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json"
        }
        data = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.7
        }

        try:
            res = requests.post(url, headers=headers, json=data, timeout=timeout)
            res.raise_for_status()
            parsed = res.json()
            return parsed["choices"][0]["message"]["content"]
        except Exception as e:
            return self._format_error("OpenAI", e, model)

    def get_anthropic_response(self, prompt: str, model: Optional[str] = None, api_key: Optional[str] = None,
                               timeout: int = 60, max_tokens: int = 4096) -> str:
        """Ejecuta una petición de generación con Anthropic (Claude) por la API de Messages."""
        if not model:
            model = self.config.get("ANTHROPIC", "model", fallback="claude-sonnet-5-5")

        key = self._anthropic_key(api_key)
        if not key:
            return "Error: No se ha configurado la API Key de Anthropic."

        data = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }

        try:
            res = requests.post(f"{ANTHROPIC_API}/messages", headers=self._anthropic_headers(key), json=data,
                                timeout=timeout)
            if not res.ok:
                try:
                    detail = res.json().get("error", {}).get("message", "")
                except ValueError:
                    detail = ""
                raise RuntimeError(f"{res.status_code} {detail or res.reason}")
            blocks = res.json().get("content", [])
            return "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
        except Exception as e:
            return self._format_error("Anthropic", e, model)

    # ─── VERIFICACIÓN Y PRUEBAS DE CONEXIÓN (API HEALTH CHECK) ────────────────

    def test_connection(
        self,
        provider: str,
        api_key: Optional[str] = None,
        model_name: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Prueba la conectividad y validez de la API Key enviando una solicitud ligera.
        Retorna un diccionario estructurado con el estado, latencia y mensaje descriptivo.
        """
        provider_norm = (provider or "gemini").strip().lower()
        test_prompt = "Hola, responde únicamente con la palabra OK."
        start_time = time.time()

        if "gemini" in provider_norm:
            target_model = model_name or self.config.get("GEMINI", "model", fallback="gemini-2.5-flash")
            response = self.get_gemini_response(test_prompt, model_name=target_model, api_key=api_key)
            provider_title = "Google Gemini"
        elif "groq" in provider_norm:
            target_model = model_name or self.config.get("GROQ", "model", fallback="qwen/qwen3.8-27b")
            response = self.get_groq_response(test_prompt, model=target_model, api_key=api_key)
            provider_title = "Groq Cloud"
        elif "openai" in provider_norm:
            target_model = model_name or self.config.get("OPENAI", "model", fallback="gpt-4o-mini")
            response = self.get_openai_response(test_prompt, model=target_model, api_key=api_key)
            provider_title = "OpenAI"
        elif "anthropic" in provider_norm or "claude" in provider_norm:
            target_model = model_name or self.config.get("ANTHROPIC", "model", fallback="claude-sonnet-5-5")
            response = self.get_anthropic_response(test_prompt, model=target_model, api_key=api_key)
            provider_title = "Anthropic (Claude)"
        else:
            return {
                "success": False,
                "provider": provider,
                "model": model_name or "",
                "latency_ms": 0,
                "response": "",
                "error": f"Proveedor '{provider}' no soportado."
            }

        elapsed_ms = int((time.time() - start_time) * 1000)
        is_error = self.is_error_response(response)

        if is_error:
            return {
                "success": False,
                "provider": provider_title,
                "model": target_model,
                "latency_ms": elapsed_ms,
                "response": "",
                "error": response
            }
        else:
            clean_resp = (response or "").strip()
            return {
                "success": True,
                "provider": provider_title,
                "model": target_model,
                "latency_ms": elapsed_ms,
                "response": clean_resp,
                "error": ""
            }

    @staticmethod
    def is_error_response(text: str) -> bool:
        """Determina si la cadena de texto retornada representa un mensaje de error."""
        if not text or not isinstance(text, str):
            return True
        t = text.strip()
        if not t:
            return True
        if t.lower().startswith("error"):
            return True
        for mark in ("⚠️", "❌", "⏳", "Cuota excedida", "Error de autenticación", "Error conectando"):
            if mark in t:
                return True
        return False

    # ─── COMPLETION INTELIGENTE CON FALLBACK ─────────────────────────────────

    def generate_ai_completion(
        self,
        prompt: str,
        model_name: Optional[str] = None,
        provider: Optional[str] = None
    ) -> str:
        """
        Genera una respuesta utilizando el proveedor y modelo configurados,
        con conmutación secuencial y fallback automático idéntico a MAGNETO.
        """
        if not self.is_enabled():
            return "La Inteligencia Artificial se encuentra desactivada en los ajustes."

        active_provider = (provider or self.config.get("IA", "provider", fallback="Gemini")).strip().lower()
        active_model = model_name or self.config.get("IA", "model", fallback="")

        # 0. Si el proveedor activo es Anthropic (Claude)
        if "anthropic" in active_provider or "claude" in active_provider:
            res = self.get_anthropic_response(prompt, model=active_model)
            if res and not self.is_error_response(res):
                return res
            if self._ensure_gemini():
                res_gemini = self.get_gemini_response(prompt, model_name="gemini-2.5-flash")
                if res_gemini and not self.is_error_response(res_gemini):
                    return res_gemini
            res_openai = self.get_openai_response(prompt)
            if res_openai and not self.is_error_response(res_openai):
                return res_openai
            return res

        # 1. Si el proveedor activo es Groq
        if "groq" in active_provider:
            res = self.get_groq_response(prompt, model=active_model)
            if res and not self.is_error_response(res):
                return res
            # Fallback a Gemini
            if self._ensure_gemini():
                res_gemini = self.get_gemini_response(prompt, model_name="gemini-2.5-flash")
                if res_gemini and not self.is_error_response(res_gemini):
                    return res_gemini
            # Fallback a OpenAI
            res_openai = self.get_openai_response(prompt)
            if res_openai and not self.is_error_response(res_openai):
                return res_openai
            return res

        # 2. Si el proveedor activo es OpenAI
        if "openai" in active_provider:
            res = self.get_openai_response(prompt, model=active_model)
            if res and not self.is_error_response(res):
                return res
            # Fallback a Gemini
            if self._ensure_gemini():
                res_gemini = self.get_gemini_response(prompt, model_name="gemini-2.5-flash")
                if res_gemini and not self.is_error_response(res_gemini):
                    return res_gemini
            # Fallback a Groq
            res_groq = self.get_groq_response(prompt)
            if res_groq and not self.is_error_response(res_groq):
                return res_groq
            return res

        # 3. Proveedor por defecto: Gemini
        res = self.get_gemini_response(prompt, model_name=active_model or "gemini-2.5-flash")
        if res and not self.is_error_response(res):
            return res

        # Fallback a Groq
        res_groq = self.get_groq_response(prompt)
        if res_groq and not self.is_error_response(res_groq):
            return res_groq

        # Fallback a OpenAI
        res_openai = self.get_openai_response(prompt)
        if res_openai and not self.is_error_response(res_openai):
            return res_openai

        return res or "Error: Ningún proveedor de IA disponible."
