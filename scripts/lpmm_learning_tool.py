import asyncio
import datetime
import os
import shutil
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from threading import Lock

import orjson
from json_repair import repair_json

# 将项目根目录添加到 sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)

from src.chat.knowledge.embedding_store import EmbeddingManager
from src.chat.knowledge.kg_manager import KGManager
from src.chat.knowledge.open_ie import OpenIE
from src.chat.knowledge.utils.hash import get_sha256
from src.common.logger import get_logger
from src.config.config import model_config
from src.llm_models.utils_model import LLMRequest

logger = get_logger("LPMM_LearningTool")
ROOT_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
RAW_DATA_PATH = os.path.join(ROOT_PATH, "data", "lpmm_raw_data")
OPENIE_OUTPUT_DIR = os.path.join(ROOT_PATH, "data", "openie")
TEMP_DIR = os.path.join(ROOT_PATH, "temp", "lpmm_cache")
file_lock = Lock()

# --- 缓存清理 ---


def clear_cache():
    """清理 lpmm_learning_tool.py 生成的缓存文件"""
    logger.info("--- 开始清理缓存 ---")
    if os.path.exists(TEMP_DIR):
        try:
            shutil.rmtree(TEMP_DIR)
            logger.info(f"成功删除缓存目录: {TEMP_DIR}")
        except OSError as e:
            logger.error(f"删除缓存时出错: {e}")
    else:
        logger.info("缓存目录不存在，无需清理。")
    logger.info("--- 缓存清理完成 ---")


# --- 模块一：数据预处理 ---


def process_text_file(file_path):
    with open(file_path, encoding="utf-8") as f:
        raw = f.read()
    return [p.strip() for p in raw.split("\n\n") if p.strip()]


def preprocess_raw_data():
    logger.info("--- 步骤 1: 开始数据预处理 ---")
    os.makedirs(RAW_DATA_PATH, exist_ok=True)
    raw_files = list(Path(RAW_DATA_PATH).glob("*.txt"))
    if not raw_files:
        logger.warning(f"警告: 在 '{RAW_DATA_PATH}' 中没有找到任何 .txt 文件")
        return []

    all_paragraphs = []
    for file in raw_files:
        logger.info(f"正在处理文件: {file.name}")
        all_paragraphs.extend(process_text_file(file))

    unique_paragraphs = {get_sha256(p): p for p in all_paragraphs}
    logger.info(f"共找到 {len(all_paragraphs)} 个段落，去重后剩余 {len(unique_paragraphs)} 个。")
    logger.info("--- 数据预处理完成 ---")
    return unique_paragraphs


# --- 模块二：信息提取 ---


def _parse_and_repair_json(json_string: str) -> dict | None:
    """
    尝试解析JSON字符串，如果失败则尝试修复并重新解析。

    该函数首先会清理字符串，去除常见的Markdown代码块标记，
    然后尝试直接解析。如果解析失败，它会调用 `repair_json`
    进行修复，并再次尝试解析。

    Args:
        json_string: 从LLM获取的、可能格式不正确的JSON字符串。

    Returns:
        解析后的字典。如果最终无法解析，则返回 None，并记录详细错误日志。
    """
    if not isinstance(json_string, str):
        logger.error(f"输入内容非字符串，无法解析: {type(json_string)}")
        return None

    # 1. 预处理：去除常见的多余字符，如Markdown代码块标记
    cleaned_string = json_string.strip()
    if cleaned_string.startswith("```json"):
        cleaned_string = cleaned_string[7:].strip()
    elif cleaned_string.startswith("```"):
        cleaned_string = cleaned_string[3:].strip()

    if cleaned_string.endswith("```"):
        cleaned_string = cleaned_string[:-3].strip()

    # 2. 性能优化：乐观地尝试直接解析
    try:
        return orjson.loads(cleaned_string)
    except orjson.JSONDecodeError:
        logger.warning("直接解析JSON失败，将尝试修复...")

        # 3. 修复与最终解析
        repaired_json_str = ""
        try:
            repaired_json_str = repair_json(cleaned_string)
            return orjson.loads(repaired_json_str)
        except Exception as e:
            # 4. 增强错误处理：记录详细的失败信息
            logger.error(f"修复并解析JSON后依然失败: {e}")
            logger.error(f"原始字符串 (清理后): {cleaned_string}")
            logger.error(f"修复后尝试解析的字符串: {repaired_json_str}")
            return None


