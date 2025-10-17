#!/usr/bin/env python
"""
轻量烟雾测试：初始化 MemorySystem 并运行一次检索，验证 MemoryMetadata.source 访问不再报错
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.chat.memory_system.memory_system import MemorySystem


async def main():
    ms = MemorySystem()
    await ms.initialize()
    results = await ms.retrieve_relevant_memories(query_text="测试查询：杰瑞喵喜欢什么？", limit=3)
    print(f"检索到 {len(results)} 条记忆（如果 >0 则表明运行成功）")
    for i, m in enumerate(results, 1):
        print(f"{i}. id={m.metadata.memory_id} source={getattr(m.metadata, 'source', None)}")


if __name__ == "__main__":
    asyncio.run(main())
