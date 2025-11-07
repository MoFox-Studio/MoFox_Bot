# 记忆图系统设计文档大纲

**版本**: v0.1  
**创建日期**: 2025-11-05  
**状态**: 草案

---

## 1. 概述

### 1.1 背景与动机
- 现有SPO记忆系统的局限性
- 噪声干扰导致召回率低
- 需要更强的语义理解和关系推理能力

### 1.2 设计目标
- 采用知识图谱 + 语义向量的混合架构
- 支持复杂关系和多跳推理
- 保持轻量级部署，降低用户使用门槛
- 通过LLM工具调用实现自主记忆构建

### 1.3 核心概念
- **节点 (Node)**: 客观存在的事物（人、物、概念）
- **边 (Edge)**: 节点间的关系
- **记忆 (Memory)**: 由节点和边组成的子图
- **记忆层级**: 主体 → 记忆类型 → 主题 → 客体 → 延伸属性

---

## 2. 架构设计

### 2.1 整体架构图
```
┌─────────────────────────────────────────────────────┐
│              LLM 对话引擎                            │
│  ┌─────────────────────────────────────────┐        │
│  │      工具调用层 (Tool Calling)          │        │
│  │  - create_memory()                      │        │
│  │  - link_memories()                      │        │
│  │  - update_memory_importance()           │        │
│  └─────────────────────────────────────────┘        │
└──────────────────┬──────────────────────────────────┘
                   │
┌──────────────────▼──────────────────────────────────┐
│           记忆图管理层 (Memory Graph)                │
│  ┌──────────────┐  ┌──────────────┐                 │
│  │ Memory       │  │ Memory       │                 │
│  │ Extractor    │  │ Builder      │                 │
│  │ 记忆提取     │  │ 记忆构建     │                 │
│  └──────────────┘  └──────────────┘                 │
│  ┌──────────────┐  ┌──────────────┐                 │
│  │ Memory       │  │ Node         │                 │
│  │ Retriever    │  │ Merger       │                 │
│  │ 记忆检索     │  │ 节点去重     │                 │
│  └──────────────┘  └──────────────┘                 │
└──────────────────┬──────────────────────────────────┘
                   │
┌──────────────────▼──────────────────────────────────┐
│              存储层 (Storage)                        │
│  ┌──────────────┐  ┌──────────────┐                 │
│  │ Vector Store │  │ Graph Index  │                 │
│  │ (ChromaDB)   │  │ (NetworkX)   │                 │
│  │ 语义向量     │  │ 图结构       │                 │
│  └──────────────┘  └──────────────┘                 │
│  ┌──────────────────────────────┐                   │
│  │ Persistence (SQLite/JSON)    │                   │
│  │ 持久化                        │                   │
│  └──────────────────────────────┘                   │
└─────────────────────────────────────────────────────┘
```

### 2.2 模块职责

#### 2.2.1 工具调用层
- 提供给LLM的标准化工具接口
- 参数验证和类型转换
- 调用结果的格式化返回

#### 2.2.2 记忆管理层
- **Extractor**: 从工具参数中提取记忆元素
- **Builder**: 构建节点和边，建立图结构
- **Retriever**: 基于查询检索相关记忆
- **Node Merger**: 基于语义相似度合并重复节点

#### 2.2.3 存储层
- **Vector Store**: 存储节点的语义向量，支持相似度搜索
- **Graph Index**: 存储图的邻接表，支持快速遍历
- **Persistence**: 数据持久化到本地文件

---

## 3. 数据模型

### 3.1 节点类型 (NodeType)
```python
class NodeType(Enum):
    SUBJECT = "主体"      # 记忆的主语（我、小明、老师）
    TOPIC = "主题"        # 动作或状态（吃饭、情绪、学习）
    OBJECT = "客体"       # 宾语（白米饭、学校、书）
    ATTRIBUTE = "属性"    # 延伸属性（时间、地点、原因）
    VALUE = "值"          # 属性的具体值（2025-11-05、不开心）
```

### 3.2 记忆类型 (MemoryType)
```python
class MemoryType(Enum):
    EVENT = "事件"        # 有时间点的动作
    FACT = "事实"         # 相对稳定的状态
    RELATION = "关系"     # 人际关系
    OPINION = "观点"      # 主观评价
```

### 3.3 边类型 (EdgeType)
```python
class EdgeType(Enum):
    MEMORY_TYPE = "记忆类型"    # 主体 → 主题
    CORE_RELATION = "核心关系"  # 主题 → 客体（是/做/有）
    ATTRIBUTE = "属性关系"      # 任意节点 → 属性
    CAUSALITY = "因果关系"      # 记忆 → 记忆
    REFERENCE = "引用关系"      # 记忆 → 记忆（转述）
```

### 3.4 数据类定义
```python
@dataclass
class MemoryNode:
    id: str                      # UUID
    content: str                 # 节点内容
    node_type: NodeType          # 节点类型
    embedding: Optional[np.ndarray]  # 语义向量（仅主题/客体需要）
    metadata: Dict[str, Any]     # 扩展元数据
    created_at: datetime
    
@dataclass
class MemoryEdge:
    id: str
    source_id: str               # 源节点ID
    target_id: str               # 目标节点ID（或目标记忆ID）
    relation: str                # 关系名称（is/做/时间/因为）
    edge_type: EdgeType          # 边类型
    importance: float            # 0-1
    metadata: Dict[str, Any]
    created_at: datetime
    
@dataclass
class Memory:
    id: str                      # 记忆ID
    subject_id: str              # 主体节点ID
    memory_type: MemoryType      # 记忆类型
    nodes: List[MemoryNode]      # 该记忆包含的所有节点
    edges: List[MemoryEdge]      # 该记忆包含的所有边
    importance: float            # 整体重要性 0-1
    created_at: datetime
    last_accessed: datetime      # 最后访问时间
    access_count: int            # 访问次数
    decay_factor: float          # 衰减因子（随时间变化）
```

