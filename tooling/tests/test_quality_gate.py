"""质量门禁测试（Phase 6，任务书 §三/§四）。

验证 quality_gate 编排 Phase 5 验证器后：
  - 真实仓库四类门禁通过；
  - 各类破损（结构 / Skill / 适配器 / 版本 / 安装计划）能被对应门禁发现；
  - --strict 升级、--group 过滤、GateResult.to_dict / 退出码正确。
全部在临时副本操作，不改真实仓库。
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tooling"))
sys.path.insert(0, str(REPO / "tooling" / "scripts"))

import quality_gate as QG      # noqa: E402
from validators import common as C  # noqa: E402


def _clone(t: Path) -> Path:
    """复制门禁依赖的整棵目录到临时目录（忽略缓存与镜像）。"""
    t.mkdir(parents=True, exist_ok=True)
    for item in ("core", "standards", "skills", "agents", "adapters", "docs",
                 "examples", "tooling"):
        shutil.copytree(REPO / item, t / item,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc",
                                                      ".ai-standards"))
    # .github 是隐藏目录，copytree 普通目录时不会带上；Phase 6 起两个 workflow
    # 属于 REQUIRED_FILES，门禁结构检查需要它们在场（与其他 clone 保持一致）。
    shutil.copytree(REPO / ".github", t / ".github")
    for f in ("VERSION", "README.md", "CHANGELOG.md", "LICENSE", "CONTRIBUTING.md"):
        shutil.copy2(REPO / f, t / f)
    return t


class QualityGateTests(unittest.TestCase):
    def test_01_real_repo_all_gates_pass(self):
        r = QG.run_gate(REPO)
        self.assertEqual(r.tool_error, "")
        self.assertTrue(r.passed, QG.render(r))
        self.assertEqual(r.total_errors, 0, QG.render(r))
        self.assertEqual(set(r.groups.keys()), set(QG.GATES))

    def test_02_real_repo_strict_passes(self):
        r = QG.run_gate(REPO, strict=True)
        self.assertTrue(r.passed, QG.render(r))
        self.assertTrue(r.strict)

    def test_03_core_structure_violation_detected(self):
        with tempfile.TemporaryDirectory() as td:
            t = _clone(Path(td))
            # 破坏一份 core 规范的六段结构（真实标题是「## 验收要求」）
            p = t / "core/security-principles.md"
            p.write_text(p.read_text(encoding="utf-8").replace("## 验收要求", "## 其他要求"),
                         encoding="utf-8")
            r = QG.run_gate(t, only=(QG.GATE_CORE,))
            self.assertFalse(r.passed)
            self.assertGreater(r.groups[QG.GATE_CORE].count(C.ERROR), 0, QG.render(r))

    def test_04_skill_frontmatter_violation_detected(self):
        with tempfile.TemporaryDirectory() as td:
            t = _clone(Path(td))
            p = t / "skills/code-review/SKILL.md"
            p.write_text(p.read_text(encoding="utf-8").replace(
                "name: code-review", "name: WRONG", 1), encoding="utf-8")
            r = QG.run_gate(t, only=(QG.GATE_SKILLS,))
            self.assertFalse(r.passed)
            self.assertGreater(r.groups[QG.GATE_SKILLS].count(C.ERROR), 0, QG.render(r))

    def test_05_adapter_config_drift_detected(self):
        with tempfile.TemporaryDirectory() as td:
            t = _clone(Path(td))
            p = t / "tooling/config/targets.json"
            cur = (t / "VERSION").read_text(encoding="utf-8").strip()
            p.write_text(p.read_text(encoding="utf-8").replace(
                f'"installer_version": "{cur}"', '"installer_version": "9.9.9"'),
                encoding="utf-8")
            r = QG.run_gate(t, only=(QG.GATE_ADAPTERS,))
            self.assertFalse(r.passed)
            self.assertGreater(r.groups[QG.GATE_ADAPTERS].count(C.ERROR), 0, QG.render(r))

    def test_06_version_drift_detected(self):
        with tempfile.TemporaryDirectory() as td:
            t = _clone(Path(td))
            cur = (t / "VERSION").read_text(encoding="utf-8").strip()
            (t / "README.md").write_text(
                (t / "README.md").read_text(encoding="utf-8").replace(
                    f"version-{cur}-blue", "version-7.7.7-blue"), encoding="utf-8")
            r = QG.run_gate(t, only=(QG.GATE_VERSION,))
            self.assertFalse(r.passed)
            self.assertGreater(r.groups[QG.GATE_VERSION].count(C.ERROR), 0, QG.render(r))

    def test_07_missing_source_in_install_plan_detected(self):
        with tempfile.TemporaryDirectory() as td:
            t = _clone(Path(td))
            # 删除一个适配器源，安装计划生成会报"源文件不存在"
            (t / "adapters/copilot/copilot-instructions.md").unlink()
            r = QG.run_gate(t, only=(QG.GATE_INSTALLER,))
            self.assertFalse(r.passed)
            self.assertGreater(r.groups[QG.GATE_INSTALLER].count(C.ERROR), 0, QG.render(r))

    def test_08_group_filter(self):
        r = QG.run_gate(REPO, only=(QG.GATE_SKILLS,))
        self.assertEqual(set(r.groups.keys()), {QG.GATE_SKILLS})
        self.assertNotIn(QG.GATE_CORE, r.groups)

    def test_09_strict_promotes_warning(self):
        with tempfile.TemporaryDirectory() as td:
            t = _clone(Path(td))
            # 制造一个纯 WARNING（标题层级跳跃：H1 后直接 H3），
            # strict 下应被升级为 ERROR；刻意不改链接，避免引入链接失效 ERROR 干扰。
            p = t / "docs/architecture.md"
            lines = p.read_text(encoding="utf-8").splitlines()
            lines.insert(2, "### 跳跃标题")
            p.write_text("\n".join(lines), encoding="utf-8")
            plain = QG.run_gate(t, only=(QG.GATE_CORE,), strict=False)
            strict = QG.run_gate(t, only=(QG.GATE_CORE,), strict=True)
            self.assertEqual(plain.total_errors, 0)
            self.assertGreater(plain.total_warnings, 0)
            self.assertGreater(strict.promoted, 0, "应至少升级 1 条 WARNING")
            self.assertGreater(strict.total_errors, plain.total_errors)

    def test_10_to_dict_shape(self):
        r = QG.run_gate(REPO, strict=True)
        d = r.to_dict()
        self.assertTrue(d["passed"])
        self.assertTrue(d["strict"])
        self.assertEqual(len(d["groups"]), len(QG.GATES))
        self.assertIn("issues", d["groups"][QG.GATE_CORE])
        self.assertEqual(d["version"]["spec"], (REPO / "VERSION").read_text(encoding="utf-8").strip())

    def test_11_missing_repo_returns_tool_error(self):
        with tempfile.TemporaryDirectory() as td:
            r = QG.run_gate(Path(td))
            self.assertFalse(r.passed)
            self.assertIn("无法定位", r.tool_error)

    def test_12_render_contains_all_groups(self):
        r = QG.run_gate(REPO)
        out = QG.render(r)
        for g in QG.GATES:
            self.assertIn(g, out)


if __name__ == "__main__":
    unittest.main()
