# 智能体记忆价值双层评测实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立 16 组确定性配对实验和 12 组 openKylin 真实 OpenHands 会话，量化 MemGuardOS 对任务完成、工具选择、交互轮数、Prompt token、错误重试和遗忘行为的实际贡献。

**Architecture:** 保留现有 `agent_interactions.py` 的抽取质量评测，新建独立的记忆价值指标模块与真实 Agent 行为判定模块。确定性层使用固定事件轨迹验证系统能力和指标计算；真实层复用现有 OpenHands runner，在专用工作区运行本地模型，并把记忆系统层与 Agent 行为层分别归因。

**Tech Stack:** Python 3.12、pytest、JSONL、SQLite、OpenHands SDK 1.28.1、Ollama、Bash、openKylin 2.0 SP2。

---

## 文件职责

| 文件 | 职责 |
|---|---|
| `memguard/eval/memory_value.py` | 指标数据模型、命令规范化、token 统计、配对差值和聚合 |
| `memguard/eval/real_agent_behavior.py` | 从规范化 Agent 事件判定工具、任务、重试、安全与偏好遵循 |
| `data/benchmarks/agent_memory_value.jsonl` | 16 组确定性配对案例 |
| `data/benchmarks/real_agent_behavior.jsonl` | 12 组真实 OpenHands 案例清单 |
| `scripts/evaluate_memory_value.py` | 执行确定性实验并输出 JSON/Markdown |
| `scripts/run_openhands_behavior_suite.py` | 执行真实 Agent 会话并保存逐案例证据 |
| `scripts/collect_memory_value_evidence.sh` | openKylin 证据采集、清单和 SHA256 |
| `tests/test_memory_value.py` | 指标算法和 16 组案例回归测试 |
| `tests/test_real_agent_behavior.py` | 真实事件判定与执行器隔离测试 |
| `docs/真实Agent记忆价值评测报告.md` | 面向评委的中文指标解释、结果和边界 |

### Task 1: 建立记忆价值指标核心

**Files:**
- Create: `memguard/eval/memory_value.py`
- Create: `tests/test_memory_value.py`

- [ ] **Step 1: 编写指标计算失败测试**

