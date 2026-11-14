"""ai-engineering-standards 安装器（Phase 5）。

分层：
- resolver   —— 计划解析（公共源 -> 目标清单，project/global 双作用域）
- filesystem —— 路径安全、原子写入、事务回滚
- backup     —— --force 覆盖前备份
- manifest   —— 文件归属记录、用户修改检测
- manager    —— 编排与摘要输出（install.py 的唯一后端）
"""
