"""Structlog JSON logging for FHWA-AGENT-003."""

import logging
import os
import sys

import structlog


def setup_logging(environment: str = "development") -> structlog.BoundLogger:
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.add_log_level,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, environment.upper(), logging.INFO)
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_call=True,
    )
    return structlog.get_logger()


logger = setup_logging(os.getenv("ENVIRONMENT", "development"))
