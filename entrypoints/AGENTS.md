# AGENTS.md

适用于 `entrypoints/`。应用生命周期和 provider 约束继续遵循 `internal/AGENTS.md` 与
`internal/infra/AGENTS.md`。

## 目录职责

本目录只提供进程启动入口：FastAPI ASGI app、Celery Worker / Beat 等命令包装。业务逻辑、连接初始化、
任务实现和路由注册分别留在 `internal/app.py`、`internal/infra/`、`internal/tasks/` 和
`internal/controllers/`。

## 修改约束

- `entrypoints/main.py` 保持薄入口，只创建并导出 ASGI app；不要在 import 时额外启动线程、连接或后台任务。
- Python 入口不得复制 lifespan、middleware、router 或配置加载逻辑。
- Shell 入口先切换到仓库根目录，使用 `exec` 启动最终进程，确保容器信号和退出码能够正确传递。
- Celery app 路径、默认队列、pool、并发参数和透传 CLI 参数属于部署 contract；修改时同步检查
  `internal/infra/celery/`、`internal/tasks/`、README 和 Compose / 运维命令。
- 不在入口脚本写入凭据、连接串或环境专属地址；配置继续由项目统一配置链提供。

## 验证重点

- Python 入口至少验证可 import，且应用 startup / shutdown 生命周期由现有测试覆盖。
- Shell 脚本修改后运行 `bash -n entrypoints/run_celery_worker.sh`，并检查最终命令的参数和信号传递。
