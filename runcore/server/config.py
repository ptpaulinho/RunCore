"""Local config store — persists provider API keys so the dashboard works without terminal env vars.

Keys are saved to .runcore/config.json and loaded into os.environ at startup and on update,
so the existing providers (which read os.environ.get("GROQ_API_KEY") etc.) pick them up live.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

_CONFIG_DIR = Path(".runcore")
_CONFIG_PATH = _CONFIG_DIR / "config.json"
_lock = threading.Lock()

# Maps a logical provider -> the env var its client reads.
PROVIDER_ENV = {
    "groq": "GROQ_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "ollama": "OLLAMA_HOST",
}


def _read() -> dict:
    if not _CONFIG_PATH.exists():
        return {}
    try:
        return json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write(data: dict) -> None:
    _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    _CONFIG_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
    # Lock down permissions — file holds secrets.
    try:
        os.chmod(_CONFIG_PATH, 0o600)
    except OSError:
        pass


def load_into_env() -> None:
    """Apply every saved key to os.environ (does not overwrite a key already set in the real env)."""
    data = _read()
    keys = data.get("keys", {})
    with _lock:
        for provider, value in keys.items():
            env_var = PROVIDER_ENV.get(provider)
            if env_var and value and not os.environ.get(env_var):
                os.environ[env_var] = value


def set_keys(keys: dict[str, str]) -> dict[str, str]:
    """Persist provided keys and apply them to os.environ immediately.

    Empty/blank values clear that provider's saved key. Returns the saved key map (masked upstream).
    """
    with _lock:
        data = _read()
        stored = data.get("keys", {})
        for provider, value in keys.items():
            if provider not in PROVIDER_ENV:
                continue
            value = (value or "").strip()
            env_var = PROVIDER_ENV[provider]
            if value:
                stored[provider] = value
                os.environ[env_var] = value
            else:
                stored.pop(provider, None)
                # only clear env if we were the ones that set it
                os.environ.pop(env_var, None)
        data["keys"] = stored
        _write(data)
        return stored


def get_setting(name: str, default=None):
    return _read().get("settings", {}).get(name, default)


def set_setting(name: str, value) -> None:
    with _lock:
        data = _read()
        settings = data.get("settings", {})
        settings[name] = value
        data["settings"] = settings
        _write(data)


def mask(value: str) -> str:
    """Mask a secret for display: keep last 4 chars."""
    if not value:
        return ""
    if len(value) <= 4:
        return "••••"
    return "••••" + value[-4:]


def provider_status() -> dict[str, dict]:
    """Return availability + masked key for each provider, for the Settings UI."""
    data = _read()
    saved = data.get("keys", {})
    out: dict[str, dict] = {}

    # Groq
    out["groq"] = _check_provider("groq", saved)
    # Gemini
    out["gemini"] = _check_provider("gemini", saved)
    # Ollama (local, no key — check if the daemon answers)
    out["ollama"] = _check_ollama(saved)
    # OpenAI / Anthropic (only key presence — paid, optional)
    out["openai"] = _check_keyonly("openai", saved)
    out["anthropic"] = _check_keyonly("anthropic", saved)
    return out


def _check_provider(name: str, saved: dict) -> dict:
    env_var = PROVIDER_ENV[name]
    key = os.environ.get(env_var) or saved.get(name, "")
    available = False
    detail = "No key set"
    if key:
        try:
            mod = __import__(f"runcore.providers.{name}", fromlist=["*"])
            cls = getattr(mod, f"{name.capitalize()}Provider")
            available = cls(api_key=key).is_available()
            detail = "Ready" if available else "Key set but provider not reachable"
        except Exception as exc:  # missing package, etc.
            detail = f"Key set — install package: pip install runcore[{name}] ({exc.__class__.__name__})"
    return {"available": available, "has_key": bool(key), "masked": mask(key), "detail": detail}


def _check_ollama(saved: dict) -> dict:
    host = os.environ.get("OLLAMA_HOST") or saved.get("ollama", "")
    try:
        from runcore.providers.ollama import OllamaProvider
        kwargs = {"host": host} if host else {}
        available = OllamaProvider(**kwargs).is_available()
        detail = "Running" if available else "Ollama not running (start the Ollama app)"
    except Exception:
        available = False
        detail = "ollama package not installed"
    return {"available": available, "has_key": bool(host), "masked": host or "localhost:11434", "detail": detail}


def _check_keyonly(name: str, saved: dict) -> dict:
    env_var = PROVIDER_ENV[name]
    key = os.environ.get(env_var) or saved.get(name, "")
    return {
        "available": bool(key),
        "has_key": bool(key),
        "masked": mask(key),
        "detail": "Key set" if key else "Optional — paid provider, no key set",
    }
