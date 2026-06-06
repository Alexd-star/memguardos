# 智能体记忆论文深度调研与 MemGuardOS 改进建议

日期：2026-06-06  
项目：MemGuardOS  
赛题：研究生操作系统开源创新大赛赛题 3「面向 Linux 的智能体记忆提取与精准遗忘机制」  
资料范围：Hugging Face Papers `2512.13564` 及 Librarian Bot 推荐的 7 篇相关论文。

## 1. 结论摘要

本次阅读的论文形成了一个非常清晰的趋势：智能体记忆已经不再是“把聊天记录塞进向量库再 RAG 一下”的工程小功能，而正在变成 Agent 系统的一级能力。主综述《Memory in the Age of AI Agents》把智能体记忆拆成三个视角：承载形式、功能类型和生命周期动态。承载形式包括 token-level、parametric、latent memory；功能类型包括 factual、experiential、working memory；生命周期动态包括 formation、evolution、retrieval。这套框架对 MemGuardOS 很有价值，因为赛题要求的“多源融合偏好与知识记忆优化”和“精准遗忘”本质上也是在操作记忆的形成、演化、检索和删除。

从项目竞争力看，MemGuardOS 当前路线是正确的：端侧、可审计、可遗忘、可复验，明显区别于单纯云端聊天助手或单纯向量数据库。它已经具备多源接入、偏好/知识/策略记忆、三层记忆、时间有效性、SQLite FTS + hash-vector + 图谱检索、RAG 上下文构建、敏感治理、精准遗忘、审计哈希链、openKylin SDK 探测、systemd/cgroup/auditd/BPF LSM/UKUI 集成等能力。与论文前沿相比，最值得继续强化的不是再堆一个大模型接口，而是把“经验记忆/程序性记忆”“研究式上下文生成”“层次化用户画像与事件记忆”“图谱时间重排”“评测难例集”进一步做深。

短期最值得补的方向有四个。第一，借鉴 PRAXIS，把工具执行和 shell 行为从普通行为日志提升为“状态-动作-结果”的程序性记忆，让 Agent 能复用成功流程、避开失败操作。第二，借鉴 GAM，把当前 `build-context` 的一次性检索扩展成“轻记忆索引 + 完整页库 + 运行时研究式上下文生成”的解释框架，优先在文档和评测中体现，代码上可逐步增加 page-store 元数据。第三，借鉴 O-Mem/MMAG，把偏好记忆细分为长期用户画像、事件记录、会话工作记忆、环境上下文记忆，并明确冲突优先级。第四，借鉴 LiCoMemory，把当前图谱检索从实体边扩展为“摘要层、实体关系层、原文 chunk 层”的可解释层次检索，并加入时间衰减/时间有效性权重。

不建议当前阶段强行做的方向也很明确。MemVerse 的多模态长期记忆很前沿，但会引入截图、OCR、视觉 embedding、多模态模型和更复杂隐私问题；在赛题 3 的 Linux 智能体记忆与精准遗忘目标下，它适合作为扩展路线，不适合作为提交前主线风险。Beyond Pipelines 的 model-native agentic AI 代表长期范式，但 MemGuardOS 是操作系统侧记忆组件，价值在于可控、可审计、可删除、可替换模型，而不是把记忆完全内化到模型参数里。

## 2. 论文一：Memory in the Age of AI Agents: A Survey

来源：<https://huggingface.co/papers/2512.13564>，<https://arxiv.org/abs/2512.13564>  
性质：综述。  
核心贡献：把智能体记忆从混乱术语中整理为“形式、功能、动态”的三维框架，并区分 agent memory、LLM memory、RAG、context engineering。

### 2.1 核心观点

