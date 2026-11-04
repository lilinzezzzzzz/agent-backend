import subprocess
import sys
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from alembic.util import CommandError
from sqlalchemy import create_engine

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DDL_PATH = PROJECT_ROOT / "ddl/postgresql/init.sql"


def test_migrations_have_a_single_head_and_base() -> None:
    scripts = ScriptDirectory.from_config(Config(str(PROJECT_ROOT / "alembic.ini")))
    assert len(scripts.get_heads()) == 1
    assert len(scripts.get_bases()) == 1


def test_model_registry_includes_every_model_module() -> None:
    script = (
        "import importlib\n"
        "import pkgutil\n"
        "from internal import models\n"
        "registered = set(models.Base.metadata.tables)\n"
        "for module in pkgutil.iter_modules(models.__path__, models.__name__ + '.'):\n"
        "    importlib.import_module(module.name)\n"
        "assert set(models.Base.metadata.tables) == registered\n"
    )
    subprocess.run(
        [
            sys.executable,
            "-c",
            script,
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )


def test_online_migrations_reject_other_dialects() -> None:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    engine = create_engine("sqlite://")
    try:
        with engine.connect() as connection:
            config.attributes["connection"] = connection
            with pytest.raises(CommandError, match="PostgreSQL only"):
                command.upgrade(config, "head")
    finally:
        engine.dispose()


def test_offline_migrations_do_not_load_application_config(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; sys.modules['internal.config'] = None; "
            "from alembic.config import main; main()",
            "-c",
            str(PROJECT_ROOT / "alembic.ini"),
            "upgrade",
            "head",
            "--sql",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    assert "CREATE TABLE agent_run" in result.stdout
    assert "INSERT INTO alembic_version" in result.stdout
    assert "Loading configuration" not in result.stderr


def test_schema_export_is_reproducible(tmp_path: Path) -> None:
    exported = tmp_path / "init.sql"
    subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "scripts/export_postgresql_schema.py"),
            "--output",
            str(exported),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    assert exported.read_text(encoding="utf-8") == DDL_PATH.read_text(encoding="utf-8")
