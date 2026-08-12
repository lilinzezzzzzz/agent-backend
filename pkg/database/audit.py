from dataclasses import dataclass
from enum import StrEnum
from typing import Self
from uuid import UUID


class AuditActorType(StrEnum):
    """持久化审计主体类型。"""

    USER = "user"
    SYSTEM = "system"
    SERVICE = "service"
    TASK = "task"


@dataclass(frozen=True, slots=True)
class AuditActor:
    """一次写操作的审计主体。"""

    actor_type: AuditActorType
    actor_id: UUID | None = None

    def __post_init__(self) -> None:
        if self.actor_type is AuditActorType.USER and self.actor_id is None:
            raise ValueError("User audit actor requires actor_id")

    @classmethod
    def user(cls, user_id: UUID) -> Self:
        return cls(actor_type=AuditActorType.USER, actor_id=user_id)

    @classmethod
    def system(cls) -> Self:
        return cls(actor_type=AuditActorType.SYSTEM)

    @classmethod
    def service(cls, service_id: UUID | None = None) -> Self:
        return cls(actor_type=AuditActorType.SERVICE, actor_id=service_id)

    @classmethod
    def task(cls, task_id: UUID | None = None) -> Self:
        return cls(actor_type=AuditActorType.TASK, actor_id=task_id)

    def creator_values(self) -> dict[str, UUID | str | None]:
        return self._values(id_column="creator_id", type_column="creator_type")

    def updater_values(self) -> dict[str, UUID | str | None]:
        return self._values(id_column="updater_id", type_column="updater_type")

    def _values(
        self,
        *,
        id_column: str,
        type_column: str,
    ) -> dict[str, UUID | str | None]:
        return {
            id_column: self.actor_id,
            type_column: self.actor_type.value,
        }
