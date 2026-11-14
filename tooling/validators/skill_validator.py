"""Skill 校验器：遵循 Agent Skills 开放规范（2026-11-11 版本）。

薄封装 common.check_skill（真正的字段/正文/引用规则在 common 里，只有一份实现），
本模块负责：批量检查 skills/、检查 global 入口模板源、检查 Skills 单源不被工具专有字段污染。

不宣称与官方 skills-ref 完全等价（见 docs/tool-adapters.md §1）；
Frontmatter 解析是受约束的 YAML 子集，不支持完整 YAML，遇到不支持语法明确报错。
"""

from __future__ import annotations

import re
from pathlib import Path

from . import common as C
from . import targets as T


def validate_skills(repo: Path, report: C.Report) -> int:
    """校验仓库 skills/ 下每个 Skill；返回检查的 Skill 数量。"""
    root = Path(repo) / "skills"
    if not root.is_dir():
        report.error("缺少 skills/ 目录")
        return 0
    count = 0
    for d in sorted(p for p in root.iterdir() if p.is_dir()):
        C.check_skill(d, report)
        count += 1
        # 单源约束：Skill 源文件不得出现工具专有 Frontmatter 字段
        skill_file = d / "SKILL.md"
        if skill_file.is_file():
            _assert_no_tool_fields(skill_file, report, f"skills/{d.name}/SKILL.md")
    # 目录名集合必须等于声明的 SKILL_NAMES（防止新增 Skill 未登记到 targets）
    actual = {p.name for p in root.iterdir() if p.is_dir()}
    declared = set(T.SKILL_NAMES)
    for name in sorted(actual - declared):
        report.warning(f"Skill 目录 {name}/ 未登记到 targets.SKILL_NAMES，安装器不会分发",
                       f"skills/{name}/")
    for name in sorted(declared - actual):
        report.error(f"targets.SKILL_NAMES 声明的 Skill 目录不存在: {name}",
                     f"skills/{name}/")
    return count


_TOOL_ONLY_FIELDS = ("applyTo", "alwaysApply", "globs", "paths", "excludeAgent",
                     "disable-model-invocation", "icon", "color")


def _assert_no_tool_fields(path: Path, report: C.Report, loc: str) -> None:
    """公共 Skill 的 Frontmatter 不得含任何工具专有字段（工具无关性红线）。"""
    text = C.read_text(path)
    data, _body, _errs = C.parse_frontmatter(text)
    for key in data:
        if key in _TOOL_ONLY_FIELDS:
            report.error(f"公共 Skill 含工具专有字段 {key!r}，会破坏可移植性", loc)


def validate_global_entry_sources(repo: Path, report: C.Report) -> int:
    """校验 global 入口模板源：必须是合法归属标记 + 含 version 生成 token。"""
    count = 0
    for tool, spec in T.GLOBAL_TOOL_SPECS.items():
        if spec.entry is None:
            continue
        src = Path(repo) / spec.entry.source
        if not src.is_file():
            report.error(f"[global/{tool}] 缺少入口模板源 {spec.entry.source}", spec.entry.source)
            continue
        count += 1
        text = C.read_text(src)
        loc = spec.entry.source
        if not C.has_marker(src):
            report.error("global 入口模板缺少归属标记", loc)
        if "version=" not in text[:2048] or "tool=" not in text[:2048]:
            report.error("global 入口模板归属标记缺 version= 或 tool=", loc)
        if T.GENERATED_VERSION_TOKEN not in text:
            report.error(f"global 入口模板缺少生成 token {T.GENERATED_VERSION_TOKEN!r}"
                         "（无法渲染真实版本）", loc)
        # global 入口是纯 Markdown，不得含工具专有 frontmatter 字段
        _assert_no_tool_fields(src, report, loc)
    return count
