"""独立的 PostgreSQL 迁移入口；不启动应用或共享连接池。"""

import asyncio
from logging.config import fileConfig
from typing import Literal

from alembic import context
from alembic.autogenerate.api import AutogenContext
from alembic.util import CommandError
from sqlalchemy import Connection, pool
from sqlalchemy.ext.asyncio import create_async_engine

from internal.models import Base
from pkg.database.types import JSONType

config = context.config

if config.config_file_name is not None and config.file_config.has_section("loggers"):
    fileConfig(config.config_file_name, disable_existing_loggers=False)


def render_item(
    item_type: str,
    item: object,
    autogen_context: AutogenContext,
) -> str | Literal[False]:
    """把应用 JSON 类型固化为 PostgreSQL 类型，避免历史 revision 依赖应用实现。"""
    if item_type == "type" and isinstance(item, JSONType):
        autogen_context.imports.add("from sqlalchemy.dialects import postgresql")
        return f"postgresql.JSONB(none_as_null={item.none_as_null!r})"
    return False


def run_migrations_offline() -> None:
    """导出 SQL 时只指定方言，不读取配置、凭据或连接数据库。"""
    context.configure(
        dialect_name="postgresql",
        target_metadata=Base.metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_item=render_item,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """在调用方提供的连接上执行迁移或结构比较。"""
    if connection.dialect.name != "postgresql":
        raise CommandError("Project migrations support PostgreSQL only.")

    context.configure(
        connection=connection,
        target_metadata=Base.metadata,
        compare_type=True,
        compare_server_default=True,
        render_item=render_item,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    # 仅在线命令需要加载应用配置；离线导出和 revision 图检查不需要 .secrets。
    from internal.config import get_settings

    settings = get_settings()
    if settings.db.type != "postgresql":
        raise CommandError("Project migrations support PostgreSQL only.")

    engine = create_async_engine(
        settings.sqlalchemy_database_uri,
        poolclass=pool.NullPool,
        echo=False,
    )
    try:
        async with engine.connect() as connection:
            await connection.run_sync(do_run_migrations)
    finally:
        await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
elif (connection := config.attributes.get("connection")) is not None:
    do_run_migrations(connection)
else:
    asyncio.run(run_async_migrations())
