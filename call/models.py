import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal

from settings import results_dir

CallStatus = Literal["pending", "answered", "completed", "no_answer", "failed", "timeout"]


@dataclass
class CallRequest:
    to: str
    task: str
    fields: list[str] = field(default_factory=list)
    caller_name: str = "Alex"

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, raw: str) -> "CallRequest":
        return cls(**json.loads(raw))


@dataclass
class CallResult:
    room: str
    to: str
    status: CallStatus = "pending"
    success: bool | None = None
    summary: str = ""
    details: dict[str, str] = field(default_factory=dict)
    transcript: list[dict[str, str]] = field(default_factory=list)
    duration_seconds: float = 0.0
    error: str | None = None


def result_path(room: str) -> Path:
    return results_dir() / f"{room}.json"


def write_result(result: CallResult) -> None:
    path = result_path(result.room)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(asdict(result), indent=2))
    tmp.replace(path)


def read_result(room: str) -> CallResult | None:
    path = result_path(room)
    if not path.exists():
        return None
    return CallResult(**json.loads(path.read_text()))
