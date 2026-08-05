from sqlalchemy.ext.asyncio import AsyncSession

from internal.infra.database import get_read_session, get_session
from internal.models.external_identity import ExternalIdentity
from pkg.database.dao import BaseDao


class ExternalIdentityDao(BaseDao[ExternalIdentity]):
    _model_cls: type[ExternalIdentity] = ExternalIdentity

    async def get_by_identity(
        self,
        *,
        provider: str,
        connection_key: str,
        subject: str,
        include_deleted: bool = False,
        for_update: bool = False,
        session: AsyncSession | None = None,
    ) -> ExternalIdentity | None:
        """从主库查询唯一身份；认证路径不读取可能延迟的只读副本。"""
        statement = self.select_stmt(include_deleted=include_deleted).where(
            self.model_cls.provider == provider,
            self.model_cls.connection_key == connection_key,
            self.model_cls.subject == subject,
        )
        if for_update:
            if session is None or not session.in_transaction():
                raise RuntimeError("for_update identity lookup requires a transaction")
            statement = statement.with_for_update()

        if session is not None:
            return await self.fetch_one(statement, session=session)

        async with self.session_provider() as primary_session:
            return await self.fetch_one(statement, session=primary_session)

    async def get_all_by_user_id(self, user_id: int) -> list[ExternalIdentity]:
        statement = (
            self.select_stmt()
            .where(self.model_cls.user_id == user_id)
            .order_by(self.model_cls.updated_at.desc(), self.model_cls.id.desc())
        )
        return await self.fetch_all(statement)


_external_identity_dao: ExternalIdentityDao | None = None


def new_external_identity_dao() -> ExternalIdentityDao:
    global _external_identity_dao
    if _external_identity_dao is None:
        _external_identity_dao = ExternalIdentityDao(
            session_provider=get_session,
            read_session_provider=get_read_session,
        )
    return _external_identity_dao
