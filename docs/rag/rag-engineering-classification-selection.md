# RAG 工程分类与技术选型

## 概述

本文档用于指导 RAG（Retrieval-Augmented Generation）系统在工程落地时的分类、选型和演进。这里的分类不只按论文概念划分，而是按生产系统里真正影响架构复杂度的因素划分：

- 问题复杂度：单跳问答、多跳推理、研究分析、流程执行。
- 数据形态：纯文本、结构化数据、半结构化文档、多模态资产、实体关系网络。
- 检索策略：关键词、向量、混合检索、rerank、图检索、工具检索。
- 编排方式：固定 pipeline、模块化 pipeline、ReAct / Agent loop。
- 生产约束：权限、延迟、成本、可观测性、评估、索引更新。

核心结论：

> 普通业务场景不要从重型 Agent 或 GraphRAG 起步。默认先落地 Advanced RAG；需要动态检索和多步查证时，再叠加轻量 ReAct，形成 Lightweight Agentic RAG。

## 1. 工程视角下的 RAG 分类

### 1.1 Naive RAG

Naive RAG 是最基础的 RAG 形态：先检索，再把检索结果拼进 prompt，最后由 LLM 生成答案。

```text
User Query
-> Embed Query
-> Vector Search Top-K
-> Prompt with Context
-> LLM Answer
```

适用场景：

- Demo、PoC、内部原型。
- 文档数量少，结构简单。
- 用户问题相对稳定。
- 允许人工复核答案。
- 对引用、权限、召回率和稳定性要求不高。

优点：

- 实现简单。
- 延迟低。
- 组件少，容易调试。
- 可以快速验证业务价值。

主要问题：

- 召回质量不稳定。
- top-k 容易混入无关 chunk。
- chunk 粒度难调。
- 多跳问题和综合分析能力弱。
- 缺少答案引用、证据校验和质量评估。

选型建议：

> 只建议用于原型阶段。进入真实业务后，至少补上 rerank、引用和基础评估。

### 1.2 Advanced RAG

Advanced RAG 在 Naive RAG 基础上增强检索前、检索中、检索后的质量控制。

```text
User Query
-> Query Rewrite / Expansion
-> Hybrid Retrieval
-> Metadata Filter
-> Rerank
-> Context Dedup / Compression
-> LLM Answer with Citations
```

常用能力：

| 环节 | 技术 | 作用 |
| --- | --- | --- |
| 查询前处理 | query rewrite、multi-query、query expansion | 提升召回覆盖率 |
| 召回 | vector search、BM25、hybrid search | 同时覆盖语义相似和关键词精确匹配 |
| 过滤 | metadata filter、ACL filter、time filter | 保证权限、业务范围和时效性 |
| 排序 | cross-encoder reranker、LLM rerank | 把真正相关内容排到前面 |
| 上下文处理 | 去重、压缩、parent-child chunk | 控制 token，减少噪声 |
| 生成 | citation、grounded answer、拒答策略 | 提高可解释性和可信度 |
| 评估 | recall、faithfulness、answer relevance | 支撑迭代和回归测试 |

适用场景：

- 企业知识库问答。
- 客服知识库。
- 产品文档助手。
- 政策、制度、流程类问答。
- 研发文档检索。
- FAQ + 文档混合问答。

优点：

- 工程复杂度适中。
- 质量显著高于 Naive RAG。
- 易于加权限、监控和评估。
- 可作为大多数 RAG 系统的默认主干。

主要问题：

- 对复杂多步问题仍然偏弱。
- 固定 pipeline 不会主动补查。
- 对“先查 A，再根据 A 查 B”的任务支持有限。

选型建议：

> Advanced RAG 是大多数生产 RAG 的默认起点。先把 hybrid retrieval、rerank、citation、评估链路做好，再考虑 Agent 化。

### 1.3 Modular RAG

Modular RAG 把 RAG 链路拆成可组合模块，由 router 或 workflow 根据问题类型选择不同处理路径。

```text
User Query
-> Intent / Task Router
-> Select Retriever / Tool / Knowledge Base
-> Retrieve / Rerank / Verify
-> Generate / Refuse / Ask Clarification
```

常见模块：

| 模块 | 职责 |
| --- | --- |
| Query Router | 判断问题类型和目标知识源 |
| Retriever Router | 选择向量、BM25、SQL、API、图检索 |
| Query Rewriter | 重写模糊问题 |
| Decomposer | 拆解复杂问题 |
| Reranker | 重排候选证据 |
| Context Compressor | 压缩上下文 |
| Verifier | 校验答案是否被证据支持 |
| Citation Builder | 生成引用 |
| Guardrail | 权限、安全、格式和拒答控制 |

