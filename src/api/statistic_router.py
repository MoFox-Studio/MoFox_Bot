from datetime import datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query

from src.chat.utils.statistic import (
    StatisticOutputTask,
)
from src.common.logger import get_logger
from src.common.security import get_api_key

logger = get_logger("LLM统计API")

router = APIRouter(dependencies=[Depends(get_api_key)])

# 定义统计数据的键,以减少魔法字符串
TOTAL_REQ_CNT = "total_requests"
TOTAL_COST = "total_cost"
REQ_CNT_BY_TYPE = "requests_by_type"
REQ_CNT_BY_USER = "requests_by_user"
REQ_CNT_BY_MODEL = "requests_by_model"
REQ_CNT_BY_MODULE = "requests_by_module"
IN_TOK_BY_TYPE = "in_tokens_by_type"
IN_TOK_BY_USER = "in_tokens_by_user"
IN_TOK_BY_MODEL = "in_tokens_by_model"
IN_TOK_BY_MODULE = "in_tokens_by_module"
OUT_TOK_BY_TYPE = "out_tokens_by_type"
OUT_TOK_BY_USER = "out_tokens_by_user"
OUT_TOK_BY_MODEL = "out_tokens_by_model"
OUT_TOK_BY_MODULE = "out_tokens_by_module"
TOTAL_TOK_BY_TYPE = "tokens_by_type"
TOTAL_TOK_BY_USER = "tokens_by_user"
TOTAL_TOK_BY_MODEL = "tokens_by_model"
TOTAL_TOK_BY_MODULE = "tokens_by_module"
COST_BY_TYPE = "costs_by_type"
COST_BY_USER = "costs_by_user"
COST_BY_MODEL = "costs_by_model"
COST_BY_MODULE = "costs_by_module"

@router.get("/llm/stats")
async def get_llm_stats(
    period_type: Literal[
        "daily", "custom", "last_hour", "last_24_hours", "last_7_days", "last_30_days"
    ] = Query("daily", description="查询的时间段类型"),
    days: int = Query(1, ge=1, description="当 period_type 为 'daily' 时,指定查询过去多少天的数据"),
    start_time_str: str = Query(None, description="当 period_type 为 'custom' 时,指定查询的开始时间 (ISO 8601)"),
    end_time_str: str = Query(None, description="当 period_type 为 'custom' 时,指定查询的结束时间 (ISO 8601)"),
    group_by: Literal["model", "module", "user", "type"] = Query("model", description="按指定维度对结果进行分组"),
):
    """
    获取大模型使用情况的统计信息。
    """
    try:
        now = datetime.now()
        end_time = now
        start_time = None

        if period_type == "daily":
            start_time = now - timedelta(days=days)
        elif period_type == "last_hour":
            start_time = now - timedelta(hours=1)
        elif period_type == "last_24_hours":
            start_time = now - timedelta(days=1)
        elif period_type == "last_7_days":
            start_time = now - timedelta(days=7)
        elif period_type == "last_30_days":
            start_time = now - timedelta(days=30)
        elif period_type == "custom":
            if not start_time_str or not end_time_str:
                raise HTTPException(status_code=400, detail="自定义时间段必须提供开始和结束时间")
            try:
                start_time = datetime.fromisoformat(start_time_str)
                end_time = datetime.fromisoformat(end_time_str)
            except ValueError:
                raise HTTPException(status_code=400, detail="无效的日期时间格式,请使用ISO 8601格式")

        if start_time is None:
            raise HTTPException(status_code=400, detail="无法确定查询的起始时间")

        stats_data = await StatisticOutputTask._collect_model_request_for_period([("custom", start_time)])
        period_stats = stats_data.get("custom", {})

        if not period_stats:
            return {"period": {"start": start_time.isoformat(), "end": end_time.isoformat()}, "data": {}}

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
