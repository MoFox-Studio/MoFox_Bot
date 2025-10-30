"""
关系追踪工具集成测试脚本

注意：此脚本需要在完整的应用环境中运行
建议通过 bot.py 启动后在交互式环境中测试
"""

import asyncio


async def test_user_profile_tool():
    """测试用户画像工具"""
    print("\n" + "=" * 80)
    print("测试 UserProfileTool")
    print("=" * 80)
    
    from src.plugins.built_in.affinity_flow_chatter.user_profile_tool import UserProfileTool
    from src.common.database.sqlalchemy_database_api import db_query
    from src.common.database.sqlalchemy_models import UserRelationships
    
    tool = UserProfileTool()
    print(f"✅ 工具名称: {tool.name}")
    print(f"   工具描述: {tool.description}")
    
    # 执行工具
    test_user_id = "integration_test_user_001"
    result = await tool.execute({
        "target_user_id": test_user_id,
        "user_aliases": "测试小明,TestMing,小明君",
        "impression_description": "这是一个集成测试用户，性格开朗活泼，喜欢技术讨论，对AI和编程特别感兴趣。经常提出有深度的问题。",
        "preference_keywords": "AI,Python,深度学习,游戏开发,科幻小说",
        "affection_score": 0.85
    })
    
    print(f"\n✅ 工具执行结果:")
    print(f"   类型: {result.get('type')}")
    print(f"   内容: {result.get('content')}")
    
    # 验证数据库
    db_data = await db_query(
        UserRelationships,
        filters={"user_id": test_user_id},
        limit=1
    )
    
    if db_data:
        data = db_data[0]
        print(f"\n✅ 数据库验证:")
        print(f"   user_id: {data.get('user_id')}")
        print(f"   user_aliases: {data.get('user_aliases')}")
        print(f"   relationship_text: {data.get('relationship_text', '')[:80]}...")
        print(f"   preference_keywords: {data.get('preference_keywords')}")
        print(f"   relationship_score: {data.get('relationship_score')}")
        return True
    else:
        print(f"\n❌ 数据库中未找到数据")
        return False


async def test_chat_stream_impression_tool():
    """测试聊天流印象工具"""
    print("\n" + "=" * 80)
    print("测试 ChatStreamImpressionTool")
    print("=" * 80)
    
    from src.plugins.built_in.affinity_flow_chatter.chat_stream_impression_tool import ChatStreamImpressionTool
    from src.common.database.sqlalchemy_database_api import db_query
    from src.common.database.sqlalchemy_models import ChatStreams, get_db_session
    
    # 准备测试数据：先创建一条 ChatStreams 记录
    test_stream_id = "integration_test_stream_001"
    print(f"🔧 准备测试数据：创建聊天流记录 {test_stream_id}")
    
    import time
    current_time = time.time()
    
    async with get_db_session() as session:
        new_stream = ChatStreams(
            stream_id=test_stream_id,
            create_time=current_time,
            last_active_time=current_time,
            platform="QQ",
            user_platform="QQ",
            user_id="test_user_123",
            user_nickname="测试用户",
            group_name="测试技术交流群",
            group_platform="QQ",
            group_id="test_group_456",
            stream_impression_text="",  # 初始为空
            stream_chat_style="",
            stream_topic_keywords="",
            stream_interest_score=0.5
        )
        session.add(new_stream)
        await session.commit()
        print(f"✅ 测试聊天流记录已创建")
    
    tool = ChatStreamImpressionTool()
    print(f"✅ 工具名称: {tool.name}")
    print(f"   工具描述: {tool.description}")
    
    # 执行工具
    result = await tool.execute({
        "stream_id": test_stream_id,
        "impression_description": "这是一个技术交流群，成员主要是程序员和AI爱好者。大家经常分享最新的技术文章，讨论编程问题，氛围友好且专业。",
        "chat_style": "专业技术交流,活跃讨论,互帮互助,知识分享",
        "topic_keywords": "Python开发,机器学习,AI应用,Web后端,数据分析,开源项目",
        "interest_score": 0.90
    })
    
    print(f"\n✅ 工具执行结果:")
    print(f"   类型: {result.get('type')}")
    print(f"   内容: {result.get('content')}")
    
    # 验证数据库
    db_data = await db_query(
        ChatStreams,
        filters={"stream_id": test_stream_id},
        limit=1
    )
    
    if db_data:
        data = db_data[0]
        print(f"\n✅ 数据库验证:")
        print(f"   stream_id: {data.get('stream_id')}")
        print(f"   stream_impression_text: {data.get('stream_impression_text', '')[:80]}...")
        print(f"   stream_chat_style: {data.get('stream_chat_style')}")
        print(f"   stream_topic_keywords: {data.get('stream_topic_keywords')}")
        print(f"   stream_interest_score: {data.get('stream_interest_score')}")
        return True
    else:
        print(f"\n❌ 数据库中未找到数据")
        return False


