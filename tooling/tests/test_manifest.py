"""manifest / 归属检测测试（Phase 5，对应任务书 §十.1 安装记录）。

纯单元级（临时目录），快；不启动子进程。覆盖：
归属分类（ABSENT/MANAGED/MODIFIED/USER/ORPHAN）、用户修改检测、
schema 字段、生成文件标记、历史条目合并、清单无绝对路径、损坏清单加载。
"""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tooling"))

from validators import targets as T        # noqa: E402
from installer import manifest as M        # noqa: E402
from installer import resolver as R        # noqa: E402


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_sha(p: Path) -> str:
    return sha(Path(p).read_bytes())


class ManifestTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="aes-mf-")
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_01_classify_absent(self):
        self.assertEqual(
            M.classify("a.md", self.root / "a.md", {}, read_sha), M.CLASS_ABSENT)

    def test_02_classify_user_file_without_marker(self):
        p = self.root / "a.md"
        p.write_bytes(b"mine")
        self.assertEqual(M.classify("a.md", p, {}, read_sha), M.CLASS_USER)

    def test_03_classify_orphan_with_marker_but_unregistered(self):
        p = self.root / "a.md"
        p.write_bytes(f"<!-- {T.MARKER} -->\ncontent".encode())
        self.assertEqual(M.classify("a.md", p, {}, read_sha), M.CLASS_ORPHAN)

    def test_04_classify_managed_when_hash_matches(self):
        data = b"managed content"
        p = self.root / "a.md"
        p.write_bytes(data)
        files = {"a.md": M.FileEntry("a.md", sha(data), "core/a.md", "bundle")}
        self.assertEqual(M.classify("a.md", p, files, read_sha), M.CLASS_MANAGED)

    def test_05_classify_modified_detects_user_edit(self):
        original = b"original"
        p = self.root / "a.md"
        p.write_bytes(original + b" edited")
        files = {"a.md": M.FileEntry("a.md", sha(original), "core/a.md", "bundle")}
        self.assertEqual(M.classify("a.md", p, files, read_sha), M.CLASS_MODIFIED)

    def test_06_entry_to_dict_marks_generated(self):
        e = M.FileEntry("x", sha(b"x"), "adapters/../global/ENTRY.md", "generated", True)
        d = e.to_dict()
        self.assertIs(d["generated"], True)
        self.assertEqual(d["category"], "generated")
        e2 = M.FileEntry("y", sha(b"y"), "core/y.md", "bundle")
        self.assertNotIn("generated", e2.to_dict())

    def test_07_round_trip_save_load(self):
        mf = M.Manifest(
            schema=T.MANIFEST_SCHEMA, generator="test", version="1.0.0",
            installer_version=T.INSTALLER_VERSION, scope=T.SCOPE_GLOBAL,
            installed_at="2026-01-01T00:00:00+00:00", tools=["codex"],
            files={"a.md": M.FileEntry("a.md", sha(b"a"), "core/a.md", "bundle")},
        )
        p = M.save(self.root, T.SCOPE_GLOBAL, mf)
        self.assertTrue(p.is_file())
        raw = M.load(self.root, T.SCOPE_GLOBAL)
        self.assertEqual(raw["scope"], "global")
        self.assertEqual(raw["installer_version"], T.INSTALLER_VERSION)
        self.assertEqual(M.entries_from(raw)["a.md"].sha256, sha(b"a"))

    def test_08_manifest_has_no_absolute_paths(self):
        mf = M.Manifest(
            schema=1, generator="t", version="1.0.0", installer_version="1.0.0",
            scope="project", installed_at="now", tools=["codex"],
            files={"a.md": M.FileEntry("a.md", sha(b"a"), "core/a.md", "bundle")},
        )
        M.save(self.root, "project", mf)
        text = (self.root / T.MANIFEST_REL).read_text(encoding="utf-8")
        self.assertNotIn(str(self.root), text)
        self.assertNotIn("C:\\", text)
        self.assertNotIn("/Users/", text)

    def test_09_load_corrupt_returns_none(self):
        mf = self.root / T.MANIFEST_REL
        mf.parent.mkdir(parents=True, exist_ok=True)
        mf.write_text("{bad", encoding="utf-8")
        self.assertIsNone(M.load(self.root, "project"))

    def test_10_merge_previous_preserves_history(self):
        prev = {
            "old.md": M.FileEntry("old.md", sha(b"old"), "core/old.md", "bundle"),
            "kept.md": M.FileEntry("kept.md", sha(b"kept"), "core/kept.md", "bundle"),
        }
        current = {
            "kept.md": M.FileEntry("kept.md", sha(b"new kept"), "core/kept.md", "bundle"),
        }
        warned = []
        # current_dests 只含本次计划覆盖的路径；old.md 未被覆盖，应从前次清单保留
        merged = M.merge_previous_files(
            current, prev, {"kept.md"}, REPO,
            on_missing_source=lambda d, s: warned.append(d))
        self.assertIn("kept.md", merged)
        self.assertIn("old.md", merged)
        self.assertEqual(merged["kept.md"].sha256, sha(b"new kept"))
        self.assertIn("old.md", warned, "源文件已不在时应告警")

    def test_11_generated_entry_render_round_trip(self):
        src = f"<!-- {T.MARKER} {T.GENERATED_VERSION_TOKEN} -->\n# E".encode("utf-8")
        rendered = R.render_global_entry(src, "1.0.0")
        self.assertIn(b"version=1.0.0", rendered)
        self.assertNotIn(T.GENERATED_VERSION_TOKEN.encode(), rendered)

    def test_12_manifest_paths_are_posix(self):
        self.assertEqual(T.path_problems(T.MANIFEST_REL), [])
        self.assertEqual(T.path_problems(T.GLOBAL_MANIFEST_REL), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
