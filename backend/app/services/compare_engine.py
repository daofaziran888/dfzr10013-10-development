"""同期对比计算引擎：周期对齐、覆盖判定与差异计算。

三条底线：
1. 未上报的明细永远不按 0 参与汇总；缺报和真实零值分别表达。
2. 只有两期同一维度、同一对齐日都有已上报数据的部分才进入「实际参与比较」；
   其余进入「未纳入部分」并写明原因。
3. 比率指标汇总分子分母后重算，绝不平均明细百分比。

引擎不读写数据仓库，输入输出都是普通字典，便于单独核对。
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from typing import Any, Iterable

ADDITIVE = "可加"
RATIO = "比率"
REPORTED = "已上报"
ALIGN_BY_DAY = "按日对齐"
ALIGN_WHOLE = "按整期"
ALIGNMENT_OPTIONS = (ALIGN_BY_DAY, ALIGN_WHOLE)

STATUS_NORMAL = "正常变化"
STATUS_BASE_ZERO = "基准为零"
STATUS_BOTH_ZERO = "两期均为零"
STATUS_MISSING = "缺报不可比"
STATUS_NEW_DIM = "新增维度"
STATUS_GONE_DIM = "消失维度"
STATUS_BASE_DEN_ZERO = "基准分母为零"
STATUS_COMPARE_DEN_ZERO = "对比分母为零"
STATUS_BOTH_DEN_ZERO = "两期分母均为零"
STATUS_NO_DATA = "无数据"
STATUS_INCOMPLETE = "分子分母缺失"
STATUS_PERIOD_LENGTH = "周期天数不一致"


class PeriodError(ValueError):
    """周期格式或范围不合法。"""


def parse_period(value: str) -> tuple[date, date]:
    text = str(value or "").strip()
    if "~" not in text:
        raise PeriodError(f"周期「{text}」格式应为 开始日期~结束日期，例如 2026-02-01~2026-02-28")
    start_text, end_text = (part.strip() for part in text.split("~", 1))
    try:
        start = date.fromisoformat(start_text)
        end = date.fromisoformat(end_text)
    except ValueError as error:
        raise PeriodError(f"周期「{text}」包含无法识别的日期：{error}") from error
    if end < start:
        raise PeriodError(f"周期「{text}」的结束日期早于开始日期")
    return start, end


def period_days(start: date, end: date) -> list[str]:
    days: list[str] = []
    current = start
    while current <= end:
        days.append(current.isoformat())
        current += timedelta(days=1)
    return days


def _number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _round(value: float | None, digits: int = 6) -> float | None:
    return None if value is None else round(value, digits)


def _key_from_dims(dims: dict[str, Any]) -> str:
    return "|".join(f"{name}={dims[name]}" for name in sorted(dims))


def _dim_label(dims: dict[str, Any]) -> str:
    if not dims:
        return "全部"
    return " / ".join(f"{name}={dims[name]}" for name in sorted(dims))


def _is_reported(row: dict[str, Any]) -> bool:
    return str(row.get("上报状态") or REPORTED) == REPORTED


def _day_buckets(rows: Iterable[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        buckets[str(row.get("业务日期") or "")].append(row)
    return dict(buckets)


def _reported_day_set(rows: Iterable[dict[str, Any]]) -> set[str]:
    return {str(row.get("业务日期") or "") for row in rows if _is_reported(row)}


def _unreported_day_set(rows: Iterable[dict[str, Any]]) -> set[str]:
    return {str(row.get("业务日期") or "") for row in rows if not _is_reported(row)}


def _aggregate(rows: list[dict[str, Any]], metric: dict[str, Any]) -> dict[str, Any]:
    """汇总一组明细。只累加已上报明细；返回参与汇总的明细 id 便于核对。

    比率指标不累加逐条百分比，而是累加分子分母，最后重算一个合并比率。
    """
    result = {
        "明细条数": len(rows),
        "已上报条数": 0, "未上报条数": 0,
        "数值": 0.0, "分子": 0.0, "分母": 0.0,
        "已上报明细ids": [], "未上报明细ids": [],
        "全部数值为零": True, "有数值": False, "分子分母齐全": True,
    }
    for row in rows:
        if not _is_reported(row):
            result["未上报条数"] += 1
            result["未上报明细ids"].append(row["id"])
            continue
        result["已上报条数"] += 1
        result["已上报明细ids"].append(row["id"])
        numerator = _number(row.get("分子值"))
        denominator = _number(row.get("分母值"))
        value = _number(row.get("指标值"))
        if metric["指标类型"] == RATIO:
            if numerator is None or denominator is None:
                result["分子分母齐全"] = False
                continue
            if denominator == 0:
                # 分母为零的明细不进入数值，但分子分母仍计入合计，
                # 让汇总层能明确报出「分母为零」而不是静默丢掉这一行。
                result["分子"] += numerator
                result["全部数值为零"] = False
                continue
            value = numerator / denominator * 100 if metric.get("比率口径") == "百分比" else numerator / denominator
            result["分子"] += numerator
            result["分母"] += denominator
            # 数值只在最后按合并分子分母重算，不能累加逐条百分比。
            if value != 0:
                result["全部数值为零"] = False
            result["有数值"] = True
            continue
        if value is None:
            result["分子分母齐全"] = False
            continue
        result["数值"] += value
        result["有数值"] = True
        if value != 0:
            result["全部数值为零"] = False
    if metric["指标类型"] == RATIO:
        if result["分母"]:
            result["数值"] = (result["分子"] / result["分母"] * 100
                             if metric.get("比率口径") == "百分比" else result["分子"] / result["分母"])
        else:
            result["数值"] = 0.0
            result["全部数值为零"] = False
    if result["已上报条数"] == 0:
        result["全部数值为零"] = False
    return result


def _classify_additive(
    base_value: float, comp_value: float, comparable_days: int, *,
    period_length_mismatch: bool = False,
    both_zero: bool = False,
) -> dict[str, Any]:
    if period_length_mismatch:
        return {
            "状态": STATUS_PERIOD_LENGTH, "变化率": None, "变化量": None,
            "说明": "两期自然日数不同，整期汇总的可加指标受周期长度影响，不计算变化率；"
                    "请改用按日对齐，或直接核对两期原始汇总与日均",
        }
    if comparable_days <= 0:
        return {
            "状态": STATUS_MISSING, "变化率": None, "变化量": None,
            "说明": "两期没有同时上报的可比日期，无法形成变化率",
        }
    change = comp_value - base_value
    if base_value == 0 and comp_value == 0 and both_zero:
        return {
            "状态": STATUS_BOTH_ZERO, "变化率": None, "变化量": 0.0, "方向": "持平",
            "说明": "两期均为上报的真实零值，变化量为 0；不计算无意义的百分比变化",
        }
    if base_value == 0:
        return {
            "状态": STATUS_BASE_ZERO, "变化率": None, "变化量": _round(change),
            "方向": "上升" if change > 0 else ("下降" if change < 0 else "持平"),
            "说明": "基准期合计为零，百分比变化无定义；只给出绝对变化量，不写成经营增长",
        }
    rate = change / abs(base_value) * 100
    return {
        "状态": STATUS_NORMAL, "变化率": _round(rate, 4), "变化量": _round(change),
        "方向": "上升" if change > 0 else ("下降" if change < 0 else "持平"),
        "说明": "按两期同时上报的明细汇总后计算",
    }


def _classify_ratio(
    metric: dict[str, Any],
    base: dict[str, Any],
    comp: dict[str, Any],
    comparable_days: int,
) -> dict[str, Any]:
    percent = metric.get("比率口径") == "百分比"
    if not base["分子分母齐全"] or not comp["分子分母齐全"]:
        return {
            "状态": STATUS_INCOMPLETE, "变化率": None, "变化量": None,
            "基准值": None, "对比值": None,
            "说明": "存在缺少分子或分母的已上报明细，比率无法重算；请补齐后重新计算",
        }
    if comparable_days <= 0:
        return {
            "状态": STATUS_MISSING, "变化率": None, "变化量": None,
            "基准值": None, "对比值": None,
            "说明": "两期没有同时上报的可比日期，无法形成变化率",
        }
    base_rate = base["分子"] / base["分母"] * 100 if base["分母"] else None
    comp_rate = comp["分子"] / comp["分母"] * 100 if comp["分母"] else None
    if not base["分母"] and not comp["分母"]:
        return {
            "状态": STATUS_BOTH_DEN_ZERO, "变化率": None, "变化量": None,
            "基准值": None, "对比值": None,
            "说明": "两期分母均为 0，比率无定义",
        }
    if not base["分母"]:
        return {
            "状态": STATUS_BASE_DEN_ZERO, "变化率": None, "变化量": None,
            "基准值": None, "对比值": _round(comp_rate),
            "说明": "基准期分母为 0，基准比率无定义，无法形成变化率",
        }
    if not comp["分母"]:
        return {
            "状态": STATUS_COMPARE_DEN_ZERO, "变化率": None, "变化量": None,
            "基准值": _round(base_rate), "对比值": None,
            "说明": "对比期分母为 0，对比比率无定义，无法形成变化率",
        }
    # 「两期都为零」只在该侧确有已上报明细、且全部为真实零值时成立。
    base_num_zero = base["已上报条数"] > 0 and base["分子"] == 0
    comp_num_zero = comp["已上报条数"] > 0 and comp["分子"] == 0
    if base_num_zero and comp_num_zero:
        return {
            "状态": STATUS_BOTH_ZERO, "变化率": None, "变化量": 0.0, "方向": "持平",
            "基准值": _round(base_rate), "对比值": _round(comp_rate),
            "说明": "两期分子均为上报的真实零值，比率均为 0；不计算无意义的百分比变化",
        }
    if base_num_zero:
        return {
            "状态": STATUS_BASE_ZERO, "变化率": None, "变化量": _round(comp_rate - base_rate),
            "基准值": _round(base_rate), "对比值": _round(comp_rate),
            "方向": "上升" if comp_rate > base_rate else ("下降" if comp_rate < base_rate else "持平"),
            "说明": "基准期分子为零，基准比率为 0，相对变化率无定义；只给出比率差额",
        }
    # 对比期分子为零是可比的变化（例如转化率降到 0），相对变化率 -100% 仍然成立。

    change = comp_rate - base_rate
    relative = change / abs(base_rate) * 100
    return {
        "状态": STATUS_NORMAL,
        # 百分比口径用「百分点」表达差额；其他比值口径用指标单位。
        "变化量": _round(change),
        "变化单位": "百分点" if percent else metric.get("单位"),
        "变化率": _round(relative, 4),
        "方向": "上升" if change > 0 else ("下降" if change < 0 else "持平"),
        "基准值": _round(base_rate),
        "对比值": _round(comp_rate),
        "说明": "汇总两期分子分母后重新计算比率，再比较；未对明细百分比取平均",
    }


def _presence_status(base_rows: list[dict[str, Any]], comp_rows: list[dict[str, Any]]) -> str | None:
    if not base_rows and not comp_rows:
        return STATUS_NO_DATA
    if not base_rows:
        return STATUS_NEW_DIM
    if not comp_rows:
        return STATUS_GONE_DIM
    return None


def _dimension_cell(
    metric: dict[str, Any],
    dims: dict[str, Any],
    base_rows: list[dict[str, Any]],
    comp_rows: list[dict[str, Any]],
    alignment: str,
    fixed_pairs: list[tuple[str, str]],
    period_days_mismatch: bool,
) -> dict[str, Any]:
    base_all = _aggregate(base_rows, metric)
    comp_all = _aggregate(comp_rows, metric)
    base_buckets = _day_buckets(base_rows)
    comp_buckets = _day_buckets(comp_rows)

    base_reported_days = [day for day in base_buckets if _reported_day_set(base_buckets[day])]
    comp_reported_days = [day for day in comp_buckets if _reported_day_set(comp_buckets[day])]

    presence = _presence_status(base_rows, comp_rows)
    if alignment == ALIGN_BY_DAY:
        paired_days = fixed_pairs
        base_comparable_days = [base_day for base_day, comp_day in paired_days
                                if _reported_day_set(base_buckets.get(base_day, []))
                                and _reported_day_set(comp_buckets.get(comp_day, []))]
        comp_comparable_days = [comp_day for base_day, comp_day in paired_days
                                if _reported_day_set(base_buckets.get(base_day, []))
                                and _reported_day_set(comp_buckets.get(comp_day, []))]
        base_used_rows = [row for day in base_comparable_days for row in base_buckets.get(day, []) if _is_reported(row)]
        comp_used_rows = [row for day in comp_comparable_days for row in comp_buckets.get(day, []) if _is_reported(row)]
        comparable_days = len(base_comparable_days)
        base_unpaired_reported = sorted(set(base_reported_days) - set(base_comparable_days))
        comp_unpaired_reported = sorted(set(comp_reported_days) - set(comp_comparable_days))
        unpaired_reason = "两期日期错位或对侧缺报，未纳入比较"
        period_mismatch = False
    else:
        base_used_rows = [row for row in base_rows if _is_reported(row)]
        comp_used_rows = [row for row in comp_rows if _is_reported(row)]
        base_comparable_days = sorted(base_reported_days)
        comp_comparable_days = sorted(comp_reported_days)
        comparable_days = min(len(base_reported_days), len(comp_reported_days))
        base_unpaired_reported = []
        comp_unpaired_reported = []
        unpaired_reason = "整期口径不使用逐日配对"
        period_mismatch = metric["指标类型"] == ADDITIVE and period_days_mismatch

    base_used = _aggregate(base_used_rows, metric)
    comp_used = _aggregate(comp_used_rows, metric)

    if presence:
        change = {"状态": presence, "变化率": None, "变化量": None,
                  "说明": {
                      STATUS_NO_DATA: "两期都没有该维度的明细",
                      STATUS_NEW_DIM: "该维度只在对比期出现，属于新增维度，无法计算同期变化，不并入可比汇总",
                      STATUS_GONE_DIM: "该维度只在基准期出现，属于消失维度，无法计算同期变化，不并入可比汇总",
                  }[presence]}
    elif metric["指标类型"] == RATIO:
        change = _classify_ratio(metric, base_used, comp_used, comparable_days)
    else:
        change = _classify_additive(
            base_used["数值"], comp_used["数值"], comparable_days,
            period_length_mismatch=period_mismatch,
            both_zero=base_used["全部数值为零"] and comp_used["全部数值为零"],
        )

    if change["状态"] == STATUS_MISSING:
        if not base_reported_days and not comp_reported_days:
            reason = "两期均未上报"
        elif not base_reported_days:
            reason = "基准期未上报"
        elif not comp_reported_days:
            reason = "对比期未上报"
        else:
            reason = "两期没有同一对齐日的上报记录"
        change["说明"] = f"{reason}；缺报不按零值参与比较，无法形成变化率"

    excluded = {
        "基准期未上报日期": sorted(_unreported_day_set(base_rows)),
        "对比期未上报日期": sorted(_unreported_day_set(comp_rows)),
        "基准期已上报但未配对日期": base_unpaired_reported,
        "对比期已上报但未配对日期": comp_unpaired_reported,
        "基准期未上报明细ids": base_all["未上报明细ids"],
        "对比期未上报明细ids": comp_all["未上报明细ids"],
        "未配对原因": unpaired_reason,
    }

    return {
        "维度值": dict(dims),
        "维度标签": _dim_label(dims),
        "状态": change["状态"],
        "基准期": {
            "统计范围": {"开始": None, "结束": None, "明细条数": base_all["明细条数"]},
            "已上报条数": base_all["已上报条数"],
            "未上报条数": base_all["未上报条数"],
            "原始范围数值": _round(base_all["数值"]),
            "原始范围分子": _round(base_all["分子"]) if metric["指标类型"] == RATIO else None,
            "原始范围分母": _round(base_all["分母"]) if metric["指标类型"] == RATIO else None,
            "参与比较数值": _round(base_used["数值"]),
            "参与比较分子": _round(base_used["分子"]) if metric["指标类型"] == RATIO else None,
            "参与比较分母": _round(base_used["分母"]) if metric["指标类型"] == RATIO else None,
            "参与比较明细条数": base_used["已上报条数"],
            "分子分母齐全": base_used["分子分母齐全"],
            "上报天数": len(base_reported_days),
            "未上报天数": len(_unreported_day_set(base_rows)),
        },
        "对比期": {
            "统计范围": {"开始": None, "结束": None, "明细条数": comp_all["明细条数"]},
            "已上报条数": comp_all["已上报条数"],
            "未上报条数": comp_all["未上报条数"],
            "原始范围数值": _round(comp_all["数值"]),
            "原始范围分子": _round(comp_all["分子"]) if metric["指标类型"] == RATIO else None,
            "原始范围分母": _round(comp_all["分母"]) if metric["指标类型"] == RATIO else None,
            "参与比较数值": _round(comp_used["数值"]),
            "参与比较分子": _round(comp_used["分子"]) if metric["指标类型"] == RATIO else None,
            "参与比较分母": _round(comp_used["分母"]) if metric["指标类型"] == RATIO else None,
            "参与比较明细条数": comp_used["已上报条数"],
            "分子分母齐全": comp_used["分子分母齐全"],
            "上报天数": len(comp_reported_days),
            "未上报天数": len(_unreported_day_set(comp_rows)),
        },
        "覆盖": {
            "对齐方式": alignment,
            "可比天数": comparable_days,
            "基准期上报天数": len(base_reported_days),
            "对比期上报天数": len(comp_reported_days),
            "可比对齐日": sorted(comp_comparable_days) if alignment == ALIGN_BY_DAY else [],
        },
        "比较结果": change,
        "未纳入部分": excluded,
        "来源核对": {
            "基准期明细ids": base_all["已上报明细ids"],
            "基准期未上报明细ids": base_all["未上报明细ids"],
            "对比期明细ids": comp_all["已上报明细ids"],
            "对比期未上报明细ids": comp_all["未上报明细ids"],
            "参与比较基准期明细ids": base_used["已上报明细ids"],
            "参与比较对比期明细ids": comp_used["已上报明细ids"],
        },
    }


def _metric_summary(
    metric: dict[str, Any],
    cells: list[dict[str, Any]],
    *,
    alignment: str,
    aligned_days: int,
    period_length_mismatch: bool,
) -> dict[str, Any]:
    included = [cell for cell in cells if cell["状态"] not in
                (STATUS_NO_DATA, STATUS_NEW_DIM, STATUS_GONE_DIM, STATUS_MISSING)]
    base_value = sum(cell["基准期"]["参与比较数值"] or 0 for cell in included)
    comp_value = sum(cell["对比期"]["参与比较数值"] or 0 for cell in included)
    base_num = sum(cell["基准期"]["参与比较分子"] or 0 for cell in included)
    base_den = sum(cell["基准期"]["参与比较分母"] or 0 for cell in included)
    comp_num = sum(cell["对比期"]["参与比较分子"] or 0 for cell in included)
    comp_den = sum(cell["对比期"]["参与比较分母"] or 0 for cell in included)
    comparable_days = max((cell["覆盖"]["可比天数"] for cell in included), default=0)
    # 汇总口径取「最窄可比天数」：只要有一个参与汇总的维度少了一天，
    # 汇总值就不能声称整个对齐窗口都可比，避免把覆盖不足写成经营下降。
    narrowest_days = min((cell["覆盖"]["可比天数"] for cell in included), default=0)
    per_dimension_days = {cell["维度标签"]: cell["覆盖"]["可比天数"] for cell in included}

    merged_base = {
        "数值": base_value, "分子": base_num, "分母": base_den,
        "已上报条数": sum(cell["基准期"]["参与比较明细条数"] for cell in included),
        "分子分母齐全": all(cell["基准期"]["参与比较明细条数"] == 0 or cell["基准期"]["分子分母齐全"]
                       for cell in included),
        "全部数值为零": False,
    }
    merged_comp = {
        "数值": comp_value, "分子": comp_num, "分母": comp_den,
        "已上报条数": sum(cell["对比期"]["参与比较明细条数"] for cell in included),
        "分子分母齐全": all(cell["对比期"]["参与比较明细条数"] == 0 or cell["对比期"]["分子分母齐全"]
                       for cell in included),
        "全部数值为零": False,
    }
    # 汇总层面「两期均为零」按总量判断，只在所有参与汇总的维度合计为 0 时成立。
    merged_base["全部数值为零"] = bool(included) and base_value == 0
    merged_comp["全部数值为零"] = bool(included) and comp_value == 0

    if metric["指标类型"] == RATIO:
        change = _classify_ratio(metric, merged_base, merged_comp, narrowest_days)
    else:
        change = _classify_additive(
            base_value, comp_value, narrowest_days,
            period_length_mismatch=period_length_mismatch,
            both_zero=merged_base["全部数值为零"] and merged_comp["全部数值为零"],
        )

    if not included and cells:
        # 所有维度都不可比时，汇总状态要沿用维度层面的原因，不能笼统写成「缺报」。
        priority = [STATUS_NEW_DIM, STATUS_GONE_DIM, STATUS_MISSING, STATUS_NO_DATA, STATUS_INCOMPLETE]
        by_status = {cell["状态"] for cell in cells}
        chosen = next((status for status in priority if status in by_status), STATUS_NO_DATA)
        reason = next(cell["比较结果"]["说明"] for cell in cells if cell["状态"] == chosen)
        change = {"状态": chosen, "变化率": None, "变化量": None,
                  "说明": f"没有可参与汇总的维度：{reason}"}

    new_dims = [cell["维度标签"] for cell in cells if cell["状态"] == STATUS_NEW_DIM]
    gone_dims = [cell["维度标签"] for cell in cells if cell["状态"] == STATUS_GONE_DIM]
    missing_dims = [cell["维度标签"] for cell in cells if cell["状态"] == STATUS_MISSING]
    den_zero = [cell["维度标签"] for cell in cells
                if cell["状态"] in (STATUS_BASE_DEN_ZERO, STATUS_COMPARE_DEN_ZERO, STATUS_BOTH_DEN_ZERO)]
    base_zero_dims = [cell["维度标签"] for cell in cells if cell["状态"] == STATUS_BASE_ZERO]
    if not cells:
        change = {"状态": STATUS_NO_DATA, "变化率": None, "变化量": None,
                  "说明": "没有符合方案指标与维度口径的明细，无法形成对比；请核对导入明细的维度组合"}
    notes: list[str] = []
    if not cells:
        notes.append("没有可参与对比的维度，请检查明细是否按方案维度导入")
    if new_dims:
        notes.append(f"新增维度不并入汇总：{'、'.join(new_dims)}")
    if gone_dims:
        notes.append(f"消失维度不并入汇总：{'、'.join(gone_dims)}")
    if missing_dims:
        notes.append(f"缺报维度不并入汇总：{'、'.join(missing_dims)}")
    if den_zero:
        notes.append(f"分母为零、比率不可计算但仍计入汇总分子分母：{'、'.join(den_zero)}")
    if base_zero_dims:
        notes.append(f"基准为零、只给绝对差额：{'、'.join(base_zero_dims)}")
    if alignment == ALIGN_WHOLE and metric["指标类型"] == RATIO and period_length_mismatch:
        notes.append("整期口径下两期自然日数不同；比率按各自整期分子分母重算，"
                     "覆盖率差异请在「覆盖」中核对，不能与可加指标的整期汇总直接相加")
    if not notes:
        notes.append("所有维度均参与可比汇总")

    if metric["指标类型"] == RATIO:
        percent = metric.get("比率口径") == "百分比"
        base_rate = (base_num / base_den * 100 if percent else base_num / base_den) if base_den else None
        comp_rate = (comp_num / comp_den * 100 if percent else comp_num / comp_den) if comp_den else None
    else:
        base_rate, comp_rate = base_value, comp_value

    return {
        "指标编码": metric["指标编码"],
        "指标名称": metric["指标名称"],
        "指标类型": metric["指标类型"],
        "单位": metric["单位"],
        "比率口径": metric.get("比率口径"),
        "分子名称": metric.get("分子名称"),
        "分母名称": metric.get("分母名称"),
        "状态": change["状态"],
        "比较结果": change,
        "可比汇总": {
            # 比率指标的汇总值按合并分子分母重算，不是各维度比率的相加。
            "基准值": _round(base_rate),
            "对比值": _round(comp_rate),
            "基准分子": _round(base_num) if metric["指标类型"] == RATIO else None,
            "基准分母": _round(base_den) if metric["指标类型"] == RATIO else None,
            "对比分子": _round(comp_num) if metric["指标类型"] == RATIO else None,
            "对比分母": _round(comp_den) if metric["指标类型"] == RATIO else None,
        },
        "原始范围汇总": {
            # 比率指标的原始范围不累加百分比，改为给出原始分子分母合计；
            # 可加指标才累加数值。
            "基准期": _round(sum(cell["基准期"]["原始范围数值"] or 0 for cell in cells))
            if metric["指标类型"] == ADDITIVE else None,
            "对比期": _round(sum(cell["对比期"]["原始范围数值"] or 0 for cell in cells))
            if metric["指标类型"] == ADDITIVE else None,
            "基准期原始分子": _round(sum(cell["基准期"]["原始范围分子"] or 0 for cell in cells))
            if metric["指标类型"] == RATIO else None,
            "基准期原始分母": _round(sum(cell["基准期"]["原始范围分母"] or 0 for cell in cells))
            if metric["指标类型"] == RATIO else None,
            "对比期原始分子": _round(sum(cell["对比期"]["原始范围分子"] or 0 for cell in cells))
            if metric["指标类型"] == RATIO else None,
            "对比期原始分母": _round(sum(cell["对比期"]["原始范围分母"] or 0 for cell in cells))
            if metric["指标类型"] == RATIO else None,
            "基准期已上报条数": sum(cell["基准期"]["已上报条数"] for cell in cells),
            "对比期已上报条数": sum(cell["对比期"]["已上报条数"] for cell in cells),
            "基准期未上报条数": sum(cell["基准期"]["未上报条数"] for cell in cells),
            "对比期未上报条数": sum(cell["对比期"]["未上报条数"] for cell in cells),
        },
        "对齐方式": alignment,
        "对齐天数": aligned_days,
        "可比天数": narrowest_days,
        "最窄可比天数": narrowest_days,
        "各维度可比天数": per_dimension_days,
        "最长可比天数": comparable_days,
        "汇总口径": "汇总按所有参与维度中最窄的可比天数计，覆盖不足不会被写成经营变化",
        "参与汇总的维度数": len(included),
        "未参与汇总的维度": [cell["维度标签"] for cell in cells if cell not in included],
        "覆盖说明": notes,
        "维度明细": cells,
    }


def _count_period(rows: list[dict[str, Any]], days: list[str]) -> dict[str, Any]:
    by_day = _day_buckets(rows)
    reported_days = {day for day, day_rows in by_day.items() if any(_is_reported(row) for row in day_rows)}
    unreported_days = {day for day, day_rows in by_day.items() if any(not _is_reported(row) for row in day_rows)}
    complete_days = {day for day, day_rows in by_day.items()
                     if day_rows and all(_is_reported(row) for row in day_rows)}
    return {
        "自然日数": len(days), "明细条数": len(rows),
        "已上报条数": sum(1 for row in rows if _is_reported(row)),
        "未上报条数": sum(1 for row in rows if not _is_reported(row)),
        "有数据天数": len(reported_days),
        "包含缺报的天数": len(unreported_days),
        "完整上报天数": len(complete_days),
        "明细上报率": _round(sum(1 for row in rows if _is_reported(row)) / len(rows) * 100, 4) if rows else None,
        "涉及维度数": len({_key_from_dims(row.get("维度值") or {}) for row in rows}),
    }


def build_dataset(
    *,
    plan: dict[str, Any],
    metrics: list[dict[str, Any]],
    details: list[dict[str, Any]],
    alignment: str = ALIGN_BY_DAY,
    dataset_id: int,
    version: int,
    run_id: str,
    generated_at: str,
    revision: int,
    failpoint: str | None = None,
) -> dict[str, Any]:
    """生成完整对比数据集。纯函数；任一步失败都不会产生半份结果。

    failpoint 仅用于验证「中断后不拼半份结果」，正常调用不传。
    """
    if alignment not in ALIGNMENT_OPTIONS:
        raise ValueError(f"对齐方式「{alignment}」不在支持范围：{'、'.join(ALIGNMENT_OPTIONS)}")
    if not metrics:
        raise ValueError("对比方案没有选择任何指标")

    base_start, base_end = parse_period(plan["基准周期"])
    comp_start, comp_end = parse_period(plan["对比周期"])
    base_days = period_days(base_start, base_end)
    comp_days = period_days(comp_start, comp_end)
    if alignment == ALIGN_BY_DAY:
        span = min(len(base_days), len(comp_days))
        base_window = base_days[:span]
        comp_window = comp_days[:span]
        fixed_pairs = list(zip(base_window, comp_window))
    else:
        base_window = list(base_days)
        comp_window = list(comp_days)
        fixed_pairs = list(zip(base_window, comp_window))

    metric_codes = {metric["指标编码"] for metric in metrics}
    dim_field_names = [str(name) for name in plan.get("维度列表") or []]
    expected_dims = set(dim_field_names)

    base_by_dim: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    comp_by_dim: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    ignored: list[dict[str, Any]] = []
    dimension_mismatch: list[dict[str, Any]] = []
    base_out_of_window: list[dict[str, Any]] = []
    comp_out_of_window: list[dict[str, Any]] = []

    for row in details:
        code = str(row.get("指标编码") or "")
        if code not in metric_codes:
            ignored.append(row)
            continue
        # 方案维度改过之后，旧明细的维度组合可能与方案不一致：不硬塞进新口径，
        # 单独列为「维度与方案不一致」，避免把不同粒度的数据混在同一个对比里。
        row_dims = set((row.get("维度值") or {}).keys())
        if expected_dims and row_dims != expected_dims:
            dimension_mismatch.append(row)
            continue
        day = str(row.get("业务日期") or "")
        key = _key_from_dims(row.get("维度值") or {})
        if day in base_window:
            base_by_dim[code][key].append(row)
        elif day in comp_window:
            comp_by_dim[code][key].append(row)
        elif day in base_days:
            base_out_of_window.append(row)
        elif day in comp_days:
            comp_out_of_window.append(row)
        else:
            ignored.append(row)

    selected_base_rows = [row for row in details
                          if str(row.get("指标编码") or "") in metric_codes and str(row.get("业务日期") or "") in base_days]
    selected_comp_rows = [row for row in details
                          if str(row.get("指标编码") or "") in metric_codes and str(row.get("业务日期") or "") in comp_days]

    base_period_raw = _count_period(selected_base_rows, base_days)
    comp_period_raw = _count_period(selected_comp_rows, comp_days)
    base_window_raw = _count_period([row for row in selected_base_rows if str(row.get("业务日期") or "") in base_window], base_window)
    comp_window_raw = _count_period([row for row in selected_comp_rows if str(row.get("业务日期") or "") in comp_window], comp_window)

    # 生成第一项指标后触发失败，模拟一侧算完、另一侧尚未算完的中断。
    metric_outputs: list[dict[str, Any]] = []
    for metric in metrics:
        code = metric["指标编码"]
        keys = sorted(set(base_by_dim[code]) | set(comp_by_dim[code]))
        cells: list[dict[str, Any]] = []
        for key in keys:
            b_rows = base_by_dim[code].get(key, [])
            c_rows = comp_by_dim[code].get(key, [])
            dims = (b_rows or c_rows)[0].get("维度值") or {}
            cell = _dimension_cell(metric, dims, b_rows, c_rows, alignment, fixed_pairs,
                                   period_days_mismatch=len(base_days) != len(comp_days))
            cell["基准期"]["统计范围"].update({"开始": base_window[0], "结束": base_window[-1]})
            cell["对比期"]["统计范围"].update({"开始": comp_window[0], "结束": comp_window[-1]})
            cells.append(cell)
        summary = _metric_summary(
            metric, cells,
            alignment=alignment,
            aligned_days=len(fixed_pairs),
            period_length_mismatch=len(base_window) != len(comp_window),
        )
        metric_outputs.append(summary)
        if failpoint == "after_first_metric":
            raise RuntimeError("模拟计算中断：第一个指标完成后中断，未发布任何对比数据集")

    alignment_info = {
        "方式": alignment,
        "基准比较窗口": {"开始": base_window[0], "结束": base_window[-1], "天数": len(base_window)},
        "对比比较窗口": {"开始": comp_window[0], "结束": comp_window[-1], "天数": len(comp_window)},
        "对齐天数": len(fixed_pairs),
        "两期自然日数不同": len(base_days) != len(comp_days),
        "基准期未纳入日期": base_days[len(base_window):],
        "对比期未纳入日期": comp_days[len(comp_window):],
    }

    base_coverage = {
        "统计范围": {"开始": base_window[0], "结束": base_window[-1], "自然日数": len(base_window),
                     "明细条数": base_window_raw["明细条数"]},
        "完整上报天数": base_window_raw["完整上报天数"],
        "包含缺报的天数": base_window_raw["包含缺报的天数"],
        "已上报条数": base_window_raw["已上报条数"],
        "未上报条数": base_window_raw["未上报条数"],
        "明细上报率": base_window_raw["明细上报率"],
        "涉及维度数": base_window_raw["涉及维度数"],
    }
    comp_coverage = {
        "统计范围": {"开始": comp_window[0], "结束": comp_window[-1], "自然日数": len(comp_window),
                     "明细条数": comp_window_raw["明细条数"]},
        "完整上报天数": comp_window_raw["完整上报天数"],
        "包含缺报的天数": comp_window_raw["包含缺报的天数"],
        "已上报条数": comp_window_raw["已上报条数"],
        "未上报条数": comp_window_raw["未上报条数"],
        "明细上报率": comp_window_raw["明细上报率"],
        "涉及维度数": comp_window_raw["涉及维度数"],
    }

    warnings: list[str] = []
    if len(base_days) != len(comp_days):
        if alignment == ALIGN_BY_DAY:
            warnings.append(
                f"基准周期 {len(base_days)} 天、对比周期 {len(comp_days)} 天；"
                f"按日对齐取前 {len(base_window)} 天，长周期多出的 "
                f"{max(len(base_days), len(comp_days)) - len(base_window)} 天列入未纳入部分"
            )
        else:
            warnings.append(
                f"基准周期 {len(base_days)} 天、对比周期 {len(comp_days)} 天；"
                "整期对比下可加指标不计算变化率，避免把周期长度当成经营变化"
            )
    new_dims = sorted({cell["维度标签"] for m in metric_outputs for cell in m["维度明细"]
                       if cell["状态"] == STATUS_NEW_DIM})
    gone_dims = sorted({cell["维度标签"] for m in metric_outputs for cell in m["维度明细"]
                        if cell["状态"] == STATUS_GONE_DIM})
    missing_dims = sorted({cell["维度标签"] for m in metric_outputs for cell in m["维度明细"]
                           if cell["状态"] == STATUS_MISSING})
    if new_dims:
        warnings.append(f"仅对比期出现的新增维度：{'、'.join(new_dims)}；不并入可比汇总")
    if gone_dims:
        warnings.append(f"仅基准期出现的消失维度：{'、'.join(gone_dims)}；不并入可比汇总")
    if missing_dims:
        warnings.append(f"两期无法配对的缺报维度：{'、'.join(missing_dims)}；未按零值参与比较")
    if dimension_mismatch:
        warnings.append(
            f"{len(dimension_mismatch)} 条明细的维度组合与方案当前维度（{'、'.join(dim_field_names)}）不一致，"
            "已列入未纳入部分；请按方案维度重新导入或调整方案后重算"
        )

    unreported_ids = [row["id"] for row in selected_base_rows + selected_comp_rows if not _is_reported(row)]

    return {
        "id": dataset_id,
        "数据集编号": f"CMPDS-{plan['id']}-V{version}",
        "方案id": plan["id"],
        "方案编号": plan["方案编号"],
        "方案名称": plan.get("方案名称"),
        "版本": version,
        "状态": "已完成",
        "对齐方式": alignment,
        "生成时间": generated_at,
        "计算批次": run_id,
        "明细修订号": revision,
        "指标列表": [metric["指标编码"] for metric in metrics],
        "维度列表": dim_field_names,
        "周期": {
            "基准周期": {**base_period_raw, "文本": f"{base_start.isoformat()}~{base_end.isoformat()}"},
            "对比周期": {**comp_period_raw, "文本": f"{comp_start.isoformat()}~{comp_end.isoformat()}"},
            "对齐": alignment_info,
            "覆盖": {"基准期": base_coverage, "对比期": comp_coverage},
        },
        "指标": metric_outputs,
        "未纳入部分": {
            "基准期未纳入日期": alignment_info["基准期未纳入日期"],
            "对比期未纳入日期": alignment_info["对比期未纳入日期"],
            "基准期窗口外明细ids": [row["id"] for row in base_out_of_window],
            "对比期窗口外明细ids": [row["id"] for row in comp_out_of_window],
            "未上报明细ids": unreported_ids,
            "未选指标或周期外明细ids": [row["id"] for row in ignored],
            "维度与方案不一致明细ids": [row["id"] for row in dimension_mismatch],
            "未上报明细条数": len(unreported_ids),
            "单侧维度": {
                "仅基准期出现": sorted({cell["维度标签"] for m in metric_outputs
                                        for cell in m["维度明细"] if cell["状态"] == STATUS_GONE_DIM}),
                "仅对比期出现": sorted({cell["维度标签"] for m in metric_outputs
                                        for cell in m["维度明细"] if cell["状态"] == STATUS_NEW_DIM}),
            },
            "缺报不可比维度": sorted({cell["维度标签"] for m in metric_outputs
                                      for cell in m["维度明细"] if cell["状态"] == STATUS_MISSING}),
            "说明": "未上报明细不计入任何一期数值；未对齐日期与单侧维度不进入可比汇总",
        },
        "覆盖说明": warnings,
        "来源": {
            "方案id": plan["id"],
            "方案编号": plan["方案编号"],
            "明细修订号": revision,
            "计算批次": run_id,
            "生成时间": generated_at,
            "版本": version,
            "基准期明细ids": [row["id"] for row in selected_base_rows],
            "对比期明细ids": [row["id"] for row in selected_comp_rows],
        },
    }
