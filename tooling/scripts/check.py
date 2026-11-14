#!/usr/bin/env python3
"""规范检查器（双职责）。

【A】安装结果检查（默认 / --target）：检查某次安装是否完整、未被篡改。
【B】一致性检查（--skills / --adapters / --all）：检查仓库内公共规范与适配器是否同步，
    输出六类报告：正常 / 缺失 / 过期 / 不一致 / 无法验证 / 需人工检查。

用法：
    python tooling/scripts/check.py --target PATH            # 安装结果（project）
    python tooling/scripts/check.py --target ~ --scope global # 安装结果（global）
    python tooling/scripts/check.py --all                    # 仓库一致性全量
    python tooling/scripts/check.py --skills                 # 仅 Skills 一致性
    python tooling/scripts/check.py --adapters               # 仅适配器一致性

退出码：0 = 无 ERROR；1 = 存在 ERROR；2 = 工具执行失败。
安装结果检查若未安装/清单损坏返回 1；一致性检查中"需人工/无法验证"不影响退出码。
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tooling"))

from validators import common as C          # noqa: E402
from validators import targets as T         # noqa: E402
from validators import skill_validator as SV       # noqa: E402
from validators import adapter_validator as AV     # noqa: E402
from validators import consistency_validator as CV  # noqa: E402
from installer import resolver as R                # noqa: E402
from installer import manifest as M                # noqa: E402


# ===========================================================================
# 六类一致性报告
# ===========================================================================

K_OK = "正常项目"
K_MISSING = "缺失文件"
K_STALE = "过期文件"
K_INCONSISTENT = "不一致文件"
K_UNVERIFIABLE = "无法验证的文件"
K_MANUAL = "需要人工检查的项目"
_CATEGORY_ORDER = (K_OK, K_MISSING, K_STALE, K_INCONSISTENT, K_UNVERIFIABLE, K_MANUAL)


@dataclass
class ConsistencyReport:
    title: str = "Consistency check"
    buckets: dict[str, list[str]] = field(default_factory=lambda: {k: [] for k in _CATEGORY_ORDER})

    def add(self, category: str, msg: str) -> None:
        self.buckets[category].append(msg)

    def has_errors(self) -> bool:
        return bool(self.buckets[K_MISSING] or self.buckets[K_STALE] or self.buckets[K_INCONSISTENT])

    def render(self) -> str:
        lines = [f"=== {self.title} ==="]
        for cat in _CATEGORY_ORDER:
            items = self.buckets[cat]
            lines.append(f"--- {cat} ({len(items)}) ---")
            for it in items:
                lines.append(f"  · {it}")
        return "\n".join(lines)


def _import_issues(report: ConsistencyReport, src: C.Report, context: str) -> None:
    """把 validators 的 ERROR/WARNING 归入一致性类别（不重复实现校验逻辑）。

    ERROR -> 不一致；WARNING -> 需人工；INFO 忽略（本命令另有正常项汇总）。
    """
    for i in src.issues:
        where = f" [{i.location}]" if i.location else ""
        if i.level == C.ERROR:
            report.add(K_INCONSISTENT, f"{context}: {i.message}{where}")
        elif i.level == C.WARNING:
            report.add(K_MANUAL, f"{context}: {i.message}{where}")


def check_skills_consistency(repo: Path, report: ConsistencyReport) -> None:
    """Skills 一致性：源完整、无重复不一致副本、无过期镜像副本、未强制安装不支持内容。"""
    repo = Path(repo)
    # 1) 公共源完整性（复用 skill_validator）
    tmp = C.Report("skills")
    n = SV.validate_skills(repo, tmp)
    _import_issues(report, tmp, "公共 Skills 源")
    if tmp.count(C.ERROR) == 0:
        report.add(K_OK, f"公共 Skills 源完整：{n} 个 Skill（{', '.join(T.SKILL_NAMES)}）")

    # 2) 全仓 SKILL.md 副本扫描：除 skills/ 源与镜像外，不得有第二权威副本
    src_hashes: dict[str, str] = {}
    for name in T.SKILL_NAMES:
        f = repo / "skills" / name / "SKILL.md"
        if f.is_file():
            src_hashes[name] = C.sha256_file(f)
    found: dict[str, list[str]] = {}
    for p in sorted(repo.rglob("SKILL.md")):
        rel = p.relative_to(repo).as_posix()
        if rel.startswith("skills/") or rel.startswith(".ai-standards/") \
                or rel.startswith(".ai-engineering-standards/"):
            continue
        # 其余位置（如 adapters/ 下）不应出现 SKILL.md 副本
        found.setdefault(C.sha256_file(p), []).append(rel)
    for sha, paths in found.items():
        match = next((name for name, h in src_hashes.items() if h == sha), None)
        for rel in paths:
            if match:
                report.add(K_OK, f"逐字节副本与公共源一致：{rel} == skills/{match}/SKILL.md")
            else:
                report.add(K_INCONSISTENT,
                           f"存在与任何公共源都不一致的 SKILL.md 副本：{rel}（疑似过期/分叉）")

    # 3) 若仓库内存在镜像副本（有人把工具装到仓库根），检查是否过期
    for mirror in (".ai-standards/skills", ".ai-engineering-standards/bundle/skills"):
        base = repo / mirror
        if base.is_dir():
            for name, h in src_hashes.items():
                f = base / name / "SKILL.md"
                if not f.is_file():
                    report.add(K_MISSING, f"镜像副本缺失：{mirror}/{name}/SKILL.md")
                elif C.sha256_file(f) != h:
                    report.add(K_STALE, f"镜像副本与公共源不一致（过期）：{mirror}/{name}/SKILL.md")

    # 4) 不得把不支持的能力强制安装：无磁盘路径 / project-only 工具全局入口必须为 None
    for tool, gs in T.GLOBAL_TOOL_SPECS.items():
        if gs.entry_status == "PARTIAL" and tool == "cursor" and gs.entry is not None:
            report.add(K_INCONSISTENT, "Cursor 全局入口被配置，但官方无磁盘可写路径（不得编造）")
        if tool == "cursor":
            report.add(K_OK, "Cursor global 未生成全局规则入口（官方 PARTIAL，未编造配置）")
        if getattr(gs, "project_only", False):
            if gs.entry is not None:
                report.add(K_INCONSISTENT,
                           f"{tool} 为 project-only，却配置了全局入口（会写用户级目录，不得编造）")
            else:
                report.add(K_OK, f"{tool} 为 project-only，global 作用域不写任何用户级目录")
    # PARTIAL 工具的已知边界交给人工
    partials = [t for t, g in T.GLOBAL_TOOL_SPECS.items() if g.entry_status == "PARTIAL"]
    report.add(K_MANUAL, f"PARTIAL 工具真实生效范围需人工验证：{', '.join(partials)}"
               "（Cursor UI / Copilot CLI 版本差异、WorkBuddy project-only 运行时加载）")


def check_adapter_consistency(repo: Path, report: ConsistencyReport) -> None:
    """规范一致性：config 与代码一致、core 被引用、工具字段不泄漏、生成文件新鲜度。"""
    repo = Path(repo)
    tmp = C.Report("adapters")
    AV.validate_config_code_consistency(repo, tmp)
    AV.validate_no_tool_leakage(repo, tmp)
    AV.validate_standards_reference_core(repo, tmp)
    _import_issues(report, tmp, "适配器/公共边界")
    if tmp.count(C.ERROR) == 0:
        report.add(K_OK, "config/targets.json 与 targets.py 代码常量一致（无双源漂移）")
        report.add(K_OK, "公共 core/standards/skills/agents 未混入工具专有配置字段")

    # core 被适配器正确引用：每个工具入口模板源应指向镜像内 core
    for tool, gs in T.GLOBAL_TOOL_SPECS.items():
        if gs.entry is None:
            continue
        src = repo / gs.entry.source
        if src.is_file() and "core/" in C.read_text(src):
            report.add(K_OK, f"global 入口 {tool} 引用了 core/ 索引")
        elif src.is_file():
            report.add(K_INCONSISTENT, f"global 入口 {tool} 未引用 core/（应提供规范索引）")

    # 生成文件新鲜度
    gen = C.Report("generated")
    CV.check_generated_freshness(repo, gen)
    SV.validate_global_entry_sources(repo, gen)
    _import_issues(report, gen, "生成文件")
    if gen.count(C.ERROR) == 0:
        n = sum(1 for g in T.GLOBAL_TOOL_SPECS.values() if g.entry is not None)
        report.add(K_OK, f"{n} 个 global 入口生成文件源均含版本 token，可用 "
                   f"installer.resolver.render_global_entry 渲染")

    # standards 无遗漏
    fcount = len(list((repo / "standards").rglob("*.md"))) if (repo / "standards").is_dir() else 0
    report.add(K_OK, f"standards/ 共 {fcount} 份技术规范，均从镜像引用而非重复维护")

    # 需人工
    report.add(K_MANUAL, "语义冲突（两条规则相互矛盾）无法自动判定，须人工评审（core/README.md §4）")
    report.add(K_MANUAL, "Hooks / 自定义 Agent 配置是否生成、各工具（含 WorkBuddy）真实加载行为，须人工验证")


def consistency_check(repo: Path, *, skills: bool, adapters: bool, all_: bool) -> int:
    report = ConsistencyReport("Repository consistency")
    do_skills = all_ or skills
    do_adapters = all_ or adapters
    if do_skills:
        check_skills_consistency(repo, report)
    if do_adapters:
        check_adapter_consistency(repo, report)
    print(report.render())
    if report.has_errors():
        print("[ERROR]   一致性检查发现问题，请修复「缺失/过期/不一致」项。", file=sys.stderr)
        return 1
    print("[INFO]    一致性检查通过（「需人工/无法验证」项不计入失败，须另行评审）。")
    return 0


# ===========================================================================
# 安装结果检查（保留 Phase 1-4 契约 + global 支持）
# ===========================================================================

def check_bundle_links(bundle: Path, report: C.Report, loc: str) -> None:
    """镜像必须自洽：所有内部相对链接都要在镜像内可解析。"""
    if not bundle.is_dir():
        return
    for p in sorted(bundle.rglob("*.md")):
        rel = p.relative_to(bundle).as_posix()
        for target, lineno in C.markdown_links(C.read_text(p)):
            clean = target.split("#")[0].strip()
            if not clean or clean.startswith(("http://", "https://", "mailto:", "data:")):
                continue
            if not (p.parent / clean).resolve().exists():
                report.error(f"L{lineno} 镜像内链接失效 -> {clean}", f"{loc}/{rel}")


def _layout(scope: str, tool: str):
    """返回 (skills_dir, [(source,dest,fmt,guarded), ...])。"""
    if scope == T.SCOPE_GLOBAL:
        gs = T.GLOBAL_TOOL_SPECS[tool]
        entries = [] if gs.entry is None else [
            (gs.entry.source, gs.entry.dest, gs.entry.fmt, gs.entry.guarded, True),
        ]
        return gs.skills_dir, entries
    ps = T.TOOL_SPECS[tool]
    entries = [(a.source, a.dest, a.fmt, a.guarded, False) for a in ps.all_adapters]
    return ps.skills_dir, entries


def installation_check(target: Path, repo: Path, scope_opt: str | None) -> int:
    report = C.Report(f"Installation check: {target}")
    manifest_rel = (T.GLOBAL_MANIFEST_REL if scope_opt == T.SCOPE_GLOBAL else T.MANIFEST_REL)
    manifest_path = target / Path(*manifest_rel.split("/"))
    if not manifest_path.is_file():
        # 兼容：显式 project 或未提供时，若 global 清单存在则提示
        alt = target / Path(*T.GLOBAL_MANIFEST_REL.split("/"))
        hint = ""
        if scope_opt is None and alt.is_file():
            hint = "；检测到 global 清单，请用 --scope global"
        report.error(f"未找到 {manifest_rel}；该目录可能尚未安装，或 --target 指错了层级"
                     f"（必须是安装根）{hint}。见 docs/installation.md §10")
        print(report.render(), file=sys.stderr)
        return 1
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        report.error(f"manifest.json 无法解析: {exc}", manifest_rel)
        print(report.render(), file=sys.stderr)
        return 1
    if not isinstance(manifest, dict):
        report.error("manifest.json 顶层必须是对象", manifest_rel)
        print(report.render(), file=sys.stderr)
        return 1

    scope = scope_opt or (manifest.get("scope") if manifest.get("scope") in T.SCOPES
                          else T.SCOPE_PROJECT)
    bundle_root = T.GLOBAL_BUNDLE_ROOT if scope == T.SCOPE_GLOBAL else T.BUNDLE_ROOT
    report.info(f"作用域 {scope}；安装器版本 {manifest.get('installer_version', '未知')}", "manifest")

    # 1. schema / 版本
    if manifest.get("schema") != T.MANIFEST_SCHEMA:
        report.error(f"manifest.schema={manifest.get('schema')!r}，本检查器支持 {T.MANIFEST_SCHEMA}",
                     manifest_rel)
    version = manifest.get("version")
    if not isinstance(version, str) or not version:
        report.error("manifest 缺少 version", manifest_rel)
    bv = target / Path(*f"{bundle_root}/VERSION".split("/"))
    if not bv.is_file():
        report.error("缺少已安装版本文件", f"{bundle_root}/VERSION")
    elif bv.read_text(encoding="utf-8").strip() != version:
        report.error("镜像 VERSION 与 manifest.version 不一致", f"{bundle_root}/VERSION")
    src_v = repo / "VERSION"
    if src_v.is_file() and src_v.read_text(encoding="utf-8").strip() != version:
        report.warning(f"已安装版本 {version}，规范仓库版本 "
                       f"{src_v.read_text(encoding='utf-8').strip()}；运行 install.py --force 更新")

    # 2. 逐文件完整性（含生成文件新鲜度）
    files = manifest.get("files")
    if not isinstance(files, dict) or not files:
        report.error("manifest.files 缺失或为空", manifest_rel)
        files = {}
    ok = 0
    generated_checked = 0
    for dest_rel, entry in sorted(files.items()):
        for prob in T.path_problems(dest_rel):
            report.error(f"清单中的目标路径不合法: {prob}", dest_rel)
        dest = target / Path(*dest_rel.split("/"))
        if not dest.is_file():
            report.error("清单登记的文件不存在", dest_rel)
            continue
        if not isinstance(entry, dict) or not isinstance(entry.get("sha256"), str):
            report.error("清单条目缺少 sha256", dest_rel)
            continue
        actual = C.sha256_file(dest)
        if actual != entry["sha256"]:
            report.error("文件哈希与清单不一致（可能被改动，或需 --force 更新）", dest_rel)
        else:
            ok += 1
        # 生成文件：用同一渲染函数重算期望内容，检测模板已变但文件未更新
        if entry.get("generated"):
            generated_checked += 1
            src = repo / entry.get("source", "") if isinstance(entry.get("source"), str) else None
            if src is not None and src.is_file():
                expected = R.render_global_entry(src.read_bytes(), version or "")
                if C.sha256_bytes(expected) != actual:
                    report.error("生成文件已过期（模板源已变化，需 --force 重新渲染）", dest_rel)
            else:
                report.warning("生成文件的模板源不在仓库中，无法验证新鲜度", dest_rel)
    report.info(f"{ok}/{len(files)} 个托管文件哈希校验通过；"
                f"{generated_checked} 个生成文件检查新鲜度", "完整性")

    # 3. 每工具 Skills 与适配器
    tools = manifest.get("tools", [])
    if not isinstance(tools, list) or not tools:
        report.warning("manifest.tools 为空，跳过工具布局检查")
        tools = []
    for tool in tools:
        specs = T.GLOBAL_TOOL_SPECS if scope == T.SCOPE_GLOBAL else T.TOOL_SPECS
        spec = specs.get(tool) if tool in specs else None
        if spec is None:
            report.warning(f"清单中的工具 {tool!r} 不在已知列表中，跳过")
            continue
        # project-only 工具（如 WorkBuddy）在 global 作用域被安装器完全跳过，
        # 不会写入用户级目录，因此这里也不应要求/校验其用户级布局。
        if scope == T.SCOPE_GLOBAL and getattr(spec, "project_only", False):
            report.info(f"工具 {tool!r} 为 project-only，global 作用域跳过用户级布局校验")
            continue
        skills_dir, entries = _layout(scope, tool)
        skills_root = target / Path(*skills_dir.split("/"))
        if not skills_root.is_dir():
            report.error("缺少 Skills 安装目录", skills_dir)
        for name in T.SKILL_NAMES:
            sd = skills_root / name
            if not (sd / "SKILL.md").is_file():
                report.error(f"缺少已安装的 Skill {name}/SKILL.md", skills_dir)
                continue
            C.check_skill(sd, report, f"installed/{skills_dir}/")
        for _src, dest_rel, _fmt, _guarded, is_global in entries:
            managed = dest_rel in files
            dest = target / Path(*dest_rel.split("/"))
            if not managed:
                # 未登记 = 安装时被识别为用户自有文件并跳过，不属于托管资产。
                # 不做格式/标记校验，也不判定为覆盖（Phase 1-4 既有契约）。
                if dest.is_file():
                    report.info(f"{dest_rel} 为用户自有文件，安装器已跳过（未被覆盖）")
                elif not is_global:
                    report.warning(f"可叠加适配器 {dest_rel} 未安装（可能被用户占用）")
                continue
            if not dest.is_file():
                report.error("清单登记的适配器/入口文件缺失", dest_rel)
                continue
            # global 入口用 global-entry 规格校验，project 用原格式
            if is_global:
                a = T.AdapterSpec(_src, dest_rel, guarded=True, fmt="global-entry")
            else:
                a = next(x for x in T.TOOL_SPECS[tool].all_adapters if x.dest == dest_rel)
            C.check_adapter(dest, a, report, "installed/")

    # 4. 镜像内部链接自洽
    check_bundle_links(target / Path(*bundle_root.split("/")), report, bundle_root)

    print(report.render())
    if report.count(C.ERROR):
        print("[ERROR]   安装结果检查未通过，请修复所有 ERROR。", file=sys.stderr)
    return report.exit_code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="check.py", description="规范一致性检查 / 安装结果检查",
    )
    parser.add_argument("--target", default=".", help="安装根（安装结果检查用，默认当前目录）")
    parser.add_argument("--repo", default=str(REPO), help="规范仓库根目录（版本比对用）")
    parser.add_argument("--scope", choices=list(T.SCOPES), default=None,
                        help="安装作用域；缺省时从 manifest.scope 推断（兼容旧清单）")
    parser.add_argument("--skills", action="store_true", help="只查 Skills 一致性（仓库内）")
    parser.add_argument("--adapters", action="store_true", help="只查适配器一致性（仓库内）")
    parser.add_argument("--all", action="store_true", help="仓库一致性全量检查")
    args = parser.parse_args(argv)

    try:
        repo = Path(args.repo).expanduser().resolve()
        if args.all or args.skills or args.adapters:
            return consistency_check(REPO, skills=args.skills, adapters=args.adapters, all_=args.all)
        target = Path(args.target).expanduser().resolve()
        return installation_check(target, repo, args.scope)
    except Exception as exc:  # noqa: BLE001 - 顶层兜底，工具失败返回 2
        print(f"[ERROR]    检查器执行失败: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
