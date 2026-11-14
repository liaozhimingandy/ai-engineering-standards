"""一致性校验器：core 结构、公共 Skills 源、生成文件、语义冲突人工审查。

对应 Phase 5 任务书 §七.1（Core 检查）与 §八（一致性检查）。职责：
- **Core 结构**：7 份规范文件是否含六段（文档目的/适用范围/核心规范/禁止事项/
  验收要求/相关规范引用）；README.md 是索引，按其 §7 约定不套六段，故排除。
- **语义冲突**：自动检查**无法可靠判断**语义冲突，因此只做可机械检测的信号扫描
  （如多文件是否各自声明"最高优先级"），命中一律标「需人工审查」的 WARNING/INFO，
  **绝不声称已完成语义分析**。
- **公共 Skills 源完整性**：targets.SKILL_NAMES 声明的源是否都齐全（不比对安装副本，
  那是 check.py --target 的工作）。
- **生成文件检查**：global 入口模板源是否含生成 token（可渲染）、config 是否与代码一致。

本模块主要针对"仓库自身一致性"，与 check.py 的"安装结果一致性"互补。
"""

from __future__ import annotations

import re
from pathlib import Path

from . import common as C
from . import targets as T


CORE_SIX_SECTIONS = ("文档目的", "适用范围", "核心规范", "禁止事项", "验收要求", "相关规范引用")
CORE_FILES_EXCLUDE = {"README.md"}   # 索引文件，按其 §7 不套六段


def validate_core(repo: Path, report: C.Report) -> int:
    """校验 core/ 规范结构；返回检查的规范文件数。"""
    repo = Path(repo)
    core = repo / "core"
    if not core.is_dir():
        report.error("缺少 core/ 目录")
        return 0
    count = 0
    for p in sorted(core.rglob("*.md")):
        rel = p.relative_to(repo).as_posix()
        if p.name in CORE_FILES_EXCLUDE:
            continue
        count += 1
        text = C.read_text(p)
        headings = [m.group(1).strip() for m in re.finditer(r"^##\s+(.+)$", text, re.M)]
        for section in CORE_SIX_SECTIONS:
            if not any(section == h or section in h for h in headings):
                report.error(f"core 规范缺少必需章节「{section}」", rel)
        # 空文件 / 内容过少由 markdown_validator 覆盖，这里只查约定：
        # 每份规范 MUST 引用 core/README.md（用语与冲突规则来源）
        if not any("README.md" in t for t, _ in C.markdown_links(text)):
            report.warning("未引用 core/README.md（用语/冲突规则唯一权威）", rel)
    report.info(f"已检查 {count} 份 core 规范（六段结构）", "Core")
    return count


def scan_semantic_conflict_signals(repo: Path, report: C.Report) -> None:
    """扫描**可能的**语义冲突信号，仅报告"需人工审查"，不判定冲突。

    自动检查无法可靠判断两条规范是否语义矛盾（core/README.md §7 已声明此边界）。
    这里只做保守的机械信号：同一规范体系里多处出现"最高优先级"/"绝对优先"
    这类排他性措辞时，提示人工核对是否与 core/README.md §4 冲突规则一致。
    """
    repo = Path(repo)
    exclusive = re.compile(r"(最高优先级|绝对优先|唯一最高|优先于所有)")
    # 否定/元描述语境不算"排他性声明"（Phase 5 校验脚本首轮曾把 core/README.md
    # 解释"不再各自声明绝对优先"的那行误报为冲突信号，故显式排除）
    negation = re.compile(r"(不再|不得|不应|不能|禁止|并非|不是|避免|而非|误以为|声明.*优先.*会与)")
    hits: list[str] = []
    for d in ("core", "standards", "skills", "agents", "adapters"):
        base = repo / d
        if not base.is_dir():
            continue
        for p in sorted(base.rglob("*.md")):
            for lineno, line in enumerate(C.read_text(p).splitlines(), start=1):
                if (exclusive.search(line) and not negation.search(line)
                        and not line.lstrip().startswith(("#", ">", "|"))):
                    hits.append(f"{p.relative_to(repo).as_posix()}:L{lineno}")
    if hits:
        report.info("发现排他性优先级措辞，需人工核对是否与 core/README.md §4 冲突规则一致（"
                    "自动检查不判定语义冲突）：" + ", ".join(hits[:12]), "语义冲突人工审查")
    else:
        report.info("未发现排他性优先级措辞信号（语义冲突仍须人工评审，本工具不做语义分析）",
                    "语义冲突人工审查")


