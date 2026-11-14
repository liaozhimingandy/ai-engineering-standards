#!/usr/bin/env python3
"""发布前检查（Phase 6，任务书 §七）。

在质量门禁（quality_gate.run_gate）结果之上叠加**发布专项检查**，全部复用
Phase 5 验证器与 Phase 6 门禁，不重复实现任何校验逻辑：

  1. VERSION 格式（SemVer）——          复用 version_validator
  2. CHANGELOG 含当前版本段 ——         复用 version_validator（发布时升为 ERROR）
  3. 核心规范完整 / Skills / 适配器 ——  复用 quality_gate 四类门禁结果
  4. 安装器测试 ——                     可选 --run-tests 真实执行（subprocess），
                                       默认不执行并**明确标注未执行**，绝不假称通过
  5. 无未处理 ERROR ——                 汇总门禁
  6. 发布文件完整 ——                   REQUIRED_FILES / BUNDLE_ROOT_FILES / Skills / 适配器
  7. Git Tag 与 VERSION 一致 ——        「适用时」：无 git 仓库 / 无 tag 时列为
                                       UNVERIFIABLE（无法执行），不伪装通过

用法：
    python tooling/scripts/release_check.py
    python tooling/scripts/release_check.py --strict
    python tooling/scripts/release_check.py --version 1.0.0
    python tooling/scripts/release_check.py --strict --run-tests

退出码：0 = 所有可执行检查通过（无法执行的项单独列出，不影响 0）；
        1 = 存在 ERROR 或版本不符；2 = 工具执行失败。
说明："无法执行"（如非 git 仓库下的 tag 检查）**不计入通过、也不伪装为失败**，
以独立清单呈现并要求人工/在具备条件的 CI 环境补验。
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tooling"))
sys.path.insert(0, str(Path(__file__).resolve().parent))  # 允许 import quality_gate

from validators import common as C                # noqa: E402
from validators import targets as T               # noqa: E402
from validators import version_validator as VV    # noqa: E402
import quality_gate as QG                         # noqa: E402


VERIFIABLE = "VERIFIABLE"
UNVERIFIABLE = "UNVERIFIABLE"


@dataclass
class CheckItem:
    name: str
    status: str              # PASS / FAIL / UNVERIFIABLE / SKIPPED
    detail: str = ""


@dataclass
class ReleaseReport:
    repo: str
    requested_version: str = ""
    strict: bool = False
    spec_version: str = ""
    items: list[CheckItem] = field(default_factory=list)
    gate: QG.GateResult | None = None
    tool_error: str = ""

    def add(self, name: str, status: str, detail: str = "") -> None:
        self.items.append(CheckItem(name, status, detail))

    @property
    def errors(self) -> int:
        return sum(1 for i in self.items if i.status == "FAIL")

    @property
    def unverifiable(self) -> list[CheckItem]:
        return [i for i in self.items if i.status == UNVERIFIABLE]

    @property
    def passed(self) -> bool:
        return (not self.tool_error and self.errors == 0
                and self.gate is not None and self.gate.passed)

    def render(self) -> str:
        lines = ["=== Phase 6 发布前检查（release_check）==="]
        lines.append(f"仓库: {self.repo}  |  严格模式: {'是' if self.strict else '否'}"
                     f"  |  VERSION: {self.spec_version}")
        if self.requested_version:
            lines.append(f"目标发版版本: --version {self.requested_version}")
        lines.append("")
        for it in self.items:
            tag = {
                "PASS": "[通过]    ",
                "FAIL": "[失败]    ",
                UNVERIFIABLE: "[无法执行]",
                "SKIPPED": "[未执行]  ",
            }.get(it.status, f"[{it.status}]")
            lines.append(f"  {tag} {it.name}" + (f" — {it.detail}" if it.detail else ""))
        if self.gate is not None:
            lines.append("")
            lines.append(f"  质量门禁汇总: {self.gate.total_errors} ERROR / "
                         f"{self.gate.total_warnings} WARNING / "
                         f"升级 {self.gate.promoted} 条")
            for name in QG.GATES:
                rep = self.gate.groups.get(name)
                if rep is not None:
                    lines.append(f"    · {name}: {rep.count(C.ERROR)} ERROR / "
                                 f"{rep.count(C.WARNING)} WARNING")
        uv = self.unverifiable
        if uv:
            lines.append("")
            lines.append("  以下检查在当前环境无法执行，须人工 / 在具备条件的 CI 补验，"
                         "未计为通过：")
            for it in uv:
                lines.append(f"    · {it.name}: {it.detail}")
        if self.tool_error:
            lines.append(f"\n  [工具失败] {self.tool_error}")
        if not self.passed:
            verdict = "不可发布（存在失败项）"
        elif uv:
            verdict = "可执行项全部通过，但存在无法执行项——须补验后方可发布"
        else:
            verdict = "全部检查通过，可发布"
        lines.append(f"\n--- 发布检查结论: {self.errors} 项失败 / "
                     f"{len(uv)} 项无法执行 -> {verdict} ---")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# 发布专项检查
# ---------------------------------------------------------------------------

def _check_version(repo: Path, rep: ReleaseReport) -> VV.VersionInfo:
    """复用版本校验器采集；把它的 ERROR/WARNING 映射为发布检查项。"""
    tmp = C.Report("version")
    info = VV.validate_versions(repo, tmp)
    rep.spec_version = info.spec
    errs = [i for i in tmp.issues if i.level == C.ERROR]
    warns = [i for i in tmp.issues if i.level == C.WARNING]
    rep.add("VERSION 为合法 SemVer", "FAIL" if errs else "PASS",
            "; ".join(i.message for i in errs) if errs else info.spec)
    # CHANGELOG 含当前版本：严格/发布语境下缺失视为失败（日常 quality_gate 仅 WARNING）
    if info.changelog_has_current:
        rep.add(f"CHANGELOG 含当前版本段 [{info.spec}]", "PASS")
    else:
        rep.add(f"CHANGELOG 含当前版本段 [{info.spec}]", "FAIL",
                "发布前必须在 CHANGELOG.md 新增该版本的已发版段")
    # 安装器版本双源一致
    ok_iv = (info.installer_code == info.installer_config
             and VV.is_semver(info.installer_code))
    rep.add("安装器版本双源一致（targets.py ↔ config）", "PASS" if ok_iv else "FAIL",
            f"{info.installer_code} / {info.installer_config}")
    # README badge 一致
    ok_badge = bool(info.readme_badge) and info.readme_badge == info.spec
    rep.add("README version badge 与 VERSION 一致", "PASS" if ok_badge else "FAIL",
            f"badge={info.readme_badge} / VERSION={info.spec}")
    for w in warns:
        if "CHANGELOG" not in w.message:
            rep.add("版本来源提示", "PASS", w.message)
    return info


def _check_requested_version(rep: ReleaseReport) -> None:
    if not rep.requested_version:
        return
    if not VV.is_semver(rep.requested_version):
        rep.add(f"--version {rep.requested_version!r} 为合法 SemVer", "FAIL")
    elif rep.requested_version != rep.spec_version:
        rep.add(f"--version {rep.requested_version} 与 VERSION {rep.spec_version} 一致", "FAIL",
                "发布版本必须与 VERSION 完全一致")
    else:
        rep.add(f"--version {rep.requested_version} 与 VERSION 一致", "PASS")


def _check_release_files(repo: Path, rep: ReleaseReport) -> None:
    """发布产物完整性：复用 version_validator 的单一清单，不在两处各写一份。"""
    missing = VV.check_release_files(repo)
    if missing:
        rep.add("发布产物文件完整", "FAIL", f"缺失 {len(missing)} 项: " + ", ".join(missing[:8]))
    else:
        rep.add("发布产物文件完整（规范 / Skills / 适配器 / 安装器 / 测试 / 文档）", "PASS")


def _check_git_tag(repo: Path, rep: ReleaseReport) -> None:
    """Git Tag 与 VERSION 一致（适用时）。无法访问 git / 无 tag 时标 UNVERIFIABLE。"""
    def git(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["git", *args], cwd=str(repo), capture_output=True,
                              text=True, timeout=10)

    try:
        inside = git("rev-parse", "--is-inside-work-tree")
    except (OSError, subprocess.SubprocessError) as exc:
        rep.add("Git Tag 与 VERSION 一致", UNVERIFIABLE,
                f"无法调用 git: {exc}")
        return
    if inside.returncode != 0 or inside.stdout.strip().lower() != "true":
        rep.add("Git Tag 与 VERSION 一致", UNVERIFIABLE,
                "当前目录不是 Git 仓库，无法核对 Tag；发布时须在已打 Tag 的 CI 环境运行")
        return
    try:
        desc = git("describe", "--tags", "--exact-match", "HEAD")
    except (OSError, subprocess.SubprocessError) as exc:
        rep.add("Git Tag 与 VERSION 一致", UNVERIFIABLE, f"git describe 失败: {exc}")
        return
    if desc.returncode != 0 or not desc.stdout.strip():
        rep.add("Git Tag 与 VERSION 一致", UNVERIFIABLE,
                "无精确匹配的 Tag（尚未打 Tag）；打 Tag 后须重新核对与 VERSION 一致")
        return
    tag = desc.stdout.strip()
    tag_ver = VV.version_from_tag(tag)   # 单一来源：Tag 必须符合版本号规则
    if tag_ver is None:
        rep.add("Git Tag 符合版本号规则", "FAIL",
                f"Tag={tag!r} 不符合 v<MAJOR.MINOR.PATCH>（可选 v 前缀）")
    elif tag_ver != rep.spec_version:
        rep.add("Git Tag 与 VERSION 一致", "FAIL", f"Tag={tag} / VERSION={rep.spec_version}")
    else:
        rep.add(f"Git Tag {tag} 与 VERSION {rep.spec_version} 一致", "PASS")


def _check_tests(repo: Path, rep: ReleaseReport, *, run_tests: bool) -> None:
    """安装器 / 全量测试：默认不执行并明确标注；--run-tests 才真实执行。"""
    if not run_tests:
        rep.add("安装器与全量自动化测试", "SKIPPED",
                "未指定 --run-tests，本次未执行；发布前须运行 "
                "`python -m unittest discover -s tooling/tests -v` 并全部通过")
        return
    try:
        p = subprocess.run(
            [sys.executable, "-u", "-m", "unittest", "discover", "-s", "tooling/tests"],
            cwd=str(repo), capture_output=True, text=True, timeout=600,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        rep.add("安装器与全量自动化测试", "UNVERIFIABLE", f"无法执行测试: {exc}")
        return
    tail = (p.stderr or p.stdout).strip().splitlines()[-3:]
    detail = " | ".join(t.strip() for t in tail if t.strip())
    rep.add("安装器与全量自动化测试", "PASS" if p.returncode == 0 else "FAIL", detail)


def run_release_check(repo: Path, *, strict: bool = False,
                      version: str = "", run_tests: bool = False) -> ReleaseReport:
    """执行发布前检查，返回结果对象（供 CLI 与测试复用）。"""
    repo = Path(repo)
    rep = ReleaseReport(repo=str(repo), requested_version=version, strict=strict)
    try:
        if not (repo / "VERSION").is_file() or not (repo / "core").is_dir():
            rep.tool_error = "无法定位规范仓库（缺 VERSION 或 core/）"
            return rep
        # 1) 版本专项（SemVer / CHANGELOG / 安装器双源 / badge）
        rep.gate = None
        _check_version(repo, rep)
        _check_requested_version(rep)
        # 2) 复用质量门禁的四类检查（不重造）
        gate = QG.run_gate(repo, strict=strict)
        rep.gate = gate
        if gate.tool_error:
            rep.tool_error = gate.tool_error
            return rep
        gate_rename = {
            QG.GATE_CORE: "公共规范完整（结构 / Markdown / 引用 / core 六段）",
            QG.GATE_SKILLS: "Skills 全部符合 Agent Skills 规范",
            QG.GATE_ADAPTERS: "多工具适配器一致性",
            QG.GATE_INSTALLER: "安装器版本 / 清单 / 计划可生成",
            QG.GATE_VERSION: "版本来源一致",
        }
        for name in QG.GATES:
            greport = gate.groups.get(name)
            if greport is None:
                continue
            n = greport.count(C.ERROR)
            status = "PASS" if n == 0 else "FAIL"
            detail = f"{n} ERROR / {greport.count(C.WARNING)} WARNING" if n else "0 ERROR"
            rep.add(gate_rename.get(name, name), status, detail)
        # 3) 发布产物完整性
        _check_release_files(repo, rep)
        # 4) 测试（可选真实执行）
        _check_tests(repo, rep, run_tests=run_tests)
        # 5) Git Tag（适用时）
        _check_git_tag(repo, rep)
    except Exception as exc:  # noqa: BLE001 - 顶层兜底，工具失败返回 2
        rep.tool_error = f"{type(exc).__name__}: {exc}"
    return rep


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="release_check.py", description="Phase 6 发布前检查")
    parser.add_argument("--strict", action="store_true",
                        help="严格模式：CHANGELOG 缺当前版本段等发布硬要求升为失败")
    parser.add_argument("--version", default="", help="声明要发布的版本，须与 VERSION 一致")
    parser.add_argument("--run-tests", action="store_true",
                        help="真实执行全量 unittest（默认不执行并标注未执行）")
    parser.add_argument("--repo", default=str(REPO), help="规范仓库根目录")
    parser.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    args = parser.parse_args(argv)
    rep = run_release_check(Path(args.repo).resolve(), strict=args.strict,
                            version=args.version, run_tests=args.run_tests)
    if rep.tool_error:
        print(f"[ERROR]    发布检查工具执行失败: {rep.tool_error}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps({
            "repo": rep.repo,
            "spec_version": rep.spec_version,
            "passed": rep.passed,
            "errors": rep.errors,
            "unverifiable": [i.name for i in rep.unverifiable],
            "gate_passed": rep.gate.passed if rep.gate else None,
            "items": [{"name": i.name, "status": i.status, "detail": i.detail}
                      for i in rep.items],
        }, ensure_ascii=False, indent=2))
    else:
        print(rep.render())
    if not rep.passed:
        print("[ERROR]    发布检查未通过，存在失败项。", file=sys.stderr)
        return 1
    if rep.unverifiable:
        print(f"[INFO]    发布检查可执行项通过，但 {len(rep.unverifiable)} 项无法执行，"
              "须人工 / 在 CI 补验，未伪装为通过。", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
