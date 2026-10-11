import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent


def load_env() -> None:
    load_dotenv(ROOT / ".env")
    load_dotenv(ROOT / "test" / ".env")


def env(name: str, default: str | None = None) -> str:
    value = os.environ.get(name) or default
    if value is None:
        raise RuntimeError(f"{name} is not set (add it to test/.env)")
    return value


def results_dir() -> Path:
    path = Path(env("CALL_RESULTS_DIR", str(ROOT / ".calls")))
    path.mkdir(parents=True, exist_ok=True)
    return path
