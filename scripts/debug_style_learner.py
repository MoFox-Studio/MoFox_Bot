"""
检查 StyleLearner 模型状态的诊断脚本
"""
import sys
from pathlib import Path

# 添加项目根目录到路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.chat.express.style_learner import style_learner_manager
from src.common.logger import get_logger

logger = get_logger("debug_style_learner")


def check_style_learner_status(chat_id: str):
    """检查指定 chat_id 的 StyleLearner 状态"""

    print("=" * 60)
    print(f"StyleLearner 状态诊断 - Chat ID: {chat_id}")
    print("=" * 60)

    # 获取 learner
    learner = style_learner_manager.get_learner(chat_id)

    # 1. 基本信息
    print("\n📊 基本信息:")
    print(f"  Chat ID: {learner.chat_id}")
    print(f"  风格数量: {len(learner.style_to_id)}")
    print(f"  下一个ID: {learner.next_style_id}")
    print(f"  最大风格数: {learner.max_styles}")

    # 2. 学习统计
    print("\n📈 学习统计:")
    print(f"  总样本数: {learner.learning_stats['total_samples']}")
    print(f"  最后更新: {learner.learning_stats.get('last_update', 'N/A')}")

    # 3. 风格列表（前20个）
    print("\n📋 已学习的风格 (前20个):")
    all_styles = learner.get_all_styles()
    if not all_styles:
        print("  ⚠️  没有任何风格！模型尚未训练")
    else:
        for i, style in enumerate(all_styles[:20], 1):
            style_id = learner.style_to_id.get(style)
            situation = learner.id_to_situation.get(style_id, "N/A")
            print(f"  [{i}] {style}")
            print(f"      (ID: {style_id}, Situation: {situation})")

    # 4. 测试预测
    print("\n🔮 测试预测功能:")
    if not all_styles:
        print("  ⚠️  无法测试，模型没有训练数据")
    else:
        test_situations = [
            "表示惊讶",
            "讨论游戏",
            "表达赞同"
        ]

        for test_sit in test_situations:
            print(f"\n  测试输入: '{test_sit}'")
            best_style, scores = learner.predict_style(test_sit, top_k=3)

            if best_style:
                print(f"  ✓ 最佳匹配: {best_style}")
                print("  Top 3:")
                for style, score in list(scores.items())[:3]:
                    print(f"    - {style}: {score:.4f}")
            else:
                print("  ✗ 预测失败")

    print("\n" + "=" * 60)
    print("诊断完成")
    print("=" * 60)


if __name__ == "__main__":
    # 从诊断报告中看到的 chat_id
    test_chat_ids = [
        "52fb94af9f500a01e023ea780e43606e",  # 有78个表达方式
        "46c8714c8a9b7ee169941fe99fcde07d",  # 有22个表达方式
    ]

    for chat_id in test_chat_ids:
        check_style_learner_status(chat_id)
        print("\n")
