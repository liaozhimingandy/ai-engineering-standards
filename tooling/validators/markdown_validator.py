"""Markdown 校验器：内容充实度、代码块闭合、标题层级。

复用 common.check_markdown_content（非空/占位/标题存在），本模块新增：
- 围栏代码块（``` / ~~~）是否闭合；
- 标题层级是否跳跃（如 ## 直接跳到 ####，中间缺 ###）。

刻意保持保守，避免误报（Phase 2 曾因误报行内代码 / `§2.1` / 代码块注释返工）：
- 只统计围栏代码块之外的标题；
- 行内代码、HTML 注释、缩进代码块内的 `#` 不计入标题；
- 标题层级跳跃报 WARNING（可能是有意的组织方式），不报 ERROR。
"""

from __future__ import annotations

import re
from pathlib import Path

from . import common as C


def check_fences(text: str, report: C.Report, loc: str) -> None:
    """检查围栏代码块成对闭合。支持 ``` 与 ~~~，忽略行内单反引号。"""
    open_fence: str | None = None
    open_line = 0
    for lineno, line in enumerate(text.splitlines(), start=1):
        m = re.match(r"^\s*(`{3,}|~{3,})", line)
        if not m:
            continue
        fence = m.group(1)
        marker = fence[0]
        if open_fence is None:
            open_fence, open_line = marker, lineno
        elif marker == open_fence and len(fence) >= len(open_fence or ""):
            open_fence = None
    if open_fence is not None:
        report.error(f"围栏代码块未闭合（起始 L{open_line}，标记 {open_fence*3}）", loc)


def check_heading_levels(text: str, report: C.Report, loc: str) -> None:
    """标题层级不得跳跃超过 1 级（在围栏代码块外统计）。"""
    prev: int | None = None
    in_fence = False
    for lineno, line in enumerate(text.splitlines(), start=1):
        if re.match(r"^\s*(`{3,}|~{3,})", line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        # 去掉行内代码后再匹配标题，避免 `## not a heading` 误判
        clean = re.sub(r"`[^`]*`", "", line)
        m = re.match(r"^(#{1,6})\s+", clean)
        if not m:
            continue
        level = len(m.group(1))
        if prev is not None and level > prev + 1:
            report.warning(f"L{lineno} 标题层级从 H{prev} 跳到 H{level}（跳过一级）", loc)
        prev = level


def validate_markdown(repo: Path, report: C.Report, *, skip_prefixes=(".ai-standards/", "tooling/tests/")
                      ) -> int:
    """校验仓库全部 Markdown；返回检查文件数。"""
    repo = Path(repo)
    md = [p for p in sorted(repo.rglob("*.md"))
          if not p.relative_to(repo).as_posix().startswith(skip_prefixes)]
    for p in md:
        loc = p.relative_to(repo).as_posix()
        # common 已实现：字节数/行数/占位行/标题存在（ERROR 级）
        C.check_markdown_content(p, report, loc)
        text = C.read_text(p)
        check_fences(text, report, loc)
        check_heading_levels(text, report, loc)
    report.info(f"已检查 {len(md)} 个 Markdown 文件（内容 / 代码块 / 标题层级）", "Markdown")
    return len(md)
