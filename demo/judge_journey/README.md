# MemGuardOS Judge Journey

Judge Journey 是面向评委复验的演示路线，不使用 scene1/scene2 这类通用命名，而是按 MemGuardOS 的产品叙事组织：记忆捕捉、上下文复用、敏感治理、精准遗忘和 openKylin 原生控制。

每个路线文件包含目标、命令参数列表和可复核证据路径。文件使用 YAML 兼容的 JSON 格式，运行器只依赖 Python 标准库。验证在隔离测试数据中执行，openKylin 系统安装与原生组件的既有验证结果位于根目录 `tests/evidence/`。

从仓库根目录先进入 `src`，再运行：

```bash
python scripts/run_judge_journey.py --list
python scripts/run_judge_journey.py --step 01_memory_capture_and_preference --evidence-dir output/judge_journey_latest
python scripts/run_judge_journey.py --all --evidence-dir output/judge_journey_latest
```

建议答辩时按 01 到 05 依次展示。若虚拟机资源紧张，可优先运行 01、02、04，桌面联动部分使用已采集的 openKylin 原生证据说明。
