"""Logging to polymarket-bot.log and stdout with timestamps and severity."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

_FMT = "%(asctime)s | %(levelname)-5s | %(name)s | %(message)s"


def setup_logging(log_file: str | Path, level: int = logging.INFO) -> logging.Logger:
    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()

    formatter = logging.Formatter(_FMT)

    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(formatter)
    root.addHandler(stream)

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    # Third-party noise stays at WARNING.
    for noisy in ("aiohttp", "websockets", "urllib3", "werkzeug"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    return logging.getLogger("polymarket-bot")
