"""Load development settings without overriding the hosting environment."""

import os
from pathlib import Path

from dotenv import load_dotenv


def load_environment(root: Path | None = None) -> None:
    # Hosting variables are authoritative; local credentials must never be
    # loaded from a deployment bundle.
    if os.getenv("VERCEL") == "1":
        return
    root = root or Path(__file__).resolve().parent.parent
    load_dotenv(root / ".env.local", override=False)
    load_dotenv(root / ".env", override=False)
