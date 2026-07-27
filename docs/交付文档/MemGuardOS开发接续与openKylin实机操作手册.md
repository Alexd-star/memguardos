# MemGuardOS 开发接续与 openKylin 实机操作手册

> 文档用途：供新的开发会话、维护人员和验收人员接续 MemGuardOS 项目时使用。
> 适用环境：Windows 主机开发目录 + openKylin 2.0 SP2 虚拟机。
> 文档版本：V1.0
> 状态基准日期：2026 年 7 月 27 日

---

## 第1章 文档目的与使用原则

### 1.1 为什么需要这份手册

MemGuardOS 的开发并不只发生在一个目录中。Windows 主机保存完整 Git 仓库、开发修改和提交历史；openKylin 虚拟机负责验证 systemd 服务、Debian 安装包、UKUI 控制中心插件、Peony 文件管理器插件、系统托盘、Polkit 授权和 openKylin 原生证据。新的开发会话如果只看到 Windows 代码，通常不知道如何把修改可靠地同步到虚拟机，也容易把旧二进制、旧安装包或旧证据误认为当前版本的结果。

本手册给出一条从“接手代码”到“完成实机验证”的完整路径。执行者应先识别当前 Git 状态，再选择快速开发同步或正式同版本同步，随后完成 Python 环境更新、服务安装、桌面插件刷新、测试和证据采集。任何步骤失败时，应先按本手册核对原因，不要绕过错误继续生成最终证据。

### 1.2 两种同步模式必须严格区分

| 同步模式 | 适用场景 | 是否允许未提交修改 | 是否可以生成最终证据 | 版本标识 |
| --- | --- | ---: | ---: | --- |
| 快速开发同步 | UI 调试、定向测试、确认按钮和流程 | 是 | 否 | `UNCOMMITTED-*` |
| 正式同 HEAD 同步 | 发布、五包安装、全量测试、最终证据 | 否 | 是 | 40 位 Git commit SHA |

“快速开发同步”不是低质量操作，而是缩短调试反馈周期的工程手段；但它无法证明虚拟机代码对应某个不可变提交，因此不能用于最终验收。“正式同 HEAD 同步”要求 Windows 工作树干净、提交已推送，并用 `git archive` 生成只包含该提交的源码包。两种模式不能混用。

### 1.3 安全边界

1. 本手册不记录虚拟机登录密码。当前环境使用 SSH 密钥登录。
2. 源码同步只作用于 `/home/luo/memguardos`，用户记忆数据默认位于 `~/.memguardos/`，不得随源码清理。
3. 同步操作保留 `.venv` 和 `.venv-agent`，但会清理源码目录内的旧 `dist/` 与 `native/ukui/build/`，以防旧安装包或旧插件污染结果。
4. 最终证据采集前必须确认工作树干净、版本号正确、系统服务正常。
5. 遇到命令失败时不要使用 `|| true` 掩盖核心错误。文档中仅在关闭不存在的桌面进程等可容忍场景使用该写法。

---

## 第2章 固定环境参数与当前状态

### 2.1 固定参数

| 项目 | 当前值 | 用途 |
| --- | --- | --- |
| Windows 项目目录 | `D:\Luo_docs\竞赛\研创赛\memguardos` | Git 开发主目录 |
| openKylin 主机 | `192.168.73.128` | 虚拟机 SSH 地址 |
| openKylin 用户 | `luo` | 普通桌面用户和 SSH 用户 |
| 虚拟机项目目录 | `/home/luo/memguardos` | 实机编译与测试目录 |
| SSH 私钥 | `C:\Users\26312\.ssh\id_ed25519` | 免密 SSH 登录 |
| Web 控制台端口 | `8765` | 仅监听虚拟机本机地址 |
| UKUI 图形显示 | `DISPLAY=:0` | 桌面程序显示目标 |
| 用户运行时目录 | `/run/user/1000` | DBus 与桌面会话运行目录 |

若虚拟机由 DHCP 分配地址，重启后应先在虚拟机终端运行 `ip -br addr`。当地址不再是 `192.168.73.128` 时，只更新 SSH 配置中的 `HostName`，不要在项目脚本中到处硬编码新地址。

### 2.2 接手时必须实时确认的版本状态

本手册不固化某次开发中的 Git SHA 或未提交文件数量，因为这些信息在每次提交后都会变化。新会话接手项目时，应以第3章命令实时读取分支、`HEAD`、`origin/main`、工作树状态和虚拟机 `.source_revision`，并把输出保存在当前任务记录中。只有当本地工作树干净、目标提交已经推送、虚拟机 `.source_revision` 与该提交的 40 位 SHA 完全一致时，才允许把实机结果称为“当前 HEAD 证据”。

