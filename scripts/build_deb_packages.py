from __future__ import annotations

from pathlib import Path
from typing import Dict, Iterable
import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess


ROOT = Path(__file__).resolve().parents[1]


def build_package_plan(version: str, architecture: str, multiarch: str) -> Dict[str, Dict[str, object]]:
    exact_core = f"memguardos-core (= {version})"
    moved = f"memguardos (<< {version})"
    return {
        "memguardos-core": {
            "architecture": architecture,
            "depends": "python3 (>= 3.9), systemd, policykit-1 | polkitd",
            "recommends": f"memguardos-ukui (= {version}), memguardos-agent-bridge (= {version})",
            "breaks": moved,
            "replaces": moved,
            "description": "MemGuardOS 本地记忆核心服务与系统集成",
            "files": [
                "/usr/bin/memguard",
                "/usr/bin/memctl",
                "/usr/bin/memguardd",
                "/lib/systemd/system/memguardd.service",
                "/lib/systemd/system/memguardd@.service",
                "/usr/lib/systemd/user/memguard-kylin-warmup.service",
                "/usr/share/polkit-1/actions/org.memguardos.policy",
                "/usr/lib/memguardos/memguardos-native-helper.sh",
                "/opt/memguardos/memguard/service.py",
            ],
        },
        "memguardos-ukui": {
            "architecture": architecture,
            "depends": f"{exact_core}, libqt5core5a, libqt5widgets5, peony, ukui-control-center, libpeony3, libukcc3",
            "breaks": moved,
            "replaces": moved,
            "description": "MemGuardOS 的 Peony、UKUI 控制中心与系统托盘",
            "files": [
                f"/usr/lib/{multiarch}/peony-extensions/libpeony-menu-plugin-memguardos.so",
                f"/usr/lib/{multiarch}/ukui-control-center/libmemguardos-ukcc-plugin.so",
                "/usr/bin/memguardos-tray",
                "/etc/xdg/autostart/memguardos-tray.desktop",
                "/usr/share/applications/memguardos-center.desktop",
            ],
        },
        "memguardos-agent-bridge": {
            "architecture": architecture,
            "depends": exact_core,
            "suggests": "python3-openhands-sdk | openhands-sdk",
            "breaks": moved,
            "replaces": moved,
            "description": "MemGuardOS 外部 OS Agent 桥接与 OpenHands 适配入口",
            "files": [
                "/lib/systemd/system/memguard-agent@.service",
                "/usr/lib/memguardos/memguardos-agent-launcher.sh",
                "/opt/memguardos/scripts/memguard_agent_bridge.py",
            ],
        },
        "memguardos-doc": {
            "architecture": "all",
            "depends": "",
            "suggests": exact_core,
            "breaks": moved,
            "replaces": moved,
            "description": "MemGuardOS 中文用户、部署、运维与验收文档",
            "files": [
                "/usr/share/doc/memguardos-doc/README.md",
                "/usr/share/doc/memguardos-doc/copyright",
            ],
        },
        "memguardos": {
            "architecture": "all",
            "depends": ", ".join(
                [
                    exact_core,
                    f"memguardos-ukui (= {version})",
                    f"memguardos-agent-bridge (= {version})",
                    f"memguardos-doc (= {version})",
                ]
            ),
            "description": "MemGuardOS openKylin 完整安装元包",
            "files": [],
        },
    }


def validate_no_overlap(plan: Dict[str, Dict[str, object]]) -> list[str]:
    owners: dict[str, str] = {}
    conflicts = []
    for package, spec in plan.items():
        for path in spec.get("files", []):
            previous = owners.setdefault(str(path), package)
            if previous != package:
                conflicts.append(f"{path}: {previous}, {package}")
    return conflicts


