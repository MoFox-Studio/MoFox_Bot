#!/usr/bin/env python3
"""
Bilibili 视频观看体验工具
支持哔哩哔哩视频链接解析和AI视频内容分析
"""

from typing import Any

from src.common.logger import get_logger
from src.plugin_system import BasePlugin, BaseTool, ComponentInfo, ConfigField, ToolParamType, register_plugin

from .bilibli_base import get_bilibili_analyzer

logger = get_logger("bilibili_tool")


class BilibiliTool(BaseTool):
    """哔哩哔哩视频观看体验工具 - 像真实用户一样观看和评价用户分享的哔哩哔哩视频"""

    name = "bilibili_video_watcher"
    description = "观看用户分享的哔哩哔哩视频，以真实用户视角给出观看感受和评价"
    available_for_llm = True

    parameters = [
        (
            "url",
            ToolParamType.STRING,
            "用户分享给我的哔哩哔哩视频链接，我会认真观看这个视频并给出真实的观看感受",
            True,
            None,
        ),
        (
            "interest_focus",
            ToolParamType.STRING,
            "你特别感兴趣的方面（如：搞笑内容、学习资料、美食、游戏、音乐等），我会重点关注这些内容",
            False,
            None,
        ),
    ]

    def __init__(self, plugin_config: dict | None = None, chat_stream=None):
        super().__init__(plugin_config, chat_stream)
        self.analyzer = get_bilibili_analyzer()

    async def execute(self, function_args: dict[str, Any]) -> dict[str, Any]:
        """执行哔哩哔哩视频观看体验"""
        try:
            url = function_args.get("url", "").strip()
            interest_focus = function_args.get("interest_focus", "").strip() or None

            if not url:
                return {"name": self.name, "content": "🤔 你想让我看哪个视频呢？给我个链接吧！"}

            logger.info(f"开始'观看'哔哩哔哩视频: {url}")

            # 验证是否为哔哩哔哩链接
            extracted_url = self.analyzer.extract_bilibili_url(url)
            if not extracted_url:
                return {
                    "name": self.name,
                    "content": "🤨 这好像不是哔哩哔哩的链接诶，我只会看哔哩哔哩的视频哦~ 给我一个bilibili.com或b23.tv的链接吧！",
                }

            # 构建个性化的观看提示词
            watch_prompt = self._build_watch_prompt(interest_focus)

            # 执行视频分析
            result = await self.analyzer.analyze_bilibili_video(extracted_url, watch_prompt)

            if result.get("error"):
                return {
                    "name": self.name,
                    "content": f"😔 唉，这个视频我看不了... {result['error']}\n可能是网络问题或者视频有限制",
                }

            # 格式化输出结果
            video_info = result.get("video_info", {})
            ai_analysis = result.get("ai_analysis", "")

            # 构建个性化的观看体验报告
            content = self._format_watch_experience(video_info, ai_analysis, interest_focus)

            logger.info("✅ 哔哩哔哩视频观看体验完成")
            return {"name": self.name, "content": content.strip()}

        except Exception as e:
            error_msg = f"😅 看视频的时候出了点问题: {e!s}"
            logger.error(error_msg)
            return {"name": self.name, "content": error_msg}

    def _build_watch_prompt(self, interest_focus: str | None = None) -> str:
        """构建个性化的观看提示词"""
        base_prompt = """请以一个真实哔哩哔哩用户的视角来观看用户分享给我的这个视频。用户特意分享了这个视频给我，我需要认真观看并给出真实的反馈。

我希望你能：
1. 像普通用户一样对这个分享的视频内容做出真实的反应和评价
2. 根据你的"个人喜好"判断这个用户分享的视频是否有趣、是否值得观看
3. 给出主观的观看感受，包括：喜欢什么、不喜欢什么、有什么感想
4. 用轻松、自然的语气表达，就像在和分享视频的朋友聊天
5. 可以表达个人偏好，比如"我比较喜欢..."、"这种类型不太符合我的口味"等
7. 对用户的分享表示感谢，体现出这是用户主动分享给我的内容"""

        if interest_focus:
            base_prompt += f"\n\n特别关注点：我对 {interest_focus} 相关的内容比较感兴趣，请重点评价这方面的内容。"

        return base_prompt

    def _format_watch_experience(self, video_info: dict, ai_analysis: str, interest_focus: str | None = None) -> str:
        """格式化观看体验报告"""

        # 根据播放量生成热度评价
        view_count = video_info.get("播放量", "0").replace(",", "")
        if view_count.isdigit():
            views = int(view_count)
            if views > 1000000:
                popularity = "🔥 超火爆"
            elif views > 100000:
                popularity = "🔥 很热门"
            elif views > 10000:
                popularity = "👍 还不错"
            else:
                popularity = "🆕 比较新"
        else:
            popularity = "🤷‍♀️ 数据不明"

        # 生成时长评价
        duration = video_info.get("时长", "")
        if "分" in duration:
            time_comment = self._get_duration_comment(duration)
        else:
            time_comment = ""

        content = f"""🎬 **谢谢你分享的这个哔哩哔哩视频！我认真看了一下~**

📺 **视频速览**
• 标题：{video_info.get("标题", "未知")}
• UP主：{video_info.get("UP主", "未知")}
• 时长：{duration} {time_comment}
• 热度：{popularity} ({video_info.get("播放量", "0")}播放)
• 互动：👍{video_info.get("点赞", "0")} 🪙{video_info.get("投币", "0")} ⭐{video_info.get("收藏", "0")}

📝 **UP主说了什么**
{video_info.get("简介", "这个UP主很懒，什么都没写...")[:150]}{"..." if len(video_info.get("简介", "")) > 150 else ""}

🤔 **我的观看感受**
{ai_analysis}
"""

        if interest_focus:
            content += (
                f"\n💭 **关于你感兴趣的'{interest_focus}'**\n我特别注意了这方面的内容，感觉{self._get_focus_comment()}~"
            )

        return content

    def _get_duration_comment(self, duration: str) -> str:
        """根据时长生成评价"""
        if "分" in duration:
            try:
                minutes = int(duration.split("分")[0])
                if minutes < 3:
                    return "(短小精悍)"
                elif minutes < 10:
                    return "(时长刚好)"
                elif minutes < 30:
                    return "(有点长，适合闲时观看)"
                else:
                    return "(超长视频，需要耐心)"
            except:
                return ""
        return ""

    def _get_focus_comment(self) -> str:
        """生成关注点评价"""
        import random

        comments = [
            "挺符合你的兴趣的",
            "内容还算不错",
            "可能会让你感兴趣",
            "值得一看",
            "可能不太符合你的口味",
            "内容比较一般",
        ]
        return random.choice(comments)


