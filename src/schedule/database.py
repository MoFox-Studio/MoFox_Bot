# mmc/src/schedule/database.py


from sqlalchemy import delete, func, select, update

from src.common.database.sqlalchemy_models import MonthlyPlan, get_db_session
from src.common.logger import get_logger
from src.config.config import global_config

logger = get_logger("schedule_database")


async def add_new_plans(plans: list[str], month: str):
    """
    批量添加新生成的月度计划到数据库，并确保不超过上限。

    :param plans: 计划内容列表。
    :param month: 目标月份，格式为 "YYYY-MM"。
    """
    async with get_db_session() as session:
        try:
            # 1. 获取当前有效计划数量（状态为 'active'）
            result = await session.execute(
                select(func.count(MonthlyPlan.id)).where(
                    MonthlyPlan.target_month == month, MonthlyPlan.status == "active"
                )
            )
            current_plan_count = result.scalar_one()

            # 2. 从配置获取上限
            max_plans = global_config.planning_system.max_plans_per_month

            # 3. 计算还能添加多少计划
            remaining_slots = max_plans - current_plan_count

            if remaining_slots <= 0:
                logger.info(f"{month} 的月度计划已达到上限 ({max_plans}条)，不再添加新计划。")
                return

            # 4. 截取可以添加的计划
            plans_to_add = plans[:remaining_slots]

            new_plan_objects = [
                MonthlyPlan(plan_text=plan, target_month=month, status="active") for plan in plans_to_add
            ]
            session.add_all(new_plan_objects)
            await session.commit()

            logger.info(f"成功向数据库添加了 {len(new_plan_objects)} 条 {month} 的月度计划。")
            if len(plans) > len(plans_to_add):
                logger.info(f"由于达到月度计划上限，有 {len(plans) - len(plans_to_add)} 条计划未被添加。")

        except Exception as e:
            logger.error(f"添加月度计划时发生错误: {e}")
            await session.rollback()
            raise


async def get_active_plans_for_month(month: str) -> list[MonthlyPlan]:
    """
    获取指定月份所有状态为 'active' 的计划。

    :param month: 目标月份，格式为 "YYYY-MM"。
    :return: MonthlyPlan 对象列表。
    """
    async with get_db_session() as session:
        try:
            result = await session.execute(
                select(MonthlyPlan)
                .where(MonthlyPlan.target_month == month, MonthlyPlan.status == "active")
                .order_by(MonthlyPlan.created_at.desc())
            )
            return list(result.scalars().all())
        except Exception as e:
            logger.error(f"查询 {month} 的有效月度计划时发生错误: {e}")
            return []


async def mark_plans_completed(plan_ids: list[int]):
    """
    将指定ID的计划标记为已完成。

    :param plan_ids: 需要标记为完成的计划ID列表。
    """
    if not plan_ids:
        return

    async with get_db_session() as session:
        try:
            result = await session.execute(select(MonthlyPlan).where(MonthlyPlan.id.in_(plan_ids)))
            plans_to_mark = result.scalars().all()
            if not plans_to_mark:
                logger.info("没有需要标记为完成的月度计划。")
                return

            plan_details = "\n".join([f"  {i + 1}. {plan.plan_text}" for i, plan in enumerate(plans_to_mark)])
            logger.info(f"以下 {len(plans_to_mark)} 条月度计划将被标记为已完成:\n{plan_details}")

            await session.execute(update(MonthlyPlan).where(MonthlyPlan.id.in_(plan_ids)).values(status="completed"))
            await session.commit()
        except Exception as e:
            logger.error(f"标记月度计划为完成时发生错误: {e}")
            await session.rollback()
            raise


async def delete_plans_by_ids(plan_ids: list[int]):
    """
    根据ID列表从数据库中物理删除月度计划。

    :param plan_ids: 需要删除的计划ID列表。
    """
    if not plan_ids:
        return

    async with get_db_session() as session:
        try:
            # 先查询要删除的计划，用于日志记录
            result = await session.execute(select(MonthlyPlan).where(MonthlyPlan.id.in_(plan_ids)))
            plans_to_delete = result.scalars().all()
            if not plans_to_delete:
                logger.info("没有找到需要删除的月度计划。")
                return

            plan_details = "\n".join([f"  {i + 1}. {plan.plan_text}" for i, plan in enumerate(plans_to_delete)])
            logger.info(f"检测到月度计划超额，将删除以下 {len(plans_to_delete)} 条计划:\n{plan_details}")

            # 执行删除
            await session.execute(delete(MonthlyPlan).where(MonthlyPlan.id.in_(plan_ids)))
            await session.commit()

        except Exception as e:
            logger.error(f"删除月度计划时发生错误: {e}")
            await session.rollback()
            raise


async def update_plan_usage(plan_ids: list[int], used_date: str):
    """
    更新计划的使用统计信息。

    :param plan_ids: 使用的计划ID列表。
    :param used_date: 使用日期，格式为 "YYYY-MM-DD"。
    """
    if not plan_ids:
        return

    async with get_db_session() as session:
        try:
            # 获取完成阈值配置，如果不存在则使用默认值
            completion_threshold = getattr(global_config.planning_system, "completion_threshold", 3)

            # 批量更新使用次数和最后使用日期
            await session.execute(
                update(MonthlyPlan)
                .where(MonthlyPlan.id.in_(plan_ids))
                .values(usage_count=MonthlyPlan.usage_count + 1, last_used_date=used_date)
            )

            # 检查是否有计划达到完成阈值
            result = await session.execute(
                select(MonthlyPlan).where(
                    MonthlyPlan.id.in_(plan_ids),
                    MonthlyPlan.usage_count >= completion_threshold,
                    MonthlyPlan.status == "active",
                )
            )
            plans_to_complete = result.scalars().all()

            if plans_to_complete:
                completed_ids = [plan.id for plan in plans_to_complete]
                await session.execute(
                    update(MonthlyPlan).where(MonthlyPlan.id.in_(completed_ids)).values(status="completed")
                )

                logger.info(f"计划 {completed_ids} 已达到使用阈值 ({completion_threshold})，标记为已完成。")

            await session.commit()
            logger.info(f"成功更新了 {len(plan_ids)} 条月度计划的使用统计。")
        except Exception as e:
            logger.error(f"更新月度计划使用统计时发生错误: {e}")
            await session.rollback()
            raise


