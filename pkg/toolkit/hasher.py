import bcrypt

from pkg.concurrency import anyio_run_in_thread


class Hasher:
    def __init__(self, rounds: int = 12):
        self.rounds = rounds

    def _hash_sync(self, password: str) -> str:
        salt = bcrypt.gensalt(rounds=self.rounds)
        return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")

    @staticmethod
    def _verify_sync(plain_password: str, hashed_password: str) -> bool:
        try:
            return bcrypt.checkpw(plain_password.encode("utf-8"), hashed_password.encode("utf-8"))
        except (ValueError, TypeError):
            return False

    async def hash(self, password: str) -> str:
        if not password:
            raise ValueError("Password cannot be empty")
        return await anyio_run_in_thread(self._hash_sync, password)

    async def verify(self, plain_password: str, hashed_password: str) -> bool:
        if not plain_password or not hashed_password:
            return False
        return await anyio_run_in_thread(self._verify_sync, plain_password, hashed_password)


hasher = Hasher()


# =========================================================
# Helpers
# =========================================================


async def hash_password(password: str) -> str:
    return await hasher.hash(password)


async def verify_password(plain_password: str, hashed_password: str) -> bool:
    return await hasher.verify(plain_password, hashed_password)
