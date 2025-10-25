import datetime
import os
import shutil
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

import toml

from src.common.logger import get_logger
from src.config.config import CONFIG_DIR
from src.plugin_system.base.component_types import (
    PermissionNodeField,
    PluginInfo,
)
from src.plugin_system.base.config_types import ConfigField
from src.plugin_system.base.plugin_metadata import PluginMetadata

logger = get_logger("plugin_base")


class PluginBase(ABC):
    """插件总基类

    所有衍生插件基类都应该继承自此类，这个类定义了插件的基本结构和行为。
    """

    # 插件基本信息（子类必须定义）
    plugin_name: str
    config_file_name: str
    enable_plugin: bool = True

    config_schema: dict[str, dict[str, ConfigField] | str] = {}

    permission_nodes: list["PermissionNodeField"] = []

    config_section_descriptions: dict[str, str] = {}

    def __init__(self, plugin_dir: str, metadata: PluginMetadata):
        """初始化插件

        Args:
            plugin_dir: 插件目录路径，由插件管理器传递
            metadata: 插件元数据对象
        """
        self.config: dict[str, Any] = {}  # 插件配置
        self.plugin_dir = plugin_dir  # 插件目录路径
        self.plugin_meta = metadata  # 插件元数据
        self.log_prefix = f"[Plugin:{self.plugin_name}]"
        self._is_enabled = self.enable_plugin  # 从插件定义中获取默认启用状态

        # 验证插件信息
        self._validate_plugin_info()

        # 加载插件配置
        self._load_plugin_config()

        # 从元数据获取显示信息
        self.display_name = self.plugin_meta.name
        self.plugin_version = self.plugin_meta.version
        self.plugin_description = self.plugin_meta.description
        self.plugin_author = self.plugin_meta.author

        # 创建插件信息对象
        self.plugin_info = PluginInfo(
            name=self.plugin_name,
            display_name=self.display_name,
            description=self.plugin_description,
            version=self.plugin_version,
            author=self.plugin_author,
            enabled=self._is_enabled,
            is_built_in=False,
            config_file=self.config_file_name or "",
            dependencies=self.plugin_meta.dependencies.copy(),
            python_dependencies=self.plugin_meta.python_dependencies.copy(),
        )

        logger.debug(f"{self.log_prefix} 插件基类初始化完成")

    def _validate_plugin_info(self):
        """验证插件基本信息"""
        if not self.plugin_name:
            raise ValueError(f"插件类 {self.__class__.__name__} 必须定义 plugin_name")

        if not self.plugin_meta.name:
            raise ValueError(f"插件 {self.plugin_name} 的元数据中缺少 name 字段")
        if not self.plugin_meta.description:
            raise ValueError(f"插件 {self.plugin_name} 的元数据中缺少 description 字段")

    def _generate_and_save_default_config(self, config_file_path: str):
        """根据插件的Schema生成并保存默认配置文件"""
        if not self.config_schema:
            logger.info(f"{self.log_prefix} 插件未定义config_schema，不生成配置文件")
            return

        toml_str = f"# {self.plugin_name} - 自动生成的配置文件\n"
        plugin_description = self.plugin_meta.description or "插件配置文件"
        toml_str += f"# {plugin_description}\n\n"

        # 遍历每个配置节
        for section, fields in self.config_schema.items():
            # 添加节描述
            if section in self.config_section_descriptions:
                toml_str += f"# {self.config_section_descriptions[section]}\n"

            toml_str += f"[{section}]\n\n"

            # 遍历节内的字段
            if isinstance(fields, dict):
                for field_name, field in fields.items():
                    if isinstance(field, ConfigField):
                        # 添加字段描述
                        toml_str += f"# {field.description}"
                        if field.required:
                            toml_str += " (必需)"
                        toml_str += "\n"

                        # 如果有示例值，添加示例
                        if field.example:
                            toml_str += f"# 示例: {field.example}\n"

                        # 如果有可选值，添加说明
                        if field.choices:
                            choices_str = ", ".join(map(str, field.choices))
                            toml_str += f"# 可选值: {choices_str}\n"

                        # 添加字段值
                        value = field.default
                        if isinstance(value, str):
                            toml_str += f'{field_name} = "{value}"\n'
                        elif isinstance(value, bool):
                            toml_str += f"{field_name} = {str(value).lower()}\n"
                        else:
                            toml_str += f"{field_name} = {value}\n"

                        toml_str += "\n"
            toml_str += "\n"

        try:
            with open(config_file_path, "w", encoding="utf-8") as f:
                f.write(toml_str)
            logger.info(f"{self.log_prefix} 已生成默认配置文件: {config_file_path}")
        except OSError as e:
            logger.error(f"{self.log_prefix} 保存默认配置文件失败: {e}", exc_info=True)

    def _backup_config_file(self, config_file_path: str) -> str:
        """备份配置文件到指定的 backup 子目录"""
        try:
            config_path = Path(config_file_path)
            backup_dir = config_path.parent / "backup"
            backup_dir.mkdir(exist_ok=True)

            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_filename = f"{config_path.name}.backup_{timestamp}"
            backup_path = backup_dir / backup_filename

            shutil.copy2(config_file_path, backup_path)
            logger.info(f"{self.log_prefix} 配置文件已备份到: {backup_path}")
            return str(backup_path)
        except Exception as e:
            logger.error(f"{self.log_prefix} 备份配置文件失败: {e}", exc_info=True)
            return ""

    def _synchronize_config(
        self, schema_config: dict[str, Any], user_config: dict[str, Any]
    ) -> tuple[dict[str, Any], bool]:
        """递归地将用户配置与 schema 同步，返回同步后的配置和是否发生变化的标志"""
        changed = False

        # 内部递归函数
        def _sync_dicts(schema_dict: dict[str, Any], user_dict: dict[str, Any], parent_key: str = "") -> dict[str, Any]:
            nonlocal changed
            synced_dict = schema_dict.copy()

            # 检查并记录用户配置中多余的、在 schema 中不存在的键
            for key in user_dict:
                if key not in schema_dict:
                    logger.warning(f"{self.log_prefix} 发现废弃配置项 '{parent_key}{key}'，将被移除。")
                    changed = True

            # 以 schema 为基准进行遍历，保留用户的值，补全缺失的项
            for key, schema_value in schema_dict.items():
                full_key = f"{parent_key}{key}"
                if key in user_dict:
                    user_value = user_dict[key]
                    if isinstance(schema_value, dict) and isinstance(user_value, dict):
                        # 递归同步嵌套的字典
                        synced_dict[key] = _sync_dicts(schema_value, user_value, f"{full_key}.")
                    else:
                        # 键存在，保留用户的值
                        synced_dict[key] = user_value
                else:
                    # 键在用户配置中缺失，补全
                    logger.info(f"{self.log_prefix} 补全缺失的配置项: '{full_key}' = {schema_value}")
                    changed = True
                    # synced_dict[key] 已经包含了来自 schema_dict.copy() 的默认值

            return synced_dict

        final_config = _sync_dicts(schema_config, user_config)
        return final_config, changed

    def _generate_config_from_schema(self) -> dict[str, Any]:
        # sourcery skip: dict-comprehension
        """根据schema生成配置数据结构（不写入文件）"""
        if not self.config_schema:
            return {}

        config_data = {}

        # 遍历每个配置节
        for section, fields in self.config_schema.items():
            if isinstance(fields, dict):
                section_data = {}

                # 遍历节内的字段
                for field_name, field in fields.items():
                    if isinstance(field, ConfigField):
                        section_data[field_name] = field.default

                config_data[section] = section_data

        return config_data

    def _save_config_to_file(self, config_data: dict[str, Any], config_file_path: str):
        """将配置数据保存为TOML文件（包含注释）"""
        if not self.config_schema:
            logger.debug(f"{self.log_prefix} 插件未定义config_schema，不生成配置文件")
            return

        toml_str = f"# {self.plugin_name} - 配置文件\n"
        plugin_description = self.plugin_meta.description or "插件配置文件"
        toml_str += f"# {plugin_description}\n\n"

        # 遍历每个配置节
        for section, fields in self.config_schema.items():
            # 添加节描述
            if section in self.config_section_descriptions:
                toml_str += f"# {self.config_section_descriptions[section]}\n"

            toml_str += f"[{section}]\n\n"

            # 遍历节内的字段
            if isinstance(fields, dict) and section in config_data:
                section_data = config_data[section]

                for field_name, field in fields.items():
                    if isinstance(field, ConfigField):
                        # 添加字段描述
                        toml_str += f"# {field.description}"
                        if field.required:
                            toml_str += " (必需)"
                        toml_str += "\n"

                        # 如果有示例值，添加示例
                        if field.example:
                            toml_str += f"# 示例: {field.example}\n"

                        # 如果有可选值，添加说明
                        if field.choices:
                            choices_str = ", ".join(map(str, field.choices))
                            toml_str += f"# 可选值: {choices_str}\n"

                        # 添加字段值（使用迁移后的值）
                        value = section_data.get(field_name, field.default)
                        if isinstance(value, str):
                            toml_str += f'{field_name} = "{value}"\n'
                        elif isinstance(value, bool):
                            toml_str += f"{field_name} = {str(value).lower()}\n"
                        elif isinstance(value, list):
                            # 格式化列表
                            if all(isinstance(item, str) for item in value):
                                formatted_list = "[" + ", ".join(f'"{item}"' for item in value) + "]"
                            else:
                                formatted_list = str(value)
                            toml_str += f"{field_name} = {formatted_list}\n"
                        else:
                            toml_str += f"{field_name} = {value}\n"

                        toml_str += "\n"
            toml_str += "\n"

        try:
            with open(config_file_path, "w", encoding="utf-8") as f:
                f.write(toml_str)
            logger.info(f"{self.log_prefix} 配置文件已保存: {config_file_path}")
        except OSError as e:
            logger.error(f"{self.log_prefix} 保存配置文件失败: {e}", exc_info=True)

    def _load_plugin_config(self):  # sourcery skip: extract-method
        """
        加载并同步插件配置文件。

        处理逻辑:
        1. 确定用户配置文件路径和插件自带的配置文件路径。
        2. 如果用户配置文件不存在，尝试从插件目录迁移（移动）一份。
        3. 如果迁移后（或原本）用户配置文件仍不存在，则根据 schema 生成一份。
        4. 加载用户配置文件。
        5. 以 schema 为基准，与用户配置进行同步，补全缺失项并移除废弃项。
        6. 如果同步过程发现不一致，则先备份原始文件，然后将同步后的完整配置写回用户目录。
        7. 将最终同步后的配置加载到 self.config。
        """
        if not self.config_file_name:
            logger.debug(f"{self.log_prefix} 未指定配置文件，跳过加载")
            return

        user_config_path = os.path.join(CONFIG_DIR, "plugins", self.plugin_name, self.config_file_name)
        plugin_config_path = os.path.join(self.plugin_dir, self.config_file_name)
        os.makedirs(os.path.dirname(user_config_path), exist_ok=True)

        # 首次加载迁移：如果用户配置不存在，但插件目录中存在，则移动过来
        if not os.path.exists(user_config_path) and os.path.exists(plugin_config_path):
            try:
                shutil.move(plugin_config_path, user_config_path)
                logger.info(f"{self.log_prefix} 已将配置文件从 {plugin_config_path} 迁移到 {user_config_path}")
            except OSError as e:
                logger.error(f"{self.log_prefix} 迁移配置文件失败: {e}", exc_info=True)

        # 如果用户配置文件仍然不存在，生成默认的
        if not os.path.exists(user_config_path):
            logger.info(f"{self.log_prefix} 用户配置文件 {user_config_path} 不存在，将生成默认配置。")
            self._generate_and_save_default_config(user_config_path)

        if not os.path.exists(user_config_path):
            if not self.config_schema:
                logger.debug(f"{self.log_prefix} 插件未定义 config_schema，使用空配置。")
                self.config = {}
            else:
                logger.warning(f"{self.log_prefix} 用户配置文件 {user_config_path} 不存在且无法创建。")
            return

        try:
            with open(user_config_path, encoding="utf-8") as f:
                user_config = toml.load(f) or {}
        except Exception as e:
            logger.error(f"{self.log_prefix} 加载用户配置文件 {user_config_path} 失败: {e}", exc_info=True)
            self.config = self._generate_config_from_schema()  # 加载失败时使用默认 schema
            return

        # 生成基于 schema 的理想配置结构
        schema_config = self._generate_config_from_schema()

        # 将用户配置与 schema 同步
        synced_config, was_changed = self._synchronize_config(schema_config, user_config)

        # 如果配置发生了变化（补全或移除），则备份并重写配置文件
        if was_changed:
            logger.info(f"{self.log_prefix} 检测到配置结构不匹配，将自动同步并更新配置文件。")
            self._backup_config_file(user_config_path)
            self._save_config_to_file(synced_config, user_config_path)
            logger.info(f"{self.log_prefix} 配置文件已同步更新。")

        self.config = synced_config
        logger.debug(f"{self.log_prefix} 配置已从 {user_config_path} 加载并同步。")

        # 从最终配置中更新插件启用状态
        if "plugin" in self.config and "enabled" in self.config["plugin"]:
            self._is_enabled = self.config["plugin"]["enabled"]
            logger.info(f"{self.log_prefix} 从配置更新插件启用状态: {self._is_enabled}")

    def get_config(self, key: str, default: Any = None) -> Any:
        """获取插件配置值，支持嵌套键访问

        Args:
            key: 配置键名，支持嵌套访问如 "section.subsection.key"
            default: 默认值

        Returns:
            Any: 配置值或默认值
        """
        # 支持嵌套键访问
        keys = key.split(".")
        current = self.config

        for k in keys:
            if isinstance(current, dict) and k in current:
                current = current[k]
            else:
                return default

        return current

    @abstractmethod
    def register_plugin(self) -> bool:
        """
        注册插件到插件管理器

        子类必须实现此方法，返回注册是否成功

        Returns:
            bool: 是否成功注册插件
        """
        raise NotImplementedError("Subclasses must implement this method")

    async def on_plugin_loaded(self):
        """插件加载完成后的钩子函数"""
        pass

    def on_unload(self):
        """插件卸载时的钩子函数"""
        pass