async def get_smart_plans_for_daily_schedule(month: str, max_count: int = 3, avoid_days: int = 7) -> list[MonthlyPlan]:
    """
    智能抽取月度计划用于每日日程生成。

    抽取规则：
    1. 避免短期内重复（avoid_days 天内不重复抽取同一个计划）
    2. 优先抽取使用次数较少的计划
    3. 在满足以上条件的基础上随机抽取

    :param month: 目标月份，格式为 "YYYY-MM"。
    :param max_count: 最多抽取的计划数量。
    :param avoid_days: 避免重复的天数。
    :return: MonthlyPlan 对象列表。
    """
    from datetime import datetime, timedelta

    async with get_db_session() as session:
        try:
            # 计算避免重复的日期阈值
            avoid_date = (datetime.now() - timedelta(days=avoid_days)).strftime("%Y-%m-%d")

            # 查询符合条件的计划
            query = select(MonthlyPlan).where(MonthlyPlan.target_month == month, MonthlyPlan.status == "active")

            # 排除最近使用过的计划
            query = query.where((MonthlyPlan.last_used_date.is_(None)) | (MonthlyPlan.last_used_date < avoid_date))

            # 按使用次数升序排列，优先选择使用次数少的
            result = await session.execute(query.order_by(MonthlyPlan.usage_count.asc()))
            plans = result.scalars().all()

            if not plans:
                logger.info(f"没有找到符合条件的 {month} 月度计划。")
                return []

            # 如果计划数量超过需要的数量，进行随机抽取
            if len(plans) > max_count:
                import random

                plans = random.sample(plans, max_count)

            logger.info(f"智能抽取了 {len(plans)} 条 {month} 的月度计划用于每日日程生成。")
            return list(plans)

        except Exception as e:
            logger.error(f"智能抽取 {month} 的月度计划时发生错误: {e}")
            return []


async def archive_active_plans_for_month(month: str):
    """
    将指定月份所有状态为 'active' 的计划归档为 'archived'。
    通常在月底调用。

    :param month: 目标月份，格式为 "YYYY-MM"。
    """
    async with get_db_session() as session:
        try:
            result = await session.execute(
                update(MonthlyPlan)
                .where(MonthlyPlan.target_month == month, MonthlyPlan.status == "active")
                .values(status="archived")
            )
            updated_count = result.rowcount
            await session.commit()
            logger.info(f"成功将 {updated_count} 条 {month} 的活跃月度计划归档。")
            return updated_count
        except Exception as e:
            logger.error(f"归档 {month} 的月度计划时发生错误: {e}")
            await session.rollback()
            raise


async def get_archived_plans_for_month(month: str) -> list[MonthlyPlan]:
    """
    获取指定月份所有状态为 'archived' 的计划。
    用于生成下个月计划时的参考。

    :param month: 目标月份，格式为 "YYYY-MM"。
    :return: MonthlyPlan 对象列表。
    """
    async with get_db_session() as session:
        try:
            result = await session.execute(
                select(MonthlyPlan).where(MonthlyPlan.target_month == month, MonthlyPlan.status == "archived")
            )
            return list(result.scalars().all())
        except Exception as e:
            logger.error(f"查询 {month} 的归档月度计划时发生错误: {e}")
            return []


async def has_active_plans(month: str) -> bool:
    """
    检查指定月份是否存在任何状态为 'active' 的计划。

    :param month: 目标月份，格式为 "YYYY-MM"。
    :return: 如果存在则返回 True，否则返回 False。
    """
    async with get_db_session() as session:
        try:
            result = await session.execute(
                select(func.count(MonthlyPlan.id)).where(
                    MonthlyPlan.target_month == month, MonthlyPlan.status == "active"
                )
            )
            return result.scalar_one() > 0
        except Exception as e:
            logger.error(f"检查 {month} 的有效月度计划时发生错误: {e}")
            return False


async def delete_plans_older_than(month: str):
    """
    删除指定月份之前的所有月度计划。

    :param month: 目标月份，格式为 "YYYY-MM"。早于此月份的计划都将被删除。
    """
    async with get_db_session() as session:
        try:
            # 首先，查询要删除的计划，用于日志记录
            result = await session.execute(select(MonthlyPlan).where(MonthlyPlan.target_month < month))
            plans_to_delete = result.scalars().all()

            if not plans_to_delete:
                logger.info(f"没有找到比 {month} 更早的月度计划需要删除。")
                return 0

            plan_months = sorted(list(set(p.target_month for p in plans_to_delete)))
            logger.info(f"将删除 {len(plans_to_delete)} 条早于 {month} 的月度计划 (涉及月份: {', '.join(plan_months)})。")

            # 然后，执行删除操作
            delete_stmt = delete(MonthlyPlan).where(MonthlyPlan.target_month < month)
            delete_result = await session.execute(delete_stmt)
            deleted_count = delete_result.rowcount
            await session.commit()

            logger.info(f"成功删除了 {deleted_count} 条旧的月度计划。")
            return deleted_count

        except Exception as e:
            logger.error(f"删除早于 {month} 的月度计划时发生错误: {e}")
            await session.rollback()
            raise