若 Windows 有未提交修改，说明仍处于开发阶段；此时可以执行快速开发同步和定向验证，但不得采集最终证据。若 openKylin 项目目录没有 `.git`，这是 `git archive` 正式部署的正常结果，证据采集脚本会读取 `.source_revision` 与 `MEMGUARD_SOURCE_MODE=git_archive` 识别源码版本。任何旧 SHA、旧安装日志或旧截图只能作为历史材料，不能替代当前版本的复验结果。

这说明当前 Windows 修改尚不能被现有虚拟机证据证明。新会话必须重新运行本章后续的状态检查，不能永远依赖上述快照。

---

## 第3章 新会话开始前检查

### 3.1 Windows 本地代码检查

打开 PowerShell，执行：

```powershell
Set-Location 'D:\Luo_docs\竞赛\研创赛\memguardos'
git status --short --branch
git rev-parse HEAD
git rev-parse origin/main
git log -5 --oneline --decorate
```

正常情况下应看见当前分支、是否领先远端以及修改文件。判断规则如下：

- 有 `M`、`A`、`D` 或 `??`：工作树不干净，只能快速开发同步，不能采最终证据。
- `main...origin/main [ahead N]`：本地有未推送提交，应在测试通过后推送。
- `main...origin/main [behind N]`：远端有新提交，应先核对并整合，不能直接覆盖虚拟机。
- `git rev-parse HEAD` 与虚拟机 `.source_revision` 不同：虚拟机不是当前版本。

### 3.2 SSH 与虚拟机状态检查

执行：

```powershell
Test-NetConnection 192.168.73.128 -Port 22
ssh -o BatchMode=yes -o ConnectTimeout=8 luo@192.168.73.128 "hostname; id; head -n 8 /etc/os-release"
ssh -o BatchMode=yes luo@192.168.73.128 "systemctl is-active memguardd.service; cat /home/luo/memguardos/.source_revision 2>/dev/null || echo missing"
```

通过标准：

1. `TcpTestSucceeded` 为 `True`；
2. SSH 不询问密码并返回 `luo-pc`、用户 UID 和 openKylin 信息；
3. `memguardd.service` 返回 `active`；
4. `.source_revision` 返回一个 40 位 SHA，或在快速开发态明确返回 `UNCOMMITTED-*`。

如果端口 22 不通，应依次检查虚拟机是否启动、IP 是否变化、网络模式是否正常以及 `sshd` 是否运行。VPN 一般不会影响同一台主机上的虚拟机私网，但路由冲突时可能造成 `192.168.73.0/24` 不可达，可临时断开 VPN 复验。

---

## 第4章 SSH 配置与连接方法

### 4.1 配置 SSH 别名

编辑 Windows 文件 `C:\Users\26312\.ssh\config`，加入：

```sshconfig
Host openkylin-memguard
    HostName 192.168.73.128
    User luo
    IdentityFile C:/Users/26312/.ssh/id_ed25519
    ServerAliveInterval 30
    ServerAliveCountMax 3
```

以后所有命令可使用短名称 `openkylin-memguard`。验证命令为：

```powershell
ssh -o BatchMode=yes -o ConnectTimeout=8 openkylin-memguard "hostname; id; head -n 8 /etc/os-release"
```

### 4.2 进入交互式 SSH

```powershell
ssh openkylin-memguard
```

需要执行 `sudo` 或持续观察日志时，使用交互式会话最稳妥。命令提示符应类似：

```text
luo@luo-pc:~$
```

退出 SSH 使用 `exit`，不要直接关闭正在执行安装或测试的终端窗口。

### 4.3 设置图形桌面环境变量

通过 SSH 启动托盘、通知或控制中心前，在虚拟机 Shell 中执行：

```bash
export DISPLAY=:0
export XDG_RUNTIME_DIR=/run/user/1000
export DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus
```

验证桌面会话：

```bash
test -S /tmp/.X11-unix/X0 && echo "X11 可用"
test -S /run/user/1000/bus && echo "用户 DBus 可用"
pgrep -af 'ukui-session|ukui-panel|peony' | head
```

如果 `DISPLAY` 或 DBus 不存在，说明用户尚未进入 openKylin 桌面。此时可以做纯命令测试，但不要验证托盘、通知和原生插件界面。

---

## 第5章 快速开发同步

### 5.1 适用条件

当 Windows 有未提交修改，需要尽快在 openKylin 上查看 UI、编译插件或运行定向测试时，使用本章流程。同步后 `.source_revision` 会被写成 `UNCOMMITTED-*`，这是防止误采最终证据的保护措施。

### 5.2 Windows 生成开发快照

在 PowerShell 中执行：

```powershell
$Repo = 'D:\Luo_docs\竞赛\研创赛\memguardos'
$Archive = Join-Path $env:TEMP 'memguardos-dev-sync.tar.gz'

Set-Location $Repo
Remove-Item -LiteralPath $Archive -Force -ErrorAction SilentlyContinue
tar -czf $Archive `
  --exclude=.git `
  --exclude=.venv `
  --exclude=.venv-agent `
  --exclude=.pytest_cache `
  --exclude=.ruff_cache `
  --exclude=__pycache__ `
  --exclude=dist `
  --exclude=native/ukui/build `
  -C $Repo .

