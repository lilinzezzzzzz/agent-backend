# AGENTS.md

`pkg/chars_matcher/` 组合拼音和字形两条独立匹配管线。

- 新匹配维度使用独立 matcher 和数据命名空间，`matcher.py` 只做编排，共享类型放在 `types.py`。
- 数据使用异步预加载和懒初始化，模块 import 时不做阻塞 I/O。
- `pkg/chars_matcher/chars/pinyin/*.json` 和 `pkg/chars_matcher/chars/shape/name_shape_chars.json` 是稳定 artifact；
  批量更新通过 `pkg/chars_matcher/scripts/rebuild_chars_matcher_pinyin_mappings.py` 生成，不手改大批量条目。
- 数据格式或 preload 变化时覆盖校验、首次加载竞态和并发预加载。
