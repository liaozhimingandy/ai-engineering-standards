#!/usr/bin/env python3
"""规范验证器（仓库自检）。

检查本仓库自身是否符合它定义的规范：结构、Markdown、Skills（Agent Skills 规范）、
core 六段结构、适配器格式、引用有效性、工具无关性、config 与代码一致性、安装目标合法性。

用法：
    python tooling/scripts/validate.py                 # 全量
    python tooling/scripts/validate.py --skills        # 只查 Skills
    python tooling/scripts/validate.py --core          # 只查 core/ 结构
    python tooling/scripts/validate.py --adapters      # 只查适配器
    python tooling/scripts/validate.py --strict        # 把指定 WARNING 升级为 ERROR

退出码：0 = 无 ERROR；1 = 存在 ERROR；2 = 工具执行失败（如无法定位仓库/读取 VERSION）。
自动检查**不判定语义冲突**；语义层面的矛盾一律标为"需人工审查"，见 --core 输出。
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tooling"))

from validators import common as C          # noqa: E402
from validators import targets as T         # noqa: E402
from validators import quality_rules as QR  # noqa: E402
from validators import skill_validator as SV       # noqa: E402
from validators import markdown_validator as MV    # noqa: E402
from validators import reference_validator as RV   # noqa: E402
from validators import adapter_validator as AV     # noqa: E402
from validators import consistency_validator as CV  # noqa: E402

# Phase 6：门禁升级规则下沉到共享层，validate / quality_gate / release_check 共用一份。
# 保留本模块级别名，既有调用点与测试无需改动。
STRICT_PROMOTIONS = QR.STRICT_PROMOTIONS
apply_strict = QR.apply_strict


README_SECTIONS = (
    "项目定位", "为什么需要", "架构说明", "目录结构", "支持的 AI 工具", "安装方法",
    "如何使用 Skills", "如何为项目增加专属规则", "如何编写新 Skill", "如何更新规范",
    "如何贡献规范", "常见问题",
)

def check_readme(repo: Path, report: C.Report) -> None:
    readme = repo / "README.md"
    if not readme.is_file():
        return
    heads = set(re.findall(r"^##\s+(.+)$", C.read_text(readme), re.M))
    for section in README_SECTIONS:
        if not any(section in h for h in heads):
            report.error(f"README 缺少必需章节「{section}」", "README.md")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="validate.py", description="检查 ai-engineering-standards 仓库规范",
    )
    parser.add_argument("--skills", action="store_true", help="只检查 Skills（Agent Skills 规范）")
    parser.add_argument("--core", action="store_true", help="只检查 core/ 六段结构与语义冲突信号")
    parser.add_argument("--adapters", action="store_true", help="只检查适配器格式与公共/适配边界")
    parser.add_argument("--strict", action="store_true",
                        help=f"把指定 WARNING 升级为 ERROR（共 {len(STRICT_PROMOTIONS)} 条规则，见下）")
    args = parser.parse_args(argv)

    # 2 = 工具执行失败：先确保仓库可用
    try:
        if not (REPO / "VERSION").is_file() or not (REPO / "core").is_dir():
            print("[ERROR]    无法定位规范仓库（缺 VERSION 或 core/）。请用绝对路径调用。",
                  file=sys.stderr)
            return 2
    except OSError as exc:
        print(f"[ERROR]    读取仓库根失败: {exc}", file=sys.stderr)
        return 2

    report = C.Report("ai-engineering-standards repository validation")
    try:
        version = C.check_version(REPO, report)
        report.info(f"规范版本 {version or '未知'}", "VERSION")

        selected_any = args.skills or args.core or args.adapters
        full = not selected_any

        if full:
            C.check_required_structure(REPO, report)
            md_count = MV.validate_markdown(REPO, report)
            report.info(f"已检查 {md_count} 个 Markdown 文件", "内容")
            RV.validate_references(REPO, report)

        if full or args.skills:
            n = SV.validate_skills(REPO, report)
            report.info(f"已检查 {n} 个 Skill", "Skills")
            SV.validate_global_entry_sources(REPO, report)

        if full or args.core:
            CV.validate_core(REPO, report)
            CV.scan_semantic_conflict_signals(REPO, report)

        if full or args.adapters:
            AV.validate_adapters(REPO, report)
            AV.validate_config_code_consistency(REPO, report)
            AV.validate_no_tool_leakage(REPO, report)
            AV.validate_standards_reference_core(REPO, report)

        if full:
            C.check_duplicates(REPO, report)
            C.check_install_targets(REPO, report)
            check_readme(REPO, report)

        if args.strict:
            promoted = apply_strict(report)
            print("[INFO]    --strict 升级规则（WARNING -> ERROR）：")
            for needle, reason in STRICT_PROMOTIONS:
                print(f"[INFO]      · 含 {needle!r} -> ERROR")
            print(f"[INFO]    本次实际升级 {promoted} 条 WARNING。")
    except Exception as exc:  # noqa: BLE001 - 顶层兜底，保证工具失败返回 2 而非 traceback
        print(f"[ERROR]    验证器执行失败: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    print(report.render())
    if report.count(C.ERROR):
        print("[ERROR]   仓库自检未通过，请修复所有 ERROR。", file=sys.stderr)
    return report.exit_code


if __name__ == "__main__":
    sys.exit(main())
