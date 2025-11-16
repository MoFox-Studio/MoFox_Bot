import os
import shutil
import sys
from datetime import datetime

import tomlkit
from pydantic import Field
from rich.traceback import install
from tomlkit import TOMLDocument
from tomlkit.items import KeyType, Table

from src.common.logger import get_logger
from src.config.config_base import ValidatedConfigBase
from src.config.official_configs import (
    AffinityFlowConfig,
    BotConfig,
    ChatConfig,
    ChineseTypoConfig,
    CommandConfig,
    CrossContextConfig,
    CustomPromptConfig,
    DatabaseConfig,
    DebugConfig,
    DependencyManagementConfig,
    EmojiConfig,
    ExperimentalConfig,
    ExpressionConfig,
    LPMMKnowledgeConfig,
    MaimMessageConfig,
    MemoryConfig,
    MessageReceiveConfig,
    MoodConfig,
    NoticeConfig,
    PermissionConfig,
    PersonalityConfig,
    PlanningSystemConfig,
    PluginHttpSystemConfig,
    ProactiveThinkingConfig,
    ReactionConfig,
    ResponsePostProcessConfig,
    ResponseSplitterConfig,
    ToolConfig,
    VideoAnalysisConfig,
    VoiceConfig,
    WebSearchConfig,
)

from .api_ada_configs import (
    APIProvider,
    ModelInfo,
    ModelTaskConfig,
)

install(extra_lines=3)


# 配置主程序日志格式
logger = get_logger("config")

# 获取当前文件所在目录的父目录的父目录（即MoFox-Bot项目根目录）
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
CONFIG_DIR = os.path.join(PROJECT_ROOT, "config")
TEMPLATE_DIR = os.path.join(PROJECT_ROOT, "template")

# 考虑到，实际上配置文件中的mai_version是不会自动更新的,所以采用硬编码
# 对该字段的更新，请严格参照语义化版本规范：https://semver.org/lang/zh-CN/
MMC_VERSION = "0.12.0"


def get_key_comment(toml_table, key):
    # 获取key的注释（如果有）
    if hasattr(toml_table, "trivia") and hasattr(toml_table.trivia, "comment"):
        return toml_table.trivia.comment
    if hasattr(toml_table, "value") and isinstance(toml_table.value, dict):
        item = toml_table.value.get(key)
        if item is not None and hasattr(item, "trivia"):
            return item.trivia.comment
    if hasattr(toml_table, "keys"):
        for k in toml_table.keys():
            if isinstance(k, KeyType) and k.key == key:  # type: ignore
                return k.trivia.comment  # type: ignore
    return None


def compare_dicts(new, old, path=None, logs=None):
    # 递归比较两个dict，找出新增和删减项，收集注释
    if path is None:
        path = []
    if logs is None:
        logs = []
    # 新增项
    for key in new:
        if key == "version":
            continue
        if key not in old:
            comment = get_key_comment(new, key)
            logs.append(f"新增: {'.'.join([*path, str(key)])}  注释: {comment or '无'}")
        elif isinstance(new[key], dict | Table) and isinstance(old.get(key), dict | Table):
            compare_dicts(new[key], old[key], [*path, str(key)], logs)
    # 删减项
    for key in old:
        if key == "version":
            continue
        if key not in new:
            comment = get_key_comment(old, key)
            logs.append(f"删减: {'.'.join([*path, str(key)])}  注释: {comment or '无'}")
    return logs


def get_value_by_path(d, path):
    for k in path:
        if isinstance(d, dict) and k in d:
            d = d[k]
        else:
            return None
    return d


def set_value_by_path(d, path, value):
    for k in path[:-1]:
        if k not in d or not isinstance(d[k], dict):
            d[k] = {}
        d = d[k]
    d[path[-1]] = value


