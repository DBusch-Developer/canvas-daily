"""ASGI entry point: `uvicorn app.main:app`.

Loads .env first so the running app sees DATABASE_URL, SESSION_SECRET,
TOKEN_ENCRYPTION_KEY, and (optionally) GROQ_API_KEY.
"""

from dotenv import load_dotenv

# override=True so a real .env value wins over any stale/placeholder variable
# already set in the OS/shell environment. In prod/CI there is no .env file, so
# load_dotenv finds nothing and this is a no-op (Render/CI env vars are used).
load_dotenv(override=True)

from app.web import create_app  # noqa: E402  (must follow load_dotenv)

app = create_app()
