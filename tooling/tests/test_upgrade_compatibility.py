"""安装器升级兼容性测试（Phase 6，任务书 §八）。

复用 Phase 5 安装器（install.py / installer.manager），测试从旧版本升级到新版本：
  1. 用户未修改过的托管文件可安全更新（源内容变化时 --force 更新）；
  2. 用户修改过的文件必须受保护（默认跳过；--force 也仅覆盖"托管且被改"并先备份）；
  3. 新增文件能够正确安装（新版本往规范源加了文件）；
  4. 已删除的源文件不能导致误删用户文件（保留 + stale 告警 + manifest 仍登记）；
  5. 发生失败时能够恢复本次操作（第 N 次写入注入失败 → 回滚、无半成品、无假清单）；
  6. Manifest 能正确反映最终状态（版本、文件数、备份索引）；
  7. global 作用域升级同样安全（--home 隔离，绝不触碰真实 home）。

方法：在临时 clone 的规范仓库里跑它自己的 install.py（REPO 由 __file__ 解析为
clone 根），从而能改 VERSION / 改源 / 删源后再升级，全程不碰真实仓库。
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tooling"))
sys.path.insert(0, str(REPO / "tooling" / "scripts"))

from validators import targets as T          # noqa: E402
from installer import manager as MGR         # noqa: E402
from installer import filesystem as FS       # noqa: E402
import quality_gate as QG                    # noqa: E402

PY = sys.executable


def clone_repo(t: Path) -> Path:
    t.mkdir(parents=True, exist_ok=True)
    for item in ("core", "standards", "skills", "agents", "adapters", "docs",
                 "examples", "tooling"):
        shutil.copytree(REPO / item, t / item,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".ai-standards"))
    shutil.copytree(REPO / ".github", t / ".github")
    for f in ("VERSION", "README.md", "CHANGELOG.md", "LICENSE", "CONTRIBUTING.md"):
        shutil.copy2(REPO / f, t / f)
    return t


def run(script: Path, *args) -> object:
    import subprocess
    return subprocess.run([PY, str(script), *map(str, args)], capture_output=True,
                          text=True, encoding="utf-8", errors="replace")


def txt(proc) -> str:
    return (proc.stdout or "") + (proc.stderr or "")


def load_manifest(target: Path, scope: str = "project") -> dict:
    rel = T.GLOBAL_MANIFEST_REL if scope == "global" else T.MANIFEST_REL
    return json.loads((target / Path(*rel.split("/"))).read_text(encoding="utf-8"))


class UpgradeCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="aes-upgrade-")
        self.base = Path(self.tmp.name)
        self.repo = clone_repo(self.base / "repo")
        self.install = self.repo / "tooling/scripts/install.py"
        self.target = self.base / "target"
        self.target.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def _bump_version(self, new: str):
        (self.repo / "VERSION").write_text(new + "\n", encoding="utf-8")

    def _install(self, *extra):
        return run(self.install, "--tool", "codex", "--target", self.target, *extra)

    # ---- 1. 未修改托管文件可更新 -------------------------------------
    def test_01_unmodified_managed_file_updates(self):
        p = self._install()
        self.assertEqual(p.returncode, 0, txt(p))
        self.assertEqual(load_manifest(self.target)["version"],
                         (self.repo / "VERSION").read_text(encoding="utf-8").strip())
        # 源内容变化 + 升版本
        (self.repo / "core/engineering-principles.md").write_text(
            (self.repo / "core/engineering-principles.md").read_text(encoding="utf-8")
            + "\n%% NEW CONTENT 1.1.0 %%\n", encoding="utf-8")
        self._bump_version("1.2.0")
        p2 = self._install("--force")
        self.assertEqual(p2.returncode, 0, txt(p2))
        self.assertIn("%% NEW CONTENT 1.1.0 %%",
                      (self.target / ".ai-standards/core/engineering-principles.md")
                      .read_text(encoding="utf-8"))
        self.assertEqual(load_manifest(self.target)["version"], "1.2.0")

    # ---- 2. 用户改过的托管文件：默认保护；--force 备份后覆盖 ----------
    def test_02_user_modified_managed_file_default_protected_force_backs_up(self):
        # 首次安装当前版本，然后用户改动一份"已登记的托管文件"
        self.assertEqual(self._install().returncode, 0)
        managed = self.target / ".ai-standards/README.md"
        managed.write_text(managed.read_text(encoding="utf-8") + "\nUSER-EDIT\n",
                           encoding="utf-8")
        self._bump_version("1.2.0")

        # A. 默认升级：SKIP_MODIFIED，用户改动必须保留，并计入 Conflict
        p_default = self._install()
        self.assertEqual(p_default.returncode, 0, txt(p_default))
        self.assertIn("USER-EDIT", managed.read_text(encoding="utf-8"),
                      "默认升级必须保护用户改过的托管文件（SKIP_MODIFIED）")
        self.assertIn("Conflict", txt(p_default), "默认升级应报告冲突计数")
        self.assertEqual(load_manifest(self.target)["version"], "1.2.0")

        # B. --force 升级：CONFLICT_FORCE，先备份再恢复为托管内容
        #    这与"用户自有文件永不覆盖"（test_02b）是两套不同语义，不可混淆。
        p_force = self._install("--force")
        self.assertEqual(p_force.returncode, 0, txt(p_force))
        self.assertNotIn("USER-EDIT", managed.read_text(encoding="utf-8"),
                         "--force 升级应把用户改过的托管文件恢复为托管内容")
        mf = load_manifest(self.target)
        entry = mf.get("backups", {}).get(".ai-standards/README.md")
        self.assertIsNotNone(entry, "--force 覆盖前必须在 manifest.backups 登记该目标")
        backup = self.target / ".ai-standards" / entry["backup"]
        self.assertTrue(backup.is_file(), "备份文件应真实存在")
        self.assertIn("USER-EDIT", backup.read_text(encoding="utf-8"),
                      "备份必须保留覆盖前的用户原始内容")
        self.assertEqual(entry["target"], ".ai-standards/README.md")

    def test_02b_user_owned_entry_never_overwritten(self):
        # 用户自有入口文件（不在清单）即使 --force 也不覆盖
        entry = self.target / "AGENTS.md"
        entry.write_text("MY OWN AGENTS\n", encoding="utf-8")
        self.assertEqual(self._install().returncode, 0)
        self._bump_version("1.2.0")
        self.assertEqual(self._install("--force").returncode, 0)
        self.assertEqual(entry.read_text(encoding="utf-8").strip(), "MY OWN AGENTS")

    # ---- 3. 新增文件正确安装 -----------------------------------------
    def test_03_new_file_installed_on_upgrade(self):
        self.assertEqual(self._install().returncode, 0)
        before = len(load_manifest(self.target)["files"])
        # 新版本往规范源加了一份文件（会被纳入 bundle 镜像）
        (self.repo / "docs/release-new.md").write_text(
            "# 新增文档\n\n这是新版本新增的规范文档，内容足以超过占位文件阈值，"
            "用于验证升级时新文件能被正确安装并登记到清单中。\n", encoding="utf-8")
        self._bump_version("1.2.0")
        p2 = self._install("--force")
        self.assertEqual(p2.returncode, 0, txt(p2))
        self.assertTrue((self.target / ".ai-standards/docs/release-new.md").is_file())
        after = len(load_manifest(self.target)["files"])
        self.assertGreater(after, before, "新增文件应被安装并登记")

    # ---- 4. 已删除源文件不误删用户文件 --------------------------------
    def test_04_deleted_source_never_deletes_user_file(self):
        self.assertEqual(self._install().returncode, 0)
        mirror = self.target / ".ai-standards/standards/languages/typescript.md"
        self.assertTrue(mirror.is_file())
        mirror.write_text(mirror.read_text(encoding="utf-8") + "\nUSER-TWEAK\n",
                          encoding="utf-8")
        (self.repo / "standards/languages/typescript.md").unlink()   # 源被删
        self._bump_version("1.2.0")
        p2 = self._install("--force")
        self.assertEqual(p2.returncode, 0, txt(p2))
        self.assertTrue(mirror.is_file(), "源文件删除不得导致安装器删除已装文件")
        self.assertIn("USER-TWEAK", mirror.read_text(encoding="utf-8"))
        self.assertTrue(any("已不在仓库" in l or "stale" in l.lower()
                            for l in txt(p2).splitlines()), "应报 stale 告警")
        self.assertIn(".ai-standards/standards/languages/typescript.md",
                      load_manifest(self.target)["files"])

    # ---- 5. 失败可回滚 ------------------------------------------------
    def test_05_failure_rolls_back(self):
        real = FS.atomic_write
        state = {"n": 0, "fail_at": 5, "failed": False}

        def flaky(dest, data, **kw):
            state["n"] += 1
            if not state["failed"] and state["n"] == state["fail_at"]:
                state["failed"] = True
                raise OSError("INJECTED write failure")
            return real(dest, data, **kw)

        with mock.patch.object(MGR.FS, "atomic_write", side_effect=flaky):
            code, res = MGR.install(REPO, self.target, ["codex"], T.SCOPE_PROJECT, "1.1.0")
        self.assertTrue(state["failed"], "注入应触发")
        self.assertEqual(code, 1)
        self.assertTrue(res.get("rolled_back"))
        self.assertFalse((self.target / ".ai-standards/manifest.json").is_file(),
                         "失败不得留下虚假成功清单")
        leftovers = [p for p in self.target.rglob("*") if p.is_file()]
        self.assertEqual(leftovers, [], "回滚后不应残留半成品文件")

    # ---- 6. Manifest 最终状态 ----------------------------------------
    def test_06_manifest_reflects_final_state(self):
        self.assertEqual(self._install().returncode, 0)
        mf = load_manifest(self.target)
        self.assertEqual(mf["schema"], T.MANIFEST_SCHEMA)
        self.assertEqual(mf["installer_version"], T.INSTALLER_VERSION)
        self.assertEqual(mf["scope"], "project")
        self.assertEqual(mf["version"], (self.repo / "VERSION").read_text(encoding="utf-8").strip())
        self.assertIn("codex", mf["tools"])
        self.assertGreater(len(mf["files"]), 100)
        # manifest 不含绝对路径（安全约束）
        self.assertNotIn(str(self.target).replace("\\", "/"),
                         json.dumps(mf, ensure_ascii=False).replace("\\", "/"))

    # ---- 7. global 作用域升级安全 -------------------------------------
    def test_07_global_upgrade_isolated(self):
        home = self.base / "ghome"
        home.mkdir()
        inst = self.repo / "tooling/scripts/install.py"
        p = run(inst, "--all", "--scope", "global", "--home", home)
        self.assertEqual(p.returncode, 0, txt(p))
        mf = load_manifest(home, "global")
        self.assertEqual(mf["version"], (self.repo / "VERSION").read_text(encoding="utf-8").strip())
        self.assertEqual(mf["scope"], "global")
        # 升级：改源 + 升版本
        (self.repo / "core/security-principles.md").write_text(
            (self.repo / "core/security-principles.md").read_text(encoding="utf-8")
            + "\n%% GLOBAL UPGRADE %%\n", encoding="utf-8")
        self._bump_version("1.2.0")
        p2 = run(inst, "--all", "--scope", "global", "--home", home, "--force")
        self.assertEqual(p2.returncode, 0, txt(p2))
        mf2 = load_manifest(home, "global")
        self.assertEqual(mf2["version"], "1.2.0")
        # 生成入口重新渲染为新版本
        entry = home / ".codex/AGENTS.md"
        self.assertIn("version=1.2.0", entry.read_text(encoding="utf-8"))
        # 真实 home 未被触碰（--home 生效）
        self.assertFalse((self.base / "AGENTS.md").exists())

    def test_08_upgraded_repo_still_passes_gate(self):
        # 端到端：升级后的规范仓库自身仍应通过质量门禁（没有把升级测试弄坏仓库）
        r = QG.run_gate(REPO)
        self.assertTrue(r.passed, QG.render(r))


if __name__ == "__main__":
    unittest.main()