---

## 4. 工具调用接口设计 ⭐

### 4.1 设计原则
1. **参数简洁明了**：减少LLM的理解负担
2. **结构化清晰**：使用嵌套对象表达层级关系
3. **类型明确**：提供枚举值，避免自由文本混乱
4. **容错性强**：可选参数，支持简单和复杂场景
5. **可组合性**：支持一次调用构建完整记忆

### 4.2 核心工具：create_memory()

#### 4.2.1 基础用法
```json
{
  "name": "create_memory",
  "description": "创建一条新的记忆。记忆由主体、类型、主题、客体等组成，可以包含时间、地点等属性。",
  "parameters": {
    "subject": {
      "type": "string",
      "description": "记忆的主体（谁）。例如：'我'、'小明'、'用户'",
      "required": true
    },
    "memory_type": {
      "type": "string",
      "enum": ["事件", "事实", "关系", "观点"],
      "description": "记忆类型。事件=有时间的动作；事实=稳定状态；关系=人际关系；观点=主观评价",
      "required": true
    },
    "topic": {
      "type": "string",
      "description": "记忆的主题（做什么/是什么状态）。例如：'吃饭'、'情绪'、'学习'",
      "required": true
    },
    "object": {
      "type": "string",
      "description": "记忆的客体（什么东西/什么状态）。例如：'白米饭'、'不开心'、'数学'",
      "required": false
    },
    "attributes": {
      "type": "object",
      "description": "额外的属性信息，键值对形式",
      "properties": {
        "时间": {"type": "string", "description": "时间（会自动标准化）"},
        "地点": {"type": "string"},
        "原因": {"type": "string"},
        "方式": {"type": "string"}
      },
      "required": false
    },
    "importance": {
      "type": "number",
      "description": "重要性等级，0-1之间。0.3=日常琐事，0.5=一般事件，0.8=重要事件，1.0=关键记忆",
      "default": 0.5,
      "required": false
    }
  }
}
```

#### 4.2.2 调用示例

**示例1：简单事件**
```json
// 用户说："我今天吃了白米饭"
{
  "subject": "我",
  "memory_type": "事件",
  "topic": "吃饭",
  "object": "白米饭",
  "attributes": {
    "时间": "今天"
  },
  "importance": 0.3
}
```

**示例2：事实状态**
```json
// 用户说："小明喜欢打篮球"
{
  "subject": "小明",
  "memory_type": "事实",
  "topic": "喜好",
  "object": "打篮球",
  "importance": 0.5
}
```

**示例3：复杂观点**
```json
// 用户说："我觉得小明昨天说的话是在撒谎"
{
  "subject": "我",
  "memory_type": "观点",
  "topic": "认为撒谎",
  "object": "小明的陈述",
  "attributes": {
    "时间": "昨天",
    "基于": "他的话前后矛盾"
  },
  "importance": 0.7
}
```

### 4.3 关联工具：link_memories()

用于建立记忆之间的因果/引用关系。

```json
{
  "name": "link_memories",
  "description": "在两条已存在的记忆之间建立关联关系（因果、引用等）",
  "parameters": {
    "source_memory_description": {
      "type": "string",
      "description": "源记忆的描述（用于查找）。例如：'我今天不开心'",
      "required": true
    },
    "target_memory_description": {
      "type": "string", 
      "description": "目标记忆的描述（用于查找）。例如：'我摔东西'",
      "required": true
    },
    "relation_type": {
      "type": "string",
      "enum": ["因为", "所以", "导致", "引用", "基于", "相关"],
      "description": "关系类型",
      "required": true
    },
    "importance": {
      "type": "number",
      "default": 0.6
    }
  }
}
```

#### 4.3.1 调用示例
```json
// 用户说："我今天不开心，所以摔了东西"
// 第一步：创建两条记忆
create_memory({subject: "我", memory_type: "事实", topic: "情绪", object: "不开心", attributes: {时间: "今天"}})
create_memory({subject: "我", memory_type: "事件", topic: "摔东西", attributes: {时间: "今天"}})

// 第二步：建立因果关系
link_memories({
  source_memory_description: "我今天不开心",
  target_memory_description: "我摔东西", 
  relation_type: "导致"
})
```

### 4.4 查询工具：search_memories()

```json
{
  "name": "search_memories",
  "description": "根据语义搜索相关记忆，用于回答问题或回忆往事",
  "parameters": {
    "query": {
      "type": "string",
      "description": "搜索查询，可以是问题或关键词。例如：'我什么时候吃过饭？'",
      "required": true
    },
    "memory_types": {
      "type": "array",
      "items": {"enum": ["事件", "事实", "关系", "观点"]},
      "description": "限定搜索的记忆类型",
      "required": false
    },
    "time_range": {
      "type": "object",
      "properties": {
        "start": {"type": "string"},
        "end": {"type": "string"}
      },
      "description": "时间范围过滤",
      "required": false
    },
    "max_results": {
      "type": "integer",
      "default": 10,
      "description": "最多返回多少条记忆"
    },
    "expand_depth": {
      "type": "integer",
      "default": 1,
      "description": "关联扩展深度（0=仅直接匹配，1=扩展1跳，2=扩展2跳）"
    }
  }
}
```

### 4.5 Prompt 设计要点

