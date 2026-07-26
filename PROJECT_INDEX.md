# MemGuardOS 项目文件索引

本文档是 MemGuardOS 仓库的中文文件索引和维护入口，用于帮助评委、开发者、运维人员和后续维护者快速理解“哪些文件是作品主体、哪些文件是证据、哪些文件是工程支撑、哪些文件属于历史或过程材料”。本文档只做说明和整理建议，不删除、不移动任何现有文件。

## 1. 项目总体说明

MemGuardOS 是面向 Linux/openKylin 的智能体记忆提取与精准遗忘系统。项目已接入现有 OpenHands Agent，围绕用户-Agent 交互习惯提供统一事件采集、偏好与知识抽取、跨会话三阶段上下文、范围化精准遗忘、敏感治理、轻量化存储、混合检索、审计哈希链、systemd/cgroup/auditd/BPF LSM/UKUI 等能力。

项目主体由 Python 包 `memguard/`、辅助脚本 `scripts/`、原生增强 `native/`、测试 `tests/`、数据 `data/` 和中文文档 `docs/` 组成。正式竞赛提交包由 `scripts/package_submission.py` 生成，完整工程仓库包用于 GitHub 发布、备份和协作。

## 2. 根目录文件说明

| 文件或目录 | 类型 | 作用 | 是否建议保留 |
|---|---|---|---|
| `README.md` | 项目入口文档 | 面向首次阅读者说明项目定位、快速开始、openKylin 复验、正式提交材料和准确边界。 | 必须保留 |
| `PROJECT_INDEX.md` | 文件索引 | 说明仓库文件结构、文档体系、历史文件建议和维护入口。 | 必须保留 |
| `CHANGELOG.md` | 更新日志 | 记录版本演进、功能补强和交付口径变化。 | 建议保留 |
| `CONTRIBUTING.md` | 贡献指南 | 说明协作、测试和提交规范。 | 建议保留 |
| `SECURITY.md` | 安全说明 | 说明安全问题报告、敏感数据处理和安全边界。 | 建议保留 |
| `CODE_OF_CONDUCT.md` | 社区规范 | 开源协作行为准则。 | 建议保留 |
| `LICENSE` | 许可证 | MIT License。 | 必须保留 |
| `pyproject.toml` | Python 包配置 | 包名、版本、入口命令、开发依赖、pytest 配置。 | 必须保留 |
| `requirements-dev.txt` | 开发依赖 | 本地开发和 CI 可复用的依赖说明。 | 建议保留 |
| `Makefile` | 工程命令 | 聚合常用 lint/test/package 命令。 | 建议保留 |
| `Dockerfile` | 开发环境支撑 | 用于容器化开发或复现实验环境，不是正式竞赛主体。 | 建议保留 |
| `.gitignore`、`.gitattributes`、`.dockerignore` | 工程配置 | 控制 Git、归档和 Docker 构建行为。 | 建议保留 |

## 3. 各文件夹作用说明

| 文件夹 | 作用 | 应放内容 | 不应放内容 |
|---|---|---|---|
| `memguard/` | 主程序源码 | 记忆服务、模型、存储、检索、抽取、安全、API、Web 控制台和 OS 适配代码。 | 临时输出、证据包、个人笔记 |
| `memguard/integrations/` | 真实 Agent 集成 | OpenHands 事件规范化、偏好聚合、上下文注入、runner、bridge 与事件重放。 | OpenHands 原始会话归档、模型文件、密钥 |
| `scripts/` | 辅助脚本 | 安装、验证、打包、证据采集、auditd/BPF/UKUI/Kylin SDK 探针脚本。 | 长期运行数据、私密配置 |
| `native/` | 原生增强源码 | BPF LSM、Peony 右键插件、UKUI 控制中心插件。 | Python 主链路代码 |
| `tests/` | 测试代码 | 单元测试、回归测试、打包边界测试、benchmark 样例构建测试。 | 正式说明文档 |
| `data/` | 数据样例 | demo 输入、benchmark JSONL、示例配置和工具结果。 | 真实隐私数据 |
| `docs/` | 中文文档和证据 | 设计、部署、测试、答辩、交付、证据索引、研究资料和编号文档体系。 | 运行时数据库、缓存 |
| `项目全流程导师资料包/` | 从零学习和答辩训练资料 | 赛题讲解、术语词典、项目结构、运行流程、算法解释、赛题要求映射、测试解释、人工核验、截图清单、功能核验、风险审查和答辩稿。 | 运行时数据、临时截图原图、未核验指标 |
| `dist/` | 生成产物 | 打包脚本生成的竞赛提交包和完整仓库包。 | 不应手工编辑；可重新生成 |
| `.github/` | GitHub 工程化 | CI、Issue/PR 模板。 | 正式竞赛主体内容 |
| `.devcontainer/` | 开发容器 | VS Code/Codespaces 类开发环境配置。 | 正式提交材料 |
| `local_notes/` | 本地笔记 | 个人过程记录，默认不进入正式包。 | 作品必须材料 |

