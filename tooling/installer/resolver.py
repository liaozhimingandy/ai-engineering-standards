"""安装计划解析：公共源 -> 目标文件清单（project / global 双作用域）。

本模块是"装什么、装到哪里、是否需要渲染"的唯一解析器。install.py 与
check.py 的一致性检查共用 render_global_entry，确保**生成规则不出现双源**。

作用域差异：
- project：镜像 <target>/.ai-standards/；Skills/入口按 ToolSpec 官方项目级路径。
- global ：镜像 <home>/.ai-engineering-standards/bundle/；Skills/入口按 GlobalToolSpec
           官方用户级路径；Cursor 无全局入口（不编造）；另生成 GLOBAL-INTEGRATION.md。

两种内容形态：
- 逐字节复制（bundle/skill/project-adapter）：dest 内容 == 源内容。
- 生成文件（global ENTRY）：源模板含 `version=GENERATED`，渲染时替换为规范版本。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from validators import targets as T


# 生成 token 定义在共享常量层 validators.targets（避免 validators 反向依赖 installer）
VERSION_TOKEN = T.GENERATED_VERSION_TOKEN


@dataclass(frozen=True)
class PlannedFile:
    dest_rel: str             # 相对安装根，POSIX
    source_abs: Path          # 源文件绝对路径
    source_rel: str           # 相对仓库根，写进 manifest
    category: str             # bundle | skill | adapter | generated
    generated: bool = False
    guarded: bool = False     # 入口文件：极易被用户占用，默认跳过

    def expected_bytes(self, version: str) -> bytes:
        """返回应写入目标的内容字节（生成文件按规则渲染）。"""
        raw = self.source_abs.read_bytes()
        if self.generated:
            return render_global_entry(raw, version)
        return raw


def render_global_entry(source: bytes, version: str) -> bytes:
    """确定性生成 global 入口：仅把 `version=GENERATED` 替换为真实版本。

    - 纯字节替换，不改换行符（避免 Windows 上 CRLF 被转换）；
    - 不做其它改写，保证 check 可用同一函数重算期望内容判断是否过期；
    - 未找到 token 时原样返回（由 validator 在源检查阶段报"缺 token"）。
    """
    return source.replace(VERSION_TOKEN.encode("ascii"),
                          f"version={version}".encode("ascii"))


def _ignored(path: Path) -> bool:
    parts = set(path.parts)
    if "__pycache__" in parts or ".git" in parts:
        return True
    return path.name == ".DS_Store" or path.suffix in {".pyc", ".pyo"}


def version_of(repo: Path) -> str:
    vf = Path(repo) / "VERSION"
    return vf.read_text(encoding="utf-8").strip() if vf.is_file() else "unknown"


def _sort(plan: list[PlannedFile]) -> list[PlannedFile]:
    # bundle 在前、Skills 次之、入口/生成文件最后；同类按路径排序，输出稳定
    order = {"bundle": 0, "skill": 1, "generated": 2, "adapter": 2}
    return sorted(plan, key=lambda p: (order.get(p.category, 9), p.dest_rel))


class _Builder:
    def __init__(self, repo: Path) -> None:
        self.repo = repo
        self.plan: list[PlannedFile] = []
        self._seen: set[str] = set()

    def add(self, dest: str, src: Path, category: str, *,
            generated: bool = False, guarded: bool = False) -> None:
        if dest in self._seen:
            raise ValueError(f"安装计划中出现重复目标路径: {dest}")
        self._seen.add(dest)
        self.plan.append(PlannedFile(
            dest, src, src.relative_to(self.repo).as_posix(),
            category, generated, guarded,
        ))

    def bundle(self, root: str) -> None:
        """规范镜像：保持目录结构，保证文件间相对链接仍成立。"""
        for rel in T.BUNDLE_ROOT_FILES:
            self.add(f"{root}/{rel}", self.repo / rel, "bundle")
        for d in T.BUNDLE_DIRS:
            for src in sorted((self.repo / d).rglob("*")):
                if src.is_file() and not _ignored(src):
                    self.add(f"{root}/{src.relative_to(self.repo).as_posix()}", src, "bundle")

    def skills(self, skills_dir: str) -> None:
        for name in T.SKILL_NAMES:
            skill_dir = self.repo / "skills" / name
            for src in sorted(skill_dir.rglob("*")):
                if src.is_file() and not _ignored(src):
                    rel = src.relative_to(skill_dir).as_posix()
                    self.add(f"{skills_dir}/{name}/{rel}", src, "skill")


def build_plan(repo: Path, tools: list[str], scope: str) -> list[PlannedFile]:
    """生成安装计划。同一目标路径只出现一次（跨工具路径冲突已由 targets.check_paths 校验）。"""
    b = _Builder(repo)
    if scope == T.SCOPE_GLOBAL:
        b.bundle(T.GLOBAL_BUNDLE_ROOT)
        b.add(T.GLOBAL_INTEGRATION_DEST, repo / T.GLOBAL_INTEGRATION_SOURCE, "generated")
        for tool in tools:
            spec = T.GLOBAL_TOOL_SPECS[tool]
            # project-only 工具（如 WorkBuddy）：任务书/官方约束不写任何用户级目录，
            # global 作用域完全跳过，既不装 Skills 也不生成入口。
            if spec.project_only:
                continue
            b.skills(spec.skills_dir)
            if spec.entry is not None:
                b.add(spec.entry.dest, repo / spec.entry.source, "generated",
                      generated=True, guarded=True)
    else:
        b.bundle(T.BUNDLE_ROOT)
        for tool in tools:
            spec = T.TOOL_SPECS[tool]
            b.skills(spec.skills_dir)
            for a in spec.all_adapters:
                b.add(a.dest, repo / a.source, "adapter", guarded=a.guarded)
    return _sort(b.plan)