Get-Item $Archive | Select-Object FullName,Length,LastWriteTime
scp $Archive openkylin-memguard:/tmp/memguardos-dev-sync.tar.gz
```

### 5.3 openKylin 覆盖源码

进入 SSH：

```powershell
ssh openkylin-memguard
```

在虚拟机中执行：

```bash
set -euo pipefail
PROJECT=/home/luo/memguardos
INCOMING=/home/luo/memguardos.incoming
ARCHIVE=/tmp/memguardos-dev-sync.tar.gz

test "$(id -un)" = "luo"
test -d /home/luo
test -s "$ARCHIVE"

sudo systemctl stop memguardd.service
rm -rf "$INCOMING"
mkdir -p "$INCOMING"
tar -xzf "$ARCHIVE" -C "$INCOMING"

mkdir -p "$PROJECT"
rsync -a --delete \
  --exclude='.venv/' \
  --exclude='.venv-agent/' \
  "$INCOMING/" "$PROJECT/"

rm -rf "$INCOMING"
printf 'UNCOMMITTED-%s\n' "$(date -u +%Y%m%dT%H%M%SZ)" > "$PROJECT/.source_revision"
find "$PROJECT/scripts" -maxdepth 1 -type f -name '*.sh' -exec chmod +x {} +
cat "$PROJECT/.source_revision"
```

随后更新 Python 可编辑安装并启动服务：

```bash
cd /home/luo/memguardos
test -x .venv/bin/python || python3 -m venv .venv
source .venv/bin/activate
python -m pip install -U pip setuptools wheel
python -m pip install -e '.[dev]'

sudo bash scripts/install_openkylin.sh "$(pwd)"
sudo systemctl daemon-reload
sudo systemctl enable --now memguardd.service
systemctl --no-pager --full status memguardd.service
```

通过标准：服务为 `active (running)`，且日志中没有循环重启、导入错误或权限错误。此时可以继续编译 UI 和运行定向测试，但禁止运行最终证据采集脚本。

---

## 第6章 正式同 Git HEAD 同步

### 6.1 本地提交前门禁

正式同步前先运行与本次修改直接相关的测试。本手册编写时，UI 与 openKylin 原生修改对应：

```powershell
Set-Location 'D:\Luo_docs\竞赛\研创赛\memguardos'
python -m pytest tests/test_web_dashboard_product_ui.py tests/test_openkylin_native.py -q
python scripts/web_dashboard_smoke.py
```

定向测试通过后再执行完整门禁：

```powershell
python -m compileall -q memguard scripts tests
python -m ruff check memguard scripts tests
python -m pytest -q
python scripts/check_docs_links.py
python scripts/web_dashboard_smoke.py
python scripts/release_preflight.py --skip-package
```

任何一条命令非零退出都应先修复。不要因为只改 UI 就跳过 Python 测试，因为 Web 控制台、API、服务和桌面入口共享行为契约。

### 6.2 提交与推送

先检查改动范围：

```powershell
git diff --stat
git diff --check
git status --short
```

确认没有密钥、临时文件、截图缓存或个人数据后提交：

```powershell
git add memguard/api/server.py memguard/service.py memguard/web/dashboard.py
git add native/ukui/tray/memguard_tray.cpp
git add native/ukui/ukcc-memguard/memguard_ukcc_plugin.cpp
git add tests/test_openkylin_native.py tests/test_web_dashboard_product_ui.py
git commit -m "feat: improve openKylin desktop and console experience"
git push origin main
git fetch origin
```

上述 `git add` 列表只适用于当前快照。以后应根据 `git status --short` 调整，不能漏掉本次任务必要文件，也不能顺手加入无关生成物。推送后验证：

```powershell
git status --porcelain
git rev-parse HEAD
git rev-parse origin/main
```

通过标准：第一条无输出，后两条 SHA 完全一致。

### 6.3 生成不可变源码归档

```powershell
$Repo = 'D:\Luo_docs\竞赛\研创赛\memguardos'
$Archive = Join-Path $env:TEMP 'memguardos-HEAD.tar.gz'
$RevisionFile = Join-Path $env:TEMP 'memguardos-source-revision.txt'

Set-Location $Repo
if (git status --porcelain) { throw '工作树不干净，禁止正式同步' }

$Head = (git rev-parse HEAD).Trim()
$OriginHead = (git rev-parse origin/main).Trim()
if ($Head -ne $OriginHead) { throw 'HEAD 与 origin/main 不一致，请先推送或拉取' }

git archive --format=tar.gz --output=$Archive HEAD
Set-Content -LiteralPath $RevisionFile -Value $Head -NoNewline -Encoding ascii

