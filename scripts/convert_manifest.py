import os
import shutil
import sys
from pathlib import Path

import orjson

# 将脚本所在的目录添加到系统路径中，以便导入项目模块
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from src.common.logger import get_logger

logger = get_logger("convert_manifest")

def convert_and_copy_plugin(plugin_dir: Path, output_dir: Path):
    """
    转换插件的 _manifest.json 文件，并将其整个目录复制到输出位置。
    """
    manifest_path = plugin_dir / "_manifest.json"
    if not manifest_path.is_file():
        logger.warning(f"在目录 '{plugin_dir.name}' 中未找到 '_manifest.json'，已跳过。")
        return

    try:
        # 1. 复制整个插件目录
        target_plugin_dir = output_dir / plugin_dir.name
        if target_plugin_dir.exists():
            shutil.rmtree(target_plugin_dir)  # 如果目标已存在，先删除
        shutil.copytree(plugin_dir, target_plugin_dir)
        logger.info(f"已将插件 '{plugin_dir.name}' 完整复制到 '{target_plugin_dir}'")

        # 2. 读取 manifest 并生成 __init__.py 内容
        with open(manifest_path, "rb") as f:
            manifest = orjson.loads(f.read())

        plugin_name = manifest.get("name", "Unknown Plugin")
        description = manifest.get("description", "No description provided.")
        version = manifest.get("version", "0.0.0")
        author = manifest.get("author", {}).get("name", "Unknown Author")
        license_type = manifest.get("license", "N/A")

        meta_template = f"""from src.plugin_system.base.plugin_metadata import PluginMetadata

__plugin_meta__ = PluginMetadata(
    name="{plugin_name}",
    description="{description}",
    usage="暂无说明",
    version="{version}",
    author="{author}",
    license="{license_type}",
)
"""
        # 3. 在复制后的目录中创建或覆盖 __init__.py
        output_init_path = target_plugin_dir / "__init__.py"
        with open(output_init_path, "w", encoding="utf-8") as f:
            f.write(meta_template)

        # 4. 删除复制后的 _manifest.json
        copied_manifest_path = target_plugin_dir / "_manifest.json"
        if copied_manifest_path.is_file():
            copied_manifest_path.unlink()

        logger.info(f"成功为 '{plugin_dir.name}' 创建元数据文件并清理清单。")

    except FileNotFoundError:
        logger.error(f"错误: 在 '{manifest_path}' 未找到清单文件")
    except orjson.JSONDecodeError:
        logger.error(f"错误: 无法解析 '{manifest_path}' 的 JSON 内容")
    except Exception as e:
        logger.error(f"处理 '{plugin_dir.name}' 时发生意外错误: {e}")

def main():
    """
    主函数，扫描 "plugins" 目录，并将合格的插件转换并复制到 "completed_plugins" 目录。
    """
    # 使用相对于脚本位置的固定路径
    script_dir = Path(__file__).parent
    input_path = script_dir / "pending_plugins"
    output_path = script_dir / "completed_plugins"

    if not input_path.is_dir():
        logger.error(f"错误: 输入目录 '{input_path}' 不存在。")
        input_path.mkdir(parents=True, exist_ok=True)
        logger.info("请在新建的文件夹里面投入插件文件夹并重新启动脚本")
        return

    output_path.mkdir(parents=True, exist_ok=True)

    logger.info(f"正在扫描 '{input_path}' 中的插件...")
    for item in input_path.iterdir():
        if item.is_dir():
            logger.info(f"发现插件目录: '{item.name}'，开始处理...")
            convert_and_copy_plugin(item, output_path)

    logger.info("所有插件处理完成。")

if __name__ == "__main__":
    main()