@register_plugin
class BilibiliPlugin(BasePlugin):
    """哔哩哔哩视频观看体验插件 - 处理用户分享的视频内容"""

    # 插件基本信息
    plugin_name: str = "bilibili_video_watcher"
    enable_plugin: bool = False
    dependencies: list[str] = []
    python_dependencies: list[str] = []
    config_file_name: str = "config.toml"

    # 配置节描述
    config_section_descriptions = {"plugin": "插件基本信息", "bilibili": "哔哩哔哩视频观看配置", "tool": "工具配置"}

    # 配置Schema定义
    config_schema: dict = {
        "plugin": {
            "name": ConfigField(type=str, default="bilibili_video_watcher", description="插件名称"),
            "version": ConfigField(type=str, default="2.0.0", description="插件版本"),
            "enabled": ConfigField(type=bool, default=True, description="是否启用插件"),
            "config_version": ConfigField(type=str, default="2.0.0", description="配置文件版本"),
        },
        "bilibili": {
            "timeout": ConfigField(type=int, default=300, description="观看超时时间（秒）"),
            "verbose_logging": ConfigField(type=bool, default=True, description="是否启用详细日志"),
            "max_retries": ConfigField(type=int, default=3, description="最大重试次数"),
        },
        "tool": {
            "available_for_llm": ConfigField(type=bool, default=True, description="是否对LLM可用"),
            "name": ConfigField(type=str, default="bilibili_video_watcher", description="工具名称"),
            "description": ConfigField(
                type=str, default="观看用户分享的哔哩哔哩视频并给出真实观看体验", description="工具描述"
            ),
        },
    }

    def get_plugin_components(self) -> list[tuple[ComponentInfo, type]]:
        """返回插件包含的工具组件"""
        return [(BilibiliTool.get_tool_info(), BilibiliTool)]
