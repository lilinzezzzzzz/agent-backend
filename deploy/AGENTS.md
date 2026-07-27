# AGENTS.md

适用于 `deploy/`。根目录 `Dockerfile`、`compose.yaml` 和部署文档共同构成部署 contract。

## 目录职责

本目录保存部署适配资产；当前 `deploy/logrotate/` 只负责单机 Compose 场景的日志轮转 sidecar。

## 修改约束

- 镜像基础版本和关键运行依赖固定版本；变更 digest 或 repository 时说明供应链来源和兼容性。
- sidecar 保持单副本、无业务网络、无 Docker socket、无应用配置或密钥挂载，并维持最小可写目录。
- API 与 sidecar 的日志路径、轮转策略、state volume 和 `copytruncate` 边界必须与 `compose.yaml`、
  `configs/.env.prod`、README 和 `docs/logging/log_rotation.md` 一致。
- 修改 retention、压缩、频率、权限或运行用户前评估磁盘预算、现有归档和故障恢复。
- 不在镜像或配置中写入真实凭据、生产主机路径以外的隐式依赖，或运行时下载未锁定内容。

## 验证重点

- Dockerfile / logrotate 配置修改后至少做构建和配置解析。
- 真实轮转行为使用 `scripts/verify_logrotate_sidecar.sh` 在显式隔离的 Linux 目录验证；本地静态检查不能替代
  Linux bind mount、权限和 inode 行为。
