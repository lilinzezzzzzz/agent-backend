"""ASGI entrypoint for the FastAPI API service."""

from internal.app import create_app

app = create_app()

__all__ = ["app"]
