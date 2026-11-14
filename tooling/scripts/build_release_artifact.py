#!/usr/bin/env python3
"""构建发布归档（Phase 6，任务书 §六「发布产物」）。

用 **Python 标准库 zipfile** 生成可复现的源码归档，零第三方依赖、跨平台。
归档内容清单与 release_check 共用 ``version_validator.release_file_list``
（单一事实来源），因此不会"检查一套、打包另一套"。

用法：
    python tooling/scripts/build_release_artifact.py
    python tooling/scripts/build_release_artifact.py --output dist/foo.zip
    python tooling/scripts/build_release_artifact.py --check dist/foo.zip   # 校验产物完整

退出码：0 = 成功 / 产物完整；1 = 产物缺失必需文件或版本不符；2 = 工具执行失败。
"""

from __future__ import annotations

import argparse
import json
import sys
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tooling"))

from validators import common as C                # noqa: E402
from validators import version_validator as VV    # noqa: E402

ARTIFACT_PREFIX = "ai-engineering-standards"


def _spec_version(repo: Path) -> str:
    return (repo / "VERSION").read_text(encoding="utf-8").strip()


def build(repo: Path, output: Path) -> dict:
    """生成发布归档，返回构建摘要。"""
    repo, output = Path(repo), Path(output)
    files = VV.release_file_list(repo)
    # 排除输出归档自身（重构建时可能已存在），避免把上一版 zip 打进新 zip。
    try:
        self_rel = output.resolve().relative_to(repo.resolve()).as_posix()
        files = [f for f in files if f != self_rel]
    except ValueError:
        pass
    missing = VV.check_release_files(repo)
    if missing:
        raise ValueError(f"源仓库缺少必需发布文件，拒绝打包: {', '.join(missing[:8])}")
    version = _spec_version(repo)
    if not VV.is_semver(version):
        raise ValueError(f"VERSION 不是合法 SemVer: {version!r}")
    output.parent.mkdir(parents=True, exist_ok=True)
    root = f"{ARTIFACT_PREFIX}/"
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for rel in files:
            zf.write(repo / rel, arcname=root + rel)
        # 归档内附一份机器可读清单，便于验收"发布产物包含什么"。
        manifest = {
            "version": version,
            "artifact": output.name,
            "prefix": root,
            "file_count": len(files),
            "files": files,
            "generator": "tooling/scripts/build_release_artifact.py",
        }
        zf.writestr(root + "RELEASE-MANIFEST.json",
                    json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return {"version": version, "output": str(output), "file_count": len(files),
            "size_bytes": output.stat().st_size, "root": root}


def check_artifact(repo: Path, archive: Path, *, expected_version: str = "") -> dict:
    """校验已有归档含全部必需发布文件、版本与 VERSION 一致。"""
    repo, archive = Path(repo), Path(archive)
    if not archive.is_file():
        return {"ok": False, "errors": [f"归档不存在: {archive}"]}
    # check_release_files 返回的文件（无 / 结尾）必须逐个在归档里；
    # 目录项（以 / 结尾）只要归档里存在该前缀下任一文件即视为包含。
    required = VV.check_release_files(repo)
    needed = set(VV.release_file_list(repo))
    # 归档自身若落在仓库内（如 --output dist/... 之外的路径），不能把它算作
    # "应有文件"再要求归档包含自己，否则自校验永远失败。排除归档相对仓库的路径。
    try:
        self_rel = archive.resolve().relative_to(repo.resolve()).as_posix()
        needed.discard(self_rel)
    except ValueError:
        pass  # 归档在仓库外，无需排除
    root = f"{ARTIFACT_PREFIX}/"
    try:
        with zipfile.ZipFile(archive) as zf:
            names = set(zf.namelist())
            vpath = root + "VERSION"
            if vpath not in names:
                return {"ok": False, "errors": [f"归档缺少 {vpath}"]}
            version = zf.read(vpath).decode("utf-8").strip()
            missing = sorted(rel for rel in needed if root + rel not in names)
            missing_required = []
            for rel in required:
                if rel.endswith("/"):
                    if not any(n.startswith(root + rel) for n in names):
                        missing_required.append(rel)
                elif root + rel not in names:
                    missing_required.append(rel)
    except (zipfile.BadZipFile, OSError) as exc:
        return {"ok": False, "errors": [f"无法读取归档: {exc}"]}
    errors: list[str] = []
    if missing:
        errors.append(f"归档较源仓库缺失 {len(missing)} 个文件: {', '.join(missing[:6])}")
    if missing_required:
        errors.append(f"归档缺少必需发布文件: {', '.join(missing_required[:6])}")
    if not VV.is_semver(version):
        errors.append(f"归档内 VERSION 非法: {version!r}")
    if expected_version and version != expected_version:
        errors.append(f"归档版本 {version} 与期望 {expected_version} 不一致")
    return {"ok": not errors, "version": version, "file_count": len(names),
            "errors": errors}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="build_release_artifact.py",
                                     description="构建 / 校验发布归档（标准库 zipfile）")
    parser.add_argument("--repo", default=str(REPO), help="规范仓库根目录")
    parser.add_argument("--output", default="", help="输出 zip 路径（默认 dist/<name>-<version>.zip）")
    parser.add_argument("--check", default="", metavar="ZIP", help="只校验已有归档，不构建")
    parser.add_argument("--expect-version", default="", help="--check 时期望的归档版本")
    args = parser.parse_args(argv)
    repo = Path(args.repo).resolve()
    try:
        if args.check:
            result = check_artifact(repo, Path(args.check).resolve(),
                                    expected_version=args.expect_version)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            if not result["ok"]:
                print("[ERROR]    发布归档校验未通过。", file=sys.stderr)
                return 1
            return 0
        version = _spec_version(repo)
        output = Path(args.output).resolve() if args.output else \
            repo / "dist" / f"{ARTIFACT_PREFIX}-{version}.zip"
        result = build(repo, output)
        # 构建后自校验（含必需文件 + 版本一致）
        verify = check_artifact(repo, output, expected_version=version)
        result["verified"] = verify["ok"]
        result["verify_errors"] = verify.get("errors", [])
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if not verify["ok"]:
            print(f"[ERROR]    构建自校验失败: {verify.get('errors')}", file=sys.stderr)
            return 1
        return 0
    except Exception as exc:  # noqa: BLE001 - 工具失败返回 2
        print(f"[ERROR]    打包工具执行失败: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
