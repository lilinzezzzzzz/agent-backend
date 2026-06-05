"""Celery CLI entrypoint for worker and beat processes."""

from celery import Celery

from internal.infra.celery import celery_app

app: Celery = celery_app

__all__ = ["app", "celery_app"]
