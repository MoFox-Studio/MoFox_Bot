from datetime import datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query

from src.chat.utils.statistic import (
    COST_BY_MODEL,
    COST_BY_MODULE,
    COST_BY_TYPE,
    COST_BY_USER,
    IN_TOK_BY_MODEL,
    IN_TOK_BY_MODULE,
    IN_TOK_BY_TYPE,
    IN_TOK_BY_USER,
    OUT_TOK_BY_MODEL,
    OUT_TOK_BY_MODULE,
    OUT_TOK_BY_TYPE,
    OUT_TOK_BY_USER,
    REQ_CNT_BY_MODEL,
    REQ_CNT_BY_MODULE,
    REQ_CNT_BY_TYPE,
    REQ_CNT_BY_USER,
    TOTAL_COST,
    TOTAL_REQ_CNT,
    TOTAL_TOK_BY_MODEL,
    TOTAL_TOK_BY_MODULE,
    TOTAL_TOK_BY_TYPE,
    TOTAL_TOK_BY_USER,
    StatisticOutputTask,
)
from src.common.logger import get_logger

logger = get_logger("LLM统计API")

router = APIRouter()


@router.get("/llm/stats")
async def get_llm_stats(
    period_type: Literal["fixed", "daily", "custom"] = Query(
        "daily", description="查询的时间段类型: 'fixed' (固定), 'daily' (按天), 'custom' (自定义)"
    ),
    days: int = Query(1, ge=1, description="当 period_type 为 'daily' 时，指定查询过去多少天的数据"),
    start_time_str: str = Query(None, description="当 period_type 为 'custom' 时，指定查询的开始时间 (ISO 8601)"),
    end_time_str: str = Query(None, description="当 period_type 为 'custom' 时，指定查询的结束时间 (ISO 8601)"),
    group_by: Literal["model", "module", "user", "type"] = Query("model", description="按指定维度对结果进行分组"),
):
    """
    获取大模型使用情况的统计信息。
    """
    try:
        now = datetime.now()
        start_time, end_time = None, now

        if period_type == "daily":
            start_time = now - timedelta(days=days)
        elif period_type == "custom":
            if not start_time_str or not end_time_str:
                raise HTTPException(status_code=400, detail="自定义时间段必须提供开始和结束时间")
            try:
                start_time = datetime.fromisoformat(start_time_str)
                end_time = datetime.fromisoformat(end_time_str)
            except ValueError:
                raise HTTPException(status_code=400, detail="无效的日期时间格式，请使用ISO 8601格式")
        elif period_type == "fixed":
            # 预设的固定时间段，这里以最近一小时为例
            start_time = now - timedelta(hours=1)

        if start_time is None:
            raise HTTPException(status_code=400, detail="无法确定查询的起始时间")

        # 调用统计函数
        stats_data = await StatisticOutputTask._collect_model_request_for_period([("custom", start_time)])
        period_stats = stats_data.get("custom", {})

        if not period_stats:
            return {"period": {"start": start_time.isoformat(), "end": end_time.isoformat()}, "data": {}}

        # 根据 group_by 参数选择对应的数据
        key_mapping = {
            "model": (REQ_CNT_BY_MODEL, COST_BY_MODEL, IN_TOK_BY_MODEL, OUT_TOK_BY_MODEL, TOTAL_TOK_BY_MODEL),
            "module": (
                REQ_CNT_BY_MODULE,
                COST_BY_MODULE,
                IN_TOK_BY_MODULE,
                OUT_TOK_BY_MODULE,
                TOTAL_TOK_BY_MODULE,
            ),
            "user": (REQ_CNT_BY_USER, COST_BY_USER, IN_TOK_BY_USER, OUT_TOK_BY_USER, TOTAL_TOK_BY_USER),
            "type": (REQ_CNT_BY_TYPE, COST_BY_TYPE, IN_TOK_BY_TYPE, OUT_TOK_BY_TYPE, TOTAL_TOK_BY_TYPE),
        }
        req_key, cost_key, in_tok_key, out_tok_key, total_tok_key = key_mapping[group_by]

        details_by_group = {}
        for group_name, count in period_stats.get(req_key, {}).items():
            details_by_group[group_name] = {
                "requests": count,
                "cost": period_stats.get(cost_key, {}).get(group_name, 0),
                "input_tokens": period_stats.get(in_tok_key, {}).get(group_name, 0),
                "output_tokens": period_stats.get(out_tok_key, {}).get(group_name, 0),
                "total_tokens": period_stats.get(total_tok_key, {}).get(group_name, 0),
            }

        return {
            "period": {"start": start_time.isoformat(), "end": end_time.isoformat()},
            "total_requests": period_stats.get(TOTAL_REQ_CNT, 0),
            "total_cost": period_stats.get(TOTAL_COST, 0),
            "details_by_group": details_by_group,
        }

    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"获取LLM统计信息失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))