论文认为，智能体记忆是基础模型智能体进行长程推理、持续适应和复杂环境交互的核心能力。过去“短期记忆/长期记忆”这种粗分法不足以描述当前系统，因为现代 Agent 的记忆既可能存在于 prompt token、外部数据库、图结构、模型参数，也可能存在于 latent state 或 KV cache 这类隐式表示中。更重要的是，记忆不是静态仓库，而是随着交互持续形成、更新、合并、遗忘和检索的动态系统。

论文的三个分析维度非常适合 MemGuardOS 作为答辩理论框架：

| 维度 | 论文分类 | 对 MemGuardOS 的意义 |
|---|---|---|
| Form | token-level、parametric、latent | MemGuardOS 目前主要是 token-level 外部记忆，配合可选 embedding 表示；没有把记忆写进模型参数，这是端侧系统组件的安全选择 |
| Function | factual、experiential、working | MemGuardOS 已覆盖偏好/知识/策略，三层记忆可映射 working/session/long_term；下一步应显式强化 experiential/procedural memory |
| Dynamics | formation、evolution、retrieval | MemGuardOS 已有抽取、时间有效性、冲突处理、检索、遗忘；可继续补“合并/巩固/重排”的评测指标 |

### 2.2 对赛题 3 的直接价值

赛题要求面向 Linux 的智能体记忆提取与精准遗忘，关键词是“智能体”“记忆提取”“精准遗忘”“端侧部署”“openKylin SDK”“轻量化存储”。这篇综述可以支撑我们在答辩中说明：MemGuardOS 不是聊天记录管理器，而是把 OS Agent 的多源交互轨迹转化为可检索、可治理、可删除的 agent memory system。

尤其是论文强调 agent memory 与 RAG 不等价。RAG 通常解决“把外部知识取回来辅助生成”，而 agent memory 还要管理用户偏好、环境状态、历史行为、工具经验、任务中间状态和跨任务演化。MemGuardOS 当前的 `build-context` 只是 Agent 使用记忆的一种出口，真正核心在于写入、抽取、治理、版本、审计和遗忘。

### 2.3 对当前项目的差距判断

MemGuardOS 与综述框架的符合度较高，但仍有两个可强化点。第一，experiential memory 在文档和评测中的存在感还不够。项目已经能接入 shell history、tool result 和 OS event，但这些数据多数被当作行为/知识记录，而不是明确建模成“在某种状态下执行某个动作得到某个结果”的经验记忆。第二，memory evolution 里的 consolidation 可以更明显。项目已有冲突处理、版本 supersede、tier promote/evict，但还可以增加“多条相似记忆合并成稳定偏好/操作准则”的说明和评测。

## 3. 论文二：Real-Time Procedural Learning From Experience for AI Agents

来源：<https://huggingface.co/papers/2511.22074>，<https://arxiv.org/abs/2511.22074>  
核心系统：PRAXIS，Procedural Recall for Agents with eXperiences Indexed by State。

### 3.1 核心观点

论文关注的是 Agent 部署之后如何从真实试错中学习“怎么做事”。它区分事实记忆和程序性记忆：事实是与状态相对独立的信息，比如用户姓名；程序性记忆是依赖状态的动作偏好，比如在某个网页状态下应该点哪个按钮、不要再走哪条失败路径。

PRAXIS 的关键是把经验存成状态索引的轨迹：环境状态、Agent 内部状态、采取的动作、动作后的环境状态或结果。检索时不是只按任务目标相似度找历史，而是联合匹配当前环境状态和内部状态，返回相似场景下的 state-action-result exemplar。论文在 REAL 网页浏览 benchmark 上报告了准确率、可靠性和效率提升，说明程序性记忆能让 Agent 在动态界面和长流程任务中减少重复试错。

### 3.2 对 MemGuardOS 的启发

这篇论文对 MemGuardOS 很关键，因为 Linux OS Agent 天然有大量程序性经验：命令行、systemd、journalctl、pytest、git、apt、权限修复、桌面插件构建、SDK 探针。这些内容如果只当文本知识存储，价值不够高；如果抽象为“状态-动作-结果”记忆，就能用于下一次自动调试。