Get-FileHash $Archive -Algorithm SHA256
Get-Content $RevisionFile
scp $Archive $RevisionFile openkylin-memguard:/tmp/
```

### 6.4 openKylin 正式覆盖

通过 SSH 进入虚拟机，在虚拟机终端执行：

```bash
set -euo pipefail
PROJECT=/home/luo/memguardos
INCOMING=/home/luo/memguardos.incoming
ARCHIVE=/tmp/memguardos-HEAD.tar.gz
REVISION_FILE=/tmp/memguardos-source-revision.txt

test "$(id -un)" = "luo"
test -s "$ARCHIVE"
test -s "$REVISION_FILE"
REVISION="$(tr -d '\r\n' < "$REVISION_FILE")"
[[ "$REVISION" =~ ^[0-9a-f]{40}$ ]]

sudo systemctl stop memguardd.service
rm -rf "$INCOMING"
mkdir -p "$INCOMING"
tar -xzf "$ARCHIVE" -C "$INCOMING"

mkdir -p "$PROJECT"
rsync -a --delete \
  --exclude='.venv/' \
  --exclude='.venv-agent/' \
  "$INCOMING/" "$PROJECT/"

rm -rf "$INCOMING"
printf '%s\n' "$REVISION" > "$PROJECT/.source_revision"
rm -rf "$PROJECT/dist" "$PROJECT/native/ukui/build"
find "$PROJECT/scripts" -maxdepth 1 -type f -name '*.sh' -exec chmod +x {} +

cd "$PROJECT"
cat .source_revision
test "$(wc -c < .source_revision)" -eq 41
```

这一流程会删除虚拟机源码目录中不属于当前归档的旧源码文件，但保留 `.venv` 与 `.venv-agent`。它不会触碰 `/home/luo/.memguardos`。正式同步完成后，不要在虚拟机源目录中手工修改代码，否则版本标识将失去真实性。

---

## 第7章 Python 环境、服务与依赖更新

### 7.1 更新虚拟环境

```bash
cd /home/luo/memguardos
test -x .venv/bin/python || python3 -m venv .venv
source .venv/bin/activate
python -m pip install -U pip setuptools wheel
python -m pip install -e '.[dev]'
python -c "import memguard, sys; print(sys.executable); print(memguard.__file__)"
```

通过标准：解释器路径为 `/home/luo/memguardos/.venv/bin/python`，模块路径指向当前项目，而不是旧的 `/opt` 副本或系统 site-packages。

### 7.2 安装 systemd 源码版本

```bash
cd /home/luo/memguardos
source .venv/bin/activate
sudo bash scripts/install_openkylin.sh "$(pwd)"
sudo systemctl daemon-reload
sudo systemctl enable --now memguardd.service
systemctl show memguardd.service \
  --property=LoadState,ActiveState,SubState,MainPID,ControlGroup,MemoryMax,CPUQuotaPerSecUSec \
  --no-pager
