#!/usr/bin/env python3
"""ai-engineering-standards 安装器（统一命令入口）。

只使用 Python 3.10+ 标准库，支持 Windows / Linux / macOS。
把 skills/ 单源与 adapters/ 复制到目标工具官方约定的目录，并把规范本体的
只读镜像放到受管目录。默认绝不覆盖用户自有文件。

作用域：
- project（默认）：装到 --target 指定的项目根；镜像 <target>/.ai-standards/。
- global：装到当前用户 home 的官方用户级目录；镜像 ~/.ai-engineering-standards/bundle/。
  Cursor 官方无磁盘全局规则路径，故不生成全局入口（不编造配置）。
  WorkBuddy 为 project-only（任务书禁止写用户级目录），--scope global 时完全跳过。

用法：
    python tooling/scripts/install.py --tool codex [--scope project] [--target PATH]
    python tooling/scripts/install.py --tool workbuddy --scope project --target PATH
    python tooling/scripts/install.py --tool claude --scope global
    python tooling/scripts/install.py --all --scope global --home /tmp/fakehome   # 隔离测试
    python tooling/scripts/install.py --all --dry-run
    python tooling/scripts/install.py --all --list
    python tooling/scripts/install.py --all --force

安装逻辑在 tooling/installer/ 包，本文件只做 CLI 解析与编排调用。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tooling"))

from validators import common as C          # noqa: E402
from validators import targets as T         # noqa: E402
from installer import manager as MGR        # noqa: E402
from installer import resolver as R         # noqa: E402


def _version() -> str:
    return R.version_of(REPO)


def _parse_tools(args, parser_error) -> list[str] | None:
    if args.all:
        return list(T.TOOLS)
    tools: list[str] = []
    for raw in args.tool:
        name = T.normalize_tool(raw)
        if name is None:
            aliases = "、".join(f"{k}={v}" for k, v in T.TOOL_ALIASES.items())
            parser_error(
                f"未知工具 {raw!r}；可选：{', '.join(T.TOOLS)}（别名：{aliases}）"
            )
            return None
        if name not in tools:
            tools.append(name)
    if not tools:
        parser_error("必须指定 --tool NAME 或 --all。用 --help 查看用法。")
        return None
    return tools


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="install.py",
        description="把 ai-engineering-standards 安装到目标项目或用户级目录（默认不覆盖用户文件）",
    )
    parser.add_argument("--tool", action="append", default=[],
                        help="工具名，可重复：codex / claude / cursor / copilot / workbuddy")
    parser.add_argument("--all", action="store_true", help="安装全部五种工具")
    parser.add_argument("--scope", default=T.SCOPE_PROJECT,
                        help="安装作用域：project（默认，装到 --target）或 global（装到用户 home）")
    parser.add_argument("--target", default=".",
                        help="目标项目根目录（scope=project 时有效，默认当前目录）")
    parser.add_argument("--home", default=None,
                        help="scope=global 时的逻辑 home 根（默认当前用户 home）；"
                             "用于隔离测试/CI，避免改动真实用户目录")
    parser.add_argument("--dry-run", action="store_true", help="只打印计划，不写任何文件")
    parser.add_argument("--list", action="store_true", help="列出将安装的文件与目标绝对路径，不写文件")
    parser.add_argument("--force", action="store_true",
                        help="允许更新本仓库管理的文件（覆盖前打印差异并备份；不覆盖用户文件）")
    parser.add_argument("--version", action="store_true", help="打印规范版本后退出")
    args = parser.parse_args(argv)

    version = _version()
    if args.version:
        print(version)
        return 0

    def perr(msg: str) -> None:
        print(f"[ERROR]    {msg}", file=sys.stderr)

    # 1. 校验仓库根
    if not all((REPO / p).exists() for p in ("VERSION", "skills", "core", T.BUNDLE_DIRS[0])):
        perr("无法定位规范仓库根目录：找不到 VERSION / skills/ / core/。请用绝对路径调用本脚本。")
        return 1

    # 2. 工具
    tools = _parse_tools(args, perr)
    if tools is None:
        return 1

    # 3. 作用域
    scope = T.normalize_scope(args.scope)
    if scope is None:
        perr(f"未知作用域 {args.scope!r}；可选：{', '.join(T.SCOPES)}")
        return 1

    # 4. 路径合法性（唯一事实来源 validators/targets.py）
    report = C.Report("Installation")
    for level, msg in T.check_paths(REPO):
        report.add(level, msg, "安装目标路径")
    if report.count(C.ERROR):
        print(report.render(), file=sys.stderr)
        perr("安装目标路径不合法，已中止。")
        return 1

    # 5. 解析安装根
    if scope == T.SCOPE_GLOBAL:
        # --home 仅覆盖"逻辑 home 根"，用于隔离测试/CI；默认仍是真实用户 home。
        # 注意：global 布局内的路径（.codex/ / .claude/skills 等）都相对此根构造。
        root = Path(args.home).expanduser().resolve() if args.home else Path.home().resolve()
        if args.home:
            print(f"[INFO]    scope=global：安装到指定逻辑 home {root}（隔离模式，--target 被忽略）")
        else:
            print(f"[INFO]    scope=global：安装到当前用户主目录 {root}"
                  "（--target 被忽略；如需隔离请用 --home）")
    else:
        try:
            root = Path(args.target).expanduser().resolve()
        except OSError as exc:
            perr(f"无法解析 --target: {exc}")
            return 1
        if not root.is_dir():
            perr(f"目标项目根目录不存在或不是目录: {root}。请先创建该目录再安装。")
            return 1

    # 6. 委托 installer.manager（含 dry-run / list / 事务 / 备份 / 摘要）
    code, _result = MGR.install(
        REPO, root, tools, scope, version,
        dry_run=args.dry_run, force=args.force, list_only=args.list,
    )
    if code == 0 and not (args.dry_run or args.list):
        mf_rel = T.GLOBAL_MANIFEST_REL if scope == T.SCOPE_GLOBAL else T.MANIFEST_REL
        print(f"[INFO]    下一步：python tooling/scripts/check.py --scope {scope}"
              f" --target {root}  （清单 {mf_rel}）")
    return code


if __name__ == "__main__":
    sys.exit(main())
