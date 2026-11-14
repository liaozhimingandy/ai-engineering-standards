#!/usr/bin/env python3
"""统一质量门禁（Phase 6，任务书 §三）。

把 Phase 5 已有的验证器按"四类门禁"编排成一个可复用入口，**不重新实现任何
Phase 5 检查逻辑**——每个检查项都直接调用对应的 validator 函数：

  1. 公共规范检查  -> common / markdown_validator / reference_validator /
                      consistency_validator
  2. Skills 检查    -> skill_validator
  3. 适配器检查     -> adapter_validator / consistency_validator.check_generated_freshness
  4. 安装器检查     -> version_validator / installer.resolver.build_plan / targets.check_paths

用法：
    python tooling/scripts/quality_gate.py                # 四类全部门禁
    python tooling/scripts/quality_gate.py --strict       # 把白名单 WARNING 升为 ERROR
    python tooling/scripts/quality_gate.py --group skills # 只跑某一类
    python tooling/scripts/quality_gate.py --json         # 机器可读输出（供 CI / 发布检查）

退出码：0 = 无 ERROR；1 = 存在 ERROR（门禁未通过）；2 = 工具执行失败。
严格/警告分级与 validate.py 共用 quality_rules.STRICT_PROMOTIONS 单一来源。
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tooling"))

from validators import common as C                # noqa: E402
from validators import targets as T               # noqa: E402
from validators import quality_rules as QR        # noqa: E402
from validators import version_validator as VV    # noqa: E402
from validators import skill_validator as SV      # noqa: E402
from validators import markdown_validator as MV   # noqa: E402
from validators import reference_validator as RV  # noqa: E402
from validators import adapter_validator as AV    # noqa: E402
from validators import consistency_validator as CV  # noqa: E402
from installer import resolver as R               # noqa: E402


# 门禁分组（顺序即报告顺序）
GATE_CORE = "公共规范"
GATE_SKILLS = "Skills"
GATE_ADAPTERS = "适配器"
GATE_INSTALLER = "安装器"
GATE_VERSION = "版本管理"
GATES = (GATE_CORE, GATE_SKILLS, GATE_ADAPTERS, GATE_INSTALLER, GATE_VERSION)

# --group 的合法取值（映射中文分组名）
_GROUP_ALIASES = {
    "core": GATE_CORE, "public": GATE_CORE,
    "skills": GATE_SKILLS,
    "adapters": GATE_ADAPTERS,
    "installer": GATE_INSTALLER,
    "version": GATE_VERSION, "versions": GATE_VERSION,
}


@dataclass
class GateResult:
    """门禁结果，供 CLI 与 release_check 复用。"""

    repo: str
    strict: bool
    groups: dict[str, C.Report] = field(default_factory=dict)
    version_info: VV.VersionInfo | None = None
    promoted: int = 0
    tool_error: str = ""

    @property
    def total_errors(self) -> int:
        return sum(r.count(C.ERROR) for r in self.groups.values())

    @property
    def total_warnings(self) -> int:
        return sum(r.count(C.WARNING) for r in self.groups.values())

    @property
    def total_infos(self) -> int:
        return sum(r.count(C.INFO) for r in self.groups.values())

    @property
    def passed(self) -> bool:
        return not self.tool_error and self.total_errors == 0

    def to_dict(self) -> dict:
        return {
            "repo": self.repo,
            "strict": self.strict,
            "passed": self.passed,
            "promoted": self.promoted,
            "errors": self.total_errors,
            "warnings": self.total_warnings,
            "infos": self.total_infos,
            "tool_error": self.tool_error,
            "groups": {
                name: {
                    "errors": rep.count(C.ERROR),
                    "warnings": rep.count(C.WARNING),
                    "infos": rep.count(C.INFO),
                    "issues": [
                        {"level": i.level, "message": i.message, "location": i.location}
                        for i in rep.issues
                    ],
                }
                for name, rep in self.groups.items()
            },
            "version": (
                {
                    "spec": self.version_info.spec,
                    "installer_code": self.version_info.installer_code,
                    "installer_config": self.version_info.installer_config,
                    "readme_badge": self.version_info.readme_badge,
                    "changelog_latest": self.version_info.changelog_latest,
                    "changelog_has_current": self.version_info.changelog_has_current,
                    "all_consistent": self.version_info.all_consistent,
                }
                if self.version_info else None
            ),
        }


# ---------------------------------------------------------------------------
# 四类门禁：全部复用 Phase 5 验证器，只编排、不重造
# ---------------------------------------------------------------------------

def gate_core(repo: Path, report: C.Report) -> None:
    """公共规范检查：结构 / 空文件与无效 Markdown / 失效引用 / 重复 / core 六段 / 引用正确。"""
    C.check_required_structure(repo, report)
    MV.validate_markdown(repo, report)
    RV.validate_references(repo, report)
    CV.validate_core(repo, report)
    CV.check_core_mapping(repo, report)  # 跨模块映射：新增 core 规范不得成为孤儿
    CV.scan_semantic_conflict_signals(repo, report)  # 只标人工审查，不做语义判定
    C.check_duplicates(repo, report)
    C.check_install_targets(repo, report)
    # README 必需章节（复用 validate.py 的契约）
    readme = repo / "README.md"
    if readme.is_file():
        import re
        heads = set(re.findall(r"^##\s+(.+)$", C.read_text(readme), re.M))
        for section in ("项目定位", "为什么需要", "架构说明", "目录结构", "支持的 AI 工具",
                        "安装方法", "如何使用 Skills", "如何为项目增加专属规则",
                        "如何编写新 Skill", "如何更新规范", "如何贡献规范", "常见问题"):
            if not any(section in h for h in heads):
                report.error(f"README 缺少必需章节「{section}」", "README.md")


def gate_skills(repo: Path, report: C.Report) -> None:
    """Skills 检查：frontmatter / name·description / 目录结构 / 引用 / 危险路径 / 工作流完整性。"""
    n = SV.validate_skills(repo, report)
    report.info(f"已检查 {n} 个 Skill 的 frontmatter / 结构 / 引用", "Skills 门禁")
    SV.validate_global_entry_sources(repo, report)
    CV.validate_public_skill_sources(repo, report)


def gate_adapters(repo: Path, report: C.Report) -> None:
    """适配器检查：各工具引用正确 / 无过期副本 / 能力声明与配置一致。"""
    AV.validate_adapters(repo, report)
    AV.validate_config_code_consistency(repo, report)
    AV.validate_no_tool_leakage(repo, report)
    AV.validate_standards_reference_core(repo, report)
    CV.check_generated_freshness(repo, report)
    for level, msg in T.check_paths(repo):
        (report.error if level == "ERROR" else report.warning)(msg, "targets.check_paths")


def gate_installer(repo: Path, report: C.Report) -> None:
    """安装器检查：版本正确 / 清单完整 / 两项安装计划可生成（不写盘）。"""
    info = VV.validate_versions(repo, report)  # 版本关系写入 report（也供 version 门禁复用摘要）
    # 安装计划完整性：project 与 global 都应能为全部工具生成计划，且目标路径合法、源存在。
    # 注意 project-only 工具（如 WorkBuddy）在 global 分支由 build_plan 自动跳过，属预期。
    for scope in T.SCOPES:
        try:
            plan = R.build_plan(repo, list(T.TOOLS), scope)
        except Exception as exc:  # noqa: BLE001 - 计划生成失败视为门禁错误
            report.error(f"{scope} 安装计划生成失败: {type(exc).__name__}: {exc}", "安装器门禁")
            continue
        if not plan:
            report.error(f"{scope} 安装计划为空（清单不完整）", "安装器门禁")
            continue
        for pf in plan:
            for prob in T.path_problems(pf.dest_rel):
                report.error(f"{scope} 计划目标路径非法 {prob}", pf.dest_rel)
            if not pf.source_abs.is_file():
                report.error(f"{scope} 计划源文件不存在: {pf.source_rel}", pf.dest_rel)
        report.info(f"{scope} 安装计划可生成：{len(plan)} 个文件 / {len(T.TOOLS)} 工具",
                    "安装器门禁")
    # 安装器安全机制的存在性（file 级断言，不执行安装）：确保事务/备份/归属能力未被移除。
    for rel in ("tooling/installer/filesystem.py", "tooling/installer/backup.py",
                "tooling/installer/manifest.py", "tooling/installer/manager.py"):
        if not (repo / rel).is_file():
            report.error(f"安装器安全模块缺失: {rel}", "安装器门禁")


def gate_version(repo: Path, report: C.Report) -> VV.VersionInfo:
    """版本管理门禁：SemVer + 各版本来源一致（含安装器版本双源）。"""
    return VV.validate_versions(repo, report)


def run_gate(repo: Path, *, strict: bool = False,
             only: tuple[str, ...] | None = None) -> GateResult:
    """执行质量门禁，返回结果对象。only 为分组名白名单（默认全部五组）。"""
    repo = Path(repo)
    result = GateResult(repo=str(repo), strict=strict)
    active = set(only) if only else set(GATES)
    try:
        if not (repo / "VERSION").is_file() or not (repo / "core").is_dir():
            result.tool_error = "无法定位规范仓库（缺 VERSION 或 core/）"
            return result
        for name in GATES:
            if name not in active:
                continue
            rep = C.Report(f"质量门禁 / {name}")
            result.groups[name] = rep
            if name == GATE_CORE:
                gate_core(repo, rep)
            elif name == GATE_SKILLS:
                gate_skills(repo, rep)
            elif name == GATE_ADAPTERS:
                gate_adapters(repo, rep)
            elif name == GATE_INSTALLER:
                gate_installer(repo, rep)
            elif name == GATE_VERSION:
                info = gate_version(repo, rep)
                result.version_info = info
        if strict:
            result.promoted = sum(QR.apply_strict(rep) for rep in result.groups.values())
    except Exception as exc:  # noqa: BLE001 - 顶层兜底，工具失败返回 2
        result.tool_error = f"{type(exc).__name__}: {exc}"
    return result


def render(result: GateResult) -> str:
    lines: list[str] = ["=== Phase 6 质量门禁 ==="]
    lines.append(f"仓库: {result.repo}  |  严格模式: {'是' if result.strict else '否'}")
    if result.version_info is not None:
        lines.append(f"版本: {VV.render_version_summary(result.version_info)}")
    for name in GATES:
        rep = result.groups.get(name)
        if rep is None:
            continue
        lines.append(f"\n--- 门禁 [{name}] ---")
        level_order = {C.ERROR: 0, C.WARNING: 1, C.INFO: 2}
        body = "\n".join(
            f"  [{i.level:<7}] {('[' + i.location + '] ') if i.location else ''}{i.message}"
            for i in sorted(rep.issues, key=lambda x: (level_order[x.level], x.location))
        )
        lines.append(body if body else "  （无检查项）")
    if result.strict:
        lines.append(f"\n严格模式升级 {result.promoted} 条 WARNING -> ERROR "
                     f"（规则 {len(QR.STRICT_PROMOTIONS)} 条，单一来源）")
    if result.tool_error:
        lines.append(f"\n[工具失败] {result.tool_error}")
    lines.append(
        f"\n--- 门禁结论: {result.total_errors} ERROR / "
        f"{result.total_warnings} WARNING / {result.total_infos} INFO -> "
        f"{'通过' if result.passed else '未通过'} ---"
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="quality_gate.py", description="Phase 6 统一质量门禁")
    parser.add_argument("--strict", action="store_true",
                        help="严格模式：把白名单 WARNING 升级为 ERROR")
    parser.add_argument("--group", action="append", choices=sorted(_GROUP_ALIASES),
                        help="只跑指定门禁分组，可重复（默认全部）")
    parser.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    parser.add_argument("--repo", default=str(REPO), help="规范仓库根目录")
    args = parser.parse_args(argv)
    only = tuple(dict.fromkeys(_GROUP_ALIASES[g] for g in args.group)) if args.group else None
    result = run_gate(Path(args.repo).resolve(), strict=args.strict, only=only)
    if args.json:
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(render(result))
        if args.strict:
            print("[INFO]    严格模式升级规则（单一来源 quality_rules）：")
            for rule in QR.describe_rules():
                print(f"[INFO]      · {rule}")
    if result.tool_error:
        print(f"[ERROR]    门禁工具执行失败: {result.tool_error}", file=sys.stderr)
        return 2
    if not result.passed:
        print("[ERROR]    质量门禁未通过，请修复所有 ERROR。", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
