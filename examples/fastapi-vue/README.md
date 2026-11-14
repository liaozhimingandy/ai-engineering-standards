# 接入示例：FastAPI + Vue 项目

> 演示如何把通用规范装进一个已有项目，同时保留项目专属规则。
> 本目录是**示例**，不是可运行的应用；所有命令都可直接复制到真实项目中使用。

## 1. 场景假设

- 目标项目：`~/work/order-console`（后端 FastAPI + 前端 Vue 3 + TypeScript）
- 项目已有自己的 `AGENTS.md` 和 `.cursor/rules/api.mdc`（**用户资产，不能被覆盖**）
- 团队同时使用 Codex、Claude Code、Cursor、GitHub Copilot
- 规范仓库位于 `~/work/ai-engineering-standards`

## 2. 安装（先 dry-run）

```bash
cd ~/work/ai-engineering-standards
python tooling/scripts/validate.py
python tooling/scripts/install.py --all --target ~/work/order-console --dry-run
```

`--dry-run` 输出中预期出现：

```
[SKIP  ] AGENTS.md  <- adapters/codex/AGENTS.md
[WARNING] 跳过 AGENTS.md（用户自有文件，永不覆盖）
[PLAN  ] .claude/rules/ai-engineering-standards.md ...
[PLAN  ] .cursor/rules/ai-engineering-standards.mdc ...
[PLAN  ] .github/instructions/ai-engineering-standards.instructions.md ...
```

确认无误后真正安装：

```bash
python tooling/scripts/install.py --all --target ~/work/order-console
python tooling/scripts/check.py --target ~/work/order-console
```

## 3. 安装后的目录（关键部分）

```text
order-console/
├── AGENTS.md                                   ← 用户原有，保持不变
├── CLAUDE.md                                   ← 新增（原本不存在）
├── .claude/rules/ai-engineering-standards.md    ← 新增（可叠加）
├── .claude/skills/<5 个 skill>/SKILL.md
├── .cursor/rules/api.mdc                        ← 用户原有，保持不变
├── .cursor/rules/ai-engineering-standards.mdc   ← 新增（唯一文件名，不冲突）
├── .cursor/skills/<5 个 skill>/SKILL.md
├── .github/copilot-instructions.md
├── .github/instructions/ai-engineering-standards.instructions.md
├── .github/skills/<5 个 skill>/SKILL.md
├── .agents/skills/<5 个 skill>/SKILL.md
└── .ai-standards/                              ← 规范本体镜像 + manifest.json
```

由于 `AGENTS.md` 已被占用，Codex 的红线由项目自有 `AGENTS.md` 中的一段
人工引用提供（见 §4），其余三个工具通过可叠加规则通道生效。

## 4. 项目专属规则（通用规范不收）

| 工具 | 放哪里 | 本示例文件 |
| --- | --- | --- |
| Codex | 自有 `AGENTS.md` 追加一段引用 | [project-rules/AGENTS.md.example](project-rules/AGENTS.md.example) |
| Claude Code | `CLAUDE.local.md`（加入 `.gitignore`） | [project-rules/CLAUDE.local.md.example](project-rules/CLAUDE.local.md.example) |
| Cursor | `.cursor/rules/order-console.mdc`（自定义文件名） | [project-rules/order-console.mdc.example](project-rules/order-console.mdc.example) |
| Copilot | `.github/instructions/backend.instructions.md`（带 `applyTo`） | [project-rules/backend.instructions.md.example](project-rules/backend.instructions.md.example) |

原则：**通用规则来自本仓库，项目规则留在项目里，两者不互相覆盖。**
不要手工编辑带 `AI-ENGINEERING-STANDARDS:MANAGED` 标记的文件——
它们会在下次 `--force` 时被覆盖。

## 5. 日常使用

```text
任务：新增「订单导出」接口
  → 常驻：适配器中的 10 条红线
  → 按需：.ai-standards/standards/languages/python.md
          .ai-standards/standards/backend/api.md
          .ai-standards/standards/backend/database.md
          .ai-standards/core/security-principles.md
  → Skills：requirements-analysis → architecture-design
           → software-development → code-review
```

Claude Code 可直接 `/software-development`；Codex 用 `/skills` 或
`$software-development`；Cursor 在 Agent 中输入 `/` 搜索技能名。

## 6. 更新规范

```bash
git -C ~/work/ai-engineering-standards pull
python ~/work/ai-engineering-standards/tooling/scripts/install.py \
  --all --target ~/work/order-console --force
python ~/work/ai-engineering-standards/tooling/scripts/check.py \
  --target ~/work/order-console
```

`--force` 只更新 `.ai-standards/manifest.json` 登记过的托管文件，
打印差异后覆盖；`AGENTS.md` 与 `.cursor/rules/api.mdc` 始终保持原样。

## 7. 验收标准

- `check.py` 输出 `0 ERROR`；
- 用户自有 `AGENTS.md` 与 `.cursor/rules/api.mdc` 的哈希与安装前一致；
- 五个工具（Codex / Claude Code / Cursor / Copilot / WorkBuddy）的 Skills 目录各含 5 个 `SKILL.md`；
- `.ai-standards/manifest.json` 的 `version` 等于规范仓库 `VERSION`。
