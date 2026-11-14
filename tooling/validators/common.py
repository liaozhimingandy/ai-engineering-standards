"""共享校验逻辑：分级报告、Frontmatter 解析、Markdown / Skill / 适配器校验。

只用 Python 3.10+ 标准库。Frontmatter 采用**受约束的 YAML 子集**解析：
支持标量（含引号）、布尔、以及一层缩进的字符串映射 —— 这正好覆盖
Agent Skills 规范与各工具适配器实际使用的字段。不引入 PyYAML。

不宣称与官方 skills-ref 校验器完全等价；差异见 docs/tool-adapters.md §1。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import hashlib
import re

from . import targets as T

# ---------------------------------------------------------------------------
# 分级报告
# ---------------------------------------------------------------------------

ERROR = "ERROR"
WARNING = "WARNING"
INFO = "INFO"
_LEVEL_ORDER = {ERROR: 0, WARNING: 1, INFO: 2}


@dataclass
class Issue:
    level: str
    message: str
    location: str = ""


class Report:
    """ERROR 必须修复（退出码 1）；WARNING 建议修复；INFO 提示。"""

    def __init__(self, title: str = "Report") -> None:
        self.title = title
        self.issues: list[Issue] = []

    def add(self, level: str, message: str, location: str = "") -> None:
        self.issues.append(Issue(level, message, location))

    def error(self, message: str, location: str = "") -> None:
        self.add(ERROR, message, location)

    def warning(self, message: str, location: str = "") -> None:
        self.add(WARNING, message, location)

    def info(self, message: str, location: str = "") -> None:
        self.add(INFO, message, location)

    def count(self, level: str) -> int:
        return sum(1 for i in self.issues if i.level == level)

    @property
    def exit_code(self) -> int:
        return 1 if self.count(ERROR) else 0

    def render(self) -> str:
        lines = [f"=== {self.title} ==="]
        for issue in sorted(self.issues, key=lambda i: (_LEVEL_ORDER[i.level], i.location)):
            loc = f" [{issue.location}]" if issue.location else ""
            lines.append(f"[{issue.level:<7}]{loc} {issue.message}")
        lines.append(
            f"--- SUMMARY: {self.count(ERROR)} ERROR / "
            f"{self.count(WARNING)} WARNING / {self.count(INFO)} INFO ---"
        )
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------

def read_text(path: Path) -> str:
    return Path(path).read_text(encoding="utf-8")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(Path(path).read_bytes())


def has_marker(path: Path) -> bool:
    """归属标记检查：读文件头 2 KiB，避免整文件扫描。"""
    try:
        with Path(path).open("rb") as fh:
            head = fh.read(2048)
        return T.MARKER.encode("ascii") in head
    except OSError:
        return False


def strip_code(text: str) -> str:
    """把围栏代码块与行内代码置空（保留行号），用于链接/占位符检查。"""
    out: list[str] = []
    in_fence = False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            out.append("")
            continue
        if in_fence:
            out.append("")
        else:
            out.append(re.sub(r"`[^`]*`", "", line))
    return "\n".join(out)


def markdown_links(text: str) -> list[tuple[str, int]]:
    """返回 [(链接目标, 行号)]，跳过围栏代码块与行内代码。"""
    result: list[tuple[str, int]] = []
    for lineno, line in enumerate(strip_code(text).splitlines(), start=1):
        for match in re.finditer(r"\[[^\]]*\]\(([^)]+)\)", line):
            result.append((match.group(1).strip(), lineno))
    return result


def _scalar(raw: str) -> tuple[object, bool]:
    """解析一个 YAML 标量，返回 (值, 是否带引号)。"""
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in ("'", '"'):
        inner = raw[1:-1]
        inner = inner.replace('\\"', '"').replace("\\n", "\n").replace("\\\\", "\\")
        return inner, True
    low = raw.lower()
    if low in ("true", "false"):
        return low == "true", False
    if low in ("null", "~"):
        return None, False
    return raw, False


def parse_frontmatter(text: str) -> tuple[dict, str, list[str]]:
    """解析 SKILL.md / .mdc / .instructions.md 的 YAML frontmatter。

    返回 (data, body, errors)。data 的值可能是 str / bool / None / dict。
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text, ["Frontmatter 必须以文件第一行的 --- 开始"]
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
    if end is None:
        return {}, text, ["Frontmatter 缺少结束标记 ---"]
    body = "\n".join(lines[end + 1:])
    data: dict = {}
    errors: list[str] = []
    current_map: dict | None = None
    for lineno, raw in enumerate(lines[1:end], start=2):
        if not raw.strip():
            continue
        indented = raw[:1].isspace()
        if indented:
            if current_map is None:
                errors.append(f"L{lineno}: 出现意外缩进（只支持一层映射）")
                continue
            m = re.match(r"^\s+([A-Za-z0-9_.-]+):\s*(.*)$", raw)
            if not m:
                errors.append(f"L{lineno}: 无法解析映射项 {raw!r}")
                continue
            key, value = m.group(1), m.group(2).strip()
            parsed, quoted = _scalar(value)
            if parsed is None or (
                not quoted
                and re.match(r"^(true|false|null|~|[-+]?\d+(?:\.\d+)?)$", value, re.I)
            ):
                errors.append(f"L{lineno}: metadata 值必须是字符串；数值/布尔请加引号")
            current_map[key] = parsed
            continue
        if "\t" in raw:
            errors.append(f"L{lineno}: Frontmatter 不允许 tab 缩进")
        m = re.match(r"^([A-Za-z][A-Za-z0-9_-]*):\s*(.*)$", raw)
        if not m:
            errors.append(f"L{lineno}: 无法解析 {raw!r}")
            continue
        key, value = m.group(1), m.group(2).strip()
        if value == "":
            current_map = {}
            data[key] = current_map
        else:
            current_map = None
            data[key] = _scalar(value)[0]
    return data, body, errors


