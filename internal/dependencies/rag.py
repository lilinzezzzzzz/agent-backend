from typing import Annotated

from fastapi import Depends

from internal.services.rag import RagService, new_rag_service

RagServiceDep = Annotated[RagService, Depends(new_rag_service)]
