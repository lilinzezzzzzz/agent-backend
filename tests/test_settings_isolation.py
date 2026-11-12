"""在独立 pytest 进程中验证配置缓存与路由装配的执行顺序。"""

import os
import subprocess
import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "preload_factory_cache",
    [False, True],
    ids=["without_factory_cache", "with_factory_cache"],
)
def test_settings_cache_then_demo_routes_with_lazy_factory_cleanup(
    tmp_path: Path,
    preload_factory_cache: bool,
) -> None:
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

if {preload_factory_cache!r}:
    import tests.support.factory_cache

class VerifyCollection:
    def pytest_collection_finish(self, session):
        assert ("tests.support.factory_cache" in sys.modules) is {preload_factory_cache!r}
        assert [item.nodeid for item in session.items] == {arguments[:2]!r}

    def pytest_runtest_call(self, item):
        factory_cache = sys.modules.get("tests.support.factory_cache")
        if factory_cache is not None:
            factory_cache.new_user_service()
            assert factory_cache.new_user_service.cache_info().currsize == 1

    def pytest_runtest_logfinish(self, nodeid, location):
        factory_cache = sys.modules.get("tests.support.factory_cache")
        assert (factory_cache is not None) is {preload_factory_cache!r}
        if factory_cache is not None:
            assert factory_cache.new_user_service.cache_info().currsize == 0
        if nodeid == {arguments[0]!r}:
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
