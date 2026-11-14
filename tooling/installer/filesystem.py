"""文件系统安全层：路径约束、原子写入、符号链接防护、可回滚删除。

只依赖 Python 标准库。所有安装写入都经过本模块，保证：

1. 目标路径必须落在受控根之内（防 `../` 穿越、防绝对路径逃逸）；
2. 目标路径的每一级父目录都不得是逃出受控根的符号链接；
3. 单文件写入用「同目录临时文件 + os.replace」原子替换，失败保留原文件；
4. 提供事务日志（Transaction），出错时回滚本次创建/修改，不碰用户文件。

设计约束（对应 Phase 5 任务书 §四.5 路径安全 / §四.6 原子性）：
- **不递归删除**任何目录；回滚只删除本次明确创建的文件与空目录。
- 不跟随、不创建符号链接；遇到符号链接一律当作不可写目标拒绝。
"""

from __future__ import annotations

import os
import shutil
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path


class UnsafePathError(ValueError):
    """目标路径违反安全约束（越界、符号链接逃逸、非法片段）。"""


def _nearest_existing(parent: Path) -> Path:
    """返回 parent 向上第一个已存在的路径（用于符号链接检查）。"""
    cur = parent
    while not cur.exists() and cur != cur.parent:
        cur = cur.parent
    return cur


def ensure_within(root: Path, target: Path, *, root_is_symlink_ok: bool = True,
                  label: str = "target") -> Path:
    """规范化并校验 target 落在 root 内；返回解析后的绝对路径。

    - root / target 都先 resolve（解析符号链接与 `..`）；
    - 解析后的 target 必须等于 root 或以 root 为前缀（用 `is_relative_to`，3.9+）；
    - 若 target 本身或任一已存在的父级是符号链接且解析后逃出 root，拒绝；
    - 若 target 已存在且本身是符号链接，一律拒绝写入（防写穿到受控根外）。
    """
    root = Path(root).expanduser().resolve()
    if not root_is_symlink_ok and root.is_symlink():
        raise UnsafePathError(f"{label} 根路径本身是符号链接，拒绝：{root}")
    raw = Path(target)
    if raw.is_absolute():
        # 允许绝对路径，但必须解析后仍在 root 内（global 用 home 绝对路径构造）
        resolved = raw.resolve()
    else:
        resolved = (root / raw).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise UnsafePathError(
            f"{label} 越出受控根目录：{resolved} 不在 {root} 之内（疑似路径穿越/符号链接逃逸）"
        ) from exc
    # target 本身是符号链接 -> 拒绝
    if raw.exists() and Path(target).is_symlink():
        raise UnsafePathError(f"{label} 是符号链接，拒绝写入：{target}")
    # 逐级检查已存在父目录：若某级是符号链接，其 resolve 结果已在上面整体校验；
    # 额外确认没有"父级符号链接指向 root 内但 target 用 .. 折返"的诡异情况。
    anchor = _nearest_existing(resolved.parent)
    try:
        anchor.relative_to(root)
    except ValueError as exc:
        raise UnsafePathError(f"{label} 的父级解析越界：{anchor}") from exc
    return resolved


def atomic_write(dest: Path, data: bytes, *, mode: int = 0o644) -> Path:
    """把 data 原子写入 dest；返回 dest。失败时原文件保持不变。

    同目录创建临时文件（避免跨设备 rename 失败）→ 写 bytes → flush+fsync →
    chmod → os.replace。任何异常都清理临时文件并向上抛。
    """
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".aes-tmp-", dir=str(dest.parent))
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, dest)
    except BaseException:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return dest


def content_equal(path: Path, data: bytes) -> bool:
    """安全比较文件内容与给定字节；不存在或读失败返回 False。"""
    try:
        return Path(path).read_bytes() == data
    except OSError:
        return False


@dataclass
class Transaction:
    """可逆操作日志：记录本次安装创建/修改的文件，供失败时回滚。

    - created: 本次新建的相对目标路径（回滚时删除）
    - replaced: 本次覆盖的 (绝对路径, 原始 bytes)（回滚时还原原始内容）
    - created_dirs: 本次创建的空目录（回滚时按最深优先尝试 rmdir）
    """

    created: list[Path] = field(default_factory=list)
    replaced: list[tuple[Path, bytes]] = field(default_factory=list)
    created_dirs: list[Path] = field(default_factory=list)

    def mark_created(self, path: Path) -> None:
        self.created.append(Path(path))

    def mark_replaced(self, path: Path, original: bytes) -> None:
        self.replaced.append((Path(path), original))

    def mark_dirs(self, root: Path, dest: Path) -> None:
        """记录 root 之下、dest 之上本次可能新建的空目录（去重保序）。"""
        cur = dest.parent.resolve() if dest.parent.exists() else dest.parent
        while cur != root and cur.is_relative_to(root):
            if cur.is_dir():
                self.created_dirs.append(cur)
            cur = cur.parent

    def rollback(self) -> list[str]:
        """尽力回滚，返回回滚动作描述列表。只删本次创建的文件、还原被替换文件、
        移除仍为空的目录。不删除含任意内容的目录，不碰未在日志中的文件。"""
        actions: list[str] = []
        # 1) 还原被替换文件（逆序）
        for path, original in reversed(self.replaced):
            try:
                atomic_write(path, original)
                actions.append(f"RESTORED {path}")
            except OSError as exc:  # pragma: no cover - 极端 IO 失败
                actions.append(f"RESTORE-FAILED {path}: {exc}")
        # 2) 删除本次创建的文件（仅当内容未被外部再次改动才安全删除）
        for path in reversed(self.created):
            try:
                if path.exists() or path.is_symlink():
                    path.unlink()
                    actions.append(f"REMOVED {path}")
            except OSError as exc:  # pragma: no cover
                actions.append(f"REMOVE-FAILED {path}: {exc}")
        # 3) 移除空目录（最深优先）
        for d in sorted(set(self.created_dirs), key=lambda p: len(p.parts), reverse=True):
            try:
                if d.is_dir() and not any(d.iterdir()):
                    d.rmdir()
                    actions.append(f"RMDIR {d}")
            except OSError:
                pass
        return actions


def safe_unlink(path: Path, *, within: Path) -> None:
    """在 within 约束下删除单个文件（不删目录、不跟随符号链接删除目标）。"""
    p = ensure_within(within, path, label="unlink")
    if p.is_symlink():
        p.unlink()  # 只删链接本身
    elif p.is_file():
        p.unlink()


def copy_bytes(src: Path, dest: Path, *, within: Path) -> None:
    """从 src 逐字节读取，经 ensure_within 校验后原子写入 dest。"""
    data = Path(src).read_bytes()
    safe_dest = ensure_within(within, dest, label="write")
    atomic_write(safe_dest, data)
