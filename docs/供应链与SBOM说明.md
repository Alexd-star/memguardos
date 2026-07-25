# MemGuardOS 软件物料清单与供应链说明

本文档面向提交审核、开源归档和后续维护，说明 MemGuardOS 的核心软件物料、依赖分组、哈希校验和供应链风险控制方式。SBOM JSON 机器可读清单位于 `docs/sbom.json`。

## 基本信息

| 项目 | 内容 |
|---|---|
| 产品名称 | memguardos |
| 版本 | 0.1.0 |
| Python 版本要求 | >=3.10 |
| 许可证 | MIT License |
| 生成时间 | 2026-07-25T23:15:07+00:00 |

## 依赖分组

### 运行时依赖

- 默认运行时依赖为空，核心 CLI、daemon、检索、审计和精准遗忘链路只依赖 Python 标准库。

### 开发与测试依赖

- `pytest>=8`
- `pytest-cov>=5`
- `ruff>=0.6,<0.16`

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
| `pyproject.toml` | 是 | 817 | `aef749ac94189d8477db1ea6327c7877d703845a7356247a2c35a319a90b698c` |
| `LICENSE` | 是 | 1080 | `3319059b1763624558499140d670dd98b2260aba3654bd19af2838b76c4d4261` |
| `README.md` | 是 | 28158 | `c3e023f47aa6e58624d0f102279719c14b9a6a0428405b2525aca7af000dc607` |
| `memguard/service.py` | 是 | 157475 | `c71e69bafa6c101494dafbfd2e0fcde666d774be4dae39e20e38481bbecb8503` |
| `memguard/api/server.py` | 是 | 20377 | `707a298f59110b649645dc8dec695cc59c715a1c3b1b1676ecd5921ab3a5ab50` |
| `memguard/web/dashboard.py` | 是 | 173534 | `a134c3f3100542887cca1c05b26305d484e1ca64a2ff0342b601783e9354140e` |
| `memguard/integrations/openhands_adapter.py` | 是 | 11446 | `0454ee0507c91d14f16aea64737ccf7ebd2ad8a3ff1523fa630df47d0346c84e` |
| `memguard/integrations/openhands_runner.py` | 是 | 35940 | `d7b77b07930e5e161a53f9c16c620bd21b8a6f47a5ac63469c706de3a0f8470b` |
| `memguard/retrieval/hybrid.py` | 是 | 27785 | `9523dec4679baea76b70e97c177a09d0922b85a01374423ee83d52ac23453807` |
| `memguard/retrieval/kylin_sdk.py` | 是 | 26935 | `77427a0102250d5d362192627e53ed35b1c04f41152fd415edc99ffc65ada6d6` |
| `memguard/store/mmap_store.py` | 是 | 8754 | `5d700fa28d079a7376d6fcef04a2166f960d01588bbccfcece3d7883292858d6` |
| `memguard/store/sqlite_meta.py` | 是 | 80568 | `baf9c8f33b2805358783d6b1807d925f9e8899dcd035bb99fdca300da78445b5` |
| `memguard/extraction/semantic.py` | 是 | 11777 | `b99e3ff27c73ac32f0d50f9ec1cb6335a8604155046d1d35db57eb7ff27eef7d` |
| `memguard/forgetting/planner.py` | 是 | 14023 | `69b5236b6d901359fb71eda28c3ac33402c3754530a5598afcbaca36f23b86cb` |
| `memguard/eval/memory_value.py` | 是 | 14093 | `916c668eb8659aef42f17063a290e4e88f72f5dd0983d078b074e2207c3c779f` |
| `memguard/eval/real_agent_behavior.py` | 是 | 18628 | `d106a58e2d03b272336471e7c5f10bde9b4c6d62456e4a245457eaa8f1011651` |
| `scripts/run_openhands_behavior_suite.py` | 是 | 15863 | `5498fa4a08399c82fb0eed1485e457e578bf724d70739e9a72a8b4570a2f2720` |
| `scripts/run_security_scan.py` | 是 | 15934 | `fc93b75c608263d66388c4682371b689b0b373c00b16272b35ef1a3ce3fc647b` |
| `scripts/collect_openkylin_evidence.sh` | 是 | 21806 | `cf3baccf82fbfdbd3a2f5b07a59b3fe906ab02356fb20b6bc0c942073a1a7960` |
| `scripts/verify_deb_package_set.sh` | 是 | 2517 | `116a33ba055db7442d209e6772c063448dd55fc665329c53ed281847f0433929` |
| `scripts/collect_memory_value_evidence.sh` | 是 | 5453 | `19653b5d5ee75b5b58e5e5d49326b3fdac13aaf6bf8d4677d5f1a6e3bc961599` |
| `scripts/build_deb.sh` | 是 | 259 | `49658e10f618c270960c39a6eca27f21dbd2acd926673a1882135b2bfdc2d7db` |
| `native/bpf_lsm/memguard_lsm.bpf.c` | 是 | 2969 | `2066908bb8f1dd3521256030b3bc13043a9a11865d47cee93f3cf4d80dc189a1` |
| `native/ukui/peony-memguard/memguard_peony_plugin.cpp` | 是 | 31652 | `ef4540438e9fd73dee763669c2961cab8ee05e80e8595e7d930c0da6faf87dda` |
| `native/ukui/ukcc-memguard/memguard_ukcc_plugin.cpp` | 是 | 101395 | `adf309542129276a564835ee1a2a5f132b57aee94579cbb69f7e21d02bc0ba16` |
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
