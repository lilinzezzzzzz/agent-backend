import sys
from uuid import UUID

import pytest
import pytest_asyncio
from sqlalchemy import String
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.orm import Mapped, mapped_column

from pkg.database import (
    Base,
    BaseDao,
    JSONType,
    ModelMixin,
    base as database_base,
    new_async_session_maker,
)

TEST_USER_ID = UUID("00000000-0000-7000-8000-000000000999")


# ==========================================
# 3. 定义测试模型
# ==========================================
class User(ModelMixin):
    __tablename__ = "users_json_type"
    username: Mapped[str] = mapped_column(String(50))
    email: Mapped[str] = mapped_column(String(100), nullable=True)
    info: Mapped[dict] = mapped_column(JSONType(), default=dict)  # 使用实例


class UserDao(BaseDao[User]):
    _model_cls: type[User] = User


# ==========================================
# 4. Pytest Fixtures
# ==========================================


@pytest_asyncio.fixture(loop_scope="function")
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_maker = new_async_session_maker(engine)
    yield session_maker
    await engine.dispose()


@pytest.fixture
def _context_user():
    with database_base.context.scoped_values(user_id=TEST_USER_ID):
        yield


@pytest.fixture(autouse=True)
def context_user(_context_user):
    return None


@pytest.fixture
def user_dao(db_session):
    return UserDao(session_provider=db_session)


# ==========================================
# 5. 测试用例
# ==========================================


@pytest.mark.asyncio
async def test_create_and_insert_strictness(user_dao, db_session):
    """测试创建对象、严格Insert检查和ID生成"""
    user = User.create(username="alice", info={"role": "admin"})
    assert user.id is not None
    assert user.info == {"role": "admin"}

    await user_dao.insert(user)

    db_user = await user_dao.query_by_primary_id(user.id)
    assert db_user is not None
    assert db_user.username == "alice"
    assert db_user.info == {"role": "admin"}

    with pytest.raises(RuntimeError) as exc:
        db_user.prepare_insert_values()
    assert "strictly for INSERT" in str(exc.value)


@pytest.mark.asyncio
async def test_update_strictness(user_dao, db_session):
    """测试更新逻辑、Mutable JSON 追踪和严格 Update 检查"""
    # 初始化
    user = User.create(username="bob", info={"login_count": 0})
    await user_dao.insert(user)

    # 1. 测试 MutableJSON 追踪（在同一个事务中修改并提交）
    async with db_session() as session:
        async with session.begin():
            db_user = await session.get(User, user.id)
            # 修改 JSON 内部字段 (验证 MutableJSON)
            db_user.info["login_count"] = 1
            db_user.info["last_login"] = "today"
            db_user.username = "bob_updated"
            # 事务结束时自动 commit

    # 2. 验证变更已持久化
    reloaded = await user_dao.query_by_primary_id(user.id)
    assert reloaded.username == "bob_updated"
    assert reloaded.info["login_count"] == 1, "JSON Mutation failed to track changes"
    assert "last_login" in reloaded.info

    # 3. Strict Update 检查（新对象不能调用 update）
    new_user = User.create(username="charlie")
    with pytest.raises(RuntimeError) as exc:
        new_user.prepare_instance_update_values(username="fail")
    assert "requires a persisted" in str(exc.value)


@pytest.mark.asyncio
async def test_batch_insert_instances(user_dao, db_session):
    users = [User.create(username=f"user_{i}") for i in range(5)]
    await user_dao.insert_instances(items=users)
    async with user_dao.session_provider() as session:
        count = (await session.execute(user_dao.count_stmt())).scalar()
    assert count == 5


@pytest.mark.asyncio
async def test_batch_insert_rows(user_dao, db_session):
    rows = [{"username": "dict_1"}, {"username": "dict_2"}]
    await user_dao.insert_rows(rows=rows)
    async with user_dao.session_provider() as session:
        count = (await session.execute(user_dao.count_stmt())).scalar()
    assert count == 2


@pytest.mark.asyncio
async def test_select_statement_composes_with_sqlalchemy(user_dao, db_session):
    await user_dao.insert_rows(rows=[{"username": f"u{i}"} for i in range(1, 6)])

    async with user_dao.read_session_provider() as session:
        result = await session.execute(
            user_dao.select_stmt().where(User.username.in_(["u1", "u2"]))
        )
        res = list(result.scalars().all())
    assert len(res) == 2

    assert await user_dao.query_by_ids([]) == []

    page_result = await user_dao.fetch_page(
        user_dao.select_stmt().order_by(User.id.asc()),
        page=1,
        limit=2,
    )
    assert len(page_result.items) == 2
    assert page_result.items[0].username == "u1"
    assert page_result.total == 5


@pytest.mark.asyncio
async def test_soft_delete(user_dao, db_session):
    user = User.create(username="del_me")
    await user_dao.insert(user)
    await user_dao.soft_delete(user)
    async with user_dao.read_session_provider() as session:
        active = (
            (await session.execute(user_dao.select_stmt().where(User.id == user.id)))
            .scalars()
            .first()
        )
        deleted = (
            (
                await session.execute(
                    user_dao.select_stmt(include_deleted=True).where(User.id == user.id)
                )
            )
            .scalars()
            .first()
        )
    assert active is None
    assert deleted is not None


@pytest.mark.asyncio
async def test_instance_update_logic(user_dao, db_session):
    user = User.create(username="old_name")
    await user_dao.insert(user)
    await user_dao.update(user, {User.username: "new_name"})
    reloaded = await user_dao.query_by_primary_id(user.id)
    assert reloaded.username == "new_name"


if __name__ == "__main__":
    sys.exit(pytest.main(["-s", "-v", "--log-cli-level=INFO", __file__]))
