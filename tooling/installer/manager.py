"""安装编排：计划 -> 决策 -> 事务写入 -> 完整性校验 -> manifest -> 摘要。

保持 Phase 1-4 的 CLI 输出契约（install.py 与既有测试依赖）：
- `[INFO]` / `[WARNING]` / `[ERROR]` 前缀；
- 逐行 `[PLAN  ] / [UPDATE] / [KEEP  ] / [SKIP  ] dest <- source`；
- `DRY-RUN` 字样；"用户自有文件，永不覆盖"；unified diff（`--- installed/`）。
Phase 5 在其上增量新增：规范化 Installation summary、用户修改检测、备份、回滚。
"""

from __future__ import annotations

import difflib
import hashlib
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from validators import common as C
from validators import targets as T

from . import backup as B
from . import filesystem as FS
from . import manifest as M
from . import resolver as R


# 动作
A_CREATE = "CREATE"
A_UPDATE = "UPDATE"
A_UP_TO_DATE = "UP_TO_DATE"
A_SKIP_USER = "SKIP_USER"          # 用户自有文件：永不覆盖
A_SKIP_MODIFIED = "SKIP_MODIFIED"  # 托管文件被用户改过 / 源变化未授权：默认跳过
A_SKIP_ORPHAN = "SKIP_ORPHAN"      # 有标记但清单未登记：视为用户资产
A_CONFLICT_FORCE = "CONFLICT_FORCE"  # --force 覆盖"用户改过的托管文件"（先备份）

# 摘要分类
S_CREATED, S_UPDATED, S_SKIPPED, S_CONFLICT, S_UNCHANGED = (
    "Created", "Updated", "Skipped", "Conflict", "Unchanged",
)


@dataclass
class Decision:
    planned: R.PlannedFile
    action: str
    classification: str
    dest_abs: Path
    note: str = ""


def _sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_sha(path: Path) -> str:
    return _sha_bytes(Path(path).read_bytes())


# 归属分类 -> 决策动作（不可覆盖类集中在一处，便于审计）
_NON_OWNED = {M.CLASS_USER: A_SKIP_USER, M.CLASS_ORPHAN: A_SKIP_ORPHAN}


def decide(plan: list[R.PlannedFile], root: Path, version: str, force: bool,
           prev: dict, report: C.Report) -> list[Decision]:
    """对计划逐项分类决策。归属检测完全交给 manifest.classify。"""
    files = M.entries_from(prev)
    out: list[Decision] = []
    for pf in plan:
        dest = Path(root) / Path(*pf.dest_rel.split("/"))
        cls = M.classify(pf.dest_rel, dest, files, _read_sha)
        if cls == M.CLASS_ABSENT:
            out.append(Decision(pf, A_CREATE, cls, dest))
            continue
        if cls in _NON_OWNED:
            note = ("用户自有文件，永不覆盖；见 docs/installation.md §8"
                    if cls == M.CLASS_USER else
                    "带归属标记但清单未登记（清单损坏/迁移残留），视为用户资产，永不覆盖")
            out.append(Decision(pf, _NON_OWNED[cls], cls, dest, note))
            continue
        try:
            expected = pf.expected_bytes(version)
        except OSError as exc:
            report.error(f"无法读取源文件 {pf.source_rel}: {exc}", pf.dest_rel)
            out.append(Decision(pf, A_SKIP_USER, M.CLASS_USER, dest, str(exc)))
            continue

        current = dest.read_bytes() if dest.is_file() else None
        if current == expected:
            out.append(Decision(pf, A_UP_TO_DATE, cls, dest))
        elif cls == M.CLASS_MODIFIED:
            # 用户改过的托管文件：默认跳过；--force 才覆盖且先备份
            if force:
                out.append(Decision(pf, A_CONFLICT_FORCE, cls, dest,
                                    "用户改过的托管文件；--force 覆盖前已备份"))
            else:
                out.append(Decision(pf, A_SKIP_MODIFIED, cls, dest,
                                    "用户改过的托管文件；默认跳过，--force 覆盖前会备份"))
        else:  # MANAGED 但内容≠期望（规范升级）
            if force:
                out.append(Decision(pf, A_UPDATE, cls, dest))
            else:
                out.append(Decision(pf, A_SKIP_MODIFIED, cls, dest,
                                    "规范内容已变化；使用 --force 更新（会先打印差异并备份）"))
    return out


_PREFIX = {
    A_CREATE: "PLAN  ", A_UPDATE: "UPDATE", A_UP_TO_DATE: "KEEP  ",
    A_SKIP_USER: "SKIP  ", A_SKIP_MODIFIED: "SKIP  ", A_SKIP_ORPHAN: "SKIP  ",
    A_CONFLICT_FORCE: "UPDATE",
}


