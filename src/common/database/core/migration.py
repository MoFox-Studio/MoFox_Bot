"""数据库迁移模块

此模块负责数据库结构的自动检查和迁移：
- 自动创建不存在的表
- 自动为现有表添加缺失的列
- 自动为现有表创建缺失的索引

使用新架构的 engine 和 models
"""

from sqlalchemy import inspect
from sqlalchemy.sql import text

from src.common.database.core.engine import get_engine
from src.common.database.core.models import Base
from src.common.logger import get_logger

logger = get_logger("db_migration")


async def check_and_migrate_database(existing_engine=None):
    """异步检查数据库结构并自动迁移
    
    自动执行以下操作：
    - 创建不存在的表
    - 为现有表添加缺失的列
    - 为现有表创建缺失的索引
    
    Args:
        existing_engine: 可选的已存在的数据库引擎。如果提供，将使用该引擎；否则获取全局引擎
        
    Note:
        此函数是幂等的，可以安全地多次调用
    """
    logger.info("正在检查数据库结构并执行自动迁移...")
    engine = existing_engine if existing_engine is not None else await get_engine()

    async with engine.connect() as connection:
        # 在同步上下文中运行inspector操作
        def get_inspector(sync_conn):
            return inspect(sync_conn)

        inspector = await connection.run_sync(get_inspector)

        # 获取数据库中已存在的表名
        db_table_names = await connection.run_sync(
            lambda conn: set(inspector.get_table_names())
        )

        # 1. 首先处理表的创建
        tables_to_create = []
        for table_name, table in Base.metadata.tables.items():
            if table_name not in db_table_names:
                tables_to_create.append(table)

        if tables_to_create:
            logger.info(f"发现 {len(tables_to_create)} 个不存在的表，正在创建...")
            try:
                # 一次性创建所有缺失的表
                await connection.run_sync(
                    lambda sync_conn: Base.metadata.create_all(
                        sync_conn, tables=tables_to_create
                    )
                )
                for table in tables_to_create:
                    logger.info(f"表 '{table.name}' 创建成功。")
                    db_table_names.add(table.name)  # 将新创建的表添加到集合中
                    
                # 提交表创建事务
                await connection.commit()
            except Exception as e:
                logger.error(f"创建表时失败: {e}", exc_info=True)
                await connection.rollback()

        # 2. 然后处理现有表的列和索引的添加
        for table_name, table in Base.metadata.tables.items():
            if table_name not in db_table_names:
                logger.warning(
                    f"跳过检查表 '{table_name}'，因为它在创建步骤中可能已失败。"
                )
                continue

            logger.debug(f"正在检查表 '{table_name}' 的列和索引...")

            try:
                # 检查并添加缺失的列
                db_columns = await connection.run_sync(
                    lambda conn: {
                        col["name"] for col in inspector.get_columns(table_name)
                    }
                )
                model_columns = {col.name for col in table.c}
                missing_columns = model_columns - db_columns

                if missing_columns:
                    logger.info(
                        f"在表 '{table_name}' 中发现缺失的列: {', '.join(missing_columns)}"
                    )

                    def add_columns_sync(conn):
                        dialect = conn.dialect
                        compiler = dialect.ddl_compiler(dialect, None)

                        for column_name in missing_columns:
                            column = table.c[column_name]
                            column_type = compiler.get_column_specification(column)
                            sql = f"ALTER TABLE {table.name} ADD COLUMN {column.name} {column_type}"

                            if column.default:
                                # 手动处理不同方言的默认值
                                default_arg = column.default.arg
                                if dialect.name == "sqlite" and isinstance(
                                    default_arg, bool
                                ):
                                    # SQLite 将布尔值存储为 0 或 1
                                    default_value = "1" if default_arg else "0"
                                elif hasattr(compiler, "render_literal_value"):
                                    try:
                                        # 尝试使用 render_literal_value
                                        default_value = compiler.render_literal_value(
                                            default_arg, column.type
                                        )
                                    except AttributeError:
                                        # 如果失败，则回退到简单的字符串转换
                                        default_value = (
                                            f"'{default_arg}'"
                                            if isinstance(default_arg, str)
                                            else str(default_arg)
                                        )
                                else:
                                    # 对于没有 render_literal_value 的旧版或特定方言
                                    default_value = (
                                        f"'{default_arg}'"
                                        if isinstance(default_arg, str)
                                        else str(default_arg)
                                    )

                                sql += f" DEFAULT {default_value}"

                            if not column.nullable:
                                sql += " NOT NULL"

                            conn.execute(text(sql))
                            logger.info(f"成功向表 '{table_name}' 添加列 '{column_name}'。")

                    await connection.run_sync(add_columns_sync)
                    # 提交列添加事务
                    await connection.commit()
                else:
                    logger.info(f"表 '{table_name}' 的列结构一致。")

                # 检查并创建缺失的索引
                db_indexes = await connection.run_sync(
                    lambda conn: {
                        idx["name"] for idx in inspector.get_indexes(table_name)
                    }
                )
                model_indexes = {idx.name for idx in table.indexes}
                missing_indexes = model_indexes - db_indexes

                if missing_indexes:
                    logger.info(
                        f"在表 '{table_name}' 中发现缺失的索引: {', '.join(missing_indexes)}"
                    )

                    def add_indexes_sync(conn):
                        for index_name in missing_indexes:
                            index_obj = next(
                                (idx for idx in table.indexes if idx.name == index_name),
                                None,
                            )
                            if index_obj is not None:
                                index_obj.create(conn)
                                logger.info(
                                    f"成功为表 '{table_name}' 创建索引 '{index_name}'。"
                                )

                    await connection.run_sync(add_indexes_sync)
                    # 提交索引创建事务
                    await connection.commit()
                else:
                    logger.debug(f"表 '{table_name}' 的索引一致。")

            except Exception as e:
                logger.error(f"在处理表 '{table_name}' 时发生意外错误: {e}", exc_info=True)
                await connection.rollback()
                continue

    logger.info("数据库结构检查与自动迁移完成。")


async def create_all_tables(existing_engine=None):
    """创建所有表（不进行迁移检查）
    
    直接创建所有在 Base.metadata 中定义的表。
    如果表已存在，将被跳过。
    
    Args:
        existing_engine: 可选的已存在的数据库引擎
        
    Note:
        生产环境建议使用 check_and_migrate_database()
    """
    logger.info("正在创建所有数据库表...")
    engine = existing_engine if existing_engine is not None else await get_engine()
    
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    
    logger.info("数据库表创建完成。")


async def drop_all_tables(existing_engine=None):
    """删除所有表（危险操作！）
    
    删除所有在 Base.metadata 中定义的表。
    
    Args:
        existing_engine: 可选的已存在的数据库引擎
        
    Warning:
        此操作将删除所有数据，不可恢复！仅用于测试环境！
    """
    logger.warning("⚠️  正在删除所有数据库表...")
    engine = existing_engine if existing_engine is not None else await get_engine()
    
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
    
    logger.warning("所有数据库表已删除。")
