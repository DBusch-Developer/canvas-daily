"""Load local secrets from .env before tests run.

Keeps TEST_DATABASE_URL and TOKEN_ENCRYPTION_KEY out of the repo while making
them available to the integration tests. No model imports here, so a missing
ORM module only fails its own test file, not the whole suite.
"""

from dotenv import load_dotenv

load_dotenv()
