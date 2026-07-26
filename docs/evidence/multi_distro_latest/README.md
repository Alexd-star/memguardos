# 多发行版复验证据口径说明

本目录保存 MemGuardOS 在 openKylin 之外的 Linux 发行版上的编译、运行与测试证据，对应赛题鼓励项"鼓励在更多 Linux 发行版上编译、运行和测试"（逐句核查编号 R5）。

## 证据构成

| 文件 | 环境 | 结论 |
|---|---|---|
| `github_actions_ubuntu_ci.txt` | GitHub Actions `ubuntu-latest`，Python 3.10 / 3.11 / 3.12 三版本矩阵 | 每个版本完整执行 13 步门禁：依赖安装、lint（compileall + ruff）、全量 pytest、文档链接检查、Web 控制台 smoke、评测 smoke、竞赛就绪 smoke、提交包生成、提交包审计、提交包归档 smoke，全部成功；对应 main 分支 HEAD `78e48b4` 的 CI run `30180185387` |
| `debian_container_full_suite.txt` | Debian GNU/Linux 13 (trixie) 容器（`python:3.12-slim` 基础镜像，由仓库根目录 `Dockerfile` 构建），Python 3.12.13 | `ruff` 全部通过、`compileall` 通过、全量 pytest `654 passed, 1 skipped`，与 openKylin 原生全量测试计数一致 |

## 复现方式

Ubuntu CI：推送到 GitHub 后由 `.github/workflows/ci.yml` 自动执行，或在 Actions 页面手动触发 `workflow_dispatch`。

Debian 容器（任何安装了 Docker 的环境）：

```bash
docker build -t memguardos-multidistro .
docker run --rm memguardos-multidistro sh -c "python -m ruff check memguard tests scripts && python -m pytest -q"
```

## 证据边界

1. 本目录证据支撑的是"更多 Linux 发行版可编译、运行、测试"的鼓励项；openKylin 实机仍是正式主验证环境，OS 机制类主张（BPF LSM、auditd、systemd/cgroup、UKUI 等）仍以 `docs/evidence/latest/` 与 `docs/evidence/openkylin_native_latest/` 为准。
2. 容器验证覆盖 Python 主链路、仓库内容一致性测试与 Linux 专属路径（AF_UNIX、mmap 锁、POSIX 信号），不覆盖 systemd 服务化、桌面插件与内核态机制；这些能力的验证见 openKylin 证据包。
3. 容器内核为宿主提供（transcript 中的 `microsoft-standard-WSL2` 为本次采集宿主的内核标识），发行版用户态为 Debian 13；CI 证据的宿主为 GitHub 托管 Ubuntu。

## 完整性

`manifest.json` 与 `SHA256SUMS.txt` 由 `scripts/write_evidence_manifest.py` 生成，覆盖本目录全部证据文件。
