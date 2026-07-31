from typing import Annotated

from fastapi import Depends

from internal.services.celery_task import (
    CeleryTaskService,
    new_celery_task_service,
)

CeleryTaskServiceDep = Annotated[
    CeleryTaskService,
    Depends(new_celery_task_service),
]
