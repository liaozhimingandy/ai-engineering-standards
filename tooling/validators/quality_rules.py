"""统一质量门禁规则（Phase 6）。

把「哪些 WARNING 在严格模式下升级为 ERROR」集中为**单一事实来源**，
供 ``validate.py``、``quality_gate.py``、``release_check.py`` 共用，避免三处
各自维护一套规则而漂移。本模块只含数据 + 纯函数，无 I/O、无子进程。

不重新实现任何 Phase 5 校验逻辑：这里只定义"严重级别提升"的策略。
"""

from __future__ import annotations

from . import common as C

# --strict 升级规则：仅把这些明确的"建议修复"提升为"必须修复"。
# 每项 = (消息子串, 升级后的说明)。不在此列的 WARNING 保持 WARNING。
# 规则内容与 Phase 5 的 validate.py 完全一致，迁移到此以保持单一来源。
STRICT_PROMOTIONS: tuple[tuple[str, str], ...] = (
    ("未引用 core/", "严格模式：standards 未引用 core/ 视为重复定义通用原则"),
    ("未引用 core/README.md", "严格模式：core 规范未引用 core/README.md"),
    ("core 规范未被任何索引/适配器/规范引用", "严格模式：新增 core 规范遗漏映射视为不合格"),
    ("description 过短", "严格模式：Skill 描述过短视为不合格"),
    ("description 未包含明确触发词", "严格模式：Skill 缺少触发词视为不合格"),
    ("标题层级从", "严格模式：标题层级跳跃视为不合格"),
    ("内容完全相同的重复文件", "严格模式：重复维护同一内容视为不合格"),
    ("非标准资源目录", "严格模式：非标准 Skill 资源目录视为不合格"),
    ("使用了 excludeAgent", "严格模式：跨版本未验证字段视为不合格"),
    ("excludeAgent", "严格模式：跨版本未验证字段视为不合格"),
)


def apply_strict(report: "C.Report") -> int:
    """把匹配的 WARNING 升级为 ERROR，返回升级条数（原地修改 report）。"""
    promoted = 0
    for issue in report.issues:
        if issue.level != C.WARNING:
            continue
        for needle, reason in STRICT_PROMOTIONS:
            if needle in issue.message:
                issue.level = C.ERROR
                issue.message = f"{issue.message} — {reason}"
                promoted += 1
                break
    return promoted


def describe_rules() -> tuple[str, ...]:
    """返回可读的规则列表，供 CLI 打印"本模式实际执行了哪些升级规则"。"""
    return tuple(f"含 {needle!r} -> ERROR" for needle, _ in STRICT_PROMOTIONS)