def compare_default_values(new, old, path=None, logs=None, changes=None):
    # 递归比较两个dict，找出默认值变化项
    if path is None:
        path = []
    if logs is None:
        logs = []
    if changes is None:
        changes = []
    for key in new:
        if key == "version":
            continue
        if key in old:
            if isinstance(new[key], dict | Table) and isinstance(old[key], dict | Table):
                compare_default_values(new[key], old[key], [*path, str(key)], logs, changes)
            elif new[key] != old[key]:
                logs.append(f"默认值变化: {'.'.join([*path, str(key)])}  旧默认值: {old[key]}  新默认值: {new[key]}")
                changes.append(([*path, str(key)], old[key], new[key]))
    return logs, changes


def _get_version_from_toml(toml_path) -> str | None:
    """从TOML文件中获取版本号"""
    if not os.path.exists(toml_path):
        return None
    with open(toml_path, encoding="utf-8") as f:
        doc = tomlkit.load(f)
    if "inner" in doc and "version" in doc["inner"]:  # type: ignore
        return doc["inner"]["version"]  # type: ignore
    return None


def _version_tuple(v):
    """将版本字符串转换为元组以便比较"""
    if v is None:
        return (0,)
    return tuple(int(x) if x.isdigit() else 0 for x in str(v).replace("v", "").split("-")[0].split("."))


def _remove_obsolete_keys(target: TOMLDocument | dict | Table, reference: TOMLDocument | dict | Table):
    """
    递归地从目标字典中移除所有不存在于参考字典中的键。
    """
    # 使用 list() 创建键的副本，以便在迭代期间安全地修改字典
    for key in list(target.keys()):
        if key not in reference:
            del target[key]
        elif isinstance(target.get(key), dict | Table) and isinstance(reference.get(key), dict | Table):
            _remove_obsolete_keys(target[key], reference[key])


def _update_dict(target: TOMLDocument | dict | Table, source: TOMLDocument | dict):
    """
    将source字典的值更新到target字典中
    对于已存在的键，使用source的值进行更新
    对于不存在的键，从source添加到target
    """
    for key, value in source.items():
        # 跳过version字段的更新
        if key == "version":
            continue

        if key in target:
            # 键已存在，更新值
            target_value = target[key]
            if isinstance(value, dict) and isinstance(target_value, dict | Table):
                _update_dict(target_value, value)
            else:
                try:
                    # 对数组类型进行特殊处理
                    if isinstance(value, list):
                        # 如果是空数组，确保它保持为空数组
                        target[key] = tomlkit.array(str(value)) if value else tomlkit.array()
                    else:
                        # 其他类型使用item方法创建新值
                        target[key] = tomlkit.item(value)
                except (TypeError, ValueError):
                    # 如果转换失败，直接赋值
                    target[key] = value
        else:
            # 键不存在，从source添加新键到target
            try:
                if isinstance(value, dict):
                    # 对于字典类型，创建新的Table
                    new_table = tomlkit.table()
                    _update_dict(new_table, value)
                    target[key] = new_table
                elif isinstance(value, list):
                    # 对于数组类型
                    target[key] = tomlkit.array(str(value)) if value else tomlkit.array()
                else:
                    # 其他类型使用item方法创建新值
                    target[key] = tomlkit.item(value)
            except (TypeError, ValueError):
                # 如果转换失败，直接赋值
                target[key] = value


