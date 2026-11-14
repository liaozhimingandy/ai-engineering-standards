"""腾讯 WorkBuddy 适配器测试（Phase 6 新增第 5 个工具）。

对应任务书：§2 官方格式、§3 适配器实现、§4 安装器集成、§5 静态验证、§6 不影响四工具。
只做**静态可自动化验证**；真实客户端运行时加载（规则自动生效、Skills/Agents 发现）
无法在本环境证明，明确标为未验证，不宣称通过。

覆盖：
- 注册表：TOOLS/TOOL_SPECS/GLOBAL_TOOL_SPECS/config 双源一致、别名；
- 适配器源：入口与叠加规则格式、无 frontmatter、MANAGED 标记；
- 安装结果：目录结构、Skills 逐字节、Rules/CODEBUDDY.md 内容；
- 安装器语义：重复安装、用户自有保护、托管文件升级备份、manifest 版本归属；
- project-only：--scope global 不写任何用户级 .codebuddy/ 目录；
- 不影响四工具：5 工具一起装时四工具目标路径齐全且校验通过。
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tooling"))

from validators import common as C            # noqa: E402
from validators import targets as T           # noqa: E402
from validators import adapter_validator as AV  # noqa: E402
from installer import resolver as R           # noqa: E402

PY = sys.executable
INSTALL = REPO / "tooling" / "scripts" / "install.py"
CHECK = REPO / "tooling" / "scripts" / "check.py"


def run(script: Path, *args: object) -> subprocess.CompletedProcess:
    return subprocess.run(
        [PY, str(script), *[str(a) for a in args]],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )


def first_line(p: Path) -> str:
    return p.read_text(encoding="utf-8").splitlines()[0] if p.is_file() else ""


class WorkBuddyAdapterTests(unittest.TestCase):

    # ---- 1. 注册表与双源一致 --------------------------------------------
    def test_01_registered_as_fifth_tool(self):
        self.assertIn("workbuddy", T.TOOLS)
        self.assertEqual(len(T.TOOLS), 5)
        self.assertIn("workbuddy", T.TOOL_SPECS)
        self.assertIn("workbuddy", T.GLOBAL_TOOL_SPECS)
        self.assertEqual(T.normalize_tool("workbuddy"), "workbuddy")
        self.assertEqual(T.normalize_tool("codebuddy"), "workbuddy")
        self.assertEqual(T.normalize_tool("tencent-workbuddy"), "workbuddy")

    def test_02_project_spec_maps_official_paths(self):
        spec = T.TOOL_SPECS["workbuddy"]
        self.assertEqual(spec.skills_dir, ".codebuddy/skills")
        dests = {a.dest for a in spec.all_adapters}
        self.assertIn("CODEBUDDY.md", dests)
        self.assertIn(".codebuddy/rules/ai-engineering-standards.md", dests)
        guarded = {a.dest for a in spec.adapters}
        self.assertIn("CODEBUDDY.md", guarded, "入口必须是 guarded")

    def test_03_global_spec_is_project_only_with_no_entry(self):
        gs = T.GLOBAL_TOOL_SPECS["workbuddy"]
        self.assertTrue(gs.project_only, "WorkBuddy 必须是 project-only")
        self.assertIsNone(gs.entry, "不得配置用户级全局入口")

    def test_04_config_code_consistent_including_project_only(self):
        r = C.Report("t")
        AV.validate_config_code_consistency(REPO, r)
        self.assertEqual(r.count(C.ERROR), 0, r.render())
        cfg = json.loads((REPO / "tooling/config/targets.json").read_text(encoding="utf-8"))
        self.assertIn("workbuddy", cfg["tools"])
        self.assertTrue(cfg["global"]["workbuddy"]["project_only"])
        self.assertEqual(cfg["global"]["workbuddy"]["entries"], [])

    def test_05_project_only_flag_drift_detected(self):
        """config 里把 project_only 改成 false 应被一致性校验发现。"""
        with tempfile.TemporaryDirectory(prefix="aes-wb-") as td:
            base = Path(td)
            (base / "tooling/config").mkdir(parents=True)
            cfg = json.loads((REPO / "tooling/config/targets.json").read_text(encoding="utf-8"))
            cfg["global"]["workbuddy"]["project_only"] = False
            (base / "tooling/config/targets.json").write_text(
                json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
            r = C.Report("t")
            AV.validate_config_code_consistency(base, r)
            self.assertIn("project_only", r.render())

    # ---- 2. 适配器源文件格式 --------------------------------------------
    def test_06_adapter_sources_pass_check_adapter(self):
        spec = T.TOOL_SPECS["workbuddy"]
        r = C.Report("t")
        for a in spec.all_adapters:
            C.check_adapter(REPO / a.source, a, r)
        self.assertEqual(r.count(C.ERROR), 0, r.render())
        self.assertEqual(r.count(C.WARNING), 0, r.render())

    def test_07_rules_and_entry_have_no_frontmatter(self):
        """两个产物都是纯 Markdown、无 frontmatter（官方默认常驻）。"""
        for rel in ("adapters/workbuddy/CODEBUDDY.md",
                    "adapters/workbuddy/rules/ai-engineering-standards.md"):
            text = (REPO / rel).read_text(encoding="utf-8")
            self.assertFalse(text.startswith("---"), f"{rel} 不应有 frontmatter")
            self.assertIn("AI-ENGINEERING-STANDARDS:MANAGED", text, rel)
            self.assertIn("tool=workbuddy", text, rel)

    def test_08_no_tool_only_frontmatter_tokens_at_line_start(self):
        """plain 适配器行首不得出现工具专有字段（防止字段泄漏/误判为条件规则）。"""
        for rel in ("adapters/workbuddy/CODEBUDDY.md",
                    "adapters/workbuddy/rules/ai-engineering-standards.md"):
            for line in (REPO / rel).read_text(encoding="utf-8").splitlines():
                for token in ("applyTo:", "alwaysApply:", "globs:", "paths:", "enabled:"):
                    self.assertFalse(line.startswith(token), f"{rel} 行首出现 {token}")

    # ---- 3. 安装结果：结构 / 格式 / Skills 逐字节 -------------------------
    def _install(self, target: Path, *extra: str) -> subprocess.CompletedProcess:
        return run(INSTALL, "--tool", "workbuddy", "--scope", "project",
                   "--target", str(target), *extra)

    def test_09_install_creates_official_structure(self):
        with tempfile.TemporaryDirectory(prefix="aes-wb-") as td:
            t = Path(td)
            p = self._install(t)
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            self.assertTrue((t / "CODEBUDDY.md").is_file())
            self.assertTrue((t / ".codebuddy/rules/ai-engineering-standards.md").is_file())
            for name in T.SKILL_NAMES:
                self.assertTrue((t / ".codebuddy/skills" / name / "SKILL.md").is_file(), name)
            self.assertTrue((t / ".ai-standards/manifest.json").is_file())

    def test_10_skills_byte_identical_to_single_source(self):
        with tempfile.TemporaryDirectory(prefix="aes-wb-") as td:
            t = Path(td)
            self.assertEqual(self._install(t).returncode, 0)
            for src in (REPO / "skills").rglob("SKILL.md"):
                rel = src.relative_to(REPO / "skills")
                dst = t / ".codebuddy/skills" / rel
                self.assertTrue(dst.is_file(), rel)
                self.assertEqual(hashlib.sha256(src.read_bytes()).digest(),
                                 hashlib.sha256(dst.read_bytes()).digest(), rel)

    def test_11_installed_rules_format_and_content(self):
        with tempfile.TemporaryDirectory(prefix="aes-wb-") as td:
            t = Path(td)
            self.assertEqual(self._install(t).returncode, 0)
            rule = (t / ".codebuddy/rules/ai-engineering-standards.md").read_text(encoding="utf-8")
            entry = (t / "CODEBUDDY.md").read_text(encoding="utf-8")
            self.assertIn("AI-ENGINEERING-STANDARDS:MANAGED", rule)
            self.assertIn("AI-ENGINEERING-STANDARDS:MANAGED", entry)
            self.assertFalse(rule.startswith("---"), "叠加规则不带 frontmatter")
            self.assertIn(".codebuddy/skills/", rule)
            self.assertIn("AGENTS.md", entry, "入口应说明与 AGENTS.md 兼容")
            self.assertIn("不写", entry, "入口应声明不写用户级目录")

    def test_12_installation_check_passes(self):
        with tempfile.TemporaryDirectory(prefix="aes-wb-") as td:
            t = Path(td)
            self.assertEqual(self._install(t).returncode, 0)
            p = run(CHECK, "--scope", "project", "--target", str(t))
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            self.assertIn("0 ERROR", p.stdout + p.stderr)

    # ---- 4. 安装器语义：重复 / 保护 / 升级 --------------------------------
    def test_13_repeat_install_is_noop(self):
        with tempfile.TemporaryDirectory(prefix="aes-wb-") as td:
            t = Path(td)
            self.assertEqual(self._install(t).returncode, 0)
            p2 = self._install(t)
            self.assertEqual(p2.returncode, 0, p2.stdout + p2.stderr)
            self.assertIn("Unchanged", p2.stdout)
            self.assertIn("Conflict  : 0", p2.stdout)

    def test_14_user_owned_entry_and_rule_never_overwritten(self):
        """安装前就存在的用户自有入口/规则（无归属证据）必须 SKIP。"""
        with tempfile.TemporaryDirectory(prefix="aes-wb-") as td:
            t = Path(td)
            (t / ".codebuddy/rules").mkdir(parents=True)
            (t / "CODEBUDDY.md").write_text("# 我的项目指令\n用户自有内容\n", encoding="utf-8")
            rule = t / ".codebuddy/rules/ai-engineering-standards.md"
            rule.write_text("# 我的规则\n自定义\n", encoding="utf-8")
            p = self._install(t, "--force")
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            self.assertIn("用户自有内容", (t / "CODEBUDDY.md").read_text(encoding="utf-8"))
            self.assertIn("自定义", rule.read_text(encoding="utf-8"))

    def test_15_managed_file_updates_with_force_and_backup(self):
        """托管文件被用户改过：默认跳过；--force 先备份再覆盖为源内容。"""
        with tempfile.TemporaryDirectory(prefix="aes-wb-") as td:
            t = Path(td)
            self.assertEqual(self._install(t).returncode, 0)
            rule = t / ".codebuddy/rules/ai-engineering-standards.md"
            rule.write_text((rule.read_text(encoding="utf-8") + "\nUSER-EDIT\n"),
                            encoding="utf-8")
            p_skip = self._install(t)
            self.assertIn("USER-EDIT", rule.read_text(encoding="utf-8"), "默认应跳过保留用户改动")
            p_force = self._install(t, "--force")
            self.assertEqual(p_force.returncode, 0, p_force.stdout + p_force.stderr)
            self.assertNotIn("USER-EDIT", rule.read_text(encoding="utf-8"),
                             "--force 应覆盖为托管内容")
            mf = json.loads((t / ".ai-standards/manifest.json").read_text(encoding="utf-8"))
            self.assertTrue(mf.get("backups"), "--force 覆盖前应留下备份记录")

    def test_16_manifest_records_version_scope_tool(self):
        with tempfile.TemporaryDirectory(prefix="aes-wb-") as td:
            t = Path(td)
            self.assertEqual(self._install(t).returncode, 0)
            mf = json.loads((t / ".ai-standards/manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(mf["tools"], ["workbuddy"])
            self.assertEqual(mf["version"], (REPO / "VERSION").read_text(encoding="utf-8").strip())
            self.assertEqual(mf["installer_version"], T.INSTALLER_VERSION)
            self.assertEqual(mf["scope"], "project")
            self.assertGreater(len(mf["files"]), 50)
            # 不含绝对路径
            for dest in mf["files"]:
                self.assertFalse(Path(dest).is_absolute(), dest)
                self.assertNotIn("\\", dest, dest)

    # ---- 5. project-only：global 作用域零写入用户级目录 --------------------
    def test_17_global_scope_writes_no_user_level_config(self):
        with tempfile.TemporaryDirectory(prefix="aes-wb-home-") as td:
            home = Path(td)
            p = run(INSTALL, "--tool", "workbuddy", "--scope", "global", "--home", str(home))
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            self.assertFalse((home / ".codebuddy").exists(),
                             "global 作用域不得创建 ~/.codebuddy/ 用户级配置目录")
            # bundle 镜像仍可存在（只读规范副本，非用户级 .codebuddy/ 配置）
            self.assertIsNone(T.GLOBAL_TOOL_SPECS["workbuddy"].entry)

    def test_18_global_plan_excludes_workbuddy_user_paths(self):
        plan = R.build_plan(REPO, ["workbuddy"], T.SCOPE_GLOBAL)
        user_cfg = [p.dest_rel for p in plan
                    if p.dest_rel.startswith(".codebuddy/")]
        self.assertEqual(user_cfg, [], f"global 计划不应含用户级 .codebuddy/ 路径: {user_cfg}")

    # ---- 6. 不影响原有四工具 ----------------------------------------------
    def test_19_all_five_tools_install_without_conflict(self):
        with tempfile.TemporaryDirectory(prefix="aes-wb-") as td:
            t = Path(td)
            p = run(INSTALL, "--all", "--scope", "project", "--target", str(t))
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            self.assertIn("Conflict  : 0", p.stdout)
            # 四工具原有目标全部齐全
            existing = {
                "codex": t / "AGENTS.md",
                "claude": t / "CLAUDE.md",
                "cursor": t / ".cursor/rules/ai-engineering-standards.mdc",
                "copilot": t / ".github/copilot-instructions.md",
                "workbuddy": t / "CODEBUDDY.md",
            }
            for tool, path in existing.items():
                self.assertTrue(path.is_file(), f"{tool} 目标缺失: {path}")
            mf = json.loads((t / ".ai-standards/manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(sorted(mf["tools"]), sorted(T.TOOLS))

    def test_20_four_tool_targets_unchanged_by_workbuddy(self):
        """五工具装配后，四工具各自目标路径与 TOOL_SPECS 声明逐项存在。"""
        with tempfile.TemporaryDirectory(prefix="aes-wb-") as td:
            t = Path(td)
            self.assertEqual(run(INSTALL, "--all", "--target", str(t)).returncode, 0)
            for tool in ("codex", "claude", "cursor", "copilot"):
                for a in T.TOOL_SPECS[tool].all_adapters:
                    self.assertTrue((t / a.dest).is_file(), f"[{tool}] {a.dest}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
