"""收集测试模块后，公共包及父包引用应仍指向真实模块。"""

from importlib import import_module

import pytest


@pytest.mark.parametrize(
    "module_name",
    [
        "pkg.logger",
        "pkg.request_context",
        "pkg.ids",
        "pkg.vectors",
        "internal.infra.database",
        "internal.infra.redis",
    ],
)
def test_collection_preserves_real_module_identity(module_name: str) -> None:
    module = import_module(module_name)
    parent_name, name = module_name.rsplit(".", 1)
    parent = import_module(parent_name)

    assert module.__spec__ is not None
    assert module.__spec__.name == module_name
    assert module.__file__ is not None
    assert getattr(parent, name) is module