#### 4.5.1 系统提示词模板
```
你是一个具有记忆能力的AI助手。在对话中，你可以使用以下工具来管理记忆：

1. **create_memory**: 创建新记忆
   - 当用户告诉你事实、发生的事情、他的观点时使用
   - 必须明确指定：主体（谁）、类型（什么类型的记忆）、主题（做什么）
   - 可选指定：客体（什么东西）、属性（时间/地点/原因等）
   - 重要性判断：日常琐事0.3，一般事件0.5，重要事件0.8，关键记忆1.0

2. **link_memories**: 关联记忆
   - 当用户表达因果关系时使用（因为、所以、导致）
   - 当用户引用之前的事情时使用（基于、关于）

3. **search_memories**: 搜索记忆
   - 当用户询问过去的事情时使用
   - 当需要回忆相关信息来回答问题时使用

**记忆构建原则**：
- 时间必须尽可能具体（将"今天"转为实际日期）
- 主体要明确（区分"我"="用户"和"你"="AI助手"）
- 复杂句子拆分成多条记忆+关联关系
- 转述的内容要明确来源（使用引用关系）
- 重要性根据内容判断，不是所有事情都重要

**示例**：
用户："我昨天去超市买了苹果和香蕉"
→ create_memory({subject:"我", memory_type:"事件", topic:"购物", object:"水果", attributes:{时间:"2025-11-04", 地点:"超市", 物品:"苹果,香蕉"}, importance:0.4})

用户："小明是我的好朋友"
→ create_memory({subject:"我", memory_type:"关系", topic:"朋友关系", object:"小明", attributes:{程度:"好朋友"}, importance:0.7})

用户："我很讨厌下雨天，因为会影响我的心情"
→ create_memory({subject:"我", memory_type:"观点", topic:"讨厌", object:"下雨天", importance:0.6})
→ create_memory({subject:"下雨天", memory_type:"事实", topic:"影响", object:"我的心情", importance:0.5})
→ link_memories({source:"下雨天影响心情", target:"我讨厌下雨天", relation_type:"因为"})
```

#### 4.5.2 Few-shot 示例集
```markdown
## 工具调用训练示例

### 示例1：基础事件
**对话**：
用户："我今天早上8点吃了面包"
助手：好的，我记住了！[调用工具]

**工具调用**：
create_memory({
  subject: "我",
  memory_type: "事件", 
  topic: "吃早餐",
  object: "面包",
  attributes: {
    时间: "2025-11-05 08:00",
    餐次: "早餐"
  },
  importance: 0.3
})

---

### 示例2：复杂因果
**对话**：
用户："昨晚我失眠了，所以今天很困，没心情工作"
助手：理解你的状态了。[调用工具]

**工具调用**：
// 记忆1：失眠事件
create_memory({
  subject: "我",
  memory_type: "事件",
  topic: "失眠",
  attributes: {时间: "2025-11-04 夜间"},
  importance: 0.6
})

// 记忆2：疲惫状态
create_memory({
  subject: "我", 
  memory_type: "事实",
  topic: "身体状态",
  object: "很困",
  attributes: {时间: "2025-11-05"},
  importance: 0.5
})

// 记忆3：工作状态
create_memory({
  subject: "我",
  memory_type: "事实", 
  topic: "工作状态",
  object: "没心情",
  attributes: {时间: "2025-11-05"},
  importance: 0.5
})

// 建立因果链
link_memories({
  source_memory_description: "我失眠",
  target_memory_description: "我很困",
  relation_type: "导致"
})

link_memories({
  source_memory_description: "我很困", 
  target_memory_description: "我没心情工作",
  relation_type: "导致"
})

---

### 示例3：转述/观点
**对话**：
用户："小明告诉我他考了满分，但我觉得他在吹牛"
助手：明白了，记录下来。[调用工具]

**工具调用**：
// 记忆1：小明的陈述（转述）
create_memory({
  subject: "小明",
  memory_type: "事件",
  topic: "考试",
  object: "满分",
  attributes: {来源: "小明本人陈述"},
  importance: 0.4
})

// 记忆2：我的观点
create_memory({
  subject: "我", 
  memory_type: "观点",
  topic: "怀疑",
  object: "小明考满分的真实性",
  attributes: {评价: "可能在吹牛"},
  importance: 0.5
})

// 建立引用关系
link_memories({
  source_memory_description: "小明考试满分",
  target_memory_description: "我怀疑真实性", 
  relation_type: "基于"
})
```

---

## 5. 核心算法

### 5.1 节点去重算法
```
输入：新节点 N, 现有节点集合 NODES
输出：合并后的节点ID

1. 计算 N 的 embedding
2. 在 ChromaDB 中搜索 top-k 相似节点 (k=5)
3. 对于每个候选节点 C:
   a. 计算语义相似度 sim = cosine_similarity(N.embedding, C.embedding)
   b. 如果 sim > 0.95: 直接合并
   c. 如果 0.85 < sim <= 0.95:
      - 检查节点类型是否相同
      - 检查上下文是否匹配（邻接节点相似度）
      - 如果满足，则合并
4. 如果没有合并，创建新节点
```

### 5.2 记忆检索算法
```
输入：查询 Q, 扩展深度 depth
输出：相关记忆列表

阶段1：向量检索（初筛）
1. 将查询 Q 转为 embedding
2. 在 ChromaDB 中检索 top-50 相似节点
3. 获取这些节点所属的记忆 M_initial

阶段2：图扩展（关联）
1. 以 M_initial 中的节点为起点
2. 执行 BFS，深度限制为 depth:
   a. 遍历邻接边，收集相邻节点
   b. 记录遍历路径和距离
   c. 获取新节点所属的记忆 M_expanded

阶段3：评分排序
1. 对于每条记忆 M，计算综合分数:
   score = α * semantic_sim        # 语义相似度
         + β * importance           # 重要性
         + γ * (1 / graph_distance) # 图距离
         + δ * time_decay          # 时间衰减
         + ε * access_frequency    # 访问频率
   
2. 按分数降序排序
3. 返回 top-N 记忆

参数建议：α=0.4, β=0.2, γ=0.2, δ=0.1, ε=0.1
```