# ---------------------------------------------------------------------------
# Markdown 内容检查
# ---------------------------------------------------------------------------

_PLACEHOLDER_LINES = {"TODO", "TBD", "FIXME", "待补充", "占位符", "（待填）", "PLACEHOLDER"}


def check_markdown_content(path: Path, report: Report, location: str = "") -> None:
    """检查文件是否有实质内容、是否只是占位符。"""
    p = Path(path)
    loc = location or p.as_posix()
    raw = p.read_bytes()
    if len(raw) < 300:
        report.error("Markdown 文件内容过少（< 300 字节），疑似占位文件", loc)
        return
    text = raw.decode("utf-8", errors="replace")
    lines = text.splitlines()
    if len(lines) < 8:
        report.error("Markdown 文件行数过少（< 8 行），疑似占位文件", loc)
    nonspace = len(re.sub(r"\s+", "", strip_code(text)))
    if nonspace < 150:
        report.error("Markdown 有效正文过少（< 150 非空白字符）", loc)
    for lineno, line in enumerate(strip_code(text).splitlines(), start=1):
        if line.strip() in _PLACEHOLDER_LINES:
            report.error(f"L{lineno} 存在孤立占位行 {line.strip()!r}", loc)
    if not re.search(r"^#{1,4} ", text, re.M):
        report.error("缺少 Markdown 标题结构", loc)


# ---------------------------------------------------------------------------
# Skill 校验（Agent Skills 开放规范）
# ---------------------------------------------------------------------------

ALLOWED_SKILL_FIELDS = {
    "name", "description", "license", "compatibility", "metadata", "allowed-tools",
}
NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SKILL_REQUIRED_SECTIONS = (
    "触发场景", "适用范围", "执行步骤", "输入要求",
    "输出要求", "质量检查", "完成标准", "异常情况处理",
)
TRIGGER_HINTS = ("Use when", "use when", "用于", "当", "触发时")


