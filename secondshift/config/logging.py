"""Shared logging policy for the control plane and isolated runner."""

from typing import Any


def logging_config(level: str, service: str) -> dict[str, Any]:
    """Route application and Django records through one console handler."""
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "console": {
                "format": (
                    "{asctime} {levelname} " + service + "[{process}] {name}: {message}"
                ),
                "style": "{",
            }
        },
        "handlers": {
            "console": {"class": "logging.StreamHandler", "formatter": "console"},
            "null": {"class": "logging.NullHandler"},
        },
        "root": {"handlers": ["console"], "level": level},
        "loggers": {
            # Replace Django's default handlers to avoid duplicate output and
            # let LOG_LEVEL apply to requests and development server messages.
            "django": {"handlers": [], "level": "NOTSET", "propagate": True},
            "django.server": {
                "handlers": [],
                "level": "NOTSET",
                "propagate": True,
            },
            # Request URLs may contain provider or webhook credentials.
            "httpx": {"level": "WARNING"},
            "httpcore": {"level": "WARNING"},
            # The SDK can log raw payloads and session identifiers. The broker
            # emits bounded audit events and sanitized errors instead.
            "mcp": {"handlers": ["null"], "propagate": False},
        },
    }
