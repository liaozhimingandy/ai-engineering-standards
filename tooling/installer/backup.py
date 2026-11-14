"""覆盖前备份：--force 更新托管文件时保留原始内容。

对应 Phase 5 任务书 §四.4：强制更新前备份现有文件、保留原始内容、
记录备份时间与对应目标、输出被覆盖清单；不覆盖安装目录以外的文件。

备份布局（在管理目录内，不污染用户的规则目录）：
    <manage_root>/backups/<run_id>/<序号>__<原相对路径展平>.bak
备份索引写入 manifest.backups（记录 dest -> {backup_rel, sha256, at, scope}）。
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path

from validators import targets as T

from . import filesystem as FS


def backup_root(manage_root: Path) -> Path:
    return Path(manage_root) / "backups"


def _flatten(dest_rel: str) -> str:
    """把相对路径展平为安全文件名片段（去掉分隔符与非法字符）。"""
    return dest_rel.replace("/", "__").replace("\\", "__").lstrip(".")


def backup_file(manage_root: Path, dest_abs: Path, dest_rel: str,
                run_id: str, *, within: Path) -> dict:
    """备份单个已存在文件，返回索引条目。within 约束备份写入不越出安装根。"""
    original = Path(dest_abs).read_bytes()
    rel = f"backups/{run_id}/{_flatten(dest_rel)}.bak"
    dest = Path(manage_root) / rel
    # 备份路径也要过安全校验（防 dest_rel 含 .. 时逃逸）
    FS.ensure_within(within, dest, label="backup")
    FS.atomic_write(dest, original)
    return {
        "backup": rel,
        "sha256": hashlib.sha256(original).hexdigest(),
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "target": dest_rel,
        "run_id": run_id,
    }


def new_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
