"""Model, credentials, and the limits that keep a confused run from burning tokens.

The model name appears exactly once in this project — here. Everything else imports it,
so there is no second place for a stray model to creep in.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# The only model this project may use, anywhere, for anything.
MODEL = "gpt-5.6-luna"

# Every call goes through the Portkey gateway.
PORTKEY_BASE_URL = os.environ.get("PORTKEY_BASE_URL", "https://api.portkey.ai/v1")

# --- limits ---------------------------------------------------------------
# A model that cannot find what it needs will keep calling tools until something
# stops it. These are what stop it. They are deliberately small: the shop has nine
# tables and three tickets, so an agent needing more than this is lost, not thorough.

MAX_TURNS = 10          # model round-trips per agent invocation; the Boss needs room
MAX_DEPTH = 3           # Boss -> Inventory -> Accounting is depth 3; no deeper
MAX_DELEGATIONS = 8     # total handoffs in one ticket run
MAX_TOOL_CALLS = 40     # total tool calls in one ticket run, across all agents
REQUEST_TIMEOUT = 90.0  # seconds per model call


def find_dotenv() -> Path | None:
    """Locate `.env`, checking the project root first, then directories above it.

    The key currently lives one level up, in the shared course folder. Walking up
    finds it there. For the public repo this should move into the project root, so
    the project does not depend on a file outside itself.
    """
    for directory in [PROJECT_ROOT, *PROJECT_ROOT.parents]:
        candidate = directory / ".env"
        if candidate.is_file():
            return candidate
    return None


def load_environment() -> None:
    """Load `.env` into the process environment, if one can be found."""
    path = find_dotenv()
    if path is not None:
        load_dotenv(path, override=False)


@lru_cache(maxsize=1)
def get_client() -> OpenAI:
    """The OpenAI client pointed at Portkey.

    Reads `PORTKEY_API_KEY` from the environment at call time. The key is never
    written to a file, a log, or a trace, and never reaches the browser — the frontend
    talks to this backend, and only this backend holds the key.
    """
    load_environment()
    api_key = os.environ.get("PORTKEY_API_KEY")
    if not api_key:
        raise RuntimeError(
            "PORTKEY_API_KEY is not set. Put it in a .env at the project root "
            "(see .env.example). It is read at runtime and never hard-coded."
        )

    return OpenAI(api_key=api_key, base_url=PORTKEY_BASE_URL, timeout=REQUEST_TIMEOUT)


def key_is_available() -> bool:
    """Whether a key is present, without revealing anything about it."""
    load_environment()
    return bool(os.environ.get("PORTKEY_API_KEY"))
