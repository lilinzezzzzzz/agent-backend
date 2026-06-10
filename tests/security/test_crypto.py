import pytest
from cryptography.fernet import Fernet

from pkg.crypter import (
    AESCipher,
    EncryptionAlgorithm,
    get_crypto_class,
)
from pkg.crypter.aes import aes_decrypt, aes_encrypt
from pkg.security import Hasher


@pytest.fixture
def anyio_backend():
    return "asyncio"


# =========================================================
# Password Hasher Tests (保持原样)
# =========================================================


@pytest.fixture
def hasher():
    return Hasher(rounds=4)


@pytest.mark.anyio
class TestPasswordHasher:
    async def test_hash_password_success(self, hasher):
        password = "test_password_123"
        hashed = await hasher.hash(password)

        assert hashed is not None
        assert isinstance(hashed, str)
        assert hashed != password
        assert hashed.startswith("$2b$")

    async def test_hash_password_empty_raises_error(self, hasher):
        with pytest.raises(ValueError, match="Password cannot be empty"):
            await hasher.hash("")

    async def test_verify_correct_password(self, hasher):
        password = "my_secure_password"
        hashed = await hasher.hash(password)

        is_valid = await hasher.verify(password, hashed)
        assert is_valid is True

    async def test_verify_wrong_password(self, hasher):
        password = "correct_password"
        wrong_password = "wrong_password"
        hashed = await hasher.hash(password)

        is_valid = await hasher.verify(wrong_password, hashed)
        assert is_valid is False

    async def test_verify_empty_plain_password(self, hasher):
        hashed = await hasher.hash("some_password")
        is_valid = await hasher.verify("", hashed)
        assert is_valid is False

    async def test_verify_empty_hashed_password(self, hasher):
        is_valid = await hasher.verify("some_password", "")
        assert is_valid is False

    async def test_verify_invalid_hash_format(self, hasher):
        is_valid = await hasher.verify("password", "invalid_hash_format")
        assert is_valid is False

    async def test_custom_rounds(self):
        hasher_4 = Hasher(rounds=4)
        hasher_6 = Hasher(rounds=6)

        assert hasher_4.rounds == 4
        assert hasher_6.rounds == 6

        hashed_4 = await hasher_4.hash("test")
        hashed_6 = await hasher_6.hash("test")

        assert "$04$" in hashed_4
        assert "$06$" in hashed_6


# =========================================================
# AES Encryption Tests (新增部分)
# =========================================================


class TestAESCipher:
    @pytest.fixture
    def valid_key_str(self):
        """生成一个有效的 Fernet Key (String 格式)"""
        return AESCipher.generate_key()

    @pytest.fixture
    def valid_key_bytes(self, valid_key_str):
        """生成一个有效的 Fernet Key (Bytes 格式)"""
        return valid_key_str.encode("utf-8")

    def test_generate_key_returns_valid_format(self):
        """测试静态方法生成的 Key 是否合法"""
        key = AESCipher.generate_key()
        assert isinstance(key, str)
        # 尝试用 Fernet 加载，不报错即为合法
        Fernet(key.encode("utf-8"))

    def test_init_accepts_string_key(self, valid_key_str):
        """测试可以传入字符串 Key (YAML/Env 场景)"""
        cipher = AESCipher(valid_key_str)
        assert cipher is not None

    def test_init_accepts_bytes_key(self, valid_key_bytes):
        """测试可以传入 bytes Key"""
        cipher = AESCipher(valid_key_bytes)
        assert cipher is not None

    def test_init_raises_error_on_invalid_key(self):
        """测试传入普通字符串（非 Base64 Fernet Key）时报错"""
        with pytest.raises(ValueError, match="Invalid AES key"):
            AESCipher("invalid_plain_password_123")

    def test_init_raises_error_on_empty_key(self):
        with pytest.raises(ValueError, match="Key cannot be empty"):
            AESCipher("")

    def test_encrypt_decrypt_cycle(self, valid_key_str):
        """测试加密后再解密，能够还原明文"""
        cipher = AESCipher(valid_key_str)
        plaintext = "Hello World! 你好世界"

        # 1. 加密
        ciphertext = cipher.encrypt(plaintext)
        assert isinstance(ciphertext, str)
        assert ciphertext != plaintext
        assert len(ciphertext) > 0

        # 2. 解密
        decrypted = cipher.decrypt(ciphertext)
        assert decrypted == plaintext

    def test_encrypt_returns_different_ciphertext_each_time(self, valid_key_str):
        """测试 AES IV 机制：相同明文加密两次，密文应该不同"""
        cipher = AESCipher(valid_key_str)
        plaintext = "secret"

        c1 = cipher.encrypt(plaintext)
        c2 = cipher.encrypt(plaintext)

        assert c1 != c2
        # 但解密后应该一样
        assert cipher.decrypt(c1) == cipher.decrypt(c2) == plaintext

    def test_decrypt_fails_with_wrong_key(self, valid_key_str):
        """测试用错误的 Key 解密失败"""
        cipher_encryptor = AESCipher(valid_key_str)
        cipher_decryptor = AESCipher(AESCipher.generate_key())  # 另一个随机 Key

        plaintext = "top_secret"
        ciphertext = cipher_encryptor.encrypt(plaintext)

        with pytest.raises(ValueError, match="Decryption failed"):
            cipher_decryptor.decrypt(ciphertext)

    def test_decrypt_fails_on_tampered_data(self, valid_key_str):
        """测试篡改密文后解密失败 (HMAC 校验)"""
        cipher = AESCipher(valid_key_str)
        ciphertext = cipher.encrypt("data")

        # 修改密文的最后几个字符
        tampered_ciphertext = ciphertext[:-4] + "abcd"

        with pytest.raises(ValueError):
            cipher.decrypt(tampered_ciphertext)

    def test_encrypt_empty_string(self, valid_key_str):
        cipher = AESCipher(valid_key_str)
        assert cipher.encrypt("") == ""

    def test_decrypt_empty_string(self, valid_key_str):
        cipher = AESCipher(valid_key_str)
        assert cipher.decrypt("") == ""


# =========================================================
# Factory & Helper Function Tests
# =========================================================


class TestCryptoFactory:
    def test_get_aes_util(self):
        key = AESCipher.generate_key()
        util = get_crypto_class(algo=EncryptionAlgorithm.AES)(key=key)
        assert isinstance(util, AESCipher)

    def test_factory_invalid_algo(self):
        # 这里的 ignore 是为了欺骗类型检查器去测试运行时错误
        with pytest.raises(NotImplementedError):
            get_crypto_class(algo="unknown_algo")  # type: ignore


def test_helper_functions_round_trip():
    """测试 aes_encrypt 和 aes_decrypt 便捷函数"""
    key = AESCipher.generate_key()
    text = "helper_function_test"

    encrypted = aes_encrypt(text, key)
    decrypted = aes_decrypt(encrypted, key)

    assert decrypted == text
