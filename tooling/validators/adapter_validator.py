"""适配器校验器：四适配器完整、格式合规、公共/适配边界清晰、配置无双源漂移。

职责：
- 逐工具检查 project 入口/叠加适配器的存在与格式（复用 common.check_adapter）；
- 检查 config/targets.json 与 targets.py 代码常量一致（防止第二个事实来源漂移）；
- 检查工具专属字段未泄漏进公共 core/ / standards/ / skills/ / agents/；
- 检查 standards/ 是否引用 core/（避免重复定义通用原则）。

不在此做 Skills 副本字节比对（那是 consistency_validator 的职责）。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from . import common as C
from . import targets as T


_TOOL_ONLY_TOKENS = ("applyTo:", "alwaysApply:", "globs:", "paths:",
                     "disable-model-invocation:", "excludeAgent:")


def validate_adapters(repo: Path, report: C.Report) -> int:
    """校验四适配器；返回检查的适配器文件数。"""
    repo = Path(repo)
    count = 0
    for tool, spec in T.TOOL_SPECS.items():
        for a in spec.all_adapters:
            C.check_adapter(repo / a.source, a, report)
            count += 1
    report.info(f"已检查 {count} 个 project 适配器 / {len(T.TOOL_SPECS)} 个工具", "Adapters")
    return count


def validate_config_code_consistency(repo: Path, report: C.Report) -> None:
    """config/targets.json 必须与 targets.py 代码常量一致（单一来源纪律）。"""
    repo = Path(repo)
    cfg_path = repo / "tooling" / "config" / "targets.json"
    if not cfg_path.is_file():
        return   # reference_validator 已报缺失
    try:
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return   # 已由 reference_validator 报错
    if cfg.get("installer_version") != T.INSTALLER_VERSION:
        report.error(f"config.installer_version={cfg.get('installer_version')!r} "
                     f"与 targets.INSTALLER_VERSION={T.INSTALLER_VERSION!r} 不一致",
                     "tooling/config/targets.json")
    if list(cfg.get("tools", [])) != list(T.TOOLS):
        report.error("config.tools 与 targets.TOOLS 不一致", "tooling/config/targets.json")
    if cfg.get("tool_aliases", {}) != T.TOOL_ALIASES:
        report.error("config.tool_aliases 与 targets.TOOL_ALIASES 不一致", "tooling/config/targets.json")
    if list(cfg.get("skills", [])) != list(T.SKILL_NAMES):
        report.error("config.skills 与 targets.SKILL_NAMES 不一致", "tooling/config/targets.json")
    for tool in T.TOOLS:
        ps = T.TOOL_SPECS.get(tool)
        cs = (cfg.get("project") or {}).get(tool)
        if ps is None or cs is None:
            report.error(f"config.project 缺少工具 {tool!r}", "tooling/config/targets.json")
            continue
        if cs.get("skills_dir") != ps.skills_dir:
            report.error(f"config.project.{tool}.skills_dir 与代码不一致", "tooling/config/targets.json")
        c_entries = cs.get("entries", [])
        code_entries = [{"source": a.source, "dest": a.dest, "fmt": a.fmt, "guarded": a.guarded}
                        for a in ps.all_adapters]
        if c_entries != code_entries:
            report.error(f"config.project.{tool}.entries 与代码不一致", "tooling/config/targets.json")
        gs = T.GLOBAL_TOOL_SPECS.get(tool)
        gc = (cfg.get("global") or {}).get(tool)
        if gs is None or gc is None:
            report.error(f"config.global 缺少工具 {tool!r}", "tooling/config/targets.json")
            continue
        if gc.get("skills_dir") != gs.skills_dir or gc.get("entry_status") != gs.entry_status:
            report.error(f"config.global.{tool} 的 skills_dir/entry_status 与代码不一致",
                         "tooling/config/targets.json")
        if bool(gc.get("project_only", False)) != bool(gs.project_only):
            report.error(f"config.global.{tool}.project_only 与代码不一致",
                         "tooling/config/targets.json")
        g_entries = gc.get("entries", [])
        code_g = [] if gs.entry is None else [
            {"source": gs.entry.source, "dest": gs.entry.dest, "fmt": gs.entry.fmt, "guarded": gs.entry.guarded}
        ]
        if g_entries != code_g:
            report.error(f"config.global.{tool}.entries 与代码不一致", "tooling/config/targets.json")


def validate_no_tool_leakage(repo: Path, report: C.Report) -> None:
    """公共规范 source 不得含工具专有配置字段（工具无关性红线）。"""
    repo = Path(repo)
    for d in ("core", "standards", "skills", "agents"):
        for p in sorted((repo / d).rglob("*.md")) if (repo / d).is_dir() else []:
            rel = p.relative_to(repo).as_posix()
            text = C.read_text(p)
            for token in _TOOL_ONLY_TOKENS:
                if re.search(rf"^{re.escape(token)}", text, re.M):
                    report.error(f"公共规范含工具专有字段 {token!r}", rel)


def validate_standards_reference_core(repo: Path, report: C.Report) -> None:
    """standards/ 应引用 core/，而不是重复定义通用原则。"""
    repo = Path(repo)
    std = repo / "standards"
    if not std.is_dir():
        return
    count = 0
    for p in sorted(std.rglob("*.md")):
        rel = p.relative_to(repo).as_posix()
        count += 1
        links = [t for t, _ in C.markdown_links(C.read_text(p))]
        if not any("core/" in t for t in links):
            report.warning("未引用 core/ 原则，可能重复定义了通用规则", rel)
    report.info(f"已检查 {count} 份 standards/ 技术规范对 core/ 的引用", "standards")