def _update_config_generic(config_name: str, template_name: str):
    """
    通用的配置文件更新函数

    Args:
        config_name: 配置文件名（不含扩展名），如 'bot_config' 或 'model_config'
        template_name: 模板文件名（不含扩展名），如 'bot_config_template' 或 'model_config_template'
    """
    # 获取根目录路径
    old_config_dir = os.path.join(CONFIG_DIR, "old")
    compare_dir = os.path.join(TEMPLATE_DIR, "compare")

    # 定义文件路径
    template_path = os.path.join(TEMPLATE_DIR, f"{template_name}.toml")
    old_config_path = os.path.join(CONFIG_DIR, f"{config_name}.toml")
    new_config_path = os.path.join(CONFIG_DIR, f"{config_name}.toml")
    compare_path = os.path.join(compare_dir, f"{template_name}.toml")

    # 创建compare目录（如果不存在）
    os.makedirs(compare_dir, exist_ok=True)

    template_version = _get_version_from_toml(template_path)
    compare_version = _get_version_from_toml(compare_path)

    # 检查配置文件是否存在
    if not os.path.exists(old_config_path):
        logger.info(f"{config_name}.toml配置文件不存在，从模板创建新配置")
        os.makedirs(CONFIG_DIR, exist_ok=True)  # 创建文件夹
        shutil.copy2(template_path, old_config_path)  # 复制模板文件
        logger.info(f"已创建新{config_name}配置文件，请填写后重新运行: {old_config_path}")
        # 新创建配置文件，退出
        sys.exit(0)

    compare_config = None
    new_config = None
    old_config = None

    # 先读取 compare 下的模板（如果有），用于默认值变动检测
    if os.path.exists(compare_path):
        with open(compare_path, encoding="utf-8") as f:
            compare_config = tomlkit.load(f)

    # 读取当前模板
    with open(template_path, encoding="utf-8") as f:
        new_config = tomlkit.load(f)

    # 检查默认值变化并处理（只有 compare_config 存在时才做）
    if compare_config:
        # 读取旧配置
        with open(old_config_path, encoding="utf-8") as f:
            old_config = tomlkit.load(f)
        logs, changes = compare_default_values(new_config, compare_config)
        if logs:
            logger.info(f"检测到{config_name}模板默认值变动如下：")
            for log in logs:
                logger.info(log)
            # 检查旧配置是否等于旧默认值，如果是则更新为新默认值
            for path, old_default, new_default in changes:
                old_value = get_value_by_path(old_config, path)
                if old_value == old_default:
                    set_value_by_path(old_config, path, new_default)
                    logger.info(
                        f"已自动将{config_name}配置 {'.'.join(path)} 的值从旧默认值 {old_default} 更新为新默认值 {new_default}"
                    )
        else:
            logger.info(f"未检测到{config_name}模板默认值变动")

    # 检查 compare 下没有模板，或新模板版本更高，则复制
    if not os.path.exists(compare_path):
        shutil.copy2(template_path, compare_path)
        logger.info(f"已将{config_name}模板文件复制到: {compare_path}")
    elif _version_tuple(template_version) > _version_tuple(compare_version):
        shutil.copy2(template_path, compare_path)
        logger.info(f"{config_name}模板版本较新，已替换compare下的模板: {compare_path}")
    else:
        logger.debug(f"compare下的{config_name}模板版本不低于当前模板，无需替换: {compare_path}")

    # 读取旧配置文件和模板文件（如果前面没读过 old_config，这里再读一次）
    if old_config is None:
        with open(old_config_path, encoding="utf-8") as f:
            old_config = tomlkit.load(f)
    # new_config 已经读取

    # 检查version是否相同
    if old_config and "inner" in old_config and "inner" in new_config:
        old_version = old_config["inner"].get("version")  # type: ignore
        new_version = new_config["inner"].get("version")  # type: ignore
        if old_version and new_version and old_version == new_version:
            logger.info(f"检测到{config_name}配置文件版本号相同 (v{old_version})，跳过更新")
            return
        else:
            logger.info(
                f"\n----------------------------------------\n检测到{config_name}版本号不同: 旧版本 v{old_version} -> 新版本 v{new_version}\n----------------------------------------"
            )
    else:
        logger.info(f"已有{config_name}配置文件未检测到版本号，可能是旧版本。将进行更新")

    # 创建old目录（如果不存在）
    os.makedirs(old_config_dir, exist_ok=True)  # 生成带时间戳的新文件名
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    old_backup_path = os.path.join(old_config_dir, f"{config_name}_{timestamp}.toml")

    # 移动旧配置文件到old目录
    shutil.move(old_config_path, old_backup_path)
    logger.info(f"已备份旧{config_name}配置文件到: {old_backup_path}")

    # 复制模板文件到配置目录
    shutil.copy2(template_path, new_config_path)
    logger.info(f"已创建新{config_name}配置文件: {new_config_path}")

    # 输出新增和删减项及注释
    if old_config:
        logger.info(f"{config_name}配置项变动如下：\n----------------------------------------")
        if logs := compare_dicts(new_config, old_config):
            for log in logs:
                logger.info(log)
        else:
            logger.info("无新增或删减项")

    # 将旧配置的值更新到新配置中
    logger.info(f"开始合并{config_name}新旧配置...")
    _update_dict(new_config, old_config)

    # 移除在新模板中已不存在的旧配置项
    logger.info(f"开始移除{config_name}中已废弃的配置项...")
    with open(template_path, encoding="utf-8") as f:
        template_doc = tomlkit.load(f)
    _remove_obsolete_keys(new_config, template_doc)
    logger.info(f"已移除{config_name}中已废弃的配置项")

    # 保存更新后的配置（保留注释和格式）
    with open(new_config_path, "w", encoding="utf-8") as f:
        f.write(tomlkit.dumps(new_config))
    logger.info(f"{config_name}配置文件更新完成，建议检查新配置文件中的内容，以免丢失重要信息")