## 4. 核心代码文件说明

| 文件 | 主要功能 | 输入 | 输出 | 关联模块 |
|---|---|---|---|---|
| `memguard/service.py` | 业务编排核心，连接抽取、存储、检索、遗忘、审计和上下文构建。 | 文本、文件、配置、行为、工具结果、OS 事件、查询请求。 | 记忆记录、检索结果、上下文包、审计记录。 | `models.py`、`store/`、`retrieval/`、`forgetting/` |
| `memguard/feedback.py` | 统一生成记忆变化、偏好生效、遗忘验证、敏感保护和通知中心的用户可见反馈结构。 | service、Agent、Peony 和审计事件结果。 | 四类稳定反馈结构、通知分级和托盘提醒策略。 | Web、CLI、UKUI、托盘、Peony |
| `memguard/models.py` | 定义 MemoryRecord、来源、敏感等级、版本、审计和时间有效性结构。 | 业务字段和文本。 | 标准化记忆对象。 | 全项目通用 |
| `memguard/config.py` | 管理本地数据目录、namespace 路径和状态。 | namespace、环境变量。 | 数据库和段文件路径、namespace 状态。 | CLI、service、tests |
| `memguard/cli/memctl.py` | 管理员和桌面集成命令行入口。 | 子命令参数、文件路径、标准输入。 | JSON/文本结果、退出码。 | service、api、web、UKUI、Peony |
| `memguard/cli/memguard.py` | 面向普通用户的交互式入口和一键体验流程。 | 任务、工作区、模型和内置斜杠命令。 | 人类可读的记忆注入、偏好生效、敏感保护和遗忘验证结果。 | service、OpenHands runner |
| `memguard/api/server.py` | Unix socket JSON API。 | 本地 Agent 请求。 | JSON 响应。 | daemon、service |
| `memguard/web/dashboard.py` | 本地 Web 控制台。 | 浏览器请求。 | HTML 页面和 JSON API 响应。 | service、doctor、api |
| `memguard/store/mmap_store.py` | 追加式 mmap 段文件存储。 | 记忆 JSON 负载。 | 偏移量、长度、可复读段数据。 | `sqlite_meta.py` |
| `memguard/store/sqlite_meta.py` | SQLite 元数据、脱敏检索副本、FTS、图谱、审计链。 | 记忆元数据、脱敏后的内容副本、索引字段、审计事件。 | 检索索引、审计校验、图谱关系。 | service、retrieval |
| `memguard/retrieval/hybrid.py` | FTS、hash vector、Kylin 可选 embedding、图谱融合检索。 | 查询文本、top-k、过滤条件。 | 排序结果、解释字段。 | `kylin_sdk.py`、`conflict.py` |
| `memguard/extraction/semantic.py` | 可解释偏好/知识/程序性记忆抽取。 | 输入文本和来源类型。 | 抽取决策、标签、解释。 | service、eval |
| `memguard/sensitivity/detector.py` | 敏感信息识别、阻断和脱敏。 | 文本。 | finding、redacted_text、敏感等级。 | service、forgetting |
| `memguard/forgetting/planner.py` | 自然语言精准遗忘候选规划。 | 遗忘请求、候选记忆。 | 候选列表、匹配理由、风险提示。 | service、tests |
| `memguard/eval/run_eval.py` | 评测入口。 | benchmark 数据、负载规模参数。 | JSON 指标和 Markdown 报告。 | data、service |
| `memguard/eval/agent_interactions.py` | Agent 交互专项评测。 | 200 条交互样例。 | 提取、污染、泄漏、遗忘、跨会话和延迟指标。 | integrations、service |
| `memguard/eval/memory_value.py` | Agent 记忆价值配对指标。 | 三组任务结果与工具轨迹。 | 完成率、工具准确率、轮数、token、错误和重复尝试变化。 | service、Agent benchmark |
| `memguard/eval/real_agent_behavior.py` | 真实 Agent 行为判定与归因。 | OpenHands 上下文、live session 事件和工作区结果。 | 记忆系统层、Agent 行为层、错误分类和聚合指标。 | openhands_runner |
| `memguard/integrations/openhands_runner.py` | 运行真实 OpenHands 会话并注入记忆。 | 任务、namespace、会话、模型配置。 | 真实 Agent 结果、标准事件和上下文证据。 | OpenHands SDK、context_injector |
| `memguard/integrations/adherence.py` | 检查用户偏好是否转化为成功工具调用和回答格式，并解析严格只读恢复意图。 | 三阶段记忆、标准 Agent 事件、当前工具偏好。 | 合规问题、修复提示、安全规范化命令和程序性证据。 | openhands_runner、OpenHands Hook |
| `memguard/integrations/interaction_extractor.py` | 提取用户偏好、纠正和反馈。 | 标准 Agent 事件。 | 带证据和作用域的偏好记忆。 | habit_aggregator、service |
| `memguard/integrations/context_injector.py` | 构建三阶段 Agent 上下文。 | 任务、Agent、会话、阶段、token 预算。 | 规划、工具选择或结果生成上下文。 | retrieval、service |
| `scripts/memguard_agent_bridge.py` | systemd Agent 队列桥。 | 受控 JSON 任务。 | runner 结果与规范化事件。 | `memguard-agent@.service` |

