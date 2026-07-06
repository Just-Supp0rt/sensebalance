import os
from pathlib import Path

DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
DB_PATH = DATA_DIR / "app.db"
SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-change-in-prod")
BASE_URL = os.getenv("BASE_URL", "http://localhost:8093")

GMAIL_USER = os.getenv("GMAIL_USER", "")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "")

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://192.168.1.159:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen3:8b")

MAGIC_LINK_TTL_MINUTES = 30
SESSION_TTL_DAYS = 30