适用场景：

- 多知识库。
- 多租户。
- 需要区分文档问答、SQL 查询、API 查询。
- 有明显业务路由规则。
- 对稳定性和可观测性要求高。

优点：

- 可维护性高。
- 可局部替换检索器或模型。
- 容易做 A/B 测试。
- 适合复杂业务系统长期演进。

主要问题：

- 设计成本高于单链路 RAG。
- 模块边界和状态管理需要规范。
- 过早模块化会增加交付成本。

选型建议：

> 当一个 RAG 系统开始服务多个业务域、多个数据源或多类问题时，应从 Advanced RAG 演进为 Modular RAG。

### 1.4 Lightweight Agentic RAG

Lightweight Agentic RAG 是在 Advanced RAG 上增加受控的 ReAct 循环。LLM 可以在有限轮数内决定是否检索、检索什么、是否需要补查，但工具集合、轮数、预算和输出格式都受到约束。

```text
User Query
-> ReAct Planner
-> Action: retrieve / read / answer / refuse
-> Observation
-> Optional Follow-up Retrieval
-> Final Answer with Citations
```

适用场景：

- 问题有一定复杂度，但不是深度研究任务。
- 用户问题经常需要补查。
- 需要多轮检索，但轮数通常不超过 1-3 轮。
- 需要在多个检索工具之间做轻量选择。
- 希望比固定 pipeline 灵活，但不想引入重型 Agent。

优点：

- 灵活性高于 Advanced RAG。
- 复杂度低于完整 Agentic RAG。
- 更适合生产控制。
- 可以复用现有 Advanced RAG 组件。

主要问题：

- 延迟和成本高于单次 RAG。
- 需要 trace、预算和循环终止条件。
- 对 prompt injection 和工具误用更敏感。

选型建议：

> 当前非复杂场景结合 ReAct，优先使用 Lightweight Agentic RAG，而不是重型多 Agent 或 GraphRAG。

### 1.5 Heavy Agentic RAG

Heavy Agentic RAG 允许 Agent 长链路规划、调用多个工具、执行多步研究、生成中间结论并持续反思。

适用场景：

- 深度研究。
- 自动报告生成。
- 多系统数据分析。
- 需要访问搜索、数据库、API、文件、代码仓库等多个工具。
- 任务执行时间可以接受数十秒到数分钟。

优点：

- 适合开放式复杂任务。
- 可以跨多个系统找证据。
- 具备动态规划和修正能力。

主要问题：

- 延迟高。
- 成本高。
- 可预测性低。
- 调试和评估困难。
- 权限、审计、失败恢复成本高。

选型建议：

> 不建议作为普通知识库问答默认方案。只有当业务明确需要长链路研究和多工具执行时再引入。

### 1.6 Graph RAG

Graph RAG 把文档中的实体、关系、社区或主题组织成图结构，并结合原始文本片段生成答案。

```text
Documents
-> Entity / Relation Extraction
-> Graph Build / Community Detection
-> Graph Retrieval + Text Retrieval
-> Answer Generation
```

适用场景：

- 实体关系密集。
- 问题经常跨文档、跨实体、跨层级。
- 需要理解整体主题、社区和关系路径。
- 文档集合具有强叙事结构或强组织结构。
- 例如：投研、法务案件、风控、医学文献、复杂项目知识、代码依赖分析。

优点：

- 适合关系推理和全局主题分析。
- 可以弥补纯向量 chunk 检索的漏召回。
- 对“这个实体和哪些风险相关”这类问题更自然。

主要问题：

- 构图成本高。
- 实体消歧难。
- 增量更新复杂。
- 需要额外图存储、图算法和图质量评估。
- 不适合从零快速验证。

选型建议：

> 只有当实体关系是核心问题，且 Advanced RAG / Lightweight Agentic RAG 无法稳定解决时，再引入 Graph RAG。

### 1.7 Multimodal RAG

Multimodal RAG 面向图片、表格、音频、视频、扫描件、PPT、图纸等非纯文本资产。

适用场景：

- PDF 版面复杂。
- 表格和图表是核心知识。
- 需要理解截图、合同扫描件、PPT、视频会议、产品图片。
- 需要跨文本和视觉内容回答问题。

常用能力：

- OCR。
- 文档版面解析。
- 表格结构抽取。
- 图片 embedding。
- 图文联合检索。
- 视频切片、字幕和摘要索引。
- 多模态模型读取证据。

选型建议：

> 不要把复杂 PDF 或图片简单转成纯文本 chunk。文档版面、表格和图像本身是知识结构，需要单独建模和索引。

