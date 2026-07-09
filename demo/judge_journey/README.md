# MemGuardOS Judge Journey

Judge Journey 是面向评委复验的演示路线，不使用 scene1/scene2 这类通用命名，而是按 MemGuardOS 的产品叙事组织：记忆捕捉、上下文复用、敏感治理、精准遗忘和 openKylin 原生控制。

每个 YAML 文件都包含 `title`、`goal`、`commands`、`expected_outputs`、`evidence_files`、`judge_talking_points` 和 `fallback_if_environment_missing`。演示脚本只执行仓库内白名单命令和离线检查；openKylin、BPF LSM、UKUI/Peony、Kylin SDK 等增强项缺失时，脚本会把结果标为 graceful fallback，不会伪造实机证据。

运行方式：

```bash
python scripts/run_judge_journey.py --list
python scripts/run_judge_journey.py --step 01_memory_capture_and_preference --evidence-dir docs/evidence/judge_journey_latest
python scripts/run_judge_journey.py --all --evidence-dir docs/evidence/judge_journey_latest
```

建议答辩时按 01 到 05 依次展示。若虚拟机资源紧张，可优先运行 01、02、04，桌面联动部分使用已采集的 openKylin 原生证据说明。