### 5.3 时间衰减算法
```python
def calculate_decay(memory: Memory, current_time: datetime) -> float:
    """
    计算记忆的时间衰减因子
    
    衰减公式：decay = base_importance * exp(-λ * days) * (1 + log(1 + access_count))
    - base_importance: 初始重要性
    - λ: 衰减率（可根据记忆类型调整）
    - days: 距离创建的天数
    - access_count: 访问次数（访问越多越不容易遗忘）
    """
    days_passed = (current_time - memory.created_at).days
    
    # 不同类型的衰减率
    decay_rate_map = {
        MemoryType.EVENT: 0.05,    # 事件衰减较快
        MemoryType.FACT: 0.01,     # 事实衰减慢
        MemoryType.RELATION: 0.005, # 关系衰减很慢
        MemoryType.OPINION: 0.03   # 观点中等衰减
    }
    
    λ = decay_rate_map[memory.memory_type]
    time_factor = math.exp(-λ * days_passed)
    access_bonus = 1 + math.log(1 + memory.access_count)
    
    return memory.importance * time_factor * access_bonus
```

---

## 6. 技术栈与依赖

### 6.1 核心依赖
```toml
[tool.poetry.dependencies]
# 已有依赖
chromadb = "^0.4.0"           # 向量数据库
sentence-transformers = "*"    # 语义向量

# 新增依赖
networkx = "^3.2"             # 图算法库（纯Python，轻量级）
pydantic = "^2.0"             # 数据验证
```

### 6.2 可选增强
- `python-igraph`: 更高性能的图计算（可选，需要C依赖）
- `graphviz`: 记忆可视化（可选）

---

## 7. 文件结构

```
src/memory_graph/
├── __init__.py
├── models.py                  # 数据模型定义
├── config.py                  # 配置管理
├── storage/
│   ├── __init__.py
│   ├── vector_store.py       # ChromaDB 封装
│   ├── graph_store.py        # NetworkX 图索引
│   └── persistence.py        # 持久化管理
├── core/
│   ├── __init__.py
│   ├── extractor.py          # 从工具参数提取记忆元素
│   ├── builder.py            # 构建记忆图
│   ├── retriever.py          # 检索记忆
│   └── node_merger.py        # 节点去重合并
├── tools/
│   ├── __init__.py
│   ├── memory_tools.py       # LLM 工具接口定义
│   └── tool_executor.py      # 工具执行器
├── utils/
│   ├── __init__.py
│   ├── time_parser.py        # 时间标准化
│   ├── embeddings.py         # 语义向量生成
│   └── graph_viz.py          # 图可视化
└── algorithms/
    ├── __init__.py
    ├── decay.py              # 时间衰减
    ├── scoring.py            # 评分算法
    └── reasoning.py          # 推理算法（Phase 4）

tests/memory_graph/
├── __init__.py
├── test_models.py
├── test_storage.py
├── test_builder.py
├── test_retriever.py
├── test_tools.py
└── fixtures/
    └── sample_memories.json

docs/memory_graph/
├── design_outline.md         # 本文档
├── api_reference.md          # API 详细文档（待编写）
├── tool_calling_guide.md     # LLM 工具调用指南（待编写）
└── examples/
    ├── basic_usage.md
    └── advanced_scenarios.md
```

---

## 8. 开发计划

### Phase 1: 基础架构 (Week 1-2)
- [ ] 数据模型定义 (`models.py`)
- [ ] 存储层实现 (`storage/`)
- [ ] 节点去重逻辑 (`core/node_merger.py`)
- [ ] 单元测试

**验收**：可以手动创建、保存、加载记忆图

### Phase 2: 工具接口 (Week 3-4)
- [ ] LLM工具定义 (`tools/memory_tools.py`)
- [ ] 参数提取器 (`core/extractor.py`)
- [ ] 记忆构建器 (`core/builder.py`)
- [ ] 集成测试

**验收**：LLM可以通过工具调用成功创建记忆

### Phase 3: 检索系统 (Week 5-6)
- [ ] 向量检索 (`core/retriever.py` - vector search)
- [ ] 图遍历检索 (`core/retriever.py` - graph expansion)
- [ ] 评分排序 (`algorithms/scoring.py`)
- [ ] 性能测试

**验收**：查询响应时间 < 2秒，召回率 > 现有系统

### Phase 4: 高级特性 (Week 7-8)
- [ ] 时间衰减 (`algorithms/decay.py`)
- [ ] 记忆推理 (`algorithms/reasoning.py`)
- [ ] 可视化工具 (`utils/graph_viz.py`)
- [ ] 完整集成测试

**验收**：所有功能正常，文档完整

### Phase 5: 集成与优化 (Week 9-10)
- [ ] 与现有系统集成
- [ ] 性能优化
- [ ] 用户文档
- [ ] 发布到主分支

---

## 9. 待解决问题

### 9.1 技术问题
1. **节点去重阈值**：如何自适应调整相似度阈值？
2. **图遍历深度**：如何动态决定最优扩展深度？
3. **内存管理**：大规模图的内存占用优化
4. **并发访问**：多线程环境下的数据一致性

### 9.2 设计问题
1. **记忆冲突**：如何处理矛盾的记忆？（今天说喜欢，昨天说讨厌）
2. **隐私保护**：敏感信息的标记和处理
3. **记忆遗忘**：何时彻底删除低价值记忆？
4. **跨对话记忆**：如何在多轮对话中保持记忆一致性？

### 9.3 用户体验
1. **调试可视化**：如何让用户理解记忆的构建过程？
2. **记忆纠错**：用户如何修正错误的记忆？
3. **性能反馈**：如何向用户展示记忆系统的运作状态？

---

## 10. 性能指标

