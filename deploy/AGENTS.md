# AGENTS.md

`deploy/` 保存部署适配资产；当前 `deploy/logrotate/` 服务于单机 Compose 日志轮转。

- 镜像基础版本和关键运行依赖固定版本；变更来源或 digest 时说明供应链和兼容性影响。
- logrotate sidecar 保持单副本、无业务网络、无 Docker socket、无应用配置或密钥挂载，并维持最小可写目录。
- 日志路径、轮转 state、权限和 retention 必须与 `compose.yaml`、生产配置和日志文档一致。
- 不引入真实凭据、隐式生产主机依赖或运行时下载的未锁定内容。
- 轮转行为需在隔离 Linux 环境验证；本地静态检查不能证明 bind mount、权限和 inode 行为。
