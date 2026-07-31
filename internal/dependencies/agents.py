from typing import Annotated

from fastapi import Depends

from internal.services.agents import (
    AgentRouterService,
    OrderAgentService,
    PaymentAgentService,
    new_agent_router_service,
    new_order_agent_service,
    new_payment_agent_service,
)

OrderAgentServiceDep = Annotated[OrderAgentService, Depends(new_order_agent_service)]
PaymentAgentServiceDep = Annotated[
    PaymentAgentService,
    Depends(new_payment_agent_service),
]
AgentRouterServiceDep = Annotated[
    AgentRouterService,
    Depends(new_agent_router_service),
]
