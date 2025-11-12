# 路径评分扩展算法技术规范

**文档版本**: 1.0.0  
**创建日期**: 2025-01-12  
**目标**: 为 C++/其他语言实现提供完整的算法规范  
**作者**: MoFox Bot Development Team

---

## 目录

1. [算法概述](#1-算法概述)
2. [核心数据结构](#2-核心数据结构)
3. [算法流程详解](#3-算法流程详解)
4. [核心公式与计算](#4-核心公式与计算)
5. [性能优化要点](#5-性能优化要点)
6. [接口定义](#6-接口定义)
7. [测试用例](#7-测试用例)
8. [附录](#8-附录)

---

## 1. 算法概述

### 1.1 问题背景

在大规模记忆图检索系统中，传统的向量相似度搜索+图扩展方法存在以下问题：

- **召回不足**: 仅依赖向量相似度，无法捕捉结构化关系
- **组合爆炸**: 深度图遍历导致候选记忆数量指数增长
- **质量下降**: 扩展层级越深，相关性越弱

### 1.2 算法目标

设计一种基于**路径评分与传播**的图检索算法，实现：

1. **精准召回**: 同时考虑语义相似度和图结构关系
2. **可控扩展**: 通过动态剪枝避免组合爆炸
3. **质量保证**: 路径评分随深度衰减，保证相关性

### 1.3 核心思想

```
初始节点 (向量搜索TopK)
    ↓
路径扩展 (多跳遍历 + 分数传播)
    ↓
路径合并 (端点相遇时智能合并)
    ↓
路径剪枝 (低分路径提前终止)
    ↓
记忆聚合 (路径映射到记忆)
    ↓
最终评分 (路径分数 + 重要性 + 时效性)
```

---

## 2. 核心数据结构

### 2.1 节点 (Node)

```cpp
struct Node {
    string id;              // 节点唯一标识符 (UUID)
    string content;         // 节点文本内容
    NodeType type;          // 节点类型枚举
    vector<float> embedding; // 向量表示 (384维或其他)
    map<string, string> metadata; // 元数据
    
    // 可选字段
    float importance;       // 节点重要性 [0.0, 1.0]
    time_t created_at;      // 创建时间戳
};

enum NodeType {
    PERSON,      // 人物实体
    ENTITY,      // 一般实体
    EVENT,       // 事件
    TOPIC,       // 主题
    ATTRIBUTE,   // 属性
    VALUE,       // 值
    TIME,        // 时间
    LOCATION,    // 地点
    OTHER        // 其他
};
```

### 2.2 边 (Edge)

```cpp
struct Edge {
    string id;              // 边唯一标识符
    string source_id;       // 源节点ID
    string target_id;       // 目标节点ID
    EdgeType type;          // 边类型枚举
    string relation;        // 关系描述文本 (如 "喜欢", "创建了")
    float importance;       // 边重要性 [0.0, 1.0]
    
    // 可选字段
    time_t created_at;      // 创建时间戳
    map<string, string> metadata;
};

enum EdgeType {
    REFERENCE,       // 引用关系 (权重 1.3)
    ATTRIBUTE,       // 属性关系 (权重 1.2)
    HAS_PROPERTY,    // 拥有属性 (权重 1.2)
    RELATION,        // 一般关系 (权重 0.9)
    TEMPORAL,        // 时序关系 (权重 0.7)
    CORE_RELATION,   // 核心关系 (权重 1.0)
    DEFAULT          // 默认关系 (权重 1.0)
};
```

### 2.3 记忆 (Memory)

```cpp
struct Memory {
    string id;              // 记忆唯一标识符
    vector<Node> nodes;     // 记忆包含的节点列表
    vector<Edge> edges;     // 记忆包含的边列表
    MemoryType type;        // 记忆类型
    
    // 评分相关字段
    float importance;       // 重要性 [0.0, 1.0]
    float activation;       // 当前激活度 [0.0, 1.0]
    time_t created_at;      // 创建时间戳
    time_t last_accessed_at; // 最后访问时间
    
    // 可选字段
    map<string, string> metadata;
};

enum MemoryType {
    FACT,        // 事实
    OPINION,     // 观点
    RELATION,    // 关系
    EVENT,       // 事件
    OTHER        // 其他
};
```

### 2.4 路径 (Path)

```cpp
struct Path {
    vector<string> nodes;   // 路径节点ID序列 (有序)
    vector<string> edges;   // 路径边ID序列 (有序，长度 = nodes.size() - 1)
    float score;            // 当前路径分数
    int depth;              // 路径深度 (跳数)
    
    // 路径合并相关
    Path* parent;           // 父路径指针 (用于合并追溯)
    bool is_merged;         // 是否为合并路径
    vector<Path*> merged_from; // 合并来源路径列表
    
    // 构造函数
    Path(const string& start_node, float initial_score) 
        : score(initial_score), depth(0), parent(nullptr), is_merged(false) {
        nodes.push_back(start_node);
    }
};
```

### 2.5 配置参数 (Config)

```cpp
struct PathExpansionConfig {
    // === 扩展控制参数 ===
    int max_hops = 2;                    // 最大跳数 (深度限制)
    float damping_factor = 0.85;         // 衰减因子 (类似 PageRank)
    int max_branches_per_node = 10;      // 每个节点最大分叉数
    
    // === 路径合并参数 ===
    enum MergeStrategy {
        WEIGHTED_GEOMETRIC,  // 加权几何平均
        MAX_BONUS           // 最大值加成
    };
    MergeStrategy merge_strategy = WEIGHTED_GEOMETRIC;
    
    // === 剪枝参数 ===
    float pruning_threshold = 0.9;       // 剪枝阈值 (路径相似度)
    
    // === 边类型权重 ===
    map<EdgeType, float> edge_type_weights = {
        {REFERENCE, 1.3},
        {ATTRIBUTE, 1.2},
        {HAS_PROPERTY, 1.2},
        {RELATION, 0.9},
        {TEMPORAL, 0.7},
        {DEFAULT, 1.0}
    };
    
    // === 最终评分权重 ===
    struct FinalScoringWeights {
        float path_score = 0.50;     // 路径分数权重
        float importance = 0.30;     // 重要性权重
        float recency = 0.20;        // 时效性权重
    } final_scoring_weights;
};
```

---

## 3. 算法流程详解

### 3.1 总体流程图

```
┌─────────────────────────────────────────┐
│ 输入: 初始节点列表 (向量搜索 TopK)      │
│       [(node_id, score, metadata), ...] │
└────────────────┬────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────────┐
│ Step 1: 初始化路径队列                  │
│  - 为每个初始节点创建路径对象           │
│  - 设置初始分数和深度                   │
└────────────────┬────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────────┐
│ Step 2: 多跳路径扩展 (主循环)          │
│  for hop in 1..max_hops:               │
│    ├─ 2.1 获取邻居边                   │
│    ├─ 2.2 计算边权重                   │
│    ├─ 2.3 计算节点分数                 │
│    ├─ 2.4 传播路径分数                 │
│    ├─ 2.5 尝试路径合并                 │
│    ├─ 2.6 执行路径剪枝                 │
│    └─ 2.7 控制分叉数量                 │
└────────────────┬────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────────┐
│ Step 3: 提取叶子路径                    │
│  - 筛选未继续扩展的终点路径             │
└────────────────┬────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────────┐
│ Step 4: 路径映射到记忆                  │
│  - 通过节点ID查找所属记忆               │
│  - 聚合同一记忆的多条路径               │
└────────────────┬────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────────┐
│ Step 5: 最终评分与排序                  │
│  final_score = w1*path + w2*imp + w3*rec│
└────────────────┬────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────────┐
│ 输出: 排序后的记忆列表 (TopK)          │
│       [(Memory, score, paths), ...]     │
└─────────────────────────────────────────┘
```

### 3.2 Step 1: 初始化路径队列

```python
# 伪代码
function initialize_paths(initial_nodes):
    active_paths = []
    best_score_to_node = {}  # 记录每个节点的最佳到达分数
    
    for (node_id, score, metadata) in initial_nodes:
        path = Path(node_id, score)
        active_paths.append(path)
        best_score_to_node[node_id] = score
    
    return active_paths, best_score_to_node
```

**关键点**:
- `best_score_to_node` 用于路径合并时的去重判断
- 初始分数来自向量搜索的相似度

### 3.3 Step 2: 多跳路径扩展 (核心算法)

```python
function expand_paths_multi_hop(active_paths, config, graph_store):
    for hop in range(1, config.max_hops + 1):
        new_paths = []
        merge_count = 0
        prune_count = 0
        branch_count = 0
        
        for path in active_paths:
            current_node_id = path.nodes[-1]  # 当前路径的终点
            
            # 2.1 获取邻居边 (按权重和重要性排序)
            neighbor_edges = get_sorted_neighbor_edges(current_node_id, graph_store)
            
            # 2.2 计算动态分叉数 (分数越高，允许更多分叉)
            max_branches = calculate_max_branches(path.score, config)
            
            # 2.3 遍历邻居边 (最多 max_branches 个)
            for edge in neighbor_edges[:max_branches]:
                next_node_id = edge.target_id
                
                # 避免重复访问
                if next_node_id in path.nodes:
                    continue
                
                # 2.4 计算边权重
                edge_weight = get_edge_weight(edge, config)
                
                # 2.5 计算节点分数 (基于查询向量相似度)
                node_score = get_node_score(next_node_id, query_embedding)
                
                # 2.6 传播路径分数 (核心公式)
                new_score = calculate_path_score(
                    old_score=path.score,
                    edge_weight=edge_weight,
                    node_score=node_score,
                    depth=hop,
                    damping=config.damping_factor
                )
                
                # 2.7 尝试路径合并
                should_merge, existing_path = try_merge_paths(
                    next_node_id, new_score, best_score_to_node, config
                )
                
                if should_merge:
                    # 合并路径
                    merged_path = merge_two_paths(path, existing_path, new_score, config)
                    new_paths.append(merged_path)
                    merge_count += 1
                else:
                    # 创建新路径
                    new_path = extend_path(path, next_node_id, edge.id, new_score, hop)
                    new_paths.append(new_path)
                    best_score_to_node[next_node_id] = max(
                        best_score_to_node.get(next_node_id, 0.0), new_score
                    )
                    branch_count += 1
        
        # 2.8 路径剪枝 (移除低分路径)
        new_paths, prune_count = prune_low_score_paths(new_paths, config)
        
        active_paths = new_paths
        
        log(f"Hop {hop}/{config.max_hops}: {len(active_paths)} paths, "
            f"{branch_count} branches, {merge_count} merges, {prune_count} pruned")
    
    return active_paths
```

### 3.4 Step 3: 提取叶子路径

```python
function extract_leaf_paths(all_paths):
    """
    提取所有未继续扩展的终点路径
    
    判断标准：路径的终点节点不再作为其他路径的起点
    """
    endpoint_nodes = set(path.nodes[-1] for path in all_paths)
    startpoint_nodes = set(path.nodes[0] for path in all_paths if path.depth > 0)
    
    leaf_paths = []
    for path in all_paths:
        if path.nodes[-1] not in startpoint_nodes or path.depth == config.max_hops:
            leaf_paths.append(path)
    
    return leaf_paths
```

### 3.5 Step 4: 路径映射到记忆

```python
function map_paths_to_memories(leaf_paths, graph_store):
    """
    将路径映射到记忆对象
    
    实现细节：
    1. 遍历路径中的每个节点
    2. 查询该节点所属的记忆列表 (node_to_memories 映射表)
    3. 将路径添加到对应记忆的路径列表中
    """
    memory_paths = {}  # { memory_id: (Memory, [Path, ...]) }
    
    for path in leaf_paths:
        # 遍历路径中的所有节点
        for node_id in path.nodes:
            # 查询节点所属的记忆列表
            memory_ids = graph_store.get_memories_by_node(node_id)
            
            for memory_id in memory_ids:
                if memory_id not in memory_paths:
                    memory = graph_store.get_memory_by_id(memory_id)
                    memory_paths[memory_id] = (memory, [])
                
                # 将路径添加到该记忆的路径列表
                memory_paths[memory_id][1].append(path)
    
    return memory_paths
```

### 3.6 Step 5: 最终评分

```python
function final_scoring(memory_paths, config, current_time):
    """
    最终评分：结合路径质量、重要性、时效性
    """
    scored_memories = []
    
    for memory_id, (memory, paths) in memory_paths.items():
        # 1. 聚合路径分数
        path_score = aggregate_path_scores(paths)
        
        # 2. 重要性分数 (直接使用)
        importance_score = memory.importance
        
        # 3. 时效性分数 (基于创建时间和最后访问时间)
        recency_score = calculate_recency(memory, current_time)
        
        # 4. 加权求和
        weights = config.final_scoring_weights
        final_score = (
            path_score * weights.path_score +
            importance_score * weights.importance +
            recency_score * weights.recency
        )
        
        scored_memories.append((memory, final_score, paths))
    
    # 5. 按分数降序排序
    scored_memories.sort(key=lambda x: x[1], reverse=True)
    
    return scored_memories
```

---

## 4. 核心公式与计算

### 4.1 路径分数传播公式

这是算法的**核心公式**，决定了分数如何沿路径传播：

```
new_score = old_score × edge_weight × decay + node_score × (1 - decay)

其中:
  decay = damping_factor ^ depth
  
参数说明:
  - old_score: 上一跳的路径分数
  - edge_weight: 边的权重 (基于边类型和重要性)
  - decay: 指数衰减因子 (随深度增加而减小)
  - node_score: 新节点的质量分数 (基于查询相似度)
  - damping_factor: 衰减系数 (默认 0.85)
  - depth: 当前跳数 (1, 2, ...)
```

**公式解析**:

1. **传播部分** (`old_score × edge_weight × decay`):
   - 继承上一跳的分数，通过边的质量加权
   - 随深度指数衰减，确保远距离节点影响力降低

2. **注入部分** (`node_score × (1 - decay)`):
   - 注入新节点的"新鲜"分数
   - 权重与衰减互补，浅层节点更依赖传播，深层节点更依赖自身质量

**数值示例**:

```
假设:
  old_score = 0.8
  edge_weight = 1.2 (ATTRIBUTE 类型边)
  node_score = 0.6
  depth = 1
  damping_factor = 0.85

计算:
  decay = 0.85^1 = 0.85
  propagated = 0.8 × 1.2 × 0.85 = 0.816
  fresh = 0.6 × (1 - 0.85) = 0.09
  new_score = 0.816 + 0.09 = 0.906
```

### 4.2 边权重计算

```cpp
float get_edge_weight(const Edge& edge, const Config& config) {
    // 1. 基础权重 (边自身的重要性)
    float base_weight = edge.importance;
    
    // 2. 类型权重 (从配置中查询)
    float type_weight = config.edge_type_weights.at(edge.type);
    
    // 3. 综合权重
    return base_weight * type_weight;
}
```

**类型权重表**:

| 边类型 | 权重 | 说明 |
|--------|------|------|
| REFERENCE | 1.3 | 引用关系，强相关 |
| ATTRIBUTE | 1.2 | 属性关系，结构重要 |
| HAS_PROPERTY | 1.2 | 拥有属性，结构重要 |
| CORE_RELATION | 1.0 | 核心关系，标准权重 |
| RELATION | 0.9 | 一般关系，略弱 |
| TEMPORAL | 0.7 | 时序关系，较弱 |
| DEFAULT | 1.0 | 默认权重 |

### 4.3 节点分数计算

```cpp
float get_node_score(const string& node_id, const vector<float>& query_embedding,
                     VectorStore& vector_store) {
    // 1. 从向量存储获取节点数据
    auto node_data = vector_store.get_node_by_id(node_id);
    if (!node_data.has_value()) {
        return 0.3;  // 无向量的节点给低分
    }
    
    // 2. 提取节点向量
    vector<float> node_embedding = node_data->embedding;
    
    // 3. 计算余弦相似度
    float similarity = cosine_similarity(query_embedding, node_embedding);
    
    // 4. 限制在 [0, 1] 范围
    return std::max(0.0f, std::min(1.0f, similarity));
}
```

### 4.4 动态分叉数计算

```cpp
int calculate_max_branches(float path_score, const Config& config) {
    // 分数越高，允许更多分叉
    // 公式: max_branches * (0.5 + 0.5 * path_score)
    
    float ratio = 0.5f + 0.5f * path_score;
    int branches = static_cast<int>(config.max_branches_per_node * ratio);
    
    // 至少保留 1 个分叉
    return std::max(1, branches);
}
```

**分叉数示例**:

| 路径分数 | 分叉比例 | 分叉数 (max=10) |
|---------|---------|----------------|
| 1.0 | 100% | 10 |
| 0.8 | 90% | 9 |
| 0.6 | 80% | 8 |
| 0.4 | 70% | 7 |
| 0.2 | 60% | 6 |
| 0.0 | 50% | 5 |

### 4.5 路径合并公式

#### 策略 1: 加权几何平均 (WEIGHTED_GEOMETRIC)

```cpp
float merge_score_weighted_geometric(float score1, float score2) {
    // 几何平均 + 20% 奖励
    float geometric_mean = std::sqrt(score1 * score2);
    return geometric_mean * 1.2f;
}
```

**特点**: 平衡两条路径的分数，避免单一高分路径主导

#### 策略 2: 最大值加成 (MAX_BONUS)

```cpp
float merge_score_max_bonus(float score1, float score2) {
    // 最大值 + 30% 奖励
    float max_score = std::max(score1, score2);
    return max_score * 1.3f;
}
```

**特点**: 奖励高质量路径，适合偏好精准召回的场景

### 4.6 路径分数聚合

```cpp
float aggregate_path_scores(const vector<Path*>& paths) {
    if (paths.empty()) return 0.0f;
    
    // 按分数排序
    vector<Path*> sorted_paths = paths;
    std::sort(sorted_paths.begin(), sorted_paths.end(),
              [](Path* a, Path* b) { return a->score > b->score; });
    
    // 加权求和 (权重随排名递减)
    float total_weight = 0.0f;
    float weighted_sum = 0.0f;
    
    for (size_t i = 0; i < sorted_paths.size(); ++i) {
        float weight = 1.0f / (i + 1);  // 第1名=1.0, 第2名=0.5, 第3名=0.33...
        weighted_sum += sorted_paths[i]->score * weight;
        total_weight += weight;
    }
    
    return weighted_sum / total_weight;
}
```

### 4.7 时效性计算

```cpp
float calculate_recency(const Memory& memory, time_t current_time) {
    // 基于创建时间和最后访问时间的综合时效性
    
    time_t created_delta = current_time - memory.created_at;
    time_t accessed_delta = current_time - memory.last_accessed_at;
    
    // 创建时间衰减 (30天半衰期)
    float creation_decay = std::exp(-created_delta / (30.0 * 86400));
    
    // 访问时间衰减 (7天半衰期)
    float access_decay = std::exp(-accessed_delta / (7.0 * 86400));
    
    // 加权平均
    return 0.4f * creation_decay + 0.6f * access_decay;
}
```

---

## 5. 性能优化要点

### 5.1 关键性能瓶颈

根据 Python 实现的性能分析：

| 操作 | 耗时占比 | 优化优先级 |
|------|---------|-----------|
| 向量相似度计算 | 35% | ⭐⭐⭐⭐⭐ |
| 图遍历 (邻居查询) | 25% | ⭐⭐⭐⭐ |
| 路径合并判断 | 15% | ⭐⭐⭐ |
| 路径对象创建 | 10% | ⭐⭐⭐ |
| 最终评分排序 | 8% | ⭐⭐ |
| 其他 | 7% | ⭐ |

### 5.2 优化策略

#### 5.2.1 向量相似度计算优化

**方法 1: SIMD 加速**

```cpp
// 使用 AVX2/AVX512 指令集加速余弦相似度计算
float cosine_similarity_simd(const float* vec1, const float* vec2, size_t dim) {
    #ifdef __AVX2__
    // AVX2 实现 (8个float并行)
    __m256 sum = _mm256_setzero_ps();
    __m256 norm1 = _mm256_setzero_ps();
    __m256 norm2 = _mm256_setzero_ps();
    
    for (size_t i = 0; i < dim; i += 8) {
        __m256 v1 = _mm256_loadu_ps(&vec1[i]);
        __m256 v2 = _mm256_loadu_ps(&vec2[i]);
        
        sum = _mm256_fmadd_ps(v1, v2, sum);
        norm1 = _mm256_fmadd_ps(v1, v1, norm1);
        norm2 = _mm256_fmadd_ps(v2, v2, norm2);
    }
    
    float dot = horizontal_sum(sum);
    float n1 = std::sqrt(horizontal_sum(norm1));
    float n2 = std::sqrt(horizontal_sum(norm2));
    
    return dot / (n1 * n2);
    #else
    // 标准实现
    // ...
    #endif
}
```

**性能提升**: 3-5倍 (依赖CPU指令集)

**方法 2: 向量量化 (Product Quantization)**

```cpp
// 将 float32 向量量化为 uint8，减少内存和计算量
struct QuantizedVector {
    vector<uint8_t> codes;  // 量化码本
    vector<float> codebook; // 码本中心点
    
    float approximate_similarity(const QuantizedVector& other) const;
};
```

**性能提升**: 5-10倍 (精度略有损失，约1-2%)

#### 5.2.2 图遍历优化

**方法: 邻接表 + 缓存优化**

```cpp
// 使用紧凑的内存布局提高缓存命中率
struct CompactGraph {
    // 节点索引: ID -> 整数索引映射
    unordered_map<string, int> node_index;
    
    // 邻接表 (CSR格式)
    vector<int> edge_offsets;     // 每个节点的边起始位置
    vector<int> edge_targets;     // 目标节点索引
    vector<Edge> edge_data;       // 边数据
    
    // 快速查询邻居
    span<Edge> get_neighbors(const string& node_id) const {
        int idx = node_index.at(node_id);
        int start = edge_offsets[idx];
        int end = edge_offsets[idx + 1];
        return span<Edge>(&edge_data[start], end - start);
    }
};
```

**性能提升**: 2-3倍 (缓存命中率提升)

#### 5.2.3 路径对象内存池

```cpp
// 避免频繁的内存分配/释放
class PathPool {
public:
    Path* allocate() {
        if (free_list.empty()) {
            return new Path();
        }
        Path* path = free_list.back();
        free_list.pop_back();
        return path;
    }
    
    void deallocate(Path* path) {
        path->reset();
        free_list.push_back(path);
    }
    
private:
    vector<Path*> free_list;
};
```

**性能提升**: 1.5-2倍 (减少内存分配开销)

#### 5.2.4 并行化

```cpp
// 多线程并行扩展路径
vector<Path*> expand_paths_parallel(const vector<Path*>& active_paths,
                                    const Config& config,
                                    int num_threads = 8) {
    vector<vector<Path*>> thread_results(num_threads);
    
    #pragma omp parallel for num_threads(num_threads)
    for (int i = 0; i < active_paths.size(); ++i) {
        int tid = omp_get_thread_num();
        auto new_paths = expand_single_path(active_paths[i], config);
        thread_results[tid].insert(thread_results[tid].end(),
                                   new_paths.begin(), new_paths.end());
    }
    
    // 合并结果
    vector<Path*> result;
    for (const auto& thread_result : thread_results) {
        result.insert(result.end(), thread_result.begin(), thread_result.end());
    }
    
    return result;
}
```

**性能提升**: 接近线程数倍数 (4-8倍，依赖CPU核心数)

### 5.3 内存优化

#### 5.3.1 内存布局优化

```cpp
// 使用紧凑的内存布局减少内存占用
struct CompactPath {
    // 不使用 vector<string>，改用索引数组
    vector<int> node_indices;  // 4 bytes per node (vs 32+ bytes per string)
    vector<int> edge_indices;  // 4 bytes per edge
    
    float score;               // 4 bytes
    uint8_t depth;             // 1 byte (vs 4 bytes int)
    uint8_t flags;             // 1 byte (存储 is_merged 等标志位)
    
    // 总内存: ~10 bytes + 8*depth bytes (vs 100+ bytes)
};
```

**内存节省**: 5-10倍

#### 5.3.2 分阶段释放内存

```cpp
// 每一跳后释放上一跳的路径对象
for (int hop = 1; hop <= config.max_hops; ++hop) {
    auto new_paths = expand_one_hop(active_paths, config);
    
    // 释放旧路径
    for (auto* path : active_paths) {
        if (!path->is_merged) {  // 保留被合并的路径
            delete path;
        }
    }
    
    active_paths = new_paths;
}
```

---

## 6. 接口定义

### 6.1 主接口

```cpp
/**
 * 路径评分扩展算法主接口
 * 
 * @param initial_nodes 初始节点列表 (来自向量搜索)
 *        格式: [(node_id, score, metadata), ...]
 * @param query_embedding 查询向量 (用于计算节点相似度)
 * @param top_k 返回的top记忆数量
 * @param config 算法配置参数
 * @param graph_store 图存储接口
 * @param vector_store 向量存储接口
 * 
 * @return 排序后的记忆列表
 *         格式: [(Memory, final_score, contributing_paths), ...]
 */
vector<tuple<Memory, float, vector<Path*>>> expand_with_path_scoring(
    const vector<tuple<string, float, map<string, string>>>& initial_nodes,
    const vector<float>& query_embedding,
    int top_k,
    const PathExpansionConfig& config,
    GraphStore& graph_store,
    VectorStore& vector_store
);
```

### 6.2 图存储接口

```cpp
class GraphStore {
public:
    /**
     * 获取节点的所有出边 (按重要性排序)
     * 
     * @param node_id 节点ID
     * @return 边列表
     */
    virtual vector<Edge> get_outgoing_edges(const string& node_id) const = 0;
    
    /**
     * 根据节点ID查询所属的记忆列表
     * 
     * @param node_id 节点ID
     * @return 记忆ID列表
     */
    virtual vector<string> get_memories_by_node(const string& node_id) const = 0;
    
    /**
     * 根据记忆ID获取记忆对象
     * 
     * @param memory_id 记忆ID
     * @return 记忆对象
     */
    virtual Memory get_memory_by_id(const string& memory_id) const = 0;
};
```

### 6.3 向量存储接口

```cpp
class VectorStore {
public:
    /**
     * 根据节点ID获取节点数据 (包含向量)
     * 
     * @param node_id 节点ID
     * @return 节点数据 (包含 embedding, metadata)
     */
    virtual optional<NodeData> get_node_by_id(const string& node_id) const = 0;
    
    struct NodeData {
        string id;
        vector<float> embedding;  // 向量表示
        map<string, string> metadata;
    };
};
```

---

## 7. 测试用例

### 7.1 单元测试

#### 测试 1: 路径分数传播

```cpp
TEST(PathExpansion, ScorePropagation) {
    // 输入
    float old_score = 0.8;
    float edge_weight = 1.2;
    float node_score = 0.6;
    int depth = 1;
    float damping = 0.85;
    
    // 执行
    float new_score = calculate_path_score(old_score, edge_weight, node_score, depth, damping);
    
    // 验证
    float expected = 0.8 * 1.2 * 0.85 + 0.6 * (1 - 0.85);
    EXPECT_NEAR(new_score, expected, 0.001);
    EXPECT_NEAR(new_score, 0.906, 0.001);
}
```

#### 测试 2: 路径合并

```cpp
TEST(PathExpansion, PathMerge) {
    // 创建两条路径
    Path path1({"A", "B", "C"}, {}, 0.8, 2);
    Path path2({"D", "E", "C"}, {}, 0.7, 2);
    
    // 合并
    PathExpansionConfig config;
    config.merge_strategy = WEIGHTED_GEOMETRIC;
    
    Path* merged = merge_two_paths(&path1, &path2, 0.9, config);
    
    // 验证
    EXPECT_TRUE(merged->is_merged);
    EXPECT_EQ(merged->merged_from.size(), 2);
    EXPECT_NEAR(merged->score, sqrt(0.8 * 0.7) * 1.2, 0.001);
}
```

#### 测试 3: 动态分叉数

```cpp
TEST(PathExpansion, DynamicBranches) {
    PathExpansionConfig config;
    config.max_branches_per_node = 10;
    
    EXPECT_EQ(calculate_max_branches(1.0, config), 10);
    EXPECT_EQ(calculate_max_branches(0.8, config), 9);
    EXPECT_EQ(calculate_max_branches(0.5, config), 7);
    EXPECT_EQ(calculate_max_branches(0.0, config), 5);
}
```

### 7.2 集成测试

#### 测试场景: 小型图检索

```cpp
TEST(PathExpansion, SmallGraphRetrieval) {
    // 构建测试图
    // 节点: A, B, C, D, E
    // 边: A->B, A->C, B->D, C->D, D->E
    GraphStore graph = build_test_graph();
    VectorStore vectors = build_test_vectors();
    
    // 初始节点: A (score=0.9), B (score=0.7)
    vector<tuple<string, float, map<string, string>>> initial_nodes = {
        {"A", 0.9, {}},
        {"B", 0.7, {}}
    };
    
    // 查询向量
    vector<float> query_embedding = generate_random_vector(384);
    
    // 配置
    PathExpansionConfig config;
    config.max_hops = 2;
    
    // 执行
    auto results = expand_with_path_scoring(
        initial_nodes, query_embedding, 5, config, graph, vectors
    );
    
    // 验证
    EXPECT_GT(results.size(), 0);
    EXPECT_LE(results.size(), 5);
    
    // 验证排序 (分数递减)
    for (size_t i = 1; i < results.size(); ++i) {
        EXPECT_GE(get<1>(results[i-1]), get<1>(results[i]));
    }
}
```

### 7.3 性能基准测试

```cpp
BENCHMARK(PathExpansion, LargeGraph) {
    // 图规模: 10,000 节点, 50,000 边
    GraphStore graph = load_large_graph("test_data/large_graph.bin");
    VectorStore vectors = load_large_vectors("test_data/large_vectors.bin");
    
    // 初始节点: 50个
    auto initial_nodes = get_top_k_nodes(vectors, query_embedding, 50);
    
    PathExpansionConfig config;
    config.max_hops = 2;
    config.max_branches_per_node = 10;
    
    // 测量执行时间
    auto start = chrono::high_resolution_clock::now();
    
    auto results = expand_with_path_scoring(
        initial_nodes, query_embedding, 20, config, graph, vectors
    );
    
    auto end = chrono::high_resolution_clock::now();
    auto duration = chrono::duration_cast<chrono::milliseconds>(end - start);
    
    // 性能目标: < 500ms
    EXPECT_LT(duration.count(), 500);
    
    cout << "Execution time: " << duration.count() << " ms" << endl;
    cout << "Throughput: " << (initial_nodes.size() / (duration.count() / 1000.0)) 
         << " nodes/sec" << endl;
}
```

---

## 8. 附录

### 8.1 Python 参考实现路径

- 核心算法: `src/memory_graph/utils/path_expansion.py`
- 数据模型: `src/memory_graph/models.py`
- 配置定义: `src/config/official_configs.py`

### 8.2 数学符号说明

| 符号 | 含义 |
|------|------|
| $s_{\text{old}}$ | 上一跳路径分数 |
| $w_e$ | 边权重 |
| $s_n$ | 节点分数 |
| $d$ | 当前深度 (跳数) |
| $\alpha$ | 衰减因子 (damping_factor) |
| $\delta$ | 衰减值 = $\alpha^d$ |
| $s_{\text{new}}$ | 新路径分数 |

**完整公式**:

$$
s_{\text{new}} = s_{\text{old}} \times w_e \times \alpha^d + s_n \times (1 - \alpha^d)
$$

### 8.3 复杂度分析

**时间复杂度**:

- 最坏情况: $O(N \times B^H)$
  - $N$: 初始节点数
  - $B$: 平均分叉数
  - $H$: 最大跳数

- 实际情况 (有剪枝): $O(N \times B \times H)$

**空间复杂度**:

- 路径存储: $O(P \times H)$
  - $P$: 总路径数
  - $H$: 平均路径长度

### 8.4 参数调优建议

| 参数 | 默认值 | 调优建议 |
|------|--------|---------|
| max_hops | 2 | 1: 快速但召回少; 2: 平衡; 3+: 慢但召回多 |
| damping_factor | 0.85 | 0.9: 更重视传播; 0.8: 更重视节点质量 |
| max_branches | 10 | 5: 快速; 10: 平衡; 15+: 召回更全 |
| pruning_threshold | 0.9 | 0.85: 更激进剪枝; 0.95: 保留更多路径 |
| path_score_weight | 0.50 | 增加: 更重视路径质量; 减少: 更重视重要性/时效 |

### 8.5 常见问题 (FAQ)

**Q1: 为什么使用指数衰减而不是线性衰减？**

A: 指数衰减 ($\alpha^d$) 更符合信息传播的实际规律：
- 第1跳: 衰减到 85%
- 第2跳: 衰减到 72%
- 第3跳: 衰减到 61%

线性衰减会导致远距离节点的影响过大。

**Q2: 路径合并的触发条件是什么？**

A: 两条路径的终点相同，且分数差异在阈值范围内：

```cpp
bool should_merge = (path1.nodes.back() == path2.nodes.back()) &&
                    (abs(path1.score - path2.score) < 0.1);
```

**Q3: 如何处理有向图 vs 无向图？**

A: 当前实现假设**有向图**。如果是无向图，需要在获取邻居时同时考虑入边和出边：

```cpp
vector<Edge> edges = graph.get_outgoing_edges(node_id);
vector<Edge> incoming = graph.get_incoming_edges(node_id);
edges.insert(edges.end(), incoming.begin(), incoming.end());
```

**Q4: 内存占用估算？**

A: 粗略估算 (max_hops=2, max_branches=10, initial_nodes=50):

```
路径数: 50 * 10 * 10 = 5000 条
每条路径: ~100 bytes
总内存: 5000 * 100 = 500 KB
```

优化后可减少到 100 KB 以内。

---

## 9. 实现检查清单

提供给 C++ 开发人员的实现验证清单：

### 9.1 数据结构 ✅
- [ ] Node 结构体 (包含 id, content, type, embedding, metadata)
- [ ] Edge 结构体 (包含 id, source_id, target_id, type, importance)
- [ ] Memory 结构体 (包含 id, nodes, edges, importance, timestamps)
- [ ] Path 结构体 (包含 nodes, edges, score, depth, merge 信息)
- [ ] PathExpansionConfig 结构体 (包含所有配置参数)

### 9.2 核心算法 ✅
- [ ] 路径分数传播公式 (指数衰减 + 边权重 + 节点分数)
- [ ] 动态分叉数计算 (基于路径分数)
- [ ] 路径合并逻辑 (加权几何平均 / 最大值加成)
- [ ] 路径剪枝逻辑 (低分路径过滤)
- [ ] 多跳扩展主循环
- [ ] 叶子路径提取
- [ ] 路径到记忆映射
- [ ] 最终评分计算 (路径 + 重要性 + 时效性)

### 9.3 辅助函数 ✅
- [ ] 余弦相似度计算 (SIMD 优化可选)
- [ ] 边权重计算 (类型权重 × 重要性)
- [ ] 节点分数计算 (查询向量相似度)
- [ ] 路径分数聚合 (加权求和)
- [ ] 时效性计算 (指数衰减)

### 9.4 接口实现 ✅
- [ ] GraphStore 接口 (get_outgoing_edges, get_memories_by_node, get_memory_by_id)
- [ ] VectorStore 接口 (get_node_by_id)
- [ ] 主函数 expand_with_path_scoring

### 9.5 性能优化 ✅
- [ ] SIMD 加速向量计算
- [ ] 紧凑图存储 (CSR 格式)
- [ ] 路径对象内存池
- [ ] 并行化 (OpenMP / std::thread)
- [ ] 缓存友好的内存布局

### 9.6 测试 ✅
- [ ] 单元测试 (分数传播, 路径合并, 动态分叉)
- [ ] 集成测试 (小型图检索)
- [ ] 性能基准测试 (大规模图)
- [ ] 边界条件测试 (空图, 单节点, 循环图)

---

## 10. 联系方式

如有算法理解或实现问题，请联系：

- **技术负责人**: MoFox Bot Team
- **Python 参考实现**: `src/memory_graph/utils/path_expansion.py`
- **配置示例**: `config/bot_config.toml`

---

**文档结束** - 祝实现顺利！🚀
