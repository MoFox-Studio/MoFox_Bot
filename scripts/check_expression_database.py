"""
检查表达方式数据库状态的诊断脚本
"""
import asyncio
import sys
from pathlib import Path

# 添加项目根目录到路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from sqlalchemy import func, select

from src.common.database.sqlalchemy_database_api import get_db_session
from src.common.database.sqlalchemy_models import Expression


async def check_database():
    """检查表达方式数据库状态"""

    print("=" * 60)
    print("表达方式数据库诊断报告")
    print("=" * 60)

    async with get_db_session() as session:
        # 1. 统计总数
        total_count = await session.execute(select(func.count()).select_from(Expression))
        total = total_count.scalar()
        print(f"\n📊 总表达方式数量: {total}")

        if total == 0:
            print("\n⚠️  数据库为空！")
            print("\n可能的原因:")
            print("1. 还没有进行过表达学习")
            print("2. 配置中禁用了表达学习")
            print("3. 学习过程中发生了错误")
            print("\n建议:")
            print("- 检查 bot_config.toml 中的 [expression] 配置")
            print("- 查看日志中是否有表达学习相关的错误")
            print("- 确认聊天流的 learn_expression 配置为 true")
            return

        # 2. 按 chat_id 统计
        print("\n📝 按聊天流统计:")
        chat_counts = await session.execute(
            select(Expression.chat_id, func.count())
            .group_by(Expression.chat_id)
        )
        for chat_id, count in chat_counts:
            print(f"  - {chat_id}: {count} 个表达方式")

        # 3. 按 type 统计
        print("\n📝 按类型统计:")
        type_counts = await session.execute(
            select(Expression.type, func.count())
            .group_by(Expression.type)
        )
        for expr_type, count in type_counts:
            print(f"  - {expr_type}: {count} 个")

        # 4. 检查 situation 和 style 字段是否有空值
        print("\n🔍 字段完整性检查:")
        null_situation = await session.execute(
            select(func.count())
            .select_from(Expression)
            .where(Expression.situation == None)
        )
        null_style = await session.execute(
            select(func.count())
            .select_from(Expression)
            .where(Expression.style == None)
        )

        null_sit_count = null_situation.scalar()
        null_sty_count = null_style.scalar()

        print(f"  - situation 为空: {null_sit_count} 个")
        print(f"  - style 为空: {null_sty_count} 个")

        if null_sit_count > 0 or null_sty_count > 0:
            print("  ⚠️  发现空值！这会导致匹配失败")

        # 5. 显示一些样例数据
        print("\n📋 样例数据 (前10条):")
        samples = await session.execute(
            select(Expression)
            .limit(10)
        )

        for i, expr in enumerate(samples.scalars(), 1):
            print(f"\n  [{i}] Chat: {expr.chat_id}")
            print(f"      Type: {expr.type}")
            print(f"      Situation: {expr.situation}")
            print(f"      Style: {expr.style}")
            print(f"      Count: {expr.count}")

        # 6. 检查 style 字段的唯一值
        print("\n📋 Style 字段样例 (前20个):")
        unique_styles = await session.execute(
            select(Expression.style)
            .distinct()
            .limit(20)
        )

        styles = [s for s in unique_styles.scalars()]
        for style in styles:
            print(f"  - {style}")

        print(f"\n  (共 {len(styles)} 个不同的 style)")

    print("\n" + "=" * 60)
    print("诊断完成")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(check_database())