def get_extraction_prompt(paragraph: str) -> str:
    return f"""
请从以下段落中提取关键信息。你需要提取两种类型的信息：
1.  **实体 (Entities)**: 识别并列出段落中所有重要的名词或名词短语。
2.  **三元组 (Triples)**: 以 [主语, 谓语, 宾语] 的格式，提取段落中描述关系或事实的核心信息。

请严格按照以下 JSON 格式返回结果，不要添加任何额外的解释或注释：
{{
    "entities": ["实体1", "实体2"],
    "triples": [["主语1", "谓语1", "宾语1"]]
}}

这是你需要处理的段落：
---
{paragraph}
---
"""


async def extract_info_async(pg_hash, paragraph, llm_api):
    temp_file_path = os.path.join(TEMP_DIR, f"{pg_hash}.json")
    with file_lock:
        if os.path.exists(temp_file_path):
            try:
                with open(temp_file_path, "rb") as f:
                    return orjson.loads(f.read()), None
            except orjson.JSONDecodeError:
                os.remove(temp_file_path)

    prompt = get_extraction_prompt(paragraph)
    content = None
    try:
        content, (_, _, _) = await llm_api.generate_response_async(prompt)

        # 改进点：调用封装好的函数处理JSON解析和修复
        extracted_data = _parse_and_repair_json(content)

        if extracted_data is None:
            # 如果解析失败，抛出异常以触发统一的错误处理逻辑
            raise ValueError("无法从LLM输出中解析有效的JSON数据")

        doc_item = {
            "idx": pg_hash,
            "passage": paragraph,
            "extracted_entities": extracted_data.get("entities", []),
            "extracted_triples": extracted_data.get("triples", []),
        }
        with file_lock:
            with open(temp_file_path, "wb") as f:
                f.write(orjson.dumps(doc_item))
        return doc_item, None
    except Exception as e:
        logger.error(f"提取信息失败：{pg_hash}, 错误：{e}")
        if content:
            logger.error(f"导致解析失败的原始输出: {content}")
        return None, pg_hash


async def extract_information(paragraphs_dict, model_set):
    logger.info("--- 步骤 2: 开始信息提取 ---")
    os.makedirs(OPENIE_OUTPUT_DIR, exist_ok=True)
    os.makedirs(TEMP_DIR, exist_ok=True)

    llm_api = LLMRequest(model_set=model_set)
    failed_hashes, open_ie_docs = [], []

    tasks = [
        extract_info_async(p_hash, p, llm_api)
        for p_hash, p in paragraphs_dict.items()
    ]

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        MofNCompleteColumn(),
        "•",
        TimeElapsedColumn(),
        "<",
        TimeRemainingColumn(),
    ) as progress:
        prog_task = progress.add_task("[cyan]正在提取信息...", total=len(tasks))
        for future in asyncio.as_completed(tasks):
            doc_item, failed_hash = await future
            if failed_hash:
                failed_hashes.append(failed_hash)
            elif doc_item:
                open_ie_docs.append(doc_item)
            progress.update(prog_task, advance=1)

    if open_ie_docs:
        all_entities = [
            e for doc in open_ie_docs for e in doc["extracted_entities"]
        ]
        num_entities = len(all_entities)
        avg_ent_chars = round(sum(len(e) for e in all_entities) / num_entities, 4) if num_entities else 0
        avg_ent_words = round(sum(len(e.split()) for e in all_entities) / num_entities, 4) if num_entities else 0
        openie_obj = OpenIE(docs=open_ie_docs, avg_ent_chars=avg_ent_chars, avg_ent_words=avg_ent_words)

        now = datetime.datetime.now()
        filename = now.strftime("%Y-%m-%d-%H-%M-%S-openie.json")
        output_path = os.path.join(OPENIE_OUTPUT_DIR, filename)
        with open(output_path, "wb") as f:
            f.write(orjson.dumps(openie_obj._to_dict()))
        logger.info(f"信息提取结果已保存到: {output_path}")

    if failed_hashes:
        logger.error(f"以下 {len(failed_hashes)} 个段落提取失败: {failed_hashes}")
    logger.info("--- 信息提取完成 ---")


# --- 模块三：数据导入 ---


