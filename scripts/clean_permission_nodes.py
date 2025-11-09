"""
清理权限节点数据库

删除所有旧的权限节点记录，让系统重新注册
"""
import asyncio
import sys
from pathlib import Path

# 添加项目根目录到 Python 路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.common.database.compatibility import get_db_session
from src.common.database.core.models import PermissionNodes
from src.common.logger import get_logger

logger = get_logger("CleanPermissionNodes")


async def clean_permission_nodes():
    """清理所有权限节点"""
    try:
        from sqlalchemy import delete

        async with get_db_session() as session:
            # 删除所有权限节点
            stmt = delete(PermissionNodes)
            result = await session.execute(stmt)
            await session.commit()

            deleted_count = result.rowcount if hasattr(result, "rowcount") else 0
            logger.info(f"✅ 已清理 {deleted_count} 个权限节点记录")
            print(f"✅ 已清理 {deleted_count} 个权限节点记录")
            print("请重启应用以重新注册权限节点")

    except Exception as e:
        logger.error(f"❌ 清理权限节点失败: {e}")
        print(f"❌ 清理权限节点失败: {e}")
        raise


if __name__ == "__main__":
    asyncio.run(clean_permission_nodes())
