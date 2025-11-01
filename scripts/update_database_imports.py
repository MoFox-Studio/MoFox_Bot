"""批量更新数据库导入语句的脚本

将旧的数据库导入路径更新为新的重构后的路径：
- sqlalchemy_models -> core, core.models
- sqlalchemy_database_api -> compatibility
- database.database -> core
"""

import re
from pathlib import Path

# 定义导入映射规则
IMPORT_MAPPINGS = {
    # 模型导入
    r"from src\.common\.database\.sqlalchemy_models import (.+)":
        r"from src.common.database.core.models import \1",

    # API导入 - 需要特殊处理
    r"from src\.common\.database\.sqlalchemy_database_api import (.+)":
        r"from src.common.database.compatibility import \1",

    # get_db_session 从 sqlalchemy_database_api 导入
    r"from src\.common\.database\.sqlalchemy_database_api import get_db_session":
        r"from src.common.database.core import get_db_session",

    # get_db_session 从 sqlalchemy_models 导入
    r"from src\.common\.database\.sqlalchemy_models import (.*)get_db_session(.*)":
        lambda m: f"from src.common.database.core import {m.group(1)}get_db_session{m.group(2)}"
        if "get_db_session" in m.group(0) else m.group(0),

    # get_engine 导入
    r"from src\.common\.database\.sqlalchemy_models import (.*)get_engine(.*)":
        lambda m: f"from src.common.database.core import {m.group(1)}get_engine{m.group(2)}",

    # Base 导入
    r"from src\.common\.database\.sqlalchemy_models import (.*)Base(.*)":
        lambda m: f"from src.common.database.core.models import {m.group(1)}Base{m.group(2)}",

    # initialize_database 导入
    r"from src\.common\.database\.sqlalchemy_models import initialize_database":
        r"from src.common.database.core import check_and_migrate_database as initialize_database",

    # database.py 导入
    r"from src\.common\.database\.database import stop_database":
        r"from src.common.database.core import close_engine as stop_database",

    r"from src\.common\.database\.database import initialize_sql_database":
        r"from src.common.database.core import check_and_migrate_database as initialize_sql_database",
}

# 需要排除的文件
EXCLUDE_PATTERNS = [
    "**/database_refactoring_plan.md",  # 文档文件
    "**/old/**",  # 旧文件目录
    "**/sqlalchemy_*.py",  # 旧的数据库文件本身
    "**/database.py",  # 旧的database文件
    "**/db_*.py",  # 旧的db文件
]


def should_exclude(file_path: Path) -> bool:
    """检查文件是否应该被排除"""
    for pattern in EXCLUDE_PATTERNS:
        if file_path.match(pattern):
            return True
    return False


def update_imports_in_file(file_path: Path, dry_run: bool = True) -> tuple[int, list[str]]:
    """更新单个文件中的导入语句

    Args:
        file_path: 文件路径
        dry_run: 是否只是预览而不实际修改

    Returns:
        (修改次数, 修改详情列表)
    """
    try:
        content = file_path.read_text(encoding="utf-8")
        original_content = content
        changes = []

        # 应用每个映射规则
        for pattern, replacement in IMPORT_MAPPINGS.items():
            matches = list(re.finditer(pattern, content))
            for match in matches:
                old_line = match.group(0)

                # 处理函数类型的替换
                if callable(replacement):
                    new_line_result = replacement(match)
                    new_line = new_line_result if isinstance(new_line_result, str) else old_line
                else:
                    new_line = re.sub(pattern, replacement, old_line)

                if old_line != new_line and isinstance(new_line, str):
                    content = content.replace(old_line, new_line, 1)
                    changes.append(f"  - {old_line}")
                    changes.append(f"  + {new_line}")

        # 如果有修改且不是dry_run，写回文件
        if content != original_content:
            if not dry_run:
                file_path.write_text(content, encoding="utf-8")
            return len(changes) // 2, changes

        return 0, []

    except Exception as e:
        print(f"❌ 处理文件 {file_path} 时出错: {e}")
        return 0, []


def main():
    """主函数"""
    print("🔍 搜索需要更新导入的文件...")

    # 获取项目根目录
    root_dir = Path(__file__).parent.parent

    # 搜索所有Python文件
    all_python_files = list(root_dir.rglob("*.py"))

    # 过滤掉排除的文件
    target_files = [f for f in all_python_files if not should_exclude(f)]

    print(f"📊 找到 {len(target_files)} 个Python文件需要检查")
    print("\n" + "="*80)

    # 第一遍：预览模式
    print("\n🔍 预览模式 - 检查需要更新的文件...\n")

    files_to_update = []
    for file_path in target_files:
        count, changes = update_imports_in_file(file_path, dry_run=True)
        if count > 0:
            files_to_update.append((file_path, count, changes))

    if not files_to_update:
        print("✅ 没有文件需要更新！")
        return

    print(f"📝 发现 {len(files_to_update)} 个文件需要更新：\n")

    total_changes = 0
    for file_path, count, changes in files_to_update:
        rel_path = file_path.relative_to(root_dir)
        print(f"\n📄 {rel_path} ({count} 处修改)")
        for change in changes[:10]:  # 最多显示前5对修改
            print(change)
        if len(changes) > 10:
            print(f"  ... 还有 {len(changes) - 10} 行")
        total_changes += count

    print("\n" + "="*80)
    print("\n📊 统计：")
    print(f"  - 需要更新的文件: {len(files_to_update)}")
    print(f"  - 总修改次数: {total_changes}")

    # 询问是否继续
    print("\n" + "="*80)
    response = input("\n是否执行更新？(yes/no): ").strip().lower()

    if response != "yes":
        print("❌ 已取消更新")
        return

    # 第二遍：实际更新
    print("\n✨ 开始更新文件...\n")

    success_count = 0
    for file_path, _, _ in files_to_update:
        count, _ = update_imports_in_file(file_path, dry_run=False)
        if count > 0:
            rel_path = file_path.relative_to(root_dir)
            print(f"✅ {rel_path} ({count} 处修改)")
            success_count += 1

    print("\n" + "="*80)
    print(f"\n🎉 完成！成功更新 {success_count} 个文件")


if __name__ == "__main__":
    main()
