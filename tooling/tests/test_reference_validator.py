"""Markdown / 引用校验器测试（Phase 5，对应任务书 §十.2）。

覆盖：空规范文件 / 代码块未闭合 / 标题层级跳跃 / 图片资源不存在 /
不存在的本地引用 / 非可移植链接路径 / config 源文件引用有效性。
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
from validators import markdown_validator as MV  # noqa: E402
from validators import reference_validator as RV  # noqa: E402


class ReferenceValidatorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="aes-ref-")
        self.base = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_01_unclosed_fence_detected(self):
        r = C.Report("t")
        MV.check_fences("# X\n\n```python\nprint(1)\n", r, "x.md")
        self.assertIn("未闭合", r.render())

    def test_02_closed_fence_passes(self):
        r = C.Report("t")
        MV.check_fences("# X\n\n```python\nprint(1)\n```\n", r, "x.md")
        self.assertEqual(r.count(C.ERROR), 0, r.render())

    def test_03_tilde_fence_passes(self):
        r = C.Report("t")
        MV.check_fences("# X\n\n~~~yaml\na: b\n~~~\n", r, "x.md")
        self.assertEqual(r.count(C.ERROR), 0, r.render())

    def test_04_heading_jump_warning(self):
        r = C.Report("t")
        MV.check_heading_levels("## A\n\n#### D\n", r, "x.md")
        self.assertIn("标题层级从", r.render())
        self.assertEqual(r.count(C.WARNING), 1)

    def test_05_heading_inside_code_ignored(self):
        r = C.Report("t")
        MV.check_heading_levels("## A\n\n```\n#### not heading\n```\n\n### B\n", r, "x.md")
        self.assertEqual(r.count(C.WARNING), 0, r.render())

    def test_06_empty_markdown_is_error(self):
        p = self.base / "tiny.md"
        p.write_text("# X\n", encoding="utf-8")
        r = C.Report("t")
        C.check_markdown_content(p, r, "tiny.md")
        self.assertGreater(r.count(C.ERROR), 0)

    def test_07_missing_image_detected(self):
        md = self.base / "doc.md"
        md.write_text("# X\n\n![图](assets/ghost.png)\n\n正文内容足够长以通过字数检查。" * 8,
                      encoding="utf-8")
        r = C.Report("t")
        n = RV.check_images(self.base, r)
        self.assertGreaterEqual(n, 1)
        self.assertIn("图片资源不存在", r.render())

    def test_08_existing_image_ok(self):
        md = self.base / "doc.md"
        md.write_text("# X\n\n![图](assets/real.png)\n", encoding="utf-8")
        (self.base / "assets").mkdir()
        (self.base / "assets/real.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        r = C.Report("t")
        RV.check_images(self.base, r)
        self.assertEqual(r.count(C.ERROR), 0, r.render())

    def test_09_windows_absolute_link_rejected(self):
        md = self.base / "doc.md"
        md.write_text(r"# X" + "\n\n[a](C:\\Users\\x\\y.md)\n", encoding="utf-8")
        r = C.Report("t")
        RV.check_link_path_safety(self.base, r)
        self.assertIn("非可移植路径", r.render())

    def test_10_internal_link_missing(self):
        md = self.base / "doc.md"
        md.write_text("# X\n\n[见](ghost.md)\n", encoding="utf-8")
        r = C.Report("t")
        C.check_internal_links(self.base, r)
        self.assertIn("无效内部引用", r.render())

    def test_11_config_source_check_on_real_repo(self):
        r = C.Report("t")
        n = RV.check_config_sources(REPO, r)
        self.assertGreater(n, 0, "应检查到 config 声明的源文件")
        self.assertEqual(r.count(C.ERROR), 0, r.render())

    def test_12_config_source_missing_detected(self):
        # 复制仓库的最小 config 骨架，故意指向不存在的文件
        cfg = {"project": {"codex": {"skills_dir": ".agents/skills", "entries": [
            {"source": "ghost/nope.md", "dest": "AGENTS.md", "fmt": "plain", "guarded": True}]}},
               "global": {}, "global_integration": {"source": "ghost/gi.md", "dest": "x/y.md"}}
        (self.base / "tooling/config").mkdir(parents=True)
        (self.base / "tooling/config/targets.json").write_text(
            json.dumps(cfg), encoding="utf-8")
        r = C.Report("t")
        RV.check_config_sources(self.base, r)
        self.assertIn("不存在", r.render())


if __name__ == "__main__":
    unittest.main(verbosity=2)