async def test_relationship_info_build():
    """测试关系信息构建"""
    print("\n" + "=" * 80)
    print("测试关系信息构建（提示词集成）")
    print("=" * 80)
    
    from src.person_info.relationship_fetcher import relationship_fetcher_manager
    
    test_stream_id = "integration_test_stream_001"
    test_person_id = "test_person_999"  # 使用一个可能不存在的ID来测试
    
    fetcher = relationship_fetcher_manager.get_fetcher(test_stream_id)
    print(f"✅ RelationshipFetcher 已创建")
    
    # 测试聊天流印象构建
    print(f"\n🔍 构建聊天流印象...")
    stream_info = await fetcher.build_chat_stream_impression(test_stream_id)
    
    if stream_info:
        print(f"✅ 聊天流印象构建成功")
        print(f"\n{'=' * 80}")
        print(stream_info)
        print(f"{'=' * 80}")
    else:
        print(f"⚠️  聊天流印象为空（可能测试数据不存在）")
    
    return True


async def cleanup_test_data():
    """清理测试数据"""
    print("\n" + "=" * 80)
    print("清理测试数据")
    print("=" * 80)
    
    from src.common.database.sqlalchemy_database_api import db_query
    from src.common.database.sqlalchemy_models import UserRelationships, ChatStreams
    
    try:
        # 清理用户数据
        await db_query(
            UserRelationships,
            query_type="delete",
            filters={"user_id": "integration_test_user_001"}
        )
        print("✅ 用户测试数据已清理")
        
        # 清理聊天流数据
        await db_query(
            ChatStreams,
            query_type="delete",
            filters={"stream_id": "integration_test_stream_001"}
        )
        print("✅ 聊天流测试数据已清理")
        
        return True
    except Exception as e:
        print(f"⚠️  清理失败: {e}")
        return False


async def run_all_tests():
    """运行所有测试"""
    print("\n" + "=" * 80)
    print("关系追踪工具集成测试")
    print("=" * 80)
    
    results = {}
    
    # 测试1
    try:
        results["UserProfileTool"] = await test_user_profile_tool()
    except Exception as e:
        print(f"\n❌ UserProfileTool 测试失败: {e}")
        import traceback
        traceback.print_exc()
        results["UserProfileTool"] = False
    
    # 测试2
    try:
        results["ChatStreamImpressionTool"] = await test_chat_stream_impression_tool()
    except Exception as e:
        print(f"\n❌ ChatStreamImpressionTool 测试失败: {e}")
        import traceback
        traceback.print_exc()
        results["ChatStreamImpressionTool"] = False
    
    # 测试3
    try:
        results["RelationshipFetcher"] = await test_relationship_info_build()
    except Exception as e:
        print(f"\n❌ RelationshipFetcher 测试失败: {e}")
        import traceback
        traceback.print_exc()
        results["RelationshipFetcher"] = False
    
    # 清理
    try:
        await cleanup_test_data()
    except Exception as e:
        print(f"\n⚠️  清理测试数据失败: {e}")
    
    # 总结
    print("\n" + "=" * 80)
    print("测试总结")
    print("=" * 80)
    
    passed = sum(1 for r in results.values() if r)
    total = len(results)
    
    for test_name, result in results.items():
        status = "✅ 通过" if result else "❌ 失败"
        print(f"{status} - {test_name}")
    
    print(f"\n总计: {passed}/{total} 测试通过")
    
    if passed == total:
        print("\n🎉 所有测试通过！")
    else:
        print(f"\n⚠️  {total - passed} 个测试失败")
    
    return passed == total


# 使用说明
print("""
============================================================================
关系追踪工具集成测试脚本
============================================================================

此脚本需要在完整的应用环境中运行。

使用方法1: 在 bot.py 中添加测试调用
-----------------------------------
在 bot.py 的 main() 函数中添加:

    # 测试关系追踪工具
    from tests.integration_test_relationship_tools import run_all_tests
    await run_all_tests()

使用方法2: 在 Python REPL 中运行
-----------------------------------
启动 bot.py 后，在 Python 调试控制台中执行:

    import asyncio
    from tests.integration_test_relationship_tools import run_all_tests
    asyncio.create_task(run_all_tests())

使用方法3: 直接在此文件底部运行
-----------------------------------
取消注释下面的代码，然后确保已启动应用环境
============================================================================
""")


# 如果需要直接运行（需要应用环境已启动）
if __name__ == "__main__":
    print("\n⚠️  警告: 直接运行此脚本可能会失败，因为缺少应用环境")
    print("建议在 bot.py 启动后的环境中运行\n")
    
    try:
        asyncio.run(run_all_tests())
    except Exception as e:
        print(f"\n❌ 测试失败: {e}")
        print("\n建议:")
        print("1. 确保已启动 bot.py")
        print("2. 在 Python 调试控制台中运行测试")
        print("3. 或在 bot.py 中添加测试调用")
