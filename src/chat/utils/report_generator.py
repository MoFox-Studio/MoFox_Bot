"""
该模块用于生成HTML格式的统计报告。
"""
from datetime import datetime, timedelta
from typing import Any

import aiofiles

from .statistic_keys import *  # noqa: F403


def format_online_time(online_seconds: int) -> str:
    """
    格式化在线时间。

    :param online_seconds: 在线时间（秒）。
    :return: 格式化后的在线时间字符串。
    """
    total_online_time = timedelta(seconds=online_seconds)
    days = total_online_time.days
    hours = total_online_time.seconds // 3600
    minutes = (total_online_time.seconds // 60) % 60
    seconds = total_online_time.seconds % 60
    if days > 0:
        return f"{days}天{hours}小时{minutes}分钟{seconds}秒"
    elif hours > 0:
        return f"{hours}小时{minutes}分钟{seconds}秒"
    else:
        return f"{minutes}分钟{seconds}秒"


class HTMLReportGenerator:
    """生成HTML统计报告"""

    def __init__(
        self,
        name_mapping: dict,
        stat_period: list,
        deploy_time: datetime,
    ):
        """
        初始化报告生成器。

        :param name_mapping: 聊天ID到名称的映射。
        :param stat_period: 统计时间段配置。
        :param deploy_time: 系统部署时间。
        """
        self.name_mapping = name_mapping
        self.stat_period = stat_period
        self.deploy_time = deploy_time

    def _format_stat_data_div(self, stat_data: dict[str, Any], div_id: str, start_time: datetime, now: datetime) -> str:
        """
        将单个时间段的统计数据格式化为HTML div块。

        :param stat_data: 统计数据。
        :param div_id: div的ID。
        :param start_time: 统计时间段的开始时间。
        :param now: 当前时间。
        :return: HTML字符串。
        """
        # 按模型分类统计
        model_rows = "\n".join(
            [
                f"<tr>"
                f"<td>{model_name}</td>"
                f"<td>{count}</td>"
                f"<td>{stat_data[AVG_TOK_BY_MODEL].get(model_name, 0)}</td>"
                f"<td>{stat_data[TOTAL_TOK_BY_MODEL].get(model_name, 0)}</td>"
                f"<td>{stat_data[TPS_BY_MODEL].get(model_name, 0):.2f}</td>"
                f"<td>{stat_data[COST_PER_KTOK_BY_MODEL].get(model_name, 0):.4f} ¥</td>"
                f"<td>{stat_data[COST_BY_MODEL].get(model_name, 0):.4f} ¥</td>"
                f"<td>{stat_data[AVG_TIME_COST_BY_MODEL].get(model_name, 0):.3f} 秒</td>"
                f"</tr>"
                for model_name, count in sorted(stat_data[REQ_CNT_BY_MODEL].items())
            ]
        )
        # 按供应商分类统计
        provider_rows = "\n".join(
            [
                f"<tr>"
                f"<td>{provider_name}</td>"
                f"<td>{count}</td>"
                f"<td>{stat_data[TOTAL_TOK_BY_PROVIDER].get(provider_name, 0)}</td>"
                f"<td>{stat_data[TPS_BY_PROVIDER].get(provider_name, 0):.2f}</td>"
                f"<td>{stat_data[COST_PER_KTOK_BY_PROVIDER].get(provider_name, 0):.4f} ¥</td>"
                f"<td>{stat_data[COST_BY_PROVIDER].get(provider_name, 0):.4f} ¥</td>"
                f"</tr>"
                for provider_name, count in sorted(stat_data[REQ_CNT_BY_PROVIDER].items())
            ]
        )
        # 按请求类型分类统计
        type_rows = "\n".join(
            [
                f"<tr>"
                f"<td>{req_type}</td>"
                f"<td>{count}</td>"
                f"<td>{stat_data[IN_TOK_BY_TYPE].get(req_type, 0)}</td>"
                f"<td>{stat_data[OUT_TOK_BY_TYPE].get(req_type, 0)}</td>"
                f"<td>{stat_data[TOTAL_TOK_BY_TYPE].get(req_type, 0)}</td>"
                f"<td>{stat_data[COST_BY_TYPE].get(req_type, 0):.4f} ¥</td>"
                f"<td>{stat_data[AVG_TIME_COST_BY_TYPE].get(req_type, 0):.3f} 秒</td>"
                f"<td>{stat_data[STD_TIME_COST_BY_TYPE].get(req_type, 0):.3f} 秒</td>"
                f"</tr>"
                for req_type, count in sorted(stat_data[REQ_CNT_BY_TYPE].items())
            ]
        )
        # 按模块分类统计
        module_rows = "\n".join(
            [
                f"<tr>"
                f"<td>{module_name}</td>"
                f"<td>{count}</td>"
                f"<td>{stat_data[IN_TOK_BY_MODULE].get(module_name, 0)}</td>"
                f"<td>{stat_data[OUT_TOK_BY_MODULE].get(module_name, 0)}</td>"
                f"<td>{stat_data[TOTAL_TOK_BY_MODULE].get(module_name, 0)}</td>"
                f"<td>{stat_data[COST_BY_MODULE].get(module_name, 0):.4f} ¥</td>"
                f"<td>{stat_data[AVG_TIME_COST_BY_MODULE].get(module_name, 0):.3f} 秒</td>"
                f"<td>{stat_data[STD_TIME_COST_BY_MODULE].get(module_name, 0):.3f} 秒</td>"
                f"</tr>"
                for module_name, count in sorted(stat_data[REQ_CNT_BY_MODULE].items())
            ]
        )
        # 聊天消息统计
        chat_rows = "\n".join(
            [
                f"<tr><td>{self.name_mapping.get(chat_id, ('未知', 0))[0]}</td><td>{count}</td></tr>"
                for chat_id, count in sorted(stat_data[MSG_CNT_BY_CHAT].items())
            ]
        )
        summary_cards = f"""
            <div class="summary-cards">
                <div class="card">
                    <h3>总花费</h3>
                    <p>{stat_data.get(TOTAL_COST, 0):.4f} ¥</p>
                </div>
                <div class="card">
                    <h3>总请求数</h3>
                    <p>{stat_data.get(TOTAL_REQ_CNT, 0)}</p>
                </div>
                <div class="card">
                    <h3>总Token数</h3>
                    <p>{sum(stat_data.get(TOTAL_TOK_BY_MODEL, {}).values())}</p>
                </div>
                 <div class="card">
                    <h3>总消息数</h3>
                    <p>{stat_data.get(TOTAL_MSG_CNT, 0)}</p>
                </div>
                <div class="card">
                    <h3>总在线时间</h3>
                    <p>{format_online_time(int(stat_data.get(ONLINE_TIME, 0)))}</p>
                </div>
            </div>
        """

        # 增加饼图和条形图
        # static_charts = self._generate_static_charts_div(stat_data, div_id) # 该功能尚未实现
        static_charts = ""
        return f"""
        <div id="{div_id}" class="tab-content">
            <p class="info-item">
                <strong>统计时段: </strong>
                {start_time.strftime("%Y-%m-%d %H:%M:%S")} ~ {now.strftime("%Y-%m-%d %H:%M:%S")}
            </p>
            {summary_cards}
            {static_charts}

            <h2>按模型分类统计</h2>
            <table>
                <tr><th>模型名称</th><th>调用次数</th><th>平均Token数</th><th>Token总量</th><th>TPS</th><th>每K Token成本</th><th>累计花费</th><th>平均耗时(秒)</th></tr>
                <tbody>{model_rows}</tbody>
            </table>

            <h2>按供应商分类统计</h2>
            <table>
                <tr><th>供应商名称</th><th>调用次数</th><th>Token总量</th><th>TPS</th><th>每K Token成本</th><th>累计花费</th></tr>
                <tbody>{provider_rows}</tbody>
            </table>

            <h2>按模块分类统计</h2>
            <table>
                <thead>
                    <tr><th>模块名称</th><th>调用次数</th><th>输入Token</th><th>输出Token</th><th>Token总量</th><th>累计花费</th><th>平均耗时(秒)</th><th>标准差(秒)</th></tr>
                </thead>
                <tbody>{module_rows}</tbody>
            </table>

            <h2>按请求类型分类统计</h2>
            <table>
                <thead>
                    <tr><th>请求类型</th><th>调用次数</th><th>输入Token</th><th>输出Token</th><th>Token总量</th><th>累计花费</th><th>平均耗时(秒)</th><th>标准差(秒)</th></tr>
                </thead>
                <tbody>{type_rows}</tbody>
            </table>

            <h2>聊天消息统计</h2>
            <table>
                <thead>
                    <tr><th>联系人/群组名称</th><th>消息数量</th></tr>
                </thead>
                <tbody>{chat_rows}</tbody>
            </table>
        </div>
        """
    def _generate_chart_tab(self, chart_data: dict) -> str:
        """生成图表选项卡的HTML内容。"""
        return f"""
        <div id="charts" class="tab-content">
            <h2>数据图表</h2>
            <div style="margin: 20px 0; text-align: center;">
                <label style="margin-right: 10px; font-weight: bold;">时间范围:</label>
                <button class="time-range-btn" onclick="switchTimeRange('6h')">6小时</button>
                <button class="time-range-btn" onclick="switchTimeRange('12h')">12小时</button>
                <button class="time-range-btn active" onclick="switchTimeRange('24h')">24小时</button>
                <button class="time-range-btn" onclick="switchTimeRange('48h')">48小时</button>
            </div>
            <div style="margin-top: 20px;">
                <div style="margin-bottom: 40px;"><canvas id="totalCostChart" width="800" height="400"></canvas></div>
                <div style="margin-bottom: 40px;"><canvas id="costByModuleChart" width="800" height="400"></canvas></div>
                <div style="margin-bottom: 40px;"><canvas id="costByModelChart" width="800" height="400"></canvas></div>
                <div><canvas id="messageByChatChart" width="800" height="400"></canvas></div>
            </div>
            <style>
                .time-range-btn {{
                    background-color: #ecf0f1; border: 1px solid #bdc3c7; color: #2c3e50;
                    padding: 8px 16px; margin: 0 5px; border-radius: 4px; cursor: pointer;
                    font-size: 14px; transition: all 0.3s ease;
                }}
                .time-range-btn:hover {{ background-color: #d5dbdb; }}
                .time-range-btn.active {{ background-color: #3498db; color: white; border-color: #2980b9; }}
            </style>
            <script>
                const allChartData = {chart_data};
                let currentCharts = {{}};
                const chartConfigs = {{
                    totalCost: {{ id: 'totalCostChart', title: '总花费', yAxisLabel: '花费 (¥)', dataKey: 'total_cost_data', fill: true }},
                    costByModule: {{ id: 'costByModuleChart', title: '各模块花费', yAxisLabel: '花费 (¥)', dataKey: 'cost_by_module', fill: false }},
                    costByModel: {{ id: 'costByModelChart', title: '各模型花费', yAxisLabel: '花费 (¥)', dataKey: 'cost_by_model', fill: false }},
                    messageByChat: {{ id: 'messageByChatChart', title: '各聊天流消息数', yAxisLabel: '消息数', dataKey: 'message_by_chat', fill: false }}
                }};
                function switchTimeRange(timeRange) {{
                    document.querySelectorAll('.time-range-btn').forEach(btn => btn.classList.remove('active'));
                    event.target.classList.add('active');
                    updateAllCharts(allChartData[timeRange], timeRange);
                }}
                function updateAllCharts(data, timeRange) {{
                    Object.values(currentCharts).forEach(chart => chart && chart.destroy());
                    currentCharts = {{}};
                    Object.keys(chartConfigs).forEach(type => createChart(type, data, timeRange));
                }}
                function createChart(chartType, data, timeRange) {{
                    const config = chartConfigs[chartType];
                    if (!data || !data[config.dataKey]) return;
                    const colors = ['#3498db', '#e74c3c', '#2ecc71', '#f39c12', '#9b59b6', '#1abc9c', '#34495e', '#e67e22', '#95a5a6', '#f1c40f'];
                    let datasets = [];
                    if (chartType === 'totalCost') {{
                        datasets = [{{ label: config.title, data: data[config.dataKey], borderColor: colors[0], backgroundColor: 'rgba(52, 152, 219, 0.1)', tension: 0.4, fill: config.fill }}];
                    }} else {{
                        let i = 0;
                        Object.entries(data[config.dataKey]).forEach(([name, chartData]) => {{
                            datasets.push({{ label: name, data: chartData, borderColor: colors[i % colors.length], backgroundColor: colors[i % colors.length] + '20', tension: 0.4, fill: config.fill }});
                            i++;
                        }});
                    }}
                    currentCharts[chartType] = new Chart(document.getElementById(config.id), {{
                        type: 'line',
                        data: {{ labels: data.time_labels, datasets: datasets }},
                        options: {{
                            responsive: true,
                            plugins: {{ title: {{ display: true, text: `${{timeRange}}内${{config.title}}趋势`, font: {{ size: 16 }} }}, legend: {{ display: chartType !== 'totalCost', position: 'top' }} }},
                            scales: {{ x: {{ title: {{ display: true, text: '时间' }}, ticks: {{ maxTicksLimit: 12 }} }}, y: {{ title: {{ display: true, text: config.yAxisLabel }}, beginAtZero: true }} }},
                            interaction: {{ intersect: false, mode: 'index' }}
                        }}
                    }});
                }}
                document.addEventListener('DOMContentLoaded', function() {{
                    if (allChartData['24h']) {{
                         updateAllCharts(allChartData['24h'], '24h');
                    }}
                }});
            </script>
        </div>
        """

    async def generate_report(self, stat: dict[str, Any], chart_data: dict, now: datetime, output_path: str):
        """
        生成并写入完整的HTML报告文件。

        :param stat: 所有时间段的统计数据。
        :param chart_data: 用于图表的数据。
        :param now: 当前时间。
        :param output_path: 输出文件路径。
        """
        tab_list = [
            f'<button class="tab-link" onclick="showTab(event, \'{period[0]}\')">{period[2]}</button>'
            for period in self.stat_period
        ]
        tab_list.append('<button class="tab-link" onclick="showTab(event, \'charts\')">数据图表</button>')

        tab_content_list = [
            self._format_stat_data_div(stat[period[0]], period[0], now - period[1], now)
            for period in self.stat_period
            if period[0] != "all_time"
        ]
        tab_content_list.append(
            self._format_stat_data_div(stat["all_time"], "all_time", self.deploy_time, now)
        )
        tab_content_list.append(self._generate_chart_tab(chart_data))

        joined_tab_list = "\n".join(tab_list)
        joined_tab_content = "\n".join(tab_content_list)

        html_template = f"""
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>MoFox-Bot运行统计报告</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif; margin: 0; padding: 20px; background-color: #f4f7f6; color: #333; line-height: 1.6; }}
        .container {{ max-width: 900px; margin: 20px auto; background-color: #fff; padding: 25px; border-radius: 8px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }}
        h1, h2 {{ color: #2c3e50; border-bottom: 2px solid #3498db; padding-bottom: 10px; margin-top: 0; }}
        h1 {{ text-align: center; font-size: 2em; }}
        h2 {{ font-size: 1.5em; margin-top: 30px; }}
        p {{ margin-bottom: 10px; }}
        .info-item {{ background-color: #ecf0f1; padding: 8px 12px; border-radius: 4px; margin-bottom: 8px; font-size: 0.95em; }}
        .info-item strong {{ color: #2980b9; }}
        table {{ width: 100%; border-collapse: collapse; margin-top: 15px; font-size: 0.9em; }}
        th, td {{ border: 1px solid #ddd; padding: 10px; text-align: left; }}
        th {{ background-color: #3498db; color: white; font-weight: bold; }}
        tr:nth-child(even) {{ background-color: #f9f9f9; }}
        .footer {{ text-align: center; margin-top: 30px; font-size: 0.8em; color: #7f8c8d; }}
        .tabs {{ overflow: hidden; background: #ecf0f1; display: flex; }}
        .tabs button {{ background: inherit; border: none; outline: none; padding: 14px 16px; cursor: pointer; transition: 0.3s; font-size: 16px; }}
        .tabs button:hover {{ background-color: #d4dbdc; }}
        .tabs button.active {{ background-color: #b3bbbd; }}
        .tab-content {{ display: none; padding: 20px; background-color: #fff; border: 1px solid #ccc; }}
        .tab-content.active {{ display: block; }}
    </style>
</head>
<body>
    <div class="container">
        <h1>MoFox-Bot运行统计报告</h1>
        <p class="info-item"><strong>统计截止时间:</strong> {now.strftime("%Y-%m-%d %H:%M:%S")}</p>
        <div class="tabs">{joined_tab_list}</div>
        {joined_tab_content}
    </div>
<script>
    let i, tab_content, tab_links;
    tab_content = document.getElementsByClassName("tab-content");
    tab_links = document.getElementsByClassName("tab-link");
    if(tab_content.length > 0) tab_content[0].classList.add("active");
    if(tab_links.length > 0) tab_links[0].classList.add("active");
    function showTab(evt, tabName) {{
        for (i = 0; i < tab_content.length; i++) tab_content[i].classList.remove("active");
        for (i = 0; i < tab_links.length; i++) tab_links[i].classList.remove("active");
        document.getElementById(tabName).classList.add("active");
        evt.currentTarget.classList.add("active");
    }}
</script>
</body>
</html>
        """
        async with aiofiles.open(output_path, "w", encoding="utf-8") as f:
            await f.write(html_template)