MemGuardOS 当前已经有 `ingest-history`、`ingest-tool-result`、`ingest-os-event` 和审计事件接入，这为程序性记忆打好了底座。建议后续把工具结果进一步拆成以下字段：

| 字段 | 含义 | 示例 |
|---|---|---|
| `state_before` | 执行动作前的环境状态 | `systemd service inactive`、`pytest failing test_core` |
| `action` | 执行动作 | `systemctl restart memguardd`、`python -m pytest -q` |
| `result` | 输出结果 | `active`、`57 passed`、`exit_code=1` |
| `state_after` | 执行后的状态 | `daemon healthy`、`audit chain ok` |
| `lesson` | 可复用经验 | `restart service after changing unit file` |
| `risk` | 风险等级 | 涉及 sudo、rm、chmod、secret path 时标高风险 |

这样做不会破坏当前架构，仍可保存在 mmap + SQLite 中，只是增加 subtype 和评测样例。对比赛来说，这能显著增强“应用于智能体”的说服力：系统不仅记用户说过什么，还记 Agent 在 Linux 环境里做过什么、哪些动作成功、哪些动作要避免。

## 4. 论文三：MemVerse: Multimodal Memory for Lifelong Learning Agents

来源：<https://huggingface.co/papers/2512.03627>，<https://arxiv.org/abs/2512.03627>  
核心主题：多模态、层次化知识图谱、短期记忆、长期记忆、参数化快速召回、持续巩固和自适应遗忘。

### 4.1 核心观点

MemVerse 认为，多模态 Agent 不能只依赖长上下文或一次性检索。它同时维护短期记忆、长期层次化多模态知识图谱和轻量参数记忆。原始多模态经验会先转成结构化表示，再进入长期图谱；系统通过巩固、冗余过滤、自适应遗忘和有界存储避免无限增长。

### 4.2 对 MemGuardOS 的启发

MemVerse 的“短期上下文 + 长期图谱 + 巩固 + 遗忘”与 MemGuardOS 的三层记忆、图谱边、精准遗忘非常一致。可借鉴的不是立刻引入多模态大模型，而是两个设计原则：

第一，记忆进入长期库前应有质量和冗余控制。MemGuardOS 已有 `quality.py` 和去噪，但可以在文档中更明确“短期/会话记忆不等于长期沉淀，长期沉淀需要质量分和可复用性”。第二，图谱不应只存实体边，还应服务于层次化组织。对 OS Agent 来说，图谱层次可以是：任务/主题摘要、实体关系、原始命令或日志 chunk。

多模态方向建议作为后续增强：比如截图 OCR、桌面窗口标题、文件图像摘要、UKUI 操作截图等。当前提交前不建议把它作为主路径，因为它会引入模型下载、GPU/CPU 推理、隐私脱敏和演示不稳定性。

## 5. 论文四：Beyond Pipelines: Model-Native Agentic AI

来源：<https://huggingface.co/papers/2510.16720>，<https://arxiv.org/abs/2510.16720>  
性质：综述。  
核心主题：Agent 从外部 pipeline 走向模型内生能力，RL 在规划、工具使用、记忆管理中的作用。

### 5.1 核心观点

论文认为 Agentic AI 正在从 pipeline-based 走向 model-native。传统系统把规划、工具、记忆作为外部脚本和模块拼起来；model-native 系统希望模型通过训练或强化学习把这些能力内化。记忆方面，论文提到短期记忆从长上下文和上下文管理发展，长期记忆从外部检索逐步走向参数化或模型内生机制。

### 5.2 对 MemGuardOS 的定位影响

这篇论文不是要求我们把 MemGuardOS 改成模型训练项目，而是帮助我们讲清楚定位：MemGuardOS 是操作系统侧、模型无关、可审计、可删除的记忆基础设施。它适合接入不同 Agent 和不同 LLM，而不是绑定某个模型权重。赛题强调 Linux、openKylin、端侧部署、轻量存储和精准遗忘，这些要求天然更偏系统组件而不是模型内生训练。