def print_plan(decisions: list[Decision], version: str, root: Path, tools: list[str],
               scope: str, dry_run: bool, old_version: str | None) -> None:
    print(f"[INFO]    ai-engineering-standards {version} -> {root}")
    print(f"[INFO]    scope: {scope} | tools: {', '.join(tools)} | files in plan: {len(decisions)}")
    if old_version and old_version != version:
        print(f"[INFO]    installed version: {old_version} -> {version} (update)")
    elif old_version:
        print(f"[INFO]    installed version: {old_version} (up to date)")
    if dry_run:
        print("[INFO]    DRY-RUN: 不创建目录、不写文件、不写清单")
    for d in decisions:
        pf = d.planned
        print(f"[{_PREFIX[d.action]}] {pf.dest_rel}  <- {pf.source_rel}")
        if d.action in (A_SKIP_USER, A_SKIP_MODIFIED, A_SKIP_ORPHAN):
            # "内容不同" 是 Phase 1-4 的 CLI 输出契约（test_12 断言），保留该字样
            extra = "（内容不同）" if d.action == A_SKIP_MODIFIED else ""
            print(f"[WARNING] 跳过 {pf.dest_rel}（{d.classification}）{extra}；{d.note}")


def print_diff(dest: Path, new: bytes, dest_rel: str) -> None:
    try:
        old = dest.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return
    diff = list(difflib.unified_diff(
        old, new.decode("utf-8", errors="replace").splitlines(),
        fromfile=f"installed/{dest_rel}", tofile=f"source/{dest_rel}", lineterm="",
    ))
    for line in diff[:80]:
        print("  " + line)
    if len(diff) > 80:
        print(f"  ... 差异共 {len(diff)} 行，仅显示前 80 行")


def print_list(decisions: list[Decision], root: Path, scope: str) -> None:
    """--list：只列"将安装到哪里"，不写任何文件。"""
    print(f"[INFO]    LIST scope={scope} root={root}")
    for d in decisions:
        tag = {
            A_CREATE: "CREATE", A_UPDATE: "UPDATE(force)",
            A_CONFLICT_FORCE: "UPDATE(force,backup)", A_UP_TO_DATE: "UP_TO_DATE",
            A_SKIP_USER: "SKIP(user)", A_SKIP_MODIFIED: "SKIP(modified)",
            A_SKIP_ORPHAN: "SKIP(orphan)",
        }[d.action]
        print(f"[{tag:<22}] {d.dest_abs}")


def summary(decisions: list[Decision], written: list[str], backed_up: list[str],
            root: Path, scope: str, version: str) -> dict[str, int]:
    """规范化安装摘要（任务书 §六示例）；返回分类计数便于测试断言。"""
    counts = {S_CREATED: 0, S_UPDATED: 0, S_SKIPPED: 0, S_CONFLICT: 0, S_UNCHANGED: 0}
    for d in decisions:
        if d.action == A_CREATE:
            counts[S_CREATED] += 1
        elif d.action in (A_UPDATE, A_CONFLICT_FORCE):
            counts[S_UPDATED] += 1
        elif d.action == A_UP_TO_DATE:
            counts[S_UNCHANGED] += 1
        else:
            counts[S_SKIPPED] += 1
            if d.action in (A_SKIP_MODIFIED, A_SKIP_ORPHAN):
                counts[S_CONFLICT] += 1
    print("=== Installation summary ===")
    for k in (S_CREATED, S_UPDATED, S_SKIPPED, S_CONFLICT, S_UNCHANGED):
        print(f"  {k:<10}: {counts[k]}")
    print(f"  Written {len(written)} file(s); backed up {len(backed_up)} file(s).")
    if counts[S_SKIPPED]:
        print("  User files were preserved. Some files were skipped/conflicted — see WARNINGs above.")
        print("  Installation completed with warnings.")
    else:
        print(f"  Installation completed. Installed version {version} (scope={scope}).")
    return counts


def _manage_root(root: Path, scope: str) -> Path:
    rel = T.GLOBAL_MANAGE_ROOT if scope == T.SCOPE_GLOBAL else T.BUNDLE_ROOT
    return Path(root) / Path(*rel.split("/"))


def _abort(tx: FS.Transaction, report: C.Report, message: str) -> None:
    report.error(message)
    actions = tx.rollback()
    print("[ERROR]    事务失败，已回滚本次创建/修改：", file=sys.stderr)
    for a in actions:
        print("  " + a, file=sys.stderr)
    print(report.render(), file=sys.stderr)


