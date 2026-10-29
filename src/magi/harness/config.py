"""Configuration for the local Ollama harness experiment.

This boundary centralizes settings; it does not implement a second model provider.
Paths in TOML are relative to the configuration file, never the shell directory.
"""

import math
import tomllib
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit


@dataclass(frozen=True, kw_only=True)
class HarnessConfig:
    docs_root: Path
    db_path: Path
    events_path: Path
    business_db_path: Path | None = None
    provider: str = "ollama"
    model: str = "qwen3.5:4b"
    base_url: str = "http://127.0.0.1:11434"
    timeout_seconds: float = 60.0
    num_ctx: int = 8192
    num_predict: int = 512
    max_model_calls: int = 4
    max_tool_calls: int = 3

    def __post_init__(self) -> None:
        if self.provider != "ollama":
            raise ValueError("当前试验仅接入 ollama，第二提供方尚未实现")
        _nonempty_string(self.model, "model.name")
        _nonempty_string(self.base_url, "model.base_url")
        try:
            url = urlsplit(self.base_url)
            port = url.port
        except ValueError as exc:
            raise ValueError("model.base_url 必须是合法 HTTP(S) 地址") from exc
        if (
            url.scheme not in {"http", "https"}
            or not url.hostname
            or url.username is not None
            or url.password is not None
            or url.query
            or url.fragment
            or (port is not None and port == 0)
        ):
            raise ValueError("model.base_url 必须是无凭据的 HTTP(S) 服务地址")
        if (
            type(self.timeout_seconds) not in {int, float}
            or not math.isfinite(self.timeout_seconds)
            or self.timeout_seconds <= 0
        ):
            raise ValueError("model.timeout_seconds 必须是有限的正数")
        for field, value, minimum in (
            ("model.num_ctx", self.num_ctx, 1),
            ("model.num_predict", self.num_predict, 1),
            ("limits.model_calls", self.max_model_calls, 1),
            ("limits.tool_calls", self.max_tool_calls, 0),
        ):
            if type(value) is not int or value < minimum:
                raise ValueError(f"{field} 必须是大于等于 {minimum} 的整数")
        if self.business_db_path is None:
            object.__setattr__(
                self, "business_db_path", self.db_path.parent / "personal.sqlite3"
            )
        for name in ("docs_root", "db_path", "events_path", "business_db_path"):
            value = getattr(self, name)
            if not isinstance(value, Path):
                raise ValueError(f"{name} 必须是 pathlib.Path")
            object.__setattr__(self, name, value.resolve())
        if not self.docs_root.is_dir():
            raise ValueError("tools.root 必须是已存在的目录")
        for name, path in (
            ("storage.checkpoint_db", self.db_path),
            ("storage.event_log", self.events_path),
            ("storage.business_db", self.business_db_path),
        ):
            if path.exists() and not path.is_file():
                raise ValueError(f"{name} 必须指向文件")
        if len({self.db_path, self.events_path, self.business_db_path}) != 3:
            raise ValueError(
                "checkpoint_db、event_log 和 business_db 不能使用同一个文件"
            )


def _nonempty_string(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} 必须是非空字符串")
    return value


def load_config(path: Path) -> HarnessConfig:
    """Read a local TOML file without creating directories or contacting a model."""
    path = Path(path).resolve()
    try:
        with path.open("rb") as file:
            data = tomllib.load(file)
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise ValueError("配置文件必须是合法的 UTF-8 TOML") from exc

    fields = {
        "model": {
            "provider": "provider",
            "name": "model",
            "base_url": "base_url",
            "timeout_seconds": "timeout_seconds",
            "num_ctx": "num_ctx",
            "num_predict": "num_predict",
        },
        "storage": {
            "checkpoint_db": "db_path",
            "event_log": "events_path",
            "business_db": "business_db_path",
        },
        "tools": {"root": "docs_root"},
        "limits": {
            "model_calls": "max_model_calls",
            "tool_calls": "max_tool_calls",
        },
    }
    unknown = set(data) - set(fields)
    if unknown:
        raise ValueError(f"未知配置表：{', '.join(sorted(unknown))}")
    values = {
        "docs_root": "docs",
        "db_path": ".magi/harness.sqlite3",
        "events_path": ".magi/harness-events.jsonl",
        "business_db_path": ".magi/personal.sqlite3",
    }
    for section, mapping in fields.items():
        table = data.get(section, {})
        if not isinstance(table, dict):
            raise ValueError(f"{section} 必须是配置表")
        unknown = set(table) - set(mapping)
        if unknown:
            raise ValueError(f"{section} 存在未知字段：{', '.join(sorted(unknown))}")
        values.update({mapping[key]: value for key, value in table.items()})
    for name in ("docs_root", "db_path", "events_path", "business_db_path"):
        value = _nonempty_string(values[name], name)
        values[name] = path.parent / value
    return HarnessConfig(**values)
