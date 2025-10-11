"""
QQ空间服务模块
封装了所有与QQ空间API的直接交互，是插件的核心业务逻辑层。
"""

import asyncio
import base64
import os
import random
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import aiohttp
import bs4
import json5
import orjson

from src.common.logger import get_logger
from src.plugin_system.apis import config_api, cross_context_api, person_api

from .content_service import ContentService
from .cookie_service import CookieService
from .image_service import ImageService
from .reply_tracker_service import ReplyTrackerService

logger = get_logger("MaiZone.QZoneService")


class QZoneService:
    """
    QQ空间服务类，负责所有API交互和业务流程编排。
    """

    # --- API Endpoints ---
    ZONE_LIST_URL = "https://user.qzone.qq.com/proxy/domain/ic2.qzone.qq.com/cgi-bin/feeds/feeds3_html_more"
    EMOTION_PUBLISH_URL = "https://user.qzone.qq.com/proxy/domain/taotao.qzone.qq.com/cgi-bin/emotion_cgi_publish_v6"
    DOLIKE_URL = "https://user.qzone.qq.com/proxy/domain/w.qzone.qq.com/cgi-bin/likes/internal_dolike_app"
    COMMENT_URL = "https://user.qzone.qq.com/proxy/domain/taotao.qzone.qq.com/cgi-bin/emotion_cgi_re_feeds"
    LIST_URL = "https://user.qzone.qq.com/proxy/domain/taotao.qq.com/cgi-bin/emotion_cgi_msglist_v6"
    REPLY_URL = "https://user.qzone.qq.com/proxy/domain/taotao.qzone.qq.com/cgi-bin/emotion_cgi_re_feeds"

    def __init__(
        self,
        get_config: Callable,
        content_service: ContentService,
        image_service: ImageService,
        cookie_service: CookieService,
        reply_tracker: ReplyTrackerService = None,
    ):
        self.get_config = get_config
        self.content_service = content_service
        self.image_service = image_service
        self.cookie_service = cookie_service
        # 如果没有提供 reply_tracker 实例，则创建一个新的
        self.reply_tracker = reply_tracker if reply_tracker is not None else ReplyTrackerService()
        # 用于防止并发回复/评论的内存锁
        self.processing_comments = set()

    # --- Public Methods (High-Level Business Logic) ---

    async def send_feed(self, topic: str, stream_id: str | None) -> dict[str, Any]:
        """发送一条说说"""
        # --- 获取互通组上下文 ---
        context = await self._get_intercom_context(stream_id) if stream_id else None

        story = await self.content_service.generate_story(topic, context=context)
        if not story:
            return {"success": False, "message": "生成说说内容失败"}

        await self.image_service.generate_images_for_story(story)

        qq_account = config_api.get_global_config("bot.qq_account", "")
        api_client = await self._get_api_client(qq_account, stream_id)
        if not api_client:
            return {"success": False, "message": "获取QZone API客户端失败"}

        image_dir = self.get_config("send.image_directory")
        images_bytes = self._load_local_images(image_dir)

        try:
            success, _ = await api_client["publish"](story, images_bytes)
            if success:
                return {"success": True, "message": story}
            return {"success": False, "message": "发布说说至QQ空间失败"}
        except Exception as e:
            logger.error(f"发布说说时发生异常: {e}", exc_info=True)
            return {"success": False, "message": f"发布说说异常: {e}"}

    async def send_feed_from_activity(self, activity: str) -> dict[str, Any]:
        """根据日程活动发送一条说说"""
        story = await self.content_service.generate_story_from_activity(activity)
        if not story:
            return {"success": False, "message": "根据活动生成说说内容失败"}

        await self.image_service.generate_images_for_story(story)

        qq_account = config_api.get_global_config("bot.qq_account", "")
        # 注意：定时任务通常在后台运行，没有特定的用户会话，因此 stream_id 为 None
        api_client = await self._get_api_client(qq_account, stream_id=None)
        if not api_client:
            return {"success": False, "message": "获取QZone API客户端失败"}

        image_dir = self.get_config("send.image_directory")
        images_bytes = self._load_local_images(image_dir)

        try:
            success, _ = await api_client["publish"](story, images_bytes)
            if success:
                return {"success": True, "message": story}
            return {"success": False, "message": "发布说说至QQ空间失败"}
        except Exception as e:
            logger.error(f"根据活动发布说说时发生异常: {e}", exc_info=True)
            return {"success": False, "message": f"发布说说异常: {e}"}

    async def read_and_process_feeds(self, target_name: str, stream_id: str | None) -> dict[str, Any]:
        """读取并处理指定好友的说说"""
        target_person_id = await person_api.get_person_id_by_name(target_name)
        if not target_person_id:
            return {"success": False, "message": f"找不到名为'{target_name}'的好友"}
        target_qq = await person_api.get_person_value(target_person_id, "user_id")
        if not target_qq:
            return {"success": False, "message": f"好友'{target_name}'没有关联QQ号"}

        qq_account = config_api.get_global_config("bot.qq_account", "")
        api_client = await self._get_api_client(qq_account, stream_id)
        if not api_client:
            return {"success": False, "message": "获取QZone API客户端失败"}

        num_to_read = self.get_config("read.read_number", 5)
        try:
            feeds = await api_client["list_feeds"](target_qq, num_to_read)
            if not feeds:
                return {"success": True, "message": f"没有从'{target_name}'的空间获取到新说说。"}

            for feed in feeds:
                await self._process_single_feed(feed, api_client, target_qq, target_name)
                await asyncio.sleep(random.uniform(3, 7))

            return {"success": True, "message": f"成功处理了'{target_name}'的 {len(feeds)} 条说说。"}
        except Exception as e:
            logger.error(f"读取和处理说说时发生异常: {e}", exc_info=True)
            return {"success": False, "message": f"处理说说异常: {e}"}

    async def monitor_feeds(self, stream_id: str | None = None):
        """监控并处理所有好友的动态，包括回复自己说说的评论"""
        logger.info("开始执行好友动态监控...")
        qq_account = config_api.get_global_config("bot.qq_account", "")
        api_client = await self._get_api_client(qq_account, stream_id)
        if not api_client:
            logger.error("监控失败：无法获取API客户端")
            return

        try:
            # --- 第一步: 单独处理自己说说的评论 ---
            if self.get_config("monitor.enable_auto_reply", False):
                try:
                    # 传入新参数，表明正在检查自己的说说
                    own_feeds = await api_client["list_feeds"](qq_account, 5)
                    if own_feeds:
                        logger.info(f"获取到自己 {len(own_feeds)} 条说说，检查评论...")
                        for feed in own_feeds:
                            await self._reply_to_own_feed_comments(feed, api_client)
                            await asyncio.sleep(random.uniform(3, 5))
                except Exception as e:
                    logger.error(f"处理自己说说评论时发生异常: {e}", exc_info=True)

            # --- 第二步: 处理好友的动态 ---
            friend_feeds = await api_client["monitor_list_feeds"](20)
            if not friend_feeds:
                logger.info("监控完成：未发现好友新说说")
                return

            logger.info(f"监控任务: 发现 {len(friend_feeds)} 条好友新动态，准备处理...")
            for feed in friend_feeds:
                target_qq = feed.get("target_qq")
                if not target_qq or str(target_qq) == str(qq_account):  # 确保不重复处理自己的
                    continue

                await self._process_single_feed(feed, api_client, target_qq, target_qq)
                await asyncio.sleep(random.uniform(5, 10))
        except Exception as e:
            logger.error(f"监控好友动态时发生异常: {e}", exc_info=True)

    # --- Internal Helper Methods ---

    async def _get_intercom_context(self, stream_id: str) -> str | None:
        """
        获取互通组的聊天上下文。
        """
        # 实际的逻辑已迁移到 cross_context_api
        return await cross_context_api.get_intercom_group_context("maizone_context_group")

    async def _reply_to_own_feed_comments(self, feed: dict, api_client: dict):
        """处理对自己说说的评论并进行回复"""
        qq_account = config_api.get_global_config("bot.qq_account", "")
        comments = feed.get("comments", [])
        content = feed.get("content", "")
        fid = feed.get("tid", "")

        if not comments or not fid:
            return

        # 1. 将评论分为用户评论和自己的回复
        user_comments = [c for c in comments if str(c.get("qq_account")) != str(qq_account)]
        [c for c in comments if str(c.get("qq_account")) == str(qq_account)]

        if not user_comments:
            return

        # 直接检查评论是否已回复，不做验证清理
        comments_to_process = []
        for comment in user_comments:
            comment_tid = comment.get("comment_tid")
            if not comment_tid:
                continue

            comment_key = f"{fid}_{comment_tid}"
            # 检查持久化记录和内存锁
            if not self.reply_tracker.has_replied(fid, comment_tid) and comment_key not in self.processing_comments:
                logger.debug(f"锁定待回复评论: {comment_key}")
                self.processing_comments.add(comment_key)
                comments_to_process.append(comment)

        if not comments_to_process:
            logger.debug(f"说说 {fid} 下的所有评论都已回复过或正在处理中")
            return

        logger.info(f"发现自己说说下的 {len(comments_to_process)} 条新评论，准备回复...")
        for comment in comments_to_process:
            comment_tid = comment.get("comment_tid")
            comment_key = f"{fid}_{comment_tid}"
            nickname = comment.get("nickname", "")
            comment_content = comment.get("content", "")

            try:
                reply_content = await self.content_service.generate_comment_reply(content, comment_content, nickname)
                if reply_content:
                    success = await api_client["reply"](fid, qq_account, nickname, reply_content, comment_tid)
                    if success:
                        self.reply_tracker.mark_as_replied(fid, comment_tid)
                        logger.info(f"成功回复'{nickname}'的评论: '{reply_content}'")
                    else:
                        logger.error(f"回复'{nickname}'的评论失败")
                    await asyncio.sleep(random.uniform(10, 20))
                else:
                    logger.warning(f"生成回复内容失败，跳过回复'{nickname}'的评论")
            except Exception as e:
                logger.error(f"回复'{nickname}'的评论时发生异常: {e}", exc_info=True)
            finally:
                # 无论成功与否，都解除锁定
                logger.debug(f"解锁评论: {comment_key}")
                if comment_key in self.processing_comments:
                    self.processing_comments.remove(comment_key)

    async def _validate_and_cleanup_reply_records(self, fid: str, my_replies: list[dict]):
        """验证并清理已删除的回复记录"""
        # 获取当前记录中该说说的所有已回复评论ID
        recorded_replied_comments = self.reply_tracker.get_replied_comments(fid)

        if not recorded_replied_comments:
            return

        # 从API返回的我的回复中提取parent_tid（即被回复的评论ID）
        current_replied_comments = set()
        for reply in my_replies:
            parent_tid = reply.get("parent_tid")
            if parent_tid:
                current_replied_comments.add(parent_tid)

        # 找出记录中有但实际已不存在的回复
        deleted_replies = recorded_replied_comments - current_replied_comments

        if deleted_replies:
            logger.info(f"检测到 {len(deleted_replies)} 个回复已被删除，清理记录...")
            for comment_tid in deleted_replies:
                self.reply_tracker.remove_reply_record(fid, comment_tid)
                logger.debug(f"已清理删除的回复记录: feed_id={fid}, comment_id={comment_tid}")

    async def _process_single_feed(self, feed: dict, api_client: dict, target_qq: str, target_name: str):
        """处理单条说说，决定是否评论和点赞"""
        content = feed.get("content", "")
        fid = feed.get("tid", "")
        rt_con = feed.get("rt_con", "")
        images = feed.get("images", [])

        # --- 处理评论 ---
        comment_key = f"{fid}_main_comment"
        should_comment = random.random() <= self.get_config("read.comment_possibility", 0.3)

        if (
            should_comment
            and not self.reply_tracker.has_replied(fid, "main_comment")
            and comment_key not in self.processing_comments
        ):
            logger.debug(f"锁定待评论说说: {comment_key}")
            self.processing_comments.add(comment_key)
            try:
                comment_text = await self.content_service.generate_comment(content, target_name, rt_con, images)
                if comment_text:
                    success = await api_client["comment"](target_qq, fid, comment_text)
                    if success:
                        self.reply_tracker.mark_as_replied(fid, "main_comment")
                        logger.info(f"成功评论'{target_name}'的说说: '{comment_text}'")
                    else:
                        logger.error(f"评论'{target_name}'的说说失败")
            except Exception as e:
                logger.error(f"评论'{target_name}'的说说时发生异常: {e}", exc_info=True)
            finally:
                logger.debug(f"解锁说说: {comment_key}")
                if comment_key in self.processing_comments:
                    self.processing_comments.remove(comment_key)

        # --- 处理点赞 (逻辑不变) ---
        if random.random() <= self.get_config("read.like_possibility", 1.0):
            await api_client["like"](target_qq, fid)

    def _load_local_images(self, image_dir: str) -> list[bytes]:
        """随机加载本地图片（不删除文件）"""
        images = []
        if not image_dir or not os.path.exists(image_dir):
            logger.warning(f"图片目录不存在或未配置: {image_dir}")
            return images

        try:
            # 获取所有图片文件
            all_files = [
                f
                for f in os.listdir(image_dir)
                if os.path.isfile(os.path.join(image_dir, f))
                and f.lower().endswith((".jpg", ".jpeg", ".png", ".gif", ".bmp"))
            ]

            if not all_files:
                logger.warning(f"图片目录中没有找到图片文件: {image_dir}")
                return images

            # 检查是否启用配图
            enable_image = bool(self.get_config("send.enable_image", False))
            if not enable_image:
                logger.info("说说配图功能已关闭")
                return images

            # 根据配置选择图片数量
            config_image_number = self.get_config("send.image_number", 1)
            try:
                config_image_number = int(config_image_number)
            except (ValueError, TypeError):
                config_image_number = 1
                logger.warning("配置项 image_number 值无效，使用默认值 1")

            max_images = min(min(config_image_number, 9), len(all_files))  # 最多9张，最少1张
            selected_count = max(1, max_images)  # 确保至少选择1张
            selected_files = random.sample(all_files, selected_count)

            logger.info(f"从 {len(all_files)} 张图片中随机选择了 {selected_count} 张配图")

            for filename in selected_files:
                full_path = os.path.join(image_dir, filename)
                try:
                    with open(full_path, "rb") as f:
                        image_data = f.read()
                        images.append(image_data)
                        logger.info(f"加载图片: {filename} ({len(image_data)} bytes)")
                except Exception as e:
                    logger.error(f"加载图片 {filename} 失败: {e}")

            return images
        except Exception as e:
            logger.error(f"加载本地图片失败: {e}")
            return []

    def _generate_gtk(self, skey: str) -> str:
        hash_val = 5381
        for char in skey:
            hash_val += (hash_val << 5) + ord(char)
        return str(hash_val & 2147483647)

    async def _renew_and_load_cookies(self, qq_account: str, stream_id: str | None) -> dict[str, str] | None:
        cookie_dir = Path(__file__).resolve().parent.parent / "cookies"
        cookie_dir.mkdir(exist_ok=True)
        cookie_file_path = cookie_dir / f"cookies-{qq_account}.json"

        # 优先尝试通过Napcat HTTP服务获取最新的Cookie
        try:
            logger.info("尝试通过Napcat HTTP服务获取Cookie...")
            host = self.get_config("cookie.http_fallback_host", "172.20.130.55")
            port = self.get_config("cookie.http_fallback_port", "9999")
            napcat_token = self.get_config("cookie.napcat_token", "")

            cookie_data = await self._fetch_cookies_http(host, port, napcat_token)
            if cookie_data and "cookies" in cookie_data:
                cookie_str = cookie_data["cookies"]
                parsed_cookies = {
                    k.strip(): v.strip() for k, v in (p.split("=", 1) for p in cookie_str.split("; ") if "=" in p)
                }
                # 成功获取后，异步写入本地文件作为备份
                try:
                    with open(cookie_file_path, "wb") as f:
                        f.write(orjson.dumps(parsed_cookies))
                    logger.info(f"通过Napcat服务成功更新Cookie，并已保存至: {cookie_file_path}")
                except Exception as e:
                    logger.warning(f"保存Cookie到文件时出错: {e}")
                return parsed_cookies
            else:
                logger.warning("通过Napcat服务未能获取有效Cookie。")

        except Exception as e:
            logger.warning(f"通过Napcat HTTP服务获取Cookie时发生异常: {e}。将尝试从本地文件加载。")

        # 如果通过服务获取失败，则尝试从本地文件加载
        logger.info("尝试从本地Cookie文件加载...")
        if cookie_file_path.exists():
            try:
                with open(cookie_file_path, "rb") as f:
                    cookies = orjson.loads(f.read())
                    logger.info(f"成功从本地文件加载Cookie: {cookie_file_path}")
                    return cookies
            except Exception as e:
                logger.error(f"从本地文件 {cookie_file_path} 读取或解析Cookie失败: {e}")
        else:
            logger.warning(f"本地Cookie文件不存在: {cookie_file_path}")

        logger.error("所有获取Cookie的方式均失败。")
        return None

    async def _fetch_cookies_http(self, host: str, port: int, napcat_token: str) -> dict | None:
        """通过HTTP服务器获取Cookie"""
        # 从配置中读取主机和端口，如果未提供则使用传入的参数
        final_host = self.get_config("cookie.http_fallback_host", host)
        final_port = self.get_config("cookie.http_fallback_port", port)
        url = f"http://{final_host}:{final_port}/get_cookies"

        max_retries = 5
        retry_delay = 1

        for attempt in range(max_retries):
            try:
                headers = {"Content-Type": "application/json"}
                if napcat_token:
                    headers["Authorization"] = f"Bearer {napcat_token}"

                payload = {"domain": "user.qzone.qq.com"}

                async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30.0)) as session:
                    async with session.post(url, json=payload, headers=headers) as resp:
                        resp.raise_for_status()

                        if resp.status != 200:
                            error_msg = f"Napcat服务返回错误状态码: {resp.status}"
                            if resp.status == 403:
                                error_msg += " (Token验证失败)"
                            raise RuntimeError(error_msg)

                        data = await resp.json()
                        if data.get("status") != "ok" or "cookies" not in data.get("data", {}):
                            raise RuntimeError(f"获取 cookie 失败: {data}")
                        return data["data"]

            except aiohttp.ClientError as e:
                if attempt < max_retries - 1:
                    logger.warning(f"无法连接到Napcat服务(尝试 {attempt + 1}/{max_retries}): {url}，错误: {e!s}")
                    await asyncio.sleep(retry_delay)
                    retry_delay *= 2
                    continue
                logger.error(f"无法连接到Napcat服务(最终尝试): {url}，错误: {e!s}")
                raise RuntimeError(f"无法连接到Napcat服务: {url}") from e
            except Exception as e:
                logger.error(f"获取cookie异常: {e!s}")
                raise

        raise RuntimeError(f"无法连接到Napcat服务: 超过最大重试次数({max_retries})")

    async def _get_api_client(self, qq_account: str, stream_id: str | None) -> dict | None:
        cookies = await self.cookie_service.get_cookies(qq_account, stream_id)
        if not cookies:
            logger.error(
                "获取API客户端失败：未能获取到Cookie。请检查Napcat连接是否正常，或是否存在有效的本地Cookie文件。"
            )
            return None

        p_skey = cookies.get("p_skey") or cookies.get("p_skey".upper())
        if not p_skey:
            logger.error(f"获取API客户端失败：Cookie中缺少关键的 'p_skey'。Cookie内容: {cookies}")
            return None

        gtk = self._generate_gtk(p_skey)
        uin = cookies.get("uin", "").lstrip("o")
        if not uin:
            logger.error(f"获取API客户端失败：Cookie中缺少关键的 'uin'。Cookie内容: {cookies}")
            return None

        async def _request(method, url, params=None, data=None, headers=None):
            final_headers = {"referer": f"https://user.qzone.qq.com/{uin}", "origin": "https://user.qzone.qq.com"}
            if headers:
                final_headers.update(headers)

            async with aiohttp.ClientSession(cookies=cookies) as session:
                timeout = aiohttp.ClientTimeout(total=20)
                async with session.request(
                    method, url, params=params, data=data, headers=final_headers, timeout=timeout
                ) as response:
                    response.raise_for_status()
                    return await response.text()

        async def _publish(content: str, images: list[bytes]) -> tuple[bool, str]:
            """发布说说"""
            try:
                post_data = {
                    "syn_tweet_verson": "1",
                    "paramstr": "1",
                    "who": "1",
                    "con": content,
                    "feedversion": "1",
                    "ver": "1",
                    "ugc_right": "1",
                    "to_sign": "0",
                    "hostuin": uin,
                    "code_version": "1",
                    "format": "json",
                    "qzreferrer": f"https://user.qzone.qq.com/{uin}",
                }

                # 处理图片上传
                if images:
                    logger.info(f"开始上传 {len(images)} 张图片...")
                    pic_bos = []
                    richvals = []

                    for i, img_bytes in enumerate(images):
                        try:
                            # 上传图片到QQ空间
                            upload_result = await _upload_image(img_bytes, i)
                            if upload_result:
                                pic_bos.append(upload_result["pic_bo"])
                                richvals.append(upload_result["richval"])
                                logger.info(f"图片 {i + 1} 上传成功")
                            else:
                                logger.error(f"图片 {i + 1} 上传失败")
                        except Exception as e:
                            logger.error(f"上传图片 {i + 1} 时发生异常: {e}")

                    if pic_bos and richvals:
                        # 完全按照原版格式设置图片参数
                        post_data["pic_bo"] = ",".join(pic_bos)
                        post_data["richtype"] = "1"
                        post_data["richval"] = "\t".join(richvals)  # 原版使用制表符分隔

                        logger.info(f"准备发布带图说说: {len(pic_bos)} 张图片")
                        logger.info(f"pic_bo参数: {post_data['pic_bo']}")
                        logger.info(f"richval参数长度: {len(post_data['richval'])} 字符")
                    else:
                        logger.warning("所有图片上传失败，将发布纯文本说说")

                res_text = await _request("POST", self.EMOTION_PUBLISH_URL, params={"g_tk": gtk}, data=post_data)
                result = orjson.loads(res_text)
                tid = result.get("tid", "")

                if tid:
                    if images and pic_bos:
                        logger.info(f"成功发布带图说说，tid: {tid}，包含 {len(pic_bos)} 张图片")
                    else:
                        logger.info(f"成功发布文本说说，tid: {tid}")
                else:
                    logger.error(f"发布说说失败，API返回: {result}")

                return bool(tid), tid
            except Exception as e:
                logger.error(f"发布说说异常: {e}", exc_info=True)
                return False, ""

        def _image_to_base64(image_bytes: bytes) -> str:
            """将图片字节转换为base64字符串（仿照原版实现）"""
            pic_base64 = base64.b64encode(image_bytes)
            return str(pic_base64)[2:-1]  # 去掉 b'...' 的前缀和后缀

        def _get_picbo_and_richval(upload_result: dict) -> tuple:
            """从上传结果中提取图片的picbo和richval值（仿照原版实现）"""
            json_data = upload_result

            if "ret" not in json_data:
                raise Exception("获取图片picbo和richval失败")

            if json_data["ret"] != 0:
                raise Exception("上传图片失败")

            # 从URL中提取bo参数
            picbo_spt = json_data["data"]["url"].split("&bo=")
            if len(picbo_spt) < 2:
                raise Exception("上传图片失败")
            picbo = picbo_spt[1]

            # 构造richval - 完全按照原版格式
            richval = ",{},{},{},{},{},{},,{},{}".format(
                json_data["data"]["albumid"],
                json_data["data"]["lloc"],
                json_data["data"]["sloc"],
                json_data["data"]["type"],
                json_data["data"]["height"],
                json_data["data"]["width"],
                json_data["data"]["height"],
                json_data["data"]["width"],
            )

            return picbo, richval

        async def _upload_image(image_bytes: bytes, index: int) -> dict[str, str] | None:
            """上传图片到QQ空间（完全按照原版实现）"""
            try:
                upload_url = "https://up.qzone.qq.com/cgi-bin/upload/cgi_upload_image"

                # 完全按照原版构建请求数据
                post_data = {
                    "filename": "filename",
                    "zzpanelkey": "",
                    "uploadtype": "1",
                    "albumtype": "7",
                    "exttype": "0",
                    "skey": cookies.get("skey", ""),
                    "zzpaneluin": uin,
                    "p_uin": uin,
                    "uin": uin,
                    "p_skey": cookies.get("p_skey", ""),
                    "output_type": "json",
                    "qzonetoken": "",
                    "refer": "shuoshuo",
                    "charset": "utf-8",
                    "output_charset": "utf-8",
                    "upload_hd": "1",
                    "hd_width": "2048",
                    "hd_height": "10000",
                    "hd_quality": "96",
                    "backUrls": "http://upbak.photo.qzone.qq.com/cgi-bin/upload/cgi_upload_image,"
                    "http://119.147.64.75/cgi-bin/upload/cgi_upload_image",
                    "url": f"https://up.qzone.qq.com/cgi-bin/upload/cgi_upload_image?g_tk={gtk}",
                    "base64": "1",
                    "picfile": _image_to_base64(image_bytes),
                }

                headers = {"referer": f"https://user.qzone.qq.com/{uin}", "origin": "https://user.qzone.qq.com"}

                logger.info(f"开始上传图片 {index + 1}...")

                async with aiohttp.ClientSession(cookies=cookies) as session:
                    timeout = aiohttp.ClientTimeout(total=60)
                    async with session.post(upload_url, data=post_data, headers=headers, timeout=timeout) as response:
                        if response.status == 200:
                            resp_text = await response.text()
                            logger.info(f"图片上传响应状态码: {response.status}")
                            logger.info(f"图片上传响应内容前500字符: {resp_text[:500]}")

                            # 按照原版方式解析响应
                            start_idx = resp_text.find("{")
                            end_idx = resp_text.rfind("}") + 1
                            if start_idx != -1 and end_idx != -1:
                                json_str = resp_text[start_idx:end_idx]
                                try:
                                    upload_result = orjson.loads(json_str)
                                except orjson.JSONDecodeError:
                                    logger.error(f"图片上传响应JSON解析失败，原始响应: {resp_text}")
                                    return None

                                logger.debug(f"图片上传解析结果: {upload_result}")

                                if upload_result.get("ret") == 0:
                                    try:
                                        # 使用原版的参数提取逻辑
                                        picbo, richval = _get_picbo_and_richval(upload_result)
                                        logger.info(f"图片 {index + 1} 上传成功: picbo={picbo}")
                                        return {"pic_bo": picbo, "richval": richval}
                                    except Exception as e:
                                        logger.error(
                                            f"从上传结果中提取图片参数失败: {e}, 上传结果: {upload_result}",
                                            exc_info=True,
                                        )
                                        return None
                                else:
                                    logger.error(f"图片 {index + 1} 上传失败: {upload_result}")
                                    return None
                            else:
                                logger.error(f"无法从响应中提取JSON内容: {resp_text}")
                                return None
                        else:
                            error_text = await response.text()
                            logger.error(f"图片上传HTTP请求失败，状态码: {response.status}, 响应: {error_text[:200]}")
                            return None

            except Exception as e:
                logger.error(f"上传图片 {index + 1} 异常: {e}", exc_info=True)
                return None

        async def _list_feeds(t_qq: str, num: int) -> list[dict]:
            """获取指定用户说说列表 (统一接口)"""
            try:
                # 统一使用 format=json 获取完整评论
                params = {
                    "g_tk": gtk,
                    "uin": t_qq,
                    "ftype": 0,
                    "sort": 0,
                    "pos": 0,
                    "num": num,
                    "replynum": 999,  # 尽量获取更多
                    "code_version": 1,
                    "format": "json",  # 关键：使用JSON格式
                    "need_comment": 1,
                }
                res_text = await _request("GET", self.LIST_URL, params=params)
                json_data = orjson.loads(res_text)

                if json_data.get("code") != 0:
                    logger.warning(
                        f"获取说说列表API返回错误: code={json_data.get('code')}, message={json_data.get('message')}"
                    )
                    return []

                feeds_list = []
                my_name = json_data.get("logininfo", {}).get("name", "")

                for msg in json_data.get("msglist", []):
                    # 当读取的是好友动态时，检查是否已评论过，如果是则跳过
                    is_friend_feed = str(t_qq) != str(uin)
                    if is_friend_feed:
                        commentlist_for_check = msg.get("commentlist")
                        is_commented = False
                        if isinstance(commentlist_for_check, list):
                            is_commented = any(
                                c.get("name") == my_name for c in commentlist_for_check if isinstance(c, dict)
                            )
                        if is_commented:
                            continue

                    # --- 安全地处理图片列表 ---
                    images = []
                    if "pic" in msg and isinstance(msg["pic"], list):
                        images = [pic.get("url1", "") for pic in msg["pic"] if pic.get("url1")]
                    elif "pictotal" in msg and isinstance(msg["pictotal"], list):
                        images = [pic.get("url1", "") for pic in msg["pictotal"] if pic.get("url1")]

                    # --- 解析完整评论列表 (包括二级评论) ---
                    comments = []
                    commentlist = msg.get("commentlist")
                    if isinstance(commentlist, list):
                        for c in commentlist:
                            if not isinstance(c, dict):
                                continue

                            # 添加主评论
                            comments.append(
                                {
                                    "qq_account": c.get("uin"),
                                    "nickname": c.get("name"),
                                    "content": c.get("content"),
                                    "comment_tid": c.get("tid"),
                                    "parent_tid": None,  # 主评论没有父ID
                                }
                            )
                            # 检查并添加二级评论 (回复)
                            if "list_3" in c and isinstance(c["list_3"], list):
                                for reply in c["list_3"]:
                                    if not isinstance(reply, dict):
                                        continue
                                    comments.append(
                                        {
                                            "qq_account": reply.get("uin"),
                                            "nickname": reply.get("name"),
                                            "content": reply.get("content"),
                                            "comment_tid": reply.get("tid"),
                                            "parent_tid": c.get("tid"),  # 父ID是主评论的ID
                                        }
                                    )

                    feeds_list.append(
                        {
                            "tid": msg.get("tid", ""),
                            "content": msg.get("content", ""),
                            "created_time": time.strftime(
                                "%Y-%m-%d %H:%M:%S", time.localtime(msg.get("created_time", 0))
                            ),
                            "rt_con": msg.get("rt_con", {}).get("content", "")
                            if isinstance(msg.get("rt_con"), dict)
                            else "",
                            "images": images,
                            "comments": comments,
                        }
                    )

                logger.info(f"成功获取到 {len(feeds_list)} 条说说 from {t_qq} (使用统一JSON接口)")
                return feeds_list
            except Exception as e:
                logger.error(f"获取说说列表失败: {e}", exc_info=True)
                return []

        async def _comment(t_qq: str, feed_id: str, text: str) -> bool:
            """评论说说"""
            try:
                data = {
                    "topicId": f"{t_qq}_{feed_id}__1",
                    "uin": uin,
                    "hostUin": t_qq,
                    "content": text,
                    "format": "fs",
                    "plat": "qzone",
                    "source": "ic",
                    "platformid": 52,
                    "ref": "feeds",
                }
                await _request("POST", self.COMMENT_URL, params={"g_tk": gtk}, data=data)
                return True
            except Exception as e:
                logger.error(f"评论说说异常: {e}", exc_info=True)
                return False

        async def _like(t_qq: str, feed_id: str) -> bool:
            """点赞说说"""
            try:
                data = {
                    "opuin": uin,
                    "unikey": f"http://user.qzone.qq.com/{t_qq}/mood/{feed_id}",
                    "curkey": f"http://user.qzone.qq.com/{t_qq}/mood/{feed_id}",
                    "from": 1,
                    "appid": 311,
                    "typeid": 0,
                    "abstime": int(time.time()),
                    "fid": feed_id,
                    "active": 0,
                    "format": "json",
                    "fupdate": 1,
                }
                await _request("POST", self.DOLIKE_URL, params={"g_tk": gtk}, data=data)
                return True
            except Exception as e:
                logger.error(f"点赞说说异常: {e}", exc_info=True)
                return False

        async def _reply(fid, host_qq, target_name, content, comment_tid):
            """回复评论 - 修复为能正确提醒的回复格式"""
            try:
                # 修复回复逻辑：确保能正确提醒被回复的人
                data = {
                    "topicId": f"{host_qq}_{fid}__1",
                    "parent_tid": comment_tid,
                    "uin": uin,
                    "hostUin": host_qq,
                    "content": content,
                    "format": "fs",
                    "plat": "qzone",
                    "source": "ic",
                    "platformid": 52,
                    "ref": "feeds",
                    "richtype": "",
                    "richval": "",
                    "paramstr": "",
                }

                # 记录详细的请求参数用于调试
                logger.info(
                    f"子回复请求参数: topicId={data['topicId']}, parent_tid={data['parent_tid']}, content='{content[:50]}...'"
                )

                await _request("POST", self.REPLY_URL, params={"g_tk": gtk}, data=data)
                return True
            except Exception as e:
                logger.error(f"回复评论异常: {e}", exc_info=True)
                return False

        async def _monitor_list_feeds(num: int) -> list[dict]:
            """监控好友动态"""
            try:
                params = {
                    "uin": uin,
                    "scope": 0,
                    "view": 1,
                    "filter": "all",
                    "flag": 1,
                    "applist": "all",
                    "pagenum": 1,
                    "count": num,
                    "format": "json",
                    "g_tk": gtk,
                    "useutf8": 1,
                    "outputhtmlfeed": 1,
                }
                res_text = await _request("GET", self.ZONE_LIST_URL, params=params)

                # 处理不同的响应格式
                json_str = ""
                stripped_res_text = res_text.strip()
                if stripped_res_text.startswith("_Callback(") and stripped_res_text.endswith(");"):
                    json_str = stripped_res_text[len("_Callback(") : -2]
                elif stripped_res_text.startswith("{") and stripped_res_text.endswith("}"):
                    json_str = stripped_res_text
                else:
                    logger.warning(f"意外的响应格式: {res_text[:100]}...")
                    return []

                json_str = json_str.replace("undefined", "null").strip()

                try:
                    json_data = json5.loads(json_str)
                    if not isinstance(json_data, dict):
                        logger.warning(f"解析后的JSON数据不是字典类型: {type(json_data)}")
                        return []

                    if json_data.get("code") != 0:
                        error_code = json_data.get("code")
                        error_msg = json_data.get("message", "未知错误")
                        logger.warning(f"QQ空间API返回错误: code={error_code}, message={error_msg}")
                        return []

                except Exception as parse_error:
                    logger.error(f"JSON解析失败: {parse_error}, 原始数据: {json_str[:200]}...")
                    return []

                feeds_data = []
                if isinstance(json_data, dict):
                    data_level1 = json_data.get("data")
                    if isinstance(data_level1, dict):
                        feeds_data = data_level1.get("data", [])

                feeds_list = []
                for feed in feeds_data:
                    if not feed or not isinstance(feed, dict):
                        continue

                    if str(feed.get("appid", "")) != "311":
                        continue

                    target_qq = str(feed.get("uin", ""))
                    tid = feed.get("key", "")
                    if not target_qq or not tid:
                        continue

                    if target_qq == str(uin):
                        continue

                    html_content = feed.get("html", "")
                    if not html_content:
                        continue

                    soup = bs4.BeautifulSoup(html_content, "html.parser")

                    like_btn = soup.find("a", class_="qz_like_btn_v3")
                    is_liked = False
                    if like_btn and isinstance(like_btn, bs4.Tag):
                        is_liked = like_btn.get("data-islike") == "1"

                    if is_liked:
                        continue

                    text_div = soup.find("div", class_="f-info")
                    text = text_div.get_text(strip=True) if text_div else ""

                    # --- 借鉴原版插件的精确图片提取逻辑 ---
                    image_urls = []
                    img_box = soup.find("div", class_="img-box")
                    if img_box:
                        for img in img_box.find_all("img"):
                            src = img.get("src")
                            # 排除QQ空间的小图标和表情
                            if src and "qzonestyle.gtimg.cn" not in src:
                                image_urls.append(src)

                    # 视频封面也视为图片
                    video_thumb = soup.select_one("div.video-img img")
                    if video_thumb and "src" in video_thumb.attrs:
                        image_urls.append(video_thumb["src"])

                    # 去重
                    images = list(set(image_urls))

                    comments = []
                    comment_divs = soup.find_all("div", class_="f-single-comment")
                    for comment_div in comment_divs:
                        # --- 处理主评论 ---
                        author_a = comment_div.find("a", class_="f-nick")
                        content_span = comment_div.find("span", class_="f-re-con")

                        if author_a and content_span:
                            comments.append(
                                {
                                    "qq_account": str(comment_div.get("data-uin", "")),
                                    "nickname": author_a.get_text(strip=True),
                                    "content": content_span.get_text(strip=True),
                                    "comment_tid": comment_div.get("data-tid", ""),
                                    "parent_tid": None,  # 主评论没有父ID
                                }
                            )

                        # --- 处理这条主评论下的所有回复 ---
                        reply_divs = comment_div.find_all("div", class_="f-single-re")
                        for reply_div in reply_divs:
                            reply_author_a = reply_div.find("a", class_="f-nick")
                            reply_content_span = reply_div.find("span", class_="f-re-con")

                            if reply_author_a and reply_content_span:
                                comments.append(
                                    {
                                        "qq_account": str(reply_div.get("data-uin", "")),
                                        "nickname": reply_author_a.get_text(strip=True),
                                        "content": reply_content_span.get_text(strip=True).lstrip(
                                            ": "
                                        ),  # 移除回复内容前多余的冒号和空格
                                        "comment_tid": reply_div.get("data-tid", ""),
                                        "parent_tid": reply_div.get(
                                            "data-parent-tid", comment_div.get("data-tid", "")
                                        ),  # 如果没有父ID，则将父ID设为主评论ID
                                    }
                                )

                    feeds_list.append(
                        {"target_qq": target_qq, "tid": tid, "content": text, "images": images, "comments": comments}
                    )
                logger.info(f"监控任务发现 {len(feeds_list)} 条未处理的新说说。")
                return feeds_list
            except Exception as e:
                logger.error(f"监控好友动态失败: {e}", exc_info=True)
                return []

        return {
            "publish": _publish,
            "list_feeds": _list_feeds,
            "comment": _comment,
            "like": _like,
            "reply": _reply,
            "monitor_list_feeds": _monitor_list_feeds,
        }
