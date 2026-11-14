# tooling：安装与验证工具链

仅使用 **Python 3.10+ 标准库**，零第三方依赖，跨平台（Windows / Linux / macOS）。
所有命令都从 `scripts/` 进入；安装逻辑与验证逻辑严格分离，不互相 import 核心模块。

---

## 1. 目录分层

```text
tooling/
├── scripts/           # 用户直接调用的命令入口（薄 CLI）
│   ├── install.py     # 安装器：--tool/--all/--scope/--target/--dry-run/--list/--force
│   ├── validate.py    # 规范验证器：--skills/--core/--adapters/--strict
│   └── check.py       # 安装结果检查（--target）+ 一致性检查（--all/--skills/--adapters）
├── installer/         # 只负责"安全地写文件"
│   ├── manager.py     # 编排：计划→决策→事务→完整性→清单→摘要
│   ├── resolver.py    # 计划解析（project/global 双作用域）+ 生成文件渲染
│   ├── filesystem.py  # 路径安全、原子写入、事务回滚
│   ├── manifest.py    # 归属记录、用户修改检测、schema
│   └── backup.py      # --force 覆盖前备份
├── validators/        # 只负责"只读检查"，不写任何文件
│   ├── targets.py     # 唯一路径事实来源（project/global）+ 共享常量
│   ├── common.py      # 分级报告、Frontmatter 解析、共享原语
│   ├── skill_validator.py / markdown_validator.py / reference_validator.py
│   ├── adapter_validator.py / consistency_validator.py
├── config/
│   └── targets.json   # targets.py 的可校验机器可读导出（非第二事实来源）
├── templates/         # 新 Skill / 新规范模板
└── tests/             # 7 个测试文件，全部用临时目录
```

**分层纪律**：`validators/` 不 import `installer/` 的核心逻辑；两者共享 `targets.py` 的
路径常量与 `GENERATED_VERSION_TOKEN`。`config/targets.json` 与代码常量的一致性由
`adapter_validator.validate_config_code_consistency` 检查，改动代码常量后必须同步该 JSON，
否则 `check.py --all` 报"不一致文件"。

---

## 2. 命令契约

退出码统一：`0` = 无 ERROR；`1` = 存在 ERROR；`2` = 工具执行失败。

| 命令 | 作用 | 关键参数 |
| --- | --- | --- |
| `install.py` | 安装规范到项目或 home | `--tool`/`--all`、`--scope project\|global`、`--target`、`--home`、`--dry-run`、`--list`、`--force`、`--version` |
| `validate.py` | 检查仓库自身规范 | 无参=全量；`--skills`/`--core`/`--adapters`、`--strict` |
| `check.py` | 安装结果 / 仓库一致性 | `--target` + `--scope`；或 `--all`/`--skills`/`--adapters` |

`--strict` 的升级规则（WARNING → ERROR）在命令输出中逐条打印；不在名单里的 WARNING 保持原级。

---

## 3. 从其它工作目录调用

脚本用 `Path(__file__).resolve().parents[2]` 定位仓库根，并从 `<repo>/tooling` 载入模块，
**不依赖当前工作目录碰巧正确**。例如：

```bash
python /abs/path/ai-engineering-standards/tooling/scripts/install.py --all --target /abs/path/my-app
```

---

## 4. 测试

```bash
python -m unittest discover -s tooling/tests -v
python tooling/scripts/validate.py --strict
python tooling/scripts/check.py --all
```

测试全部使用 `tempfile.TemporaryDirectory`；global 作用域测试通过 `--home` 指向隔离目录，
**不覆盖当前仓库的真实配置、不触碰真实用户 home、不写入任何真实凭据**。
符号链接安全测试仅在 POSIX 上运行（Windows 创建符号链接需额外权限，自动跳过）。

---

## 5. 已知边界

- Frontmatter 是**受约束的 YAML 子集**解析器（零依赖），不是完整 YAML；不支持的语法明确报错，
  不宣称与官方 `skills-ref` 完全等价。
- 语义冲突**不做自动判定**，只扫描排他性措辞信号并标"需人工审查"。
- 不生成 Hooks / 自定义 Agent 配置（Phase 4 决策，避免虚构工具专有字段）。
- 静态检查通过 ≠ 真实 AI 客户端运行验证；后者需人工在对应工具里确认。
