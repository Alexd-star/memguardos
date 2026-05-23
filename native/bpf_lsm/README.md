# MemGuardOS BPF LSM 强制拦截增强

该目录提供可选的 Linux BPF LSM 原型，用于在内核 `file_open` LSM hook 上证明强制拦截能力。默认演示只阻断专用测试文件 `/tmp/memguardos_lsm_secret`，避免影响登录、SSH、sudo 或 PAM。生产 denylist 代码保留在 `MEMGUARDOS_PRODUCTION_DENYLIST` 编译开关后，可扩展到 SSH 私钥、AWS/Kubernetes 凭据和 `.env` 文件等高风险路径；也可以通过 JSON 策略生成 `memguard_lsm_policy.h`，形成可审阅、可复现的静态 denylist。

该增强不是默认主路径，原因是加载 BPF LSM 需要 root、BTF、libbpf 开发头、clang，并且内核 active LSM 列表必须包含 `bpf`。默认提交链路仍以 auditd 真实审计事件和 MemGuardOS 高风险路径阻断为稳定主路径。

## 构建与运行

```bash
cd native/bpf_lsm
make probe
make
sudo ./build/memguard_lsm_loader ./build/memguard_lsm.bpf.o
```

另一个终端中访问专用测试文件：

```bash
echo secret >/tmp/memguardos_lsm_secret
chmod 0644 /tmp/memguardos_lsm_secret
cat /tmp/memguardos_lsm_secret
```

如果 BPF LSM 成功加载，访问会返回 `Permission denied`，并在 trace pipe 或内核日志中出现 `memguardos denied open`。

如需在隔离测试机上启用生产 denylist：

```bash
make clean all BPF_EXTRA_CFLAGS=-DMEMGUARDOS_PRODUCTION_DENYLIST
```

如需按项目策略生成可审阅 denylist：

```bash
python3 scripts/generate_bpf_lsm_policy.py --policy data/demo/bpf_lsm_policy.json
make -C native/bpf_lsm clean all
```

策略文件只接受绝对路径片段，生成脚本会拒绝空字符串、通配符、控制字符和过长路径。这样做的目的不是把 BPF LSM 变成无人审查的动态规则系统，而是在赛题演示和真实部署之间增加一道可评审的策略生成层。

## 边界

- 这是可选内核级增强，不替代默认 auditd 证据链。
- 默认运行期间只阻断 `/tmp/memguardos_lsm_secret`；停止 loader 后 BPF link 自动释放。
- 评测和演示中必须明确说明当前是否真的 attach 成功，不能仅凭源码宣称已在目标环境启用。