因此，答辩时可以把 model-native 作为长期趋势承认，但强调当前赛题下外部 OS memory substrate 的必要性：模型内生记忆难以证明删除，难以审计，难以做用户级 namespace 隔离，难以对接 auditd、systemd、cgroup 和 BPF LSM。MemGuardOS 的优势正是补足这些系统层需求。

## 6. 论文五：General Agentic Memory Via Deep Research

来源：<https://huggingface.co/papers/2511.18423>，<https://arxiv.org/abs/2511.18423>  
核心系统：GAM，General Agentic Memory。

### 6.1 核心观点

GAM 反对把记忆完全提前压缩成静态摘要。论文认为 AOT 风格的预构建记忆必然有信息损失，因为摘要是一种压缩。GAM 提出 JIT 风格：离线阶段只维护轻量关键记忆，同时保留完整历史 page-store；在线阶段由 Researcher 根据当前请求进行类似 deep research 的检索、整合、反思，生成高效上下文。

这个思路的关键不是“永远存所有东西不删”，而是区分两类存储：轻量记忆用于导航和索引，完整页库用于需要高保真细节时回查。Researcher 会计划搜索、取回页面、整合信息、判断是否足够，不足则继续搜索。

### 6.2 对 MemGuardOS 的启发

MemGuardOS 的 mmap 段文件天然类似 page-store，SQLite 元数据/FTS/图谱天然类似轻量索引。当前 `build-context` 是一次检索排序后输出上下文包，已经能满足赛题；但如果要冲更高亮点，可以把它解释和发展为“OS 记忆 page-store + 轻量索引 + Agent 上下文编译器”。

推荐后续改进：

1. 在文档中把 mmap segment 明确描述为不可变原始证据页库，SQLite/FTS/graph 是索引层，审计链是可信性证明层。
2. 在 `build-context` 输出中增加“上下文来源分布”：偏好、知识、程序性经验、OS 事件、审计事件各占多少。
3. 增加 `research-context` 或 `explain-context-plan` 命令，输出本次上下文构建为什么选这些记忆、哪些记忆被过滤、是否触发敏感脱敏。
4. 保持默认轻量，不引入外部 LLM。Researcher 可以先是确定性规划器，未来再接 LLM planner。

GAM 对精准遗忘也有启发：如果保留完整页库，就必须确保遗忘时不仅删除索引，还要 tombstone 原文、compact 后验证明文不可检索。MemGuardOS 已经做了这一点，这是相比很多 Agent memory 框架更贴合 OS 赛题的强项。

## 7. 论文六：O-Mem: Omni Memory System for Personalized, Long Horizon, Self-Evolving Agents

来源：<https://huggingface.co/papers/2511.13593>，<https://arxiv.org/abs/2511.13593>  
核心主题：主动用户画像、persona memory、event record、working memory、hierarchical retrieval。

### 7.1 核心观点

O-Mem 指出，很多记忆系统依赖“先语义分组、再检索”的设计，但这种方式可能漏掉语义上不相近却对个性化很关键的信息，也可能引入检索噪声。它将用户交互持续抽取成用户画像属性、事实事件、当前主题相关工作记忆，并通过层次化检索支持个性化回答。论文也特别关注延迟和 token 成本，强调性能和效率的 Pareto 平衡。

### 7.2 对 MemGuardOS 的启发

MemGuardOS 的偏好记忆目前已经能提取“以后回答 Linux 命令时直接给命令和风险提示”这类偏好，但还可以更明确地区分：