def update_config():
    """更新bot_config.toml配置文件"""
    _update_config_generic("bot_config", "bot_config_template")


def update_model_config():
    """更新model_config.toml配置文件"""
    _update_config_generic("model_config", "model_config_template")


class Config(ValidatedConfigBase):
    """总配置类"""

    MMC_VERSION: str = Field(default=MMC_VERSION, description="MaiCore版本号")

    database: DatabaseConfig = Field(..., description="数据库配置")
    bot: BotConfig = Field(..., description="机器人基本配置")
    personality: PersonalityConfig = Field(..., description="个性配置")
    chat: ChatConfig = Field(..., description="聊天配置")
    message_receive: MessageReceiveConfig = Field(..., description="消息接收配置")
    notice: NoticeConfig = Field(..., description="Notice消息配置")
    emoji: EmojiConfig = Field(..., description="表情配置")
    expression: ExpressionConfig = Field(..., description="表达配置")
    memory: MemoryConfig | None = Field(default=None, description="记忆配置")
    mood: MoodConfig = Field(..., description="情绪配置")
    reaction: ReactionConfig = Field(default_factory=ReactionConfig, description="反应规则配置")
    chinese_typo: ChineseTypoConfig = Field(..., description="中文错别字配置")
    response_post_process: ResponsePostProcessConfig = Field(..., description="响应后处理配置")
    response_splitter: ResponseSplitterConfig = Field(..., description="响应分割配置")
    experimental: ExperimentalConfig = Field(default_factory=lambda: ExperimentalConfig(), description="实验性功能配置")
    maim_message: MaimMessageConfig = Field(..., description="Maim消息配置")
    lpmm_knowledge: LPMMKnowledgeConfig = Field(..., description="LPMM知识配置")
    tool: ToolConfig = Field(..., description="工具配置")
    debug: DebugConfig = Field(..., description="调试配置")
    custom_prompt: CustomPromptConfig = Field(..., description="自定义提示配置")

    voice: VoiceConfig = Field(..., description="语音配置")
    permission: PermissionConfig = Field(..., description="权限配置")
    command: CommandConfig = Field(..., description="命令系统配置")

    # 有默认值的字段放在后面
    video_analysis: VideoAnalysisConfig = Field(
        default_factory=lambda: VideoAnalysisConfig(), description="视频分析配置"
    )
    dependency_management: DependencyManagementConfig = Field(
        default_factory=lambda: DependencyManagementConfig(), description="依赖管理配置"
    )
    web_search: WebSearchConfig = Field(default_factory=lambda: WebSearchConfig(), description="网络搜索配置")
    planning_system: PlanningSystemConfig = Field(
        default_factory=lambda: PlanningSystemConfig(), description="规划系统配置"
    )
    cross_context: CrossContextConfig = Field(
        default_factory=lambda: CrossContextConfig(), description="跨群聊上下文共享配置"
    )
    affinity_flow: AffinityFlowConfig = Field(default_factory=lambda: AffinityFlowConfig(), description="亲和流配置")
    proactive_thinking: ProactiveThinkingConfig = Field(
        default_factory=lambda: ProactiveThinkingConfig(), description="主动思考配置"
    )
    plugin_http_system: PluginHttpSystemConfig = Field(
        default_factory=lambda: PluginHttpSystemConfig(), description="插件HTTP端点系统配置"
    )