### 1.8 Production RAG

Production RAG 不是单独算法，而是一组生产能力。任何进入真实业务的 RAG 都需要逐步补齐。

核心能力：

| 能力 | 说明 |
| --- | --- |
| 数据接入 | 文件、网页、数据库、对象存储、第三方系统 |
| 增量索引 | 文档更新后局部重建索引 |
| 权限过滤 | 先过滤权限，再进入模型上下文 |
| 多租户隔离 | 租户、项目、知识库、用户维度隔离 |
| 版本管理 | 文档版本、chunk 版本、embedding 版本 |
| 评估 | 检索召回、rerank、答案真实性、引用准确性 |
| 可观测性 | trace 每次 query、召回、rerank、prompt 和 token |
| 缓存 | query cache、embedding cache、rerank cache |
| 安全 | prompt injection、防越权、敏感信息脱敏 |
| 失败处理 | 超时、降级、重试、拒答、人工兜底 |

选型建议：

> 生产 RAG 的核心难点通常不是“能不能检索”，而是权限、评估、更新、观测和稳定性。

## 2. 推荐选型矩阵

| 场景 | 推荐方案 | 不建议默认使用 |
| --- | --- | --- |
| 快速 PoC | Naive RAG | Graph RAG、Heavy Agent |
| 普通知识库问答 | Advanced RAG | Heavy Agent、Graph RAG |
| 企业内部文档助手 | Advanced RAG + citation + ACL | 纯 Naive RAG |
| 多知识库 / 多数据源 | Modular RAG | 单一固定 pipeline |
| 有轻量多步查证 | Lightweight Agentic RAG | Heavy Agent |
| 深度研究 / 自动报告 | Heavy Agentic RAG | 纯 Advanced RAG |
| 实体关系强 | Graph RAG + Text RAG | 纯向量检索 |
| PDF / 表格 / 图片密集 | Multimodal RAG | 简单 OCR 后纯文本 RAG |
| 高权限要求业务 | Production RAG + ACL-first retrieval | 先检索再过滤 |
| 低延迟客服 | Advanced RAG，少量工具，强缓存 | 多轮 Agent |

## 3. 默认工程路线

推荐分阶段建设：

```text
Phase 1: Naive RAG
-> Phase 2: Advanced RAG
-> Phase 3: Lightweight Agentic RAG
-> Phase 4: Modular RAG
-> Phase 5: Graph / Multimodal / Heavy Agent by need
```

### 3.1 Phase 1：最小可用 RAG

目标：

- 验证业务是否真的需要 RAG。
- 验证数据质量是否足够。
- 建立文档接入、chunk、embedding、检索、生成的闭环。

必备能力：

- 文档加载。
- chunk 切分。
- embedding。
- vector search。
- 简单 prompt。
- 基础引用。

退出条件：

- 能回答核心 FAQ。
- 能暴露检索漏召回和 chunk 质量问题。
- 有一组最小评估问题集。

### 3.2 Phase 2：生产可用 Advanced RAG

目标：

- 提升召回和生成质量。
- 建立可观测和评估链路。
- 支持权限和增量更新。

必备能力：

- hybrid retrieval。
- rerank。
- metadata / ACL filter。
- citation。
- 检索和答案日志。
- 离线评估集。
- 索引版本。

退出条件：

- 典型问题质量稳定。
- 能定位问题出在 query rewrite、召回、rerank、上下文还是生成。
- 有上线前评估和回归流程。

### 3.3 Phase 3：轻量 Agent 化

目标：

- 处理需要补查、多跳或工具选择的问题。
- 保持生产可控。

必备能力：

- ReAct loop。
- 有限工具集合。
- 最大迭代次数。
- token 和耗时预算。
- trace。
- 明确终止条件。
- 失败降级。

退出条件：

- 对复杂问题质量明显优于固定 Advanced RAG。
- 平均延迟和成本仍在业务可接受范围。
- Agent 行为可回放、可解释、可限制。

### 3.4 Phase 4：模块化和多业务扩展

目标：

- 支持多个知识库、多个检索器、多个业务流程。
- 控制复杂度，避免单条链路膨胀。

必备能力：

- router。
- retriever registry。
- tool registry。
- workflow state。
- 统一 trace。
- 统一评估接口。

退出条件：

- 新增知识源不需要重写主流程。
- 不同业务可以独立配置 retrieval policy。
- 关键模块可以独立测试和替换。

## 4. 技术组件选型建议

### 4.1 检索策略

