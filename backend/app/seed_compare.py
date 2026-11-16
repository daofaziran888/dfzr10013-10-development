"""同期对比分析示例数据：指标目录、对比方案与带业务日期的明细。

明细刻意覆盖了几类边界：2 月/3 月的长短月份、真实零值、未上报缺报、
只在一期出现的维度、以及分子分母都不同的比率指标。
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

# 指标目录：可加指标按范围汇总；比率指标必须提供分子分母，汇总后重算。
METRICS: list[dict[str, Any]] = [
    {"id": 1, "指标编码": "SALES_AMOUNT", "指标名称": "销售额", "指标类型": "可加",
     "单位": "元", "比率口径": None, "分子名称": None, "分母名称": None,
     "说明": "金额类可加指标，按维度直接汇总"},
    {"id": 2, "指标编码": "ORDER_COUNT", "指标名称": "订单量", "指标类型": "可加",
     "单位": "单", "比率口径": None, "分子名称": None, "分母名称": None,
     "说明": "数量类可加指标，按维度直接汇总"},
    {"id": 3, "指标编码": "CONVERSION_RATE", "指标名称": "成交转化率", "指标类型": "比率",
     "单位": "%", "比率口径": "百分比", "分子名称": "成交数", "分母名称": "访问数",
     "说明": "汇总分子分母后重新计算，不平均明细百分比"},
    {"id": 4, "指标编码": "AVG_ORDER_VALUE", "指标名称": "客单价", "指标类型": "比率",
     "单位": "元", "比率口径": "比值", "分子名称": "销售额", "分母名称": "订单量",
     "说明": "由销售额与订单量汇总后重算"},
]

METRIC_BY_CODE = {item["指标编码"]: item for item in METRICS}


def _days(start: str, end: str) -> list[str]:
    current = date.fromisoformat(start)
    last = date.fromisoformat(end)
    days: list[str] = []
    while current <= last:
        days.append(current.isoformat())
        current += timedelta(days=1)
    return days


# 方案：指标、基准/对比周期、维度、业务规则口径都可以在方案上看到。
PLANS: list[dict[str, Any]] = [
    {
        "id": 1, "status": "草稿", "pending": True, "abnormal": False,
        "方案编号": "COMP-0001", "方案名称": "销售额月度同期对比",
        "对比指标": "销售额", "指标列表": ["SALES_AMOUNT"], "维度列表": ["区域"],
        "基准周期": "2026-02-01~2026-02-28", "对比周期": "2026-03-01~2026-03-31",
        "基准周期开始": "2026-02-01", "基准周期结束": "2026-02-28",
        "对比周期开始": "2026-03-01", "对比周期结束": "2026-03-31",
        "对齐方式": "按日对齐", "方案状态": "草稿", "计算状态": "待计算",
        "当前数据集": None, "当前版本": None, "最近计算时间": None,
        "明细修订号": 0, "导入明细数": 0,
    },
    {
        "id": 2, "status": "草稿", "pending": True, "abnormal": False,
        "方案编号": "COMP-0002", "方案名称": "成交转化率渠道对比",
        "对比指标": "成交转化率", "指标列表": ["CONVERSION_RATE"], "维度列表": ["渠道"],
        "基准周期": "2026-01-01~2026-01-31", "对比周期": "2026-02-01~2026-02-28",
        "基准周期开始": "2026-01-01", "基准周期结束": "2026-01-31",
        "对比周期开始": "2026-02-01", "对比周期结束": "2026-02-28",
        "对齐方式": "按日对齐", "方案状态": "草稿", "计算状态": "待计算",
        "当前数据集": None, "当前版本": None, "最近计算时间": None,
        "明细修订号": 0, "导入明细数": 0,
    },
]

DETAILS: list[dict[str, Any]] = []


def _add_detail(plan_id: int, metric: str, day: str, dims: dict[str, str],
                value: float | None, status: str = "已上报",
                numerator: float | None = None, denominator: float | None = None,
                batch: str = "初始化批次") -> None:
    key = "|".join(f"{name}={dims[name]}" for name in sorted(dims))
    DETAILS.append({
        "id": len(DETAILS) + 1, "方案id": plan_id, "指标编码": metric,
        "业务日期": day, "维度值": dict(dims), "维度键": key,
        "指标值": value, "分子值": numerator, "分母值": denominator,
        "上报状态": status, "来源批次": batch, "录入时间": "2026-03-01T00:00:00",
        "修订号": 1,
    })


# ---- 方案 1：销售额，2026-02（28 天）对 2026-03（31 天） ----
# 华东、华北、西北、东北两期都有；华南只在基准期（消失维度）；西南只在对比期（新增维度）。
# 华东 03-20 整日未上报；华北 02-14 未上报。
# 东北两期都上报真实 0；西北基准期真实 0、对比期有销售（基准为零）。
_BASE_FACTOR = {"华东": 1.0, "华北": 0.72, "华南": 0.45, "西北": 0.0, "东北": 0.0}
_COMP_FACTOR = {"华东": 1.0, "华北": 0.72, "西南": 0.58, "西北": 0.5, "东北": 0.0}
for _day in _days("2026-02-01", "2026-02-28"):
    _offset = date.fromisoformat(_day).day
    for _name, _factor in _BASE_FACTOR.items():
        if _name == "华北" and _day == "2026-02-14":
            _add_detail(1, "SALES_AMOUNT", _day, {"区域": _name}, None, "未上报")
            continue
        _value = round(10000 * _factor + 120 * (_offset % 7), 2) if _factor else 0.0
        _add_detail(1, "SALES_AMOUNT", _day, {"区域": _name}, _value)

for _day in _days("2026-03-01", "2026-03-31"):
    _offset = date.fromisoformat(_day).day
    for _name, _factor in _COMP_FACTOR.items():
        if _name == "华东" and _day == "2026-03-20":
            _add_detail(1, "SALES_AMOUNT", _day, {"区域": _name}, None, "未上报")
            continue
        _value = round(11000 * _factor + 150 * (_offset % 5), 2) if _factor else 0.0
        _add_detail(1, "SALES_AMOUNT", _day, {"区域": _name}, _value)

# 华中：两期都有该维度，但对比期整期未上报 —— 属于「缺报不可比」，
# 不能因为基准期有数就把它当成对比期归零。
for _day in _days("2026-02-01", "2026-02-28"):
    _offset = date.fromisoformat(_day).day
    _add_detail(1, "SALES_AMOUNT", _day, {"区域": "华中"}, round(9000 + 100 * (_offset % 6), 2))
for _day in _days("2026-03-01", "2026-03-31"):
    _add_detail(1, "SALES_AMOUNT", _day, {"区域": "华中"}, None, "未上报")

# ---- 方案 2：成交转化率（分子成交数 / 分母访问数），2026-01 对 2026-02 ----
# 线上、线下两期都有；代理只在基准期；门店只在对比期。
# 线上 02-10 未上报；线下 01-15 未上报。
_CHANNELS = {"线上": (0.040, 0.043), "线下": (0.085, 0.070), "代理": (0.060, 0.0), "门店": (0.0, 0.050)}
for _day in _days("2026-01-01", "2026-01-31"):
    _offset = date.fromisoformat(_day).day
    for _name, (_rate, _) in _CHANNELS.items():
        if _name == "门店":
            continue
        if _name == "线下" and _day == "2026-01-15":
            _add_detail(2, "CONVERSION_RATE", _day, {"渠道": _name}, None, "未上报")
            continue
        _den = 900 + _offset * 7
        _num = round(_den * _rate)
        _add_detail(2, "CONVERSION_RATE", _day, {"渠道": _name}, round(_num / _den * 100, 4), "已上报", _num, _den)

for _day in _days("2026-02-01", "2026-02-28"):
    _offset = date.fromisoformat(_day).day
    for _name, (_, _rate) in _CHANNELS.items():
        if _name == "代理":
            continue
        if _name == "线上" and _day == "2026-02-10":
            _add_detail(2, "CONVERSION_RATE", _day, {"渠道": _name}, None, "未上报")
            continue
        _den = 1000 + _offset * 9
        _num = round(_den * _rate)
        _add_detail(2, "CONVERSION_RATE", _day, {"渠道": _name}, round(_num / _den * 100, 4), "已上报", _num, _den)

for plan in PLANS:
    plan["导入明细数"] = sum(1 for row in DETAILS if row["方案id"] == plan["id"])
    plan["明细修订号"] = 1 if plan["导入明细数"] else 0