## 5. 配置文件说明

`pyproject.toml` 是 Python 包和测试配置的主文件，定义 `memctl` 与 `memguardd` 两个命令入口。`requirements-dev.txt` 提供开发工具依赖。`scripts/memguardd.service` 和 `scripts/memguardd@.service` 是 systemd 服务单元，用于 openKylin 中的 daemon 与 namespace 模板服务验证。`data/demo/user_config.json` 是配置接入示例，不是系统全局配置。

## 6. 数据文件说明

`data/demo/` 保存演示输入，包括用户配置、工具执行结果、Linux 工作流文本和 BPF LSM 策略示例。`data/benchmarks/` 保存评测 JSONL，包括通用记忆种子、检索、精准遗忘、敏感检测、配置质量、冲突处理、图谱检索、程序性记忆、`agent_interactions.jsonl` 交互抽取专项、`agent_memory_value.jsonl` 的 16 组配对实验，以及基础、扩展和 holdout 合计 28 组真实 Agent 场景定义。不得将可识别个人身份的真实交互、真实凭据或私人日志直接放入该目录。

## 7. 文档文件说明

正式中文文档集中在 `docs/` 根目录、`docs/交付文档/` 和 `项目全流程导师资料包/`。其中 `docs/项目介绍总览.md`、`docs/概要设计说明书.md`、`docs/详细设计说明书.md`、`docs/真实Agent接入使用说明.md`、`docs/真实Agent闭环测试报告.md`、`docs/真实Agent记忆价值评测报告.md`、`docs/测试报告.md`、`docs/赛题符合性说明.md` 面向正式评审；`docs/供应链与SBOM说明.md` 和 `docs/sbom.json` 用于说明依赖分组、核心文件哈希和供应链交付边界；`docs/evidence/latest/` 是通用 openKylin 证据，`docs/evidence/agent_latest/` 是真实 Agent 基础闭环证据，`docs/evidence/memory_value_latest/` 是 16+12 组记忆价值证据；`项目全流程导师资料包/` 面向参赛者本人从零理解、验收和答辩训练。

编号文档 `docs/01_项目简介.md` 到 `docs/17_项目交付说明.md` 是本次补齐的维护型中文文档体系，用于把项目介绍、用户操作、部署运维、开发者说明、架构、代码结构、算法、接口、数据、配置、测试、FAQ、更新日志、历史文件和交付边界统一串起来。`docs/用户使用指南.md`、`docs/Web控制台与Agent工作台说明.md`、`docs/UKUI与托盘反馈链说明.md`、`docs/验证链设计说明.md` 和 `docs/人工演示与录屏脚本.md` 专门说明“记忆产生—生效—风险—遗忘—验证”的产品反馈链。

## 8. 历史文件说明

历史文件包括旧证据包、过程研究材料、打包产物和本地缓存。当前整理不删除这些文件，只建议在长期维护时按以下规则处理：

- `docs/evidence/latest/` 必须保留，代表最新可复验证据。
- `docs/evidence/openkylin_*`、`docs/evidence/kylin_embedding_diag_*` 若存在，属于历史证据，可保留用于回溯，也可在发布前移动到 `archive/evidence/`。
- `docs/research/sources/` 属于论文、网页和 PDF 源材料缓存，适合保留在完整仓库或研究归档中，但不建议放入正式竞赛提交包。
- `dist/` 是生成物，可重新生成；上传或发布前保留最新包即可，旧包可归档。
- `.pytest_cache/`、`.ruff_cache/`、`memguardos.egg-info/` 是本地开发产物，不属于作品主体。

## 9. 最新版本文件说明

