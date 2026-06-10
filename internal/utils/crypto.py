from pkg.crypter import AESCipher
from pkg.lazy_proxy import lazy_proxy

_aes_cipher: AESCipher | None = None


def init_aes_cipher(secret_key: str):
    global _aes_cipher
    _aes_cipher = AESCipher(secret_key)


def _get_aes_cipher() -> AESCipher:
    if _aes_cipher is None:
        raise RuntimeError("AES Cipher is not initialized")
    return _aes_cipher


aes_cipher = lazy_proxy(_get_aes_cipher)
