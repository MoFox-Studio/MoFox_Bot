"""Napcat Adapter 插件数据库层 (基于主程序异步SQLAlchemy API)

本模块替换原先的 sqlmodel + 同步Session 实现:
1. 复用主项目的异步数据库连接与迁移体系
2. 提供与旧接口名兼容的方法(update_ban_record/create_ban_record/delete_ban_record)
3. 新增首选异步方法: update_ban_records / create_or_update / delete_record / get_ban_records

数据语义:
    user_id == 0 表示群全体禁言

注意: 所有方法均为异步, 需要在 async 上下文中调用。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence

from sqlalchemy import BigInteger, Column, Index, Integer, UniqueConstraint, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.common.database.core.models import Base
from src.common.database.core import get_db_session
from src.common.logger import get_logger

logger = get_logger("napcat_adapter")


class NapcatBanRecord(Base):
    __tablename__ = "napcat_ban_records"

    id = Column(Integer, primary_key=True, autoincrement=True)
    group_id = Column(BigInteger, nullable=False, index=True)
    user_id = Column(BigInteger, nullable=False, index=True)  # 0 == 全体禁言
    lift_time = Column(BigInteger, nullable=True)  # -1 / None 表示未知/永久

    __table_args__ = (
        UniqueConstraint("group_id", "user_id", name="uq_napcat_group_user"),
        Index("idx_napcat_ban_group", "group_id"),
        Index("idx_napcat_ban_user", "user_id"),
    )


@dataclass
class BanUser:
    user_id: int
    group_id: int
    lift_time: Optional[int] = -1

    def identity(self) -> tuple[int, int]:
        return self.group_id, self.user_id


class NapcatDatabase:
    async def _fetch_all(self, session: AsyncSession) -> Sequence[NapcatBanRecord]:
        result = await session.execute(select(NapcatBanRecord))
        return result.scalars().all()

    async def get_ban_records(self) -> List[BanUser]:
        async with get_db_session() as session:
            rows = await self._fetch_all(session)
            return [BanUser(group_id=r.group_id, user_id=r.user_id, lift_time=r.lift_time) for r in rows]

    async def update_ban_records(self, ban_list: List[BanUser]) -> None:
        target_map = {b.identity(): b for b in ban_list}
        async with get_db_session() as session:
            rows = await self._fetch_all(session)
            existing_map = {(r.group_id, r.user_id): r for r in rows}

            changed = 0
            for ident, ban in target_map.items():
                if ident in existing_map:
                    row = existing_map[ident]
                    if row.lift_time != ban.lift_time:
                        row.lift_time = ban.lift_time
                        changed += 1
                else:
                    session.add(
                        NapcatBanRecord(group_id=ban.group_id, user_id=ban.user_id, lift_time=ban.lift_time)
                    )
                    changed += 1

            removed = 0
            for ident, row in existing_map.items():
                if ident not in target_map:
                    await session.delete(row)
                    removed += 1

            logger.debug(
                f"Napcat ban list sync => total_incoming={len(ban_list)} created_or_updated={changed} removed={removed}"
            )

    async def create_or_update(self, ban_record: BanUser) -> None:
        async with get_db_session() as session:
            stmt = select(NapcatBanRecord).where(
                NapcatBanRecord.group_id == ban_record.group_id,
                NapcatBanRecord.user_id == ban_record.user_id,
            )
            result = await session.execute(stmt)
            row = result.scalars().first()
            if row:
                if row.lift_time != ban_record.lift_time:
                    row.lift_time = ban_record.lift_time
                    logger.debug(
                        f"更新禁言记录 group={ban_record.group_id} user={ban_record.user_id} lift={ban_record.lift_time}"
                    )
            else:
                session.add(
                    NapcatBanRecord(
                        group_id=ban_record.group_id, user_id=ban_record.user_id, lift_time=ban_record.lift_time
                    )
                )
                logger.debug(
                    f"创建禁言记录 group={ban_record.group_id} user={ban_record.user_id} lift={ban_record.lift_time}"
                )

    async def delete_record(self, ban_record: BanUser) -> None:
        async with get_db_session() as session:
            stmt = select(NapcatBanRecord).where(
                NapcatBanRecord.group_id == ban_record.group_id,
                NapcatBanRecord.user_id == ban_record.user_id,
            )
            result = await session.execute(stmt)
            row = result.scalars().first()
            if row:
                await session.delete(row)
                logger.debug(
                    f"删除禁言记录 group={ban_record.group_id} user={ban_record.user_id} lift={row.lift_time}"
                )
            else:
                logger.info(
                    f"未找到禁言记录 group={ban_record.group_id} user={ban_record.user_id}"
                )

    # 兼容旧命名
    async def update_ban_record(self, ban_list: List[BanUser]) -> None:  # old name
        await self.update_ban_records(ban_list)

    async def create_ban_record(self, ban_record: BanUser) -> None:  # old name
        await self.create_or_update(ban_record)

    async def delete_ban_record(self, ban_record: BanUser) -> None:  # old name
        await self.delete_record(ban_record)


napcat_db = NapcatDatabase()


def is_identical(a: BanUser, b: BanUser) -> bool:
    return a.group_id == b.group_id and a.user_id == b.user_id


__all__ = [
    "BanUser",
    "NapcatBanRecord",
    "napcat_db",
    "is_identical",
]