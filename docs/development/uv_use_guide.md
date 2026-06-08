# uv 工作流最佳实践指南（生产环境）

---

## 1. 项目结构与基本约定

项目根目录包含以下文件：

```
pyproject.toml   # 项目依赖声明（手动或 uv 自动维护）
uv.lock          # 依赖锁定文件（不可手动改）
.venv/           # 由 uv 管理的虚拟环境（可忽略到 .gitignore）
```

团队原则：

1. pyproject.toml 用来描述“你想要的依赖”
2. uv.lock 用来描述“团队实际锁定的依赖版本”
3. 不向仓库提交 requirements.txt（除非你需要兼容其他工具）

---

## 2. 依赖安装与环境初始化

新加入项目或切换分支时：

```bash
uv sync
```

这会自动：

* 创建虚拟环境（如果不存在）
* 安装锁定文件中的依赖
* 保证环境与团队保持一致

**不要手动执行 pip install**，避免环境漂移。

---

## 3. 团队协作：依赖更新流程

### 3.1 修改依赖（添加、删除、升级）

添加依赖：

```bash
uv add fastapi
```

删除依赖：

```bash
uv remove requests
```

升级依赖：

```bash
uv lock --upgrade-package <包名>
uv add --upgrade-package <包名>
```

uv 会自动进行：

* 依赖解析
* 更新 pyproject.toml
* 更新 uv.lock
* 同步虚拟环境

**开发者不需要手动修改 pyproject.toml**。

---

## 4. 保持锁文件一致（团队必备流程）

Whenever 修改依赖后，都必须提交：

```
pyproject.toml
uv.lock
```

团队成员同步：

```bash
uv sync
```

**这一步相当于 python 版的 go mod tidy**，保证依赖整齐、无污染。

---

## 5. Python 版本管理（避免“这在我机器上可以运行”）

项目应在 pyproject.toml 中固定 Python 版本，例如：

```toml
[project]
requires-python = ">=3.12,<3.13"
```

开发环境可以用 uv 安装对应版本：

```bash
uv python list          # 查看可安装版本
uv venv .venv --python 3.12
```

如果是 CI 环境，也应显式使用相同的 Python 版本。

---

## 6. CI/CD：可复现构建

在 CI 中，环境安装只需：

```bash
uv sync --frozen
```

含义：

* 强制使用 uv.lock 中的具体版本
* 不进行依赖重新解析
* 如果 lock 不匹配 pyproject，会失败（防止野蛮提交）

这能保证构建可复现、环境一致性。

---

## 7. 分组依赖（dev/test/build 分离）

推荐使用 uv 的依赖分组：

在 pyproject.toml：

```toml
[project.optional-dependencies]
dev = ["pytest", "mypy"]
test = ["pytest"]
build = ["uvicorn"]
```

安装主依赖：

```bash
uv sync
```

安装 dev：

```bash
uv sync --group dev
```

安装多个组：

```bash
uv sync --group dev --group test
```

让开发环境更轻、生产镜像更干净。

---

## 8. 环境重建（怀疑环境污染时）

```bash
rm -rf .venv
uv sync
```

---

## 9. 运行命令

在激活虚拟环境后：

```bash
uv run python app.py
```

或运行其他命令：

```bash
uv run pytest
```

这是可复现且隔离的运行方式。

---

## 10. 清理缓存（CI 或空间不足）

```bash
uv clean
```

特别适用于 Docker 构建阶段。

---

## 11. 兼容 IDE（PyCharm、VSCode）

Pycharm 有时需要这些工具包来识别环境：

```bash
uv pip install pip setuptools wheel
```

VSCode 一般识别 `.venv/bin/python` 即可。

---

## 12. 常见工作流整合总结

下面是一个典型的团队协作顺序：

### 开发者 A：添加或升级依赖

```bash
uv add httpx
```

生成：

* pyproject.toml（更新）
* uv.lock（更新）
* .venv（本地同步）

提交：

```
git add pyproject.toml uv.lock
git commit -m "Add httpx"
```

### 开发者 B：拉取代码

```bash
git pull
uv sync
```

环境自动同步到一致状态。

### CI 构建：

```bash
uv sync --frozen
uv run pytest
```

---

```shell
# 安装uv
curl -LsSf https://astral.sh/uv/install.sh | sh

# 初始化新项目 (生成 pyproject.toml)
uv init <项目名>

# 或者在当前目录初始化
uv init

# 把 requirements.txt 中的依赖迁移到 pyproject.toml 并生成 uv.lock
uv add -r requirements.txt

# 查看所有可用的 Python 版本(uv 能自动安装官方预编译版本)
uv python list
 
# 查看系统中已安装的可用 Python 版本
uv python find

# 在当前目录下创建虚拟环境 
uv venv
 
# 使用指定解释器路径创建虚拟环境
uv venv <环境名> --python <解释器路径或者版本>
uv venv .venv --python 3.12.9

# 激活虚拟环境
source .venv/bin/activate

# 强制根据 toml 重新生成 lock 文件 (慎用，可能会升级包版本)
uv lock

# 同步所有依赖
uv sync

# 同步主依赖
uv sync --no-default-groups

# 同步主依赖+dev依赖
uv sync --no-default-groups --group dev

# 同步所有依赖
uv sync --all-groups

# 同步依赖到当前激活的虚拟环境
uv sync --active

# 同步依赖到指定的虚拟环境
uv sync --python <解释器路径>

# 添加依赖(会自动执行uv lock, 并且更新project.toml)
uv add <包名>

# 升级依赖(会自动执行uv lock, 并且更新project.toml)
uv add <包名> --upgrade

# 升级依赖(会自动执行uv lock, 并且更新project.toml)
uv add "starlette>0.49.1"

# 升级依赖(会自动执行uv lock, 并且更新project.toml)
uv lock --upgrade-package <包名>

# 安装全局工具
uv tool install <工具名>

# 安装全局工具(指定版本)
uv tool install black==24.0.0

# 升级工具
uv tool upgrade black

# 升级所有工具
uv tool upgrade --all

# 删除依赖(会自动执行uv lock, 并且更新project.toml)
uv remove <包名>

# 所有的依赖
uv pip list

# 强制重建环境(清理缓存并重新同步)
uv clean && uv sync

# 重建环境
rm -rf .venv && uv lock && uv sync

# 激活环境
source .venv/bin/activate

# 运行命令(会寻找当前文件夹下的.venv环境)
uv run <命令>

# 使用指定解释器运行命令
uv run --python <uv的解释器路径>

# 导出依赖到requirements.txt
uv pip freeze > requirements.txt

# PyCharm 兼容性
uv pip install pip setuptools wheel

# PyCharm 运行时环境变量
PYTHONUNBUFFERED=1;PYTHONIOENCODING=utf-8;APP_ENV=local
```
