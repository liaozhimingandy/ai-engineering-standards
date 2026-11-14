# 接入示例：纯 Python 应用 / 工具库

> 演示**单工具、最小接入**：一个没有前端、只用 Python 标准库的小项目，
> 只装 Codex 适配器。适合脚本、CLI 工具、数据处理程序。

## 1. 场景假设

- 目标项目：`~/work/report-tool`（纯 Python 3.10+，无第三方依赖）
- 只用 OpenAI Codex；不需要 Claude Code / Cursor / Copilot
- 项目根目录还没有 `AGENTS.md`

## 2. 安装

```bash
cd ~/work/ai-engineering-standards
python tooling/scripts/install.py --tool codex --target ~/work/report-tool --dry-run
python tooling/scripts/install.py --tool codex --target ~/work/report-tool
python tooling/scripts/check.py --target ~/work/report-tool
```

因为只装了 `codex`，`check.py` 只检查清单里登记的 codex 布局，
不会要求 `.claude/skills/` 等目录存在。

## 3. 安装后的目录

```text
report-tool/
├── AGENTS.md                         ← 新增（原本不存在）
├── .agents/skills/<5 个 skill>/SKILL.md
└── .ai-standards/
    ├── manifest.json
    ├── VERSION
    ├── core/ · standards/ · agents/ · adapters/ · docs/ · skills/
    ├── tooling/templates/
    └── README.md · CHANGELOG.md · LICENSE · CONTRIBUTING.md
```

`.ai-standards/` 是规范仓库的只读镜像，因此其中所有相对链接仍然有效。
`.ai-standards/skills/` 只作引用，不会被 Codex 当作技能发现目录
（Codex 扫描的是 `.agents/skills/`）。

## 4. 项目专属规则

纯 Python 项目通常只需要很少的项目规则。示例见
[project-rules/AGENTS.md.example](project-rules/AGENTS.md.example)：
它给出「运行命令、目录约定、报告输出格式」这类真正属于项目的信息，
不重复 `core/` 中的通用原则。

如果以后团队开始用 Claude Code：

```bash
python tooling/scripts/install.py --tool claude --target ~/work/report-tool
python tooling/scripts/check.py --target ~/work/report-tool
```

安装器会把 `claude` 加入 `manifest.json` 的 `tools`，并**保留**已有的
codex 文件；不会删除或重装无关内容。

## 5. 按需加载路径

```text
任务：修复 CSV 导出的边界 bug
  → 常驻：AGENTS.md 中的 10 条红线
  → 按需：.ai-standards/standards/languages/python.md
          .ai-standards/core/testing-principles.md
          .ai-standards/core/ai-behavior.md
  → Skills：software-development（含回归测试）→ code-review
```

## 6. 验收标准

- `check.py` 输出 `0 ERROR`；
- `.agents/skills/` 下有 5 个 `SKILL.md`，且 `name` 与目录名一致；
- `manifest.json` 中 `tools == ["codex"]`；
- 项目代码零改动（本示例只做安装，不改业务文件）。
