"""
测试增强版时间解析器

验证各种时间表达式的解析能力
"""

from datetime import datetime, timedelta

from src.memory_graph.utils.time_parser import TimeParser


def test_time_parser():
    """测试时间解析器的各种情况"""
    
    # 使用固定的参考时间进行测试
    reference_time = datetime(2025, 11, 5, 15, 30, 0)  # 2025年11月5日 15:30
    parser = TimeParser(reference_time=reference_time)
    
    print("=" * 60)
    print("时间解析器增强测试")
    print("=" * 60)
    print(f"参考时间: {reference_time.strftime('%Y-%m-%d %H:%M:%S')}")
    print()
    
    test_cases = [
        # 相对日期
        ("今天", "应该是今天0点"),
        ("明天", "应该是明天0点"),
        ("昨天", "应该是昨天0点"),
        ("前天", "应该是前天0点"),
        ("后天", "应该是后天0点"),
        
        # X天前/后
        ("1天前", "应该是昨天0点"),
        ("2天前", "应该是前天0点"),
        ("5天前", "应该是5天前0点"),
        ("3天后", "应该是3天后0点"),
        
        # X周前/后（新增）
        ("1周前", "应该是1周前0点"),
        ("2周前", "应该是2周前0点"),
        ("3周后", "应该是3周后0点"),
        
        # X个月前/后（新增）
        ("1个月前", "应该是约30天前"),
        ("2月前", "应该是约60天前"),
        ("3个月后", "应该是约90天后"),
        
        # X年前/后（新增）
        ("1年前", "应该是约365天前"),
        ("2年后", "应该是约730天后"),
        
        # X小时前/后
        ("1小时前", "应该是1小时前"),
        ("3小时前", "应该是3小时前"),
        ("2小时后", "应该是2小时后"),
        
        # X分钟前/后
        ("30分钟前", "应该是30分钟前"),
        ("15分钟后", "应该是15分钟后"),
        
        # 时间段
        ("早上", "应该是今天早上8点"),
        ("上午", "应该是今天上午10点"),
        ("中午", "应该是今天中午12点"),
        ("下午", "应该是今天下午15点"),
        ("晚上", "应该是今天晚上20点"),
        
        # 组合表达（新增）
        ("今天下午", "应该是今天下午15点"),
        ("昨天晚上", "应该是昨天晚上20点"),
        ("明天早上", "应该是明天早上8点"),
        ("前天中午", "应该是前天中午12点"),
        
        # 具体时间点
        ("早上8点", "应该是今天早上8点"),
        ("下午3点", "应该是今天下午15点"),
        ("晚上9点", "应该是今天晚上21点"),
        
        # 具体日期
        ("2025-11-05", "应该是2025年11月5日"),
        ("11月5日", "应该是今年11月5日"),
        ("11-05", "应该是今年11月5日"),
        
        # 周/月/年
        ("上周", "应该是上周"),
        ("上个月", "应该是上个月"),
        ("去年", "应该是去年"),
        
        # 中文数字
        ("一天前", "应该是昨天"),
        ("三天前", "应该是3天前"),
        ("五天后", "应该是5天后"),
        ("十天前", "应该是10天前"),
    ]
    
    success_count = 0
    fail_count = 0
    
    for time_str, expected_desc in test_cases:
        result = parser.parse(time_str)
        
        # 计算与参考时间的差异
        if result:
            diff = result - reference_time
            
            # 格式化输出
            if diff.total_seconds() == 0:
                diff_str = "当前时间"
            elif abs(diff.days) > 0:
                if diff.days > 0:
                    diff_str = f"+{diff.days}天"
                else:
                    diff_str = f"{diff.days}天"
            else:
                hours = diff.seconds // 3600
                minutes = (diff.seconds % 3600) // 60
                if hours > 0:
                    diff_str = f"{hours}小时"
                else:
                    diff_str = f"{minutes}分钟"
            
            result_str = result.strftime("%Y-%m-%d %H:%M")
            status = "[OK]"
            success_count += 1
        else:
            result_str = "解析失败"
            diff_str = "N/A"
            status = "[FAILED]"
            fail_count += 1
        
        print(f"{status} '{time_str:15s}' -> {result_str:20s} ({diff_str:10s}) | {expected_desc}")
    
    print()
    print("=" * 60)
    print(f"测试结果: 成功 {success_count}/{len(test_cases)}, 失败 {fail_count}/{len(test_cases)}")
    
    if fail_count == 0:
        print("[SUCCESS] 所有测试通过！")
    else:
        print(f"[WARNING] 有 {fail_count} 个测试失败")
    
    print("=" * 60)


if __name__ == "__main__":
    test_time_parser()
