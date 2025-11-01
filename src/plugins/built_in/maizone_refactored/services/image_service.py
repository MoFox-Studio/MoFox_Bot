"""
图片服务模块
负责处理所有与图片相关的任务，特别是AI生成图片。
"""

import base64
from collections.abc import Callable
from pathlib import Path

import aiofiles
import aiohttp

from src.common.logger import get_logger

logger = get_logger("MaiZone.ImageService")


class ImageService:
    """
    图片服务类，封装了生成和管理图片的所有逻辑。
    """

    def __init__(self, get_config: Callable):
        """
        初始化图片服务。

        :param get_config: 一个函数，用于从插件主类获取配置信息。
        """
        self.get_config = get_config

    async def generate_images_for_story(self, story: str) -> bool:
        """
        根据说说内容，判断是否需要生成AI配图，并执行生成任务。

        :param story: 说说内容。
        :return: 图片是否成功生成（或不需要生成）。
        """
        try:
            enable_ai_image = bool(self.get_config("send.enable_ai_image", False))
            api_key = str(self.get_config("models.siliconflow_apikey", ""))
            image_dir = str(self.get_config("send.image_directory", "./data/plugins/maizone_refactored/images"))
            image_num_raw = self.get_config("send.ai_image_number", 1)
            image_num = int(image_num_raw if image_num_raw is not None else 1)

            if not enable_ai_image:
                return True  # 未启用AI配图，视为成功

            if not api_key:
                logger.error("启用了AI配图但未填写SiliconFlow API密钥")
                return False

            # 确保图片目录存在
            Path(image_dir).mkdir(parents=True, exist_ok=True)

            logger.info(f"正在为说说生成 {image_num} 张AI配图...")
            return await self._call_siliconflow_api(api_key, story, image_dir, image_num)

        except Exception as e:
            logger.error(f"处理AI配图时发生异常: {e}")
            return False

    async def _call_siliconflow_api(self, api_key: str, story: str, image_dir: str, batch_size: int) -> bool:
        """
        调用硅基流动（SiliconFlow）的API来生成图片。

        :param api_key: SiliconFlow API密钥。
        :param story: 用于生成图片的文本内容（说说）。
        :param image_dir: 图片保存目录。
        :param batch_size: 生成图片的数量。
        :return: API调用是否成功。
        """
        url = "https://api.siliconflow.cn/v1/images/generations"
        headers = {
            "accept": "application/json",
            "authorization": f"Bearer {api_key}",
            "content-type": "application/json",
        }
        payload = {"prompt": story, "n": batch_size, "response_format": "b64_json", "style": "cinematic-default"}

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, headers=headers) as response:
                    if response.status == 200:
                        data = await response.json()
                        for i, img_data in enumerate(data.get("data", [])):
                            b64_json = img_data.get("b64_json")
                            if b64_json:
                                image_bytes = base64.b64decode(b64_json)
                                file_path = Path(image_dir) / f"image_{i + 1}.png"
                                async with aiofiles.open(file_path, "wb") as f:
                                    await f.write(image_bytes)
                                logger.info(f"成功保存AI图片到: {file_path}")
                        return True
                    else:
                        error_text = await response.text()
                        logger.error(f"AI生图API请求失败，状态码: {response.status}, 错误信息: {error_text}")
                        return False
        except Exception as e:
            logger.error(f"调用AI生图API时发生异常: {e}")
            return False