| 记忆层 | 当前项目对应 | 可增强点 |
|---|---|---|
| Persona/Profile | 长期偏好、命名空间配置 | 增加用户画像 subtype，例如 `answer_style`、`risk_tolerance`、`tool_preference`、`privacy_preference` |
| Event | 工具结果、OS 事件、auditd 事件 | 增加事件时间线和“事件对偏好的证据关系” |
| Working | `working` tier、当前上下文 | 检索时优先但不长期沉淀，任务结束可驱逐 |
| Topic/Session | `session` tier | 按任务主题维护短周期记忆 |

对比赛最有帮助的是把“偏好记忆”从一句文本扩展成结构化用户画像与证据事件。这样评委问“你怎么证明偏好不是随便抽的”时，可以回答：每条偏好有来源、时间、生效窗口、置信/质量分、关联事件和可遗忘路径。

## 8. 论文七：MMAG: Mixed Memory-Augmented Generation

来源：<https://huggingface.co/papers/2512.01710>，<https://arxiv.org/abs/2512.01710>  
核心主题：五层混合记忆：conversational、long-term user、episodic/event-linked、sensory/context-aware、short-term working。

### 8.1 核心观点

MMAG 更像一个实践设计模式。它把 Agent 记忆分为会话历史、长期用户记忆、事件记忆、感知/上下文记忆、短期工作记忆，并讨论不同记忆之间的协调、优先级、冲突处理、隐私、延迟和 token 剪枝。论文的一个重要现实观点是：多层记忆越丰富，越要处理隐私、延迟、成本和冲突；不能把所有记忆都无脑塞进 prompt。

### 8.2 对 MemGuardOS 的启发

MemGuardOS 已经有 token 预算估算和脱敏上下文包，这与 MMAG 的工程思想一致。可继续补的是“多层记忆协调策略”：

1. 当前任务相关的 working memory 优先，但生命周期短。
2. 长期用户偏好优先级高，但必须经过敏感治理和用户可遗忘控制。
3. OS 环境事实优先于旧偏好，例如真实 systemd 状态、auditd 事件不能被过期偏好覆盖。
4. 事件记忆用于解释“为什么这样建议”，不是永远进入 prompt。
5. 高敏感记忆默认不进入生成上下文，只能进入审计或治理流程。

这可以直接写入 `详细设计说明书` 或后续改进计划，也可以在 `build-context` 结果中体现为层级与过滤原因。

## 9. 论文八：LiCoMemory: Lightweight and Cognitive Agentic Memory for Efficient Long-Term Reasoning

来源：<https://huggingface.co/papers/2511.01448>，<https://arxiv.org/abs/2511.01448>  
核心系统：LiCoMemory / CogniGraph。

### 9.1 核心观点

LiCoMemory 认为很多图记忆系统把语义内容和拓扑结构混在一起，导致图过大、冗余、检索慢、准确率下降。它提出 CogniGraph：轻量层次图，包含摘要层、实体关系层、chunk 层。检索时先从摘要层定位相关 session，再到实体关系层找结构化证据，最后回到 chunk 层取原文。它还使用时间与层次感知重排，在效率和准确率之间取得平衡。

### 9.2 对 MemGuardOS 的启发

MemGuardOS 已经有 SQLite 图谱边和时间有效性字段，这是非常好的基础。下一步可以把图谱能力从“实体关联检索”升级为“层次化认知图谱”：

| 层次 | MemGuardOS 当前基础 | 建议增强 |
|---|---|---|
| 摘要层 | memory summary、type/subtype/tier | 增加 task/session summary，作为 top-down 检索入口 |
| 实体关系层 | graph edges | 增加关系类型、证据来源、时间权重 |
| 原文层 | mmap segment content | 保持原文可追溯、可 compact、可明文探针验证 |
| 重排层 | hybrid score、conflict resolver | 增加 temporal score、tier score、source trust score |

该方向对赛题很适合，因为它仍然轻量、可解释、端侧可运行，而且能展示比普通向量库更强的 OS 系统设计能力。

## 10. 横向技术脉络