### 10.1 功能指标
- 记忆构建成功率 > 95%
- 节点去重准确率 > 90%
- 记忆召回相关性 > 80%

### 10.2 性能指标
- 单条记忆创建时间 < 500ms
- 检索响应时间 < 2s (depth=1), < 5s (depth=2)
- 存储空间：平均每条记忆 < 10KB

### 10.3 可用性指标
- LLM工具调用成功率 > 90%
- 系统启动时间 < 5s
- 内存占用 < 500MB (1万条记忆)

---

## 11. 参考资料

### 11.1 理论基础
- Knowledge Graph Construction
- Semantic Memory Networks
- Graph Neural Networks for Reasoning

### 11.2 相关项目
- LangChain Memory Systems
- MemGPT
- GraphRAG (Microsoft)

### 11.3 论文
- "Building Dynamic Knowledge Graphs from Text using Machine Learning"
- "Graph Neural Networks for Natural Language Processing"

---

## 附录 A: 工具定义完整JSON Schema

```json
{
  "tools": [
    {
      "type": "function",
      "function": {
        "name": "create_memory",
        "description": "创建一条新的记忆...",
        "parameters": {
          "type": "object",
          "properties": {
            "subject": {
              "type": "string",
              "description": "记忆的主体（谁）"
            },
            "memory_type": {
              "type": "string",
              "enum": ["事件", "事实", "关系", "观点"]
            },
            "topic": {
              "type": "string", 
              "description": "记忆的主题"
            },
            "object": {
              "type": "string",
              "description": "记忆的客体"
            },
            "attributes": {
              "type": "object",
              "additionalProperties": {"type": "string"}
            },
            "importance": {
              "type": "number",
              "minimum": 0,
              "maximum": 1,
              "default": 0.5
            }
          },
          "required": ["subject", "memory_type", "topic"]
        }
      }
    },
    {
      "type": "function",
      "function": {
        "name": "link_memories",
        "description": "在两条记忆之间建立关联...",
        "parameters": {
          "type": "object",
          "properties": {
            "source_memory_description": {"type": "string"},
            "target_memory_description": {"type": "string"},
            "relation_type": {
              "type": "string",
              "enum": ["因为", "所以", "导致", "引用", "基于", "相关"]
            },
            "importance": {
              "type": "number",
              "default": 0.6
            }
          },
          "required": ["source_memory_description", "target_memory_description", "relation_type"]
        }
      }
    },
    {
      "type": "function",
      "function": {
        "name": "search_memories",
        "description": "搜索相关记忆...",
        "parameters": {
          "type": "object",
          "properties": {
            "query": {"type": "string"},
            "memory_types": {
              "type": "array",
              "items": {"enum": ["事件", "事实", "关系", "观点"]}
            },
            "time_range": {
              "type": "object",
              "properties": {
                "start": {"type": "string"},
                "end": {"type": "string"}
              }
            },
            "max_results": {"type": "integer", "default": 10},
            "expand_depth": {"type": "integer", "default": 1}
          },
          "required": ["query"]
        }
      }
    }
  ]
}
```

---

## 12. 记忆系统运作模式 ⭐

### 12.1 记忆生命周期完整流程

```
┌─────────────────────────────────────────────────────────────┐
│                    记忆生命周期                              │
└─────────────────────────────────────────────────────────────┘

[1] 记忆构建 (实时)
    用户消息 → LLM处理 → 回复 + create_memory工具调用
                           ↓
                    临时记忆池 (Staging Area)
                    - 标记为"未整理"状态
                    - 保存原始结构
                    - 等待定期整理

[2] 记忆检索 (实时)
    用户查询 → 检索策略 → 召回记忆 → 返回结果
                ↓
          两种模式可选:
          A) 继续使用现有小模型查询规划器
          B) 使用图遍历增强检索

[3] 记忆整理 (定期后台)
    定时任务 (每日/每周)
        ↓
    [整理器] 处理"未整理"记忆:
        - 节点去重合并
        - 发现隐含关联
        - 时间衰减更新
        - 冲突检测修复
        ↓
    标记为"已整理"状态
```

### 12.2 记忆构建：工具调用 + 临时池

#### 12.2.1 设计思路
**问题**：如果每次构建记忆都立即执行去重、合并、关联，会导致：
- 对话响应变慢（需要等待图操作完成）
- 重复计算（短时间内多次相似操作）
- 无法批量优化（一条条处理效率低）

**解决方案**：两阶段处理
```python
# 阶段1：快速入库（对话中）
create_memory() → 保存到临时池 
    - 状态: "staged"
    - 仅创建基础节点和边
    - 不执行去重合并
    - 响应时间: < 100ms

# 阶段2：批量整理（后台）
定时任务(每6小时) → 整理器处理临时池
    - 节点语义去重
    - 记忆关联发现
    - 图结构优化
    - 状态更新: "staged" → "consolidated"
```

#### 12.2.2 临时池数据结构
```python
@dataclass
class StagedMemory:
    """临时记忆（未整理状态）"""
    memory: Memory              # 原始记忆对象
    status: MemoryStatus        # staged/consolidated/archived
    created_at: datetime
    consolidated_at: Optional[datetime]  # 整理时间
    merge_history: List[str]    # 被合并的节点ID列表
```

