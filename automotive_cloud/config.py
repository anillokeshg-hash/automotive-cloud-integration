"""Settings, read from environment variables with safe local defaults."""

import os

HOST = os.getenv("HOST", "127.0.0.1")  # 127.0.0.1 = this computer only
PORT = int(os.getenv("PORT", "8080"))
API_KEY = os.getenv("AUTOMOTIVE_API_KEY", "dev-key")  # CHANGE for anything beyond local testing
DB_PATH = os.getenv("AUTOMOTIVE_DB", "automotive.db")

DEFAULT_KEY_WARNING = "Using the default API key 'dev-key'. Set AUTOMOTIVE_API_KEY before exposing this beyond your own computer."
