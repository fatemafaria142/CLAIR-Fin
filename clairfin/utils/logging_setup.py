from __future__ import annotations

import logging
import warnings
from logging.handlers import RotatingFileHandler

from configs.settings import get_settings

_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
_MAX_BYTES = 5 * 1024 * 1024
_BACKUP_COUNT = 3


def _silence_known_noisy_warnings() -> None:
    """`with_structured_output` stashes the raw structured-output object in
    `AIMessage.additional_kwargs["parsed"]` (langchain_openai/chat_models/base.py). Re-serializing
    that message later validates it against a generic `parsed: None` annotation from the OpenAI SDK
    and always warns, even though the value round-trips correctly — cosmetic noise, not a real
    problem, on every one of our structured-output calls (tabular/chart/hedge extraction,
    entailment, planner). Filtered at the source rather than per call site."""
    warnings.filterwarnings(
        "ignore",
        message=r"Pydantic serializer warnings:\s*\n\s*PydanticSerializationUnexpectedValue\(Expected `none` - serialized value may not be as expected \[field_name='parsed'.*",
        category=UserWarning,
        module="pydantic.main",
    )


def configure_logging(name: str, level: int = logging.INFO) -> None:
    """Set up console + rotating file logging for one entry point. `name` becomes the log
    filename (`logs/<name>.log`) — call once per process (`run`, `ingest`, `server`). Idempotent:
    safe to call more than once (e.g. uvicorn's `--reload` re-importing the module) without piling
    up duplicate handlers."""
    root = logging.getLogger()
    root.setLevel(level)

    if any(getattr(h, "_clairfin_log_name", None) == name for h in root.handlers):
        return

    _silence_known_noisy_warnings()

    settings = get_settings()
    settings.paths.ensure_runtime_dirs()
    formatter = logging.Formatter(_LOG_FORMAT)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler._clairfin_log_name = name
    root.addHandler(console_handler)

    file_handler = RotatingFileHandler(
        settings.paths.logs_dir / f"{name}.log",
        maxBytes=_MAX_BYTES,
        backupCount=_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    file_handler._clairfin_log_name = name
    root.addHandler(file_handler)