def validate_public_skill_sources(repo: Path, report: C.Report) -> None:
    """公共 Skills 源完整性、无重复权威副本、无工具专属字段。"""
    repo = Path(repo)
    from . import skill_validator as SV
    SV.validate_skills(repo, report)
    SV.validate_global_entry_sources(repo, report)


def check_generated_freshness(repo: Path, report: C.Report) -> None:
    """生成文件来源/规则/新鲜度：global 入口模板源必须可渲染出真实版本。

    "生成文件"指安装时由模板渲染的 global 入口。仓库内保存的是**源模板**，
    安装器用 installer.resolver.render_global_entry 统一渲染。此处检查：
    1. 每个 global 入口源含版本 token（可渲染）；
    2. 源模板若被手工写入真实版本（token 丢失），报过期/规则破坏；
    3. config/global 声明的入口与代码一致（双源漂移由 adapter_validator 覆盖）。
    """
    repo = Path(repo)
    token = T.GENERATED_VERSION_TOKEN
    for tool, spec in T.GLOBAL_TOOL_SPECS.items():
        if spec.entry is None:
            continue
        src = repo / spec.entry.source
        if not src.is_file():
            continue   # skill_validator 已报缺失
        text = C.read_text(src)
        loc = spec.entry.source
        if token not in text:
            report.error(f"生成文件源缺少 token {token!r}，无法渲染真实版本（视为过期/规则破坏）",
                         loc)
        # dest 必须是官方确认的路径，不得出现 Cursor 的 .cursor/rules 等错误路径
        for prob in T.path_problems(spec.entry.dest):
            report.error(f"[global/{tool}] 生成目标路径非法: {prob}", loc)


def check_core_mapping(repo: Path, report: C.Report) -> None:
    """跨模块映射检查（Phase 6，任务书 §四.1）：每份 core 规范必须被引用。

    ``validate_references`` 只保证"链接目标存在"，不保证"每个 core 规范都被映射"。
    新增一份 core 规范却忘记在 core/README.md 索引、standards/、adapters/ 或 docs/
    里引用它，会形成孤儿规范。本函数补齐该方向：收集全仓内部链接目标，检查每份
    ``core/*.md``（除 README 索引）是否至少被一处链接指向。命中报 WARNING，
    严格模式（quality_rules.STRICT_PROMOTIONS）将其升级为 ERROR。

    只扫描源仓库 Markdown，跳过安装镜像与缓存，避免把安装产物当作有效映射。
    """
    repo = Path(repo)
    core = repo / "core"
    if not core.is_dir():
        return
    linked_names: set[str] = set()
    for p in repo.rglob("*.md"):
        parts = p.relative_to(repo).parts
        if any(seg in parts for seg in ("__pycache__", ".ai-standards",
                                         ".ai-engineering-standards", "dist")):
            continue
        for target, _lineno in C.markdown_links(C.read_text(p)):
            clean = target.split("#")[0].strip().replace("\\", "/")
            if clean.startswith(("http://", "https://", "mailto:", "data:")) or not clean:
                continue
            linked_names.add(clean.rsplit("/", 1)[-1])   # 以文件名比对，忽略 ../ 层级
    core_docs = sorted(p for p in core.glob("*.md") if p.name != "README.md")
    orphans = [p.name for p in core_docs if p.name not in linked_names]
    if orphans:
        for name in orphans:
            report.warning(
                f"core 规范未被任何索引/适配器/规范引用（新增后疑似遗漏映射）："
                f"core/{name}",
                "core 映射",
            )
    else:
        report.info(f"core 映射完整：{len(core_docs)} 份规范均被至少一处链接引用", "core 映射")


def validate_all(repo: Path, report: C.Report) -> None:
    """仓库一致性全量检查（供 check.py --all 调用）。"""
    repo = Path(repo)
    validate_core(repo, report)
    validate_public_skill_sources(repo, report)
    check_generated_freshness(repo, report)
    check_core_mapping(repo, report)
    scan_semantic_conflict_signals(repo, report)