把 8 篇论文合起来看，可以得到以下脉络。

第一，记忆系统从“静态存储”走向“生命周期管理”。形成、演化、检索、遗忘同等重要。只做写入和搜索，不够像智能体记忆系统。

第二，记忆类型从“短期/长期”走向多层协同。事实、偏好、经验、工作上下文、事件、环境状态、程序性轨迹都要区分，否则检索时会混乱。

第三，检索从“单向量相似度”走向混合检索和重排。关键词、图谱、时间、层级、来源可靠性、敏感等级都可以参与排序。

第四，记忆从“越多越好”走向“有界、有质量、有成本控制”。O-Mem、MMAG、LiCoMemory 都提到 token、延迟或效率。对 MemGuardOS 来说，轻量化存储、token 预算和 P95 延迟是加分点，不是次要指标。

第五，遗忘和可信治理仍是很多论文的薄弱项。多数 Agent memory 论文强调记住和检索，较少真正实现 OS 级审计、删除验证、明文探针、tombstone、compact、敏感阻断。MemGuardOS 应当把这一点作为差异化亮点。

## 11. 与 MemGuardOS 当前能力的对照

| 前沿要求 | MemGuardOS 当前状态 | 判断 |
|---|---|---|
| 多源记忆形成 | 支持文本、文件、配置、shell、工具结果、OS 事件、auditd | 已满足，建议强化程序性字段 |
| 多层记忆 | working/session/long_term，偏好/知识/策略 | 已满足，建议文档映射到 factual/experiential/working |
| 记忆演化 | 时间有效性、supersede、冲突处理、tier promote/evict | 已满足，建议增加 consolidation 评测 |
| 混合检索 | FTS、hash-vector、图谱、跨语言 hints、Kylin SDK 可选重排 | 已满足，建议增加 temporal/source/tier 权重说明 |
| RAG 上下文 | build-context/build-prompt，token 预算，脱敏 | 已满足，建议增加上下文选择解释 |
| 精准遗忘 | dry-run、confirm、tombstone、compact、verify-forget、审计链 | 亮点能力，应继续突出 |
| 端侧部署 | 默认离线、无外部 API/GPU、mmap + SQLite | 贴合赛题 |
| openKylin SDK | embedding/vector 探测，可选 rerank，fallback | 已做集成，需准确表述不是默认强依赖 |
| 程序性记忆 | 行为/工具结果接入，尚未充分建模为 state-action-result | 可补强 |
| 多模态记忆 | 未作为主路径 | 合理边界，后续方向 |
| 模型内生记忆/RL | 未做模型训练 | 合理边界，赛题不要求 |

## 12. 建议改进路线

### 12.1 立即可做且风险低

1. 在文档中补充“论文框架映射”：把 MemGuardOS 映射到 forms/functions/dynamics，避免评委认为只是工程堆叠。
2. 增加程序性记忆 benchmark 样例：从真实工具日志中构造 state-action-result，用检索验证能找回正确修复经验。
3. 增加 `build-context` 解释字段或文档说明：说明上下文按 memory type、tier、sensitivity、source trust 过滤。
4. 增加“记忆成本与效果平衡”说明：赛题没有硬性 token 成本指标，但 Agent memory 论文普遍关注 latency/token/quality，MemGuardOS 已通过 token budget 和 P95 延迟体现。
5. 在答辩稿加入“为什么不是只用大模型抽取”的解释：端侧 OS 组件需要稳定、可复现、离线、可审计；LLM/NER 可作为可选增强。

### 12.2 提交前可选择性补强

1. 程序性记忆结构化：为工具结果新增 subtype 或 metadata，不破坏现有存储。
2. 层次图谱检索报告：输出摘要层、实体层、原文层的检索证据。
3. 真实日志难例扩充：增加 shell history、journalctl、systemd、pytest、git、SDK timeout 的真实样例，降低 benchmark “自建过拟合”质疑。
4. 上下文包来源统计：给 `build-context` 增加 memory type/tier/source_kind/sensitivity 统计。