```

进一步核验：

```bash
python -m memguard.cli.memctl status
python -m memguard.cli.memctl doctor
journalctl -u memguardd.service -n 80 --no-pager
```

注意：`scripts/install_openkylin.sh` 会把源码同步到 `/opt/memguardos`，系统服务使用 `/usr/bin/python3` 和 `/opt/memguardos`。项目目录中的 `.venv` 主要用于测试和开发命令，两者职责不同。

---

## 第8章 五个 Debian 包构建、安装与生命周期验证

### 8.1 构建五包

在 openKylin 项目目录执行：

```bash
cd /home/luo/memguardos
source .venv/bin/activate
rm -rf dist native/ukui/build
bash scripts/build_deb.sh
bash scripts/verify_deb_package_set.sh
ls -lh dist/*.deb
```

预期生成：

```text
memguardos-core_0.1.0-2_amd64.deb
memguardos-ukui_0.1.0-2_amd64.deb
memguardos-agent-bridge_0.1.0-2_amd64.deb
memguardos-doc_0.1.0-2_all.deb
memguardos_0.1.0-2_all.deb
```

包版本应以 `packaging/debian/packages.json` 和构建输出为准。未来版本变化时，不要继续复制旧文件名，可执行 `ls dist/*.deb` 后调整安装命令。

### 8.2 生命周期验证

```bash
bash scripts/verify_deb_lifecycle.sh
```

该脚本验证五包职责、依赖关系、离线安装、桌面包移除和重装，以及卸载流程不会删除用户记忆数据。脚本失败时应先修包内容或依赖，不能只手工安装绕过。

### 8.3 实机重新安装

安装操作需要管理员权限：

```bash
cd /home/luo/memguardos
sudo -v
sudo apt install --reinstall \
  ./dist/memguardos-core_0.1.0-2_amd64.deb \
  ./dist/memguardos-ukui_0.1.0-2_amd64.deb \
  ./dist/memguardos-agent-bridge_0.1.0-2_amd64.deb \
  ./dist/memguardos-doc_0.1.0-2_all.deb \
  ./dist/memguardos_0.1.0-2_all.deb
```

安装后核验：

```bash
dpkg-query -W -f='${binary:Package}\t${Version}\t${db:Status-Abbrev}\n' \
  memguardos memguardos-core memguardos-ukui memguardos-agent-bridge memguardos-doc
systemctl is-enabled memguardd.service
systemctl is-active memguardd.service
```

五个包均应显示 `ii`，服务应为 `enabled` 和 `active`。若 `apt` 报依赖错误，先运行 `sudo apt --fix-broken install`，再重新执行五包安装；不要单独强装某个 `.deb` 后就宣称五包交付通过。

---

## 第9章 UKUI、Peony 与托盘刷新

### 9.1 编译和安装原生组件

先设置桌面会话变量：

```bash
export DISPLAY=:0
export XDG_RUNTIME_DIR=/run/user/1000
export DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus

cd /home/luo/memguardos
bash scripts/build_ukui_plugins.sh
bash scripts/install_ukui_plugins.sh --no-build
bash scripts/verify_ukui_plugins.sh
```

通过标准：

- Peony 插件和 UKUI 控制中心插件均被编译为 ELF `.so`；
- `ldd` 没有 `not found`；
- 安装目录存在对应插件；
- 托盘可执行文件存在；
- 验证脚本能识别当前桌面进程。

### 9.2 刷新桌面进程

```bash
export DISPLAY=:0
export XDG_RUNTIME_DIR=/run/user/1000
export DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus

peony -q 2>/dev/null || killall peony 2>/dev/null || true
pkill -f '^/usr/bin/ukui-control-center( |$)' || true
pkill -x memguardos-tray || true

nohup peony >/tmp/peony-memguardos.log 2>&1 &
nohup /usr/bin/memguardos-tray >/tmp/memguardos-tray.log 2>&1 &
sleep 3
pgrep -af 'peony|memguardos-tray'
```

UKUI 控制中心无需后台强制启动，用户重新打开“设置”即可加载新插件。若使用 SSH 启动 GUI 后没有显示，优先检查 `DISPLAY`、DBus 和桌面用户是否为 `luo`，不要反复启动多个托盘实例。

### 9.3 人工界面核验

1. 在 Peony 中右击一个本地 `.txt` 或 `.md` 文件，确认出现 MemGuardOS 记忆检查入口。
2. 点击入口，确认先显示扫描预览，再显示敏感风险、候选条目和导入确认，不应直接静默导入。
3. 打开“设置—安全—MemGuardOS 记忆安全”，确认页面可见，普通用户与管理员操作分组清楚。
4. 检查托盘图标，确认菜单能打开记忆安全中心、本地控制台和状态页面。
5. 点击服务检查、审计校验、智能检索检查和系统报告，确认按钮立即给出执行中反馈，完成后显示中文结果或明确处理入口。

界面能打开不等于功能通过。每个按钮都要观察结果区域、服务日志和对应数据变化，避免只做“入口存在”的形式验证。

---

## 第10章 Web 控制台启动与 SSH 隧道

### 10.1 在虚拟机启动 Web 控制台

建议在 `tmux` 中运行，避免 SSH 断开导致服务退出：

```bash
cd /home/luo/memguardos
source .venv/bin/activate
tmux new -s memguard-web
python -m memguard.cli.memctl web --host 127.0.0.1 --port 8765
```

记下终端输出的带 token 地址。按 `Ctrl+B`，再按 `D` 可退出 tmux 而不停止 Web 服务。重新进入：

```bash
tmux attach -t memguard-web
```

### 10.2 在 Windows 建立端口转发

另开一个 PowerShell 窗口执行：

```powershell
ssh -N -L 8765:127.0.0.1:8765 openkylin-memguard
```

该窗口保持运行。在 Windows 浏览器打开虚拟机终端给出的 URL，例如：

```text
http://127.0.0.1:8765/?token=<终端实际输出的令牌>
```

不要把 token 提交到 Git、截图公开区域或文档。控制台默认只监听虚拟机回环地址，通过 SSH 隧道访问比直接开放到局域网更安全。

### 10.3 Web 故障定位

```bash
ss -lntp | grep 8765
curl -fsS http://127.0.0.1:8765/health
tmux capture-pane -pt memguard-web -S -120
```

Windows 检查：

```powershell
Test-NetConnection 127.0.0.1 -Port 8765
```

若端口已被占用，可先查进程再决定是否结束：

```bash
ss -lntp | grep ':8765'
```

不要一看到端口占用就直接 `kill -9`，已有进程可能正是当前控制台。

---

## 第11章 分层测试矩阵

### 11.1 第一层：语法与静态检查

```bash
cd /home/luo/memguardos
source .venv/bin/activate
python -m compileall -q memguard scripts tests
python -m ruff check memguard scripts tests
```

证明范围：代码可被 Python 编译，且通过当前 Ruff 规则。它不能证明业务逻辑正确，也不能代替实机操作。

### 11.2 第二层：定向回归测试

```bash
python -m pytest \
  tests/test_web_dashboard_product_ui.py \
  tests/test_openkylin_native.py -q
python scripts/web_dashboard_smoke.py
```

适合每次 UI、API、托盘和原生插件修改后快速运行。若按钮行为或跳转逻辑改变，还应查找对应测试文件并加入本次相关用例，而不是只依赖这两个固定文件。

### 11.3 第三层：完整自动化测试

```bash
python -m pytest -q
python scripts/check_docs_links.py
python scripts/release_preflight.py --skip-package
```

通过标准：所有命令退出码为 0，Pytest 不得存在 failed 或 error。warning 应逐条解释，不能仅因为测试通过就忽略新的警告。

### 11.4 第四层：openKylin 综合验证

```bash
source .venv/bin/activate
bash scripts/openkylin_verify.sh
```

该脚本覆盖 openKylin 状态、Kylin SDK、检索、OS 机制、审计、评测和相关 smoke test。它能证明命令与系统集成链路，但不能替代 Peony、UKUI、托盘和 Web 控制台的人工点击验收。

### 11.5 第五层：面向用户的完整流程

至少走完一次以下流程：

1. 用户通过 Agent 交互或 Peony 文件扫描产生记忆候选；
2. 系统识别敏感信息并阻断不应进入持久记忆的内容；
3. 用户在 Web 控制台查看记忆列表、来源、编号和相关度；
4. 用一个跨会话问题检索记忆，确认结果按相关度排序且数量受控；
5. 预览精准遗忘影响范围，确认候选编号可以打开详情；
6. 执行遗忘并确认索引、图谱、存储和审计结果；
7. 再次检索同一内容，确认已遗忘内容不再召回；
8. 查看托盘通知、操作记录和审计链，确认用户能理解结果及下一步。

该层是“产品是否真的可用”的关键证据。自动化指标高不能证明导航清楚、反馈及时或用户不会误操作，因此最终验收必须同时保留人工操作记录。

---

## 第12章 最终 openKylin 证据采集

### 12.1 采集前硬门槛

最终证据必须满足：

- Windows 工作树在同步时干净；
- `.source_revision` 是当前正式代码提交的 40 位 SHA；
- 该提交已推送至远端；
- 虚拟机源码来自 `git archive`；
- 五包安装、服务、UKUI 构建和全量测试已通过；
- `sudo -v` 已建立有效授权票据。

快速开发同步后 `.source_revision` 为 `UNCOMMITTED-*`，证据脚本会拒绝执行，这是预期保护行为。

### 12.2 执行采集

在 openKylin 终端执行：

```bash
cd /home/luo/memguardos
source .venv/bin/activate
sudo -v

export MEMGUARD_SOURCE_REVISION="$(cat .source_revision)"
export MEMGUARD_SOURCE_MODE=git_archive
export PYTHON_BIN="$PWD/.venv/bin/python"

[[ "$MEMGUARD_SOURCE_REVISION" =~ ^[0-9a-f]{40}$ ]]

bash scripts/collect_openkylin_native_evidence.sh \
  docs/evidence/openkylin_native_latest
```

采集过程会构建桌面组件、检查 Polkit 和 cgroup、重启服务、构建五包、运行生命周期验证和完整 Pytest，耗时明显长于普通 smoke test。不要在采集中途关闭虚拟机或断电。

### 12.3 证据核验

```bash
cat docs/evidence/openkylin_native_latest/source_revision.json
tail -n 30 docs/evidence/openkylin_native_latest/pytest_openkylin.txt
cat docs/evidence/openkylin_native_latest/service_restart.txt
cat docs/evidence/openkylin_native_latest/debian_packages.txt | tail -n 40
python scripts/check_competition_ready.py
```

重点核对：

- `source_revision` 等于 `.source_revision`；
- `source_mode` 为 `git_archive`；
- `dirty` 为 `false`；
- Pytest 无失败；
- 服务重启返回码为 0 且仍为 active；
- Debian 五包均构建成功；
- competition readiness 通过。

### 12.4 把证据回传 Windows

在 Windows PowerShell 执行：

```powershell
Set-Location 'D:\Luo_docs\竞赛\研创赛\memguardos'
scp -r openkylin-memguard:/home/luo/memguardos/docs/evidence/openkylin_native_latest `
  .\docs\evidence\
```

回传后检查：

```powershell
Get-Content .\docs\evidence\openkylin_native_latest\source_revision.json
git status --short
git diff -- docs/evidence/openkylin_native_latest
```

证据文件是运行生成物，因此回传后 Windows 工作树会变为有修改。正确顺序是“代码提交 C → 按 C 采证 → 把证据提交为 E”。证据中的 `source_revision` 应指向代码提交 C；如果 E 只增加或更新证据文件，这种父提交关联是可审计且合理的。不要为了让证据 SHA 等于 E 而无限重复自引用采集。

---

## 第13章 回滚与数据保护

### 13.1 代码回滚

正式版本出现问题时，在 Windows 选择已知稳定提交重新归档：

```powershell
Set-Location 'D:\Luo_docs\竞赛\研创赛\memguardos'
git log --oneline --decorate -20
$Stable = '<填写已确认的40位稳定提交SHA>'
git archive --format=tar.gz --output="$env:TEMP\memguardos-rollback.tar.gz" $Stable
Set-Content "$env:TEMP\memguardos-source-revision.txt" $Stable -NoNewline -Encoding ascii
scp "$env:TEMP\memguardos-rollback.tar.gz" openkylin-memguard:/tmp/memguardos-HEAD.tar.gz
scp "$env:TEMP\memguardos-source-revision.txt" openkylin-memguard:/tmp/
```

然后按第 6.4 节重新覆盖，并按第 7 至第 9 章重新安装。不要在 `main` 上使用 `git reset --hard` 来“回滚”，以免破坏其他修改和提交历史。

### 13.2 用户数据保护

用户记忆默认位于：

```text
/home/luo/.memguardos/
```

源码同步、重建 `dist/`、重编 UKUI 和重新安装桌面包不应删除该目录。涉及迁移或高风险测试前先备份：

```bash
mkdir -p /home/luo/backups
tar -czf "/home/luo/backups/memguardos-user-data-$(date +%Y%m%d-%H%M%S).tar.gz" \
  -C /home/luo .memguardos
ls -lh /home/luo/backups/
```

测试最终证据时，采集脚本会设置临时 `MEMGUARD_HOME`，不会向真实用户记忆库写入测试记录。

---

## 第14章 常见故障排查

### 14.1 SSH 连接失败

Windows：

```powershell
Test-NetConnection 192.168.73.128 -Port 22
ssh -vvv -o ConnectTimeout=8 openkylin-memguard
```

虚拟机本地：

```bash
ip -br addr
systemctl status ssh --no-pager
ss -lntp | grep ':22'
```

若 IP 改变，更新 `~/.ssh/config` 的 `HostName`。若主机密钥因虚拟机重装变化，先在虚拟机确认指纹，再删除旧条目，不能盲目忽略 SSH 警告。

### 14.2 服务启动慢或反复重启

```bash
systemctl status memguardd.service --no-pager -l
journalctl -u memguardd.service -b -n 150 --no-pager
systemctl show memguardd.service -p NRestarts,ExecMainStatus,MemoryCurrent,MemoryMax
```

常见原因包括 `/opt/memguardos` 未更新、Python 导入失败、运行目录权限错误和资源限制。先修根因，再运行 `sudo systemctl restart memguardd.service`。不要持续重复点击 UI 按钮制造更多并发任务。

### 14.3 UKUI 插件编译失败

```bash
command -v qmake || command -v qmake-qt5
pkg-config --modversion peony
test -f /usr/include/ukcc/interface/interface.h
```

缺依赖时在维护模式安装：

```bash
sudo apt update
sudo apt install build-essential qtbase5-dev libpeony-dev libukcc-dev pkg-config
```

重新构建前删除旧构建目录：

```bash
rm -rf /home/luo/memguardos/native/ukui/build
bash /home/luo/memguardos/scripts/build_ukui_plugins.sh
```

### 14.4 插件已安装但界面没有变化

```bash
export DISPLAY=:0
export XDG_RUNTIME_DIR=/run/user/1000
export DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus

bash scripts/verify_ukui_plugins.sh
sha256sum native/ukui/build/ukcc-memguard/libmemguardos-ukcc-plugin.so \
  /usr/lib/x86_64-linux-gnu/ukui-control-center/libmemguardos-ukcc-plugin.so
sha256sum native/ukui/build/peony-memguard/libpeony-menu-plugin-memguardos.so \
  /usr/lib/x86_64-linux-gnu/peony-extensions/libpeony-menu-plugin-memguardos.so
```

两个对应文件的 SHA256 应一致。随后关闭并重新打开 Peony 与 UKUI 设置。若系统 `/usr` 为只读，安装脚本可能使用 bind mount 演示路径；重启后需重新安装插件或使用正式 `memguardos-ukui` 包。

### 14.5 托盘不显示或重复

```bash
pgrep -af memguardos-tray
tail -n 100 /tmp/memguardos-tray.log
pkill -x memguardos-tray || true
nohup /usr/bin/memguardos-tray >/tmp/memguardos-tray.log 2>&1 &
```

只保留一个托盘进程。若 SSH 环境启动失败，直接在 openKylin 桌面终端运行 `/usr/bin/memguardos-tray`，观察即时错误。

### 14.6 Web 页面慢、按钮长时间执行

```bash
curl -w '\nconnect=%{time_connect}s total=%{time_total}s\n' \
  -fsS http://127.0.0.1:8765/health
top -p "$(pgrep -f 'memctl web' | head -1)"
journalctl -u memguardd.service -n 100 --no-pager
```

先判断是浏览器渲染慢、SSH 隧道问题、后端操作慢，还是 Kylin SDK 首次推理。页面应立即显示执行反馈，耗时任务应异步完成；若所有按钮都一直处于执行中，应检查前端请求是否收到终态响应，而不是只增加超时时间。

### 14.7 最终证据拒绝执行

出现以下错误：

```text
A verified 40-character MEMGUARD_SOURCE_REVISION is required when Git metadata is absent.
```

说明 `.source_revision` 不是正式 SHA，通常是快速开发同步造成。应按第 6 章完成提交和 `git archive` 正式同步，不能手工随便填一个 SHA。

出现 dirty worktree 错误时，说明带 `.git` 的采集环境仍有未提交修改。先检查并提交或清理生成物，不要修改采集脚本绕过门禁。

---

## 第15章 新会话交接清单

新会话接手后，建议把以下内容作为第一条上下文提供给开发助手：

```text
项目：MemGuardOS
Windows 仓库：D:\Luo_docs\竞赛\研创赛\memguardos
openKylin SSH：openkylin-memguard（luo@192.168.73.128）
虚拟机项目：/home/luo/memguardos
实机操作手册：docs/交付文档/MemGuardOS开发接续与openKylin实机操作手册.md

开始前必须：
1. 读取 AGENTS.md、README.md 和本手册；
2. 执行 git status、git rev-parse HEAD、git rev-parse origin/main；
3. 读取虚拟机 /home/luo/memguardos/.source_revision；
4. 有未提交修改时只做快速开发同步，不采最终证据；
5. 最终流程必须是：定向测试 → 全量测试 → 提交推送 → git archive 正式同步 →
   Python/服务更新 → 五包构建安装 → UKUI/Peony/托盘刷新 → openKylin 综合验证 →
   人工用户流程 → 最终证据采集 → 回传证据。
```

每次交接至少记录以下结果：

| 检查项 | 应记录内容 |
| --- | --- |
| Windows 分支与 HEAD | 分支名、40 位 SHA |
| 工作树状态 | clean 或具体修改文件 |
| origin/main | 40 位 SHA、是否已推送 |
| VM `.source_revision` | 40 位 SHA 或开发态标识 |
| 同步方式 | 快速开发同步 / `git archive` 正式同步 |
| 服务状态 | active / failed 及日志摘要 |
| 五包状态 | 包名、版本、是否 `ii` |
| 桌面状态 | Peony、UKUI、托盘是否可见可操作 |
| 测试结果 | 命令、通过数、失败数、警告 |
| 证据路径 | 目录、source revision、校验结果 |
| 未完成事项 | 明确文件、函数、现象和下一步 |

---

## 附录A 一页式执行顺序

```text
Windows 查看 Git 状态
        ↓
存在未提交修改？──是──→ 快速开发同步 → 定向测试/UI 调试 → 禁止最终采证
        │
        否
        ↓
定向测试 → 全量门禁 → 提交 → 推送 → 确认 HEAD = origin/main
        ↓
git archive + 40 位 revision 文件
        ↓
scp 到 openKylin → rsync 正式覆盖 → 写入 .source_revision
        ↓
更新 .venv → install_openkylin.sh → systemd active
        ↓
构建五包 → 生命周期验证 → apt --reinstall 五包
        ↓
编译安装 UKUI/Peony/托盘 → 刷新桌面进程 → 人工点击验收
        ↓
openkylin_verify.sh → 最终证据采集 → competition ready
        ↓
scp 证据回 Windows → 核对 source_revision → 单独提交证据
```

## 附录B 最常用命令速查

```powershell
# Windows：连接
ssh openkylin-memguard

# Windows：Web 隧道
ssh -N -L 8765:127.0.0.1:8765 openkylin-memguard

# Windows：比较版本
git rev-parse HEAD
ssh openkylin-memguard "cat /home/luo/memguardos/.source_revision"
```

```bash
# openKylin：服务
systemctl is-active memguardd.service
journalctl -u memguardd.service -n 80 --no-pager

# openKylin：定向测试
cd /home/luo/memguardos
source .venv/bin/activate
python -m pytest tests/test_web_dashboard_product_ui.py tests/test_openkylin_native.py -q

# openKylin：桌面刷新
export DISPLAY=:0
export XDG_RUNTIME_DIR=/run/user/1000
export DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus
bash scripts/build_ukui_plugins.sh
bash scripts/install_ukui_plugins.sh --no-build
bash scripts/verify_ukui_plugins.sh

# openKylin：综合验证
bash scripts/openkylin_verify.sh
```

---

## 附录C 版本修订记录

| 版本号 | 修订日期 | 修订内容 | 修订人 |
| --- | --- | --- | --- |
| V1.0 | 2026-07-26 | 建立 Windows 与 openKylin 双环境接续、同步、安装、测试、证据和回滚流程 | MemGuardOS 项目组 |
