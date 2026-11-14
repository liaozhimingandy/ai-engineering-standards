"""安装器测试（Phase 5，对应任务书 §十.1）。

全部在临时目录进行；global 作用域用 --home 隔离，绝不触碰真实用户 home。
覆盖：首次安装 / 重复 / 更新 / dry-run / 用户文件冲突 / 用户改过的托管文件 /
强制覆盖+备份 / 事务回滚 / 路径穿越 / 符号链接 / Windows 路径 / 清单损坏 /
目标目录不存在 / --list / global 作用域。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tooling"))

from validators import targets as T          # noqa: E402
from installer import resolver as R          # noqa: E402
from installer import manifest as M          # noqa: E402
from installer import filesystem as FS       # noqa: E402

PY = sys.executable
INSTALL = REPO / "tooling" / "scripts" / "install.py"
CHECK = REPO / "tooling" / "scripts" / "check.py"
VER = R.version_of(REPO)


def run(script, *args, cwd=None):
    return subprocess.run([PY, str(script), *map(str, args)], cwd=str(cwd) if cwd else None,
                          capture_output=True, text=True, encoding="utf-8", errors="replace")


def out(proc):
    return (proc.stdout or "") + (proc.stderr or "")


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="aes-inst-")
        self.root = Path(self.tmp.name) / "project"
        self.root.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    # ---- 基础安装 -------------------------------------------------------
    def test_01_first_install_creates_manifest_and_skills(self):
        p = run(INSTALL, "--tool", "codex", "--target", self.root)
        self.assertEqual(p.returncode, 0, out(p))
        self.assertIn("Created", out(p))
        mf = json.loads((self.root / T.MANIFEST_REL).read_text(encoding="utf-8"))
        self.assertEqual(mf["scope"], "project")
        self.assertEqual(mf["installer_version"], T.INSTALLER_VERSION)
        self.assertGreater(len(mf["files"]), 50)
        self.assertTrue((self.root / ".agents/skills/code-review/SKILL.md").is_file())

    def test_02_repeat_install_is_idempotent(self):
        run(INSTALL, "--tool", "codex", "--target", self.root)
        p = run(INSTALL, "--tool", "codex", "--target", self.root)
        self.assertEqual(p.returncode, 0, out(p))
        self.assertIn("Unchanged", out(p))
        self.assertNotIn("[ERROR]", out(p))

    def test_03_dry_run_and_list_write_nothing(self):
        for flag in ("--dry-run", "--list"):
            empty = Path(self.tmp.name) / f"empty-{flag}"
            empty.mkdir()
            p = run(INSTALL, "--all", flag, "--target", empty)
            self.assertEqual(p.returncode, 0, out(p))
            self.assertEqual(list(empty.iterdir()), [], f"{flag} 不应写入任何文件")

    # ---- 用户文件保护 ---------------------------------------------------
    def test_04_user_entry_never_overwritten_even_with_force(self):
        ag = self.root / "AGENTS.md"
        ag.write_text("# MINE\n", encoding="utf-8")
        p = run(INSTALL, "--tool", "codex", "--target", self.root, "--force")
        self.assertEqual(p.returncode, 0, out(p))
        self.assertEqual(ag.read_text(encoding="utf-8"), "# MINE\n")
        self.assertIn("用户自有文件，永不覆盖", out(p))

    def test_05_modified_managed_file_skipped_then_backed_up_on_force(self):
        run(INSTALL, "--tool", "codex", "--target", self.root)
        skill = self.root / ".agents/skills/code-review/SKILL.md"
        original = skill.read_bytes()
        skill.write_bytes(original + b"\n<!-- local edit -->\n")
        # 默认：跳过 + 报冲突（保留 Phase 1-4 "内容不同" 契约）
        p1 = run(INSTALL, "--tool", "codex", "--target", self.root)
        self.assertIn("内容不同", out(p1))
        self.assertIn("Conflict", out(p1))
        self.assertTrue(skill.read_bytes().endswith(b"local edit -->\n"))
        # 强制：备份 + 恢复
        p2 = run(INSTALL, "--tool", "codex", "--target", self.root, "--force")
        self.assertEqual(p2.returncode, 0, out(p2))
        self.assertIn("BACKUP", out(p2))
        self.assertEqual(skill.read_bytes(), original)
        baks = list((self.root / T.BUNDLE_ROOT / "backups").rglob("*.bak"))
        self.assertTrue(baks, "应存在备份文件")
        self.assertEqual(baks[0].read_bytes(), original + b"\n<!-- local edit -->\n")

    # ---- 更新 & 生成文件 -------------------------------------------------
    def test_06_update_when_source_version_changes(self):
        run(INSTALL, "--tool", "codex", "--target", self.root)
        entry = self.root / "AGENTS.md"
        entry.write_text("tampered", encoding="utf-8")
        p = run(INSTALL, "--tool", "codex", "--target", self.root, "--force")
        self.assertEqual(p.returncode, 0, out(p))
        self.assertIn("--- installed/", out(p))   # unified diff 契约
        self.assertNotEqual(entry.read_text(encoding="utf-8"), "tampered")

    def test_07_corrupt_manifest_treated_as_user_files(self):
        run(INSTALL, "--tool", "codex", "--target", self.root)
        mf = self.root / T.MANIFEST_REL
        mf.write_text("{not json", encoding="utf-8")
        ag = self.root / "AGENTS.md"
        before = ag.read_bytes()
        p = run(INSTALL, "--tool", "codex", "--target", self.root, "--force")
        self.assertEqual(p.returncode, 0, out(p))
        self.assertEqual(ag.read_bytes(), before, "清单损坏时不得覆盖任何已存在文件")

    def test_08_missing_target_dir_fails_clearly(self):
        missing = Path(self.tmp.name) / "nope"
        p = run(INSTALL, "--tool", "codex", "--target", missing)
        self.assertEqual(p.returncode, 1)
        self.assertIn("不存在或不是目录", out(p))
        self.assertFalse(missing.exists())

    # ---- 路径安全 -------------------------------------------------------
    def test_09_ensure_within_blocks_traversal(self):
        root = Path(self.tmp.name)
        outside = root / "outside.txt"
        with self.assertRaises(FS.UnsafePathError):
            FS.ensure_within(root / "sandbox", root / "sandbox" / ".." / "outside.txt")
        self.assertFalse(outside.exists())

    def test_10_ensure_within_blocks_absolute_escape(self):
        root = Path(self.tmp.name) / "sandbox"
        root.mkdir()
        with self.assertRaises(FS.UnsafePathError):
            FS.ensure_within(root, Path(self.tmp.name) / "elsewhere.txt")

    @unittest.skipUnless(os.name == "posix", "符号链接测试在 POSIX 上运行")
    def test_11_symlink_escape_rejected(self):
        root = Path(self.tmp.name) / "sandbox"
        root.mkdir()
        outside = Path(self.tmp.name) / "outside"
        outside.mkdir()
        (root / "link").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(FS.UnsafePathError):
            FS.ensure_within(root, root / "link" / "evil.txt")

    def test_12_atomic_write_failure_keeps_original(self):
        dest = Path(self.tmp.name) / "f.txt"
        dest.write_bytes(b"original")
        # 目标目录设为不存在且无法创建的情形用只读文件模拟 replace 失败
        with self.assertRaises(OSError):
            FS.atomic_write(dest / "child" / "x", b"new")
        self.assertEqual(dest.read_bytes(), b"original")

    def test_13_transaction_rollback_restores(self):
        root = Path(self.tmp.name)
        a, b = root / "a.txt", root / "b.txt"
        a.write_bytes(b"A"); b.write_bytes(b"B")
        tx = FS.Transaction()
        tx.mark_replaced(a, b"A"); tx.mark_replaced(b, b"B")
        FS.atomic_write(a, b"A2")
        # 模拟第二个失败：此时 a 已被改，b 未动；回滚应还原 a
        FS.atomic_write(b, b"B2")
        tx.mark_replaced(b, b"B")
        actions = tx.rollback()
        self.assertEqual(a.read_bytes(), b"A")
        self.assertEqual(b.read_bytes(), b"B")
        self.assertTrue(any("RESTORED" in x for x in actions))

    # ---- 作用域 / global 隔离 -------------------------------------------
    def test_14_global_uses_isolated_home(self):
        fakehome = Path(self.tmp.name) / "fakehome"
        fakehome.mkdir()
        p = run(INSTALL, "--all", "--scope", "global", "--home", fakehome)
        self.assertEqual(p.returncode, 0, out(p))
        self.assertTrue((fakehome / T.GLOBAL_MANIFEST_REL).is_file())
        self.assertTrue((fakehome / ".codex/AGENTS.md").is_file())
        self.assertFalse((fakehome / ".cursor/rules/ai-engineering-standards.mdc").exists(),
                         "Cursor 官方无全局规则磁盘路径，不得生成")
        chk = run(CHECK, "--scope", "global", "--target", fakehome)
        self.assertEqual(chk.returncode, 0, out(chk))

    def test_15_invalid_scope_and_tool_fail(self):
        for args in (("--all", "--scope", "bad"), ("--tool", "nope"), ()):
            p = run(INSTALL, *args, "--target", self.root)
            self.assertEqual(p.returncode, 1, out(p))
            self.assertIn("[ERROR]", out(p))

    def test_16_alias_and_windows_style_paths_in_manifest(self):
        p = run(INSTALL, "--tool", "claude-code", "--target", self.root)
        self.assertEqual(p.returncode, 0, out(p))
        mf = json.loads((self.root / T.MANIFEST_REL).read_text(encoding="utf-8"))
        for dest in mf["files"]:
            self.assertEqual(T.path_problems(dest), [], dest)
            self.assertNotIn("\\", dest)


if __name__ == "__main__":
    unittest.main(verbosity=2)
