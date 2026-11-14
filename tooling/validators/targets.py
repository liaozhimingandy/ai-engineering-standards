"""安装目标定义 —— 唯一的路径事实来源。

所有路径都是**相对目标项目根目录的 POSIX 风格路径**。install.py 与 check.py
共用本模块；新增工具只需在这里加一个 ToolSpec，不需要改架构。

路径与格式依据官方文档核实（2026-11-11），见 docs/tool-adapters.md。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import re

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

TOOLS: tuple[str, ...] = ("codex", "claude", "cursor", "copilot", "workbuddy")
TOOL_ALIASES: dict[str, str] = {
    "claude-code": "claude",
    "codebuddy": "workbuddy",          # WorkBuddy 的 CLI 引擎名 CodeBuddy Code
    "tencent-workbuddy": "workbuddy",
}

MARKER = "AI-ENGINEERING-STANDARDS:MANAGED"
BUNDLE_ROOT = ".ai-standards"
MANIFEST_REL = ".ai-standards/manifest.json"
VERSION_REL = ".ai-standards/VERSION"
MANIFEST_SCHEMA = 1

# 安装作用域。project = 目标项目根目录（Phase 1-4 行为，缺省）；
# global = 当前用户 home 目录（Phase 5 新增）。
SCOPE_PROJECT = "project"
SCOPE_GLOBAL = "global"
SCOPES: tuple[str, ...] = (SCOPE_PROJECT, SCOPE_GLOBAL)

# global 作用域的管理目录（home 下）。不复用 .ai-standards/ 这个名字，
# 以免与用户可能在 home 里做的 project 安装混淆；命名直观且不与各工具冲突。
GLOBAL_MANAGE_ROOT = ".ai-engineering-standards"
GLOBAL_BUNDLE_ROOT = f"{GLOBAL_MANAGE_ROOT}/bundle"
GLOBAL_MANIFEST_REL = f"{GLOBAL_MANAGE_ROOT}/manifest.json"
GLOBAL_BACKUPS_DIR = f"{GLOBAL_MANAGE_ROOT}/backups"
PROJECT_BACKUPS_DIR = f"{BUNDLE_ROOT}/backups"

# Phase 5 安装器版本（与规范 VERSION 分线管理；manifest 新增 installer_version 字段）。
INSTALLER_VERSION = "1.0.0"

# global 入口模板的"生成 token"：安装器渲染时替换为真实规范版本。
# 放在共享常量层，让 installer/resolver 与 validators 都能引用，
# 避免验证逻辑反向依赖安装逻辑（layers: validators 不 import installer）。
GENERATED_VERSION_TOKEN = "version=GENERATED"

SKILL_NAMES: tuple[str, ...] = (
    "project-analysis",
    "requirements-analysis",
    "architecture-design",
    "software-development",
    "code-review",
)

# .ai-standards/ 是规范仓库的只读镜像：保持目录结构，使文件间相对链接在安装后仍成立。
# 不含 tooling/scripts、tooling/validators、tooling/tests 与 examples。
BUNDLE_ROOT_FILES: tuple[str, ...] = (
    "README.md",
    "CHANGELOG.md",
    "VERSION",
    "LICENSE",
    "CONTRIBUTING.md",
)
BUNDLE_DIRS: tuple[str, ...] = (
    "core",
    "standards",
    "skills",
    "agents",
    "adapters",
    "docs",
    "tooling/templates",
)

# Windows 保留设备名（作为路径片段时非法）
_WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AdapterSpec:
    """一个适配器源文件到目标文件的映射。"""
    source: str                 # 相对仓库根，POSIX
    dest: str                   # 相对目标项目根，POSIX
    guarded: bool = False       # True：入口文件，极易被用户占用
    fmt: str = "plain"          # plain | cursor-mdc | copilot-path | claude-rule


@dataclass(frozen=True)
class ToolSpec:
    name: str
    skills_dir: str             # 官方项目级 Skills 发现目录
    adapters: tuple[AdapterSpec, ...]      # 入口文件（guarded）
    additive: tuple[AdapterSpec, ...]      # 可叠加文件（唯一文件名）
    note: str = ""

    @property
    def all_adapters(self) -> tuple[AdapterSpec, ...]:
        return self.adapters + self.additive


TOOL_SPECS: dict[str, ToolSpec] = {
    "codex": ToolSpec(
        name="codex",
        skills_dir=".agents/skills",
        adapters=(
            AdapterSpec("adapters/codex/AGENTS.md", "AGENTS.md", guarded=True),
        ),
        additive=(),
        note="官方：AGENTS.md 位于项目根；REPO 作用域 Skills 在 .agents/skills",
    ),
    "claude": ToolSpec(
        name="claude",
        skills_dir=".claude/skills",
        adapters=(
            AdapterSpec("adapters/claude-code/CLAUDE.md", "CLAUDE.md", guarded=True),
        ),
        additive=(
            AdapterSpec(
                "adapters/claude-code/rules/ai-engineering-standards.md",
                ".claude/rules/ai-engineering-standards.md",
                guarded=False,
                fmt="claude-rule",
            ),
        ),
        note="官方：.claude/skills/<name>/SKILL.md；.claude/rules/*.md 可叠加加载",
    ),
    "cursor": ToolSpec(
        name="cursor",
        skills_dir=".cursor/skills",
        adapters=(),
        additive=(
            AdapterSpec(
                "adapters/cursor/rules/ai-engineering-standards.mdc",
                ".cursor/rules/ai-engineering-standards.mdc",
                guarded=False,
                fmt="cursor-mdc",
            ),
        ),
        note="官方：Project Rules 必须是 .cursor/rules/*.mdc；Skills 在 .cursor/skills",
    ),
    "copilot": ToolSpec(
        name="copilot",
        skills_dir=".github/skills",
        adapters=(
            AdapterSpec(
                "adapters/copilot/copilot-instructions.md",
                ".github/copilot-instructions.md",
                guarded=True,
            ),
        ),
        additive=(
            AdapterSpec(
                "adapters/copilot/instructions.md",
                ".github/instructions/ai-engineering-standards.instructions.md",
                guarded=False,
                fmt="copilot-path",
            ),
        ),
        note="官方：仓库级 .github/copilot-instructions.md；路径级 *.instructions.md 需 applyTo",
    ),
    "workbuddy": ToolSpec(
        name="workbuddy",
        skills_dir=".codebuddy/skills",
        adapters=(
            AdapterSpec("adapters/workbuddy/CODEBUDDY.md", "CODEBUDDY.md", guarded=True),
        ),
        additive=(
            AdapterSpec(
                "adapters/workbuddy/rules/ai-engineering-standards.md",
                ".codebuddy/rules/ai-engineering-standards.md",
                guarded=False,
                fmt="plain",
            ),
        ),
        note="官方：CODEBUDDY.md 项目记忆入口（与 AGENTS.md 兼容且优先）；"
             ".codebuddy/rules/*.md 递归自动加载、无 frontmatter 默认常驻；"
             "Skills 在 .codebuddy/skills/<name>/SKILL.md；仅 project 作用域，不写用户级目录",
    ),
}


# ---------------------------------------------------------------------------
# global（用户级 home）作用域
# ---------------------------------------------------------------------------
# 路径事实同样来自 Phase 4 能力矩阵（docs/architecture/tool-compatibility.md §2/§3）：
#   Codex   — 全局规则 ~/.codex/AGENTS.md [SUPPORTED]；用户级 Skills ~/.agents/skills
#   Claude  — 全局规则 ~/.claude/CLAUDE.md [SUPPORTED]；用户级 Skills ~/.claude/skills
#   Cursor  — User Rules 仅 Customize UI，磁盘无项目可写路径 [PARTIAL] → 不生成全局入口
#   Copilot — 仅 CLI 有 ~/.copilot/copilot-instructions.md [PARTIAL] → 生成但标注 PARTIAL
#   WorkBuddy — 官方有 ~/.codebuddy/，但任务书禁写用户级目录 [project_only] → global 完全跳过
# 规范镜像只有一份，放 ~/.ai-engineering-standards/bundle/，各工具共享。
# 参与 global 的工具 Skills 各用官方"专属"用户级目录，避免多工具争用同一目录产生重复副本；
# project-only 工具（WorkBuddy）不参与 global，无用户级目录。

@dataclass(frozen=True)
class GlobalToolSpec:
    name: str
    skills_dir: str                      # 相对 home 的官方用户级 Skills 目录
    entry: "AdapterSpec | None"          # 全局规则入口；None = 官方无磁盘可写路径
    entry_status: str                    # SUPPORTED | PARTIAL（该入口能力等级）
    note: str
    project_only: bool = False           # True：任务书/官方约束，global 作用域完全跳过（不写任何用户级目录）


GLOBAL_TOOL_SPECS: dict[str, GlobalToolSpec] = {
    "codex": GlobalToolSpec(
        name="codex",
        skills_dir=".agents/skills",
        entry=AdapterSpec(
            "adapters/codex/global/ENTRY.md", ".codex/AGENTS.md",
            guarded=True, fmt="global-entry",
        ),
        entry_status="SUPPORTED",
        note="官方：~/.codex/AGENTS.md；CODEX_HOME 可改 home；用户级 Skills $HOME/.agents/skills",
    ),
    "claude": GlobalToolSpec(
        name="claude",
        skills_dir=".claude/skills",
        entry=AdapterSpec(
            "adapters/claude-code/global/ENTRY.md", ".claude/CLAUDE.md",
            guarded=True, fmt="global-entry",
        ),
        entry_status="SUPPORTED",
        note="官方：~/.claude/CLAUDE.md；用户级 Skills ~/.claude/skills；无硬优先级",
    ),
    "cursor": GlobalToolSpec(
        name="cursor",
        skills_dir=".cursor/skills",
        entry=None,                       # 官方 User Rules 磁盘无路径，禁止编造
        entry_status="PARTIAL",
        note="官方：用户级 Skills ~/.cursor/skills；User Rules 仅在 Customize UI 配置，无磁盘路径 → 不生成全局入口",
    ),
    "copilot": GlobalToolSpec(
        name="copilot",
        skills_dir=".copilot/skills",
        entry=AdapterSpec(
            "adapters/copilot/global/ENTRY.md", ".copilot/copilot-instructions.md",
            guarded=True, fmt="global-entry",
        ),
        entry_status="PARTIAL",
        note="官方：仅 Copilot CLI 有 ~/.copilot/copilot-instructions.md（IDE 组织级靠 UI）；用户级 Skills ~/.copilot/skills",
    ),
    # WorkBuddy 是 project-only 工具：任务书明令"不创建用户级全局规则""不写入用户级目录"。
    # global 作用域由 build_plan 与 check.py 统一跳过（见 project_only 守卫），
    # 绝不写 ~/.codebuddy/ 下的 skills / rules / CODEBUDDY.md。
    "workbuddy": GlobalToolSpec(
        name="workbuddy",
        skills_dir=".codebuddy/skills",
        entry=None,
        entry_status="PARTIAL",
        project_only=True,
        note="官方有用户级 ~/.codebuddy/，但任务书禁止写用户级目录 → project_only=True，"
             "global 作用域由安装器与校验器统一跳过；仅支持 --scope project",
    ),
}

# global 安装时始终生成的手工集成说明（源文件 → home 管理目录）
GLOBAL_INTEGRATION_SOURCE = "adapters/GLOBAL-INTEGRATION.template.md"
GLOBAL_INTEGRATION_DEST = f"{GLOBAL_MANAGE_ROOT}/GLOBAL-INTEGRATION.md"


def normalize_scope(name: str) -> str | None:
    """规范化安装作用域；无法识别返回 None。"""
    key = name.strip().lower()
    return key if key in SCOPES else None


# ---------------------------------------------------------------------------
# 路径合法性（跨平台）
# ---------------------------------------------------------------------------

def path_problems(rel: str) -> list[str]:
    """返回一条相对路径的合法性问题列表；空列表表示合法。"""
    problems: list[str] = []
    if not rel or not isinstance(rel, str):
        return ["路径为空"]
    if "\\" in rel:
        problems.append("包含反斜杠（必须使用 POSIX 风格 /）")
    if rel.startswith("/") or rel.startswith("~"):
        problems.append("不允许绝对路径或 ~ 开头")
    if re.match(r"^[A-Za-z]:", rel):
        problems.append("包含 Windows 盘符，不允许绝对路径")
    p = PurePosixPath(rel)
    if p.is_absolute():
        problems.append("是绝对路径")
    for part in p.parts:
        if part in ("", ".", ".."):
            problems.append(f"含非法片段 {part!r}")
        if part.upper().split(".")[0] in _WINDOWS_RESERVED:
            problems.append(f"含 Windows 保留名 {part!r}")
        if part.endswith(" ") or part.endswith("."):
            problems.append(f"片段 {part!r} 以空格或点结尾（Windows 不合法）")
    return problems


def check_paths(repo_root: Path) -> list[tuple[str, str]]:
    """静态校验全部安装目标与源文件路径。返回 [(level, message), ...]。"""
    out: list[tuple[str, str]] = []
    repo_root = Path(repo_root)

    def bad(msg: str) -> None:
        out.append(("ERROR", msg))

    # 1. 源目录与源文件
    for name in SKILL_NAMES:
        skill = repo_root / "skills" / name / "SKILL.md"
        if not skill.is_file():
            bad(f"缺少 Skill 源文件 skills/{name}/SKILL.md")
    for rel in BUNDLE_ROOT_FILES:
        if not (repo_root / rel).is_file():
            bad(f"缺少镜像根文件 {rel}")
    for d in BUNDLE_DIRS:
        if not (repo_root / d).is_dir():
            bad(f"缺少镜像目录 {d}/")

    # 2. 每个工具的路径
    seen_dest: dict[str, str] = {}
    for tool, spec in TOOL_SPECS.items():
        for label, rel in [("skills_dir", spec.skills_dir)]:
            for prob in path_problems(rel):
                bad(f"[{tool}] {label}={rel!r}: {prob}")
        for a in spec.all_adapters:
            for prob in path_problems(a.dest):
                bad(f"[{tool}] dest={a.dest!r}: {prob}")
            for prob in path_problems(a.source):
                bad(f"[{tool}] source={a.source!r}: {prob}")
            if not (repo_root / a.source).is_file():
                bad(f"[{tool}] 适配器源文件不存在: {a.source}")
            if a.dest in seen_dest:
                bad(f"安装目标冲突: {a.dest!r} 同时属于 {seen_dest[a.dest]} 与 {tool}")
            seen_dest[a.dest] = tool
            if a.fmt == "cursor-mdc" and not a.dest.endswith(".mdc"):
                bad(f"[{tool}] Cursor Project Rule 必须以 .mdc 结尾: {a.dest}")
            if a.fmt == "copilot-path" and not a.dest.endswith(".instructions.md"):
                bad(f"[{tool}] Copilot 路径级指令必须以 .instructions.md 结尾: {a.dest}")
        # Skills 目标目录不能与其他工具的适配器目标冲突
        for name in SKILL_NAMES:
            dest = f"{spec.skills_dir}/{name}/SKILL.md"
            for prob in path_problems(dest):
                bad(f"[{tool}] skill dest={dest!r}: {prob}")

    # 3. 镜像目标不得越出 .ai-standards
    for d in BUNDLE_DIRS:
        for prob in path_problems(f"{BUNDLE_ROOT}/{d}"):
            bad(f"镜像目录 {d}: {prob}")

    # 4. global 作用域路径与源文件（Phase 5）
    for prob in path_problems(GLOBAL_MANIFEST_REL):
        bad(f"global 管理路径: {prob}")
    for prob in path_problems(GLOBAL_INTEGRATION_DEST):
        bad(f"global 集成说明路径: {prob}")
    if not (repo_root / GLOBAL_INTEGRATION_SOURCE).is_file():
        bad(f"缺少 global 集成说明源文件 {GLOBAL_INTEGRATION_SOURCE}")
    g_seen: dict[str, str] = {}
    for tool, spec in GLOBAL_TOOL_SPECS.items():
        for label, rel in [("skills_dir", spec.skills_dir)]:
            for prob in path_problems(rel):
                bad(f"[global/{tool}] {label}={rel!r}: {prob}")
        for name in SKILL_NAMES:
            dest = f"{spec.skills_dir}/{name}/SKILL.md"
            for prob in path_problems(dest):
                bad(f"[global/{tool}] skill dest={dest!r}: {prob}")
        if spec.entry is not None:
            for prob in path_problems(spec.entry.dest):
                bad(f"[global/{tool}] entry dest={spec.entry.dest!r}: {prob}")
            for prob in path_problems(spec.entry.source):
                bad(f"[global/{tool}] entry source={spec.entry.source!r}: {prob}")
            if not (repo_root / spec.entry.source).is_file():
                bad(f"[global/{tool}] 入口源文件不存在: {spec.entry.source}")
            if spec.entry.dest in g_seen:
                bad(f"global 安装目标冲突: {spec.entry.dest!r} 同时属于 {g_seen[spec.entry.dest]} 与 {tool}")
            g_seen[spec.entry.dest] = tool
            if spec.entry_status not in ("SUPPORTED", "PARTIAL"):
                bad(f"[global/{tool}] entry_status 非法: {spec.entry_status!r}") 
        # 入口互斥：Cursor 官方无磁盘路径，entry 必须为 None
        if tool == "cursor" and spec.entry is not None:
            bad("[global/cursor] 官方 User Rules 无磁盘可写路径，entry 必须为 None")
        # project-only 工具（如 WorkBuddy）：不得配置全局入口，也不得被 global 安装
        if spec.project_only and spec.entry is not None:
            bad(f"[global/{tool}] project_only 工具的 entry 必须为 None（禁止写用户级目录）")
    # 各工具 global Skills 目录不得互相冲突
    gd: dict[str, str] = {}
    for tool, spec in GLOBAL_TOOL_SPECS.items():
        if spec.skills_dir in gd:
            bad(f"global Skills 目录冲突: {spec.skills_dir!r} 同属 {gd[spec.skills_dir]} 与 {tool}")
        gd[spec.skills_dir] = tool
    return out


def normalize_tool(name: str) -> str | None:
    """把命令行工具名规范化；无法识别返回 None。"""
    key = name.strip().lower()
    key = TOOL_ALIASES.get(key, key)
    return key if key in TOOLS else None
