"""版本管理测试（Phase 6，任务书 §五）。

覆盖：VERSION 唯一来源、SemVer 合法性、README badge / CHANGELOG /
安装器 targets.py↔config 三处版本一致性，以及严格门禁规则单一来源。
所有文件操作在临时目录，绝不修改真实仓库。
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tooling"))

from validators import common as C            # noqa: E402
from validators import quality_rules as QR    # noqa: E402
from validators import version_validator as V  # noqa: E402


def _mini_repo(t: Path) -> Path:
    """复制版本校验所需的最小文件集到临时目录。"""
    t.mkdir(parents=True, exist_ok=True)
    for f in ("VERSION", "README.md", "CHANGELOG.md"):
        shutil.copy2(REPO / f, t / f)
    cfg = t / "tooling/config"
    cfg.mkdir(parents=True)
    shutil.copy2(REPO / "tooling/config/targets.json", cfg / "targets.json")
    return t


class VersionValidatorTests(unittest.TestCase):
    def test_01_real_repo_consistent(self):
        r = C.Report("t")
        info = V.validate_versions(REPO, r)
        self.assertEqual(r.count(C.ERROR), 0, r.render())
        self.assertTrue(info.all_consistent)
        self.assertEqual(info.spec, "1.0.0")

    def test_02_semver_valid(self):
        for v in ("1.0.0", "0.0.1", "2.10.3", "1.0.0-alpha", "1.0.0-rc.1+build.5"):
            self.assertTrue(V.is_semver(v), v)

    def test_03_semver_invalid(self):
        for v in ("v1.0.0", "1.0", "01.0.0", "1.0.0.0", "1.0.0-", "", "1.0.0-blue.svg"):
            self.assertFalse(V.is_semver(v), v)

    def test_04_badge_mismatch_detected(self):
        with tempfile.TemporaryDirectory() as td:
            t = _mini_repo(Path(td))
            p = t / "README.md"
            p.write_text(p.read_text(encoding="utf-8").replace("version-1.0.0", "version-9.9.9"),
                         encoding="utf-8")
            r = C.Report("t")
            V.validate_versions(t, r)
            self.assertTrue(any("badge" in i.message and "不一致" in i.message for i in r.issues),
                            r.render())

    def test_05_badge_shields_color_suffix_ok(self):
        # shields.io 颜色后缀不能被误当版本号（1.0.0-blue.svg -> 1.0.0）
        with tempfile.TemporaryDirectory() as td:
            t = _mini_repo(Path(td))
            p = t / "README.md"
            p.write_text(p.read_text(encoding="utf-8").replace("version-1.0.0-blue",
                                                                "version-1.0.0-brightgreen"),
                         encoding="utf-8")
            r = C.Report("t")
            info = V.validate_versions(t, r)
            self.assertEqual(info.readme_badge, "1.0.0")
            self.assertEqual(r.count(C.ERROR), 0, r.render())

    def test_06_installer_config_drift_detected(self):
        with tempfile.TemporaryDirectory() as td:
            t = _mini_repo(Path(td))
            p = t / "tooling/config/targets.json"
            p.write_text(p.read_text(encoding="utf-8").replace(
                '"installer_version": "1.0.0"', '"installer_version": "2.0.0"'),
                encoding="utf-8")
            r = C.Report("t")
            V.validate_versions(t, r)
            self.assertTrue(any("双源不一致" in i.message for i in r.issues), r.render())

    def test_07_changelog_missing_version_warns(self):
        with tempfile.TemporaryDirectory() as td:
            t = _mini_repo(Path(td))
            p = t / "CHANGELOG.md"
            p.write_text(p.read_text(encoding="utf-8").replace("## [1.0.0]", "## [0.9.0]"),
                         encoding="utf-8")
            r = C.Report("t")
            info = V.validate_versions(t, r)
            self.assertFalse(info.changelog_has_current)
            self.assertTrue(any("CHANGELOG" in i.message and "未找到当前版本" in i.message
                                for i in r.issues), r.render())

    def test_08_bad_version_format_error(self):
        with tempfile.TemporaryDirectory() as td:
            t = _mini_repo(Path(td))
            (t / "VERSION").write_text("v1.0\n", encoding="utf-8")
            r = C.Report("t")
            V.validate_versions(t, r)
            self.assertTrue(any("语义化版本号" in i.message for i in r.issues), r.render())

    def test_09_missing_version_error(self):
        with tempfile.TemporaryDirectory() as td:
            t = _mini_repo(Path(td))
            (t / "VERSION").unlink()
            r = C.Report("t")
            V.validate_versions(t, r)
            self.assertTrue(any("缺少 VERSION" in i.message for i in r.issues), r.render())

    def test_10_strict_promotion_single_source(self):
        # quality_rules 是唯一来源；apply_strict 只升级在白名单内的 WARNING
        r = C.Report("t")
        r.warning("xx description 过短 yy")
        r.warning("某个普通建议，不在白名单")
        n = QR.apply_strict(r)
        self.assertEqual(n, 1)
        levels = {i.message[:12]: i.level for i in r.issues}
        self.assertIn(C.ERROR, levels.values())
        self.assertEqual(r.count(C.ERROR), 1)
        self.assertEqual(r.count(C.WARNING), 1)

    def test_11_describe_rules_matches_promotions(self):
        self.assertEqual(len(QR.describe_rules()), len(QR.STRICT_PROMOTIONS))

    def test_12_version_info_dataclass_fields(self):
        info = V.VersionInfo(spec="1.0.0", readme_badge="1.0.0",
                             installer_code="1.0.0", installer_config="1.0.0",
                             changelog_has_current=True)
        self.assertTrue(info.all_consistent)
        info2 = V.VersionInfo(spec="1.0.0", readme_badge="2.0.0",
                              changelog_has_current=False)
        self.assertFalse(info2.all_consistent)

    def test_13_version_from_tag(self):
        self.assertEqual(V.version_from_tag("v1.0.0"), "1.0.0")
        self.assertEqual(V.version_from_tag("1.2.3"), "1.2.3")
        self.assertEqual(V.version_from_tag("V2.0.1"), "2.0.1")
        self.assertEqual(V.version_from_tag(" v0.0.1 "), "0.0.1")

    def test_14_version_from_tag_rejects_bad(self):
        for bad in ("v1.0", "v1.0.0.0", "release-1.0.0", "vv1.0.0", "", "v01.0.0"):
            self.assertIsNone(V.version_from_tag(bad), bad)


if __name__ == "__main__":
    unittest.main()
