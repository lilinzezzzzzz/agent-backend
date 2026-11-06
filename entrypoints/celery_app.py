"""Celery application entrypoint for Worker and Beat."""

from internal.infra.celery.application import celery_app

__all__ = ["celery_app"]
