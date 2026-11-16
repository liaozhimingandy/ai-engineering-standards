"""发布归档测试（Phase 6，任务书 §六「发布产物」）。

验证 build_release_artifact：
  - 构建后归档含全部必需文件与 RELEASE-MANIFEST.json、版本正确、自校验通过；
  - --check 校验通过 / 版本不符失败 / 源缺必需文件拒绝打包；
  - 损坏归档报失败；release_file_list 与 check_release_files 单一来源一致。
全部在临时目录构建归档，不改真实仓库的 dist/。
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tooling"))
sys.path.insert(0, str(REPO / "tooling" / "scripts"))

import build_release_artifact as B    # noqa: E402
from validators import version_validator as VV  # noqa: E402


def _clone(t: Path) -> Path:
    t.mkdir(parents=True, exist_ok=True)
    for item in ("core", "standards", "skills", "agents", "adapters", "docs",
                 "examples", "tooling"):
        shutil.copytree(REPO / item, t / item,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".ai-standards"))
    # .github 是隐藏目录，需显式复制（发布产物包含两个 workflow）
    shutil.copytree(REPO / ".github", t / ".github")
    for f in ("VERSION", "README.md", "CHANGELOG.md", "LICENSE", "CONTRIBUTING.md"):
        shutil.copy2(REPO / f, t / f)
    return t


class ReleaseArtifactTests(unittest.TestCase):
    def test_01_build_and_verify(self):
        with tempfile.TemporaryDirectory() as td:
            t = _clone(Path(td))
            out = t / "out" / "a.zip"
            meta = B.build(t, out)
            self.assertTrue(out.is_file())
            self.assertEqual(meta["version"], (t / "VERSION").read_text(encoding="utf-8").strip())
            self.assertGreater(meta["file_count"], 100)
            v = B.check_artifact(t, out, expected_version=(t / "VERSION").read_text(encoding="utf-8").strip())
            self.assertTrue(v["ok"], v.get("errors"))

    def test_02_archive_contains_required_markers(self):
        with tempfile.TemporaryDirectory() as td:
            t = _clone(Path(td))
            out = t / "a.zip"
            B.build(t, out)
            with zipfile.ZipFile(out) as zf:
                names = set(zf.namelist())
            p = B.ARTIFACT_PREFIX + "/"
            for must in ("VERSION", "README.md", "LICENSE", "CHANGELOG.md",
                         "RELEASE-MANIFEST.json",
                         "tooling/scripts/install.py",
                         "tooling/scripts/validate.py",
                         "tooling/scripts/check.py",
                         "tooling/scripts/quality_gate.py",
                         "tooling/scripts/release_check.py",
                         ".github/workflows/ci.yml", ".github/workflows/release.yml",
                         "skills/code-review/SKILL.md",
                         "adapters/codex/AGENTS.md"):
                self.assertIn(p + must, names, must)

    def test_03_release_manifest_consistent(self):
        with tempfile.TemporaryDirectory() as td:
            t = _clone(Path(td))
            out = t / "a.zip"
            B.build(t, out)
            import json
            with zipfile.ZipFile(out) as zf:
                m = json.loads(zf.read(B.ARTIFACT_PREFIX + "/RELEASE-MANIFEST.json"))
            self.assertEqual(m["version"], (t / "VERSION").read_text(encoding="utf-8").strip())
            self.assertEqual(m["file_count"], len(m["files"]))
            self.assertIn("VERSION", m["files"])

    def test_04_check_version_mismatch_fails(self):
        with tempfile.TemporaryDirectory() as td:
            t = _clone(Path(td))
            out = t / "a.zip"
            B.build(t, out)
            v = B.check_artifact(t, out, expected_version="9.9.9")
            self.assertFalse(v["ok"])
            self.assertTrue(any("不一致" in e for e in v["errors"]))

    def test_05_missing_archive_fails(self):
        with tempfile.TemporaryDirectory() as td:
            v = B.check_artifact(Path(td), Path(td) / "nope.zip")
            self.assertFalse(v["ok"])

    def test_06_corrupt_archive_fails(self):
        with tempfile.TemporaryDirectory() as td:
            t = _clone(Path(td))
            fake = t / "bad.zip"
            fake.write_bytes(b"not a zip")
            v = B.check_artifact(t, fake)
            self.assertFalse(v["ok"])
            self.assertTrue(any("无法读取" in e for e in v["errors"]))

    def test_07_refuses_pack_when_required_file_missing(self):
        with tempfile.TemporaryDirectory() as td:
            t = _clone(Path(td))
            (t / "tooling/scripts/release_check.py").unlink()
            with self.assertRaises(ValueError) as ctx:
                B.build(t, t / "a.zip")
            self.assertIn("缺少必需", str(ctx.exception))

    def test_08_file_list_excludes_build_outputs(self):
        with tempfile.TemporaryDirectory() as td:
            t = _clone(Path(td))
            (t / "dist").mkdir()
            (t / "dist" / "old.zip").write_bytes(b"x")
            (t / "__pycache__").mkdir()
            (t / "__pycache__" / "x.pyc").write_bytes(b"x")
            files = VV.release_file_list(t)
            self.assertFalse(any(f.startswith("dist/") for f in files))
            self.assertFalse(any("__pycache__" in f for f in files))

    def test_09_release_file_list_matches_required(self):
        # 单一来源：源仓库的 release_file_list 覆盖 check_release_files 的每个必需文件
        files = set(VV.release_file_list(REPO))
        required = VV.check_release_files(REPO)
        self.assertEqual(required, [])  # 真实仓库无缺失
        for rel in ("VERSION", "README.md", "tooling/scripts/build_release_artifact.py"):
            self.assertIn(rel, files)


if __name__ == "__main__":
    unittest.main()
