"""引用校验器：内部链接、图片资源、配置引用目标的有效性与路径安全。

复用 common.check_internal_links（Markdown 相对链接存在性 + 技能目录越界），
本模块新增：
- 图片资源引用（![...](path)）是否存在；
- 链接目标不得含 suspicious 片段（../ 越界到仓库外由 common 已覆盖，这里补绝对路径/盘符）；
- tooling/config/targets.json 中声明的所有 source 文件是否存在（配置引用有效性）。
"""

from __future__ import annotations

import json
import re
from pathlib import Path, PurePosixPath

from . import common as C
from . import targets as T

_IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp", ".ico"}


def check_images(repo: Path, report: C.Report) -> int:
    """检查仓库 Markdown 中图片引用是否存在；返回检查的图片引用数。"""
    repo = Path(repo)
    pattern = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
    count = 0
    for p in sorted(repo.rglob("*.md")):
        rel = p.relative_to(repo).as_posix()
        if rel.startswith(".ai-standards/"):
            continue
        text = C.strip_code(C.read_text(p))   # 跳过代码块内的示例图片语法
        for m in pattern.finditer(text):
            target = m.group(1).strip().split("#")[0]
            if not target or target.startswith(("http://", "https://", "data:")):
                continue
            count += 1
            dest = (p.parent / target).resolve()
            if not dest.exists():
                report.error(f"图片资源不存在 -> {target}", rel)
            elif dest.suffix.lower() not in _IMAGE_EXT:
                report.warning(f"图片引用指向非图片文件 {target!r}", rel)
    return count


def check_link_path_safety(repo: Path, report: C.Report) -> None:
    """链接目标不得是绝对路径 / Windows 盘符（安装后不可移植）。"""
    repo = Path(repo)
    for p in sorted(repo.rglob("*.md")):
        rel = p.relative_to(repo).as_posix()
        if rel.startswith(".ai-standards/"):
            continue
        for target, lineno in C.markdown_links(C.read_text(p)):
            clean = target.split("#")[0].strip()
            if not clean:
                continue
            problems = T.path_problems(clean) if not clean.startswith(
                ("http://", "https://", "mailto:", "data:", "#", "/", "~")
            ) else []
            # 绝对路径 / 盘符 / 反斜杠一律报（链接应相对且用 /）
            if clean.startswith("/") or re.match(r"^[A-Za-z]:", clean) or "\\" in clean:
                report.error(f"L{lineno} 链接使用非可移植路径 {clean!r}", rel)
            elif clean.startswith("../") and rel.startswith("skills/"):
                # 技能目录内不得越出（common 也会查，这里给更明确的报错）
                pass  # 交给 common.check_internal_links，避免重复报错


def check_config_sources(repo: Path, report: C.Report) -> int:
    """config/targets.json 声明的 source / dest 路径合法且源文件存在。"""
    repo = Path(repo)
    cfg_path = repo / "tooling" / "config" / "targets.json"
    if not cfg_path.is_file():
        report.error("缺少 tooling/config/targets.json")
        return 0
    try:
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
        report.error(f"tooling/config/targets.json 无法解析: {exc}",
                     "tooling/config/targets.json")
        return 0
    count = 0
    scope_blocks = [("project", cfg.get("project", {})), ("global", cfg.get("global", {}))]
    for scope, block in scope_blocks:
        if not isinstance(block, dict):
            report.error(f"config.{scope} 必须是对象", "tooling/config/targets.json")
            continue
        for tool, spec in block.items():
            for entry in spec.get("entries", []):
                src = entry.get("source")
                if isinstance(src, str):
                    count += 1
                    for prob in T.path_problems(src):
                        report.error(f"config.{scope}.{tool} source 非法: {prob}",
                                     "tooling/config/targets.json")
                    if not (repo / src).is_file():
                        report.error(f"config.{scope}.{tool} source 文件不存在: {src}",
                                     "tooling/config/targets.json")
                dest = entry.get("dest")
                if isinstance(dest, str):
                    for prob in T.path_problems(dest):
                        report.error(f"config.{scope}.{tool} dest 非法: {prob}",
                                     "tooling/config/targets.json")
    # global_integration 源
    gi = cfg.get("global_integration", {})
    gi_src = gi.get("source") if isinstance(gi, dict) else None
    if isinstance(gi_src, str) and not (repo / gi_src).is_file():
        report.error(f"config.global_integration source 不存在: {gi_src}",
                     "tooling/config/targets.json")
    return count


def validate_references(repo: Path, report: C.Report) -> None:
    """执行全部引用相关检查（common 内部链接 + 图片 + 配置源 + 路径安全）。"""
    repo = Path(repo)
    C.check_internal_links(repo, report)
    imgs = check_images(repo, report)
    cfg_refs = check_config_sources(repo, report)
    check_link_path_safety(repo, report)
    report.info(f"已检查 {imgs} 个图片引用 / {cfg_refs} 个配置源文件引用", "引用")
