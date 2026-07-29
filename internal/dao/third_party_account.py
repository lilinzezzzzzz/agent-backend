from internal.infra.database import get_read_session, get_session
from internal.models.third_party_account import ThirdPartyAccount
from pkg.database.dao import BaseDao


class ThirdPartyAccountDao(BaseDao[ThirdPartyAccount]):
    _model_cls: type[ThirdPartyAccount] = ThirdPartyAccount

    async def get_by_platform_and_openid(
        self, platform: str, openid: str
    ) -> ThirdPartyAccount | None:
        """通过平台和 open_id 查询第三方账号"""
        statement = self.select_stmt().where(
            self.model_cls.platform == platform,
            self.model_cls.open_id == openid,
        )
        return await self.fetch_first(statement)

    async def is_platform_openid_exist(self, platform: str, openid: str) -> bool:
        """检查指定平台的 open_id 是否已存在"""
        return await self.get_by_platform_and_openid(platform, openid) is not None

    async def get_by_user_id_and_platform(
        self, user_id: int, platform: str
    ) -> ThirdPartyAccount | None:
        """通过用户 ID 和平台查询第三方账号"""
        statement = self.select_stmt().where(
            self.model_cls.user_id == user_id,
            self.model_cls.platform == platform,
        )
        return await self.fetch_first(statement)

    async def get_all_by_user_id(self, user_id: int) -> list[ThirdPartyAccount]:
        """获取用户的所有第三方账号"""
        statement = (
            self.select_stmt()
            .where(self.model_cls.user_id == user_id)
            .order_by(self.model_cls.updated_at.desc())
        )
        return await self.fetch_all(statement)

    async def delete_by_user_id_and_platform(self, user_id: int, platform: str) -> None:
        """删除用户的指定平台账号（谨慎使用）"""
        account = await self.get_by_user_id_and_platform(user_id, platform)
        if account:
            await self.soft_delete(account)


# 全局单例（懒加载）
_third_party_account_dao: ThirdPartyAccountDao | None = None


def new_third_party_account_dao() -> ThirdPartyAccountDao:
    global _third_party_account_dao
    if _third_party_account_dao is None:
        _third_party_account_dao = ThirdPartyAccountDao(
            session_provider=get_session,
            read_session_provider=get_read_session,
        )
    return _third_party_account_dao