最新代码以当前 Git 分支为准。最新正式证据入口是 `docs/evidence/latest/`，多发行版复验证据位于 `docs/evidence/multi_distro_latest/`。最新项目说明优先阅读 `README.md`、`PROJECT_INDEX.md`、`docs/01_项目简介.md` 和 `项目全流程导师资料包/README.md`。正式竞赛提交说明优先阅读 `docs/最终提交说明.md`、`docs/平台摘要.md` 和 `docs/正式提交材料说明.md`。需要制作技术报告或答辩 PPT 图表时，运行 `scripts/generate_plottie_publication_figures.py` 从最新证据 JSON 再生成图包（产物输出到本地 `outputs/`，绘图规范见 `docs/科研期刊绘图规范与图包重构依据.md`）。

## 10. 不建议删除的文件

不建议删除 `memguard/`、`scripts/` 中列入正式脚本的文件、`native/`、`tests/`、`data/benchmarks/`、`docs/evidence/latest/`、正式中文文档、`pyproject.toml`、`LICENSE`、`README.md` 和本索引。删除这些内容会直接影响运行、测试、评审或复验。

## 11. 可归档、可清理或疑似无用文件

以下内容不建议直接删除，但可按发布策略归档：

| 路径 | 类型 | 建议 |
|---|---|---|
| `dist/` 旧压缩包 | 生成产物 | 保留最新竞赛包和完整仓库包，旧包可移动到 `archive/dist/`。 |
| `.pytest_cache/`、`.ruff_cache/` | 本地缓存 | 可由工具自动重建，正式提交包会排除。 |
| `memguardos.egg-info/` | editable install 产物 | 可重建，正式提交包会排除。 |
| `docs/research/sources/` | 研究源材料 | 建议保留在完整仓库或研究归档，不进入正式竞赛包。 |
| `local_notes/` | 个人过程记录 | 保留本地即可，不进入正式竞赛包。 |

## 12. 项目运行入口

常用入口如下：

```bash
python -m memguard.cli.memctl --help
python -m memguard.cli.memctl ingest --text "以后回答 Linux 问题时先给风险提示"
python -m memguard.cli.memctl search "Linux 风险提示"
python -m memguard.cli.memctl build-context "openKylin systemd cgroup"
python -m memguard.cli.memctl web --host 127.0.0.1 --port 8765
python -m memguard.cli.memguard demo
python scripts/run_judge_journey.py --product-flow
python -m memguard.daemon --once
.venv-agent/bin/python scripts/run_openhands_memguard_demo.py --namespace realagent-demo --session-id demo --task "使用 rg 查找 build_agent_context" --max-iterations 8
```

安装后命令入口为 `memctl` 和 `memguardd`。

## 13. 构建、运行、测试流程

```bash
python -m pip install -e ".[dev]"
python -m pytest -q
python -m ruff check .
python scripts/demo_smoke.py
python scripts/check_docs_links.py
python scripts/check_competition_ready.py --output docs/competition_ready.json --markdown docs/竞赛就绪自检报告.md
python scripts/package_submission.py
python scripts/inspect_submission_package.py
python scripts/smoke_submission_archive.py
```

openKylin 环境还应运行 `scripts/openkylin_verify.sh`、`scripts/collect_openkylin_evidence.sh`、Kylin SDK 探针、UKUI/Peony smoke、BPF LSM 安全测试和 `.deb` 安装复验。
真实 Agent 验证还应运行 `scripts/install_agent_extras_openkylin.sh`、`scripts/run_openhands_memguard_demo.py`、`scripts/collect_agent_evidence.sh` 和 `scripts/evaluate_agent_interactions.py`。记忆价值复验使用 `scripts/evaluate_memory_value.py`、`scripts/run_openhands_behavior_suite.py` 和 `scripts/collect_memory_value_evidence.sh`；证据内容发生受控更新后使用 `scripts/refresh_evidence_manifest.py` 原子刷新 SHA256 清单和 manifest。

## 14. 后续维护建议

后续维护应坚持三条原则。第一，所有正式指标都以最新证据包为准，文档不得写旧口径。第二，正式竞赛包和完整工程仓库包继续分离，过程脚本、CI、研究源材料和缓存不应混入正式评审包。第三，新功能进入主线前应补测试、补文档、补 evidence smoke，并更新 `CHANGELOG.md`、`PROJECT_INDEX.md` 和对应编号文档。桌面反馈新增或修改时，应同时验证普通用户文案、管理员详细信息、托盘提醒分级和 URL/hash 跳转，避免“入口存在但不能到达对应页面”。

如果后续升级 OpenHands SDK、增加其他 Agent 适配器、更多 UKUI 插件能力、真实业务日志样例或新的 SDK 后端，应同步更新 `docs/真实Agent接入使用说明.md`、`docs/06_代码结构说明.md`、`docs/07_核心模块说明.md`、`docs/08_算法说明.md` 和 `docs/13_测试说明.md`。
