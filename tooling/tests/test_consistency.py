"""一致性检查测试（Phase 5，对应任务书 §十.3）。

覆盖：core 六段结构（合法/缺章节/索引 README 排除）、语义冲突只标人工审查、
公共 Skills 源完整性、SKILL.md 副本不一致/过期检测、生成文件 token 新鲜度、
config 与代码无双源漂移。
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tooling"))

from validators import common as C            # noqa: E402
from validators import targets as T           # noqa: E402
from validators import consistency_validator as CV  # noqa: E402


class ConsistencyTests(unittest.TestCase):
    def test_01_real_repo_core_passes(self):
        r = C.Report("t")
        n = CV.validate_core(REPO, r)
        self.assertEqual(n, 7)
        self.assertEqual(r.count(C.ERROR), 0, r.render())

    def test_02_core_missing_section_detected(self):
        with tempfile.TemporaryDirectory(prefix="aes-cv-") as td:
            base = Path(td)
            (base / "core").mkdir()
            (base / "core/x.md").write_text(
                "# X\n\n## 文档目的\n\n内容\n\n## 适用范围\n\n内容\n" + "y" * 300,
                encoding="utf-8")
            r = C.Report("t")
            CV.validate_core(base, r)
            txt = r.render()
            for sec in ("核心规范", "禁止事项", "验收要求", "相关规范引用"):
                self.assertIn(sec, txt)
            self.assertGreater(r.count(C.ERROR), 0)

    def test_03_core_readme_index_excluded(self):
        with tempfile.TemporaryDirectory(prefix="aes-cv-") as td:
            base = Path(td)
            (base / "core").mkdir()
            (base / "core/README.md").write_text("# 索引\n\n## 1. A\n\n## 2. B\n",
                                                 encoding="utf-8")
            r = C.Report("t")
            n = CV.validate_core(base, r)
            self.assertEqual(n, 0, "README 索引不套六段，应被排除")
            self.assertEqual(r.count(C.ERROR), 0)

    def test_04_semantic_conflict_is_info_only(self):
        with tempfile.TemporaryDirectory(prefix="aes-cv-") as td:
            base = Path(td)
            (base / "core").mkdir()
            (base / "core/x.md").write_text("# X\n\n本文件最高优先级，优先于所有其他规则。\n",
                                            encoding="utf-8")
            r = C.Report("t")
            CV.scan_semantic_conflict_signals(base, r)
            self.assertEqual(r.count(C.ERROR), 0, "不得自动判定语义冲突")
            self.assertIn("需人工", r.render())

    def test_05_negation_not_flagged(self):
        with tempfile.TemporaryDirectory(prefix="aes-cv-") as td:
            base = Path(td)
            (base / "core").mkdir()
            (base / "core/x.md").write_text(
                "# X\n\n本文件不再各自声明绝对优先，以免产生冲突。\n", encoding="utf-8")
            r = C.Report("t")
            CV.scan_semantic_conflict_signals(base, r)
            self.assertNotIn("需人工", r.render())

    def test_06_generated_freshness_real_repo(self):
        r = C.Report("t")
        CV.check_generated_freshness(REPO, r)
        self.assertEqual(r.count(C.ERROR), 0, r.render())

    def test_07_generated_token_missing_detected(self):
        with tempfile.TemporaryDirectory(prefix="aes-cv-") as td:
            base = Path(td)
            src = REPO / T.GLOBAL_TOOL_SPECS["codex"].entry.source
            dest = base / T.GLOBAL_TOOL_SPECS["codex"].entry.source
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(src.read_text(encoding="utf-8").replace(
                T.GENERATED_VERSION_TOKEN, "version=1.0.0"), encoding="utf-8")
            r = C.Report("t")
            CV.check_generated_freshness(base, r)
            self.assertIn("生成文件源缺少 token", r.render())

    def test_08_public_skill_source_sanity(self):
        r = C.Report("t")
        CV.validate_public_skill_sources(REPO, r)
        self.assertEqual(r.count(C.ERROR), 0, r.render())

    def test_09_check_all_cli_returns_zero(self):
        import subprocess
        p = subprocess.run([sys.executable, str(REPO / "tooling/scripts/check.py"), "--all"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        self.assertEqual(p.returncode, 0, (p.stdout or "") + (p.stderr or ""))
        for cat in ("正常项目", "缺失文件", "过期文件", "不一致文件",
                    "无法验证的文件", "需要人工检查的项目"):
            self.assertIn(cat, p.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
