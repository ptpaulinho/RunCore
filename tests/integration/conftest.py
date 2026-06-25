"""Integration test configuration — skip markers for missing API keys."""
import os
import pytest


def groq_available() -> bool:
    try:
        from runcore.providers.groq import GroqProvider
        return GroqProvider().is_available()
    except ImportError:
        return False


def gemini_available() -> bool:
    try:
        from runcore.providers.gemini import GeminiProvider
        return GeminiProvider().is_available()
    except ImportError:
        return False


def ollama_available() -> bool:
    try:
        from runcore.providers.ollama import OllamaProvider
        return OllamaProvider().is_available()
    except ImportError:
        return False


requires_groq = pytest.mark.skipif(
    not groq_available(),
    reason="GROQ_API_KEY not set or groq package not installed",
)

requires_gemini = pytest.mark.skipif(
    not gemini_available(),
    reason="GEMINI_API_KEY not set or google-generativeai not installed",
)

requires_ollama = pytest.mark.skipif(
    not ollama_available(),
    reason="Ollama not running or ollama package not installed",
)