```python
from memguard.eval.memory_value import evaluate_paired_case


def test_paired_case_reports_memory_value_without_using_no_memory_as_token_baseline():
    result = evaluate_paired_case(
        {
            "case_id": "value-1",
            "groups": {
                "no_memory": {
                    "task_completed": False,
                    "tool_calls": ["grep target src"],
                    "expected_tool": "rg",
                    "interaction_turns": 3,
                    "prompt_tokens": 40,
                },
                "full_history": {
                    "task_completed": True,
                    "tool_calls": ["rg target src"],
                    "expected_tool": "rg",
                    "interaction_turns": 2,
                    "prompt_tokens": 500,
                },
                "memguard": {
                    "task_completed": True,
                    "tool_calls": ["rg target src"],
                    "expected_tool": "rg",
                    "interaction_turns": 1,
                    "prompt_tokens": 120,
                },
            },
        }
    )
    assert result["deltas"]["completion_vs_no_memory"] == 1
    assert result["deltas"]["tool_accuracy_vs_no_memory"] == 1
    assert result["deltas"]["interaction_turns_vs_no_memory"] == -2
    assert result["deltas"]["prompt_tokens_vs_full_history"] == -380
    assert result["deltas"]["prompt_token_reduction_ratio"] == 0.76
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `python -m pytest tests/test_memory_value.py::test_paired_case_reports_memory_value_without_using_no_memory_as_token_baseline -q`  
Expected: FAIL，提示 `memguard.eval.memory_value` 不存在。

- [ ] **Step 3: 实现统一指标与命令统计**

实现以下公开函数：

```python
def normalize_command(command: str) -> str: ...
def summarize_group(group: dict[str, object]) -> dict[str, object]: ...
def evaluate_paired_case(case: dict[str, object]) -> dict[str, object]: ...
def aggregate_paired_results(results: list[dict[str, object]]) -> dict[str, object]: ...
```

`summarize_group` 必须输出：

```python
{
    "task_completed": bool,
    "first_tool_correct": bool,
    "interaction_turns": int,
    "tool_call_count": int,
    "failed_command_count": int,
    "repeated_command_count": int,
    "blocked_high_risk_count": int,
    "prompt_tokens": int,
}
```

重复命令按去除连续空白后的完整命令字符串计算；安全策略正确拒绝不计入普通失败命令。

- [ ] **Step 4: 补充边界测试**

覆盖：

- 完整历史 token 为零时减少率返回 `None`；
- 未完成任务不能因轮数更少被标记为效率提升；
- 相同命令空白差异仍识别为重复；
- 非零退出码计入失败；
- 安全拒绝只计入 `blocked_high_risk_count`。

- [ ] **Step 5: 运行专项测试**

Run: `python -m pytest tests/test_memory_value.py -q`  
Expected: PASS。

- [ ] **Step 6: 提交**

```bash
git add memguard/eval/memory_value.py tests/test_memory_value.py
git commit -m "feat: add agent memory value metrics"
```

### Task 2: 建立 16 组确定性配对案例

**Files:**
- Create: `data/benchmarks/agent_memory_value.jsonl`
- Create: `scripts/evaluate_memory_value.py`
- Modify: `tests/test_memory_value.py`

- [ ] **Step 1: 编写数据完整性失败测试**

```python
def test_memory_value_dataset_has_sixteen_balanced_cases():
    cases = load_jsonl(ROOT / "data/benchmarks/agent_memory_value.jsonl")
    assert len(cases) == 16
    assert Counter(case["category"] for case in cases) == {
        "user_correction": 2,
        "implicit_habit": 2,
        "conflict_override": 2,
        "repair_experience": 2,
        "risk_confirmation": 2,
        "cross_session": 2,
        "precise_forgetting": 2,
        "role_pollution": 2,
    }
    assert all(set(case["groups"]) == {"no_memory", "full_history", "memguard"} for case in cases)
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `python -m pytest tests/test_memory_value.py::test_memory_value_dataset_has_sixteen_balanced_cases -q`  
Expected: FAIL，数据文件不存在。

- [ ] **Step 3: 创建 16 组数据**

每行包含：

```json
{
  "case_id": "D01",
  "category": "user_correction",
  "title": "用户将代码搜索工具纠正为 rg",
  "expected_tool": "rg",
  "history_events": [],
  "groups": {
    "no_memory": {},
    "full_history": {},
    "memguard": {}
  },
  "forget_filters": {},
  "assertions": {}
}
```

16 组必须严格对应设计文档 D01-D16，且包含中文场景说明、固定事件、可执行断言和预期行为。工具失败案例使用真实可解释的退出码；高风险案例使用 `/tmp/memguardos-value-workspace` 下测试文件。

- [ ] **Step 4: 实现确定性执行脚本**

`scripts/evaluate_memory_value.py` 应：

1. 加载 JSONL；
2. 为每个案例创建隔离 `MemGuardService`；
3. 写入历史事件；
4. 构建三个对照组；
5. 调用 `evaluate_paired_case`；
6. 输出 `metrics.json`、`cases.json`、`report.md`；
7. 有任何断言失败时返回非零退出码。

- [ ] **Step 5: 测试输出可追溯性**

断言聚合指标可由 `cases.json` 重新计算，且报告包含六类指标、所有失败和数据边界。

- [ ] **Step 6: 运行 16 组实验**

Run: `python scripts/evaluate_memory_value.py --output-dir outputs/memory_value_deterministic`  
Expected: 16 个案例全部产生结果，进程返回 0。

- [ ] **Step 7: 提交**

