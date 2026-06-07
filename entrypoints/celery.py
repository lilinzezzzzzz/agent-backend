"""Celery CLI entrypoint for worker and beat processes."""

from internal.infra.celery import celery_app

app = celery_app

__all__ = ["app", "celery_app"]
