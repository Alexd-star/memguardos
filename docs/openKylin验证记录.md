# openKylin 验证记录

本文记录在 openKylin 上验证 MemGuardOS 的命令和结果。录制最终视频前需要在最终环境中复跑。

## 0. 已验证 VM 快照

日期：2026-05-18 至 2026-05-22

说明：本页记录已经完成的 openKylin 证据。最新证据包采集时间为 `20260522_234709`，已包含用户行为接入、Unix socket token 鉴权、namespace 多会话隔离、systemd 模板 cgroup、并发审计、自然语言敏感规则、Kylin embedding 实际检索重排、Kylin 向量数据库探针、BPF LSM 安全测试文件强制拒绝、Peony/UKUI 原生插件构建和 10k 规模评测。

环境：

- OS：openKylin 2.0 SP2
- 内核/平台：`Linux-6.6.0-19-generic-x86_64-with-glibc2.38`
- Python：3.12.2
- SQLite：3.42.0
- cgroup v2：已挂载
- `systemctl`、`journalctl`、`systemd-cat`：可用
- `memguardd.service`：常驻 daemon，`active (running)`
- `memguardd@demo-a.service`、`memguardd@demo-b.service`：模板 namespace daemon，`active (running)`
- `auditctl`、`ausearch`：已安装并验证
- BPF LSM：`/sys/kernel/security/lsm` 包含 `bpf`，BTF 可用，BPF LSM loader 可 attach
- UKUI 插件构建环境：`libpeony-dev`、`libukcc-dev` 和 Qt5 开发工具可用
- Kylin embedding SDK：`libkysdk-coreai-embedding.so.1` 可加载，Python/C++ 探针均返回 768 维向量
- Kylin embedding 可选检索后端：`MEMGUARD_EMBEDDING_BACKEND=auto` 实际检索返回 `vector_backend: "kylin_sdk"`
- Kylin vector SDK：`libkysdk-vector-engine-client.so.1` 可加载，C++ 探针完成连接、建表、插入、搜索和清理
- Kylin vector engine 用户服务：active，socket 可用

已验证命令：

```bash
cd ~/memguardos
source .venv/bin/activate
python -m pytest -q
python -m memguard.eval.run_eval --latency-size 1000 --latency-queries 120 \
  --output docs/eval_result_openkylin.json \
  --markdown docs/eval_report_openkylin.md
bash scripts/openkylin_verify.sh
python -m memguard.cli.memctl sdk-status
bash scripts/collect_openkylin_evidence.sh --with-10k
```

最新结果：

- 单元测试：`42 passed`
- 负载评测：1000 条记忆，120 次检索请求
- 最新 1k 负载 P95：76.182 ms
- 5k 负载 P95：142.534 ms
- 10k 负载 P95：343.328 ms
- 最新 openKylin RSS：34668.0 KB
- 10k 评测 RSS：66116.0 KB
- 纯文本基线 Recall@5：0.7800
- 完整 MemGuardOS Recall@5：1.0000
- 该证据包采集时的 benchmark：30 条种子记忆、25 条检索用例、25 条敏感检测用例、17 条精准遗忘用例、10 条冲突解决用例、13 条图谱检索用例、6 条数据质量用例。当前代码已扩充到 45/40/25/20/10/13/6，提交前复跑证据包后会同步刷新。
- 类型/子类型准确率、Recall@5、敏感阻断 F1、脱敏 F1、敏感标签 F1、遗忘精确率/召回率、冲突正确率、图谱 Recall@5 均为 1.0000
- 审计哈希链验证通过
- 并发审计验证通过：6 线程共 72 条事件，`verify.ok=true`
- OS 敏感路径 smoke test 被正确阻断
- 真实 auditd 日志接入已验证：访问 `/etc/shadow` 后解析出的事件被 `sensitive_block` 阻断
- BPF LSM 强制拦截已验证：访问 `/tmp/memguardos_lsm_secret` 返回 `Permission denied`，证据为 `enforcement_result=denied`
- Peony/UKUI 原生插件已构建：Peony 右键菜单插件和 UKUI 控制中心插件均构建出 `.so`；系统目录安装因根文件系统只读未完成
- SDK 状态：embedding 模型信息显示 768 维端侧文本模型，Python/C++ 探针均可返回 768 维向量；实际检索可使用 `kylin_sdk` 作为可选重排后端；vector SDK 库和服务可用
- 自然语言敏感规则：`patterns suggest` 和 `patterns add-natural` 可从“工号格式”指令生成受限正则，并在写入记忆时完成脱敏

## 1. 基础环境验证

```bash
cd ~/memguardos
python3 --version
python3 -m memguard.cli.memctl doctor
```

期望证据：

- 检测到 `systemctl`。
- 检测到 `journalctl`。
- `/proc` 存在。
- `/sys/fs/cgroup` 存在。
- cgroup v2 状态可见。
- `doctor` 输出包含 Kylin AI SDK 状态。

## 2. Kylin AI SDK 验证

```bash
python3 -m memguard.cli.memctl sdk-status
python3 -m memguard.cli.memctl sdk-status --probe-embedding
bash scripts/run_kylin_embedding_probe.sh "openKylin agent memory"
bash scripts/run_kylin_vector_probe.sh
```

期望证据：