```bash
git add data/benchmarks/agent_memory_value.jsonl scripts/evaluate_memory_value.py tests/test_memory_value.py
git commit -m "test: add paired agent memory value benchmark"
```

### Task 3: 建立真实 Agent 行为判定器

**Files:**
- Create: `memguard/eval/real_agent_behavior.py`
- Create: `tests/test_real_agent_behavior.py`

- [ ] **Step 1: 编写事件判定失败测试**

```python
from memguard.eval.real_agent_behavior import evaluate_real_agent_run


def test_real_run_separates_memory_system_success_from_agent_failure():
    result = evaluate_real_agent_run(
        case={"expected_tool": "rg", "completion": {"output_contains": "service.py"}},
        context={"selected_topics": ["code_search_tool"]},
        events=[
            {"event_type": "tool_call", "tool_name": "terminal",
             "tool_args": {"command": "rg target missing"}},
            {"event_type": "tool_result", "exit_code": 2, "tool_result": "missing"},
        ],
    )
    assert result["memory_system"]["context_selected"] is True
    assert result["agent_behavior"]["first_tool_correct"] is True
    assert result["agent_behavior"]["task_completed"] is False
    assert result["status"] == "memory_pass_agent_fail"
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `python -m pytest tests/test_real_agent_behavior.py -q`  
Expected: FAIL，模块不存在。

- [ ] **Step 3: 实现事件判定**

实现：

```python
def evaluate_real_agent_run(
    case: dict[str, object],
    context: dict[str, object],
    events: list[dict[str, object]],
    workspace: Path | None = None,
) -> dict[str, object]: ...
```

支持断言：

- `output_contains`
- `output_regex`
- `file_exists`
- `file_contains`
- `expected_exit_code`
- `expected_tool`
- `forbidden_tools`
- `expected_topics`
- `absent_topics`
- `retained_topics`
- `high_risk_blocked`

输出 `memory_system`、`agent_behavior`、`metrics`、`failures` 和四态结果：

- `pass`
- `memory_pass_agent_fail`
- `memory_fail_agent_pass`
- `fail`

- [ ] **Step 4: 测试错误分类**

覆盖超时、最大迭代、工具错误、安全拒绝、无工具调用、重复命令、Agent 自述污染和工具输出污染。

- [ ] **Step 5: 运行专项测试并提交**

Run: `python -m pytest tests/test_real_agent_behavior.py -q`  
Expected: PASS。

```bash
git add memguard/eval/real_agent_behavior.py tests/test_real_agent_behavior.py
git commit -m "feat: add real agent behavior evaluator"
```

### Task 4: 建立 12 组真实 OpenHands 场景与运行器

**Files:**
- Create: `data/benchmarks/real_agent_behavior.jsonl`
- Create: `scripts/run_openhands_behavior_suite.py`
- Modify: `tests/test_real_agent_behavior.py`
- Modify: `memguard/integrations/openhands_runner.py`

- [ ] **Step 1: 编写场景清单失败测试**

断言数据集恰好包含 R01-R12，覆盖纠正、隐式习惯、冲突、修复经验、风险确认、跨会话、遗忘和污染防护。

- [ ] **Step 2: 为 runner 增加可控评测元数据**

先写测试，要求 `OpenHandsMemoryRunner.run()` 额外返回：

```python
{
    "context": {
        "trace_id": "...",
        "stage_budgets": {...},
        "selected_memory_ids": {...},
        "selected_topics": {...}
    },
    "duration_ms": 0.0
}
```

不得更改原始用户任务文本；不得把完整敏感上下文写入汇总结果。

- [ ] **Step 3: 创建 12 组真实场景**

每组包含：

- `case_id`
- `title`
- `setup_events`
- `task`
- `model`
- `tool_profile`
- `max_iterations`
- `workspace_fixture`
- `memory_assertions`
- `behavior_assertions`
- `forget_action`
- `safety_mode`

风险任务仅允许测试工作区和安全 Hook，不运行真实破坏命令。

- [ ] **Step 4: 实现真实套件运行器**

脚本参数：

```text
--dataset
--output-dir
--namespace
--model
--base-url
--case-id
--resume
```

运行规则：

1. 检查 OpenHands、Ollama 和模型；
2. 为案例创建独立 namespace、workspace、state；
3. 保存执行前清单；
4. 写入 setup 交互；
5. 运行 OpenHands；
6. 导出规范化事件和上下文；
7. 执行客观断言；
8. 保存执行后清单；
9. 聚合但不删除失败。

`--resume` 只跳过已有完整结果的案例，不允许选择性覆盖失败。

- [ ] **Step 5: 使用 fake conversation 完成无模型测试**

测试运行器能够：

- 保留失败；
- 分开系统层和行为层；
- 计算重复命令；
- 输出逐案例 JSON；
- 不访问真实用户主目录。

- [ ] **Step 6: 运行专项测试并提交**

Run: `python -m pytest tests/test_agent_closed_loop.py tests/test_real_agent_behavior.py -q`  
Expected: PASS。

```bash
git add data/benchmarks/real_agent_behavior.jsonl scripts/run_openhands_behavior_suite.py memguard/integrations/openhands_runner.py tests/test_real_agent_behavior.py
git commit -m "feat: add openhands memory value behavior suite"
```

### Task 5: 建立 openKylin 证据采集与质量门禁

**Files:**
- Create: `scripts/collect_memory_value_evidence.sh`
- Modify: `scripts/check_competition_ready.py`
- Modify: `scripts/inspect_submission_package.py`
- Modify: `tests/test_packaging.py`

- [ ] **Step 1: 编写门禁失败测试**

构造缺少 `deterministic_metrics.json`、真实案例不足 12、SHA256 不完整和存在未归因失败的证据目录，断言质量门禁失败并给出具体路径。

- [ ] **Step 2: 实现证据采集脚本**

脚本依次采集：

```bash
python --version
python -m pip show openhands-sdk openhands-tools
ollama --version
ollama list
python scripts/evaluate_memory_value.py
python scripts/run_openhands_behavior_suite.py
python -m pytest -q
python scripts/verify_audit.py
```

随后生成 `manifest.json` 和 `SHA256SUMS.txt`。脚本使用临时目录生成，全部成功后再原子替换 `docs/evidence/memory_value_latest/`。

- [ ] **Step 3: 扩展竞赛门禁**

检查：

- 16 个确定性案例；
- 12 个真实案例；
- 系统层和行为层字段完整；
- 失败有分类；
- token 基线为完整历史；
- 遗忘与污染防护关键案例存在；
- SHA256 可复验。

- [ ] **Step 4: 运行打包测试**

Run: `python -m pytest tests/test_packaging.py -q`  
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add scripts/collect_memory_value_evidence.sh scripts/check_competition_ready.py scripts/inspect_submission_package.py tests/test_packaging.py
git commit -m "build: gate agent memory value evidence"
```

