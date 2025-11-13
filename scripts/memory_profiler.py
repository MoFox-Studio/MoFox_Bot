#!/usr/bin/env python3
"""
统一内存分析工具 - Bot 内存诊断完整解决方案

支持三种模式:
  1. 进程监控模式 (--monitor): 从外部监控 bot 进程内存、子进程
  2. 对象分析模式 (--objects): 在 bot 内部统计所有对象（包括所有线程）
  3. 可视化模式 (--visualize): 将 JSONL 数据绘制成图表

示例:
  # 进程监控（启动 bot 并监控）
  python scripts/memory_profiler.py --monitor --interval 10

  # 对象分析（深度对象统计）
  python scripts/memory_profiler.py --objects --interval 10 --output memory_data.txt

  # 生成可视化图表
  python scripts/memory_profiler.py --visualize --input memory_data.txt.jsonl --top 15
"""

import argparse
import asyncio
import gc
import json
import subprocess
import sys
import threading
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import psutil

try:
    from pympler import muppy, summary, tracker
    PYMPLER_AVAILABLE = True
except ImportError:
    PYMPLER_AVAILABLE = False

try:
    import matplotlib.pyplot as plt
    MATPLOTLIB_AVAILABLE = True
except ImportError:
    MATPLOTLIB_AVAILABLE = False


# ============================================================================
# 进程监控模式
# ============================================================================

async def monitor_bot_process(bot_process: subprocess.Popen, interval: int = 5):
    """从外部监控 bot 进程的内存使用（进程级）"""
    if bot_process.pid is None:
        print("❌ Bot 进程 PID 为空")
        return

    print(f"🔍 开始监控 Bot 内存（PID: {bot_process.pid}）")
    print(f"监控间隔: {interval} 秒")
    print("按 Ctrl+C 停止监控和 Bot\n")

    try:
        process = psutil.Process(bot_process.pid)
    except psutil.NoSuchProcess:
        print("❌ 无法找到 Bot 进程")
        return

    history = []
    iteration = 0

    try:
        while bot_process.poll() is None:
            try:
                mem_info = process.memory_info()
                mem_percent = process.memory_percent()

                children = process.children(recursive=True)
                children_mem = sum(child.memory_info().rss for child in children)

                info = {
                    "timestamp": time.strftime("%H:%M:%S"),
                    "rss_mb": mem_info.rss / 1024 / 1024,
                    "vms_mb": mem_info.vms / 1024 / 1024,
                    "percent": mem_percent,
                    "children_count": len(children),
                    "children_mem_mb": children_mem / 1024 / 1024,
                }

                history.append(info)
                iteration += 1

                print(f"{'=' * 80}")
                print(f"检查点 #{iteration} - {info['timestamp']}")
                print(f"Bot 进程 (PID: {bot_process.pid})")
                print(f"  RSS: {info['rss_mb']:.2f} MB")
                print(f"  VMS: {info['vms_mb']:.2f} MB")
                print(f"  占比: {info['percent']:.2f}%")

                if children:
                    print(f"  子进程: {info['children_count']} 个")
                    print(f"  子进程内存: {info['children_mem_mb']:.2f} MB")
                    total_mem = info["rss_mb"] + info["children_mem_mb"]
                    print(f"  总内存: {total_mem:.2f} MB")

                    print("\n  📋 子进程详情:")
                    for idx, child in enumerate(children, 1):
                        try:
                            child_mem = child.memory_info().rss / 1024 / 1024
                            child_name = child.name()
                            child_cmdline = " ".join(child.cmdline()[:3])
                            if len(child_cmdline) > 80:
                                child_cmdline = child_cmdline[:77] + "..."
                            print(f"    [{idx}] PID {child.pid}: {child_name} - {child_mem:.2f} MB")
                            print(f"        命令: {child_cmdline}")
                        except (psutil.NoSuchProcess, psutil.AccessDenied):
                            print(f"    [{idx}] 无法访问进程信息")

                if len(history) > 1:
                    prev = history[-2]
                    rss_diff = info["rss_mb"] - prev["rss_mb"]
                    print("\n变化:")
                    print(f"  RSS: {rss_diff:+.2f} MB")
                    if rss_diff > 10:
                        print("  ⚠️  内存增长较快！")
                    if info["rss_mb"] > 1000:
                        print("  ⚠️  内存使用超过 1GB！")

                print(f"{'=' * 80}\n")
                await asyncio.sleep(interval)

            except psutil.NoSuchProcess:
                print("\n❌ Bot 进程已结束")
                break
            except Exception as e:
                print(f"\n❌ 监控出错: {e}")
                break

    except KeyboardInterrupt:
        print("\n\n⚠️  用户中断监控")

    finally:
        if history and bot_process.pid:
            save_process_history(history, bot_process.pid)