| 数据特点 | 推荐检索 |
| --- | --- |
| 术语明确、编号多、专有名词多 | BM25 + metadata filter |
| 问法多变、语义表达丰富 | vector search |
| 企业文档混合场景 | hybrid search |
| 强结构字段 | SQL / structured filter |
| 实体关系强 | graph retrieval + text retrieval |
| 图片和扫描件 | OCR + multimodal retrieval |

默认建议：

```text
普通文档问答 = hybrid retrieval + rerank
```

### 4.2 Chunk 策略

| 文档类型 | 推荐策略 |
| --- | --- |
| Markdown / 技术文档 | 按标题层级切分，保留 heading path |
| PDF 制度文档 | 按段落和章节切分，保留页码 |
| API 文档 | 按 endpoint / schema / example 切分 |
| FAQ | 一问一答作为最小单元 |
| 表格 | 保留表头、主键、上下文说明 |
| 长报告 | parent-child chunk，小块召回，大块生成 |

关键原则：

- chunk 不是越小越好；太小会丢上下文。
- chunk 不是越大越好；太大会降低召回精度。
- 检索单元和生成上下文可以不是同一个粒度。

### 4.3 Rerank 策略

建议优先级：

1. 先用 embedding / BM25 做宽召回。
2. 再用 reranker 对候选结果精排。
3. 对高价值业务可加 LLM verifier，但不要默认全量使用。

适合加 rerank 的信号：

- top-k 结果噪声多。
- 用户问题短或模糊。
- 文档块语义相似但答案只在少数块中。
- 召回命中但答案经常引用错误片段。

### 4.4 引用策略

生产答案应尽量带引用。引用粒度建议：

- 文档标题。
- 章节路径。
- 页码或段落号。
- chunk id。
- 文档版本。
- 更新时间。

引用不是装饰，而是用于：

- 用户信任。
- 错误定位。
- 权限审计。
- 评估答案 groundedness。

### 4.5 权限策略

权限过滤必须发生在模型看到上下文之前。

正确流程：

```text
User
-> Resolve ACL
-> Retrieve only allowed documents/chunks
-> Rerank allowed candidates
-> Build prompt
```

错误流程：

```text
Retrieve all
-> Put into prompt
-> Ask model not to reveal unauthorized content
```

## 5. 决策规则

### 5.1 默认选择

如果没有明确复杂需求，默认选择：

```text
Advanced RAG
```

如果用户问题经常需要补查或轻量多步推理，选择：

```text
Advanced RAG + ReAct = Lightweight Agentic RAG
```

### 5.2 不要过早选择 Graph RAG

只有满足以下条件时才考虑 Graph RAG：

- 业务问题核心是实体关系。
- 纯文本检索已验证不足。
- 有构图、更新、评估和运维预算。
- 可以接受更高索引成本和更长建设周期。

### 5.3 不要过早选择 Heavy Agent

只有满足以下条件时才考虑 Heavy Agentic RAG：

- 任务本身是开放式研究或多系统执行。
- 可以接受较高延迟和成本。
- 已有工具权限、审计和失败恢复设计。
- 有 trace 和评估能力。

### 5.4 最推荐的普通生产方案

普通企业知识问答推荐：

```text
Hybrid Search
+ Rerank
+ Metadata / ACL Filter
+ Citation
+ Evaluation
+ Optional Lightweight ReAct
```

## 6. 与本项目的落地映射

结合当前项目定位，建议后续落地时按以下边界组织：

| 层 | 建议职责 |
| --- | --- |
| `internal/controllers` | 提供 RAG API，不直接拼 prompt 或访问向量库 |
| `internal/services` | 编排 RAG use case、权限、检索、生成和 trace |
| `internal/dao` | 管理知识库、文档、chunk、任务、评估记录等持久化 |
| `internal/cache` | query cache、embedding cache、短期状态、幂等键 |
| `internal/tasks` | 文档解析、chunk、embedding、索引重建、评估任务 |
| `pkg/vectors` | 向量后端抽象、repository、检索接口 |
| `pkg/logger` | RAG trace、span、错误上下文 |

建议不要把 RAG 逻辑直接写进 controller，也不要让模型层直接访问数据库或 Redis。RAG 的业务编排应进入 service 层，检索后端通过 repository / provider 注入。

## 7. 参考资料

- Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks: <https://arxiv.org/abs/2005.11401>
- Retrieval-Augmented Generation for Large Language Models: A Survey: <https://arxiv.org/abs/2312.10997>
- ReAct: Synergizing Reasoning and Acting in Language Models: <https://arxiv.org/abs/2210.03629>
- LangChain RAG agent documentation: <https://docs.langchain.com/oss/python/langchain/rag>
- Microsoft GraphRAG query overview: <https://microsoft.github.io/graphrag/query/overview/>
