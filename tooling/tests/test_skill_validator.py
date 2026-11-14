"""Skill 校验器测试（Phase 5，对应任务书 §十.2）。

纯单元级：构造合法/非法 Skill，验证 Agent Skills 规范字段检查。
覆盖：合法 Skill / 缺 Frontmatter / 缺 name / 缺 description / 非法名称 /
目录名不一致 / 非法引用 / 不存在的引用 / 工具专有字段泄漏。
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tooling"))

from validators import common as C          # noqa: E402
from validators import skill_validator as SV  # noqa: E402


HEADS = ("触发场景", "适用范围", "执行步骤", "输入要求",
         "输出要求", "质量检查", "完成标准", "异常情况处理")


def skill_text(name="demo", description="Use when 需要做 X 任务时触发此技能来完成具体工作。",
               extra="", body_sections=True, fm=True, path_refs=()):
    fm_block = ""
    if fm:
        lines = [f"name: {name}", f'description: "{description}"']
        if extra:
            lines.append(extra)
        fm_block = "---\n" + "\n".join(lines) + "\n---\n"
    body = "# Demo\n\n" + ("\n".join(f"## {h}\n\n内容。\n" for h in HEADS) if body_sections else "正文。\n")
    for r in path_refs:
        body += f"\n见 [{r}]({r})。\n"
    return fm_block + body


def make_skill(base: Path, name: str, text: str, *, with_refs=()) -> Path:
    d = base / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(text, encoding="utf-8")
    for r in with_refs:
        f = d / r
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("resource", encoding="utf-8")
    return d


def errors_for(d: Path) -> str:
    r = C.Report("t")
    C.check_skill(d, r)
    return r.render()


class SkillValidatorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="aes-skill-")
        self.base = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_01_valid_skill_passes(self):
        d = make_skill(self.base, "demo", skill_text("demo"),
                       with_refs=("references/edge-cases.md",))
        # SKILL.md 未引用该资源也不算 ERROR；只在引用时校验存在
        r = C.Report("t")
        self.assertTrue(C.check_skill(d, r))
        self.assertEqual(r.count(C.ERROR), 0, r.render())

    def test_02_missing_frontmatter(self):
        d = make_skill(self.base, "demo", skill_text(fm=False))
        self.assertIn("Frontmatter", errors_for(d))

    def test_03_missing_name(self):
        d = make_skill(self.base, "demo",
                       "---\ndescription: Use when 做 X 时触发并执行具体工作步骤。\n---\n" +
                       "\n".join(f"## {h}\n\n内容\n" for h in HEADS))
        self.assertIn("name", errors_for(d))

    def test_04_missing_description(self):
        d = make_skill(self.base, "demo",
                       "---\nname: demo\n---\n" +
                       "\n".join(f"## {h}\n\n内容\n" for h in HEADS))
        self.assertIn("description", errors_for(d))

    def test_05_illegal_name(self):
        d = make_skill(self.base, "Demo", skill_text("Demo"))  # 大写
        e = errors_for(d)
        self.assertTrue("name" in e or "与目录名" in e, e)

    def test_06_directory_name_mismatch(self):
        d = make_skill(self.base, "demo", skill_text("other-name"))
        self.assertIn("与目录名", errors_for(d))

    def test_07_illegal_frontmatter_field(self):
        d = make_skill(self.base, "demo", skill_text("demo", extra="applyTo: '**'"))
        self.assertIn("未知 Frontmatter 字段", errors_for(d))

    def test_08_missing_reference(self):
        d = make_skill(self.base, "demo",
                       skill_text("demo", path_refs=("references/ghost.md",)))
        self.assertIn("引用不存在", errors_for(d))

    def test_09_existing_reference_ok(self):
        d = make_skill(self.base, "demo",
                       skill_text("demo", path_refs=("references/real.md",)),
                       with_refs=("references/real.md",))
        self.assertEqual(errors_for(d).count("[ERROR"), 0)

    def test_10_escape_reference_rejected(self):
        d = make_skill(self.base, "demo",
                       skill_text("demo").replace("# Demo", "见 [x](../../core/README.md)\n\n# Demo"))
        self.assertIn("技能目录之外", errors_for(d))

    def test_11_tool_only_field_via_skill_validator(self):
        d = make_skill(self.base, "demo", skill_text("demo", extra="alwaysApply: true"))
        r = C.Report("t")
        SV._assert_no_tool_fields(d / "SKILL.md", r, "demo/SKILL.md")
        self.assertIn("工具专有字段", r.render())

    def test_12_description_length_limit(self):
        long_desc = "Use when " + "x" * 1100
        d = make_skill(self.base, "demo", skill_text("demo", description=long_desc))
        self.assertIn("超过 1024", errors_for(d))


if __name__ == "__main__":
    unittest.main(verbosity=2)
