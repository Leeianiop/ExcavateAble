"""
Edge Node Logging Setup
Configures structured, rotating file + console logging for the capture node.
"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from ..config import LoggingConfig


def setup_logging(config: LoggingConfig) -> None:
    log_dir = Path(config.file).parent
    if str(log_dir) != ".":
        log_dir.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(getattr(logging, config.level.upper(), logging.INFO))

    if root.handlers:
        for h in list(root.handlers):
            root.removeHandler(h)

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(name)-18s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    root.addHandler(console)

    try:
        file_handler = RotatingFileHandler(
            config.file,
            maxBytes=config.max_bytes,
            backupCount=config.backup_count,
            encoding="utf-8",
        )
        file_handler.setFormatter(fmt)
        root.addHandler(file_handler)
    except (OSError, PermissionError):
            logging.getLogger(__name__).warning(
                f"Could not open log file {config.file}; continuing with console only"
            )