#### 12.2.3 工具调用流程优化
```python
async def tool_create_memory(
    subject: str,
    memory_type: str,
    topic: str,
    object: Optional[str] = None,
    attributes: Optional[Dict[str, str]] = None,
    importance: float = 0.5
) -> Dict[str, Any]:
    """LLM工具：创建记忆（快速入库）"""
    
    # 1. 时间标准化（相对→绝对）
    if attributes and "时间" in attributes:
        attributes["时间"] = normalize_time(attributes["时间"])
    
    # 2. 构建基础记忆结构
    memory = await builder.build_simple_memory(
        subject=subject,
        memory_type=MemoryType(memory_type),
        topic=topic,
        object=object,
        attributes=attributes,
        importance=importance
    )
    
    # 3. 快速入库到临时池（不执行去重）
    memory_id = await storage.save_staged_memory(memory)
    
    # 4. 异步触发embedding计算（不阻塞）
    asyncio.create_task(compute_embeddings_async(memory_id))
    
    return {
        "success": True,
        "memory_id": memory_id,
        "status": "staged",
        "message": "记忆已保存，将在后台整理"
    }
```

### 12.3 记忆检索：双模式策略

#### 12.3.1 方案对比

| 维度 | 方案A：沿用小模型查询规划 | 方案B：图遍历增强 | 推荐方案 |
|------|-------------------------|------------------|----------|
| **实现复杂度** | 低（已有代码） | 中（需新增图遍历） | **混合使用** |
| **召回准确性** | 中（依赖向量相似度） | 高（利用图关系） | 图遍历更准 |
| **响应速度** | 快（纯向量检索） | 稍慢（图遍历开销） | 分场景选择 |
| **上下文理解** | 一般 | 强（关联记忆） | 图遍历更强 |
| **资源占用** | 低 | 中 | 可接受 |

#### 12.3.2 推荐方案：混合检索策略

```python
class HybridMemoryRetriever:
    """混合检索器：向量 + 图遍历"""
    
    async def retrieve_memories(
        self,
        query: str,
        context: Dict[str, Any],
        mode: str = "auto"  # auto/fast/deep
    ) -> List[Memory]:
        """
        检索策略：
        - fast: 仅向量检索（现有方案A）
        - deep: 向量 + 图遍历（新方案B）
        - auto: 根据查询复杂度自动选择
        """
        
        # 1. 使用现有的小模型查询规划器
        query_plan = await self.query_planner.plan_query(query, context)
        
        # 2. 向量初筛（沿用现有逻辑）
        initial_results = await self._vector_search(
            query_plan.semantic_query,
            filters=self._build_filters(query_plan),
            limit=50
        )
        
        # 3. 判断是否需要图扩展
        need_expansion = self._should_expand(query, query_plan, mode)
        
        if not need_expansion:
            # 快速模式：直接返回向量结果
            return self._rerank_and_return(initial_results, limit=10)
        
        # 4. 图遍历扩展（新增）
        expanded_results = await self._graph_expansion(
            seed_memories=initial_results[:10],
            depth=query_plan.expand_depth or 1,
            relation_types=self._infer_relation_types(query_plan)
        )
        
        # 5. 综合评分（向量 + 图距离 + 重要性 + 时间）
        final_results = self._hybrid_rerank(
            initial_results + expanded_results,
            query_embedding=...,
            limit=10
        )
        
        return final_results
    
    def _should_expand(
        self, 
        query: str, 
        query_plan: MemoryQueryPlan,
        mode: str
    ) -> bool:
        """判断是否需要图扩展"""
        if mode == "fast":
            return False
        if mode == "deep":
            return True
        
        # auto模式：启发式判断
        # 1. 查询包含因果词（因为、所以、导致）→ 需要扩展
        causality_keywords = ["因为", "所以", "为什么", "原因", "导致"]
        if any(kw in query for kw in causality_keywords):
            return True
        
        # 2. 查询包含关系词（和...有关、关于）→ 需要扩展
        relation_keywords = ["有关", "关于", "相关", "联系"]
        if any(kw in query for kw in relation_keywords):
            return True
        
        # 3. 向量初筛结果不够好（top-1相似度 < 0.7）→ 需要扩展
        if initial_results and initial_results[0][1] < 0.7:
            return True
        
        return False
```

#### 12.3.3 图遍历算法

```python
async def _graph_expansion(
    self,
    seed_memories: List[Memory],
    depth: int = 1,
    relation_types: Optional[List[str]] = None
) -> List[Memory]:
    """
    从种子记忆出发，沿图扩展
    
    算法：广度优先搜索（BFS）
    """
    visited = set()
    expanded = []
    queue = [(mem, 0) for mem in seed_memories]  # (memory, current_depth)
    
    while queue:
        current_memory, current_depth = queue.pop(0)
        
        if current_memory.id in visited:
            continue
        visited.add(current_memory.id)
        
        if current_depth > 0:  # 不包括种子本身
            expanded.append(current_memory)
        
        if current_depth >= depth:
            continue
        
        # 查找相邻记忆
        neighbors = await self._find_neighbors(
            current_memory,
            relation_types=relation_types
        )
        
        for neighbor in neighbors:
            if neighbor.id not in visited:
                queue.append((neighbor, current_depth + 1))
    
    return expanded

async def _find_neighbors(
    self,
    memory: Memory,
    relation_types: Optional[List[str]] = None
) -> List[Memory]:
    """查找一条记忆的相邻记忆"""
    
    neighbor_memory_ids = set()
    
    # 1. 从记忆的节点出发，查找连接的边
    for node in memory.nodes:
        edges = await self.graph_store.get_edges_from_node(node.id)
        
        for edge in edges:
            # 过滤边类型
            if relation_types and edge.relation not in relation_types:
                continue
            
            # 获取目标节点所属的记忆
            target_memory_id = await self.graph_store.get_memory_by_node(
                edge.target_id
            )
            if target_memory_id:
                neighbor_memory_ids.add(target_memory_id)
    
    # 2. 加载相邻记忆
    neighbors = []
    for mem_id in neighbor_memory_ids:
        mem = await self.storage.load_memory(mem_id)
        if mem:
            neighbors.append(mem)
    
    return neighbors
```

#### 12.3.4 查询规划器增强

保留现有的`MemoryQueryPlanner`，但增加图相关字段：

