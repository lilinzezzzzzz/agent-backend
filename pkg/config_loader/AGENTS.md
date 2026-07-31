# AGENTS.md

`pkg/config_loader/` 提供多格式配置读取和 dict 合并，不是应用配置加载旁路。

- YAML 只使用 safe loader；各格式的顶层非 mapping、缺文件和不支持格式保持明确错误或既有空值语义。
- deep merge 只递归合并两侧都是 dict 的值，后者覆盖前者，list 不隐式拼接。
- ENV parser 的引号、注释、空行和首个 `=` 语义按兼容性 contract 处理。
- 文件内容和解析错误不得泄露可能存在的凭据。
