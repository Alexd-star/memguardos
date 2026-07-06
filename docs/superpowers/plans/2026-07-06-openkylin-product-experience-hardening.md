# openKylin Product Experience Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 openKylin 桌面集成从“可见、可用”继续提升为普通用户和管理员都能理解的本机产品体验。

**Architecture:** 以服务层能力为中心，Peony、UKUI 控制中心、托盘和 CLI 共用同一组可审计动作。普通用户入口只呈现服务健康、文件安全、敏感阻断和遗忘入口；管理员入口保留 systemd、cgroup、auditd、BPF、Polkit、Kylin SDK 和证据导出。

**Tech Stack:** Python 3.14/3.12、pytest、Qt Widgets、UKUI、Peony、systemd、Polkit、Debian packaging、openKylin。

---

### Task 1: Peony 本次导入直接撤销

**Files:**
- Modify: `memguard/service.py`
- Modify: `memguard/desktop/scan_manifest.py`
- Modify: `memguard/cli/memctl.py`
- Modify: `native/ukui/peony-memguard/memguard_peony_plugin.cpp`
- Modify: `native/ukui/peony-memguard/memguard_peony_plugin.h`
- Test: `tests/test_openkylin_native.py`

- [x] **Step 1: Write failing test**

Add `test_desktop_forget_import_cli_targets_selected_memory_ids`, requiring `memctl desktop forget-import --memory-id <id> --compact` to delete only the memory IDs created by the latest Peony import and return verification data.

- [x] **Step 2: Run red test**

Run:

```bash
python -m pytest tests/test_openkylin_native.py::test_desktop_forget_import_cli_targets_selected_memory_ids -q
```

Expected before implementation: invalid desktop subcommand.

- [x] **Step 3: Implement direct memory-id forgetting**

Add `MemGuardService.forget_memory_ids`, `forget_imported_memories`, and `desktop forget-import`. The method writes tombstones, marks metadata deleted, optionally compacts mmap storage, verifies each memory ID and writes audit information.

- [x] **Step 4: Wire Peony result dialog**

Change Peony's “撤销导入 / 立即遗忘” button to call `desktop forget-import` with the stored IDs from the current batch. “查看结果” now opens the local console audit view instead of an unscoped page.

- [x] **Step 5: Verify**

Run:

```bash
python -m pytest tests/test_openkylin_native.py::test_desktop_forget_import_cli_targets_selected_memory_ids tests/test_openkylin_native.py::test_peony_plugin_uses_preview_confirmation_contract -q
```

Expected: both tests pass.

### Task 2: 托盘定向跳转

**Files:**
- Modify: `native/ukui/tray/memguard_tray.cpp`
- Modify: `native/ukui/ukcc-memguard/memguard_ukcc_plugin.cpp`
- Modify: `native/ukui/ukcc-memguard/memguard_ukcc_plugin.h`
- Test: `tests/test_openkylin_native.py`

- [x] **Step 1: Write failing test**

Extend tray test to require SDK、审计、敏感阻断定向入口 and a product-facing sensitive block label.

- [x] **Step 2: Add tray menu actions**

Add “查看端侧向量能力”、“查看审计状态”、“查看最近一次阻断记录”。 SDK and audit write a small target file under `XDG_RUNTIME_DIR`; sensitive block opens the local console audit fragment.

- [x] **Step 3: Add control center target consumption**

UKUI plugin reads `memguardos-ukcc-target` on startup, switches to the matching tab, and records a concise operation-history entry.

- [x] **Step 4: Verify**

Run:

```bash
python -m pytest tests/test_openkylin_native.py::test_ukui_tray_follows_system_theme_and_deduplicates_notifications -q
```

Expected: pass.

### Task 3: 控制中心产品化

**Files:**
- Modify: `native/ukui/ukcc-memguard/memguard_ukcc_plugin.cpp`
- Test: `tests/test_openkylin_native.py`

- [x] **Step 1: Write failing test**

Add a source-level product-facing contract: ordinary user wording, administrator tools wording, no debug/stack wording in the primary UI section, and clear fallback copy.

- [x] **Step 2: Refine UX copy and structure**

Rename task groups to “普通用户常用入口”、“普通用户关注项”、“管理员工具：服务控制/端侧能力/安全审计”。 Keep ordinary user actions small and move high-risk operations to administrator/system maintenance tabs.

- [x] **Step 3: Improve SDK state**

Show Kylin SDK as enhancement, not a fault. The visible UI says fallback does not affect daily memory retrieval; detailed timestamps remain in diagnostics.

- [x] **Step 4: Verify**

Run:

```bash
python -m pytest tests/test_openkylin_native.py::test_ukui_control_center_keeps_user_ui_product_facing -q
```

Expected: pass.

### Task 4: Kylin SDK latency budget and warmup state

**Files:**
- Modify: `memguard/retrieval/hybrid.py`
- Modify: `memguard/os_guard/openkylin_native.py`
- Modify: `native/ukui/ukcc-memguard/memguard_ukcc_plugin.cpp`

- [x] **Step 1: Add latency-budget fallback**

`MEMGUARD_KYLIN_RERANK_BUDGET_MS` defaults to 160 ms for `auto` mode. If SDK rerank exceeds the budget, the retriever returns stable hash/FTS/graph results and records a degradation reason. Explicit `kylin` / `kylin_sdk` modes remain available for evidence probes.

- [x] **Step 2: Preserve warmup success/failure state**

`openkylin-status` now exposes last success/failure fields in `product_state`. Ordinary UI summarizes them without showing raw diagnostic timing.

### Task 5: Native install polish

**Files:**
- Modify: `scripts/build_deb_packages.py`
- Test: `tests/test_packaging.py`

- [x] **Step 1: Refresh desktop caches**

`memguardos-ukui` maintainer scripts run `ldconfig`, `update-desktop-database`, `xdg-desktop-menu forceupdate`, `gtk-update-icon-cache`, `peony -q`, and restart `ukui-control-center` if present.

- [x] **Step 2: Preserve user data**

No maintainer script deletes namespace homes, mmap segments, SQLite metadata, audit chains or user state.

### Task 6: Verification

- [x] **Step 1: Run focused local tests**

```bash
python -m pytest tests/test_openkylin_native.py tests/test_packaging.py -q
```

- [x] **Step 2: Run full local gates**

```bash
python -m pytest -q
python -m ruff check memguard tests scripts
python scripts/check_docs_links.py
python scripts/check_competition_ready.py
```

Observed on 2026-07-06: focused tests `54 passed`; full pytest `283 passed, 1 skipped`; ruff, Markdown links and competition readiness all passed.

- [ ] **Step 3: Apply on openKylin**

Sync the changed files to `/home/luo/memguardos-product-head`, rebuild five Debian packages, reinstall with `apt install --reinstall`, restart the tray in the current UKUI session, and collect openKylin native evidence for the committed code revision.
