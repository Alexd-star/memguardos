# MemGuardOS 部署手册

## 1. 本地开发验证

```powershell
cd <项目目录>\memguardos
python -m pytest -q
python -m memguard.eval.run_eval --latency-size 1000 --latency-queries 120 --output output/eval_result.json --markdown output/eval_report.md
python scripts/demo_smoke.py
python scripts/web_dashboard_smoke.py
```

## 2. openKylin 部署

在 openKylin 虚拟机中进入项目目录：

```bash
cd ~/memguardos
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -e ".[dev]"
```

基础验证：

```bash
python -m memguard.cli.memctl doctor
python -m pytest -q
python -m memguard.eval.run_eval --latency-size 1000 --latency-queries 120 --output output/eval_result_openkylin.json --markdown output/eval_report_openkylin.md
```

抽取解释验证：

```bash
python -m memguard.cli.memctl explain-extraction --text "真实案例：systemd service failed with exit_code=1，修复后运行 pytest。"
```

## 3. namespace 多会话隔离

MemGuardOS 默认 namespace 为 `default`，继续使用 `~/.memguardos/` 下的旧路径。显式 namespace 会使用独立目录、独立 mmap 段文件、独立 SQLite 元数据库和独立自定义敏感规则文件。

基础验证：

```bash
python -m memguard.cli.memctl --namespace alpha ingest --text "alpha 会话只保存 openKylin systemd MemoryMax 方案。"
python -m memguard.cli.memctl --namespace beta ingest --text "beta 会话只保存 auditd shadow access 方案。"
python -m memguard.cli.memctl --namespace alpha search MemoryMax
python -m memguard.cli.memctl --namespace beta search MemoryMax
python -m memguard.cli.memctl --namespace alpha namespace-status
python -m memguard.cli.memctl --namespace beta namespace-status
python -m memguard.cli.memctl namespaces
```

预期结果：

- `alpha` 可以检索到 `MemoryMax` 记忆；
- `beta` 不返回 `alpha` 的 `MemoryMax` 记忆；
- 两个 namespace 的 `store_path`、`db_path`、`custom_patterns_path` 不相同；
- 审计详情中包含当前 `namespace` 字段。

## 4. systemd/cgroup 服务

安装服务：

```bash
sudo bash scripts/install_openkylin.sh "$(pwd)"
sudo systemctl start memguardd
systemctl status memguardd
```

验证资源限制：

```bash
python -m memguard.cli.memctl status
systemctl show memguardd -p MemoryMax -p CPUQuotaPerSecUSec
cat /sys/fs/cgroup/system.slice/memguardd.service/memory.max
cat /sys/fs/cgroup/system.slice/memguardd.service/cpu.max
```

服务模板包含：

```ini
MemoryMax=512M
CPUQuota=50%
ExecStart=/usr/bin/python3 -m memguard.daemon --interval 60 --socket /run/memguardos/memguard.sock --socket-mode 660
```

安装脚本同时安装 namespace 模板服务 `memguardd@.service`。模板服务会为每个 namespace 建立独立 daemon、独立 Unix socket 和独立 systemd cgroup：

```bash
sudo systemctl start memguardd@demo-a memguardd@demo-b
systemctl status memguardd@demo-a memguardd@demo-b --no-pager
systemctl show memguardd@demo-a memguardd@demo-b -p ControlGroup -p MemoryMax -p CPUQuotaPerSecUSec --no-pager
MEMGUARD_NAMESPACE=demo-a python -m memguard.cli.memctl namespace-status
MEMGUARD_NAMESPACE=demo-b python -m memguard.cli.memctl namespace-status
```

默认服务使用 `0660`，更适合多人主机和正式交付场景。若临时演示需要让本机普通用户直接访问 socket，应优先通过用户组授权；确需放宽权限时，也应配合本地令牌文件并在演示结束后恢复为 `0660`：

```bash
install -m 600 /dev/null ~/.memguardos-api.token
python3 - <<'PY' > ~/.memguardos-api.token
import secrets
print(secrets.token_urlsafe(32))
PY
chmod 600 ~/.memguardos-api.token
python -m memguard.daemon --interval 60 --socket /run/memguardos/memguard.sock --socket-mode 660 --auth-token-file ~/.memguardos-api.token
```

验证本地 Agent API：