class DebianPackageBuilder:
    def __init__(self, root: Path, output_dir: Path, build_root: Path, version: str, architecture: str, multiarch: str):
        self.root = root
        self.output_dir = output_dir
        self.build_root = build_root
        self.version = version
        self.architecture = architecture
        self.multiarch = multiarch
        self.plan = build_package_plan(version, architecture, multiarch)
        self.outputs: list[dict[str, object]] = []

    def build(self, with_ukui: bool = True) -> dict[str, object]:
        if validate_no_overlap(self.plan):
            raise RuntimeError("package plan contains file ownership conflicts")
        self._require("dpkg-deb")
        if with_ukui:
            subprocess.run(["bash", "scripts/build_ukui_plugins.sh"], cwd=self.root, check=True)
        self.build_root.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        stages = {name: self._new_stage(name) for name in self.plan}
        self._stage_core(stages["memguardos-core"])
        self._stage_ukui(stages["memguardos-ukui"], required=with_ukui)
        self._stage_agent(stages["memguardos-agent-bridge"])
        self._stage_docs(stages["memguardos-doc"])
        self._verify_staged_ownership(stages)
        for package, stage in stages.items():
            self._write_metadata(package, stage)
            _normalize_stage_permissions(stage)
            self._build_one(package, stage)
        manifest = {
            "schema": "memguardos-debian-package-set-v1",
            "version": self.version,
            "architecture": self.architecture,
            "multiarch": self.multiarch,
            "packages": self.outputs,
        }
        manifest_path = self.output_dir / "memguardos_debian_packages.json"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        _write_sha256(self.output_dir / "memguardos_debian_packages_SHA256SUMS.txt", [Path(item["path"]) for item in self.outputs])
        return manifest

    def _new_stage(self, package: str) -> Path:
        architecture = str(self.plan[package]["architecture"])
        stage = self.build_root / f"{package}_{self.version}_{architecture}"
        if stage.exists():
            shutil.rmtree(stage)
        (stage / "DEBIAN").mkdir(parents=True)
        return stage

    def _stage_core(self, stage: Path) -> None:
        self._copy_tree(self.root / "memguard", stage / "opt/memguardos/memguard")
        self._copy_file(self.root / "pyproject.toml", stage / "opt/memguardos/pyproject.toml")
        self._copy_file(self.root / "README.md", stage / "opt/memguardos/README.md")
        self._copy_file(self.root / "LICENSE", stage / "opt/memguardos/LICENSE")
        core_scripts = [
            "warmup_kylin_sdk.sh",
            "install_auditd_rules.sh",
            "run_bpf_lsm_enforcer.sh",
            "generate_bpf_lsm_policy.py",
        ]
        for name in core_scripts:
            self._copy_file(self.root / "scripts" / name, stage / "opt/memguardos/scripts" / name, executable=name.endswith(".sh"))
        self._copy_tree(self.root / "native/bpf_lsm", stage / "opt/memguardos/native/bpf_lsm")
        self._write_wrapper(stage / "usr/bin/memguard", "memguard.cli.memguard")
        self._write_wrapper(stage / "usr/bin/memctl", "memguard.cli.memctl")
        self._write_wrapper(stage / "usr/bin/memguardd", "memguard.daemon")
        for unit in ("memguardd.service", "memguardd@.service"):
            self._copy_file(self.root / "scripts" / unit, stage / "lib/systemd/system" / unit)
        self._copy_file(self.root / "scripts/memguard-kylin-warmup.service", stage / "usr/lib/systemd/user/memguard-kylin-warmup.service")
        self._copy_file(self.root / "scripts/memguardos-native-helper.sh", stage / "usr/lib/memguardos/memguardos-native-helper.sh", executable=True)
        self._copy_file(self.root / "packaging/polkit/org.memguardos.policy", stage / "usr/share/polkit-1/actions/org.memguardos.policy")

    def _stage_ukui(self, stage: Path, required: bool) -> None:
        paths = {
            self.root / "native/ukui/build/peony-memguard/libpeony-menu-plugin-memguardos.so": stage / f"usr/lib/{self.multiarch}/peony-extensions/libpeony-menu-plugin-memguardos.so",
            self.root / "native/ukui/build/ukcc-memguard/libmemguardos-ukcc-plugin.so": stage / f"usr/lib/{self.multiarch}/ukui-control-center/libmemguardos-ukcc-plugin.so",
            self.root / "native/ukui/build/tray/memguardos-tray": stage / "usr/bin/memguardos-tray",
        }
        for source, destination in paths.items():
            if required and not source.exists():
                raise FileNotFoundError(f"required UKUI artifact missing: {source}")
            if source.exists():
                self._copy_file(source, destination, executable=True)
        self._copy_file(self.root / "packaging/desktop/memguardos-tray.desktop", stage / "etc/xdg/autostart/memguardos-tray.desktop")
        self._copy_file(self.root / "packaging/desktop/memguardos-center.desktop", stage / "usr/share/applications/memguardos-center.desktop")

    def _stage_agent(self, stage: Path) -> None:
        self._copy_file(self.root / "scripts/memguard-agent@.service", stage / "lib/systemd/system/memguard-agent@.service")
        self._copy_file(
            self.root / "scripts/memguardos-agent-launcher.sh",
            stage / "usr/lib/memguardos/memguardos-agent-launcher.sh",
            executable=True,
        )
        for name in ("memguard_agent_bridge.py", "run_openhands_memguard_demo.py", "run_openhands_behavior_suite.py"):
            self._copy_file(self.root / "scripts" / name, stage / "opt/memguardos/scripts" / name)

    def _stage_docs(self, stage: Path) -> None:
        destination = stage / "usr/share/doc/memguardos-doc"
        self._copy_file(self.root / "README.md", destination / "README.md")
        self._copy_file(self.root / "LICENSE", destination / "copyright")
        for name in ("PROJECT_INDEX.md", "CHANGELOG.md", "PRODUCT.md", "DESIGN.md"):
            source = self.root / name
            if source.exists():
                self._copy_file(source, destination / name)
        docs_target = destination / "docs"
        shutil.copytree(
            self.root / "docs",
            docs_target,
            ignore=shutil.ignore_patterns("evidence", "research", "superpowers", "*.docx", "*.pdf", "*.png", "*.svg"),
        )

    def _verify_staged_ownership(self, stages: Dict[str, Path]) -> None:
        owners: dict[str, str] = {}
        conflicts = []
        for package, stage in stages.items():
            for path in stage.rglob("*"):
                if not path.is_file() or "DEBIAN" in path.relative_to(stage).parts:
                    continue
                relative = "/" + path.relative_to(stage).as_posix()
                previous = owners.setdefault(relative, package)
                if previous != package:
                    conflicts.append(f"{relative}: {previous}, {package}")
        if conflicts:
            raise RuntimeError("staged file ownership conflicts:\n" + "\n".join(conflicts))

    def _write_metadata(self, package: str, stage: Path) -> None:
        spec = self.plan[package]
        fields = [
            f"Package: {package}",
            f"Version: {self.version}",
            "Section: utils",
            "Priority: optional",
            f"Architecture: {spec['architecture']}",
            "Maintainer: MemGuardOS Project Team <memguardos@example.com>",
        ]
        for field, key in (("Depends", "depends"), ("Recommends", "recommends"), ("Suggests", "suggests"), ("Breaks", "breaks"), ("Replaces", "replaces")):
            if spec.get(key):
                fields.append(f"{field}: {spec[key]}")
        fields.extend(
            [
                "Homepage: https://github.com/Alexd-star/memguardos",
                f"Description: {spec['description']}",
                " MemGuardOS 为 openKylin OS Agent 提供本地记忆提取、检索、治理与精准遗忘能力。",
            ]
        )
        (stage / "DEBIAN/control").write_text("\n".join(fields) + "\n", encoding="utf-8")
        files = [path for path in stage.rglob("*") if path.is_file() and "DEBIAN" not in path.relative_to(stage).parts]
        md5_lines = [
            f"{hashlib.md5(path.read_bytes(), usedforsecurity=False).hexdigest()}  {path.relative_to(stage).as_posix()}"
            for path in sorted(files)
        ]
        (stage / "DEBIAN/md5sums").write_text("\n".join(md5_lines) + ("\n" if md5_lines else ""), encoding="utf-8")
        if package == "memguardos-core":
            self._write_maintainer_script(
                stage / "DEBIAN/postinst",
                "\n".join(
                    [
                        "command -v systemctl >/dev/null 2>&1 && systemctl daemon-reload || true",
                        "command -v systemctl >/dev/null 2>&1 && systemctl enable --now memguardd.service >/dev/null 2>&1 || true",
                        "command -v systemctl >/dev/null 2>&1 && systemctl --global disable memguard-kylin-warmup.service >/dev/null 2>&1 || true",
                        "command -v systemctl >/dev/null 2>&1 && systemctl --global enable memguard-kylin-warmup.service >/dev/null 2>&1 || true",
                    ]
                ),
            )
            self._write_maintainer_script(
                stage / "DEBIAN/prerm",
                "\n".join(
                    [
                        "# 用户数据保留：卸载服务包只停止服务，不删除 namespace、段文件、SQLite、审计链或用户状态目录。",
                        '[ "$1" != remove ] || { command -v systemctl >/dev/null 2>&1 && systemctl stop memguardd.service >/dev/null 2>&1 || true; }',
                    ]
                ),
            )
            self._write_maintainer_script(stage / "DEBIAN/postrm", "command -v systemctl >/dev/null 2>&1 && systemctl daemon-reload || true")
        elif package == "memguardos-ukui":
            desktop_refresh = "\n".join(
                [
                    "command -v ldconfig >/dev/null 2>&1 && ldconfig || true",
                    "command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database /usr/share/applications >/dev/null 2>&1 || true",
                    "command -v xdg-desktop-menu >/dev/null 2>&1 && xdg-desktop-menu forceupdate >/dev/null 2>&1 || true",
                    "command -v gtk-update-icon-cache >/dev/null 2>&1 && gtk-update-icon-cache -q /usr/share/icons/hicolor >/dev/null 2>&1 || true",
                    "command -v peony >/dev/null 2>&1 && peony -q >/dev/null 2>&1 || true",
                    "command -v pkill >/dev/null 2>&1 && pkill -x ukui-control-center >/dev/null 2>&1 || true",
                ]
            )
            self._write_maintainer_script(stage / "DEBIAN/postinst", desktop_refresh)
            self._write_maintainer_script(stage / "DEBIAN/postrm", desktop_refresh)

    def _build_one(self, package: str, stage: Path) -> None:
        architecture = str(self.plan[package]["architecture"])
        output = (self.output_dir / f"{package}_{self.version}_{architecture}.deb").resolve()
        subprocess.run(["dpkg-deb", "--build", "--root-owner-group", str(stage), str(output)], check=True)
        self.outputs.append(
            {
                "package": package,
                "version": self.version,
                "architecture": architecture,
                "path": str(output),
                "bytes": output.stat().st_size,
                "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
            }
        )

    @staticmethod
    def _copy_file(source: Path, destination: Path, executable: bool = False) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        if executable:
            destination.chmod(destination.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    @staticmethod
    def _copy_tree(source: Path, destination: Path) -> None:
        shutil.copytree(source, destination, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "build"))

    @staticmethod
    def _write_wrapper(path: Path, module: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "#!/bin/sh\nexport PYTHONPATH=/opt/memguardos${PYTHONPATH:+:$PYTHONPATH}\nexec /usr/bin/python3 -m " + module + ' "$@"\n',
            encoding="utf-8",
        )
        path.chmod(0o755)

    @staticmethod
    def _write_maintainer_script(path: Path, body: str) -> None:
        path.write_text("#!/bin/sh\nset -e\n" + body + "\nexit 0\n", encoding="utf-8")
        path.chmod(0o755)

    @staticmethod
    def _require(command: str) -> None:
        if not shutil.which(command):
            raise RuntimeError(f"required command not found: {command}")


