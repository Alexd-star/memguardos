# 可验证演示交付物

从根目录进入 `src`，安装项目后即可复现无需外部模型的核心闭环：

```bash
cd src
python -m pip install -e .
memguard demo
python scripts/demo_smoke.py
```

该流程在临时空间中执行多源写入、混合检索、Agent 上下文、敏感保护、遗忘预览和验证，不会修改日常记忆空间。

真实 Agent 演示使用 `memguard-agent-setup` 配置运行环境，然后以 `memguard-agent-showcase /path/to/workspace` 启动。模型接口由使用者配置。

`judge_journey/` 保留各功能的演示步骤，演示样例位于 `../src/data/demo/`，已采集运行结果见 `../tests/evidence/`，视频与 PPT 见 `../presentation/`。
