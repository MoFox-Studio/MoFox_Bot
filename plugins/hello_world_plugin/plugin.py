import random
from typing import Any, ClassVar

from src.common.logger import get_logger
from src.common.security import VerifiedDep

# 修正导入路径，让Pylance不再抱怨
from src.plugin_system import (
    BaseAction,
    BaseEventHandler,
    BasePlugin,
    BasePrompt,
    BaseTool,
    ChatType,
    CommandArgs,
    ComponentInfo,
    ConfigField,
    EventType,
    PlusCommand,
    ToolParamType,
    register_plugin,
)
from src.plugin_system.base.base_event import HandlerResult
from src.plugin_system.base.base_http_component import BaseRouterComponent
from src.plugin_system.base.component_types import InjectionRule, InjectionType

logger = get_logger("hello_world_plugin")


class StartupMessageHandler(BaseEventHandler):
    """启动时打印消息的事件处理器。"""

    handler_name = "hello_world_startup_handler"
    handler_description = "在机器人启动时打印一条日志。"
    init_subscribe: ClassVar[list[EventType]] = [EventType.ON_START]

    async def execute(self, params: dict) -> HandlerResult:
        logger.info("🎉 Hello World 插件已启动，准备就绪！")
        return HandlerResult(success=True, continue_process=True)


class GetSystemInfoTool(BaseTool):
    """一个提供系统信息的示例工具。"""

    name = "get_system_info"
    description = "获取当前系统的模拟版本和状态信息。"
    available_for_llm = True
    parameters: ClassVar = [
        ("query", ToolParamType.STRING, "要搜索的关键词或问题。", True, None),
        ("num_results", ToolParamType.INTEGER, "期望每个搜索引擎返回的搜索结果数量，默认为5。", False, None),
        (
            "time_range",
            ToolParamType.STRING,
            "指定搜索的时间范围，可以是 'any', 'week', 'month'。默认为 'any'。",
            False,
            ["any", "week", "month"],
        ),
    ]  # type: ignore

    async def execute(self, function_args: dict[str, Any]) -> dict[str, Any]:
        return {"name": self.name, "content": "系统版本: 1.0.1, 状态: 运行正常"}


class HelloCommand(PlusCommand):
    """一个简单的 /hello 命令，使用配置文件中的问候语。"""

    command_name = "hello"
    command_description = "向机器人发送一个简单的问候。"
    command_aliases: ClassVar[list[str]] = ["hi", "你好"]
    chat_type_allow = ChatType.ALL

    async def execute(self, args: CommandArgs) -> tuple[bool, str | None, bool]:
        greeting = str(self.get_config("greeting.message", "Hello, World! 我是一个由 MoFox_Bot 驱动的插件。"))
        await self.send_text(greeting)
        return True, "成功发送问候", True


# ==================================================================================
# 新的激活方式示例 Actions
# ==================================================================================


class KeywordActivationExampleAction(BaseAction):
    """关键词激活示例

    此示例展示如何使用关键词匹配来激活 Action。
    """

    action_name = "keyword_example"
    action_description = "当检测到特定关键词时发送回应"
    action_require: ClassVar[list[str]] = ["用户提到了问候语"]
    associated_types: ClassVar[list[str]] = ["text"]

    async def go_activate(self, chat_content: str = "", llm_judge_model=None) -> bool:
        """关键词激活：检测到"你好"、"hello"或"hi"时激活"""
        return await self._keyword_match(
            chat_content,
            keywords=["你好", "hello", "hi", "嗨"],
            case_sensitive=False  # 不区分大小写
        )

    async def execute(self) -> tuple[bool, str]:
        await self.send_text("检测到问候语，我也向你问好！👋")
        return True, "发送了问候回应"


class LLMJudgeExampleAction(BaseAction):
    """LLM 判断激活示例

    此示例展示如何使用 LLM 来智能判断是否激活 Action。
    """

    action_name = "llm_judge_example"
    action_description = "当用户表达情绪低落时提供安慰"
    action_require: ClassVar[list[str]] = ["用户情绪低落", "需要情感支持"]
    associated_types: ClassVar[list[str]] = ["text"]

    async def go_activate(self, chat_content: str = "", llm_judge_model=None) -> bool:
        """LLM 判断激活：判断用户是否情绪低落"""
        return await self._llm_judge_activation(
            judge_prompt="""
判断用户是否表达了以下情绪或需求：
1. 感到难过、沮丧或失落
2. 表达了负面情绪
3. 需要安慰或鼓励

如果用户表达了上述情绪或需求，回答"是"，否则回答"否"。
            """,
            llm_judge_model=llm_judge_model
        )

    async def execute(self) -> tuple[bool, str]:
        await self.send_text("看起来你心情不太好，希望能让你开心一点！🤗💕")
        return True, "发送了安慰消息"


