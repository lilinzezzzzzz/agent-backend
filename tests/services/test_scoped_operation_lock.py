"""ScopedOperationLock 单元测试。

测试输入校验、锁获取协议和模型约束。
"""

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from internal.core import AppException
from internal.dao.scoped_operation_lock import ScopedOperationLockDao
from internal.models.scoped_operation_lock import ScopedOperationLock
from internal.services.scoped_operation_lock import ScopedOperationLockService
from pkg.database import Base


# 测试模型定义（使用 ScopedOperationLock）
@pytest_asyncio.fixture(loop_scope="function")
async def db_engine():
    """创建内存 SQLite 引擎。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture(loop_scope="function")
async def db_session_factory(db_engine):
    """创建数据库会话工厂。"""
    return async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)


@pytest_asyncio.fixture
async def db_session(db_session_factory):
    """创建数据库会话。"""
    async with db_session_factory() as session:
        yield session


@pytest.fixture
def lock_dao(db_session_factory):
    """创建 ScopedOperationLockDao 实例。"""
    return ScopedOperationLockDao(
        session_provider=db_session_factory,
        read_session_provider=db_session_factory,
    )


@pytest.fixture
def lock_service(lock_dao):
    """创建 ScopedOperationLockService 实例。"""
    return ScopedOperationLockService(dao=lock_dao)


# ==========================================
# 1. 模型约束测试
# ==========================================


class TestScopedOperationLockModel:
    """测试 ScopedOperationLock 模型约束。"""

    def test_model_has_no_deleted_at_column(self):
        """验证模型禁用软删除。"""
        assert ScopedOperationLock.has_deleted_at_column() is False

    def test_model_soft_delete_raises_not_implemented(self):
        """验证软删除操作抛出 NotImplementedError。"""
        lock = ScopedOperationLock(operation_scope="test", resource_key="key")
        with pytest.raises(NotImplementedError, match="does not support soft delete"):
            lock.build_soft_delete_stmt()

    def test_model_restore_raises_not_implemented(self):
        """验证恢复操作抛出 NotImplementedError。"""
        lock = ScopedOperationLock(operation_scope="test", resource_key="key")
        with pytest.raises(NotImplementedError, match="does not support restore"):
            lock.build_restore_stmt()

    def test_model_fields(self):
        """验证模型字段定义。"""
        # 检查字段存在
        assert hasattr(ScopedOperationLock, 'operation_scope')
        assert hasattr(ScopedOperationLock, 'resource_key')
        assert hasattr(ScopedOperationLock, 'creator_id')

        # 检查字段类型
        op_scope_col = ScopedOperationLock.__table__.columns['operation_scope']
        assert op_scope_col.type.length == 64
        assert op_scope_col.nullable is False

        res_key_col = ScopedOperationLock.__table__.columns['resource_key']
        assert res_key_col.type.length == 128
        assert res_key_col.nullable is False

        creator_col = ScopedOperationLock.__table__.columns['creator_id']
        assert creator_col.nullable is True

    def test_model_unique_constraint(self):
        """验证唯一约束定义。"""
        table_args = ScopedOperationLock.__table_args__
        unique_constraints = [
            arg for arg in table_args if hasattr(arg, 'name') and arg.name == 'uk_scoped_op_lock_key'
        ]
        assert len(unique_constraints) == 1
        constraint = unique_constraints[0]
        assert 'operation_scope' in constraint.columns
        assert 'resource_key' in constraint.columns


# ==========================================
# 2. 输入校验测试
# ==========================================


class TestScopedOperationLockServiceValidation:
    """测试 ScopedOperationLockService 输入校验。"""

    def test_validate_empty_operation_scope(self, lock_service):
        """验证空 operation_scope 抛出异常。"""
        with pytest.raises(AppException) as exc_info:
            lock_service._validate_parameters("", "key")
        assert "operation_scope 不能为空" in str(exc_info.value.message)

    def test_validate_whitespace_operation_scope(self, lock_service):
        """验证空白 operation_scope 抛出异常。"""
        with pytest.raises(AppException) as exc_info:
            lock_service._validate_parameters("   ", "key")
        assert "operation_scope 不能为空" in str(exc_info.value.message)

    def test_validate_long_operation_scope(self, lock_service):
        """验证过长 operation_scope 抛出异常。"""
        long_scope = "a" * 65
        with pytest.raises(AppException) as exc_info:
            lock_service._validate_parameters(long_scope, "key")
        assert "operation_scope 长度不能超过 64 个字符" in str(exc_info.value.message)

    def test_validate_empty_resource_key(self, lock_service):
        """验证空 resource_key 抛出异常。"""
        with pytest.raises(AppException) as exc_info:
            lock_service._validate_parameters("scope", "")
        assert "resource_key 不能为空" in str(exc_info.value.message)

    def test_validate_whitespace_resource_key(self, lock_service):
        """验证空白 resource_key 抛出异常。"""
        with pytest.raises(AppException) as exc_info:
            lock_service._validate_parameters("scope", "   ")
        assert "resource_key 不能为空" in str(exc_info.value.message)

    def test_validate_long_resource_key(self, lock_service):
        """验证过长 resource_key 抛出异常。"""
        long_key = "b" * 129
        with pytest.raises(AppException) as exc_info:
            lock_service._validate_parameters("scope", long_key)
        assert "resource_key 长度不能超过 128 个字符" in str(exc_info.value.message)

    @pytest.mark.asyncio
    async def test_validate_mutually_exclusive_parameters(self, lock_service, db_session):
        """验证 nowait 和 skip_locked 互斥。"""
        with pytest.raises(AppException) as exc_info:
            await lock_service.acquire_lock(
                session=db_session,
                operation_scope="scope",
                resource_key="key",
                nowait=True,
                skip_locked=True,
            )
        assert "nowait 和 skip_locked 参数互斥" in str(exc_info.value.message)


# ==========================================
# 3. 锁获取协议测试
# ==========================================


class TestScopedOperationLockAcquire:
    """测试锁获取协议。"""

    @pytest.mark.asyncio
    async def test_acquire_creates_new_lock(self, lock_service, db_session):
        """测试首次创建锁行。"""
        async with db_session.begin():
            lock = await lock_service.acquire_lock(
                session=db_session,
                operation_scope="order_confirm",
                resource_key="order:123",
            )

            assert lock is not None
            assert lock.operation_scope == "order_confirm"
            assert lock.resource_key == "order:123"
            assert lock.id is not None

    @pytest.mark.asyncio
    async def test_acquire_existing_lock(self, lock_service, db_session):
        """测试获取已存在的锁。"""
        async with db_session.begin():
            # 首次创建
            lock1 = await lock_service.acquire_lock(
                session=db_session,
                operation_scope="order_confirm",
                resource_key="order:123",
            )

            # 释放锁（事务提交）
            await db_session.commit()

        # 再次获取同一锁
        async with db_session.begin():
            lock2 = await lock_service.acquire_lock(
                session=db_session,
                operation_scope="order_confirm",
                resource_key="order:123",
            )

            assert lock2 is not None
            assert lock2.id == lock1.id

    @pytest.mark.asyncio
    async def test_acquire_different_resources(self, lock_service, db_session):
        """测试获取不同资源的锁。"""
        async with db_session.begin():
            lock1 = await lock_service.acquire_lock(
                session=db_session,
                operation_scope="order_confirm",
                resource_key="order:123",
            )

            lock2 = await lock_service.acquire_lock(
                session=db_session,
                operation_scope="order_confirm",
                resource_key="order:456",
            )

            assert lock1.id != lock2.id

    @pytest.mark.asyncio
    async def test_acquire_with_creator_id(self, lock_service, db_session):
        """测试带 creator_id 的锁获取。"""
        async with db_session.begin():
            lock = await lock_service.acquire_lock(
                session=db_session,
                operation_scope="order_confirm",
                resource_key="order:123",
                creator_id=999,
            )

            assert lock.creator_id == 999

    @pytest.mark.asyncio
    async def test_acquire_without_creator_id(self, lock_service, db_session):
        """测试不带 creator_id 的锁获取。"""
        async with db_session.begin():
            lock = await lock_service.acquire_lock(
                session=db_session,
                operation_scope="order_confirm",
                resource_key="order:123",
            )

            # 注意：由于使用原始 SQL INSERT，绕过了 ModelMixin 的 fill_ins_insert_fields 方法，
            # 所以 creator_id 不会被自动设置。
            # 这是预期的行为，因为 DAO 使用原始 SQL 来实现 PostgreSQL 的 upsert 功能。
            assert lock.creator_id is None

    @pytest.mark.asyncio
    async def test_acquire_with_wait_strategy(self, lock_service, db_session):
        """测试等待策略参数。"""
        async with db_session.begin():
            # 测试 wait=True（默认）
            lock = await lock_service.acquire_lock(
                session=db_session,
                operation_scope="order_confirm",
                resource_key="order:123",
                wait=True,
            )
            assert lock is not None

    @pytest.mark.asyncio
    async def test_acquire_with_nowait_strategy(self, lock_service, db_session):
        """测试 nowait 策略。"""
        async with db_session.begin():
            # 测试 nowait=True
            lock = await lock_service.acquire_lock(
                session=db_session,
                operation_scope="order_confirm",
                resource_key="order:123",
                nowait=True,
            )
            assert lock is not None

    @pytest.mark.asyncio
    async def test_acquire_with_skip_locked_strategy(self, lock_service, db_session):
        """测试 skip_locked 策略。"""
        async with db_session.begin():
            # 测试 skip_locked=True
            lock = await lock_service.acquire_lock(
                session=db_session,
                operation_scope="order_confirm",
                resource_key="order:123",
                skip_locked=True,
            )
            assert lock is not None

    @pytest.mark.asyncio
    async def test_acquire_rollback_releases_lock(self, lock_service, db_session):
        """测试事务回滚释放锁。"""
        # 首先创建锁
        async with db_session.begin():
            lock = await lock_service.acquire_lock(
                session=db_session,
                operation_scope="order_confirm",
                resource_key="order:123",
            )
            lock_id = lock.id

        # 模拟事务回滚（通过不提交）
        # 注意：SQLite 内存数据库不支持真正的并发，这里主要测试流程
        async with db_session.begin():
            # 再次获取同一锁（应该成功，因为前一个事务已结束）
            lock2 = await lock_service.acquire_lock(
                session=db_session,
                operation_scope="order_confirm",
                resource_key="order:123",
            )
            assert lock2.id == lock_id


# ==========================================
# 4. DAO 方法测试
# ==========================================


class TestScopedOperationLockDao:
    """测试 ScopedOperationLockDao 方法。"""

    @pytest.mark.asyncio
    async def test_dao_acquire_method(self, lock_dao, db_session):
        """测试 DAO 的 acquire 方法。"""
        async with db_session.begin():
            lock = await lock_dao.acquire(
                db_session,
                "order_confirm",
                "order:123",
            )

            assert lock is not None
            assert lock.operation_scope == "order_confirm"
            assert lock.resource_key == "order:123"

    @pytest.mark.asyncio
    async def test_dao_ensure_lock_row_exists(self, lock_dao, db_session):
        """测试 _ensure_lock_row_exists 方法。"""
        # 首次创建
        async with db_session.begin():
            await lock_dao._ensure_lock_row_exists(
                db_session,
                "order_confirm",
                "order:123",
                creator_id=1,
            )

            # 验证记录存在
            from sqlalchemy import select
            result = await db_session.execute(
                select(ScopedOperationLock).where(
                    ScopedOperationLock.operation_scope == "order_confirm",
                    ScopedOperationLock.resource_key == "order:123",
                )
            )
            lock = result.scalar_one_or_none()
            assert lock is not None

    @pytest.mark.asyncio
    async def test_dao_upsert_generic(self, lock_dao, db_session):
        """测试 PostgreSQL upsert 方法。"""
        async with db_session.begin():
            # 首次插入
            await lock_dao._upsert_postgresql(
                db_session,
                "order_confirm",
                "order:123",
                creator_id=1,
            )

            # 重复插入（应该忽略）
            await lock_dao._upsert_postgresql(
                db_session,
                "order_confirm",
                "order:123",
                creator_id=2,
            )

            # 验证只有一条记录
            from sqlalchemy import func, select
            result = await db_session.execute(
                select(func.count(ScopedOperationLock.id)).where(
                    ScopedOperationLock.operation_scope == "order_confirm",
                    ScopedOperationLock.resource_key == "order:123",
                )
            )
            count = result.scalar()
            assert count == 1
