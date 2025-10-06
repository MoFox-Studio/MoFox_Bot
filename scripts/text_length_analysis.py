import os
import re
import sys
import time
from datetime import datetime
from typing import Optional, Tuple, List, Dict

# Add project root to Python path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)
from src.common.database.database_model import Messages, ChatStreams  # noqa

# 常量定义
TEXT_LENGTH_RANGES = {
    "0": (0, 0),      # 空文本
    "1-5": (1, 5),    # 极短文本
    "6-10": (6, 10),  # 很短文本
    "11-20": (11, 20), # 短文本
    "21-30": (21, 30), # 较短文本
    "31-50": (31, 50), # 中短文本
    "51-70": (51, 70), # 中等文本
    "71-100": (71, 100), # 较长文本
    "101-150": (101, 150), # 长文本
    "151-200": (151, 200), # 很长文本
    "201-300": (201, 300), # 超长文本
    "301-500": (301, 500), # 极长文本
    "501-1000": (501, 1000), # 巨长文本
    "1000+": (1001, float('inf')), # 超巨长文本
}

# 预编译
EMOJI_PATTERN = re.compile(r"\[表情包[^\]]*\]")
IMAGE_PATTERN = re.compile(r"\[图片[^\]]*\]")
REPLY_PATTERN = re.compile(r"\[回复[^\]]*\]")