def install(repo: Path, root: Path, tools: list[str], scope: str, version: str,
            *, dry_run: bool = False, force: bool = False, list_only: bool = False
            ) -> tuple[int, dict]:
    """执行安装。返回 (退出码, 结果字典)。所有写操作经 filesystem 安全层。"""
    report = C.Report("Installation")
    try:
        plan = R.build_plan(repo, tools, scope)
    except (OSError, ValueError) as exc:
        report.error(f"生成安装计划失败: {exc}")
        print(report.render(), file=sys.stderr)
        return 1, {"error": str(exc), "plan_failed": True}

    prev = M.load(root, scope)
    old_version = (prev or {}).get("version")
    decisions = decide(plan, root, version, force, prev, report)

    if dry_run:
        print_plan(decisions, version, root, tools, scope, True, old_version)
        summary(decisions, [], [], root, scope, version)
        return report.exit_code, {"decisions": decisions, "dry_run": True}
    if list_only:
        print_list(decisions, root, scope)
        return report.exit_code, {"decisions": decisions, "list_only": True}

    print_plan(decisions, version, root, tools, scope, False, old_version)

    # 事务：root 为受控根，所有写入路径必须落在其内
    tx = FS.Transaction()
    written: list[str] = []
    backed_up: list[str] = []
    backups_index: dict[str, dict] = dict((prev or {}).get("backups", {}) or {})
    run_id = B.new_run_id()
    to_write = [d for d in decisions if d.action in
                (A_CREATE, A_UPDATE, A_CONFLICT_FORCE)]

    for d in to_write:
        pf = d.planned
        try:
            expected = pf.expected_bytes(version)
            safe_dest = FS.ensure_within(root, d.dest_abs, label="write")
        except (FS.UnsafePathError, OSError, ValueError) as exc:
            _abort(tx, report, f"路径安全校验失败 {pf.dest_rel}: {exc}")
            return 1, {"decisions": decisions, "error": str(exc), "rolled_back": True}
        try:
            if safe_dest.exists():
                tx.mark_replaced(safe_dest, safe_dest.read_bytes())
            else:
                tx.mark_created(safe_dest)
            tx.mark_dirs(root, safe_dest)
            if d.action == A_CONFLICT_FORCE:
                entry = B.backup_file(_manage_root(root, scope), safe_dest,
                                      pf.dest_rel, run_id, within=root)
                backups_index[pf.dest_rel] = entry
                backed_up.append(pf.dest_rel)
                print(f"[INFO]    BACKUP {pf.dest_rel} -> {entry['backup']}")
            if d.action in (A_UPDATE, A_CONFLICT_FORCE):
                print(f"[INFO]    UPDATE {pf.dest_rel}（覆盖前差异如下）")
                print_diff(safe_dest, expected, pf.dest_rel)
            FS.atomic_write(safe_dest, expected)
            written.append(pf.dest_rel)
        except (OSError, FS.UnsafePathError) as exc:
            _abort(tx, report, f"写入失败 {pf.dest_rel}: {exc}")
            return 1, {"decisions": decisions, "error": str(exc), "rolled_back": True}

    # 完整性校验：逐文件比对期望内容哈希
    for d in to_write:
        try:
            safe_dest = FS.ensure_within(root, d.dest_abs, label="verify")
            actual = _read_sha(safe_dest)
        except (OSError, FS.UnsafePathError) as exc:
            _abort(tx, report, f"完整性校验无法读取 {d.planned.dest_rel}: {exc}")
            return 1, {"decisions": decisions, "error": str(exc), "rolled_back": True}
        if actual != _sha_bytes(d.planned.expected_bytes(version)):
            _abort(tx, report,
                   f"完整性校验失败：{d.planned.dest_rel} 与源内容哈希不一致")
            return 1, {"decisions": decisions, "error": "integrity", "rolled_back": True}

    # 构建 manifest.files：登记"安装器期望的哈希"，而非磁盘实际哈希。
    # 这样被用户改过的文件（SKIP_MODIFIED）下次仍能被 classify 判定为 MODIFIED。
    files: dict[str, M.FileEntry] = {}
    current_dests: set[str] = set()
    for d in decisions:
        pf = d.planned
        if d.action == A_SKIP_USER:
            continue                              # 用户文件不登记、不拥有
        current_dests.add(pf.dest_rel)
        try:
            expected_sha = _sha_bytes(pf.expected_bytes(version))
        except OSError:
            continue
        files[pf.dest_rel] = M.FileEntry(
            dest=pf.dest_rel, sha256=expected_sha, source=pf.source_rel,
            category=pf.category, generated=pf.generated,
        )
    # 保留此前安装、但本次未覆盖的托管文件（增量安装不丢历史工具）
    files = M.merge_previous_files(
        files, M.entries_from(prev), current_dests, repo,
        on_missing_source=lambda dest, src: report.warning(
            f"源文件 {src!r} 已不在仓库中，不自动删除 {dest}；见 docs/installation.md 手工清理",
            dest),
    )

    # manifest 最后写；写入失败则回滚本次所有文件操作，绝不留下虚假成功记录
    merged_tools = sorted(set((prev or {}).get("tools", [])) | set(tools))
    mf = M.Manifest(
        schema=T.MANIFEST_SCHEMA, generator="ai-engineering-standards/install.py",
        version=version, installer_version=T.INSTALLER_VERSION, scope=scope,
        installed_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        tools=merged_tools, files=files, backups=backups_index,
    )
    try:
        M.save(root, scope, mf)
    except OSError as exc:
        _abort(tx, report, f"写入 manifest 失败: {exc}")
        return 1, {"decisions": decisions, "error": str(exc), "rolled_back": True}

    counts = summary(decisions, written, backed_up, root, scope, version)
    print(report.render())
    return report.exit_code, {
        "decisions": decisions, "written": written, "backed_up": backed_up,
        "counts": counts, "manifest": str(M.manifest_path(root, scope)),
    }
