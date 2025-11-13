let i, tab_content, tab_links;
tab_content = document.getElementsByClassName("tab-content");
tab_links = document.getElementsByClassName("tab-link");
if (tab_content.length > 0) tab_content[0].classList.add("active");
if (tab_links.length > 0) tab_links[0].classList.add("active");
function showTab(evt, tabName) {
    for (i = 0; i < tab_content.length; i++) tab_content[i].classList.remove("active");
    for (i = 0; i < tab_links.length; i++) tab_links[i].classList.remove("active");
    document.getElementById(tabName).classList.add("active");
    evt.currentTarget.classList.add("active");
}

document.addEventListener('DOMContentLoaded', function () {
    // This is a placeholder for chart data which will be injected by python.
    const allChartData = JSON.parse('{{ all_chart_data }}')
;
    let currentCharts = {};
    const chartConfigs = {
        totalCost: { id: 'totalCostChart', title: '总花费', yAxisLabel: '花费 (¥)', dataKey: 'total_cost_data', fill: true },
        costByModule: { id: 'costByModuleChart', title: '各模块花费', yAxisLabel: '花费 (¥)', dataKey: 'cost_by_module', fill: false },
        costByModel: { id: 'costByModelChart', title: '各模型花费', yAxisLabel: '花费 (¥)', dataKey: 'cost_by_model', fill: false },
        messageByChat: { id: 'messageByChatChart', title: '各聊天流消息数', yAxisLabel: '消息数', dataKey: 'message_by_chat', fill: false }
    };

    window.switchTimeRange = function(timeRange) {
        document.querySelectorAll('.time-range-btn').forEach(btn => btn.classList.remove('active'));
        event.target.classList.add('active');
        updateAllCharts(allChartData[timeRange], timeRange);
    }

    function updateAllCharts(data, timeRange) {
        Object.values(currentCharts).forEach(chart => chart && chart.destroy());
        currentCharts = {};
        Object.keys(chartConfigs).forEach(type => createChart(type, data, timeRange));
    }

    function createChart(chartType, data, timeRange) {
        const config = chartConfigs[chartType];
        if (!data || !data[config.dataKey]) return;
        const colors = ['#3498db', '#e74c3c', '#2ecc71', '#f39c12', '#9b59b6', '#1abc9c', '#34495e', '#e67e22', '#95a5a6', '#f1c40f'];
        let datasets = [];
        if (chartType === 'totalCost') {
            datasets = [{ label: config.title, data: data[config.dataKey], borderColor: colors[0], backgroundColor: 'rgba(52, 152, 219, 0.1)', tension: 0.4, fill: config.fill }];
        } else {
            let i = 0;
            Object.entries(data[config.dataKey]).forEach(([name, chartData]) => {
                datasets.push({ label: name, data: chartData, borderColor: colors[i % colors.length], backgroundColor: colors[i % colors.length] + '20', tension: 0.4, fill: config.fill });
                i++;
            });
        }
        currentCharts[chartType] = new Chart(document.getElementById(config.id), {
            type: 'line',
            data: { labels: data.time_labels, datasets: datasets },
            options: {
                responsive: true,
                plugins: { title: { display: true, text: `${timeRange}内${config.title}趋势`, font: { size: 16 } }, legend: { display: chartType !== 'totalCost', position: 'top' } },
                scales: { x: { title: { display: true, text: '时间' }, ticks: { maxTicksLimit: 12 } }, y: { title: { display: true, text: config.yAxisLabel }, beginAtZero: true } },
                interaction: { intersect: false, mode: 'index' }
            }
        });
    }

    if (allChartData['24h']) {
        updateAllCharts(allChartData['24h'], '24h');
        // Activate the 24h button by default
        document.querySelectorAll('.time-range-btn').forEach(btn => {
            if (btn.textContent.includes('24小时')) {
                btn.classList.add('active');
            } else {
                btn.classList.remove('active');
            }
        });
    }

    // Static charts
    const staticChartData = JSON.parse('{{ static_chart_data }}')
;
    Object.keys(staticChartData).forEach(period_id => {
        const providerCostData = staticChartData[period_id].provider_cost_data;
        const modelCostData = staticChartData[period_id].model_cost_data;
        const colors = ['#3498db', '#2ecc71', '#f1c40f', '#e74c3c', '#9b59b6', '#1abc9c', '#34495e', '#e67e22'];

        // Provider Cost Pie Chart
        const providerCtx = document.getElementById(`providerCostPieChart_${period_id}`);
        if (providerCtx && providerCostData && providerCostData.data.length > 0) {
            new Chart(providerCtx, {
                type: 'pie',
                data: {
                    labels: providerCostData.labels,
                    datasets: [{
                        label: '按供应商花费',
                        data: providerCostData.data,
                        backgroundColor: colors,
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {
                        title: { display: true, text: '按供应商花费分布', font: { size: 16 } },
                        legend: { position: 'top' }
                    }
                }
            });
        }

        // Model Cost Bar Chart
        const modelCtx = document.getElementById(`modelCostBarChart_${period_id}`);
        if (modelCtx && modelCostData && modelCostData.data.length > 0) {
            new Chart(modelCtx, {
                type: 'bar',
                data: {
                    labels: modelCostData.labels,
                    datasets: [{
                        label: '按模型花费',
                        data: modelCostData.data,
                        backgroundColor: colors,
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {
                        title: { display: true, text: '按模型花费排行', font: { size: 16 } },
                        legend: { display: false }
                    },
                    scales: {
                        y: { beginAtZero: true, title: { display: true, text: '花费 (¥)' } }
                    }
                }
            });
        }
    });
});