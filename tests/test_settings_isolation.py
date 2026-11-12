"""在独立 pytest 进程中验证配置缓存与路由装配的执行顺序。"""

import os
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_settings_cache_then_demo_routes_without_factory_cache(tmp_path: Path) -> None:
    project = tmp_path / "project"
    (project / "configs").mkdir(parents=True)
    (project / "configs/app.test.toml").write_text(
        (PROJECT_ROOT / "configs/app.test.toml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (project / ".env").write_text("APP_ENV=test\n", encoding="utf-8")
    (project / ".secrets").write_text(
        "AES_SECRET=\nJWT_SECRET=settings-isolation-test\nDB_PASSWORD=\n"
        "REDIS_PASSWORD=\nWECHAT_APP_ID=\nWECHAT_APP_SECRET=\n"
        "LLM_MIMO_API_KEY=\nLLM_DEEPSEEK_API_KEY=\nEMBEDDING_API_KEY=\n",
        encoding="utf-8",
    )
    arguments = [
        "tests/config/test_config.py::test_get_settings_caches_until_reset",
        "tests/api/test_demo_app.py::test_only_demo_app_registers_experimental_routes",
        "-q",
        "--basetemp",
        str(tmp_path / "nested-pytest"),
    ]
    script = f"""
import sys
from pathlib import Path

import pytest
import internal.config as config_module

config_module.BASE_DIR = Path({str(project)!r})

class VerifyCollection:
    def pytest_collection_finish(self, session):
        assert "internal.factory_cache" not in sys.modules
        assert [item.nodeid for item in session.items] == {arguments[:2]!r}

    def pytest_runtest_logfinish(self, nodeid, location):
        if nodeid == {arguments[0]!r}:
            assert "internal.factory_cache" not in sys.modules
            assert config_module.get_settings.cache_info().currsize == 0

raise SystemExit(pytest.main({arguments!r}, plugins=[VerifyCollection()]))
"""
    environment = dict(os.environ)
    for key in ("PYTEST_CURRENT_TEST", "PYTEST_ADDOPTS"):
        environment.pop(key, None)
    environment["APP_ENV"] = "test"
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "2 passed" in result.stdout
