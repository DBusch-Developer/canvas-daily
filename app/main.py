"""ASGI entry point: `uvicorn app.main:app`.

Loads .env first so the running app sees DATABASE_URL, SESSION_SECRET,
TOKEN_ENCRYPTION_KEY, and (optionally) GROQ_API_KEY.
"""

from dotenv import load_dotenv

load_dotenv()

from app.web import create_app  # noqa: E402  (must follow load_dotenv)

app = create_app()
