# MemGuardOS 软件物料清单与供应链说明

本文档面向提交审核、开源归档和后续维护，说明 MemGuardOS 的核心软件物料、依赖分组、哈希校验和供应链风险控制方式。SBOM JSON 机器可读清单位于 `docs/sbom.json`。

## 基本信息

| 项目 | 内容 |
|---|---|
| 产品名称 | memguardos |
| 版本 | 0.1.0 |
| Python 版本要求 | >=3.9 |
| 许可证 | MIT License |
| 生成时间 | 2026-07-01T09:06:18+00:00 |

## 依赖分组

### 运行时依赖

- 默认运行时依赖为空，核心 CLI、daemon、检索、审计和精准遗忘链路只依赖 Python 标准库。

### 开发与测试依赖

- `pytest>=8`
- `pytest-cov>=5`
- `ruff>=0.6`

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
| `pyproject.toml` | 是 | 770 | `9ec2eb185c74a2c809513c09c1c1290cabac5e244dd13581e6614a4ba3e367d4` |
| `LICENSE` | 是 | 1080 | `3319059b1763624558499140d670dd98b2260aba3654bd19af2838b76c4d4261` |
| `README.md` | 是 | 23635 | `58f33f6198923421057c12a1a56b38bab5e2897a3b55876528af0c5f096867c0` |
| `memguard/service.py` | 是 | 98559 | `4822ab7045e5f32a5d887df96a7652e87e0f9dc6cb6d93866f7dda4112f5f38f` |
| `memguard/api/server.py` | 是 | 16504 | `dbf0b25d1f7162ea35d5071bdde0779be0538a3e195bba7a27a0ce962be4a3f2` |
| `memguard/web/dashboard.py` | 是 | 51882 | `0e184ca79a8be95db0b506ec9941f31ad6494bf346a787176f8808e5687c6fc9` |
| `memguard/integrations/openhands_adapter.py` | 是 | 11446 | `0454ee0507c91d14f16aea64737ccf7ebd2ad8a3ff1523fa630df47d0346c84e` |
| `memguard/integrations/openhands_runner.py` | 是 | 27855 | `5f3d17bc4fbc80fd6269c4a3b9caab5329a1a9e811b513a3cb3b3fd54e377ee8` |
| `memguard/retrieval/hybrid.py` | 是 | 8499 | `ff6a2d3720b05e844c567ddb551b3ebd8ee3878d83be1907f350b6e79dac77e7` |
| `memguard/retrieval/kylin_sdk.py` | 是 | 23899 | `53ca4e83298bb5f5b2cc5c0a8c8644f5c477cb1eab498c6fc70974e4ac3648b5` |
| `memguard/store/mmap_store.py` | 是 | 5100 | `af8284a3a6646be832e81424476fd2b623d1aecfb3eb7295e5e81938711c7236` |
| `memguard/store/sqlite_meta.py` | 是 | 40195 | `1a0eb182fa448c27c4405d430c75db7897df89a4f5403daf22424f77c2899cc4` |
| `memguard/extraction/semantic.py` | 是 | 11462 | `55e6b54551e547e23b8676e8aa7a8d37c4ba9801325ec139584b39f0c9c0a54b` |
| `memguard/forgetting/planner.py` | 是 | 6140 | `1d4f783f92c28c25f98404980a475f06902ebeeebdd2e199c657cf75ea51de67` |
| `memguard/eval/memory_value.py` | 是 | 14093 | `916c668eb8659aef42f17063a290e4e88f72f5dd0983d078b074e2207c3c779f` |
| `memguard/eval/real_agent_behavior.py` | 是 | 18099 | `8487de6ca4a2a6ed2759c3fcbb9a62f63bf9d2d33558ee3678566acc640e43f9` |
| `scripts/run_openhands_behavior_suite.py` | 是 | 15863 | `5498fa4a08399c82fb0eed1485e457e578bf724d70739e9a72a8b4570a2f2720` |
| `scripts/run_security_scan.py` | 是 | 15385 | `07bb3899c2fb2dcbe13aa11293ae657e65bfc8f4b7cfc21ac32a0e8d13cbccd9` |
| `scripts/collect_openkylin_evidence.sh` | 是 | 21284 | `b22b5f62d7c7332c3941ab1a88a8b5279b1eae7b30f975bf72e876e2a040e447` |
| `scripts/collect_memory_value_evidence.sh` | 是 | 5453 | `19653b5d5ee75b5b58e5e5d49326b3fdac13aaf6bf8d4677d5f1a6e3bc961599` |
| `scripts/build_deb.sh` | 是 | 5878 | `d2745d89d0a7e1ab3c88868c945a8b7ee3dcb9fd2dfb550ae5d0b1c984848130` |
| `native/bpf_lsm/memguard_lsm.bpf.c` | 是 | 2969 | `2066908bb8f1dd3521256030b3bc13043a9a11865d47cee93f3cf4d80dc189a1` |
| `native/ukui/peony-memguard/memguard_peony_plugin.cpp` | 是 | 5162 | `e2bdbde9f293720d874855f01507d7b65c5973d72591d3ea50858b700ea8d239` |
| `native/ukui/ukcc-memguard/memguard_ukcc_plugin.cpp` | 是 | 8610 | `38a91f0fd7ef26edb3df6d33220c86e9cf4c6024cb0bed563f857271a85aa137` |
| `data/benchmarks/agent_memory_value.jsonl` | 是 | 17182 | `0ec615d69930561e7886bc47f9aca388f3cf0848291078e6ef0c74ab05e877b2` |
| `data/benchmarks/real_agent_behavior.jsonl` | 是 | 16653 | `96f7096113a4f72d584e29aa9c8946e32a0e7374c8d55a68f1eeaa7aa6178df1` |
| `data/benchmarks/real_agent_behavior_holdout.jsonl` | 是 | 8108 | `94a985700ee93a69596598c81919024e1b784bb2e51fc27151325a330c314d63` |

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
