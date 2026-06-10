from internal.config import settings
from pkg.logger import logger
from pkg.security import SignatureAuthHandler
from pkg.lazy_proxy import lazy_proxy

_signature_auth_handler: SignatureAuthHandler | None = None


def init_signature_auth_handler():
    global _signature_auth_handler

    if _signature_auth_handler is not None:
        return

    _signature_auth_handler = SignatureAuthHandler(secret_key=settings.JWT_SECRET.get_secret_value())

    logger.success("Signature Auth Handler initialized Successfully.")


def get_signature_auth_handler() -> SignatureAuthHandler:
    if _signature_auth_handler is None:
        raise RuntimeError("Signature Auth Handler not initialized. Call init_signature_auth_handler() first.")
    return _signature_auth_handler


signature_auth_handler = lazy_proxy(get_signature_auth_handler)