class TextLengthAnalyzer:
    """文本长度分析器"""
    
    def __init__(self):
        self.cache = {}  # 聊天名称缓存
        
    def contains_emoji_or_image_tags(self, text: str) -> bool:
        """检查文本是否包含表情包或图片标签"""
        if not text:
            return False
        return bool(EMOJI_PATTERN.search(text) or IMAGE_PATTERN.search(text))
    
    def clean_reply_text(self, text: str) -> str:
        """清理回复引用文本"""
        if not text:
            return text
        return REPLY_PATTERN.sub("", text).strip()
    
    def get_chat_name(self, chat_id: str) -> str:
        """获取聊天名称（带缓存）"""
        if chat_id in self.cache:
            return self.cache[chat_id]
            
        try:
            chat_stream = ChatStreams.get_or_none(ChatStreams.stream_id == chat_id)
            if chat_stream is None:
                name = f"未知聊天 ({chat_id})"
            elif chat_stream.group_name:
                name = f"{chat_stream.group_name} ({chat_id})"
            elif chat_stream.user_nickname:
                name = f"{chat_stream.user_nickname}的私聊 ({chat_id})"
            else:
                name = f"未知聊天 ({chat_id})"
                
            self.cache[chat_id] = name
            return name
        except Exception:
            return f"查询失败 ({chat_id})"
    
    def format_timestamp(self, timestamp: float) -> str:
        """格式化时间戳"""
        try:
            return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")
        except (ValueError, OSError):
            return "未知时间"
    
    def _get_length_range(self, length: int) -> str:
        """根据长度获取对应的范围标签"""
        for range_name, (min_len, max_len) in TEXT_LENGTH_RANGES.items():
            if min_len <= length <= max_len:
                return range_name
        return "1000+"  # 兜底
    
    def calculate_text_length_distribution(self, messages) -> Dict[str, int]:
        """计算文本长度分布"""
        distribution = {range_name: 0 for range_name in TEXT_LENGTH_RANGES}
        
        for msg in messages:
            if msg.processed_plain_text is None:
                continue
                
            if self.contains_emoji_or_image_tags(msg.processed_plain_text):
                continue
                
            cleaned_text = self.clean_reply_text(msg.processed_plain_text)
            length = len(cleaned_text)
            range_name = self._get_length_range(length)
            distribution[range_name] += 1
            
        return distribution
    
    def get_text_length_stats(self, messages) -> Dict[str, float]:
        """计算文本长度统计信息"""
        lengths = []
        null_count = 0
        excluded_count = 0
        
        for msg in messages:
            if msg.processed_plain_text is None:
                null_count += 1
            elif self.contains_emoji_or_image_tags(msg.processed_plain_text):
                excluded_count += 1
            else:
                cleaned_text = self.clean_reply_text(msg.processed_plain_text)
                lengths.append(len(cleaned_text))
        
        if not lengths:
            return {
                "count": 0,
                "null_count": null_count,
                "excluded_count": excluded_count,
                "min": 0,
                "max": 0,
                "avg": 0,
                "median": 0,
            }
        
        lengths.sort()
        count = len(lengths)
        
        return {
            "count": count,
            "null_count": null_count,
            "excluded_count": excluded_count,
            "min": min(lengths),
            "max": max(lengths),
            "avg": sum(lengths) / count,
            "median": lengths[count // 2] if count % 2 == 1 else 
                     (lengths[count // 2 - 1] + lengths[count // 2]) / 2,
        }
    
    def get_available_chats(self) -> List[Tuple[str, str, int]]:
        """获取可用的聊天列表"""
        try:
            # 使用更高效的查询方式
            chat_ids = (Messages
                       .select(Messages.chat_id)
                       .where((Messages.is_emoji != 1) & 
                             (Messages.is_picid != 1) & 
                             (Messages.is_command != 1))
                       .distinct())
            
            result = []
            for chat in chat_ids:
                count = (Messages
                        .select()
                        .where((Messages.chat_id == chat.chat_id) &
                              (Messages.is_emoji != 1) &
                              (Messages.is_picid != 1) &
                              (Messages.is_command != 1))
                        .count())
                if count > 0:
                    chat_name = self.get_chat_name(chat.chat_id)
                    result.append((chat.chat_id, chat_name, count))
            
            result.sort(key=lambda x: x[2], reverse=True)
            return result
        except Exception as e:
            print(f"获取聊天列表失败: {e}")
            return []
    
    def get_time_range_input(self) -> Tuple[Optional[float], Optional[float]]:
        """获取时间范围输入"""
        print("\n时间范围选择:")
        options = {
            "1": ("最近1天", 1),
            "2": ("最近3天", 3),
            "3": ("最近7天", 7),
            "4": ("最近30天", 30),
            "5": ("自定义时间范围", None),
            "6": ("不限制时间", None),
        }
        
        for key, (desc, _) in options.items():
            print(f"{key}. {desc}")
        
        choice = input("请选择时间范围 (1-6): ").strip()
        
        if choice in ("1", "2", "3", "4"):
            days = options[choice][1]
            return time.time() - days * 24 * 3600, time.time()
        elif choice == "5":
            return self._get_custom_time_range()
        else:
            return None, None
    
    def _get_custom_time_range(self) -> Tuple[Optional[float], Optional[float]]:
        """获取自定义时间范围"""
        try:
            start_str = input("请输入开始时间 (格式: YYYY-MM-DD HH:MM:SS): ").strip()
            end_str = input("请输入结束时间 (格式: YYYY-MM-DD HH:MM:SS): ").strip()
            
            start_time = datetime.strptime(start_str, "%Y-%m-%d %H:%M:%S").timestamp()
            end_time = datetime.strptime(end_str, "%Y-%m-%d %H:%M:%S").timestamp()
            return start_time, end_time
        except ValueError:
            print("时间格式错误，将不限制时间范围")
            return None, None
    
    def get_top_longest_messages(self, messages, top_n: int = 10) -> List[Tuple[str, int, str, str]]:
        """获取最长的消息"""
        message_data = []
        
        for msg in messages:
            if (msg.processed_plain_text is not None and 
                not self.contains_emoji_or_image_tags(msg.processed_plain_text)):
                
                cleaned_text = self.clean_reply_text(msg.processed_plain_text)
                length = len(cleaned_text)
                chat_name = self.get_chat_name(msg.chat_id)
                time_str = self.format_timestamp(msg.time)
                preview = cleaned_text[:100] + "..." if len(cleaned_text) > 100 else cleaned_text
                message_data.append((chat_name, length, time_str, preview))
        
        message_data.sort(key=lambda x: x[1], reverse=True)
        return message_data[:top_n]
    
    def build_query(self, chat_id: Optional[str] = None, 
                   start_time: Optional[float] = None, 
                   end_time: Optional[float] = None):
        """构建查询条件"""
        query = Messages.select().where(
            (Messages.is_emoji != 1) & 
            (Messages.is_picid != 1) & 
            (Messages.is_command != 1)
        )
        
        if chat_id:
            query = query.where(Messages.chat_id == chat_id)
        if start_time:
            query = query.where(Messages.time >= start_time)
        if end_time:
            query = query.where(Messages.time <= end_time)
            
        return query
    
    def analyze_text_lengths(self, chat_id: Optional[str] = None, 
                            start_time: Optional[float] = None, 
                            end_time: Optional[float] = None) -> None:
        """分析文本长度"""
        query = self.build_query(chat_id, start_time, end_time)
        messages = list(query)
        
        if not messages:
            print("没有找到符合条件的消息")
            return
        
        distribution = self.calculate_text_length_distribution(messages)
        stats = self.get_text_length_stats(messages)
        top_longest = self.get_top_longest_messages(messages, 10)
        
        self._display_results(chat_id, start_time, end_time, messages, 
                             distribution, stats, top_longest)
    
    def _display_results(self, chat_id: Optional[str], start_time: Optional[float],
                        end_time: Optional[float], messages: List, 
                        distribution: Dict[str, int], stats: Dict[str, float],
                        top_longest: List[Tuple]) -> None:
        """显示分析结果"""
        print("\n=== Processed Plain Text 长度分析结果 ===")
        print("(已排除表情、图片ID、命令类型消息，已排除[表情包]和[图片]标记消息，已清理回复引用)")
        
        # 显示筛选条件
        print(f"聊天: {self.get_chat_name(chat_id) if chat_id else '全部聊天'}")
        self._display_time_range(start_time, end_time)
        
        # 显示统计信息
        self._display_stats(len(messages), stats)
        
        # 显示分布情况
        self._display_distribution(distribution, stats["count"])
        
        # 显示最长消息
        self._display_top_messages(top_longest)
    
    def _display_time_range(self, start_time: Optional[float], end_time: Optional[float]) -> None:
        """显示时间范围"""
        if start_time and end_time:
            print(f"时间范围: {self.format_timestamp(start_time)} 到 {self.format_timestamp(end_time)}")
        elif start_time:
            print(f"时间范围: {self.format_timestamp(start_time)} 之后")
        elif end_time:
            print(f"时间范围: {self.format_timestamp(end_time)} 之前")
        else:
            print("时间范围: 不限制")
    
    def _display_stats(self, total_messages: int, stats: Dict[str, float]) -> None:
        """显示统计信息"""
        print("\n基本统计:")
        print(f"总消息数量: {total_messages}")
        print(f"有文本消息数量: {stats['count']}")
        print(f"空文本消息数量: {stats['null_count']}")
        print(f"被排除的消息数量: {stats['excluded_count']}")
        
        if stats["count"] > 0:
            print(f"最短长度: {stats['min']} 字符")
            print(f"最长长度: {stats['max']} 字符")
            print(f"平均长度: {stats['avg']:.2f} 字符")
            print(f"中位数长度: {stats['median']:.2f} 字符")
    
    def _display_distribution(self, distribution: Dict[str, int], total: int) -> None:
        """显示分布情况"""
        print("\n文本长度分布:")
        if total > 0:
            for range_name, count in distribution.items():
                if count > 0:
                    percentage = count / total * 100
                    print(f"{range_name} 字符: {count} ({percentage:.2f}%)")
    
    def _display_top_messages(self, top_longest: List[Tuple]) -> None:
        """显示最长消息"""
        if top_longest:
            print(f"\n最长的 {len(top_longest)} 条消息:")
            for i, (chat_name, length, time_str, preview) in enumerate(top_longest, 1):
                print(f"{i}. [{chat_name}] {time_str}")
                print(f"   长度: {length} 字符")
                print(f"   预览: {preview}")
                print()
    
    def interactive_menu(self) -> None:
        """交互式菜单"""
        while True:
            print("\n" + "=" * 50)
            print("Processed Plain Text 长度分析工具")
            print("=" * 50)
            print("1. 分析全部聊天")
            print("2. 选择特定聊天分析")
            print("q. 退出")
            
            choice = input("\n请选择分析模式 (1-2, q): ").strip().lower()
            
            if choice == "q":
                print("再见！")
                break
            elif choice in ("1", "2"):
                self._handle_choice(choice)
            else:
                print("无效选择")
    
    def _handle_choice(self, choice: str) -> None:
        """处理菜单选择"""
        chat_id = None
        
        if choice == "2":
            chat_id = self._select_chat()
            if chat_id is None:
                return
        
        start_time, end_time = self.get_time_range_input()
        self.analyze_text_lengths(chat_id, start_time, end_time)
        input("\n按回车键继续...")
    
    def _select_chat(self) -> Optional[str]:
        """选择聊天"""
        chats = self.get_available_chats()
        if not chats:
            print("没有找到聊天数据")
            return None
        
        print(f"\n可用的聊天 (共{len(chats)}个):")
        for i, (_cid, name, count) in enumerate(chats, 1):
            print(f"{i}. {name} ({count}条消息)")
        
        try:
            chat_choice = int(input(f"\n请选择聊天 (1-{len(chats)}): ").strip())
            if 1 <= chat_choice <= len(chats):
                return chats[chat_choice - 1][0]
            else:
                print("无效选择")
        except ValueError:
            print("请输入有效数字")
        
        return None


def main():
    """主函数"""
    analyzer = TextLengthAnalyzer()
    analyzer.interactive_menu()


if __name__ == "__main__":
    main()
