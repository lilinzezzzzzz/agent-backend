from pathlib import Path

import pytest

from pkg.files import AnyioFile


# 标记所有测试为 anyio 测试，需要安装 pytest-anyio 或配置 pytest-asyncio
@pytest.mark.anyio
class TestAnyioFile:
    async def test_init_and_exists(self, tmp_path: Path):
        """测试初始化和存在性检查"""
        file_path = tmp_path / "test_init.txt"
        af = AnyioFile(file_path)

        # 验证路径转换
        assert af.file_path == str(file_path)

        # 文件尚未创建
        assert await af.exists() is False

        # 创建文件后检查
        await af.write("test")
        assert await af.exists() is True

    async def test_write_read_text(self, tmp_path: Path):
        """测试文本模式读写"""
        file_path = tmp_path / "text.txt"
        af = AnyioFile(file_path)

        content = "Hello, 世界\nNew Line"
        # 写入
        bytes_written = await af.write(content)
        assert bytes_written > 0

        # 读取
        read_content = await af.read()
        assert read_content == content
        assert isinstance(read_content, str)

    async def test_write_read_binary(self, tmp_path: Path):
        """测试二进制模式读写"""
        file_path = tmp_path / "binary.bin"
        af = AnyioFile(file_path)

        data = b"\x00\x01\x02\xff"
        # 写入 (mode="wb")
        await af.write(data, mode="wb")

        # 读取 (mode="rb")
        read_data = await af.read(mode="rb")
        assert read_data == data
        assert isinstance(read_data, bytes)

    async def test_ensure_parent(self, tmp_path: Path):
        """测试自动创建父目录"""
        # 创建一个深层嵌套的路径
        file_path = tmp_path / "nested" / "deep" / "dir" / "file.txt"
        af = AnyioFile(file_path)

        # 默认 ensure_parent=True
        await af.write("data")

        assert await af.exists() is True
        assert (tmp_path / "nested" / "deep" / "dir").exists()

    async def test_unlink(self, tmp_path: Path):
        """测试删除文件"""
        file_path = tmp_path / "delete_me.txt"
        af = AnyioFile(file_path)
        await af.write("data")
        assert await af.exists() is True

        # 正常删除
        await af.unlink()
        assert await af.exists() is False

        # 测试 missing_ok=True (默认) - 删除不存在的文件不应报错
        await af.unlink(missing_ok=True)

        # 测试 missing_ok=False - 删除不存在的文件应抛出异常
        with pytest.raises(FileNotFoundError):
            await af.unlink(missing_ok=False)

    async def test_mkdir(self, tmp_path: Path):
        """测试创建目录"""
        dir_path = tmp_path / "new_dir"
        af = AnyioFile(dir_path)

        await af.mkdir()
        assert dir_path.exists()
        assert dir_path.is_dir()

        # 测试 exist_ok=False (默认)
        with pytest.raises(FileExistsError):
            await af.mkdir(exist_ok=False)

        # 测试 exist_ok=True
        await af.mkdir(exist_ok=True)

    async def test_read_chunks(self, tmp_path: Path):
        """测试分块读取"""
        file_path = tmp_path / "chunks.bin"
        af = AnyioFile(file_path)

        # 写入一段较长的数据
        chunk_pattern = b"0123456789"
        data = chunk_pattern * 10
        await af.write(data, mode="wb")

        chunks = []
        # 每次读 10 字节
        async for chunk in af.read_chunks(chunk_size=10, mode="rb"):
            chunks.append(chunk)
            assert len(chunk) == 10

        assert b"".join(chunks) == data
        assert len(chunks) == 10

    async def test_read_lines(self, tmp_path: Path):
        """测试按行读取"""
        file_path = tmp_path / "lines.txt"
        af = AnyioFile(file_path)

        content = "Line 1\nLine 2\nLine 3"
        await af.write(content)

        # 测试保留换行符 (默认)
        lines = []
        async for line in af.read_lines(strip_newline=False):
            lines.append(line)
        assert lines == ["Line 1\n", "Line 2\n", "Line 3"]

        # 测试去除换行符
        lines_stripped = []
        async for line in af.read_lines(strip_newline=True):
            lines_stripped.append(line)
        assert lines_stripped == ["Line 1", "Line 2", "Line 3"]

    async def test_stat(self, tmp_path: Path):
        """测试获取文件状态"""
        file_path = tmp_path / "stat.txt"
        af = AnyioFile(file_path)
        data = "12345"
        await af.write(data)

        stat = await af.stat()
        assert stat.st_size == 5

    async def test_type_errors(self, tmp_path: Path):
        """测试类型错误校验"""
        af = AnyioFile(tmp_path / "error.txt")

        # 文本模式写入 bytes
        with pytest.raises(TypeError, match="Text mode .* requires str data"):
            await af.write(b"bytes", mode="w")  # type: ignore

        # 二进制模式写入 str
        with pytest.raises(TypeError, match="Binary mode .* requires bytes-like data"):
            await af.write("str", mode="wb")  # type: ignore