```bash
test -S /run/memguardos/memguard.sock
python3 - <<'PY'
import json, socket
with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
    client.connect("/run/memguardos/memguard.sock")
    client.sendall(json.dumps({"command": "health"}).encode("utf-8") + b"\n")
    print(client.recv(4096).decode("utf-8"))
PY
```

查看当前 API 实例的 namespace 和实际存储路径：

```bash
python3 - <<'PY'
import json, socket
with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
    client.connect("/run/memguardos/memguard.sock")
    client.sendall(json.dumps({"command": "namespace_status"}).encode("utf-8") + b"\n")
    print(client.recv(65536).decode("utf-8"))
PY
```

启用令牌后，请求需要包含 `token`：

```bash
TOKEN="$(cat ~/.memguardos-api.token)"
TOKEN="$TOKEN" python3 - <<'PY'
import json, os, socket
with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
    client.connect("/run/memguardos/memguard.sock")
    client.sendall(json.dumps({"command": "health", "token": os.environ["TOKEN"]}).encode("utf-8") + b"\n")
    print(client.recv(4096).decode("utf-8"))
PY
```

### 用户会话内的本机向量维护

Kylin AI 运行时按桌面用户提供 socket。需要在当前用户会话内使用 SDK 和后台向量补齐时，五包同时提供独立的用户服务；安装软件包不会自动启用它：

```bash
systemctl --user daemon-reload
systemctl --user enable --now memguardd-user.service
systemctl --user status memguardd-user.service --no-pager
systemctl --user show memguardd-user.service -p MemoryHigh -p MemoryMax -p CPUQuotaPerSecUSec
```

该服务使用 `~/.local/share/memguardos` 保存数据，socket 位于 `$XDG_RUNTIME_DIR/memguardos-user/memory.sock`，目录权限为 `0700`、socket 权限为 `0600`。它与系统服务以及 CLI 默认的 `~/.memguardos` 数据分开，不迁移或合并原有记忆。直接使用 CLI 查看这份用户服务数据时，应在同一用户下显式设置 `MEMGUARD_HOME="$HOME/.local/share/memguardos"`；已有 Agent 则连接上述用户 socket。操作系统用户身份才是安全边界，同一用户内部的 namespace 用于管理不同记忆空间。

后台维护每 30 秒检查一次待补齐向量，单轮最多处理 8 项、总工作预算为 2 秒。内存接近 cgroup 上限、PSI 内存或 I/O 压力过高时暂停本轮任务；已完成向量带记忆版本和模型修订，更新或遗忘后不能作为当前版本继续使用。停止用户服务可执行 `systemctl --user disable --now memguardd-user.service`，原有数据保留。

## 5. journald 审计

```bash
python -m memguard.cli.memctl ingest --text "journald audit smoke test"
python -m memguard.cli.memctl verify-audit
journalctl -t memguardd -n 50
```

## 6. auditd 敏感访问规则

预览规则：

```bash
python -m memguard.cli.memctl auditd-rules --format auditctl
```

安装规则：

```bash
sudo bash scripts/install_auditd_rules.sh "$(pwd)"
sudo auditctl -l | grep memguard_sensitive
```

采集并写入 MemGuardOS：

```bash
bash scripts/auditd_to_memguard.sh "$(pwd)" memguard_sensitive 200 --summary
```

## 7. Kylin SDK 验证

查看 SDK 状态：

```bash
python -m memguard.cli.memctl sdk-status
python -m memguard.cli.memctl sdk-status --probe-embedding --probe-timeout 90
python -m memguard.cli.memctl openkylin-status
python -m memguard.cli.memctl openkylin-status --probe-sdk --probe-timeout 90
```

`openkylin-status` 默认面向 UKUI 设置页、托盘和日常状态刷新，只检查系统服务、插件文件、模型仓库、socket 和基础路径，不加载完整 Kylin SDK 推理链路；`--fast` 仍作为兼容参数保留。这样普通用户打开【设置】>【安全】时不会因为 embedding 模型冷启动而明显卡顿。需要采集真实 SDK 推理证据时，再运行 `sdk-status --probe-embedding` 或 `openkylin-status --probe-sdk --probe-timeout 90`；两类命令的输出语义不同，不能把快速状态等同于真实推理成功。

如文本 embedding 探针未返回向量，先采集只读诊断。SDK 文件存在、socket 存在和服务 active 都不能代替实际向量推理成功：