def check_skill(skill_dir: Path, report: Report, location_prefix: str = "") -> bool:
    """按 Agent Skills 规范校验一个 Skill 目录。返回是否完全通过（无 ERROR）。"""
    d = Path(skill_dir)
    loc = f"{location_prefix}{d.name}/SKILL.md"
    skill_file = d / "SKILL.md"
    if not skill_file.is_file():
        report.error("缺少 SKILL.md", loc)
        return False
    text = read_text(skill_file)
    lines = text.splitlines()
    data, body, fm_errors = parse_frontmatter(text)
    for err in fm_errors:
        report.error(f"Frontmatter: {err}", loc)

    before = report.count(ERROR)

    # 字段白名单（工具扩展字段会破坏可移植性）
    for key in data:
        if key not in ALLOWED_SKILL_FIELDS:
            report.error(f"未知 Frontmatter 字段 {key!r}（只允许 6 个标准字段）", loc)

    name = data.get("name")
    if not isinstance(name, str) or not name:
        report.error("缺少必填字段 name", loc)
    else:
        if len(name) > 64:
            report.error(f"name 超过 64 字符（当前 {len(name)}）", loc)
        if not NAME_RE.match(name):
            report.error("name 只允许小写字母/数字/连字符，且不得以 - 开头结尾或含连续 --", loc)
        if name != d.name:
            report.error(f"name={name!r} 与目录名 {d.name!r} 不一致", loc)

    desc = data.get("description")
    if not isinstance(desc, str) or not desc.strip():
        report.error("缺少必填字段 description（非空）", loc)
    else:
        if len(desc) > 1024:
            report.error(f"description 超过 1024 字符（当前 {len(desc)}）", loc)
        if len(desc) < 40:
            report.warning("description 过短，可能不足以说明能力与触发场景", loc)
        if not any(h in desc for h in TRIGGER_HINTS):
            report.warning("description 未包含明确触发词（如 'Use when' / '用于'）", loc)

    if "compatibility" in data:
        comp = data["compatibility"]
        if not isinstance(comp, str):
            report.error("compatibility 必须是字符串", loc)
        elif len(comp) > 500:
            report.error(f"compatibility 超过 500 字符（当前 {len(comp)}）", loc)
    if "metadata" in data:
        meta = data["metadata"]
        if not isinstance(meta, dict):
            report.error("metadata 必须是 string→string 映射", loc)
        else:
            for k, v in meta.items():
                if not isinstance(k, str) or not isinstance(v, str):
                    report.error(f"metadata.{k} 的值必须是字符串", loc)
    if "allowed-tools" in data and not isinstance(data["allowed-tools"], str):
        report.error("allowed-tools 必须是空格分隔的字符串", loc)
    if "license" in data and not isinstance(data["license"], str):
        report.error("license 必须是字符串", loc)

    # 正文
    if not body.strip():
        report.error("SKILL.md 正文为空", loc)
    if len(lines) >= 500:
        report.error(f"SKILL.md 达到 {len(lines)} 行（规范要求 < 500 行）", loc)
    elif len(lines) >= 400:
        report.warning(f"SKILL.md 已 {len(lines)} 行，建议把详细资料移到 references/", loc)
    headings = set(re.findall(r"^##\s+(.+)$", body, re.M))
    for section in SKILL_REQUIRED_SECTIONS:
        if not any(section in h for h in headings):
            report.error(f"缺少必需章节「{section}」", loc)

    # 引用：只能指向技能目录内的一级资源，保证安装后链接仍成立
    for target, lineno in markdown_links(text):
        clean = target.split("#")[0]
        if not clean:
            continue
        if clean.startswith(("http://", "https://")):
            report.error(f"L{lineno} SKILL.md 不允许外部链接 {clean!r}（安装后无法保证）", loc)
            continue
        if clean.startswith("..") or clean.startswith("/"):
            report.error(f"L{lineno} SKILL.md 不允许引用技能目录之外的 {clean!r}", loc)
            continue
        if clean.count("/") > 1:
            report.error(f"L{lineno} 引用层级过深 {clean!r}（应保持一级深度）", loc)
        if not (d / clean).exists():
            report.error(f"L{lineno} 引用不存在 {clean!r}", loc)
        top = clean.split("/")[0]
        if top not in ("references", "scripts", "assets", "templates"):
            report.error(f"L{lineno} 引用了非标准资源目录 {top!r}", loc)
    for child in d.iterdir():
        if child.is_dir() and child.name not in ("references", "scripts", "assets", "templates"):
            report.warning(f"非标准资源目录 {child.name}/", loc)

    return report.count(ERROR) == before


# ---------------------------------------------------------------------------
# 适配器格式校验
# ---------------------------------------------------------------------------

_ADAPTER_FIELDS = {
    "plain": set(),
    "claude-rule": {"paths"},
    "cursor-mdc": {"description", "globs", "alwaysApply"},
    "copilot-path": {"applyTo", "excludeAgent"},
}