- embedding 动态库可加载：`libkysdk-coreai-embedding.so.1`。
- embedding 头文件位于 `/usr/include/kylin-ai/`。
- 模型信息包含文本向量维度和 `ondevice: true`。
- Python 子进程探针和 C++ 探针均返回 768 维文本向量。
- `MEMGUARD_EMBEDDING_BACKEND=auto` 时，实际检索结果包含 `vector_backend: "kylin_sdk"`。
- vector SDK 动态库可加载：`libkysdk-vector-engine-client.so.1`。
- vector 数据库服务 `kylin-ai-vector-engine.service` active。
- vector engine socket 存在。
- C++ 探针完成创建、插入、搜索和删除。

## 3. 单元测试与评测

```bash
python3 -m pytest -q
python3 -m memguard.eval.run_eval --latency-size 1000 --latency-queries 120 \
  --output docs/eval_result_openkylin.json \
  --markdown docs/eval_report_openkylin.md
```

期望证据：

- 所有测试通过。
- 报告包含检索、敏感检测、遗忘、冲突、图谱、记忆层级指标。
- P95 延迟低于 500 ms。
- 负载评测至少包含 100 次检索请求。
- 报告包含 RSS 和存储 footprint。

## 4. systemd/cgroup 验证

```bash
sudo bash scripts/install_openkylin.sh "$(pwd)"
sudo systemctl start memguardd
systemctl status memguardd
python3 -m memguard.cli.memctl status
systemctl show memguardd -p MemoryMax -p CPUQuotaPerSecUSec
cat /sys/fs/cgroup/system.slice/memguardd.service/memory.max
cat /sys/fs/cgroup/system.slice/memguardd.service/cpu.max
```

当前 VM 证据：

```text
ControlGroup=/system.slice/memguardd.service
CPUQuotaPerSecUSec=500ms
MemoryMax=536870912
/sys/fs/cgroup/system.slice/memguardd.service/memory.max = 536870912
/sys/fs/cgroup/system.slice/memguardd.service/cpu.max = 50000 100000
```

## 5. journald 审计验证

```bash
python3 -m memguard.cli.memctl ingest --text "openKylin journald audit smoke test"
python3 -m memguard.cli.memctl verify-audit
journalctl -t memguardd -n 50
```

期望证据：

- `verify-audit` 返回 `ok: true`。
- systemd 环境中 journald 能看到 MemGuardOS 审计事件。

## 6. auditd 敏感路径验证

```bash
python3 -m memguard.cli.memctl auditd-rules --format auditctl
sudo bash scripts/install_auditd_rules.sh "$(pwd)"
sudo auditctl -l | grep memguard_sensitive
sudo ausearch -k memguard_sensitive -i
```

真实日志接入：

```bash
sudo cat /etc/shadow >/dev/null 2>/dev/null || true
bash scripts/auditd_to_memguard.sh "$(pwd)" memguard_sensitive 200 --summary
```

期望证据：

- 敏感路径事件不进入长期记忆。
- 审计日志包含 `os_event` 和 `sensitive_block`。
- `/etc/shadow` 和 `~/.ssh/authorized_keys` 相关事件返回 `sensitive_block`。

## 7. 记忆层级验证

```bash
python3 -m memguard.cli.memctl ingest --text "临时工作记忆" --tier working
python3 -m memguard.cli.memctl ingest --text "会话记忆" --tier session
python3 -m memguard.cli.memctl ingest --text "长期偏好：回答要简洁" --tier long_term
python3 -m memguard.cli.memctl list --tier working
python3 -m memguard.cli.memctl evict-tier session --yes
```

期望证据：

- `working`、`session`、`long_term` 均可用。
- 清理 `session` 不会删除长期记忆。

## 8. BPF LSM 强制拦截验证

```bash
bash scripts/run_bpf_lsm_enforcer.sh docs/evidence/latest
```

当前 VM 证据：

```text
MemGuardOS BPF LSM enforcer attached. Press Ctrl+C to detach.
cat: /tmp/memguardos_lsm_secret: 权限不够
enforcement_result=denied
```

边界说明：默认演示只拦 `/tmp/memguardos_lsm_secret`，不拦 `/etc/shadow`、SSH、sudo 或 PAM 相关路径，避免影响登录链路。auditd 仍是默认稳定审计主路径。

## 9. Peony/UKUI 原生插件验证

```bash
bash scripts/probe_ukui_deep_plugins.sh docs/evidence/latest
bash scripts/build_ukui_plugins.sh
bash scripts/install_ukui_plugins.sh --no-build
```

当前 VM 证据：

- Peony 右键菜单插件构建产物：`native/ukui/build/peony-memguard/libpeony-menu-plugin-memguardos.so`。
- UKUI 控制中心插件构建产物：`native/ukui/build/ukcc-memguard/libmemguardos-ukcc-plugin.so`。
- 安装到 `/usr/lib/x86_64-linux-gnu/...` 时返回“只读文件系统”，因此当前只宣称源码和 `.so` 构建成功，不宣称 UI 已可见。

## 10. 一键验证

```bash
bash scripts/openkylin_verify.sh
```

该脚本会运行 doctor、Kylin AI SDK 状态、向量数据库探针、单元测试、评测、auditd 规则预览、OS 事件 smoke test、真实 auditd 日志接入和审计链验证。
