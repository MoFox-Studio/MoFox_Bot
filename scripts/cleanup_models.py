#!/usr/bin/env python3
"""清理 core/models.py，只保留模型定义"""

import os

# 文件路径
models_file = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),
    "src",
    "common",
    "database",
    "core",
    "models.py"
)

print(f"正在清理文件: {models_file}")

# 读取文件
with open(models_file, encoding="utf-8") as f:
    lines = f.readlines()

# 找到最后一个模型类的结束位置（MonthlyPlan的 __table_args__ 结束）
# 我们要保留到第593行（包含）
keep_lines = []
found_end = False

for i, line in enumerate(lines, 1):
    keep_lines.append(line)

    # 检查是否到达 MonthlyPlan 的 __table_args__ 结束
    if i > 580 and line.strip() == ")":
        # 再检查前一行是否有 Index 相关内容
        if "idx_monthlyplan" in "".join(lines[max(0, i-5):i]):
            print(f"找到模型定义结束位置: 第 {i} 行")
            found_end = True
            break

if not found_end:
    print("❌ 未找到模型定义结束标记")
    exit(1)

# 写回文件
with open(models_file, "w", encoding="utf-8") as f:
    f.writelines(keep_lines)

print("✅ 文件清理完成")
print(f"保留行数: {len(keep_lines)}")
print(f"原始行数: {len(lines)}")
print(f"删除行数: {len(lines) - len(keep_lines)}")
