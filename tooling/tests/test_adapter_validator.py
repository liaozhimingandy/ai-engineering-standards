"""适配器校验器测试（Phase 5，对应任务书 §十.2 / §十一）。

覆盖：四适配器存在与格式、Cursor .mdc frontmatter、Copilot applyTo、
纯 Markdown 适配器不得含工具字段、config 与代码双源漂移、公共规范工具字段泄漏。
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tooling"))

from validators import common as C            # noqa: E402
from validators import targets as T           # noqa: E402
from validators import adapter_validator as AV  # noqa: E402


class AdapterValidatorTests(unittest.TestCase):
    def test_01_real_repo_adapters_pass(self):
        r = C.Report("t")
        n = AV.validate_adapters(REPO, r)
        self.assertEqual(n, sum(len(s.all_adapters) for s in T.TOOL_SPECS.values()))
        self.assertEqual(r.count(C.ERROR), 0, r.render())

    def test_02_real_repo_config_code_consistent(self):
        r = C.Report("t")
        AV.validate_config_code_consistency(REPO, r)
        self.assertEqual(r.count(C.ERROR), 0, r.render())

    def test_03_config_drift_detected(self):
        with tempfile.TemporaryDirectory(prefix="aes-av-") as td:
            base = Path(td) / "repo"
            # 搭建最小骨架：只放会被 validate_config_code_consistency 读取的文件
            (base / "tooling/config").mkdir(parents=True)
            cfg = json.loads((REPO / "tooling/config/targets.json").read_text(encoding="utf-8"))
            cfg["installer_version"] = "9.9.9"   # 制造漂移
            (base / "tooling/config/targets.json").write_text(
                json.dumps(cfg), encoding="utf-8")
            r = C.Report("t")
            AV.validate_config_code_consistency(base, r)
            self.assertIn("installer_version", r.render())

    def test_04_cursor_mdc_requires_frontmatter(self):
        with tempfile.TemporaryDirectory(prefix="aes-av-") as td:
            p = Path(td) / "r.mdc"
            p.write_text("# no frontmatter, long enough text to pass size check" * 10,
                         encoding="utf-8")
            spec = T.AdapterSpec("x", ".cursor/rules/r.mdc", fmt="cursor-mdc")
            r = C.Report("t")
            C.check_adapter(p, spec, r)
            self.assertIn("--- 开头", r.render())

    def test_05_cursor_mdc_wrong_field(self):
        with tempfile.TemporaryDirectory(prefix="aes-av-") as td:
            p = Path(td) / "r.mdc"
            p.write_text("---\nalwaysApply: true\ndescription: d\napplyTo: '**'\n---\n"
                         "# " + "x" * 400, encoding="utf-8")
            spec = T.AdapterSpec("x", ".cursor/rules/r.mdc", fmt="cursor-mdc")
            r = C.Report("t")
            C.check_adapter(p, spec, r)
            self.assertIn("不支持的配置项", r.render())

    def test_06_copilot_path_requires_applyTo(self):
        with tempfile.TemporaryDirectory(prefix="aes-av-") as td:
            p = Path(td) / "r.instructions.md"
            p.write_text("---\ndescription: d\n---\n# " + "x" * 400, encoding="utf-8")
            spec = T.AdapterSpec("x", ".github/instructions/r.instructions.md",
                                 fmt="copilot-path")
            r = C.Report("t")
            C.check_adapter(p, spec, r)
            self.assertIn("applyTo", r.render())

    def test_07_plain_adapter_rejects_tool_fields(self):
        with tempfile.TemporaryDirectory(prefix="aes-av-") as td:
            p = Path(td) / "AGENTS.md"
            p.write_text(f"<!-- {T.MARKER} version=1.0.0 tool=codex -->\n"
                         "alwaysApply: true\n" + "# " + "x" * 400,
                         encoding="utf-8")
            spec = T.AdapterSpec("x", "AGENTS.md", guarded=True)
            r = C.Report("t")
            C.check_adapter(p, spec, r)
            self.assertIn("不应包含", r.render())

    def test_08_no_tool_leakage_in_real_repo(self):
        r = C.Report("t")
        AV.validate_no_tool_leakage(REPO, r)
        self.assertEqual(r.count(C.ERROR), 0, r.render())

    def test_09_tool_leakage_detected(self):
        with tempfile.TemporaryDirectory(prefix="aes-av-") as td:
            base = Path(td)
            (base / "core").mkdir()
            (base / "core/x.md").write_text("# X\n\nalwaysApply: true\n\n" + "y" * 400,
                                            encoding="utf-8")
            r = C.Report("t")
            AV.validate_no_tool_leakage(base, r)
            self.assertIn("工具专有字段", r.render())

    def test_10_missing_adapter_file(self):
        spec = T.AdapterSpec("ghost.md", "AGENTS.md", guarded=True)
        r = C.Report("t")
        C.check_adapter(Path("Z:/ghost.md"), spec, r)
        self.assertIn("不存在", r.render())


if __name__ == "__main__":
    unittest.main(verbosity=2)