### Task 6: 更新中文报告、符合性核查和证据索引

**Files:**
- Create: `docs/真实Agent记忆价值评测报告.md`
- Modify: `docs/真实Agent闭环测试报告.md`
- Modify: `docs/测试报告.md`
- Modify: `docs/赛题要求逐句符合性核查.md`
- Modify: `docs/评委证据包索引.md`
- Modify: `docs/最终提交说明.md`
- Modify: `README.md`

- [ ] **Step 1: 从实际 JSON 生成报告数据**

报告必须引用确定性和真实案例指标文件，不手填尚未运行的数据。包含：

- 六类记忆价值指标；
- 三类对照口径；
- 逐案例失败与归因；
- 本地模型边界；
- token 估算口径；
- 遗忘前后行为；
- 复现命令。

- [ ] **Step 2: 同步赛题符合性**

明确说明：

- 现有 OpenHands 是第三方现有 Agent；
- 用户操作习惯来源是用户与 Agent 交互；
- shell、auditd 和 OS 事件属于补充多源信息；
- 记忆价值指标证明 Agent 行为变化；
- 不把确定性模拟结果冒充真实 Agent 结果。

- [ ] **Step 3: 检查文档一致性**

Run:

```powershell
rg -n "141 passed|142 passed|200 条|记忆价值|Prompt token|OpenHands" README.md docs
```