def check_adapter(path: Path, spec: "T.AdapterSpec", report: Report,
                  location_prefix: str = "") -> None:
    """校验适配器文件是否符合目标工具的官方约定。"""
    p = Path(path)
    loc = f"{location_prefix}{spec.dest}"
    if not p.is_file():
        report.error("适配器文件不存在", loc)
        return
    text = read_text(p)
    if not has_marker(p):
        report.error("缺少归属标记 AI-ENGINEERING-STANDARDS:MANAGED", loc)
    else:
        head = text[:2048]
        if "version=" not in head or "tool=" not in head:
            report.warning("归属标记缺少 version= 或 tool= 字段", loc)
    if len(text.strip()) < 300:
        report.error("适配器内容过少", loc)

    first = text.splitlines()[0].strip() if text.splitlines() else ""
    if spec.fmt in ("cursor-mdc", "copilot-path"):
        if first != "---":
            report.error("含 frontmatter 的适配器必须以 --- 开头（工具要求）", loc)
            return
        data, _body, errs = parse_frontmatter(text)
        for e in errs:
            report.error(f"Frontmatter: {e}", loc)
        allowed = _ADAPTER_FIELDS[spec.fmt]
        for key in data:
            if key not in allowed:
                report.error(f"写入了目标工具不支持的配置项 {key!r}", loc)
        if spec.fmt == "cursor-mdc":
            if "alwaysApply" not in data:
                report.error("Cursor .mdc 缺少 alwaysApply", loc)
            elif not isinstance(data["alwaysApply"], bool):
                report.error("alwaysApply 必须是布尔值", loc)
            if not isinstance(data.get("description"), str) or not data["description"].strip():
                report.error("Cursor .mdc 缺少非空 description", loc)
            if "applyTo" in data:
                report.error(".mdc 中不应出现 Copilot 的 applyTo 字段", loc)
        else:
            if not isinstance(data.get("applyTo"), str) or not data["applyTo"].strip():
                report.error("Copilot 路径级指令缺少非空 applyTo", loc)
            if "alwaysApply" in data or "globs" in data:
                report.error(".instructions.md 中不应出现 Cursor 的 alwaysApply/globs 字段", loc)
            if "excludeAgent" in data:
                report.warning("使用了 excludeAgent；其跨环境行为待验证", loc)
    else:
        if first == "---":
            data, _body, errs = parse_frontmatter(text)
            for e in errs:
                report.error(f"Frontmatter: {e}", loc)
            for key in data:
                if key not in _ADAPTER_FIELDS.get(spec.fmt, set()):
                    report.error(f"写入了目标工具不支持的配置项 {key!r}", loc)
        else:
            # 纯 Markdown 适配器不应出现任何工具专有配置字段
            for forbidden in ("applyTo:", "alwaysApply:", "globs:", "paths:"):
                if re.search(rf"^{re.escape(forbidden)}", text, re.M):
                    report.error(f"纯 Markdown 适配器不应包含 {forbidden!r}", loc)


# ---------------------------------------------------------------------------
# 仓库级检查
# ---------------------------------------------------------------------------

REQUIRED_DIRS = (
    "core", "standards/languages", "standards/backend", "standards/frontend",
    "standards/devops", "skills", "agents", "adapters", "tooling/scripts",
    "tooling/validators", "tooling/installer", "tooling/config",
    "tooling/templates", "tooling/tests", "examples", "docs",
)
REQUIRED_FILES = (
    "README.md", "CHANGELOG.md", "VERSION", "LICENSE", "CONTRIBUTING.md",
    "tooling/scripts/install.py", "tooling/scripts/validate.py",
    "tooling/scripts/check.py", "tooling/scripts/quality_gate.py",
    "tooling/scripts/release_check.py", "tooling/scripts/build_release_artifact.py",
    ".github/workflows/ci.yml", ".github/workflows/release.yml",
    "tooling/validators/targets.py",
    "tooling/validators/common.py", "tooling/validators/quality_rules.py",
    "tooling/validators/version_validator.py", "tooling/validators/skill_validator.py",
    "tooling/validators/markdown_validator.py", "tooling/validators/reference_validator.py",
    "tooling/validators/adapter_validator.py", "tooling/validators/consistency_validator.py",
    "tooling/installer/__init__.py", "tooling/installer/manager.py",
    "tooling/installer/resolver.py", "tooling/installer/filesystem.py",
    "tooling/installer/manifest.py", "tooling/installer/backup.py",
    "tooling/config/targets.json",
    "tooling/templates/SKILL.md.template",
    "tooling/templates/STANDARD.md.template", "tooling/tests/test_tooling.py",
    "docs/architecture.md", "docs/installation.md", "docs/skills-guide.md",
    "docs/standards-guide.md", "docs/versioning.md", "docs/tool-adapters.md",
    "docs/acceptance-report.md",
    # Phase 6 质量治理与发布体系文档
    "docs/quality/quality-gates.md", "docs/quality/regression-testing.md",
    "docs/quality/compatibility-testing.md",
    "docs/release/versioning.md", "docs/release/release-process.md",
    "docs/release/release-checklist.md",
    # Phase 6 腾讯 WorkBuddy 适配器（第 5 个工具）
    "adapters/workbuddy/README.md", "adapters/workbuddy/mappings.md",
    "adapters/workbuddy/references.md", "adapters/workbuddy/CODEBUDDY.md",
    "adapters/workbuddy/rules/ai-engineering-standards.md",
    "adapters/workbuddy/templates/agent-mapping.template.md",
    "adapters/workbuddy/templates/codebuddy-rules-snippet.template.md",
)