```python
@dataclass
class MemoryQueryPlan:
    """查询规划结果（增强版）"""
    # === 现有字段 ===
    semantic_query: str
    memory_types: List[MemoryType]
    subject_includes: Optional[List[str]]
    # ...
    
    # === 新增字段：图检索 ===
    expand_depth: int = 0           # 图扩展深度 (0=不扩展)
    relation_filters: List[str] = None  # 关系过滤 ["因为", "导致"]
    search_mode: str = "auto"       # auto/fast/deep
```

小模型Prompt增强：
```
## 新增：图检索参数
如果用户查询涉及因果关系、关联关系，建议：
- expand_depth: 1-2（扩展深度）
- relation_filters: ["因为", "导致", "相关"] 等
- search_mode: "deep"

示例：
用户："我为什么今天不开心？"
→ expand_depth: 1, relation_filters: ["因为", "导致"]

用户："和小明有关的记忆"
→ expand_depth: 1, relation_filters: null (不限制类型)
```

### 12.4 记忆整理：定时任务

#### 12.4.1 整理器架构

```python
class MemoryConsolidator:
    """记忆整理器（后台任务）"""
    
    def __init__(
        self,
        storage: MemoryGraphStore,
        node_merger: NodeMerger,
        relation_discoverer: RelationDiscoverer
    ):
        self.storage = storage
        self.node_merger = node_merger
        self.relation_discoverer = relation_discoverer
        self.logger = get_logger(__name__)
    
    async def run_consolidation(self, batch_size: int = 100):
        """执行一轮整理任务"""
        self.logger.info("=== 开始记忆整理任务 ===")
        
        # 1. 获取未整理的记忆
        staged_memories = await self.storage.get_staged_memories(
            limit=batch_size
        )
        
        if not staged_memories:
            self.logger.info("没有需要整理的记忆")
            return
        
        self.logger.info(f"本轮整理 {len(staged_memories)} 条记忆")
        
        # 2. 节点去重合并
        merge_stats = await self._merge_duplicate_nodes(staged_memories)
        self.logger.info(f"节点合并: {merge_stats}")
        
        # 3. 发现隐含关联
        link_stats = await self._discover_relations(staged_memories)
        self.logger.info(f"关联发现: {link_stats}")
        
        # 4. 时间衰减更新
        decay_stats = await self._update_decay_factors(staged_memories)
        self.logger.info(f"衰减更新: {decay_stats}")
        
        # 5. 冲突检测
        conflict_stats = await self._detect_conflicts(staged_memories)
        self.logger.info(f"冲突检测: {conflict_stats}")
        
        # 6. 更新状态为"已整理"
        for mem in staged_memories:
            await self.storage.mark_as_consolidated(mem.memory.id)
        
        self.logger.info("=== 记忆整理任务完成 ===")
```

#### 12.4.2 节点去重合并

```python
async def _merge_duplicate_nodes(
    self,
    memories: List[StagedMemory]
) -> Dict[str, int]:
    """批量处理节点去重"""
    
    merged_count = 0
    checked_count = 0
    
    # 收集所有新节点
    new_nodes = []
    for staged_mem in memories:
        for node in staged_mem.memory.nodes:
            if node.node_type in [NodeType.TOPIC, NodeType.OBJECT]:
                # 只对主题和客体节点去重（主体通常是唯一的）
                new_nodes.append(node)
    
    # 批量计算embedding（如果还没有）
    await self._ensure_embeddings(new_nodes)
    
    # 对每个新节点，查找相似节点
    for node in new_nodes:
        checked_count += 1
        
        similar_nodes = await self.node_merger.find_similar_nodes(
            node,
            threshold=0.85,
            limit=5
        )
        
        if similar_nodes:
            # 找到相似节点，执行合并
            target_node = similar_nodes[0]  # 相似度最高的
            await self.node_merger.merge_nodes(
                source=node,
                target=target_node
            )
            merged_count += 1
            
            self.logger.debug(
                f"节点合并: '{node.content}' → '{target_node.content}' "
                f"(相似度: {similar_nodes[0].similarity:.3f})"
            )
    
    return {
        "checked": checked_count,
        "merged": merged_count
    }
```

#### 12.4.3 关联发现

```python
class RelationDiscoverer:
    """隐含关系发现器"""
    
    async def discover_relations(
        self,
        memories: List[Memory]
    ) -> List[MemoryEdge]:
        """
        发现记忆间的隐含关系
        
        策略：
        1. 时间相近 + 语义相关 → 可能有因果关系
        2. 共享相同节点 → 可能有关联关系
        3. 矛盾检测 → 标记冲突关系
        """
        discovered_edges = []
        
        # 策略1：时间相近的记忆
        temporal_pairs = self._find_temporal_neighbors(memories)
        for mem_a, mem_b in temporal_pairs:
            # 使用LLM判断是否有因果关系
            has_causality = await self._check_causality_with_llm(
                mem_a, mem_b
            )
            if has_causality:
                edge = MemoryEdge(
                    source_id=mem_a.id,
                    target_id=mem_b.id,
                    relation="推测因果",
                    edge_type=EdgeType.CAUSALITY,
                    importance=0.5,  # 推测的关系重要性较低
                    metadata={"discovered": True, "confidence": 0.7}
                )
                discovered_edges.append(edge)
        
        # 策略2：共享节点的记忆
        shared_node_groups = self._group_by_shared_nodes(memories)
        for group in shared_node_groups:
            if len(group) >= 2:
                # 建立"相关"关系
                for i in range(len(group) - 1):
                    edge = MemoryEdge(
                        source_id=group[i].id,
                        target_id=group[i+1].id,
                        relation="相关",
                        edge_type=EdgeType.REFERENCE,
                        importance=0.4
                    )
                    discovered_edges.append(edge)
        
        return discovered_edges
    
    def _find_temporal_neighbors(
        self,
        memories: List[Memory],
        time_window: int = 3600  # 1小时内
    ) -> List[Tuple[Memory, Memory]]:
        """查找时间上相近的记忆对"""
        pairs = []
        sorted_mems = sorted(memories, key=lambda m: m.created_at)
        
        for i in range(len(sorted_mems) - 1):
            mem_a = sorted_mems[i]
            mem_b = sorted_mems[i + 1]
            
            time_diff = (mem_b.created_at - mem_a.created_at).total_seconds()
            if time_diff <= time_window:
                pairs.append((mem_a, mem_b))
        
        return pairs
    
    async def _check_causality_with_llm(
        self,
        mem_a: Memory,
        mem_b: Memory
    ) -> bool:
        """使用小模型判断两条记忆是否有因果关系"""
        prompt = f"""
判断以下两条记忆是否存在因果关系：

记忆A: {mem_a.to_text()}
记忆B: {mem_b.to_text()}

如果A是B的原因，或B是A的结果，回答"是"；否则回答"否"。
仅回答"是"或"否"，不要解释。
"""
        response = await self.llm_model.generate_response_async(
            prompt,
            temperature=0.1
        )
        return "是" in response[0]
```

