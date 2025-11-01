import copy
import datetime
import hashlib
import time
from collections.abc import Callable
from typing import Any

import orjson
from json_repair import repair_json
from sqlalchemy import select

from src.common.database.compatibility import get_db_session
from src.common.database.core.models import PersonInfo
from src.common.database.api.crud import CRUDBase
from src.common.database.utils.decorators import cached
from src.common.logger import get_logger
from src.config.config import global_config, model_config
from src.llm_models.utils_model import LLMRequest

"""
PersonInfoManager 类方法功能摘要：
1. get_person_id - 根据平台和用户ID生成MD5哈希的唯一person_id
2. create_person_info - 创建新个人信息文档（自动合并默认值）
3. update_one_field - 更新单个字段值（若文档不存在则创建）
4. del_one_document - 删除指定person_id的文档
5. get_value - 获取单个字段值（返回实际值或默认值）
6. get_values - 批量获取字段值（任一字段无效则返回空字典）
7. del_all_undefined_field - 清理全集合中未定义的字段
8. get_specific_value_list - 根据指定条件，返回person_id,value字典
"""


logger = get_logger("person_info")

JSON_SERIALIZED_FIELDS = ["points", "forgotten_points", "info_list"]

person_info_default = {
    "person_id": None,
    "person_name": None,
    "name_reason": None,  # Corrected from person_name_reason to match common usage if intended
    "platform": "unknown",
    "user_id": "unknown",
    "nickname": "Unknown",
    "know_times": 0,
    "know_since": None,
    "last_know": None,
    "impression": None,  # Corrected from person_impression
    "short_impression": None,
    "info_list": None,
    "points": None,
    "forgotten_points": None,
    "relation_value": None,
    "attitude": 50,
}


