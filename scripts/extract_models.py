#!/usr/bin/env python3
"""提取models.py中的模型定义"""

import re

# 读取原始文件
with open("src/common/database/sqlalchemy_models.py", encoding="utf-8") as f:
    content = f.read()

# 找到get_string_field函数的开始和结束
get_string_field_start = content.find("# MySQL兼容的字段类型辅助函数")
get_string_field_end = content.find("\n\nclass ChatStreams(Base):")
get_string_field = content[get_string_field_start:get_string_field_end]

# 找到第一个class定义开始
first_class_pos = content.find("class ChatStreams(Base):")

# 找到所有class定义，直到遇到非class的def
# 简单策略：找到所有以"class "开头且继承Base的类
classes_pattern = r"class \w+\(Base\):.*?(?=\nclass \w+\(Base\):|$)"
matches = list(re.finditer(classes_pattern, content[first_class_pos:], re.DOTALL))

if matches:
    # 取最后一个匹配的结束位置
    models_content = content[first_class_pos:first_class_pos + matches[-1].end()]
else:
    # 备用方案：从第一个class到文件的85%位置
    models_end = int(len(content) * 0.85)
    models_content = content[first_class_pos:models_end]

# 创建新文件内容
header = '''"""SQLAlchemy数据库模型定义

本文件只包含纯模型定义，使用SQLAlchemy 2.0的Mapped类型注解风格。
引擎和会话管理已移至core/engine.py和core/session.py。

所有模型使用统一的类型注解风格：
    field_name: Mapped[PyType] = mapped_column(Type, ...)

这样IDE/Pylance能正确推断实例属性类型。
"""

import datetime
import time

from sqlalchemy import Boolean, DateTime, Float, Index, Integer, String, Text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import Mapped, mapped_column

# 创建基类
Base = declarative_base()


'''

new_content = header + get_string_field + "\n\n" + models_content

# 写入新文件
with open("src/common/database/core/models.py", "w", encoding="utf-8") as f:
    f.write(new_content)

print("✅ Models file rewritten successfully")
print(f"File size: {len(new_content)} characters")
pattern = r"^class \w+\(Base\):"
model_count = len(re.findall(pattern, models_content, re.MULTILINE))
print(f"Number of model classes: {model_count}")
