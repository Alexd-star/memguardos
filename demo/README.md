# 可验证演示交付物

## openKylin 五包安装

`packages/` 包含已完成 openKylin 实机安装验证的五个 Debian 包、原始包清单及 SHA-256 校验文件。安装包版本为 `1.0.1-9+finals20261006`，包内源码修订见 [包清单](packages/memguardos_debian_packages.json)；当前源码在 `../src/`，可使用源码构建脚本重新构建。

在 openKylin 2.0 SP2 x86_64 的仓库根目录执行：

```bash
cd demo/packages
sha256sum -c SHA256SUMS.txt
sudo apt install ./memguardos-core_*.deb ./memguardos-ukui_*.deb ./memguardos-agent-bridge_*.deb ./memguardos-doc_*.deb ./memguardos_[0-9]*_all.deb
memguard status
systemctl status memguardd --no-pager
```

## 核心流程复现

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
