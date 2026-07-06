"""Call Ollama to translate free-text note to Thai. Best-effort, cached on the DB row."""
import logging
import httpx

from app import config

log = logging.getLogger(__name__)


def translate_to_thai(text: str) -> str:
    if not text.strip():
        return ""
    try:
        resp = httpx.post(
            f"{config.OLLAMA_URL}/api/generate",
            json={
                "model": config.OLLAMA_MODEL,
                "prompt": (
                    "Translate the following text to Thai. "
                    "Output ONLY the Thai translation, nothing else.\n\n"
                    f"{text}"
                ),
                "stream": False,
            },
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json().get("response", "").strip()
    except Exception:
        log.exception("Ollama translation failed")
        return ""