class CombinedActivationExampleAction(BaseAction):
    """组合激活条件示例

    此示例展示如何组合多种激活条件。
    """

    action_name = "combined_example"
    action_description = "展示如何组合多种激活条件"
    action_require: ClassVar[list[str]] = ["展示灵活的激活逻辑"]
    associated_types: ClassVar[list[str]] = ["text"]

    async def go_activate(self, chat_content: str = "", llm_judge_model=None) -> bool:
        """组合激活：随机 20% 概率，或者匹配特定关键词"""
        # 先尝试随机激活
        if await self._random_activation(0.2):
            return True

        # 如果随机未激活，尝试关键词匹配
        if await self._keyword_match(chat_content, ["表情", "emoji", "😊"], case_sensitive=False):
            return True

        # 都不满足则不激活
        return False

    async def execute(self) -> tuple[bool, str]:
        await self.send_text("这是一个组合激活条件的示例！✨")
        return True, "发送了示例消息"


class RandomEmojiAction(BaseAction):
    """一个随机发送表情的动作。

    此示例展示了如何使用新的 go_activate() 方法来实现随机激活。
    """

    action_name = "random_emoji"
    action_description = "随机发送一个表情符号，增加聊天的趣味性。"
    action_require: ClassVar[list[str]] = ["当对话气氛轻松时", "可以用来回应简单的情感表达"]
    associated_types: ClassVar[list[str]] = ["text"]

    async def go_activate(self, llm_judge_model=None) -> bool:
        """使用新的激活方式：10% 的概率激活

        注意：不需要传入 chat_content，会自动从实例属性中获取
        """
        return await self._random_activation(0.1)

    async def execute(self) -> tuple[bool, str]:
        emojis = ["😊", "😂", "👍", "🎉", "🤔", "🤖"]
        await self.send_text(random.choice(emojis))
        return True, "成功发送了一个随机表情"


class WeatherPrompt(BasePrompt):
    """一个简单的Prompt组件，用于向Planner注入天气信息。"""

    prompt_name = "weather_info_prompt"
    prompt_description = "向Planner注入当前天气信息，以丰富对话上下文。"
    injection_rules: ClassVar[list[InjectionRule]] = [InjectionRule(target_prompt="planner_prompt", injection_type=InjectionType.REPLACE, target_content="## 可用动作列表")]

    async def execute(self) -> str:
        # 在实际应用中，这里可以调用天气API
        # 为了演示，我们返回一个固定的天气信息
        logger.info(self.target_prompt_name)
        return "当前天气：晴朗，温度25°C。"


class HelloWorldRouter(BaseRouterComponent):
    """一个简单的HTTP端点示例。"""

    component_name = "hello_world_router"
    component_description = "提供一个简单的 /greet HTTP GET 端点。"

    def register_endpoints(self) -> None:
        @self.router.get("/greet", summary="返回一个问候消息")
        def greet(_=VerifiedDep):
            """这个端点返回一个固定的问候语。"""
            return {"message": "Hello from your new API endpoint!"}


@register_plugin
class HelloWorldPlugin(BasePlugin):
    """一个包含四大核心组件和高级配置功能的入门示例插件。"""

    plugin_name = "hello_world_plugin"
    enable_plugin: bool = True
    dependencies: ClassVar = []
    python_dependencies: ClassVar = []
    config_file_name = "config.toml"

    config_schema: ClassVar = {
        "meta": {
            "config_version": ConfigField(type=int, default=1, description="配置文件版本，请勿手动修改。"),
        },
        "greeting": {
            "message": ConfigField(
                type=str, default="这是来自配置文件的问候！👋", description="HelloCommand 使用的问候语。"
            ),
        },
        "components": {
            "hello_command_enabled": ConfigField(type=bool, default=True, description="是否启用 /hello 命令。"),
            "random_emoji_action_enabled": ConfigField(type=bool, default=True, description="是否启用随机表情动作。"),
        },
    }

    def get_plugin_components(self) -> list[tuple[ComponentInfo, type]]:
        """根据配置文件动态注册插件的功能组件。"""
        components: list[tuple[ComponentInfo, type]] = []

        components.append((StartupMessageHandler.get_handler_info(), StartupMessageHandler))
        components.append((GetSystemInfoTool.get_tool_info(), GetSystemInfoTool))

        if self.get_config("components.hello_command_enabled", True):
            components.append((HelloCommand.get_plus_command_info(), HelloCommand))

        if self.get_config("components.random_emoji_action_enabled", True):
            components.append((RandomEmojiAction.get_action_info(), RandomEmojiAction))

        # 注册新的Prompt组件
        components.append((WeatherPrompt.get_prompt_info(), WeatherPrompt))

        # 注册新的Router组件
        components.append((HelloWorldRouter.get_router_info(), HelloWorldRouter))

        return components