### 12.3 不建议当前冲刺强行做

1. 完整多模态长期记忆：需要视觉模型、OCR、截图隐私处理和额外评测，风险大。
2. RL 训练或模型内生记忆：不符合当前设备、预算和赛题核心交付。
3. 把 Kylin embedding SDK 改成无 fallback 主路径：如果 SDK 推理在某些 openKylin 环境超时，会影响稳定性；正确做法是可选增强 + 稳定 fallback。
4. 默认外部 LLM API 生成器：会引入费用、网络、隐私和复现不确定性。

## 13. 可用于答辩的技术表述

可以这样讲：

MemGuardOS 把智能体记忆定义为 OS Agent 与用户、工具和 Linux 环境交互后形成的可治理状态，而不是单纯聊天记录。系统按照“形成、演化、检索、遗忘”的生命周期设计：在形成阶段从文本、配置、shell、工具结果、OS 事件和 auditd 中抽取偏好、知识和策略；在演化阶段用 tier、时间有效性、冲突处理和质量治理控制记忆增长；在检索阶段结合 FTS、轻量向量、图谱、时间和敏感等级构造 RAG 上下文；在遗忘阶段通过候选规划、确认、tombstone、compact、明文探针和审计哈希链证明删除效果。

系统默认端侧可运行，不依赖外部 API/GPU，适合 openKylin 场景。Kylin embedding/vector SDK 被作为可检测和可选增强后端接入，确保在 SDK 不可用或推理超时时仍能稳定完成比赛任务。与很多 Agent memory 论文相比，MemGuardOS 的突出差异是 OS 机制和可信治理：它能与 systemd/cgroup/auditd/journald/BPF LSM/UKUI 对接，并能把精准遗忘变成可验证的系统过程。

## 14. 对项目评分竞争力的影响

这些论文不会推翻当前项目路线，反而增强了项目理论基础。按照赛题要求，MemGuardOS 的强项在于端侧、可复验、OS 机制、精准遗忘、轻量存储、openKylin 适配和完整证据包。论文前沿提醒我们，若想更出彩，需要把“智能体”二字讲得更具体：智能体不是普通用户，而是会执行工具、观察系统状态、从成败经验中改进的自动化主体。因此，程序性记忆和工具轨迹学习是最值得补的差异化亮点。

如果后续继续开发，建议优先级如下：

| 优先级 | 改进 | 预期收益 | 风险 |
|---:|---|---|---|
| P0 | 文档映射 forms/functions/dynamics 与赛题要求 | 提升评委理解度 | 低 |
| P0 | 增加程序性记忆评测样例 | 强化“应用于智能体” | 低 |
| P1 | 上下文包选择解释和来源统计 | 提升可解释性与企业级观感 | 低 |
| P1 | 层次图谱检索说明/轻量实现 | 对齐 LiCoMemory，强化技术亮点 | 中 |
| P2 | 多模态截图/OCR 记忆原型 | 展示未来方向 | 中高 |
| P2 | LLM planner/Researcher 可选插件 | 对齐 GAM | 中高 |

## 15. 参考链接

- Memory in the Age of AI Agents: <https://arxiv.org/abs/2512.13564>
- Real-Time Procedural Learning From Experience for AI Agents: <https://arxiv.org/abs/2511.22074>
- MemVerse: <https://arxiv.org/abs/2512.03627>
- Beyond Pipelines: <https://arxiv.org/abs/2510.16720>
- General Agentic Memory Via Deep Research: <https://arxiv.org/abs/2511.18423>
- O-Mem: <https://arxiv.org/abs/2511.13593>
- MMAG: <https://arxiv.org/abs/2512.01710>
- LiCoMemory: <https://arxiv.org/abs/2511.01448>

