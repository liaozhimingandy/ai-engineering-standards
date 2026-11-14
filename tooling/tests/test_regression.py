"""跨模块回归测试（Phase 6，任务书 §四，7 个变异场景）。

在隔离的**临时副本**里注入变更，验证质量门禁能发现跨模块不一致：
  1. 修改 core 规范（改六段结构 / 新增未被映射的孤儿）→ 门禁发现；
  2. 修改 Skill（引用失效）→ 门禁发现；
  3. 修改适配器（config 与代码声明不一致）→ 门禁发现；
  4. 删除公共规范 → 关联引用失效被门禁发现；
  5. 修改 VERSION → 版本来源不一致被门禁发现；
  6. 修改安装器（删安全模块 / 删适配器源使计划不完整）→ 门禁发现；
     （完整升级行为回归见 test_upgrade_compatibility.py）
  7. 新增文件（含失效链接 / 非法内容）→ 被纳入验证。

每个用例都在临时目录 clone 后注入，绝不修改真实仓库。断言落在具体的
门禁分组与严重级别上，不只数总数，避免"恰好有别处 ERROR"造成假通过。
"""

from __future__ import annotations

import re
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


ORPHAN_CORE = """# 孤儿规范

## 文档目的
这是一份用于验证跨模块映射检查的新增规范文件，内容足够长以避开占位文件检测，
说明它没有被任何索引引用。

## 适用范围
仅用于测试，描述清楚适用范围与边界条件，保证内容充实完整。

## 核心规范
本规范定义了一些示例性的核心约束，用于占位并说明映射缺失应当被质量门禁发现与报告出来。

## 禁止事项
禁止把本测试规范当作真实规则使用，禁止在其它文档中引用它。

## 验收要求
验收标准是质量门禁能报告本文件未被任何索引或适配器映射引用。

## 相关规范引用
[core/README.md](README.md) 提供用语定义与冲突处理规则，本文件引用它。
"""


def clone(t: Path) -> Path:
    t.mkdir(parents=True, exist_ok=True)
    for item in ("core", "standards", "skills", "agents", "adapters", "docs",
                 "examples", "tooling"):
        shutil.copytree(REPO / item, t / item,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".ai-standards"))
    shutil.copytree(REPO / ".github", t / ".github")
    for f in ("VERSION", "README.md", "CHANGELOG.md", "LICENSE", "CONTRIBUTING.md"):
        shutil.copy2(REPO / f, t / f)
    return t


def level_errors(result: QG.GateResult, group: str) -> list[str]:
    rep = result.groups.get(group)
    return [i.message for i in rep.issues if i.level == C.ERROR] if rep else []


