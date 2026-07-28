# 贡献指南

本文面向 MemGuardOS 的维护者和协作者，说明如何在不破坏安全边界、兼容性与可复现性的前提下修改项目。

## 开发环境

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
python -m pip install -e ".[dev]"
```

Windows PowerShell：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -U pip
python -m pip install -e ".[dev]"
```

## 本地质量门禁

提交前至少运行：

```bash
make ci
```

等价命令：

```bash
python -m compileall -q memguard scripts
python -m ruff check memguard tests scripts
python -m pytest -q
python scripts/check_docs_links.py
```

## 设计原则

1. 默认链路必须离线可复现，不依赖外部 API、GPU 或网络。
2. Kylin embedding、bpftrace/eBPF、外部 LLM 等能力只能作为可选增强，不能替代稳定主路径。
3. 任何影响公开行为、接口或指标口径的改动都要同步更新中文文档。
4. 精准遗忘、敏感检测、审计链相关改动必须有测试。
5. 不提交 `.venv/`、`dist/`、缓存、临时数据库、真实凭据或个人私密数据。

## 分支和提交

- 主分支：`main`
- 功能分支建议命名：`feature/<short-name>`
- 修复分支建议命名：`fix/<short-name>`
- 提交信息尽量说明“为什么改”，例如：

```text
Add quality scoring for config ingestion
```

## Pull Request 检查

提交 PR 前确认：

- [ ] `make ci` 通过
- [ ] 如影响 openKylin，已在 openKylin 虚拟机复跑
- [ ] 中文文档已更新
- [ ] 公开结论均有测试或证据支持
- [ ] 没有提交真实 token、密码、私钥、个人隐私数据
