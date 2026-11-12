"""应用签名认证实例的延迟访问与初始化生命周期。"""

from types import SimpleNamespace

import pytest
from pydantic import SecretStr

import internal.utils.signature as signature_module


def test_signature_handler_requires_initialization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(signature_module, "_signature_auth_handler", None)

    with pytest.raises(RuntimeError, match="Signature Auth Handler not initialized"):
        signature_module.get_signature_auth_handler()

    with pytest.raises(RuntimeError, match="Signature Auth Handler not initialized"):
        signature_module.signature_auth_handler.generate_signature({"nonce": "test"})


def test_signature_handler_initialization_is_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(signature_module, "_signature_auth_handler", None)
    settings = SimpleNamespace(
        secrets=SimpleNamespace(jwt_secret=SecretStr("test-signature-key")),
    )
    monkeypatch.setattr(signature_module, "settings", settings)

    signature_module.init_signature_auth_handler()
    handler = signature_module.get_signature_auth_handler()
    data = {"timestamp": "123", "nonce": "test"}
    signature = signature_module.signature_auth_handler.generate_signature(data)

    assert handler.secret_key == b"test-signature-key"
    assert handler.verify_signature(data, signature)
    assert not handler.verify_signature({**data, "nonce": "changed"}, signature)

    settings.secrets.jwt_secret = SecretStr("another-test-key")
    signature_module.init_signature_auth_handler()

    assert signature_module.get_signature_auth_handler() is handler
    assert signature_module.signature_auth_handler.generate_signature(data) == signature
