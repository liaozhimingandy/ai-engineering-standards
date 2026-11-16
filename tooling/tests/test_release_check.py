"""发布检查测试（Phase 6，任务书 §七）。

验证 release_check：
  - 真实仓库可执行项通过；
  - 非 git 仓库时 Git Tag 检查标 UNVERIFIABLE（不伪装通过、不计入失败）；
  - --version 与 VERSION 不一致 / 非法 SemVer 时判失败；
  - CHANGELOG 缺当前版本段 / 发布产物缺失 时判失败；
  - --run-tests 会真实调用 unittest（用 monkeypatch 断言，避免拖慢套件）；
  - json 输出结构、无法执行项如实上报。
全部在临时副本操作，不改真实仓库、不触碰真实 git。
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tooling"))
sys.path.insert(0, str(REPO / "tooling" / "scripts"))

import release_check as RC      # noqa: E402
import quality_gate as QG       # noqa: E402


def _status(rep: RC.ReleaseReport, needle: str) -> str:
    for it in rep.items:
        if needle in it.name:
            return it.status
    return ""


class ReleaseCheckTests(unittest.TestCase):
    def test_01_real_repo_passes(self):
        rep = RC.run_release_check(REPO)
        self.assertTrue(rep.passed, rep.render())
        self.assertEqual(rep.errors, 0)

    def test_02_non_git_tag_is_unverifiable_not_pass(self):
        rep = RC.run_release_check(REPO)
        st = _status(rep, "Git Tag")
        # 真实仓库当前非 git 仓库；即便在 git 环境也可能是 UNVERIFIABLE（无 tag）。
        self.assertIn(st, (RC.UNVERIFIABLE, "PASS"))
        # 关键：绝不能因无法核对就报 PASS 而 item 里没有任何说明
        item = next(i for i in rep.items if "Git Tag" in i.name)
        if st == RC.UNVERIFIABLE:
            self.assertTrue(item.detail)
            self.assertIn(item, rep.unverifiable)

    def test_03_version_mismatch_fails(self):
        rep = RC.run_release_check(REPO, version="2.0.0")
        self.assertFalse(rep.passed)
        self.assertEqual(_status(rep, "--version 2.0.0"), "FAIL")

    def test_04_invalid_requested_version_fails(self):
        rep = RC.run_release_check(REPO, version="v1.0")
        self.assertFalse(rep.passed)

    def test_05_changelog_missing_current_fails(self):
        with tempfile.TemporaryDirectory() as td:
            t = Path(td)
            for f in ("VERSION", "README.md", "CHANGELOG.md"):
                shutil.copy2(REPO / f, t / f)
            # 只做最小集的版本专项：直接调 _check_version 观察 CHANGELOG 项
            p = t / "CHANGELOG.md"
            cur = (t / "VERSION").read_text(encoding="utf-8").strip()
            p.write_text(p.read_text(encoding="utf-8").replace(f"## [{cur}]", "## [0.5.0]"),
                         encoding="utf-8")
            rep = RC.ReleaseReport(repo=str(t))
            RC._check_version(t, rep)
            self.assertEqual(_status(rep, "CHANGELOG 含当前版本"), "FAIL")

    def test_06_release_files_missing_detected(self):
        with tempfile.TemporaryDirectory() as td:
            t = Path(td)
            rep = RC.ReleaseReport(repo=str(t))
            RC._check_release_files(t, rep)  # 空目录 -> 大量缺失
            self.assertEqual(_status(rep, "发布产物文件完整"), "FAIL")

    def test_07_run_tests_invokes_unittest(self):
        calls: list[list[str]] = []

        class FakeCompleted:
            returncode = 0
            stdout = "Ran 999 tests\n\nOK\n"
            stderr = ""

        def fake_run(cmd, **kwargs):
            calls.append(list(cmd))
            return FakeCompleted()

        with mock.patch.object(RC.subprocess, "run", side_effect=fake_run):
            rep = RC.run_release_check(REPO, run_tests=True)
        self.assertEqual(_status(rep, "安装器与全量自动化测试"), "PASS")
        self.assertTrue(any("unittest" in " ".join(c) for c in calls),
                        "应真实调用 unittest discover")

    def test_08_without_run_tests_is_skipped(self):
        rep = RC.run_release_check(REPO, run_tests=False)
        self.assertEqual(_status(rep, "安装器与全量自动化测试"), "SKIPPED")

    def test_09_run_tests_failure_fails_release(self):
        class FakeCompleted:
            returncode = 1
            stdout = "FAILED (failures=1)\n"
            stderr = "Ran 1 test\nFAILED (failures=1)"

        with mock.patch.object(RC.subprocess, "run", return_value=FakeCompleted()):
            rep = RC.run_release_check(REPO, run_tests=True)
        self.assertEqual(_status(rep, "安装器与全量自动化测试"), "FAIL")
        self.assertFalse(rep.passed)

    def test_10_strict_flag_recorded(self):
        rep = RC.run_release_check(REPO, strict=True)
        self.assertTrue(rep.strict)
        self.assertTrue(rep.passed)

    def test_11_render_lists_unverifiable(self):
        rep = RC.run_release_check(REPO)
        out = rep.render()
        self.assertIn("无法执行", out)
        self.assertIn("须补验", out)

    def test_12_missing_repo_tool_error(self):
        with tempfile.TemporaryDirectory() as td:
            rep = RC.run_release_check(Path(td))
            self.assertTrue(rep.tool_error)
            self.assertFalse(rep.passed)

    def test_13_unverifiable_not_counted_as_pass(self):
        rep = RC.run_release_check(REPO)
        names = {i.name for i in rep.unverifiable}
        self.assertTrue(any("Git Tag" in n for n in names))


if __name__ == "__main__":
    unittest.main()
