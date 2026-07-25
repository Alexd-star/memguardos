# MemGuardOS 软件物料清单与供应链说明

本文档面向提交审核、开源归档和后续维护，说明 MemGuardOS 的核心软件物料、依赖分组、哈希校验和供应链风险控制方式。SBOM JSON 机器可读清单位于 `docs/sbom.json`。

## 基本信息

| 项目 | 内容 |
|---|---|
| 产品名称 | memguardos |
| 版本 | 0.1.0 |
| Python 版本要求 | >=3.9 |
| 许可证 | MIT License |
| 生成时间 | 2026-07-10T13:42:08+00:00 |

## 依赖分组

### 运行时依赖

- 默认运行时依赖为空，核心 CLI、daemon、检索、审计和精准遗忘链路只依赖 Python 标准库。

### 开发与测试依赖

- `pytest>=8`
- `pytest-cov>=5`
- `ruff>=0.6,<0.16`（固定兼容上界，避免静态规则集升级导致不可复现的门禁漂移）

### OpenHands 适配依赖

- `openhands-sdk==1.28.1`
- `openhands-tools==1.28.1`
- `browser-use==0.11.13`

### 安全扫描依赖

- `bandit>=1.7`
- `pip-audit>=2.7`

## 核心交付物哈希

| 路径 | 是否存在 | 字节数 | SHA-256 |
|---|---:|---:|---|
| `pyproject.toml` | 是 | 810 | `bb539eea1d072c95735076ed6558c60d8a7eeed50eebd004ecac146aa112d064` |
| `LICENSE` | 是 | 1080 | `3319059b1763624558499140d670dd98b2260aba3654bd19af2838b76c4d4261` |
| `README.md` | 是 | 27805 | `0a62d4ea590a6dc83be655078a11ea0de24d824cb0bef502dab47970b4852a31` |
| `memguard/service.py` | 是 | 133892 | `87638f4db43588c5e633ba74c3b2b36a75a57f27c5fe67e5b29ab9fed10a3987` |
| `memguard/api/server.py` | 是 | 20124 | `c21fd7434c29144d5fc4ed46b4e6dd6b4ddd2a0429954f782a835cb9dd646c07` |
| `memguard/web/dashboard.py` | 是 | 143179 | `7efa5b60f755d482548686566382d87ef84ab19fdaac6f5886dd5170c28b2699` |
| `memguard/integrations/openhands_adapter.py` | 是 | 11446 | `0454ee0507c91d14f16aea64737ccf7ebd2ad8a3ff1523fa630df47d0346c84e` |
| `memguard/integrations/openhands_runner.py` | 是 | 35940 | `d7b77b07930e5e161a53f9c16c620bd21b8a6f47a5ac63469c706de3a0f8470b` |
| `memguard/retrieval/hybrid.py` | 是 | 22971 | `0931971816939599b22e4c8be8a1b12c78d10acec766a6b3be3dc8db45782b44` |
| `memguard/retrieval/kylin_sdk.py` | 是 | 26935 | `77427a0102250d5d362192627e53ed35b1c04f41152fd415edc99ffc65ada6d6` |
| `memguard/store/mmap_store.py` | 是 | 8754 | `5d700fa28d079a7376d6fcef04a2166f960d01588bbccfcece3d7883292858d6` |
| `memguard/store/sqlite_meta.py` | 是 | 45528 | `e569cb8507385b85f838691152b7116aafa37611c8a5e095b5cd6dd09d0e54d1` |
| `memguard/extraction/semantic.py` | 是 | 11777 | `b99e3ff27c73ac32f0d50f9ec1cb6335a8604155046d1d35db57eb7ff27eef7d` |
| `memguard/forgetting/planner.py` | 是 | 12723 | `37844d880f9a3a3b8a1844df55de822cb6496efa952c53db682f15a86646a9d1` |
| `memguard/eval/memory_value.py` | 是 | 14093 | `916c668eb8659aef42f17063a290e4e88f72f5dd0983d078b074e2207c3c779f` |
| `memguard/eval/real_agent_behavior.py` | 是 | 18628 | `d106a58e2d03b272336471e7c5f10bde9b4c6d62456e4a245457eaa8f1011651` |
| `scripts/run_openhands_behavior_suite.py` | 是 | 15863 | `5498fa4a08399c82fb0eed1485e457e578bf724d70739e9a72a8b4570a2f2720` |
| `scripts/run_security_scan.py` | 是 | 15934 | `fc93b75c608263d66388c4682371b689b0b373c00b16272b35ef1a3ce3fc647b` |
| `scripts/collect_openkylin_evidence.sh` | 是 | 21284 | `b22b5f62d7c7332c3941ab1a88a8b5279b1eae7b30f975bf72e876e2a040e447` |
| `scripts/collect_memory_value_evidence.sh` | 是 | 5453 | `19653b5d5ee75b5b58e5e5d49326b3fdac13aaf6bf8d4677d5f1a6e3bc961599` |
| `scripts/build_deb.sh` | 是 | 259 | `49658e10f618c270960c39a6eca27f21dbd2acd926673a1882135b2bfdc2d7db` |
| `native/bpf_lsm/memguard_lsm.bpf.c` | 是 | 2969 | `2066908bb8f1dd3521256030b3bc13043a9a11865d47cee93f3cf4d80dc189a1` |
| `native/ukui/peony-memguard/memguard_peony_plugin.cpp` | 是 | 31648 | `e2540e8e04aeed598bc7321a45c4b9563eac7656b1c3313c64f725d3d68a4195` |
| `native/ukui/ukcc-memguard/memguard_ukcc_plugin.cpp` | 是 | 92277 | `38dbd79949679048ef5ccb3a487fdef0ea575759fbb459f3c69375aeb3371d64` |
| `data/benchmarks/agent_memory_value.jsonl` | 是 | 17182 | `0ec615d69930561e7886bc47f9aca388f3cf0848291078e6ef0c74ab05e877b2` |
| `data/benchmarks/real_agent_behavior.jsonl` | 是 | 16653 | `96f7096113a4f72d584e29aa9c8946e32a0e7374c8d55a68f1eeaa7aa6178df1` |
| `data/benchmarks/real_agent_behavior_holdout.jsonl` | 是 | 8194 | `f6b2a890ade32f956972eb6e7952ff95f51af2c1071b9f9b9c120b513bcc12b2` |

## 供应链控制说明

- 默认运行时依赖为空，核心功能仅依赖 Python 标准库，降低供应链攻击面。
- OpenHands 真实 Agent 适配依赖被放入可选依赖组 agent-openhands，不进入默认安装路径。
- Bandit 与 pip-audit 被放入可选依赖组 security，用于发布前源码安全扫描和依赖漏洞审计。
- 交付包同时保留 manifest、SHA-256 清单、审计链和 openKylin 证据，用于复验构建与运行结果。
- SBOM 记录核心源码、脚本、原生增强和 benchmark 数据的哈希，便于提交前后比对。

## 复验方式

1. 运行 `python scripts/generate_sbom.py` 重新生成 `docs/sbom.json` 和本文档。
2. 运行 `python scripts/check_competition_ready.py` 检查 SBOM、证据包、文档和源码门禁。
3. 运行 `python scripts/package_submission.py` 后，再运行 `python scripts/inspect_submission_package.py` 与 `python scripts/smoke_submission_archive.py` 检查提交包。
