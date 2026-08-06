# AGENTS.md

本目录集中声明 FastAPI 依赖、认证 guard 和可复用 dependency alias。这里的规则只约束
`internal/dependencies/` 及其子目录。

## 依赖声明风格

- 新增依赖 alias 时，优先声明为 `Depends(...)` alias，例如：
  `CurrentUserDep = Depends(require_authenticated_user)`。
- 只有调用点确实需要同时表达类型与依赖元数据时，才使用 `Annotated[T, Depends(...)]` typed alias；
  typed `Annotated` 是次要风格，不作为新增依赖的默认写法。
- Header、Query、Path、Body 等参数元数据仍可使用 `Annotated[T, Header(...)]` 等 FastAPI 标准形式；
  本规则主要针对 service、guard、router dependency 的声明风格。
- 对外导出的 alias 命名保持清晰稳定，优先复用已有 `*Dep` / `*Dependency` 命名，不为同一依赖新增平行别名。
