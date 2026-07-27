# AGENTS.md

适用于 `pkg/files/`。

## 模块职责

本模块提供基于 AnyIO 的异步文件读取、分块迭代、写入和基础路径操作。调用方仍负责路径授权、
文件类型和业务配额。

## 修改约束

- 不依赖 `internal/` 或全局配置，不在 import 时访问文件系统。
- 文本/二进制 mode、data 类型、encoding 和返回类型保持一致；新增 mode 时同步 overload 与运行时校验。
- 完整 `read()` 会加载全部内容；大文件路径使用 `read_chunks()` / `read_lines()`，chunk size 必须为正且有界。
- `unlink()`、覆盖写和自动创建父目录可能破坏数据；不要隐式扩大目标路径或绕过调用方确认。
- 需要原子替换时提供明确的临时文件 + rename 协议，不把普通 `write()` 描述为原子或崩溃安全。
- 取消和 I/O 异常应原样传播，确保文件句柄通过 async context manager 关闭。

## 验证重点

- 运行 `tests/test_anyio_file.py`。
- 覆盖文本/二进制、分块、逐行、追加、父目录、flush、缺文件、非法 mode/data 和取消路径。