class RegressionTests(unittest.TestCase):
    def test_00_baseline_clean(self):
        r = QG.run_gate(REPO)
        self.assertTrue(r.passed, QG.render(r))
        self.assertEqual(r.total_errors, 0)

    def test_01_core_section_change_detected(self):
        with tempfile.TemporaryDirectory() as td:
            t = clone(Path(td))
            p = t / "core/security-principles.md"
            p.write_text(p.read_text(encoding="utf-8").replace("## 验收要求", "## 其他要求"),
                         encoding="utf-8")
            r = QG.run_gate(t)
            errs = level_errors(r, QG.GATE_CORE)
            self.assertTrue(any("验收要求" in e for e in errs), errs)

    def test_01b_core_new_orphan_mapping_detected(self):
        with tempfile.TemporaryDirectory() as td:
            t = clone(Path(td))
            (t / "core/orphan-principles.md").write_text(ORPHAN_CORE, encoding="utf-8")
            plain = QG.run_gate(t, strict=False)
            strict = QG.run_gate(t, strict=True)
            # 非严格：报 WARNING（孤儿映射）
            warns = [i.message for i in plain.groups[QG.GATE_CORE].issues
                     if i.level == C.WARNING and "未被任何索引" in i.message]
            self.assertTrue(warns, "新增孤儿 core 规范应报映射 WARNING")
            # 严格：升级为 ERROR
            errs = level_errors(strict, QG.GATE_CORE)
            self.assertTrue(any("映射" in e and "遗漏" in e for e in errs), errs)
            self.assertGreater(strict.promoted, 0)

    def test_02_skill_reference_broken_detected(self):
        with tempfile.TemporaryDirectory() as td:
            t = clone(Path(td))
            p = t / "skills/code-review/SKILL.md"
            txt = p.read_text(encoding="utf-8")
            txt2 = re.sub(r"\]\((assets/[^)]+|references/[^)]+)\)",
                          "](assets/NOPE-MISSING.md)", txt, count=1)
            self.assertNotEqual(txt2, txt, "注入未生效：应存在资源引用")
            p.write_text(txt2, encoding="utf-8")
            r = QG.run_gate(t)
            self.assertTrue(any("NOPE-MISSING" in e for g in QG.GATES
                                for e in level_errors(r, g)), "应发现 Skill 引用失效")

    def test_03_adapter_capability_drift_detected(self):
        with tempfile.TemporaryDirectory() as td:
            t = clone(Path(td))
            p = t / "tooling/config/targets.json"
            p.write_text(p.read_text(encoding="utf-8").replace(
                '"entry_status": "SUPPORTED"', '"entry_status": "PARTIAL"', 1),
                encoding="utf-8")
            r = QG.run_gate(t)
            errs = level_errors(r, QG.GATE_ADAPTERS)
            self.assertTrue(any("entry_status" in e and "不一致" in e for e in errs), errs)

    def test_04_deleted_public_spec_broken_links_detected(self):
        with tempfile.TemporaryDirectory() as td:
            t = clone(Path(td))
            (t / "core/architecture-principles.md").unlink()
            r = QG.run_gate(t)
            errs = level_errors(r, QG.GATE_CORE)
            broken = [e for e in errs if "无效内部引用" in e and
                      "architecture-principles.md" in e]
            self.assertGreater(len(broken), 0, "删除 core 规范应使关联引用失效")

    def test_05_version_change_inconsistency_detected(self):
        with tempfile.TemporaryDirectory() as td:
            t = clone(Path(td))
            (t / "VERSION").write_text("2.0.0\n", encoding="utf-8")
            r = QG.run_gate(t)
            errs_v = level_errors(r, QG.GATE_VERSION)
            errs_i = level_errors(r, QG.GATE_INSTALLER)
            self.assertTrue(any("badge" in e or "CHANGELOG" in e for e in errs_v),
                            errs_v + errs_i)

    def test_06_installer_safety_module_missing_detected(self):
        with tempfile.TemporaryDirectory() as td:
            t = clone(Path(td))
            (t / "tooling/installer/manifest.py").unlink()
            r = QG.run_gate(t)
            errs = level_errors(r, QG.GATE_INSTALLER)
            self.assertTrue(any("安全模块缺失" in e for e in errs), errs)
            # 同时 check.py 的结构检查也会报缺失必需文件
            self.assertFalse(r.passed)

    def test_06b_install_source_missing_breaks_plan(self):
        with tempfile.TemporaryDirectory() as td:
            t = clone(Path(td))
            (t / "adapters/copilot/copilot-instructions.md").unlink()
            r = QG.run_gate(t)
            errs = level_errors(r, QG.GATE_INSTALLER)
            self.assertTrue(any("源文件不存在" in e for e in errs), errs)

    def test_07_new_file_with_broken_link_detected(self):
        with tempfile.TemporaryDirectory() as td:
            t = clone(Path(td))
            (t / "docs/NEW-broken.md").write_text(
                "# 新增文件\n\n[坏链接](NOPE.md)\n\n这是一段说明文字，用于让文件超过占位检测的字节阈值，"
                "确保真正被测到的是失效链接而不是内容过少。\n", encoding="utf-8")
            r = QG.run_gate(t)
            errs = level_errors(r, QG.GATE_CORE)
            self.assertTrue(any("NOPE.md" in e and "无效内部引用" in e for e in errs), errs)

    def test_08_all_in_one_no_false_positive_on_clean(self):
        # 反向保护：clone 一份干净副本，门禁必须 0 ERROR（证明上述断言不是"随便都会失败"）
        with tempfile.TemporaryDirectory() as td:
            t = clone(Path(td))
            r = QG.run_gate(t)
            self.assertTrue(r.passed, QG.render(r))
            self.assertEqual(r.total_errors, 0, QG.render(r))


if __name__ == "__main__":
    unittest.main()