#### 12.4.4 定时任务调度

```python
# 在 src/memory_graph/scheduler.py

import asyncio
from datetime import datetime, timedelta
from src.common.logger import get_logger

logger = get_logger(__name__)

class MemoryConsolidationScheduler:
    """记忆整理定时调度器"""
    
    def __init__(
        self,
        consolidator: MemoryConsolidator,
        interval_hours: int = 6  # 默认每6小时整理一次
    ):
        self.consolidator = consolidator
        self.interval = timedelta(hours=interval_hours)
        self.running = False
        self.last_run = None
    
    async def start(self):
        """启动定时任务"""
        self.running = True
        logger.info(f"记忆整理调度器已启动，间隔: {self.interval}")
        
        while self.running:
            try:
                # 执行整理
                await self.consolidator.run_consolidation()
                self.last_run = datetime.now()
                
                # 等待下一次
                await asyncio.sleep(self.interval.total_seconds())
                
            except Exception as e:
                logger.error(f"记忆整理任务失败: {e}", exc_info=True)
                # 失败后等待10分钟再重试
                await asyncio.sleep(600)
    
    def stop(self):
        """停止定时任务"""
        self.running = False
        logger.info("记忆整理调度器已停止")

# 在主程序启动时初始化
# bot.py 或 main.py

async def setup_memory_system():
    # ... 初始化存储、构建器等 ...
    
    # 创建整理器
    consolidator = MemoryConsolidator(
        storage=memory_storage,
        node_merger=node_merger,
        relation_discoverer=relation_discoverer
    )
    
    # 创建调度器并启动
    scheduler = MemoryConsolidationScheduler(
        consolidator=consolidator,
        interval_hours=6  # 每6小时整理一次
    )
    
    # 在后台启动调度器
    asyncio.create_task(scheduler.start())
    
    return memory_system
```

### 12.5 完整流程示例

#### 场景：用户对话
```
[时间: 10:00] 用户: "我今天心情不好"
                ↓
        LLM回复 + create_memory工具调用
                ↓
        记忆入库（staged状态）:
        [我]--事实--[心情]--是--[不好]--时间--[2025-11-05 10:00]
        
[时间: 10:05] 用户: "因为昨晚没睡好"
                ↓
        LLM回复 + create_memory工具调用
                ↓
        记忆入库（staged状态）:
        [我]--事件--[睡眠]--是--[不好]--时间--[2025-11-04 夜间]
                +
        link_memories工具调用:
        [睡眠不好] --导致--> [心情不好]

[时间: 16:00] 定时任务触发
                ↓
        整理器处理这两条记忆:
        1. 节点去重: "心情"节点与历史"情绪"节点合并
        2. 关联发现: 已有明确因果边，无需额外发现
        3. 状态更新: staged → consolidated

[时间: 18:00] 用户查询: "我为什么今天不开心？"
                ↓
        检索器处理:
        1. 小模型查询规划: 识别出"因果查询"，设置 expand_depth=1
        2. 向量初筛: 召回"心情不好"记忆
        3. 图遍历扩展: 沿"导致"边，找到"睡眠不好"记忆
        4. 返回结果:
           - 心情不好 (相关度: 0.95, 图距离: 0)
           - 睡眠不好 (相关度: 0.82, 图距离: 1, 因果关系)
                ↓
        LLM生成回答:
        "根据记忆，你今天心情不好是因为昨晚没睡好。"
```

### 12.6 配置建议

```toml
# config/memory_graph.toml

[consolidation]
# 整理任务配置
interval_hours = 6              # 整理间隔（小时）
batch_size = 100                # 每次处理记忆数量
enable_auto_discovery = true    # 是否启用自动关联发现
enable_conflict_detection = true # 是否启用冲突检测

[retrieval]
# 检索配置
default_mode = "auto"           # auto/fast/deep
max_expand_depth = 2            # 最大图扩展深度
vector_weight = 0.4             # 向量相似度权重
graph_distance_weight = 0.2     # 图距离权重
importance_weight = 0.2         # 重要性权重
recency_weight = 0.2            # 时效性权重

[node_merger]
# 节点去重配置
similarity_threshold = 0.85     # 相似度阈值
context_match_required = true   # 是否要求上下文匹配
merge_batch_size = 50           # 批量处理大小
```

---

## 更新日志

- **2025-11-05**: 初始版本，完成整体架构设计和工具接口定义
- **2025-11-05**: 新增第12章 - 记忆系统运作模式，明确构建/检索/整理的完整流程
