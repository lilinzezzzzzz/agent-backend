"""进程级 dependency factory 缓存的统一清理入口。"""

from typing import Protocol

from internal.cache import (
    new_agent_action_cache,
    new_auth_cache,
    new_endpoint_guard_cache,
)
from internal.config import reset_settings
from internal.dao.agent_audit import new_agent_audit_dao
from internal.dao.agent_conversation import (
    new_agent_message_dao,
    new_agent_run_dao,
    new_agent_run_step_dao,
    new_agent_session_dao,
)
from internal.dao.celery_task import new_celery_task_dao
from internal.dao.external_identity import new_external_identity_dao
from internal.dao.rag import new_rag_metadata_dao
from internal.dao.scoped_operation_lock import new_scoped_operation_lock_dao
from internal.dao.user import new_user_dao
from internal.services.agents import (
    new_agent_audit_service,
    new_agent_conversation_service,
    new_agent_router_service,
    new_order_agent_service,
    new_payment_agent_service,
)
from internal.services.auth import new_auth_service
from internal.services.celery_task import new_celery_task_service
from internal.services.logger_span import new_logger_span_service
from internal.services.order import new_order_service
from internal.services.rag import new_rag_service
from internal.services.scoped_operation_lock import new_scoped_operation_lock_service
from internal.services.user import new_user_service


class _CachedFactory(Protocol):
    def __call__(self) -> object: ...

    def cache_clear(self) -> None: ...


# 依赖从底层到高层排列；清理时反向执行，先释放上层对象引用。
_CACHED_FACTORIES: tuple[_CachedFactory, ...] = (
    new_agent_action_cache,
    new_auth_cache,
    new_endpoint_guard_cache,
    new_agent_audit_dao,
    new_agent_session_dao,
    new_agent_message_dao,
    new_agent_run_dao,
    new_agent_run_step_dao,
    new_celery_task_dao,
    new_external_identity_dao,
    new_rag_metadata_dao,
    new_scoped_operation_lock_dao,
    new_user_dao,
    new_agent_audit_service,
    new_agent_conversation_service,
    new_auth_service,
    new_celery_task_service,
    new_logger_span_service,
    new_order_service,
    new_rag_service,
    new_scoped_operation_lock_service,
    new_user_service,
    new_order_agent_service,
    new_payment_agent_service,
    new_agent_router_service,
)


def reset_factory_caches() -> None:
    """清理配置与 dependency factory 缓存，主要用于测试隔离。

    本函数不关闭数据库、Redis、tracing 或其他外部资源；这些资源
    仍由各自的 lifespan close/reset 入口负责。
    """
    for factory in reversed(_CACHED_FACTORIES):
        factory.cache_clear()
    reset_settings()


__all__ = ["reset_factory_caches"]