```bash
bash scripts/fix_kylin_embedding_runtime.sh --no-probe
bash scripts/diagnose_kylin_embedding.sh --no-probe
```

修复脚本默认不补链接、不重启服务，也不执行推理。如果两个模型仓库出现同名模型，只在确认 `/opt` 条目是指向同一 `/usr` 模型的符号链接时，才可使用 `--fix-model-repo --restart-kytensor`：重复链接先移入带恢复清单的备份目录，全部唯一模型继续保留。名称相同但内容不同、仓库路径相同或存在未托管运行时进程时，脚本停止操作，交由管理员核对，不自行选择模型或删除 socket。

重启桌面后，模型可能仍处于加载阶段。应同时查看 Triton 模型状态、内存及 swap 压力，并在就绪后做推理探针；短时间探针超时不能直接判定模型损坏。默认记忆检索在这段时间继续使用轻量后端，不等待完整模型加载。

运行文本向量化 SDK 官方 C API 风格探针：

```bash
bash scripts/run_kylin_embedding_probe.sh "openKylin agent memory"
```

该探针用于排除 Python `ctypes` 适配层因素。如果它与 `sdk-status --probe-embedding` 都不能返回向量，优先检查 `kytensor.service`、`kylin-ai-runtime.service`、模型仓库、`core-textembedding.sock`、ondevice 插件和 VM 资源环境。

验证 Kylin embedding 作为实际可选检索重排后端：

```bash
MEMGUARD_EMBEDDING_BACKEND=auto MEMGUARD_KYLIN_EMBEDDING_TIMEOUT=90 \
  python -m memguard.cli.memctl search "openKylin 服务内存限制" --top-k 2
```

预期输出中命中项可能包含 `vector_backend: "kylin_sdk"`。如果 SDK 不可用、尚未预热或超过延迟预算，系统会保留 FTS/hash/graph 检索结果。语义候选和重排共用 `MEMGUARD_KYLIN_RERANK_BUDGET_MS=160` 的总预算，包括锁等待和推理等待。切换到 `kylin` 后端或单独提高 `MEMGUARD_KYLIN_EMBEDDING_TIMEOUT` 不会取消这项检索预算。需要测量语义效果时，应在隔离的评测进程中显式设置更大的总预算，同时报告延迟与降级情况；不能将长时间探针结果作为默认交互延迟成绩。

运行向量数据库 SDK 探针：

```bash
bash scripts/run_kylin_vector_probe.sh
```

## 8. 一键验证

```bash
bash scripts/openkylin_verify.sh
```

该脚本会运行 doctor、SDK 状态、embedding 探针、Kylin 可选检索 smoke、自然语言敏感规则 smoke、向量数据库探针、单元测试、评测、auditd 规则预览、OS 事件 smoke test、真实 auditd 日志接入和审计链验证。完整证据包使用 `scripts/collect_openkylin_evidence.sh --with-10k` 采集，包含 namespace 隔离、systemd 模板实例、Kylin 实际检索、自然语言敏感规则、可解释语义抽取、本地 Web 控制台 smoke、BPF LSM 策略生成、UKUI 插件可见性核查脚本和并发审计证据。

## 9. 本地 Web 控制台

启动控制台：

```bash
python -m memguard.cli.memctl web --host 127.0.0.1 --port 8765
```

访问 `http://127.0.0.1:8765`。控制台用于本机运维、调试和验证，提供运行状态、写入、抽取解释、检索、RAG 上下文、精准遗忘、审计和系统诊断视图。诊断视图可校验审计链、查看检索后端和记忆策略；高风险遗忘操作会要求二次确认。控制台不建议绑定公网地址。若需要 API 鉴权，可先生成本地令牌文件，再使用：

```bash
python -m memguard.cli.memctl web --host 127.0.0.1 --port 8765 --auth-token-file ~/.memguardos-api.token
```

## 10. BPF LSM 策略与 UKUI 桌面插件验证

生成可审查的 BPF LSM denylist header：

```bash
python3 scripts/generate_bpf_lsm_policy.py --policy data/demo/bpf_lsm_policy.json --output /tmp/memguard_lsm_policy.generated.h
```

验证 BPF LSM 安全测试文件强制拒绝：

```bash
bash scripts/run_bpf_lsm_enforcer.sh output/bpf-lsm-evidence
```

验证 UKUI 插件构建、安装和可见性核查步骤：

