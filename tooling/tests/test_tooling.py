"""ai-engineering-standards 自动化验收测试。

只用 unittest（标准库）。运行：

    python -m unittest discover -s tooling/tests -v

覆盖：仓库自检、dry-run、五种工具的安装目录生成、已有文件保护、
--force 更新与差异、跨平台相对路径、含空格路径、清单与哈希、
完整性校验、增量安装、校验器单元测试。
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tooling"))

from validators import common as C          # noqa: E402
from validators import targets as T         # noqa: E402

PY = sys.executable
INSTALL = REPO / "tooling" / "scripts" / "install.py"
VALIDATE = REPO / "tooling" / "scripts" / "validate.py"
CHECK = REPO / "tooling" / "scripts" / "check.py"


def run(script: Path, *args: object, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [PY, str(script), *[str(a) for a in args]],
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def combined(proc: subprocess.CompletedProcess) -> str:
    return (proc.stdout or "") + (proc.stderr or "")


class ToolingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="aes-test-")
        self.target = Path(self.tmp.name) / "project"
        self.target.mkdir()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    # ------------------------------------------------------------------
    # 仓库自检与静态路径
    # ------------------------------------------------------------------

    def test_01_validate_repository(self) -> None:
        proc = run(VALIDATE)
        self.assertEqual(proc.returncode, 0, combined(proc))
        self.assertIn("0 ERROR", combined(proc))
        self.assertIn("已检查 5 个 Skill", combined(proc))

    def test_02_all_target_paths_are_legal(self) -> None:
        problems = T.check_paths(REPO)
        errors = [m for level, m in problems if level == "ERROR"]
        self.assertEqual(errors, [], "\n".join(errors))

    def test_03_tool_alias(self) -> None:
        self.assertEqual(T.normalize_tool("claude-code"), "claude")
        self.assertEqual(T.normalize_tool("CLAUDE"), "claude")
        self.assertIsNone(T.normalize_tool("unknown"))

    # ------------------------------------------------------------------
    # dry-run
    # ------------------------------------------------------------------

    def test_04_dry_run_writes_nothing(self) -> None:
        proc = run(INSTALL, "--all", "--dry-run", "--target", self.target)
        self.assertEqual(proc.returncode, 0, combined(proc))
        self.assertIn("DRY-RUN", combined(proc))
        self.assertFalse((self.target / T.MANIFEST_REL).exists())
        self.assertFalse((self.target / "AGENTS.md").exists())
        self.assertFalse((self.target / ".ai-standards").exists())
        self.assertEqual(list(self.target.iterdir()), [])

    def test_05_dry_run_paths_are_portable(self) -> None:
        proc = run(INSTALL, "--all", "--dry-run", "--target", self.target)
        self.assertEqual(proc.returncode, 0, combined(proc))
        dests = re.findall(r"^\[(?:PLAN  |UPDATE|KEEP  |SKIP  )\] (\S+)\s+<-",
                           combined(proc), re.M)
        self.assertGreater(len(dests), 50)
        for dest in dests:
            self.assertNotIn("\\", dest, dest)
            self.assertFalse(dest.startswith("/"), dest)
            self.assertNotIn("..", dest.split("/"), dest)
            self.assertIsNone(re.match(r"^[A-Za-z]:", dest), dest)
            self.assertEqual(T.path_problems(dest), [], dest)

    # ------------------------------------------------------------------
    # 五种工具分别安装
    # ------------------------------------------------------------------

    def _assert_skills_installed(self, skills_dir: str) -> None:
        for name in T.SKILL_NAMES:
            p = self.target / skills_dir / name / "SKILL.md"
            self.assertTrue(p.is_file(), f"缺少 {p}")
            self.assertIn(f"name: {name}", p.read_text(encoding="utf-8"))

    def _install_and_check(self, tool: str) -> None:
        proc = run(INSTALL, "--tool", tool, "--target", self.target)
        self.assertEqual(proc.returncode, 0, combined(proc))
        chk = run(CHECK, "--target", self.target)
        self.assertEqual(chk.returncode, 0, combined(chk))
        self.assertIn("0 ERROR", combined(chk))

    def test_06_install_codex(self) -> None:
        self._install_and_check("codex")
        self._assert_skills_installed(".agents/skills")
        self.assertTrue((self.target / "AGENTS.md").is_file())
        self.assertFalse((self.target / ".claude/skills").exists())

    def test_07_install_claude(self) -> None:
        self._install_and_check("claude")
        self._assert_skills_installed(".claude/skills")
        self.assertTrue((self.target / "CLAUDE.md").is_file())
        self.assertTrue((self.target / ".claude/rules/ai-engineering-standards.md").is_file())

    def test_08_install_cursor(self) -> None:
        self._install_and_check("cursor")
        self._assert_skills_installed(".cursor/skills")
        self.assertTrue((self.target / ".cursor/rules/ai-engineering-standards.mdc").is_file())
        self.assertIn("alwaysApply: true",
                      (self.target / ".cursor/rules/ai-engineering-standards.mdc")
                      .read_text(encoding="utf-8"))

    def test_09_install_copilot(self) -> None:
        self._install_and_check("copilot")
        self._assert_skills_installed(".github/skills")
        instr = self.target / ".github/instructions/ai-engineering-standards.instructions.md"
        self.assertTrue(instr.is_file())
        self.assertIn('applyTo: "**"', instr.read_text(encoding="utf-8"))
        self.assertTrue((self.target / ".github/copilot-instructions.md").is_file())

    def test_10_install_all(self) -> None:
        proc = run(INSTALL, "--all", "--target", self.target)
        self.assertEqual(proc.returncode, 0, combined(proc))
        chk = run(CHECK, "--target", self.target)
        self.assertEqual(chk.returncode, 0, combined(chk))
        for spec in T.TOOL_SPECS.values():
            self._assert_skills_installed(spec.skills_dir)
        manifest = json.loads((self.target / T.MANIFEST_REL).read_text(encoding="utf-8"))
        self.assertEqual(sorted(manifest["tools"]), sorted(T.TOOLS))

    # ------------------------------------------------------------------
    # 用户文件保护
    # ------------------------------------------------------------------

    def test_11_existing_user_files_never_overwritten(self) -> None:
        user_files = {
            "AGENTS.md": "# MY CODEX RULES - do not touch\n",
            "CLAUDE.md": "# MY CLAUDE RULES - do not touch\n",
            ".github/copilot-instructions.md": "# MY COPILOT RULES - do not touch\n",
            ".cursor/rules/api.mdc": "---\nalwaysApply: false\n---\n# MY RULE\n",
        }
        for rel, content in user_files.items():
            p = self.target / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")

        proc = run(INSTALL, "--all", "--target", self.target, "--force")
        self.assertEqual(proc.returncode, 0, combined(proc))
        for rel, content in user_files.items():
            self.assertEqual((self.target / rel).read_text(encoding="utf-8"), content,
                             f"用户文件被改动: {rel}")
        self.assertIn("用户自有文件，永不覆盖", combined(proc))
        # 可叠加通道仍然安装
        self.assertTrue((self.target / ".claude/rules/ai-engineering-standards.md").is_file())
        self.assertTrue((self.target / ".cursor/rules/ai-engineering-standards.mdc").is_file())
        self.assertTrue((self.target / ".github/instructions/"
                         "ai-engineering-standards.instructions.md").is_file())
        chk = run(CHECK, "--target", self.target)
        self.assertEqual(chk.returncode, 0, combined(chk))

    # ------------------------------------------------------------------
    # 更新、差异、完整性
    # ------------------------------------------------------------------

    def test_12_force_updates_managed_file_and_prints_diff(self) -> None:
        self.assertEqual(run(INSTALL, "--all", "--target", self.target).returncode, 0)
        managed = self.target / ".claude/rules/ai-engineering-standards.md"
        original = managed.read_bytes()
        managed.write_bytes(original + b"\n<!-- local edit -->\n")

        skip = run(INSTALL, "--all", "--target", self.target)
        self.assertIn("内容不同", combined(skip))
        self.assertEqual(managed.read_bytes(), original + b"\n<!-- local edit -->\n")

        corrupt_check = run(CHECK, "--target", self.target)
        self.assertEqual(corrupt_check.returncode, 1)
        self.assertIn("哈希与清单不一致", combined(corrupt_check))

        forced = run(INSTALL, "--all", "--target", self.target, "--force")
        self.assertEqual(forced.returncode, 0, combined(forced))
        self.assertIn("UPDATE", combined(forced))
        self.assertIn("--- installed/", combined(forced))  # unified diff 头部
        self.assertEqual(managed.read_bytes(), original)
        self.assertEqual(run(CHECK, "--target", self.target).returncode, 0)

    def test_13_manifest_records_version_and_hashes(self) -> None:
        self.assertEqual(run(INSTALL, "--all", "--target", self.target).returncode, 0)
        manifest = json.loads((self.target / T.MANIFEST_REL).read_text(encoding="utf-8"))
        version = (REPO / "VERSION").read_text(encoding="utf-8").strip()
        self.assertEqual(manifest["version"], version)
        self.assertEqual((self.target / T.VERSION_REL).read_text(encoding="utf-8").strip(),
                         version)
        self.assertEqual(manifest["schema"], T.MANIFEST_SCHEMA)
        self.assertGreater(len(manifest["files"]), 50)
        for rel, entry in manifest["files"].items():
            self.assertEqual(entry["sha256"], C.sha256_file(self.target / rel), rel)
            self.assertEqual(entry["owner"], "managed", rel)

    def test_14_integrity_check_detects_corruption(self) -> None:
        self.assertEqual(run(INSTALL, "--tool", "codex", "--target", self.target).returncode, 0)
        skill = self.target / ".agents/skills/code-review/SKILL.md"
        skill.write_bytes(skill.read_bytes() + b"\ncorrupted\n")
        chk = run(CHECK, "--target", self.target)
        self.assertEqual(chk.returncode, 1)
        self.assertIn("哈希与清单不一致", combined(chk))

    def test_15_incremental_install_preserves_previous_tool(self) -> None:
        self.assertEqual(run(INSTALL, "--tool", "codex", "--target", self.target).returncode, 0)
        self.assertEqual(run(INSTALL, "--tool", "claude", "--target", self.target).returncode, 0)
        manifest = json.loads((self.target / T.MANIFEST_REL).read_text(encoding="utf-8"))
        self.assertEqual(sorted(manifest["tools"]), ["claude", "codex"])
        self.assertTrue((self.target / ".agents/skills/code-review/SKILL.md").is_file())
        self.assertTrue((self.target / ".claude/skills/code-review/SKILL.md").is_file())
        self.assertEqual(run(CHECK, "--target", self.target).returncode, 0)

    # ------------------------------------------------------------------
    # 跨平台路径
    # ------------------------------------------------------------------

    def test_16_target_path_with_spaces(self) -> None:
        spaced = Path(self.tmp.name) / "my project with spaces"
        spaced.mkdir()
        proc = run(INSTALL, "--tool", "codex", "--target", spaced)
        self.assertEqual(proc.returncode, 0, combined(proc))
        chk = run(CHECK, "--target", spaced)
        self.assertEqual(chk.returncode, 0, combined(chk))

    def test_17_bundle_internal_links_resolve(self) -> None:
        self.assertEqual(run(INSTALL, "--all", "--target", self.target).returncode, 0)
        bundle = self.target / T.BUNDLE_ROOT
        report = C.Report("bundle links")
        checked = 0
        # 复制 check.py 的规则做一次独立断言
        for p in sorted(bundle.rglob("*.md")):
            for target, lineno in C.markdown_links(C.read_text(p)):
                clean = target.split("#")[0].strip()
                if not clean or clean.startswith(("http://", "https://", "mailto:")):
                    continue
                checked += 1
                if not (p.parent / clean).resolve().exists():
                    report.error(f"{p.relative_to(bundle)}:L{lineno} -> {clean}")
        self.assertGreater(checked, 20, "镜像内链接检查不能是空跑")
        self.assertEqual(report.count(C.ERROR), 0, report.render())

    def test_22_claude_code_alias_end_to_end(self) -> None:
        proc = run(INSTALL, "--tool", "claude-code", "--target", self.target)
        self.assertEqual(proc.returncode, 0, combined(proc))
        self.assertTrue((self.target / ".claude/skills/code-review/SKILL.md").is_file())
        self.assertEqual(run(CHECK, "--target", self.target).returncode, 0)

    def test_23_unknown_tool_fails_clearly(self) -> None:
        proc = run(INSTALL, "--tool", "not-a-tool", "--target", self.target)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("未知工具", combined(proc))
        self.assertFalse((self.target / T.MANIFEST_REL).exists())

    def test_18_check_on_uninstalled_directory(self) -> None:
        empty = Path(self.tmp.name) / "empty"
        empty.mkdir()
        chk = run(CHECK, "--target", empty)
        self.assertEqual(chk.returncode, 1)
        self.assertIn("未找到", combined(chk))

    # ------------------------------------------------------------------
    # 校验器单元测试
    # ------------------------------------------------------------------

    def test_19_validator_rejects_bad_skill(self) -> None:
        skill = Path(self.tmp.name) / "Bad-Skill"
        skill.mkdir()
        (skill / "SKILL.md").write_text(
            "---\nname: wrong-name\ndescription: Helps with stuff.\n"
            "paths: src/**\n---\n\n# Bad\n\n## 触发场景\n\n- 无\n",
            encoding="utf-8",
        )
        report = C.Report("unit")
        C.check_skill(skill, report)
        text = report.render()
        self.assertGreater(report.count(C.ERROR), 0)
        self.assertIn("未知 Frontmatter 字段", text)
        self.assertIn("与目录名", text)
        self.assertIn("缺少必需章节", text)

    def test_20_frontmatter_parser_handles_quotes_and_maps(self) -> None:
        data, body, errors = C.parse_frontmatter(
            '---\nname: demo\ndescription: "Use when 测试: 冒号"\n'
            'applyTo: "**"\nalwaysApply: true\nmetadata:\n  owner: team\n'
            '  version: "1.0.0"\n---\n\n# Body\n'
        )
        self.assertEqual(errors, [])
        self.assertEqual(data["description"], "Use when 测试: 冒号")
        self.assertEqual(data["applyTo"], "**")
        self.assertIs(data["alwaysApply"], True)
        self.assertEqual(data["metadata"], {"owner": "team", "version": "1.0.0"})
        self.assertIn("# Body", body)

    def test_21_path_problems(self) -> None:
        self.assertEqual(T.path_problems(".agents/skills/code-review/SKILL.md"), [])
        self.assertNotEqual(T.path_problems("../outside/file.md"), [])
        self.assertNotEqual(T.path_problems("C:\\abs\\file.md"), [])
        self.assertNotEqual(T.path_problems("dir\\file.md"), [])
        self.assertNotEqual(T.path_problems("a/CON/file.md"), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
