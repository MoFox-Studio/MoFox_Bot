# JSON 解析统一化改进文档

## 改进目标
统一项目中所有 LLM 响应的 JSON 解析逻辑，使用 `json_repair` 库和统一的解析工具，简化代码并提高解析成功率。

## 创建的新工具模块

### `src/utils/json_parser.py`
提供统一的 JSON 解析功能：

#### 主要函数：
1. **`extract_and_parse_json(response, strict=False)`**
   - 从 LLM 响应中提取并解析 JSON
   - 自动处理 Markdown 代码块标记
   - 使用 json_repair 修复格式问题
   - 支持严格模式和容错模式

2. **`safe_parse_json(json_str, default=None)`**
   - 安全解析 JSON，失败时返回默认值

3. **`extract_json_field(response, field_name, default=None)`**
   - 从 LLM 响应中提取特定字段的值

#### 处理策略：
1. 清理 Markdown 代码块标记（```json 和 ```）
2. 提取 JSON 对象或数组（使用栈匹配算法）
3. 尝试直接解析
4. 如果失败，使用 json_repair 修复后解析
5. 容错模式下返回空字典或空列表

## 已修改的文件

### 1. `src/chat/memory_system/memory_query_planner.py` ✅
- 移除了自定义的 `_extract_json_payload` 方法
- 使用 `extract_and_parse_json` 替代原有的解析逻辑
- 简化了代码，提高了可维护性

**修改前：**
```python
payload = self._extract_json_payload(response)
if not payload:
    return self._default_plan(query_text)
try:
    data = orjson.loads(payload)
except orjson.JSONDecodeError as exc:
    ...
```

**修改后：**
```python
data = extract_and_parse_json(response, strict=False)
if not data or not isinstance(data, dict):
    return self._default_plan(query_text)
```

### 2. `src/chat/memory_system/memory_system.py` ✅
- 移除了自定义的 `_extract_json_payload` 方法
- 在 `_evaluate_information_value` 方法中使用统一解析工具
- 简化了错误处理逻辑

### 3. `src/chat/interest_system/bot_interest_manager.py` ✅
- 移除了自定义的 `_clean_llm_response` 方法
- 使用 `extract_and_parse_json` 解析兴趣标签数据
- 改进了错误处理和日志输出

### 4. `src/plugins/built_in/affinity_flow_chatter/chat_stream_impression_tool.py` ✅
- 将 `_clean_llm_json_response` 标记为已废弃
- 使用 `extract_and_parse_json` 解析聊天流印象数据
- 添加了类型检查和错误处理

## 待修改的文件

### 需要类似修改的其他文件：
1. `src/plugins/built_in/affinity_flow_chatter/proactive_thinking_executor.py`
   - 包含自定义的 JSON 清理逻辑
   
2. `src/plugins/built_in/affinity_flow_chatter/user_profile_tool.py`
   - 包含自定义的 JSON 清理逻辑

3. 其他包含自定义 JSON 解析逻辑的文件

## 改进效果

### 1. 代码简化
- 消除了重复的 JSON 提取和清理代码
- 减少了代码行数和维护成本
- 统一了错误处理模式

### 2. 解析成功率提升
- 使用 json_repair 自动修复常见的 JSON 格式问题
- 支持多种 JSON 包装格式（代码块、纯文本等）
- 更好的容错处理

### 3. 可维护性提升
- 集中管理 JSON 解析逻辑
- 易于添加新的解析策略
- 便于调试和日志记录

### 4. 一致性提升
- 所有 LLM 响应使用相同的解析流程
- 统一的日志输出格式
- 一致的错误处理

## 使用示例

### 基本用法：
```python
from src.utils.json_parser import extract_and_parse_json

# LLM 响应可能包含 Markdown 代码块或其他文本
llm_response = '```json\\n{"key": "value"}\\n```'

# 自动提取和解析
data = extract_and_parse_json(llm_response, strict=False)
# 返回: {'key': 'value'}

# 如果解析失败，返回空字典（非严格模式）
# 严格模式下返回 None
```

### 提取特定字段：
```python
from src.utils.json_parser import extract_json_field

llm_response = '{"score": 0.85, "reason": "Good quality"}'
score = extract_json_field(llm_response, "score", default=0.0)
# 返回: 0.85
```

## 测试建议

1. **单元测试**：
   - 测试各种 JSON 格式（带/不带代码块标记）
   - 测试格式错误的 JSON（验证 json_repair 的修复能力）
   - 测试嵌套 JSON 结构
   - 测试空响应和无效响应

2. **集成测试**：
   - 在实际 LLM 调用场景中测试
   - 验证不同模型的响应格式兼容性
   - 测试错误处理和日志输出

3. **性能测试**：
   - 测试大型 JSON 的解析性能
   - 验证缓存和优化策略

## 迁移指南

### 旧代码模式：
```python
# 旧的自定义解析逻辑
def _extract_json(response: str) -> str | None:
    stripped = response.strip()
    code_block_match = re.search(r"```(?:json)?\\s*(.*?)```", stripped, re.DOTALL)
    if code_block_match:
        return code_block_match.group(1)
    # ... 更多自定义逻辑
    
# 使用
payload = self._extract_json(response)
if payload:
    data = orjson.loads(payload)
```

### 新代码模式：
```python
# 使用统一工具
from src.utils.json_parser import extract_and_parse_json

# 直接解析
data = extract_and_parse_json(response, strict=False)
if data and isinstance(data, dict):
    # 使用数据
    pass
```

## 注意事项

1. **导入语句**：确保添加正确的导入
   ```python
   from src.utils.json_parser import extract_and_parse_json
   ```

2. **错误处理**：统一工具已包含错误处理，无需额外 try-except
   ```python
   # 不需要
   try:
       data = extract_and_parse_json(response)
   except Exception:
       ...
   
   # 应该
   data = extract_and_parse_json(response, strict=False)
   if not data:
       # 处理失败情况
       pass
   ```

3. **类型检查**：始终验证返回值类型
   ```python
   data = extract_and_parse_json(response)
   if isinstance(data, dict):
       # 处理字典
   elif isinstance(data, list):
       # 处理列表
   ```

## 后续工作

1. 完成剩余文件的迁移
2. 添加完整的单元测试
3. 更新相关文档
4. 考虑添加性能监控和统计

## 日期
2025年11月2日