def save_process_history(history: list, pid: int):
    """保存进程监控历史"""
    output_dir = Path("data/memory_diagnostics")
    output_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_file = output_dir / f"process_monitor_{timestamp}_pid{pid}.txt"

    with open(output_file, "w", encoding="utf-8") as f:
        f.write("Bot 进程内存监控历史记录\n")
        f.write("=" * 80 + "\n\n")
        f.write(f"Bot PID: {pid}\n\n")

        for info in history:
            f.write(f"时间: {info['timestamp']}\n")
            f.write(f"RSS: {info['rss_mb']:.2f} MB\n")
            f.write(f"VMS: {info['vms_mb']:.2f} MB\n")
            f.write(f"占比: {info['percent']:.2f}%\n")
            if info["children_count"] > 0:
                f.write(f"子进程: {info['children_count']} 个\n")
                f.write(f"子进程内存: {info['children_mem_mb']:.2f} MB\n")
            f.write("\n")

    print(f"\n✅ 监控历史已保存到: {output_file}")


async def run_monitor_mode(interval: int):
    """进程监控模式主函数"""
    print("=" * 80)
    print("🚀 进程监控模式")
    print("=" * 80)
    print("此模式将:")
    print("  1. 使用虚拟环境启动 bot.py")
    print("  2. 实时监控进程内存（RSS、VMS）")
    print("  3. 显示子进程详细信息")
    print("  4. 自动保存监控历史")
    print("=" * 80 + "\n")

    project_root = Path(__file__).parent.parent
    bot_file = project_root / "bot.py"

    if not bot_file.exists():
        print(f"❌ 找不到 bot.py: {bot_file}")
        return 1

    # 检测虚拟环境
    venv_python = project_root / ".venv" / "Scripts" / "python.exe"
    if not venv_python.exists():
        venv_python = project_root / ".venv" / "bin" / "python"

    if venv_python.exists():
        python_exe = str(venv_python)
        print(f"🐍 使用虚拟环境: {venv_python}")
    else:
        python_exe = sys.executable
        print(f"⚠️  未找到虚拟环境，使用当前 Python: {python_exe}")

    print(f"🤖 启动 Bot: {bot_file}")

    bot_process = subprocess.Popen(
        [python_exe, str(bot_file)],
        cwd=str(project_root),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    await asyncio.sleep(2)

    if bot_process.poll() is not None:
        print("❌ Bot 启动失败")
        if bot_process.stdout:
            output = bot_process.stdout.read()
            if output:
                print(f"\nBot 输出:\n{output}")
        return 1

    print(f"✅ Bot 已启动 (PID: {bot_process.pid})\n")

    # 启动输出读取线程
    def read_bot_output():
        if bot_process.stdout:
            try:
                for line in bot_process.stdout:
                    print(f"[Bot] {line}", end="")
            except Exception:
                pass

    output_thread = threading.Thread(target=read_bot_output, daemon=True)
    output_thread.start()

    try:
        await monitor_bot_process(bot_process, interval)
    except KeyboardInterrupt:
        print("\n\n⚠️  用户中断")

        if bot_process.poll() is None:
            print("\n正在停止 Bot...")
            bot_process.terminate()
            try:
                bot_process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                print("⚠️  强制终止 Bot...")
                bot_process.kill()
                bot_process.wait()

        print("✅ Bot 已停止")

    return 0


# ============================================================================
# 对象分析模式
# ============================================================================

class ObjectMemoryProfiler:
    """对象级内存分析器"""

    def __init__(self, interval: int = 10, output_file: str | None = None, object_limit: int = 20):
        self.interval = interval
        self.output_file = output_file
        self.object_limit = object_limit
        self.running = False
        self.tracker = None
        if PYMPLER_AVAILABLE:
            self.tracker = tracker.SummaryTracker()
        self.iteration = 0

    def get_object_stats(self) -> dict:
        """获取当前进程的对象统计（所有线程）"""
        if not PYMPLER_AVAILABLE:
            return {}

        try:
            gc.collect()
            all_objects = muppy.get_objects()
            sum_data = summary.summarize(all_objects)

            # 按总大小（第3个元素）降序排序
            sorted_sum_data = sorted(sum_data, key=lambda x: x[2], reverse=True)

            # 按模块统计内存
            module_stats = self._get_module_stats(all_objects)

            threads = threading.enumerate()
            thread_info = [
                {
                    "name": t.name,
                    "daemon": t.daemon,
                    "alive": t.is_alive(),
                }
                for t in threads
            ]

            gc_stats = {
                "collections": gc.get_count(),
                "garbage": len(gc.garbage),
                "tracked": len(gc.get_objects()),
            }

            return {
                "summary": sorted_sum_data[:self.object_limit],
                "module_stats": module_stats,
                "gc_stats": gc_stats,
                "total_objects": len(all_objects),
                "threads": thread_info,
            }
        except Exception as e:
            print(f"❌ 获取对象统计失败: {e}")
            return {}

    def _get_module_stats(self, all_objects: list) -> dict:
        """统计各模块的内存占用"""
        module_mem = defaultdict(lambda: {"count": 0, "size": 0})

        for obj in all_objects:
            try:
                # 获取对象所属模块
                obj_type = type(obj)
                module_name = obj_type.__module__

                if module_name:
                    # 获取顶级模块名（例如 src.chat.xxx -> src）
                    top_module = module_name.split(".")[0]

                    obj_size = sys.getsizeof(obj)
                    module_mem[top_module]["count"] += 1
                    module_mem[top_module]["size"] += obj_size
            except Exception:
                # 忽略无法获取大小的对象
                continue

        # 转换为列表并按大小排序
        sorted_modules = sorted(
            [(mod, stats["count"], stats["size"])
             for mod, stats in module_mem.items()],
            key=lambda x: x[2],
            reverse=True
        )

        return {
            "top_modules": sorted_modules[:20],  # 前20个模块
            "total_modules": len(module_mem)
        }

    def print_stats(self, stats: dict, iteration: int):
        """打印统计信息"""
        print("\n" + "=" * 80)
        print(f"🔍 对象级内存分析 #{iteration} - {time.strftime('%H:%M:%S')}")
        print("=" * 80)

        if "summary" in stats:
            print(f"\n📦 对象统计 (前 {self.object_limit} 个类型):\n")
            print(f"{'类型':<50} {'数量':>12} {'总大小':>15}")
            print("-" * 80)

            for obj_type, obj_count, obj_size in stats["summary"]:
                if obj_size >= 1024 * 1024 * 1024:
                    size_str = f"{obj_size / 1024 / 1024 / 1024:.2f} GB"
                elif obj_size >= 1024 * 1024:
                    size_str = f"{obj_size / 1024 / 1024:.2f} MB"
                elif obj_size >= 1024:
                    size_str = f"{obj_size / 1024:.2f} KB"
                else:
                    size_str = f"{obj_size} B"

                print(f"{obj_type:<50} {obj_count:>12,} {size_str:>15}")

        if stats.get("module_stats"):
            print("\n📚 模块内存占用 (前 20 个模块):\n")
            print(f"{'模块名':<40} {'对象数':>12} {'总内存':>15}")
            print("-" * 80)

            for module_name, obj_count, obj_size in stats["module_stats"]["top_modules"]:
                if obj_size >= 1024 * 1024 * 1024:
                    size_str = f"{obj_size / 1024 / 1024 / 1024:.2f} GB"
                elif obj_size >= 1024 * 1024:
                    size_str = f"{obj_size / 1024 / 1024:.2f} MB"
                elif obj_size >= 1024:
                    size_str = f"{obj_size / 1024:.2f} KB"
                else:
                    size_str = f"{obj_size} B"

                print(f"{module_name:<40} {obj_count:>12,} {size_str:>15}")

            print(f"\n  总模块数: {stats['module_stats']['total_modules']}")

        if "threads" in stats:
            print(f"\n🧵 线程信息 ({len(stats['threads'])} 个):")
            for idx, t in enumerate(stats["threads"], 1):
                status = "✓" if t["alive"] else "✗"
                daemon = "(守护)" if t["daemon"] else ""
                print(f"  [{idx}] {status} {t['name']} {daemon}")

        if "gc_stats" in stats:
            gc_stats = stats["gc_stats"]
            print("\n🗑️  垃圾回收:")
            print(f"  代 0: {gc_stats['collections'][0]:,} 次")
            print(f"  代 1: {gc_stats['collections'][1]:,} 次")
            print(f"  代 2: {gc_stats['collections'][2]:,} 次")
            print(f"  追踪对象: {gc_stats['tracked']:,}")

        if "total_objects" in stats:
            print(f"\n📊 总对象数: {stats['total_objects']:,}")

        print("=" * 80 + "\n")

    def print_diff(self):
        """打印对象变化"""
        if not PYMPLER_AVAILABLE or not self.tracker:
            return

        print("\n📈 对象变化分析:")
        print("-" * 80)
        self.tracker.print_diff()
        print("-" * 80)

    def save_to_file(self, stats: dict):
        """保存统计信息到文件"""
        if not self.output_file:
            return

        try:
            # 保存文本
            with open(self.output_file, "a", encoding="utf-8") as f:
                f.write(f"\n{'=' * 80}\n")
                f.write(f"时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write(f"迭代: #{self.iteration}\n")
                f.write(f"{'=' * 80}\n\n")

                if "summary" in stats:
                    f.write("对象统计:\n")
                    for obj_type, obj_count, obj_size in stats["summary"]:
                        f.write(f"  {obj_type}: {obj_count:,} 个, {obj_size:,} 字节\n")

                if stats.get("module_stats"):
                    f.write("\n模块统计 (前 20 个):\n")
                    for module_name, obj_count, obj_size in stats["module_stats"]["top_modules"]:
                        f.write(f"  {module_name}: {obj_count:,} 个对象, {obj_size:,} 字节\n")

                f.write(f"\n总对象数: {stats.get('total_objects', 0):,}\n")
                f.write(f"线程数: {len(stats.get('threads', []))}\n")

            # 保存 JSONL
            jsonl_path = str(self.output_file) + ".jsonl"
            record = {
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                "iteration": self.iteration,
                "total_objects": stats.get("total_objects", 0),
                "threads": stats.get("threads", []),
                "gc_stats": stats.get("gc_stats", {}),
                "summary": [
                    {"type": t, "count": c, "size": s}
                    for (t, c, s) in stats.get("summary", [])
                ],
                "module_stats": stats.get("module_stats", {}),
            }

            with open(jsonl_path, "a", encoding="utf-8") as jf:
                jf.write(json.dumps(record, ensure_ascii=False) + "\n")

            if self.iteration == 1:
                print(f"💾 数据保存到: {self.output_file}")
                print(f"💾 结构化数据: {jsonl_path}")

        except Exception as e:
            print(f"⚠️  保存文件失败: {e}")

    def start_monitoring(self):
        """启动监控线程"""
        self.running = True

        def monitor_loop():
            print("🚀 对象分析器已启动")
            print(f"   监控间隔: {self.interval} 秒")
            print(f"   对象类型限制: {self.object_limit}")
            print(f"   输出文件: {self.output_file or '无'}")
            print()

            while self.running:
                try:
                    self.iteration += 1
                    stats = self.get_object_stats()
                    self.print_stats(stats, self.iteration)

                    if self.iteration % 3 == 0 and self.tracker:
                        self.print_diff()

                    if self.output_file:
                        self.save_to_file(stats)

                    time.sleep(self.interval)

                except Exception as e:
                    print(f"❌ 监控出错: {e}")
                    import traceback
                    traceback.print_exc()

        monitor_thread = threading.Thread(target=monitor_loop, daemon=True)
        monitor_thread.start()
        print("✓ 监控线程已启动\n")

    def stop(self):
        """停止监控"""
        self.running = False


def run_objects_mode(interval: int, output: str | None, object_limit: int):
    """对象分析模式主函数"""
    if not PYMPLER_AVAILABLE:
        print("❌ pympler 未安装，无法使用对象分析模式")
        print("   安装: pip install pympler")
        return 1

    print("=" * 80)
    print("🔬 对象分析模式")
    print("=" * 80)
    print("此模式将:")
    print("  1. 在 bot.py 进程内部运行")
    print("  2. 统计所有对象（包括所有线程）")
    print("  3. 显示对象变化（diff）")
    print("  4. 保存 JSONL 数据用于可视化")
    print("=" * 80 + "\n")

    # 添加项目根目录到 Python 路径
    project_root = Path(__file__).parent.parent
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
        print(f"✓ 已添加项目根目录到 Python 路径: {project_root}\n")

    profiler = ObjectMemoryProfiler(
        interval=interval,
        output_file=output,
        object_limit=object_limit
    )

    profiler.start_monitoring()

    print("🤖 正在启动 Bot...\n")

    try:
        import bot

        if hasattr(bot, "main_async"):
            asyncio.run(bot.main_async())
        elif hasattr(bot, "main"):
            bot.main()
        else:
            print("⚠️  bot.py 未找到 main_async() 或 main() 函数")
            print("   Bot 模块已导入，监控线程在后台运行")
            print("   按 Ctrl+C 停止\n")

            while profiler.running:
                time.sleep(1)

    except KeyboardInterrupt:
        print("\n\n⚠️  用户中断")
    except Exception as e:
        print(f"\n❌ Bot 运行出错: {e}")
        import traceback
        traceback.print_exc()
    finally:
        profiler.stop()

    return 0


# ============================================================================
# 可视化模式
# ============================================================================

def load_jsonl(path: Path) -> list[dict]:
    """加载 JSONL 文件"""
    snapshots = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                snapshots.append(json.loads(line))
            except Exception:
                continue
    return snapshots


def aggregate_top_types(snapshots: list[dict], top_n: int = 10):
    """聚合前 N 个对象类型的时间序列"""
    type_max = defaultdict(int)
    for snap in snapshots:
        for item in snap.get("summary", []):
            t = item.get("type")
            s = int(item.get("size", 0))
            type_max[t] = max(type_max[t], s)

    top_types = sorted(type_max.items(), key=lambda kv: kv[1], reverse=True)[:top_n]
    top_names = [t for t, _ in top_types]

    times = []
    series = {t: [] for t in top_names}

    for snap in snapshots:
        ts = snap.get("timestamp")
        try:
            times.append(datetime.strptime(ts, "%Y-%m-%d %H:%M:%S"))
        except Exception:
            times.append(None)

        summary = {item.get("type"): int(item.get("size", 0))
                   for item in snap.get("summary", [])}
        for t in top_names:
            series[t].append(summary.get(t, 0) / 1024.0 / 1024.0)

    return times, series


def plot_series(times: list, series: dict, output: Path, top_n: int):
    """绘制时间序列图"""
    plt.figure(figsize=(14, 8))

    for name, values in series.items():
        if all(v == 0 for v in values):
            continue
        plt.plot(times, values, marker="o", label=name, linewidth=2)

    plt.xlabel("时间", fontsize=12)
    plt.ylabel("内存 (MB)", fontsize=12)
    plt.title(f"对象类型随时间的内存占用 (前 {top_n} 类型)", fontsize=14)
    plt.legend(loc="upper left", fontsize="small")
    plt.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig(str(output), dpi=150)
    print(f"✅ 已保存图像: {output}")


def run_visualize_mode(input_file: str, output_file: str, top: int):
    """可视化模式主函数"""
    if not MATPLOTLIB_AVAILABLE:
        print("❌ matplotlib 未安装，无法使用可视化模式")
        print("   安装: pip install matplotlib")
        return 1

    print("=" * 80)
    print("📊 可视化模式")
    print("=" * 80)

    path = Path(input_file)
    if not path.exists():
        print(f"❌ 找不到输入文件: {path}")
        return 1

    print(f"📂 读取数据: {path}")
    snaps = load_jsonl(path)

    if not snaps:
        print("❌ 未读取到任何快照数据")
        return 1

    print(f"✓ 读取 {len(snaps)} 个快照")

    times, series = aggregate_top_types(snaps, top_n=top)
    print(f"✓ 提取前 {top} 个对象类型")

    output_path = Path(output_file)
    plot_series(times, series, output_path, top)

    return 0


# ============================================================================
# 主入口
# ============================================================================

def main():
    """主函数"""
    parser = argparse.ArgumentParser(
        description="统一内存分析工具 - Bot 内存诊断完整解决方案",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
模式说明:
  --monitor    进程监控模式：从外部监控 bot 进程内存、子进程
  --objects    对象分析模式：在 bot 内部统计所有对象（包括所有线程）
  --visualize  可视化模式：将 JSONL 数据绘制成图表

使用示例:
  # 进程监控（启动 bot 并监控）
  python scripts/memory_profiler.py --monitor --interval 10

  # 对象分析（深度对象统计）
  python scripts/memory_profiler.py --objects --interval 10 --output memory_data.txt

  # 生成可视化图表
  python scripts/memory_profiler.py --visualize --input memory_data.txt.jsonl --top 15 --output plot.png

注意:
  - 对象分析模式需要: pip install pympler
  - 可视化模式需要: pip install matplotlib
        """,
    )

    # 模式选择
    mode_group = parser.add_mutually_exclusive_group(required=True)
    mode_group.add_argument("--monitor", "-m", action="store_true",
                           help="进程监控模式（外部监控 bot 进程）")
    mode_group.add_argument("--objects", "-o", action="store_true",
                           help="对象分析模式（内部统计所有对象）")
    mode_group.add_argument("--visualize", "-v", action="store_true",
                           help="可视化模式（绘制 JSONL 数据）")

    # 通用参数
    parser.add_argument("--interval", "-i", type=int, default=10,
                       help="监控间隔（秒），默认 10")

    # 对象分析参数
    parser.add_argument("--output", type=str,
                       help="输出文件路径（对象分析模式）")
    parser.add_argument("--object-limit", "-l", type=int, default=20,
                       help="对象类型显示数量，默认 20")

    # 可视化参数
    parser.add_argument("--input", type=str,
                       help="输入 JSONL 文件（可视化模式）")
    parser.add_argument("--top", "-t", type=int, default=10,
                       help="展示前 N 个类型（可视化模式），默认 10")
    parser.add_argument("--plot-output", type=str, default="memory_analysis_plot.png",
                       help="图表输出文件，默认 memory_analysis_plot.png")

    args = parser.parse_args()

    # 根据模式执行
    if args.monitor:
        return asyncio.run(run_monitor_mode(args.interval))

    elif args.objects:
        if not args.output:
            print("⚠️  建议使用 --output 指定输出文件以保存数据")
        return run_objects_mode(args.interval, args.output, args.object_limit)

    elif args.visualize:
        if not args.input:
            print("❌ 可视化模式需要 --input 参数指定 JSONL 文件")
            return 1
        return run_visualize_mode(args.input, args.plot_output, args.top)

    return 0


if __name__ == "__main__":
    sys.exit(main())
