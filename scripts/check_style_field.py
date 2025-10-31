"""
检查数据库中 style 字段的内容特征
"""
import asyncio
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from sqlalchemy import select

from src.common.database.sqlalchemy_database_api import get_db_session
from src.common.database.sqlalchemy_models import Expression


async def analyze_style_fields():
    """分析 style 字段的内容"""

    print("=" * 60)
    print("Style 字段内容分析")
    print("=" * 60)

    async with get_db_session() as session:
        # 获取所有表达方式
        result = await session.execute(select(Expression).limit(30))
        expressions = result.scalars().all()

        print(f"\n总共检查 {len(expressions)} 条记录\n")

        # 按类型分类
        style_examples = []

        for expr in expressions:
            if expr.type == "style":
                style_examples.append({
                    "situation": expr.situation,
                    "style": expr.style,
                    "length": len(expr.style) if expr.style else 0
                })

        print("📋 Style 类型样例 (前15条):")
        print("="*60)
        for i, ex in enumerate(style_examples[:15], 1):
            print(f"\n[{i}]")
            print(f"  Situation: {ex['situation']}")
            print(f"  Style: {ex['style']}")
            print(f"  长度: {ex['length']} 字符")

            # 判断是具体表达还是风格描述
            if ex["length"] <= 20 and any(word in ex["style"] for word in ["简洁", "短句", "陈述", "疑问", "感叹", "省略", "完整"]):
                style_type = "✓ 风格描述"
            elif ex["length"] <= 10:
                style_type = "? 可能是具体表达（较短）"
            else:
                style_type = "✗ 具体表达内容"

            print(f"  类型判断: {style_type}")

        print("\n" + "="*60)
        print("分析完成")
        print("="*60)


if __name__ == "__main__":
    asyncio.run(analyze_style_fields())
