"""版本一致性校验（Phase 6，任务书 §五）。

建立统一版本事实来源，防止"不同文件各自维护互相矛盾的版本号"：

事实来源层级（只上不下）：
  - 规范版本：``VERSION``（单行 SemVer）——唯一权威。
  - 安装器版本：``targets.INSTALLER_VERSION``，并在 ``config/targets.json``
    有一份**可校验导出快照**；两者必须一致（否则 ERROR）。
  - 发布记录：``CHANGELOG.md`` 的 ``## [X.Y.Z]`` 段。
  - 对外展示：``README.md`` 的 ``version-X.Y.Z`` badge。

本模块**只做只读检查 + 采集**，不修改任何文件；所有校验结果写入调用方
提供的 ``C.Report``，遵循 Phase 5 的分层（validators 不 import installer）。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from . import common as C
from . import targets as T

# 严格 SemVer：MAJOR.MINOR.PATCH，允许可选的 -prerelease / +build。
# 本项目 1.x 发布只用三段；放宽到完整 SemVer 以符合任务书「使用语义化版本号」。
SEMVER_RE = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-((?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*)"
    r"(?:\.(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*))*))?"
    r"(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)


@dataclass(frozen=True)
class VersionInfo:
    """一次版本采集的快照，供 quality_gate / release_check 复用。"""

    spec: str = ""                 # VERSION 文件
    installer_code: str = ""       # targets.INSTALLER_VERSION
    installer_config: str = ""     # config/targets.json.installer_version
    readme_badge: str = ""         # README version badge
    changelog_latest: str = ""     # CHANGELOG 顶部已发版段版本号
    changelog_has_current: bool = False  # CHANGELOG 是否含当前 spec 段
    collector: str = ""            # targets.json.installer_version（同 installer_config，冗余采集）
    manifest_schema: int = 0       # targets.MANIFEST_SCHEMA

    @property
    def all_consistent(self) -> bool:
        return (
            self.spec != ""
            and self.readme_badge == self.spec
            and self.installer_code == self.installer_config
            and self.changelog_has_current
        )


def is_semver(value: str) -> bool:
    if not isinstance(value, str):
        return False
    v = value.strip()
    # 严格 SemVer 语法；额外拒绝带文件扩展名（如 shields.io 颜色段 -blue.svg）
    # 这类"看似 prerelease 实为文件名"的值，避免 badge 颜色被误收作版本号。
    if v.endswith((".svg", ".png", ".json", ".md")):
        return False
    return bool(SEMVER_RE.match(v))


def version_from_tag(tag: str) -> str | None:
    """从 Git Tag 解析版本号（可选 ``v`` 前缀），返回合法 SemVer 或 None。

    Git Tag 必须符合版本号规则（任务书 §六/§七）。workflow 的 on.push.tags glob
    只做粗筛，严格校验统一走这里，避免 glob 与正则两套规则漂移。
    """
    if not isinstance(tag, str):
        return None
    t = tag.strip()
    if t[:1] in ("v", "V"):
        t = t[1:]
    return t if is_semver(t) else None


def _read_version_file(repo: Path, report: C.Report) -> str:
    p = repo / "VERSION"
    if not p.is_file():
        report.error("缺少 VERSION（规范版本唯一事实来源）", "VERSION")
        return ""
    raw = C.read_text(p).strip()
    if not is_semver(raw):
        report.error(f"VERSION 不是合法语义化版本号: {raw!r}（应为 MAJOR.MINOR.PATCH）", "VERSION")
        return raw  # 继续采集其它来源，便于一次报全
    return raw


def _read_installer_versions(repo: Path, report: C.Report) -> tuple[str, str]:
    code = T.INSTALLER_VERSION
    if not is_semver(code):
        report.error(f"targets.INSTALLER_VERSION 不是合法 SemVer: {code!r}",
                     "tooling/validators/targets.py")
    cfg_path = repo / "tooling/config/targets.json"
    cfg_val = ""
    if not cfg_path.is_file():
        report.error("缺少 tooling/config/targets.json", "tooling/config/targets.json")
    else:
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            report.error(f"targets.json 无法解析: {exc}", "tooling/config/targets.json")
            cfg = None
        if isinstance(cfg, dict):
            cfg_val = cfg.get("installer_version", "")
            if not isinstance(cfg_val, str) or not is_semver(cfg_val):
                report.error(f"config.installer_version 不是合法 SemVer: {cfg_val!r}",
                             "tooling/config/targets.json")
    if code and cfg_val and code != cfg_val:
        report.error(
            f"安装器版本双源不一致：targets.INSTALLER_VERSION={code!r} 与 "
            f"config/targets.json.installer_version={cfg_val!r} 不相同",
            "版本关系",
        )
    # 明确版本关系：安装器版本与规范版本可不同线，但必须各自合法且关系显式。
    return code, cfg_val if isinstance(cfg_val, str) else ""


def _read_readme_badge(repo: Path, report: C.Report, spec: str) -> str:
    p = repo / "README.md"
    if not p.is_file():
        report.error("缺少 README.md（对外版本展示）", "README.md")
        return ""
    # shields.io badge 形如 ``version-1.0.0-blue.svg``：版本号后面还会拼颜色与
    # 扩展名（``-blue.svg``）。若直接用贪婪正则会把 ``-blue.svg`` 误并进 SemVer
    # 的 prerelease。做法：定位 ``version-<三段数字>`` 后，从全长开始回退，取
    # 最长的**合法 SemVer** 前缀作为版本号。
    text = C.read_text(p)
    m = re.search(r"version-(\d+\.\d+\.\d+)", text)
    if not m:
        report.warning("README.md 未找到 version badge（建议 version-<VERSION>）", "README.md")
        return ""
    base = m.group(1)
    # 取 ``version-<base>`` 之后到 badge 结束（右括号 / 空白）之间的后缀，
    # 再去掉 shields.io 的 ``-<color>.svg`` 颜色段。余下若构成合法 SemVer
    # prerelease/build 则并入版本号，否则只取三段核心版本。
    after = text[m.end(1):]
    suffix = re.match(r"[-+0-9A-Za-z.]*", after).group(0)
    suffix = re.sub(r"-[0-9A-Za-z]+\.svg$", "", suffix)   # -blue.svg / -brightgreen.svg
    suffix = re.sub(r"\.svg.*$", "", suffix)
    badge = base
    for cut in range(len(suffix), -1, -1):
        cand = base + suffix[:cut]
        if is_semver(cand):
            badge = cand
            break
    if spec and badge != spec:
        report.error(
            f"README version badge={badge!r} 与 VERSION={spec!r} 不一致（对外展示错误版本）",
            "README.md",
        )
    return badge


def _read_changelog(repo: Path, report: C.Report, spec: str) -> tuple[str, bool]:
    p = repo / "CHANGELOG.md"
    if not p.is_file():
        report.error("缺少 CHANGELOG.md（发布记录）", "CHANGELOG.md")
        return "", False
    text = C.read_text(p)
    released = re.findall(r"^##\s*\[(\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?)\]",
                          text, re.M)
    latest = released[0] if released else ""
    has_current = bool(spec) and re.search(
        rf"^##\s*\[{re.escape(spec)}\]", text, re.M) is not None
    # 当前 VERSION 若已在 CHANGELOG 登记为已发版段则通过；否则给 WARNING：
    # 开发中提升 VERSION 但尚未写发布段是常见中间态，发布门禁（--strict / release_check）
    # 会把它升级为阻断项，日常校验只提醒。
    if spec and not has_current:
        report.warning(
            f"CHANGELOG.md 未找到当前版本 [{spec}] 的已发版段（发布前必须补齐）",
            "CHANGELOG.md",
        )
    return latest, has_current


def validate_versions(repo: Path, report: C.Report) -> VersionInfo:
    """采集并校验全部版本来源的一致性，结果写入 report，返回快照。"""
    repo = Path(repo)
    spec = _read_version_file(repo, report)
    code, cfg = _read_installer_versions(repo, report)
    badge = _read_readme_badge(repo, report, spec)
    latest, has_cur = _read_changelog(repo, report, spec)
    info = VersionInfo(
        spec=spec,
        installer_code=code,
        installer_config=cfg,
        readme_badge=badge,
        changelog_latest=latest,
        changelog_has_current=has_cur,
        collector=cfg,
        manifest_schema=T.MANIFEST_SCHEMA,
    )
    if info.all_consistent:
        report.info(
            f"版本来源一致：规范 {spec} / 安装器 {code} / README badge {badge} / "
            f"CHANGELOG 含 [{spec}]",
            "版本一致性",
        )
    return info


def release_file_list(repo: Path) -> list[str]:
    """发布产物清单（**单一事实来源**）：返回应随 release 一起分发的相对路径。

    release_check 用它核对完整性，build_release_artifact 用它生成归档，二者不各写一份。
    包含：规范源（core/standards/skills/agents）、适配器、tooling（安装器/验证器/
    配置/模板/测试/脚本）、docs、examples、CI、根级必要文件。排除缓存、镜像
    （.ai-standards/）、打包输出（dist/）与 VCS。
    """
    repo = Path(repo)
    out: set[str] = set()
    for p in repo.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(repo).as_posix()
        parts = p.relative_to(repo).parts
        if any(seg in parts for seg in ("__pycache__", ".git", "dist", ".ai-standards",
                                         ".ai-engineering-standards")) or ".ai-standards" in rel:
            continue
        if p.suffix in (".pyc",):
            continue
        out.add(rel)
    return sorted(out)


def check_release_files(repo: Path) -> list[str]:
    """返回缺失的**必需**发布文件（相对路径）。用 REQUIRED_FILES/DIRS + Skills + 适配器源。"""
    missing: list[str] = []
    for rel in C.REQUIRED_FILES:
        if not (repo / rel).is_file():
            missing.append(rel)
    for d in C.REQUIRED_DIRS:
        if not (repo / d).is_dir():
            missing.append(d + "/")
    for name in T.SKILL_NAMES:
        f = f"skills/{name}/SKILL.md"
        if not (repo / f).is_file():
            missing.append(f)
    for spec in T.TOOL_SPECS.values():
        for a in spec.all_adapters:
            if not (repo / a.source).is_file():
                missing.append(a.source)
    return missing


def render_version_summary(info: VersionInfo) -> str:
    """供 CLI 打印人类可读的版本关系。"""
    return (
        f"规范版本 VERSION={info.spec or '未知'}；"
        f"安装器 targets.INSTALLER_VERSION={info.installer_code or '未知'} "
        f"/ config.installer_version={info.installer_config or '未知'}；"
        f"README badge={info.readme_badge or '缺失'}；"
        f"CHANGELOG 最新已发版段={info.changelog_latest or '无'}"
    )