def _project_version(root: Path) -> str:
    text = (root / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    return match.group(1) if match else "0.1.0"


def _command_output(command: Iterable[str], fallback: str) -> str:
    try:
        completed = subprocess.run(list(command), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, check=False)
        return completed.stdout.strip() or fallback
    except OSError:
        return fallback


def _write_sha256(path: Path, files: Iterable[Path]) -> None:
    lines = [f"{hashlib.sha256(item.read_bytes()).hexdigest()}  {item.name}" for item in files]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _normalize_stage_permissions(stage: Path) -> None:
    for path in [stage, *sorted(stage.rglob("*"))]:
        if path.is_symlink():
            continue
        mode = 0o755 if path.is_dir() or path.stat().st_mode & 0o111 else 0o644
        path.chmod(mode)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the split MemGuardOS Debian package set")
    parser.add_argument("--output-dir", default="dist")
    parser.add_argument("--build-root", default="dist/deb-split")
    parser.add_argument("--version", default="")
    parser.add_argument("--revision", default=os.environ.get("DEB_REVISION", "2"))
    parser.add_argument("--without-ukui", action="store_true")
    parser.add_argument("--print-plan", action="store_true")
    args = parser.parse_args()
    upstream = args.version or _project_version(ROOT)
    version = upstream if "-" in upstream else f"{upstream}-{args.revision}"
    architecture = _command_output(["dpkg", "--print-architecture"], "amd64")
    multiarch = _command_output(["dpkg-architecture", "-qDEB_HOST_MULTIARCH"], "x86_64-linux-gnu")
    plan = build_package_plan(version, architecture, multiarch)
    if args.print_plan:
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return 0
    manifest = DebianPackageBuilder(
        ROOT,
        (ROOT / args.output_dir).resolve(),
        (ROOT / args.build_root).resolve(),
        version,
        architecture,
        multiarch,
    ).build(with_ukui=not args.without_ukui)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
