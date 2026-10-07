# 测试与验证材料

本目录包含完整 Python 测试用例和 `evidence/` 运行结果。证据按 openKylin 系统验证、真实 Agent、检索性能、决赛功能与跨发行版验证归类，并保留原始修订信息和文件哈希。

安装测试依赖后，在源码目录执行：

```bash
cd src
python -m pip install -e ".[dev,security]"
python -m pytest ../tests -q
python -m memguard.eval.action_contract --input data/benchmarks/action_contract.jsonl
```

主要验证材料：

- [决赛实机测试](evidence/finals_20261006/README.md)
- [真实 Agent 运行结果](evidence/real_agent_20261006/README.md)
- [检索性能结果](evidence/performance_latest/README.md)
- [证据导航](evidence/README.md)

历史验证文件保持原始内容，具体环境与代码修订以各证据清单为准。