async def import_data(openie_obj: OpenIE | None = None):
    """
    将OpenIE数据导入知识库（Embedding Store 和 KG）

    Args:
        openie_obj (Optional[OpenIE], optional): 如果提供，则直接使用这个OpenIE对象；
                                                 否则，将自动从默认文件夹加载最新的OpenIE文件。
                                                 默认为 None.
    """
    logger.info("--- 步骤 3: 开始数据导入 ---")
    embed_manager, kg_manager = EmbeddingManager(), KGManager()

    logger.info("正在加载现有的 Embedding 库...")
    try:
        embed_manager.load_from_file()
    except Exception as e:
        logger.warning(f"加载 Embedding 库失败: {e}。")

    logger.info("正在加载现有的 KG...")
    try:
        kg_manager.load_from_file()
    except Exception as e:
        logger.warning(f"加载 KG 失败: {e}。")

    try:
        if openie_obj:
            openie_data = openie_obj
            logger.info("已使用指定的 OpenIE 对象。")
        else:
            openie_data = OpenIE.load()
    except Exception as e:
        logger.error(f"加载OpenIE数据文件失败: {e}")
        return

    raw_paragraphs = openie_data.extract_raw_paragraph_dict()
    triple_list_data = openie_data.extract_triple_dict()

    new_raw_paragraphs, new_triple_list_data = {}, {}
    stored_embeds = embed_manager.stored_pg_hashes
    stored_kgs = kg_manager.stored_paragraph_hashes

    for p_hash, raw_p in raw_paragraphs.items():
        if p_hash not in stored_embeds and p_hash not in stored_kgs:
            new_raw_paragraphs[p_hash] = raw_p
            new_triple_list_data[p_hash] = triple_list_data.get(p_hash, [])

    if not new_raw_paragraphs:
        logger.info("没有新的段落需要处理。")
    else:
        logger.info(f"去重完成，发现 {len(new_raw_paragraphs)} 个新段落。")
        logger.info("开始生成 Embedding...")
        embed_manager.store_new_data_set(new_raw_paragraphs, new_triple_list_data)
        embed_manager.rebuild_faiss_index()
        embed_manager.save_to_file()
        logger.info("Embedding 处理完成！")

        logger.info("开始构建 KG...")
        kg_manager.build_kg(new_triple_list_data, embed_manager)
        kg_manager.save_to_file()
        logger.info("KG 构建完成！")

    logger.info("--- 数据导入完成 ---")


async def import_from_specific_file():
    """从用户指定的 openie.json 文件导入数据"""
    file_path = input("请输入 openie.json 文件的完整路径: ").strip()

    if not os.path.exists(file_path):
        logger.error(f"文件路径不存在: {file_path}")
        return

    if not file_path.endswith(".json"):
        logger.error("请输入一个有效的 .json 文件路径。")
        return

    try:
        logger.info(f"正在从 {file_path} 加载 OpenIE 数据...")
        openie_obj = OpenIE.load()
        await import_data(openie_obj=openie_obj)
    except Exception as e:
        logger.error(f"从指定文件导入数据时发生错误: {e}")


# --- 主函数 ---


async def async_main():
    # 使用 os.path.relpath 创建相对于项目根目录的友好路径
    raw_data_relpath = os.path.relpath(
        RAW_DATA_PATH, os.path.join(ROOT_PATH, "..")
    )
    openie_output_relpath = os.path.relpath(
        OPENIE_OUTPUT_DIR, os.path.join(ROOT_PATH, "..")
    )

    print("=== LPMM 知识库学习工具 ===")
    print(f"1. [数据预处理] -> 读取 .txt 文件 (来源: ./{raw_data_relpath}/)")
    print(
        f"2. [信息提取] -> 提取信息并存为 .json (输出至: ./{openie_output_relpath}/)"
    )
    print("3. [数据导入] -> 从 openie 文件夹自动导入最新知识")
    print("4. [全流程] -> 按顺序执行 1 -> 2 -> 3")
    print("5. [指定导入] -> 从特定的 openie.json 文件导入知识")
    print("6. [清理缓存] -> 删除所有已提取信息的缓存")
    print("0. [退出]")
    print("-" * 30)
    choice = input("请输入你的选择 (0-5): ").strip()

    if choice == "1":
        preprocess_raw_data()
    elif choice == "2":
        paragraphs = preprocess_raw_data()
        if paragraphs:
            await extract_information(
                paragraphs, model_config.model_task_config.lpmm_qa
            )
    elif choice == "3":
        await import_data()
    elif choice == "4":
        paragraphs = preprocess_raw_data()
        if paragraphs:
            await extract_information(
                paragraphs, model_config.model_task_config.lpmm_qa
            )
            await import_data()
    elif choice == "5":
        await import_from_specific_file()
    elif choice == "6":
        clear_cache()
    elif choice == "0":
        sys.exit(0)
    else:
        print("无效输入，请重新运行脚本。")


if __name__ == "__main__":
    asyncio.run(async_main())
