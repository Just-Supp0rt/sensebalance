"""Call Ollama to translate free-text notes. Best-effort, cached on the DB row."""
import logging

import httpx

from app import config

log = logging.getLogger(__name__)

_LANG_NAMES = {
    "th": "Thai",
    "en": "English",
    "cs": "Czech",
}

# Kept short: the note is written under time pressure at a tablet and the
# translation blocks nothing (see BackgroundTasks usage in app/routes/kiosk.py),
# but a hung request would still tie up a thread pool slot.
_TIMEOUT_SECONDS = 10


def translate(text: str, target_lang: str) -> str:
    text = (text or "").strip()
    if not text:
        return ""
    lang_name = _LANG_NAMES.get(target_lang, target_lang)
    try:
        resp = httpx.post(
            f"{config.OLLAMA_URL}/api/generate",
            json={
                "model": config.OLLAMA_MODEL,
                "prompt": (
                    f"Translate the following text to {lang_name}. "
                    "Output ONLY the translation, nothing else.\n\n"
                    f"{text}"
                ),
                "stream": False,
            },
            timeout=_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        return resp.json().get("response", "").strip()
    except Exception:
        log.exception("Ollama translation to %s failed", target_lang)
        return ""


def translate_to_thai(text: str) -> str:
    """Kept for compatibility with any in-flight callers; prefer translate()."""
    return translate(text, "th")
