"""安装清单（manifest）读写、归属判定与用户修改检测。

manifest 是**安装器的文件归属记录**，不是用户项目配置。设计遵循 Phase 5 任务书 §四.2/§四.3：

- 不写入绝对路径、不写敏感信息；只记录相对路径、哈希、来源标识。
- 每个条目记录 `sha256`（写入时的内容哈希）与 `source`（相对仓库的源路径），
  以及 `generated`（是否由安装器渲染生成）、`category`（bundle/skill/adapter/generated）。
- **用户修改检测**：安装后若磁盘文件哈希 != manifest 记录的 sha256，判定为"用户改过的
  托管文件"，默认跳过更新、报冲突；`--force` 才覆盖（且先备份）。
- 归属判定不能只看文件名：必须同时满足 manifest 登记 + 归属标记，缺一视为用户文件。
- schema 只增字段、不改语义（versioning.md §2），保持 1，向后兼容 Phase 1-4 安装。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from validators import targets as T

from . import filesystem as FS


# 归属分类
CLASS_ABSENT = "ABSENT"        # 目标不存在
CLASS_MANAGED = "MANAGED"      # 本工具管理且未被改动
CLASS_MODIFIED = "MODIFIED"    # 本工具管理但用户改过（哈希不符）
CLASS_USER = "USER"            # 用户自有文件（无归属证据）
CLASS_ORPHAN = "ORPHAN"        # 有归属标记但清单未登记（清单损坏/迁移残留）

# 文件类别
CAT_BUNDLE = "bundle"
CAT_SKILL = "skill"
CAT_ADAPTER = "adapter"
CAT_GENERATED = "generated"


@dataclass(frozen=True)
class FileEntry:
    dest: str                  # 相对安装根，POSIX
    sha256: str                # 写入时内容哈希
    source: str                # 相对规范仓库的源路径（generated 文件也指向模板源）
    category: str              # bundle | skill | adapter | generated
    generated: bool = False    # 是否由安装器渲染（区别于逐字节复制）
    owner: str = "managed"

    def to_dict(self) -> dict:
        d = {
            "sha256": self.sha256,
            "source": self.source,
            "category": self.category,
            "owner": self.owner,
        }
        if self.generated:
            d["generated"] = True
        return d

    @classmethod
    def from_dict(cls, dest: str, d: dict) -> "FileEntry | None":
        if not isinstance(d, dict):
            return None
        sha, src = d.get("sha256"), d.get("source")
        if not isinstance(sha, str) or not isinstance(src, str):
            return None
        return cls(
            dest=dest, sha256=sha, source=src,
            category=d.get("category", CAT_ADAPTER),
            generated=bool(d.get("generated", False)),
            owner=d.get("owner", "managed"),
        )


@dataclass
class Manifest:
    schema: int
    generator: str
    version: str
    installer_version: str
    scope: str
    installed_at: str
    tools: list[str]
    files: dict[str, FileEntry]
    backups: dict[str, dict] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.backups is None:
            self.backups = {}

    def to_dict(self) -> dict:
        return {
            "schema": self.schema,
            "generator": self.generator,
            "version": self.version,
            "installer_version": self.installer_version,
            "scope": self.scope,
            "installed_at": self.installed_at,
            "tools": sorted(self.tools),
            "files": {k: self.files[k].to_dict() for k in sorted(self.files)},
            "backups": {k: self.backups[k] for k in sorted(self.backups)},
        }


def manifest_path(root: Path, scope: str) -> Path:
    rel = T.GLOBAL_MANIFEST_REL if scope == T.SCOPE_GLOBAL else T.MANIFEST_REL
    return Path(root) / Path(*rel.split("/"))


def load(root: Path, scope: str) -> dict | None:
    """读取 manifest；损坏或顶层非对象返回 None（调用方据此走安全默认）。"""
    p = manifest_path(root, scope)
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None


def save(root: Path, scope: str, manifest: Manifest) -> Path:
    """原子写入 manifest（最后一步），返回路径。"""
    p = manifest_path(root, scope)
    payload = (json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    FS.atomic_write(p, payload.encode("utf-8"))
    return p


def entries_from(raw: dict | None) -> dict[str, FileEntry]:
    out: dict[str, FileEntry] = {}
    for dest, d in (raw or {}).get("files", {}).items():
        if not isinstance(dest, str):
            continue
        e = FileEntry.from_dict(dest, d)
        if e is not None:
            out[dest] = e
    return out


def classify(dest_rel: str, dest_abs: Path, files: dict[str, FileEntry],
             read_sha) -> str:
    """判定一个已存在文件的归属类别（ classify 之"不得只看文件名"）。

    参数 read_sha: 一个可调用 `f(Path)->str|None` 读取文件哈希，便于测试注入。
    """
    p = Path(dest_abs)
    exists = p.exists() or p.is_symlink()
    if not exists:
        return CLASS_ABSENT
    entry = files.get(dest_rel)
    has_marker = _has_marker(p)
    if entry is None:
        # 没有清单登记：即使带标记也算孤儿（清单损坏/迁移残留），绝不当作可覆盖资产
        return CLASS_ORPHAN if has_marker else CLASS_USER
    # 清单登记即视为托管资产。Phase 1-4 的 Skills / 镜像文件是逐字节复制，
    # 本就不带归属标记（只有入口 adapter 带），因此标记不能作为归属的必要条件，
    # 否则重复安装会把 95 个技能/镜像文件误判成用户文件。
    # 用户是否改动由下面的 SHA-256 比对负责；标记仅作为孤儿识别的辅助证据。
    current = read_sha(p)
    if current is None:
        return CLASS_USER  # 读不出来，按用户文件保护
    return CLASS_MANAGED if current == entry.sha256 else CLASS_MODIFIED


def _has_marker(path: Path, head_bytes: int = 2048) -> bool:
    try:
        with Path(path).open("rb") as fh:
            return T.MARKER.encode("ascii") in fh.read(head_bytes)
    except OSError:
        return False


def merge_previous_files(current: dict[str, FileEntry], previous: dict[str, FileEntry],
                         current_dests: set[str], repo: Path,
                         on_missing_source) -> dict[str, FileEntry]:
    """保留此前安装、但本次计划未覆盖的托管文件（增量安装不丢历史工具）。

    源文件仍在 → 用源重算哈希（保持与仓库同步）；源文件已不在 → 保留旧条目并回调告警，
    安装器**不自动删除**该文件（versioning.md §5 / installation.md stale 规则）。
    """
    merged = dict(current)
    for dest, old in previous.items():
        if dest in current_dests:
            continue
        src = Path(repo) / old.source if not os.path.isabs(old.source) else None
        if src is not None and src.is_file():
            merged[dest] = FileEntry(
                dest=dest, sha256=_sha256_file(src), source=old.source,
                category=old.category, generated=old.generated, owner=old.owner,
            )
        else:
            merged[dest] = old
            on_missing_source(dest, old.source)
    return merged


def _sha256_file(path: Path) -> str:
    import hashlib
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()