```bash
bash scripts/build_ukui_plugins.sh
bash scripts/install_ukui_plugins.sh --no-build
bash scripts/verify_ukui_plugins.sh
```

Peony 文件管理器和 UKUI 控制中心都属于 openKylin 桌面原生插件路径。`install_ukui_plugins.sh` 会优先安装到系统插件目录；如果 openKylin 使用 ostree/overlay 导致 `/usr` 只读，脚本会复制系统插件目录到 `/var/tmp/memguardos-ukui-visible/` 并通过 bind mount 做演示安装。该方式适合评审现场验证 UI 可见性，不等同于正式 `.deb` 包安装。

安装后刷新当前 UKUI 桌面会话。脚本会从正在运行的 Peony 进程自动识别实际 `DISPLAY`（例如 `:0` 或 `:1`），避免固定显示号导致插件安装后仍未重新加载：

```bash
bash scripts/refresh_ukui_desktop.sh
bash scripts/smoke_peony_file_scan.sh
```

第二条命令加载实际安装的 Peony 插件，触发一次只读检查，并在检测到预览结果窗口后自动关闭。输出 `peony_file_scan_dialog=visible` 且 `preview_rows` 不小于 1，表示“右键动作 → 扫描进程 → 结果窗口”链路可用；该烟测不会导入文件或修改记忆。

预期结果：

- Peony 文件右键菜单中出现 MemGuardOS 文件扫描入口，并能显示预览确认对话框；
- 对小于 1 MB 的文本类文件，右键动作可对应 `peony_file` 内容导入；导入结果中的【撤销导入 / 立即遗忘】会直接调用 `memctl desktop forget-import` 处理本次返回的 `memory_id`，可用 `bash scripts/peony_file_scan_smoke.sh "$(pwd)"` 复验写入、检索和审计链；
- UKUI 设置/控制中心左侧安全分类下出现【MemGuardOS 记忆安全】；
- 控制中心页面可见服务状态、服务级 cgroup、Kylin SDK、审计链、Polkit 高权限动作、诊断报告和本地控制台操作；
- 【处理建议】每一行右侧都有处理按钮，可跳转到对应区域或直接执行安全检查：服务类建议进入【管理员】>【服务控制】，SDK 类建议执行本机智能检索检查，审计类建议执行审计链校验，授权、桌面和资源类建议定位到相应说明区；
- 控制中心首次打开应保持流畅，状态刷新使用轻量探针；点击【检查本机智能检索】或【预热本机智能检索】时才执行完整 SDK 检查，界面应提示用户等待并在结果区显示检查结果。
- 托盘菜单可直接打开【记忆安全中心】、【本机智能检索】、【审计状态】和【最近一次阻断记录】。其中 SDK 和审计会定向到控制中心管理员页，敏感阻断会打开本地控制台审计页。

正式部署使用下一节的五包安装流程；插件可见性、动态链接和菜单入口由上述核查脚本验证。

## 11. Debian 安装包

MemGuardOS 提供 `.deb` 打包脚本，用于把项目从“源码目录运行”提升为 openKylin/Debian 系统包安装。当前版本采用五包拆分，而不是把所有能力塞进一个大包。这样便于最小化部署，也便于后续企业环境按职责安装核心服务、桌面插件、Agent 桥接和文档。

构建安装包：

```bash
cd ~/memguardos
bash scripts/build_deb.sh
```

构建成功后会生成：

```text
dist/memguardos-core_<版本>_<架构>.deb
dist/memguardos-ukui_<版本>_<架构>.deb
dist/memguardos-agent-bridge_<版本>_<架构>.deb
dist/memguardos-doc_<版本>_all.deb
dist/memguardos_<版本>_all.deb
```

安装：

```bash
mapfile -t packages < <(python3 -c 'import json; print(*[p["path"] for p in json.load(open("dist/memguardos_debian_packages.json"))["packages"]], sep="\n")')
sudo apt install "${packages[@]}"
```

推荐使用清单驱动安装门禁。它先核对系统是否允许安装，再检查五包归属、版本、架构和 SHA-256，执行 `apt-get --reinstall`，最后逐项核对实际安装版本和包内文件校验。检查通过后才启动服务并输出成功：

```bash
bash scripts/install_deb_package_set.sh
# 已有完整五包与清单时，不重复构建：
bash scripts/install_deb_package_set.sh --no-build
```

