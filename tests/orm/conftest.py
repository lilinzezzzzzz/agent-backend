import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--run-postgresql-migrations",
        action="store_true",
        default=False,
        help="使用 Docker postgres:17 一次性容器验证 Alembic 迁移",
    )
