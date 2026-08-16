"""Structured logging used across the whole pipeline.

A single configured logger so every stage logs in a consistent format. Uses
`rich` for clean colored terminal output when available, falling back to the
stdlib formatter otherwise.

Also provides :func:`warn_once` — report a FACT once per run, however many times
the code that discovers it runs. Much of this pipeline is deliberately built
from pure functions that are called repeatedly (the build sequencer resolves a
feature's dimensions while choosing the base, again while sorting, again while
emitting), so a plain ``log.warning`` inside one of them reports the same single
finding four or five times and buries the signal it exists to raise.
"""
from __future__ import annotations

import logging
import sys

# Loggers already given a handler, by name. Previously a single global boolean:
# whichever logger was requested FIRST claimed the flag, so a first call for any
# other name left "pipeline" permanently handler-less (and silent).
_CONFIGURED: set[str] = set()

# Facts already reported by warn_once, by key.
_WARNED: set[str] = set()


def get_logger(name: str = "pipeline") -> logging.Logger:
    """Return the shared, lazily-configured pipeline logger.

    Configures each named logger's handler exactly once. Safe to call from any
    module at import time.
    """
    logger = logging.getLogger(name)

    if name not in _CONFIGURED:
        logger.setLevel(logging.INFO)
        handler: logging.Handler
        try:
            from rich.logging import RichHandler  # type: ignore

            handler = RichHandler(rich_tracebacks=True, show_path=False)
            handler.setFormatter(logging.Formatter("%(message)s", datefmt="[%X]"))
        except Exception:  # rich missing or failed — fall back to stdlib.
            handler = logging.StreamHandler(stream=sys.stderr)
            handler.setFormatter(
                logging.Formatter(
                    "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                    datefmt="%H:%M:%S",
                )
            )
        # Avoid duplicate handlers if a parent already configured logging.
        if not logger.handlers:
            logger.addHandler(handler)
        logger.propagate = False
        _CONFIGURED.add(name)

    return logger


def set_level(level: int, name: str = "pipeline") -> None:
    """Set the level on a shared pipeline logger (e.g. logging.DEBUG)."""
    get_logger(name).setLevel(level)


def warn_once(logger: logging.Logger, key: str, msg: str, *args) -> bool:
    """Log ``msg`` at WARNING the first time this ``key`` is seen; then stay quiet.

    ``key`` identifies the FACT, not the call site — e.g.
    ``f"dim-collision:{feature_id}:{note}"``. Returns True if it logged.

    This exists because a repeated warning is worse than a quiet one: an
    operator who sees the same line four times learns to skim warnings, which is
    exactly when the one that matters gets missed.
    """
    if key in _WARNED:
        return False
    _WARNED.add(key)
    logger.warning(msg, *args)
    return True


def reset_warn_once() -> None:
    """Forget every fact reported by :func:`warn_once` (per-run/test isolation)."""
    _WARNED.clear()