逐项核对所有测试计数、案例数和证据路径与实际文件一致。

- [ ] **Step 4: 提交**

```bash
git add README.md docs
git commit -m "docs: report real agent memory value evidence"
```

### Task 7: 本地全量回归与静态检查

**Files:**
- Modify only when tests expose defects.

- [ ] **Step 1: 运行新增测试**

Run:

```bash
python -m pytest tests/test_memory_value.py tests/test_real_agent_behavior.py -q
```

Expected: 全部通过。

- [ ] **Step 2: 运行全量测试**

Run: `python -m pytest -q`  
Expected: 全部通过，Linux 专属测试在 Windows 上仅按既有规则跳过。

- [ ] **Step 3: 运行静态检查**

Run:

```bash
python -m ruff check memguard tests scripts
python -m compileall -q memguard scripts
git diff --check
```

Expected: 无错误。

- [ ] **Step 4: 运行确定性评测和门禁**

Run:

```bash
python scripts/evaluate_memory_value.py
python scripts/check_competition_ready.py
```

Expected: 16 组确定性实验通过；门禁在真实 openKylin 证据采集前明确提示缺少真实证据，而不是误报完成。

- [ ] **Step 5: 提交修复**

仅在回归暴露缺陷时提交：

```bash
git add <实际修复文件>
git commit -m "fix: harden agent memory value evaluation"
```

### Task 8: openKylin 真实运行、证据固化与最终打包

**Files:**
- Generate: `docs/evidence/memory_value_latest/`
- Generate: `dist/memguardos_competition_submission_*.zip`
- Modify documentation only when actual results require correction.

- [ ] **Step 1: 同步分支到 openKylin**

通过既有 SSH 通道同步代码，不覆盖虚拟机中的私有环境配置。确认：

```bash
git rev-parse HEAD
python --version
systemctl --user is-active memguard-agent@luo.service || true
```

- [ ] **Step 2: 验证本地模型环境**

Run:

```bash
ollama --version
ollama list
python -m pip show openhands-sdk openhands-tools
```

Expected: OpenHands 1.28.1 与已下载的 1.5B/1.7B 模型可用。

- [ ] **Step 3: 运行 12 组真实会话**

Run:

```bash
bash scripts/collect_memory_value_evidence.sh
```

预计运行 1 至 3 小时。不得因结果不理想删除失败案例。

- [ ] **Step 4: 复核真实结果**

检查：

- 12 个案例均有结果；
- 失败有明确归因；
- 无敏感明文；
- 真实工具调用和退出码存在；
- 精准遗忘后新会话证据完整；
- SHA256 校验通过。

- [ ] **Step 5: 运行 openKylin 全量门禁**

Run:

```bash
python -m pytest -q
python scripts/check_competition_ready.py
python scripts/verify_audit.py
```

Expected: 全部通过。

- [ ] **Step 6: 根据实测更新报告**

只填入实际生成的指标，不预设提升幅度。若某项未提升，保留数值并给出检索、注入、模型、工具或任务设计层面的归因。

- [ ] **Step 7: 构建并复验提交包**

Run:

```bash
python scripts/package_submission.py
python scripts/inspect_submission_package.py
python scripts/smoke_submission_archive.py
```

Expected: 提交包检查和解压 smoke 全部通过。

- [ ] **Step 8: 最终提交**

```bash
git add docs README.md
git commit -m "evidence: add openkylin agent memory value results"
git status --short
```

工作区应保持干净，最终回复记录 commit、测试数、真实案例结果、证据路径、提交包路径和 SHA256。