openKylin 2.0 SP2 的普通模式可能把 `dpkg` 调用交给应用安装容器，而没有更新系统软件包。即使该命令返回 0，也必须核对实际安装版本。安装器遇到包管理包装器时，通过官方 D-Bus 查询维护模式，并用 `statvfs` 检查当前进程可见的 `/usr` 是否可写；查询失败、普通模式或只读状态均停止，不自行切换模式或重新挂载根目录。

请先保存桌面工作，在【设置】的维护模式页面开启维护模式；系统可能立即注销。重新登录后执行安装器，确认成功，再在该页面退出维护模式，选择保留维护期间的更改并重新启动。部分版本的 `mm-cli -c -a` 不支持退出实时维护模式，此时使用系统设置页面，不选择“放弃更改”。重启后检查：

```bash
mm-cli -s
dpkg-query -W memguardos memguardos-core memguardos-ukui memguardos-agent-bridge memguardos-doc
systemctl is-active memguardd.service
```

发布证据应与对应源码和包清单匹配。已提交版本记录 Git 修订，未提交的开发版本还需记录文件级 SHA-256 快照，不能只用 Git HEAD 代表当前工作树。

`memguardos-ukui` 安装后会刷新桌面菜单、图标缓存，重启 Peony，并关闭已打开的 UKUI 控制中心实例。用户重新打开文件管理器或设置后应能看到最新入口；如果仍看不到，先重新打开 Peony/控制中心，再执行 `bash scripts/verify_ukui_plugins.sh` 检查插件路径和 Qt 元数据。

如果当前环境不能进入维护模式，可先运行：

```bash
bash scripts/verify_deb_lifecycle.sh
```

该脚本会验证五包齐全、包间依赖、离线解包安装、桌面包移除后核心能力保留、桌面包重装恢复和用户数据保护。它不能完全替代系统级 `apt install` 证据，但能证明包边界和安装内容是可审计的。

从早期单包或手工安装升级时，`memguardos-core` 会清理 `/opt/memguardos/scripts/` 中由旧版本遗留的已知程序文件。清理前会逐项调用 `dpkg-query -S`；当前仍属于任一 Debian 包的文件不会删除，名称不在项目已知脚本清单中的自定义文件也不会删除。该迁移逻辑不扫描或删除 namespace、SQLite、mmap 段文件、审计链、用户配置、备份和日志目录。

安装后可直接使用系统命令：

```bash
memctl doctor
memctl verify-audit
sudo systemctl start memguardd
systemctl status memguardd --no-pager
```

安装内容：

| 路径 | 内容 |
|---|---|
| `/opt/memguardos` | MemGuardOS 运行代码、脚本、数据样例和正式文档 |
| `/usr/bin/memctl` | 命令行包装入口，自动设置 `PYTHONPATH=/opt/memguardos` |
| `/usr/bin/memguardd` | daemon 包装入口 |
| `/lib/systemd/system/memguardd.service` | 默认 MemGuardOS 后台服务 |
| `/lib/systemd/system/memguardd@.service` | namespace 模板服务 |
| `/usr/lib/<multiarch>/peony-extensions/` | Peony 右键菜单插件，构建依赖存在时打包 |
| `/usr/lib/<multiarch>/ukui-control-center/` | UKUI 控制中心插件，构建依赖存在时打包 |
| `/usr/bin/memguardos-tray` | UKUI 托盘入口 |
| `/usr/share/polkit-1/actions/org.memguardos.policy` | Polkit 授权策略 |
| `/usr/lib/memguardos/memguardos-native-helper.sh` | 受控高权限 helper |

卸载：

```bash
sudo systemctl stop memguardd 2>/dev/null || true
sudo apt remove memguardos memguardos-ukui memguardos-agent-bridge memguardos-doc memguardos-core
```

注意事项：

- `.deb` 安装包面向 openKylin/Debian 系系统包验证，不替代竞赛平台要求的源码、文档和证据压缩包。
- 如果构建机器缺少 `qmake`、`libpeony-dev` 或 `libukcc-dev`，桌面包构建会失败并给出明确提示；正式提交建议在 openKylin 上构建，保证 Peony、UKUI 控制中心和托盘都进入 `memguardos-ukui`。
- openKylin 只读 `/usr` 环境中，正式 `.deb` 安装应通过系统提供的维护模式执行，保存更改并重启后复核。源码脚本的临时可见性验证不能代替软件包安装。