class PersonInfoManager:
    def __init__(self):
        """初始化PersonInfoManager"""
        self.person_name_list = {}
        self.qv_name_llm = LLMRequest(model_set=model_config.model_task_config.utils, request_type="relation.qv_name")
        # try:
        #     async with get_db_session() as session:
        #         db.connect(reuse_if_open=True)
        #         # 设置连接池参数（仅对SQLite有效）
        #         if hasattr(db, "execute_sql"):
        #             # 检查数据库类型，只对SQLite执行PRAGMA语句
        #             if global_config.database.database_type == "sqlite":
        #                 # 设置SQLite优化参数
        #                 db.execute_sql("PRAGMA cache_size = -64000")  # 64MB缓存
        #                 db.execute_sql("PRAGMA temp_store = memory")  # 临时存储在内存中
        #                 db.execute_sql("PRAGMA mmap_size = 268435456")  # 256MB内存映射
        #         db.create_tables([PersonInfo], safe=True)
        # except Exception as e:
        #         logger.error(f"数据库连接或 PersonInfo 表创建失败: {e}")

        #     # 初始化时读取所有person_name
        try:
            pass
            # 在这里获取会话
            # async with get_db_session() as session:
            #     for record in session.execute(
            #         select(PersonInfo.person_id, PersonInfo.person_name).where(PersonInfo.person_name.is_not(None))
            #     ).fetchall():
            #         if record.person_name:
            #             self.person_name_list[record.person_id] = record.person_name
            #     logger.debug(f"已加载 {len(self.person_name_list)} 个用户名称 (SQLAlchemy)")
        except Exception as e:
            logger.error(f"从 SQLAlchemy 加载 person_name_list 失败: {e}")

    @staticmethod
    def get_person_id(platform: str, user_id: int | str) -> str:
        """获取唯一id（同步）

        说明: 原来该方法为异步并在内部尝试执行数据库检查/迁移，导致在许多调用处未 await 时返回 coroutine 对象。
        为了避免将 coroutine 传递到其它同步调用（例如数据库查询条件）中，这里将方法改为同步并仅返回基于 platform 和 user_id 的 MD5 哈希值。

        注意: 这会跳过原有的 napcat->qq 迁移检查逻辑。如需保留迁移，请使用显式的、在合适时机执行的迁移任务。
        """
        # 检查platform是否为None或空
        if platform is None:
            platform = "unknown"

        if "-" in platform:
            platform = platform.split("-")[1]

        components = [platform, str(user_id)]
        key = "_".join(components)

        # 直接返回计算的 id（同步）
        return hashlib.md5(key.encode()).hexdigest()

    @cached(ttl=300, key_prefix="person_known", use_kwargs=False)
    async def is_person_known(self, platform: str, user_id: int):
        """判断是否认识某人（带5分钟缓存）"""
        person_id = self.get_person_id(platform, user_id)

        try:
            # 使用CRUD进行查询
            crud = CRUDBase(PersonInfo)
            record = await crud.get_by(person_id=person_id)
            return record is not None
        except Exception as e:
            logger.error(f"检查用户 {person_id} 是否已知时出错: {e}")
            return False

    async def get_person_id_by_person_name(self, person_name: str) -> str:
        """
        根据用户名获取用户ID（同步）

        说明: 为了避免在多个调用点将 coroutine 误传递到数据库查询中，
        此处提供一个同步实现。优先在内存缓存 `self.person_name_list` 中查找，
        若未命中则返回空字符串。若后续需要更强的一致性，可在异步上下文
        额外实现带 await 的查询方法。
        """
        try:
            # 优先使用内存缓存加速查找：self.person_name_list maps person_id -> person_name
            for pid, pname in self.person_name_list.items():
                if pname == person_name:
                    return pid

            # 未找到缓存命中，避免在同步路径中进行阻塞的数据库查询，直接返回空字符串
            return ""
        except Exception as e:
            logger.error(f"根据用户名 {person_name} 获取用户ID时出错: {e}")
            return ""

    @staticmethod
    async def first_knowing_some_one(platform: str, user_id: str, user_nickname: str, user_cardname: str):
        """判断是否认识某人"""
        person_id = PersonInfoManager.get_person_id(platform, user_id)
        # 生成唯一的 person_name
        person_info_manager = get_person_info_manager()
        unique_nickname = await person_info_manager._generate_unique_person_name(user_nickname)
        data = {
            "platform": platform,
            "user_id": user_id,
            "nickname": user_nickname,
            "konw_time": int(time.time()),
            "person_name": unique_nickname,  # 使用唯一的 person_name
        }
        # 先创建用户基本信息，使用安全创建方法避免竞态条件
        await person_info_manager._safe_create_person_info(person_id=person_id, data=data)
        # 更新昵称
        await person_info_manager.update_one_field(
            person_id=person_id, field_name="nickname", value=user_nickname, data=data
        )

    @staticmethod
    async def create_person_info(person_id: str, data: dict | None = None):
        """创建一个项"""
        if not person_id:
            logger.debug("创建失败，person_id不存在")
            return

        _person_info_default = copy.deepcopy(person_info_default)
        # 获取 SQLAlchemy 模型的所有字段名
        model_fields = [column.name for column in PersonInfo.__table__.columns]

        final_data = {"person_id": person_id}

        # Start with defaults for all model fields
        final_data.update({key: default_value for key, default_value in _person_info_default.items() if key in model_fields})

        # Override with provided data
        if data:
            final_data.update({key: value for key, value in data.items() if key in model_fields})

        # Ensure person_id is correctly set from the argument
        final_data["person_id"] = person_id
        # 你们的英文注释是何意味？

        # 检查并修复关键字段为None的情况喵
        if final_data.get("user_id") is None:
            logger.warning(f"user_id为None，使用'unknown'作为默认值 person_id={person_id}")
            final_data["user_id"] = "unknown"

        if final_data.get("platform") is None:
            logger.warning(f"platform为None，使用'unknown'作为默认值 person_id={person_id}")
            final_data["platform"] = "unknown"

        # 这里的目的是为了防止在识别出错的情况下有一个最小回退，不只是针对@消息识别成视频后的报错问题

        # Serialize JSON fields
        for key in JSON_SERIALIZED_FIELDS:
            if key in final_data:
                if isinstance(final_data[key], list | dict):
                    final_data[key] = orjson.dumps(final_data[key]).decode("utf-8")
                elif final_data[key] is None:  # Default for lists is [], store as "[]"
                    final_data[key] = orjson.dumps([]).decode("utf-8")
                # If it's already a string, assume it's valid JSON or a non-JSON string field

        async def _db_create_async(p_data: dict):
            async with get_db_session() as session:
                try:
                    new_person = PersonInfo(**p_data)
                    session.add(new_person)
                    await session.commit()
                    return True
                except Exception as e:
                    logger.error(f"创建 PersonInfo 记录 {p_data.get('person_id')} 失败 (SQLAlchemy): {e}")
                    return False

        await _db_create_async(final_data)

    @staticmethod
    async def _safe_create_person_info(person_id: str, data: dict | None = None):
        """安全地创建用户信息，处理竞态条件"""
        if not person_id:
            logger.debug("创建失败，person_id不存在")
            return

        _person_info_default = copy.deepcopy(person_info_default)
        # 获取 SQLAlchemy 模型的所有字段名
        model_fields = [column.name for column in PersonInfo.__table__.columns]

        final_data = {"person_id": person_id}

        # Start with defaults for all model fields
        final_data.update({key: default_value for key, default_value in _person_info_default.items() if key in model_fields})

        # Override with provided data
        if data:
            final_data.update({key: value for key, value in data.items() if key in model_fields})

        # Ensure person_id is correctly set from the argument
        final_data["person_id"] = person_id

        # 检查并修复关键字段为None的情况
        if final_data.get("user_id") is None:
            logger.warning(f"user_id为None，使用'unknown'作为默认值 person_id={person_id}")
            final_data["user_id"] = "unknown"

        if final_data.get("platform") is None:
            logger.warning(f"platform为None，使用'unknown'作为默认值 person_id={person_id}")
            final_data["platform"] = "unknown"

        # Serialize JSON fields
        for key in JSON_SERIALIZED_FIELDS:
            if key in final_data:
                if isinstance(final_data[key], list | dict):
                    final_data[key] = orjson.dumps(final_data[key]).decode("utf-8")
                elif final_data[key] is None:  # Default for lists is [], store as "[]"
                    final_data[key] = orjson.dumps([]).decode("utf-8")

        async def _db_safe_create_async(p_data: dict):
            try:
                # 使用CRUD进行检查和创建
                crud = CRUDBase(PersonInfo)
                existing = await crud.get_by(person_id=p_data["person_id"])
                if existing:
                    logger.debug(f"用户 {p_data['person_id']} 已存在，跳过创建")
                    return True

                # 创建新记录
                await crud.create(p_data)
                return True
            except Exception as e:
                if "UNIQUE constraint failed" in str(e):
                    logger.debug(f"检测到并发创建用户 {p_data.get('person_id')}，跳过错误")
                    return True
                else:
                    logger.error(f"创建 PersonInfo 记录 {p_data.get('person_id')} 失败: {e}")
                    return False

        await _db_safe_create_async(final_data)

    async def update_one_field(self, person_id: str, field_name: str, value, data: dict | None = None):
        """更新某一个字段，会补全"""
        # 获取 SQLAlchemy 模型的所有字段名
        model_fields = [column.name for column in PersonInfo.__table__.columns]
        if field_name not in model_fields:
            logger.debug(f"更新'{field_name}'失败，未在 PersonInfo SQLAlchemy 模型中定义的字段。")
            return

        processed_value = value
        if field_name in JSON_SERIALIZED_FIELDS:
            if isinstance(value, list | dict):
                processed_value = orjson.dumps(value).decode("utf-8")
            elif value is None:  # Store None as "[]" for JSON list fields
                processed_value = orjson.dumps([]).decode("utf-8")

        async def _db_update_async(p_id: str, f_name: str, val_to_set):
            start_time = time.time()
            try:
                # 使用CRUD进行更新
                crud = CRUDBase(PersonInfo)
                record = await crud.get_by(person_id=p_id)
                query_time = time.time()
                
                if record:
                    # 更新记录
                    await crud.update(record.id, {f_name: val_to_set})
                    save_time = time.time()
                    total_time = save_time - start_time
                    
                    if total_time > 0.5:
                        logger.warning(
                            f"数据库更新操作耗时 {total_time:.3f}秒 (查询: {query_time - start_time:.3f}s, 保存: {save_time - query_time:.3f}s) person_id={p_id}, field={f_name}"
                        )
                    
                    # 使缓存失效
                    from src.common.database.optimization.cache_manager import get_cache
                    from src.common.database.utils.decorators import generate_cache_key
                    cache = await get_cache()
                    # 使相关缓存失效
                    await cache.delete(generate_cache_key("person_value", p_id, f_name))
                    await cache.delete(generate_cache_key("person_values", p_id))
                    await cache.delete(generate_cache_key("person_has_field", p_id, f_name))
                    
                    return True, False
                else:
                    total_time = time.time() - start_time
                    if total_time > 0.5:
                        logger.warning(f"数据库查询操作耗时 {total_time:.3f}秒 person_id={p_id}, field={f_name}")
                    return False, True
            except Exception as e:
                total_time = time.time() - start_time
                logger.error(f"数据库操作异常，耗时 {total_time:.3f}秒: {e}")
                raise

        found, needs_creation = await _db_update_async(person_id, field_name, processed_value)

        if needs_creation:
            logger.info(f"{person_id} 不存在，将新建。")
            creation_data = data if data is not None else {}
            # Ensure platform and user_id are present for context if available from 'data'
            # but primarily, set the field that triggered the update.
            # The create_person_info will handle defaults and serialization.
            creation_data[field_name] = value  # Pass original value to create_person_info

            # Ensure platform and user_id are in creation_data if available,
            # otherwise create_person_info will use defaults.
            if data and "platform" in data:
                creation_data["platform"] = data["platform"]
            if data and "user_id" in data:
                creation_data["user_id"] = data["user_id"]

            # 额外检查关键字段，如果为None则使用默认值
            if creation_data.get("user_id") is None:
                logger.warning(f"创建用户时user_id为None，使用'unknown'作为默认值 person_id={person_id}")
                creation_data["user_id"] = "unknown"

            if creation_data.get("platform") is None:
                logger.warning(f"创建用户时platform为None，使用'unknown'作为默认值 person_id={person_id}")
                creation_data["platform"] = "unknown"

            # 使用安全的创建方法，处理竞态条件
            await self._safe_create_person_info(person_id, creation_data)

    @staticmethod
    @cached(ttl=300, key_prefix="person_has_field")
    async def has_one_field(person_id: str, field_name: str):
        """判断是否存在某一个字段（带5分钟缓存）"""
        # 获取 SQLAlchemy 模型的所有字段名
        model_fields = [column.name for column in PersonInfo.__table__.columns]
        if field_name not in model_fields:
            logger.debug(f"检查字段'{field_name}'失败，未在 PersonInfo SQLAlchemy 模型中定义。")
            return False

        try:
            # 使用CRUD进行查询
            crud = CRUDBase(PersonInfo)
            record = await crud.get_by(person_id=person_id)
            return bool(record)
        except Exception as e:
            logger.error(f"检查字段 {field_name} for {person_id} 时出错: {e}")
            return False

    @staticmethod
    def _extract_json_from_text(text: str) -> dict:
        """从文本中提取JSON数据的高容错方法"""
        try:
            fixed_json = repair_json(text)
            if isinstance(fixed_json, str):
                parsed_json = orjson.loads(fixed_json)
            else:
                parsed_json = fixed_json

            if isinstance(parsed_json, list) and parsed_json:
                parsed_json = parsed_json[0]

            if isinstance(parsed_json, dict):
                return parsed_json

        except Exception as e:
            logger.warning(f"JSON提取失败: {e}")

        logger.warning(f"无法从文本中提取有效的JSON字典: {text}")
        logger.info(f"文本: {text}")
        return {"nickname": "", "reason": ""}

    async def _generate_unique_person_name(self, base_name: str) -> str:
        """生成唯一的 person_name，如果存在重复则添加数字后缀"""
        # 处理空昵称的情况
        if not base_name or base_name.isspace():
            base_name = "空格"

        # 检查基础名称是否已存在
        if base_name not in self.person_name_list.values():
            return base_name

        # 如果存在，添加数字后缀
        counter = 1
        while True:
            new_name = f"{base_name}[{counter}]"
            if new_name not in self.person_name_list.values():
                return new_name
            counter += 1

    async def qv_person_name(
        self, person_id: str, user_nickname: str, user_cardname: str, user_avatar: str, request: str = ""
    ):
        """给某个用户取名"""
        if not person_id:
            logger.debug("取名失败：person_id不能为空")
            return None

        old_name = await self.get_value(person_id, "person_name")
        old_reason = await self.get_value(person_id, "name_reason")

        max_retries = 8
        current_try = 0
        existing_names_str = ""
        current_name_set = set(self.person_name_list.values())

        while current_try < max_retries:
            # prompt_personality =get_individuality().get_prompt(x_person=2, level=1)
            bot_name = global_config.bot.nickname

            qv_name_prompt = f"你是{bot_name}，一个伪装成人类的AI，你不能让别人发现这一点，"
            qv_name_prompt += f"现在你想给一个用户取一个昵称，用户的qq昵称是{user_nickname}，"
            qv_name_prompt += f"用户的qq群昵称名是{user_cardname}，"
            if user_avatar:
                qv_name_prompt += f"用户的qq头像是{user_avatar}，"
            if old_name:
                qv_name_prompt += f"你之前叫他{old_name}，是因为{old_reason}，"

            qv_name_prompt += f"\n其他取名的要求是：{request}，不要太浮夸，简短，"
            qv_name_prompt += "\n请根据以上用户信息，想想你叫他什么比较好，不要太浮夸，请最好使用用户的qq昵称或群昵称原文，可以稍作修改，优先使用原文。优先使用用户的qq昵称或者群昵称原文。"

            if existing_names_str:
                qv_name_prompt += f"\n请注意，以下名称已被你尝试过或已知存在，请避免：{existing_names_str}。\n"

            if len(current_name_set) < 50 and current_name_set:
                qv_name_prompt += f"已知的其他昵称有: {', '.join(list(current_name_set)[:10])}等。\n"

            qv_name_prompt += "请用json给出你的想法，并给出理由，示例如下："
            qv_name_prompt += """{
                "nickname": "昵称",
                "reason": "理由"
            }"""
            response, _ = await self.qv_name_llm.generate_response_async(qv_name_prompt)
            # logger.info(f"取名提示词：{qv_name_prompt}\n取名回复：{response}")
            result = self._extract_json_from_text(response)

            if not result or not result.get("nickname"):
                logger.error("生成的昵称为空或结果格式不正确，重试中...")
                current_try += 1
                continue

            generated_nickname = result["nickname"]

            is_duplicate = False
            if generated_nickname in current_name_set:
                is_duplicate = True
                logger.info(f"尝试给用户{user_nickname} {person_id} 取名，但是 {generated_nickname} 已存在，重试中...")
            else:

                async def _db_check_name_exists_async(name_to_check):
                    async with get_db_session() as session:
                        result = await session.execute(
                            select(PersonInfo).where(PersonInfo.person_name == name_to_check)
                        )
                        record = result.scalar()
                        return record is not None

                if await _db_check_name_exists_async(generated_nickname):
                    is_duplicate = True
                    current_name_set.add(generated_nickname)

            if not is_duplicate:
                await self.update_one_field(person_id, "person_name", generated_nickname)
                await self.update_one_field(person_id, "name_reason", result.get("reason", "未提供理由"))

                logger.info(
                    f"成功给用户{user_nickname} {person_id} 取名 {generated_nickname}，理由：{result.get('reason', '未提供理由')}"
                )

                self.person_name_list[person_id] = generated_nickname
                return result
            else:
                if existing_names_str:
                    existing_names_str += "、"
                existing_names_str += generated_nickname
                logger.debug(f"生成的昵称 {generated_nickname} 已存在，重试中...")
                current_try += 1

        # 如果多次尝试后仍未成功，使用唯一的 user_nickname 作为默认值
        unique_nickname = await self._generate_unique_person_name(user_nickname)
        logger.warning(f"在{max_retries}次尝试后未能生成唯一昵称，使用默认昵称 {unique_nickname}")
        await self.update_one_field(person_id, "person_name", unique_nickname)
        await self.update_one_field(person_id, "name_reason", "使用用户原始昵称作为默认值")
        self.person_name_list[person_id] = unique_nickname
        return {"nickname": unique_nickname, "reason": "使用用户原始昵称作为默认值"}

    @staticmethod
    async def del_one_document(person_id: str):
        """删除指定 person_id 的文档"""
        if not person_id:
            logger.debug("删除失败：person_id 不能为空")
            return

        async def _db_delete_async(p_id: str):
            try:
                # 使用CRUD进行删除
                crud = CRUDBase(PersonInfo)
                record = await crud.get_by(person_id=p_id)
                if record:
                    await crud.delete(record.id)
                    
                    # 清除相关缓存
                    from src.common.database.optimization.cache_manager import get_cache
                    from src.common.database.utils.decorators import generate_cache_key
                    cache = await get_cache()
                    
                    # 清除所有相关的person缓存
                    await cache.delete(generate_cache_key("person_known", p_id))
                    await cache.delete(generate_cache_key("person_field", p_id))
                    return 1
                return 0
            except Exception as e:
                logger.error(f"删除 PersonInfo {p_id} 失败: {e}")
                return 0

        deleted_count = await _db_delete_async(person_id)

        if deleted_count > 0:
            logger.debug(f"删除成功：person_id={person_id}")
        else:
            logger.debug(f"删除失败：未找到 person_id={person_id} 或删除未影响行")

    @staticmethod
    @cached(ttl=600, key_prefix="person_value")
    async def get_value(person_id: str, field_name: str) -> Any:
        """获取单个字段值（带10分钟缓存）"""
        if not person_id:
            logger.debug("get_value获取失败：person_id不能为空")
            return None

        # 使用CRUD进行查询
        crud = CRUDBase(PersonInfo)
        record = await crud.get_by(person_id=person_id)

        model_fields = [column.name for column in PersonInfo.__table__.columns]

        if field_name not in model_fields:
            if field_name in person_info_default:
                logger.debug(f"字段'{field_name}'不在SQLAlchemy模型中，使用默认配置值。")
                return copy.deepcopy(person_info_default[field_name])
            else:
                logger.debug(f"get_value查询失败：字段'{field_name}'未在SQLAlchemy模型和默认配置中定义。")
                return None

        if record:
            value = getattr(record, field_name)
            if value is not None:
                return value
            else:
                return copy.deepcopy(person_info_default.get(field_name))
        else:
            return copy.deepcopy(person_info_default.get(field_name))

    @staticmethod
    @cached(ttl=600, key_prefix="person_values")
    async def get_values(person_id: str, field_names: list) -> dict:
        """获取指定person_id文档的多个字段值（带10分钟缓存）"""
        if not person_id:
            logger.debug("get_values获取失败：person_id不能为空")
            return {}

        result = {}

        # 使用CRUD进行查询
        crud = CRUDBase(PersonInfo)
        record = await crud.get_by(person_id=person_id)

        # 获取 SQLAlchemy 模型的所有字段名
        model_fields = [column.name for column in PersonInfo.__table__.columns]

        for field_name in field_names:
            if field_name not in model_fields:
                if field_name in person_info_default:
                    result[field_name] = copy.deepcopy(person_info_default[field_name])
                    logger.debug(f"字段'{field_name}'不在SQLAlchemy模型中，使用默认配置值。")
                else:
                    logger.debug(f"get_values查询失败：字段'{field_name}'未在SQLAlchemy模型和默认配置中定义。")
                    result[field_name] = None
                continue

            if record:
                value = getattr(record, field_name)
                if value is not None:
                    result[field_name] = value
                else:
                    result[field_name] = copy.deepcopy(person_info_default.get(field_name))
            else:
                result[field_name] = copy.deepcopy(person_info_default.get(field_name))

        return result

    @staticmethod
    async def get_specific_value_list(
        field_name: str,
        way: Callable[[Any], bool],
    ) -> dict[str, Any]:
        """
        获取满足条件的字段值字典
        """
        # 获取 SQLAlchemy 模型的所有字段名
        model_fields = [column.name for column in PersonInfo.__table__.columns]
        if field_name not in model_fields:
            logger.error(f"字段检查失败：'{field_name}'未在 PersonInfo SQLAlchemy 模型中定义")
            return {}

        async def _db_get_specific_async(f_name: str):
            found_results = {}
            try:
                # 使用CRUD获取所有记录
                crud = CRUDBase(PersonInfo)
                all_records = await crud.get_all()
                for record in all_records:
                    value = getattr(record, f_name, None)
                    if value is not None and way(value):
                        found_results[record.person_id] = value
            except Exception as e_query:
                logger.error(
                    f"数据库查询失败 (specific_value_list for {f_name}): {e_query!s}", exc_info=True
                )
            return found_results

        try:
            return await _db_get_specific_async(field_name)
        except Exception as e:
            logger.error(f"执行 get_specific_value_list 时出错: {e!s}", exc_info=True)
            return {}

    async def get_or_create_person(
        self, platform: str, user_id: int, nickname: str, user_cardname: str, user_avatar: str | None = None
    ) -> str:
        """
        根据 platform 和 user_id 获取 person_id。
        如果对应的用户不存在，则使用提供的可选信息创建新用户。
        使用try-except处理竞态条件，避免重复创建错误。
        """
        person_id = self.get_person_id(platform, user_id)

        async def _db_get_or_create_async(p_id: str, init_data: dict):
            """原子性的获取或创建操作"""
            # 使用CRUD进行获取或创建
            crud = CRUDBase(PersonInfo)
            
            # 首先尝试获取现有记录
            record = await crud.get_by(person_id=p_id)
            if record:
                return record, False  # 记录存在，未创建

            # 记录不存在，尝试创建
            try:
                new_person = await crud.create(init_data)
                return new_person, True  # 创建成功
            except Exception as e:
                # 如果创建失败（可能是因为竞态条件），再次尝试获取
                if "UNIQUE constraint failed" in str(e):
                    logger.debug(f"检测到并发创建用户 {p_id}，获取现有记录")
                    record = await crud.get_by(person_id=p_id)
                    if record:
                        return record, False  # 其他协程已创建，返回现有记录
                # 如果仍然失败，重新抛出异常
                raise e

        unique_nickname = await self._generate_unique_person_name(nickname)
        initial_data = {
            "person_id": person_id,
            "platform": platform,
            "user_id": str(user_id),
            "nickname": nickname,
            "person_name": unique_nickname,
            "name_reason": "从群昵称获取",
            "know_times": 0,
            "know_since": int(datetime.datetime.now().timestamp()),
            "last_know": int(datetime.datetime.now().timestamp()),
            "impression": None,
            "points": [],
            "forgotten_points": [],
        }

        for key in JSON_SERIALIZED_FIELDS:
            if key in initial_data:
                if isinstance(initial_data[key], list | dict):
                    initial_data[key] = orjson.dumps(initial_data[key]).decode("utf-8")
                elif initial_data[key] is None:
                    initial_data[key] = orjson.dumps([]).decode("utf-8")

        model_fields = [column.name for column in PersonInfo.__table__.columns]
        filtered_initial_data = {k: v for k, v in initial_data.items() if v is not None and k in model_fields}

        record, was_created = await _db_get_or_create_async(person_id, filtered_initial_data)

        if was_created:
            logger.info(f"用户 {platform}:{user_id} (person_id: {person_id}) 不存在，将创建新记录。")
            logger.info(f"已为 {person_id} 创建新记录，初始数据: {filtered_initial_data}")
        else:
            logger.debug(f"用户 {platform}:{user_id} (person_id: {person_id}) 已存在，返回现有记录。")

        return person_id

    async def get_person_info_by_name(self, person_name: str) -> dict | None:
        """根据 person_name 查找用户并返回基本信息 (如果找到)"""
        if not person_name:
            logger.debug("get_person_info_by_name 获取失败：person_name 不能为空")
            return None

        found_person_id = None
        for pid, name_in_cache in self.person_name_list.items():
            if name_in_cache == person_name:
                found_person_id = pid
                break

        if not found_person_id:

            # 使用CRUD进行查询
            crud = CRUDBase(PersonInfo)
            record = await crud.get_by(person_name=person_name)
            if record:
                found_person_id = record.person_id
                if (
                    found_person_id not in self.person_name_list
                    or self.person_name_list[found_person_id] != person_name
                ):
                    self.person_name_list[found_person_id] = person_name
            else:
                logger.debug(f"数据库中也未找到名为 '{person_name}' 的用户 (Peewee)")
                return None

        if found_person_id:
            required_fields = [
                "person_id",
                "platform",
                "user_id",
                "nickname",
                "user_cardname",
                "user_avatar",
                "person_name",
                "name_reason",
            ]
            # 获取 SQLAlchemy 模型的所有字段名
            model_fields = [column.name for column in PersonInfo.__table__.columns]
            valid_fields_to_get = [f for f in required_fields if f in model_fields or f in person_info_default]

            person_data = await self.get_values(found_person_id, valid_fields_to_get)

            if person_data:
                final_result = {key: person_data.get(key) for key in required_fields}
                return final_result
            else:
                logger.warning(f"找到了 person_id '{found_person_id}' 但 get_values 返回空 (Peewee)")
                return None

        logger.error(f"逻辑错误：未能为 '{person_name}' 确定 person_id (Peewee)")
        return None


person_info_manager = None


def get_person_info_manager():
    global person_info_manager
    if person_info_manager is None:
        person_info_manager = PersonInfoManager()
    return person_info_manager
