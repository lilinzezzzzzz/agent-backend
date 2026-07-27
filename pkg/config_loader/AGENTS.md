# AGENTS.md

适用于 `pkg/config_loader/`。应用配置必须继续通过 `internal/config.py` 加载，不使用本包建立旁路。

## 模块职责

本模块提供 JSON、YAML、TOML、INI、ENV 文件读取和 dict 合并的通用纯工具。

## 修改约束

- 不依赖 `internal/`、全局 settings 或环境专属路径；调用方负责路径授权和 secret 分类。
- YAML 仅使用 safe loader；不得恢复任意对象构造。
- 各格式返回 dict；顶层不是 mapping 时保持明确的既有空值或错误语义，不静默转换成其他结构。
- deep merge 保持“后者覆盖前者”，只递归合并两侧都是 dict 的值；list 不做隐式拼接。
- ENV parser 的引号、注释、空行和首个 `=` 行为属于兼容性 contract，调整时补边界测试。
- 文件读取不打印内容，解析错误不得泄露可能包含凭据的完整配置。

## 验证重点

- 覆盖每种扩展名、缺文件、不支持格式、非 mapping YAML、ENV 引号/等号和 deep/shallow merge。
- 修改格式支持时同步 `pyproject.toml` 的直接依赖与导入降级行为。
