# MemGuardOS 源代码

项目介绍及正式交付文件见 [项目根目录 README](../README.md)。

本目录保留核心 Python 模块、原生组件、配置、构建和安装脚本的相对布局。所有源码命令均在本目录执行：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev,security]"
python -m pytest ../tests -q
bash scripts/build_ukui_plugins.sh
bash scripts/install_deb_package_set.sh
```

`memguard/` 为核心实现，`native/` 为 openKylin 原生组件，`packaging/` 为安装资产，`scripts/` 为构建、运行与验证脚本，`data/` 为复现数据，`docs/` 为配套技术手册。测试用例位于根目录 `tests/`，正式说明书位于根目录 `docs/`，决赛 PPT 和视频位于根目录 `presentation/`。
