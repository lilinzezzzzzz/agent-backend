"""Backward-compatible ASGI entrypoint.

Prefer ``entrypoints.api:app`` for new deployments.
"""

from entrypoints.api import app

__all__ = ["app"]