def check_required_structure(repo: Path, report: Report) -> None:
    for d in REQUIRED_DIRS:
        if not (repo / d).is_dir():
            report.error(f"缺少必要目录 {d}/")
    for f in REQUIRED_FILES:
        if not (repo / f).is_file():
            report.error(f"缺少必要文件 {f}")
    for name in T.SKILL_NAMES:
        if not (repo / "skills" / name).is_dir():
            report.error(f"缺少首批 Skill 目录 skills/{name}/")


def check_version(repo: Path, report: Report) -> str:
    version = ""
    vf = repo / "VERSION"
    if not vf.is_file():
        report.error("缺少 VERSION")
        return version
    version = vf.read_text(encoding="utf-8").strip()
    if not re.match(r"^\d+\.\d+\.\d+$", version):
        report.error(f"VERSION 不是语义化版本: {version!r}")
    changelog = repo / "CHANGELOG.md"
    if changelog.is_file():
        # 按"版本段标题"精确匹配，而非前 2000 字符 —— 后者会随 Unreleased 段落增多
        # 把 [1.0.0] 挤出窗口，产生随文档增长而出现的假 WARNING。
        text = changelog.read_text(encoding="utf-8")
        if not re.search(rf"^##\s*\[{re.escape(version)}\]", text, re.M):
            report.warning(f"CHANGELOG.md 未找到版本段 [{version}]")
    return version


def check_internal_links(repo: Path, report: Report) -> None:
    """检查仓库内所有 Markdown 的相对引用是否存在。"""
    repo = Path(repo)
    for p in sorted(repo.rglob("*.md")):
        rel = p.relative_to(repo).as_posix()
        if rel.startswith(".ai-standards/") or rel.startswith("tooling/tests/"):
            continue
        for target, lineno in markdown_links(read_text(p)):
            clean = target.split("#")[0].strip()
            if not clean or clean.startswith(("http://", "https://", "mailto:", "data:")):
                continue
            dest = (p.parent / clean).resolve()
            if not dest.exists():
                report.error(f"L{lineno} 无效内部引用 -> {clean}", rel)
            elif rel.startswith("skills/"):
                # 技能目录内引用必须留在该技能目录里
                skill_root = repo / "skills" / rel.split("/")[1]
                try:
                    dest.relative_to(skill_root.resolve())
                except ValueError:
                    report.error(f"L{lineno} 引用越出技能目录 -> {clean}", rel)


def check_duplicates(repo: Path, report: Report) -> None:
    """内容完全相同的 Markdown 文件通常是规则被复制粘贴的信号。"""
    seen: dict[str, list[str]] = {}
    for p in sorted(repo.rglob("*.md")):
        rel = p.relative_to(repo).as_posix()
        if rel.startswith(".ai-standards/"):
            continue
        h = sha256_file(p)
        seen.setdefault(h, []).append(rel)
    for group in seen.values():
        if len(group) > 1:
            report.warning("内容完全相同的重复文件: " + ", ".join(group))


def check_install_targets(repo: Path, report: Report) -> None:
    for level, msg in T.check_paths(repo):
        report.add(level, msg, "安装目标路径")
