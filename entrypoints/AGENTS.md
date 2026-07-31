# AGENTS.md

`entrypoints/` 只提供 FastAPI、Celery Worker / Beat 等进程启动入口。

- Python 入口保持薄层，不复制 lifespan、middleware、router、配置加载或业务逻辑；import 时不得启动连接、
  线程或后台任务。
- Shell 入口切到仓库根目录并用 `exec` 启动最终进程，确保信号和退出码正确传递。
- Celery app 路径、队列、pool、并发和 CLI 参数属于部署 contract；变更时同步 Worker 配置和运维入口。
- 凭据、连接串和环境地址继续通过统一配置链提供，不写入入口脚本。
