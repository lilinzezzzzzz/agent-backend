import os
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any, Literal, cast, overload

import anyio


class AnyioFile:
    __slots__ = ("_path", "file_path")  # 优化内存，防止随意添加属性

    def __init__(self, file_path: str | Path):
        if not isinstance(file_path, (str, Path)):
            raise TypeError(f"file_path must be str or Path, got {type(file_path)}")
        self.file_path = str(file_path)
        self._path: anyio.Path = anyio.Path(file_path)

    async def unlink(self, missing_ok: bool = True) -> None:
        """
        删除文件。

        Args:
            missing_ok: 如果为 True (默认)，文件不存在时不报错；否则抛出 FileNotFoundError。
        """
        try:
            await self._path.unlink()
        except FileNotFoundError:
            if not missing_ok:
                raise

    async def exists(self) -> bool:
        """检查文件是否存在。"""
        return await self._path.exists()

    async def stat(self) -> os.stat_result:
        """获取文件信息。"""
        return await self._path.stat()

    async def mkdir(self, parents: bool = False, exist_ok: bool = False) -> None:
        """创建目录。"""
        await self._path.mkdir(parents=parents, exist_ok=exist_ok)

    # 使用 overload 提供更准确的类型推断提示
    @overload
    async def read(self, mode: Literal["r"] = "r", encoding: str | None = "utf-8") -> str: ...

    @overload
    async def read(self, mode: Literal["rb", "br"], encoding: None = None) -> bytes: ...

    async def read(self, mode: Literal["r", "rb", "br"] = "r", encoding: str | None = "utf-8") -> str | bytes:
        """
        读取完整文件内容。
        注意：这会将整个文件加载到内存，大文件请慎用。
        """
        if mode in ("rb", "br"):
            async with await self._path.open(mode=mode) as f:
                return await f.read()
        else:
            async with await self._path.open(mode=mode, encoding=encoding) as f:
                return await f.read()

    async def read_chunks(
        self,
        chunk_size: int = 1024 * 64,  # 默认 64KB
        mode: str = "rb",
        encoding: str | None = "utf-8",
    ) -> AsyncGenerator[bytes | str, None]:
        """
        [新增] 分块读取大文件（生成器）。
        适用于无法一次性加载到内存的大文件。

        Args:
            chunk_size: 每次读取的块大小（字节数或字符数）
            mode: 读取模式 ("rb" 返回 bytes, "r" 返回 str)
            encoding: 文本模式下的编码
        """
        if "b" in mode:
            async with await self._path.open(mode=cast(Any, mode)) as f:
                while True:
                    chunk = await f.read(chunk_size)
                    if not chunk:
                        break
                    yield chunk
        else:
            async with await self._path.open(mode=cast(Any, mode), encoding=encoding) as f:
                while True:
                    chunk = await f.read(chunk_size)
                    if not chunk:
                        break
                    yield chunk

    async def read_lines(
        self, encoding: str | None = "utf-8", strip_newline: bool = False
    ) -> AsyncGenerator[str, None]:
        """
        [新增] 逐行读取大文本文件（生成器）。
        适用于处理大日志文件或 CSV。

        Args:
            encoding: 文件编码
            strip_newline: 是否去除行尾换行符
        """
        async with await self._path.open(mode="r", encoding=encoding) as f:
            line: str
            async for line in f:
                if strip_newline:
                    yield line.rstrip("\n")
                else:
                    yield line

    async def write(
        self,
        data: str | bytes,
        mode: str = "w",
        encoding: str | None = "utf-8",
        ensure_parent: bool = True,
        flush: bool = False,
    ) -> int:
        """
        写入文件。
        Args:
            data: 内容 (str 或 bytes)
            mode: 模式 ('w', 'wb', 'a', 'ab' 等)
            encoding: 文本模式下的编码
            ensure_parent: 是否自动创建父目录
            flush: 是否强制刷新缓冲区
        Returns:
            写入的字符数或字节数
        """
        if ensure_parent:
            # 使用 exist_ok=True 避免并发创建时的报错
            await self._path.parent.mkdir(parents=True, exist_ok=True)

        is_binary = "b" in mode

        # 参数校验：提前报错比进入 IO 后报错更好
        if is_binary and not isinstance(data, (bytes, bytearray, memoryview)):
            raise TypeError(f"Binary mode '{mode}' requires bytes-like data, got {type(data)}")
        if not is_binary and not isinstance(data, str):
            raise TypeError(f"Text mode '{mode}' requires str data, got {type(data)}")

        # 打开文件并写入
        # 注意：anyio.Path.open() 返回的是一个 coroutine，必须 await 拿到 context manager
        if is_binary:
            async with await self._path.open(mode=cast(Any, mode)) as f:
                n = await f.write(data)
                if flush:
                    await f.flush()
                return n
        else:
            async with await self._path.open(mode=cast(Any, mode), encoding=encoding) as f:
                n = await f.write(data)
                if flush:
                    await f.flush()
                return n
