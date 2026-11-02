from __future__ import annotations

import logging
import sys

import structlog

from .config import config


def configure_logging() -> None:
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=logging.DEBUG if config.env != "production" else logging.INFO,
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            # JSONRenderer can't serialize exc_info itself: without this,
            # every log.exception() in production logged "exc_info": true
            # and dropped the actual error. ConsoleRenderer formats it on
            # its own, so dev output is unchanged.
            *([structlog.processors.format_exc_info] if config.env == "production" else []),
            structlog.processors.JSONRenderer()
            if config.env == "production"
            else structlog.dev.ConsoleRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.DEBUG),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


log = structlog.get_logger()
