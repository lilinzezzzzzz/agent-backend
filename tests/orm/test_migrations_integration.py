"""只操作测试自身创建的 PostgreSQL 容器，永不使用工作区的数据库配置。"""

import os
import re
import shutil
import subprocess
import sys
import time
import tomllib
from collections.abc import AsyncGenerator, Generator
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
import pytest_asyncio
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import Column, Connection, MetaData, String, inspect, select, text
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from internal import models
from internal.dao.user import UserDao
from internal.models import User
from pkg.database.audit import AuditActor

pytestmark = pytest.mark.integration
PROJECT_ROOT = Path(__file__).resolve().parents[2]
TEST_PASSWORD = "alembic_test%@:/"
COLUMN_REVISION = "test_add_column"
COLUMN_NAME = "migration_test_note"


@pytest.fixture
def postgres_project(
    request: pytest.FixtureRequest,
    tmp_path: Path,
) -> Generator[Path, None, None]:
    if not request.config.getoption("--run-postgresql-migrations"):
        pytest.skip("需要 --run-postgresql-migrations、Docker 和本地 postgres:17 镜像")
    if shutil.which("docker") is None:
        pytest.skip("需要 Docker CLI")
    available = subprocess.run(
        ["docker", "info"],
        capture_output=True,
        text=True,
        timeout=15,
    )
    if available.returncode != 0:
        pytest.skip("需要运行中的 Docker daemon")

    container_id = subprocess.run(
        [
            "docker",
            "run",
            "--detach",
            "--rm",
            "--pull=never",
            "--name",
            f"agent-backend-migrations-{uuid4().hex}",
            "--publish",
            "127.0.0.1::5432",
            "--env",
            "POSTGRES_USER=alembic_test",
            "--env",
            f"POSTGRES_PASSWORD={TEST_PASSWORD}",
            "--env",
            "POSTGRES_DB=alembic_test",
            "postgres:17",
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    ).stdout.strip()
    try:
        deadline = time.monotonic() + 30
        while True:
            ready = subprocess.run(
                [
                    "docker",
                    "exec",
                    container_id,
                    "pg_isready",
                    "-h",
                    "127.0.0.1",
                    "-U",
                    "alembic_test",
                    "-d",
                    "alembic_test",
                ],
                capture_output=True,
                timeout=5,
            )
            if ready.returncode == 0:
                break
            if time.monotonic() >= deadline:
                pytest.fail("一次性 PostgreSQL 容器未在 30 秒内就绪")
            time.sleep(0.25)

        binding = subprocess.run(
            ["docker", "port", container_id, "5432/tcp"],
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        ).stdout.strip()
        assert binding.startswith("127.0.0.1:")
        port = int(binding.rsplit(":", 1)[1])
        (tmp_path / "configs").mkdir()
        template = (PROJECT_ROOT / "configs/app.test.toml").read_text(encoding="utf-8")
        database_config = (
            '[db]\ntype = "postgresql"\nhost = "127.0.0.1"\n'
            f'port = {port}\nusername = "alembic_test"\n'
            'database = "alembic_test"\necho = false\n\n'
        )
        config_text, count = re.subn(
            r"(?ms)^\[db\]\n.*?(?=^\[|\Z)",
            database_config,
            template,
        )
        assert count == 1
        (tmp_path / "configs/app.test.toml").write_text(config_text, encoding="utf-8")
        (tmp_path / ".env").write_text("APP_ENV=test\n", encoding="utf-8")
        (tmp_path / ".secrets").write_text(
            f"AES_SECRET=\nJWT_SECRET=migration-test\nDB_PASSWORD={TEST_PASSWORD}\n"
            "REDIS_PASSWORD=\nWECHAT_APP_ID=\nWECHAT_APP_SECRET=\n"
            "LLM_MIMO_API_KEY=\nLLM_DEEPSEEK_API_KEY=\nEMBEDDING_API_KEY=\n",
            encoding="utf-8",
        )
        # 只重定位配置根目录；其余使用真实配置加载器和 Alembic CLI。
        (tmp_path / "alembic_cli.py").write_text(
            "import sys\nfrom pathlib import Path\n"
            f"sys.path.insert(0, {str(PROJECT_ROOT)!r})\n"
            "import internal.config as app_config\n"
            "app_config.BASE_DIR = Path(__file__).parent\n"
            "from alembic.config import main\nmain()\n",
            encoding="utf-8",
        )
        yield tmp_path
    finally:
        subprocess.run(
            ["docker", "rm", "--force", "--volumes", container_id],
            capture_output=True,
            check=True,
            timeout=15,
        )


def run_alembic(
    project: Path,
    *args: str,
    config_path: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env.pop("APP_ENV", None)
    return subprocess.run(
        [
            sys.executable,
            str(project / "alembic_cli.py"),
            "-c",
            str(config_path or PROJECT_ROOT / "alembic.ini"),
            *args,
        ],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


@pytest_asyncio.fixture
async def postgres_engine(postgres_project: Path) -> AsyncGenerator[AsyncEngine, None]:
    config = tomllib.loads(
        (postgres_project / "configs/app.test.toml").read_text(encoding="utf-8"),
    )["db"]
    engine = create_async_engine(
        URL.create(
            "postgresql+asyncpg",
            username=config["username"],
            password=TEST_PASSWORD,
            host=config["host"],
            port=config["port"],
            database=config["database"],
        ),
    )
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture
def model_metadata() -> MetaData:
    """只复制应用注册模型，避免其他测试在共享 Base 上注册的表混入期望结构。"""
    metadata = MetaData()
    for name in models.__all__:
        if name != "Base":
            getattr(models, name).__table__.to_metadata(metadata)
    return metadata


@pytest_asyncio.fixture
async def orm_engine(
    postgres_engine: AsyncEngine,
) -> AsyncGenerator[AsyncEngine, None]:
    """在同一个一次性容器中另建数据库，独立构造 ORM 的期望结构。"""
    async with postgres_engine.connect() as connection:
        await connection.execution_options(isolation_level="AUTOCOMMIT")
        await connection.execute(text("CREATE DATABASE orm_expected"))
    engine = create_async_engine(postgres_engine.url.set(database="orm_expected"))
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture
def column_migration(postgres_project: Path) -> tuple[Path, str]:
    """在当前 head 后追加测试专用增量，不修改仓库的实际迁移历史。"""
    migration_dir = postgres_project / "migrations"
    shutil.copytree(
        PROJECT_ROOT / "migrations",
        migration_dir,
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    config_path = postgres_project / "alembic.ini"
    config_path.write_text(
        "[alembic]\nscript_location = %(here)s/migrations\n"
        f"prepend_sys_path = {PROJECT_ROOT}\npath_separator = os\n",
        encoding="utf-8",
    )
    scripts = ScriptDirectory.from_config(Config(str(config_path)))
    parent_revision = scripts.get_current_head()
    assert parent_revision is not None
    (migration_dir / "versions" / "test_add_column.py").write_text(
        "import sqlalchemy as sa\n"
        "from alembic import context, op\n\n"
        f"revision = {COLUMN_REVISION!r}\n"
        f"down_revision = {parent_revision!r}\n\n"
        "def upgrade():\n"
        "    op.add_column(\n"
        "        'user',\n"
        f"        sa.Column({COLUMN_NAME!r}, sa.String(50), nullable=True),\n"
        "    )\n"
        "    failure = context.get_x_argument(as_dictionary=True).get('fail')\n"
        "    if failure == 'upgrade':\n"
        "        raise RuntimeError('injected upgrade failure')\n\n"
        "def downgrade():\n"
        f"    op.drop_column('user', {COLUMN_NAME!r})\n"
        "    failure = context.get_x_argument(as_dictionary=True).get('fail')\n"
        "    if failure == 'downgrade':\n"
        "        raise RuntimeError('injected downgrade failure')\n",
        encoding="utf-8",
    )
    return config_path, parent_revision


def assert_current_head(project: Path, config_path: Path | None = None) -> None:
    scripts = ScriptDirectory.from_config(
        Config(str(config_path or PROJECT_ROOT / "alembic.ini")),
    )
    head = scripts.get_current_head()
    assert head is not None
    result = run_alembic(project, "current", config_path=config_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"{head} (head)" in result.stdout


def schema_snapshot(connection: Connection) -> dict[str, Any]:
    """反射数据库中的最终结构；列的物理顺序不影响 ORM contract。"""
    inspector = inspect(connection)
    result = {}
    for table_name in inspector.get_table_names():
        if table_name == "alembic_version":
            continue
        columns = {}
        for column in inspector.get_columns(table_name):
            definition = dict(column)
            name = definition.pop("name")
            definition["type"] = str(column["type"].compile(dialect=connection.dialect))
            columns[name] = definition
        result[table_name] = {
            "columns": columns,
            "primary_key": inspector.get_pk_constraint(table_name),
            "unique_constraints": sorted(
                inspector.get_unique_constraints(table_name),
                key=lambda item: item["name"] or "",
            ),
            "check_constraints": sorted(
                inspector.get_check_constraints(table_name),
                key=lambda item: (item["name"] or "", item["sqltext"]),
            ),
            "foreign_keys": sorted(
                inspector.get_foreign_keys(table_name),
                key=lambda item: item["name"] or "",
            ),
            "indexes": sorted(
                inspector.get_indexes(table_name),
                key=lambda item: item["name"] or "",
            ),
            "comment": inspector.get_table_comment(table_name),
        }
    return result


async def read_schema(engine: AsyncEngine) -> dict[str, Any]:
    async with engine.connect() as connection:
        return await connection.run_sync(schema_snapshot)


async def assert_schema_matches_orm(
    migrated_engine: AsyncEngine,
    expected_engine: AsyncEngine,
    metadata: MetaData,
) -> None:
    async with expected_engine.begin() as connection:
        await connection.run_sync(metadata.create_all)
    expected = await read_schema(expected_engine)
    actual = await read_schema(migrated_engine)
    assert actual.keys() == expected.keys()
    for table_name in expected:
        assert actual[table_name] == expected[table_name], table_name


async def insert_test_user(engine: AsyncEngine) -> None:
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    dao = UserDao(session_provider=sessions)
    user = User.create(
        username="migration-test",
        account="migration-test",
        phone="13800000000",
        audit_actor=AuditActor.system(),
    )
    await dao.insert(user, audit_actor=AuditActor.system())


async def read_users(engine: AsyncEngine) -> list[dict[str, Any]]:
    async with engine.connect() as connection:
        result = await connection.execute(select(User.__table__).order_by(User.id))
        return [dict(row) for row in result.mappings()]


async def current_revision(engine: AsyncEngine) -> str | None:
    async with engine.connect() as connection:
        return await connection.scalar(text("SELECT version_num FROM alembic_version"))


async def execute_offline_sql(engine: AsyncEngine, sql: str) -> None:
    """使用当前连接的 asyncpg 驱动执行包含多语句事务的离线 SQL。"""
    async with engine.connect() as connection:
        raw = await connection.get_raw_connection()
        driver = raw.driver_connection
        assert driver is not None
        await driver.execute(sql)


@pytest.mark.asyncio
async def test_upgrade_preserves_data_and_detects_drift(
    postgres_project: Path,
    postgres_engine: AsyncEngine,
    orm_engine: AsyncEngine,
    model_metadata: MetaData,
) -> None:
    result = run_alembic(postgres_project, "upgrade", "head")
    assert result.returncode == 0, result.stdout + result.stderr
    result = run_alembic(postgres_project, "check")
    assert result.returncode == 0, result.stdout + result.stderr
    assert_current_head(postgres_project)
    await assert_schema_matches_orm(postgres_engine, orm_engine, model_metadata)

    sessions = async_sessionmaker(postgres_engine, expire_on_commit=False)
    dao = UserDao(session_provider=sessions)
    user = User.create(
        username="migration-test",
        account="migration-test",
        phone="13800000000",
        audit_actor=AuditActor.system(),
    )
    await dao.insert(user, audit_actor=AuditActor.system())
    result = run_alembic(postgres_project, "upgrade", "head")
    assert result.returncode == 0, result.stdout + result.stderr
    saved = await dao.fetch_one(dao.select_stmt().where(User.id == user.id))
    assert saved is not None
    assert saved.username == "migration-test"

    async with postgres_engine.begin() as connection:
        await connection.execute(text("DROP INDEX idx_agent_run_trace_id"))
    result = run_alembic(postgres_project, "check")
    assert result.returncode != 0
    assert "idx_agent_run_trace_id" in result.stdout + result.stderr
    async with postgres_engine.begin() as connection:
        await connection.execute(
            text("CREATE INDEX idx_agent_run_trace_id ON agent_run (trace_id)"),
        )

    result = run_alembic(postgres_project, "downgrade", "base")
    assert result.returncode == 0, result.stdout + result.stderr
    async with postgres_engine.connect() as connection:
        tables = await connection.run_sync(lambda conn: inspect(conn).get_table_names())
        assert tables == ["alembic_version"]
        assert (
            await connection.execute(text("SELECT * FROM alembic_version"))
        ).all() == []
    result = run_alembic(postgres_project, "upgrade", "head")
    assert result.returncode == 0, result.stdout + result.stderr
    result = run_alembic(postgres_project, "check")
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.asyncio
async def test_offline_sql_builds_a_database_at_head(
    postgres_project: Path,
    postgres_engine: AsyncEngine,
    orm_engine: AsyncEngine,
    model_metadata: MetaData,
) -> None:
    result = run_alembic(postgres_project, "upgrade", "head", "--sql")
    assert result.returncode == 0, result.stdout + result.stderr
    await execute_offline_sql(postgres_engine, result.stdout)
    result = run_alembic(postgres_project, "check")
    assert result.returncode == 0, result.stdout + result.stderr
    assert_current_head(postgres_project)
    await assert_schema_matches_orm(postgres_engine, orm_engine, model_metadata)


@pytest.mark.asyncio
async def test_cross_revision_upgrade_and_downgrade_preserve_existing_data(
    postgres_project: Path,
    postgres_engine: AsyncEngine,
    orm_engine: AsyncEngine,
    model_metadata: MetaData,
    column_migration: tuple[Path, str],
) -> None:
    config_path, parent_revision = column_migration
    result = run_alembic(
        postgres_project,
        "upgrade",
        parent_revision,
        config_path=config_path,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    await insert_test_user(postgres_engine)
    original_rows = await read_users(postgres_engine)
    original_schema = await read_schema(postgres_engine)
    assert len(original_rows) == 1

    result = run_alembic(postgres_project, "upgrade", "head", config_path=config_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert_current_head(postgres_project, config_path)
    assert await read_users(postgres_engine) == original_rows
    model_metadata.tables["user"].append_column(Column(COLUMN_NAME, String(50)))
    await assert_schema_matches_orm(postgres_engine, orm_engine, model_metadata)

    result = run_alembic(
        postgres_project,
        "downgrade",
        parent_revision,
        config_path=config_path,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert await current_revision(postgres_engine) == parent_revision
    assert await read_schema(postgres_engine) == original_schema
    assert await read_users(postgres_engine) == original_rows

    result = run_alembic(postgres_project, "upgrade", "head", config_path=config_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert_current_head(postgres_project, config_path)
    assert await read_users(postgres_engine) == original_rows
    await assert_schema_matches_orm(postgres_engine, orm_engine, model_metadata)


@pytest.mark.asyncio
async def test_incremental_sql_export_matches_final_orm_schema(
    postgres_project: Path,
    postgres_engine: AsyncEngine,
    orm_engine: AsyncEngine,
    model_metadata: MetaData,
    column_migration: tuple[Path, str],
) -> None:
    config_path, _ = column_migration
    result = run_alembic(
        postgres_project,
        "upgrade",
        "head",
        "--sql",
        config_path=config_path,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert f'ALTER TABLE "user" ADD COLUMN {COLUMN_NAME}' in result.stdout
    await execute_offline_sql(postgres_engine, result.stdout)

    assert_current_head(postgres_project, config_path)
    model_metadata.tables["user"].append_column(Column(COLUMN_NAME, String(50)))
    await assert_schema_matches_orm(postgres_engine, orm_engine, model_metadata)


@pytest.mark.asyncio
async def test_failed_upgrade_rolls_back_schema_version_and_data(
    postgres_project: Path,
    postgres_engine: AsyncEngine,
    column_migration: tuple[Path, str],
) -> None:
    config_path, parent_revision = column_migration
    result = run_alembic(
        postgres_project,
        "upgrade",
        parent_revision,
        config_path=config_path,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    await insert_test_user(postgres_engine)
    original_rows = await read_users(postgres_engine)
    original_schema = await read_schema(postgres_engine)
    assert len(original_rows) == 1

    # 故障发生在 ADD COLUMN 已执行、Alembic 版本登记尚未提交时。
    result = run_alembic(
        postgres_project,
        "-x",
        "fail=upgrade",
        "upgrade",
        "head",
        config_path=config_path,
    )
    assert result.returncode != 0
    assert "injected upgrade failure" in result.stdout + result.stderr
    assert await current_revision(postgres_engine) == parent_revision
    assert await read_schema(postgres_engine) == original_schema
    assert await read_users(postgres_engine) == original_rows

    result = run_alembic(postgres_project, "upgrade", "head", config_path=config_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert_current_head(postgres_project, config_path)
    assert COLUMN_NAME in (await read_schema(postgres_engine))["user"]["columns"]
    assert await read_users(postgres_engine) == original_rows


@pytest.mark.asyncio
async def test_failed_downgrade_restores_dropped_column_and_data(
    postgres_project: Path,
    postgres_engine: AsyncEngine,
    column_migration: tuple[Path, str],
) -> None:
    config_path, parent_revision = column_migration
    result = run_alembic(postgres_project, "upgrade", "head", config_path=config_path)
    assert result.returncode == 0, result.stdout + result.stderr
    await insert_test_user(postgres_engine)
    async with postgres_engine.begin() as connection:
        await connection.execute(
            text(f'UPDATE "user" SET {COLUMN_NAME} = :note'),
            {"note": "must survive a failed downgrade"},
        )
    original_rows = await read_users(postgres_engine)
    original_schema = await read_schema(postgres_engine)
    assert len(original_rows) == 1

    # DROP COLUMN 后失败，既要恢复列定义，也要恢复该列中已经写入的值。
    result = run_alembic(
        postgres_project,
        "-x",
        "fail=downgrade",
        "downgrade",
        parent_revision,
        config_path=config_path,
    )
    assert result.returncode != 0
    assert "injected downgrade failure" in result.stdout + result.stderr
    assert await current_revision(postgres_engine) == COLUMN_REVISION
    assert await read_schema(postgres_engine) == original_schema
    assert await read_users(postgres_engine) == original_rows
    async with postgres_engine.connect() as connection:
        assert (
            await connection.scalar(
                text(f'SELECT {COLUMN_NAME} FROM "user"'),
            )
            == "must survive a failed downgrade"
        )

    result = run_alembic(
        postgres_project,
        "downgrade",
        parent_revision,
        config_path=config_path,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert await current_revision(postgres_engine) == parent_revision
    assert COLUMN_NAME not in (await read_schema(postgres_engine))["user"]["columns"]
    assert await read_users(postgres_engine) == original_rows


def test_autogenerate_freezes_json_types_and_renders_new_revisions(
    postgres_project: Path,
) -> None:
    migration_dir = postgres_project / "migrations"
    migration_dir.mkdir()
    (migration_dir / "versions").mkdir()
    for filename in ("env.py", "script.py.mako"):
        shutil.copyfile(
            PROJECT_ROOT / "migrations" / filename,
            migration_dir / filename,
        )
    config_path = postgres_project / "alembic.ini"
    config_path.write_text(
        "[alembic]\nscript_location = %(here)s/migrations\n"
        f"prepend_sys_path = {PROJECT_ROOT}\npath_separator = os\n",
        encoding="utf-8",
    )
    # 不配置 fileConfig 的日志段；程序化测试仍需允许最小 Alembic 配置。
    result = run_alembic(
        postgres_project,
        "revision",
        "--autogenerate",
        "--rev-id",
        "test_initial",
        "-m",
        "test schema",
        config_path=config_path,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    generated = next((migration_dir / "versions").glob("*.py")).read_text(
        encoding="utf-8",
    )
    assert "postgresql.JSONB(" in generated
    assert "pkg.database" not in generated
    assert "internal.models" not in generated
    result = run_alembic(postgres_project, "upgrade", "head", config_path=config_path)
    assert result.returncode == 0, result.stdout + result.stderr
    result = run_alembic(postgres_project, "check", config_path=config_path)
    assert result.returncode == 0, result.stdout + result.stderr
