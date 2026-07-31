from typing import Annotated

from fastapi import Depends

from internal.services.logger_span import (
    LoggerSpanService,
    new_logger_span_service,
)

LoggerSpanServiceDep = Annotated[
    LoggerSpanService,
    Depends(new_logger_span_service),
]