class APIAdapterConfig(ValidatedConfigBase):
    """API Adapter配置类"""

    models: list[ModelInfo] = Field(..., min_items=1, description="模型列表")
    model_task_config: ModelTaskConfig = Field(..., description="模型任务配置")
    api_providers: list[APIProvider] = Field(..., min_items=1, description="API提供商列表")

    def __init__(self, **data):
        super().__init__(**data)
        self.api_providers_dict = {provider.name: provider for provider in self.api_providers}
        self.models_dict = {model.name: model for model in self.models}

    @classmethod
    def validate_models_list(cls, v):
        """验证模型列表"""
        if not v:
            raise ValueError("模型列表不能为空，请在配置中设置有效的模型列表。")

        # 检查模型名称是否重复
        model_names = [model.name for model in v]
        if len(model_names) != len(set(model_names)):
            raise ValueError("模型名称存在重复，请检查配置文件。")

        # 检查模型标识符是否有效
        for model in v:
            if not model.model_identifier:
                raise ValueError(f"模型 '{model.name}' 的 model_identifier 不能为空")

        return v

    @classmethod
    def validate_api_providers_list(cls, v):
        """验证API提供商列表"""
        if not v:
            raise ValueError("API提供商列表不能为空，请在配置中设置有效的API提供商列表。")

        # 检查API提供商名称是否重复
        provider_names = [provider.name for provider in v]
        if len(provider_names) != len(set(provider_names)):
            raise ValueError("API提供商名称存在重复，请检查配置文件。")

        return v

    def get_model_info(self, model_name: str) -> ModelInfo:
        """根据模型名称获取模型信息"""
        if not model_name:
            raise ValueError("模型名称不能为空")
        if model_name not in self.models_dict:
            raise KeyError(f"模型 '{model_name}' 不存在")
        return self.models_dict[model_name]

    def get_provider(self, provider_name: str) -> APIProvider:
        """根据提供商名称获取API提供商信息"""
        if not provider_name:
            raise ValueError("API提供商名称不能为空")
        if provider_name not in self.api_providers_dict:
            raise KeyError(f"API提供商 '{provider_name}' 不存在")
        return self.api_providers_dict[provider_name]


def load_config(config_path: str) -> Config:
    """
    加载配置文件
    Args:
        config_path: 配置文件路径
    Returns:
        Config对象
    """
    # 读取配置文件
    with open(config_path, encoding="utf-8") as f:
        config_data = tomlkit.load(f)

    # 创建Config对象（各个配置类会自动进行 Pydantic 验证）
    try:
        logger.info("正在解析和验证配置文件...")
        config = Config.from_dict(config_data)
        logger.info("配置文件解析和验证完成")
        return config
    except Exception as e:
        logger.critical(f"配置文件解析失败: {e}")
        raise e


def api_ada_load_config(config_path: str) -> APIAdapterConfig:
    """
    加载API适配器配置文件
    Args:
        config_path: 配置文件路径
    Returns:
        APIAdapterConfig对象
    """
    # 读取配置文件
    with open(config_path, encoding="utf-8") as f:
        config_data = tomlkit.load(f)

    config_dict = dict(config_data)

    try:
        logger.info("正在解析和验证API适配器配置文件...")
        config = APIAdapterConfig.from_dict(config_dict)
        logger.info("API适配器配置文件解析和验证完成")
        return config
    except Exception as e:
        logger.critical(f"API适配器配置文件解析失败: {e}")
        raise e


# 获取配置文件路径
logger.info(f"MaiCore当前版本: {MMC_VERSION}")
update_config()
update_model_config()

logger.info("正在品鉴配置文件...")
global_config = load_config(config_path=os.path.join(CONFIG_DIR, "bot_config.toml"))
model_config = api_ada_load_config(config_path=os.path.join(CONFIG_DIR, "model_config.toml"))

logger.info("非常的新鲜，非常的美味！")
