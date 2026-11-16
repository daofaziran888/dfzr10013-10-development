"""同期对比分析业务规则：方案配置、明细导入、周期对齐计算与历史版本。

几个关键约束都在这里落地：
- 明细导入整体校验，全通过才写入；补报/更正会抬高「明细修订号」。
- 计算先快照明细、在内存里完整算完，再一次性发布版本；中途失败不产生半份数据集。
- 发布前核对明细修订号与方案配置版本，期间有任何改动则作废本次计算，绝不拼接新旧结果。
- 已发布的数据集不可修改；重新计算只追加新版本，历史版本与已生成的报告附件保持原样。
"""
from __future__ import annotations

import copy
import uuid
from datetime import date, datetime
from typing import Any

from app.seed_compare import METRIC_BY_CODE, METRICS
from app.services.compare_engine import (
    ADDITIVE,
    ALIGN_BY_DAY,
    ALIGNMENT_OPTIONS,
    RATIO,
    PeriodError,
    build_dataset,
    parse_period,
)
from app.store import store

MODULE = "compare"
DATASETS = "compare_datasets"
DETAILS = "compare_details"
RUNS = "compare_runs"
METRICS_TABLE = "compare_metrics"

REQUIRED_FIELDS = ["方案编号", "基准周期", "对比周期"]
STATUS_ORDER = ["草稿", "已对比", "已分析", "已归档"]
ACTION_RULES = {"执行对比": "已对比", "原因分析": "已分析", "归档方案": "已归档"}
REPORT_STATUSES = ["已上报", "未上报"]
DIMENSION_DEFAULT = "区域"


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _text(value: Any) -> str:
    return str(value or "").strip()


def _numeric(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValueError("不能把布尔值当作数值")
    try:
        return float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"「{value}」不是有效数值") from error


def _dim_key(dims: dict[str, Any]) -> str:
    return "|".join(f"{name}={dims[name]}" for name in sorted(dims))


def _period_from(values: dict[str, Any], prefix: str) -> tuple[str, str, str]:
    """支持「基准周期」文本或「基准周期开始/结束」两种提交方式。"""
    direct = _text(values.get(f"{prefix}周期"))
    start = _text(values.get(f"{prefix}周期开始"))
    end = _text(values.get(f"{prefix}周期结束"))
    if direct:
        try:
            begin, finish = parse_period(direct)
        except PeriodError as error:
            raise ValueError(str(error)) from error
        return f"{begin.isoformat()}~{finish.isoformat()}", begin.isoformat(), finish.isoformat()
    if not start or not end:
        raise ValueError(f"缺少必填字段：{prefix}周期")
    try:
        begin, finish = parse_period(f"{start}~{end}")
    except PeriodError as error:
        raise ValueError(str(error)) from error
    return f"{begin.isoformat()}~{finish.isoformat()}", begin.isoformat(), finish.isoformat()


class CompareService:
    # ---------- 指标目录 ----------
    def list_metrics(self) -> list[dict[str, Any]]:
        return [dict(item) for item in store.rows(METRICS_TABLE)]

    def _resolve_metrics(self, values: dict[str, Any]) -> list[dict[str, Any]]:
        raw = values.get("指标列表")
        codes: list[str] = []
        if isinstance(raw, list):
            codes = [_text(code) for code in raw if _text(code)]
        elif isinstance(raw, str) and raw.strip():
            codes = [part.strip() for part in raw.replace("，", ",").split(",") if part.strip()]
        if not codes:
            display = _text(values.get("对比指标"))
            codes = [metric["指标编码"] for metric in METRICS if metric["指标名称"] == display]
        if not codes:
            raise ValueError("请选择对比指标（指标列表为空，且无法从对比指标名称匹配到指标目录）")
        unknown = [code for code in codes if code not in METRIC_BY_CODE]
        if unknown:
            raise ValueError(f"指标目录中不存在：{'、'.join(unknown)}")
        deduped: list[str] = []
        for code in codes:
            if code not in deduped:
                deduped.append(code)
        return [METRIC_BY_CODE[code] for code in deduped]

    def _resolve_dimensions(self, values: dict[str, Any], *, fallback: list[str] | None = None) -> list[str]:
        raw = values.get("维度列表", values.get("维度"))
        if raw is None:
            dims = list(fallback or [])
        elif isinstance(raw, list):
            dims = [_text(name) for name in raw if _text(name)]
        else:
            dims = [part.strip() for part in str(raw).replace("，", ",").split(",") if part.strip()]
        if not dims:
            dims = [DIMENSION_DEFAULT]
        if len(set(dims)) != len(dims):
            raise ValueError("维度列表存在重复项")
        return dims

    # ---------- 方案 ----------
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("方案编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def get_plan_detail(self, entry_id: int) -> dict[str, Any] | None:
        plan = store.find(MODULE, entry_id)
        if plan is None:
            return None
        detail = copy.deepcopy(plan)
        dataset = store.find(DATASETS, int(plan.get("当前数据集") or 0))
        detail["当前数据集摘要"] = self._dataset_brief(dataset) if dataset else None
        detail["版本历史"] = [
            self._dataset_brief(item) for item in sorted(
                (row for row in store.rows(DATASETS) if row.get("方案id") == entry_id),
                key=lambda row: int(row.get("版本", 0)),
            )
        ]
        detail["指标明细"] = [METRIC_BY_CODE[code] for code in plan.get("指标列表", []) if code in METRIC_BY_CODE]
        return detail

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS
                   if not _text(values.get(field))
                   and not (_text(values.get(f"{field}开始")) and _text(values.get(f"{field}结束")))]
        if missing:
            return None, missing
        code = _text(values.get("方案编号"))
        if any(row.get("方案编号") == code for row in store.rows(MODULE)):
            return None, [f"方案编号 {code} 已存在"]
        try:
            dims = self._resolve_dimensions(values)
            base_text, base_start, base_end = _period_from(values, "基准")
            comp_text, comp_start, comp_end = _period_from(values, "对比")
        except ValueError as error:
            return None, [str(error)]
        try:
            metrics = self._resolve_metrics(values)
        except ValueError as error:
            return None, [str(error)]
        alignment = _text(values.get("对齐方式")) or ALIGN_BY_DAY
        if alignment not in ALIGNMENT_OPTIONS:
            return None, [f"对齐方式「{alignment}」不在支持范围：{'、'.join(ALIGNMENT_OPTIONS)}"]
        with store.lock:
            entry = {
                "id": store.next_id(MODULE),
                "status": STATUS_ORDER[0],
                "pending": True,
                "abnormal": False,
                "方案编号": code,
                "方案名称": _text(values.get("方案名称")) or f"{code} 同期对比方案",
                "对比指标": "、".join(metric["指标名称"] for metric in metrics),
                "指标列表": [metric["指标编码"] for metric in metrics],
                "维度列表": dims,
                "基准周期": base_text,
                "基准周期开始": base_start,
                "基准周期结束": base_end,
                "对比周期": comp_text,
                "对比周期开始": comp_start,
                "对比周期结束": comp_end,
                "对齐方式": alignment,
                "方案状态": STATUS_ORDER[0],
                "计算状态": "待计算",
                "当前数据集": None,
                "当前版本": None,
                "最近计算时间": None,
                "明细修订号": 0,
                "配置版本": 1,
                "导入明细数": 0,
                "导入批次计数": 0,
                "变化幅度": None,
                "变化方向": None,
                "原因分析": None,
            }
            store.rows(MODULE).append(entry)
        return entry, []

    def update_entry(self, entry_id: int, values: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        plan = store.find(MODULE, entry_id)
        if plan is None:
            return None, f"对比方案 {entry_id} 不存在或已归档"
        if plan.get("status") == STATUS_ORDER[-1]:
            return None, "已归档的对比方案不能再修改配置"
        updates: dict[str, Any] = {}
        try:
            if "指标列表" in values or "对比指标" in values:
                metrics = self._resolve_metrics(values)
                updates["指标列表"] = [metric["指标编码"] for metric in metrics]
                updates["对比指标"] = "、".join(metric["指标名称"] for metric in metrics)
            if any(key in values for key in ("维度列表", "维度")):
                updates["维度列表"] = self._resolve_dimensions(values, fallback=plan.get("维度列表"))
            if any(key in values for key in ("基准周期", "基准周期开始", "基准周期结束")):
                text, start, end = _period_from(values, "基准")
                updates.update({"基准周期": text, "基准周期开始": start, "基准周期结束": end})
            if any(key in values for key in ("对比周期", "对比周期开始", "对比周期结束")):
                text, start, end = _period_from(values, "对比")
                updates.update({"对比周期": text, "对比周期开始": start, "对比周期结束": end})
            if "对齐方式" in values:
                alignment = _text(values.get("对齐方式"))
                if alignment not in ALIGNMENT_OPTIONS:
                    raise ValueError(f"对齐方式「{alignment}」不在支持范围：{'、'.join(ALIGNMENT_OPTIONS)}")
                updates["对齐方式"] = alignment
        except ValueError as error:
            return None, str(error)
        if not updates:
            return None, "没有可修改的配置项；支持指标列表、维度列表、基准/对比周期、对齐方式"
        with store.lock:
            plan.update(updates)
            plan["配置版本"] = int(plan.get("配置版本") or 1) + 1
            if plan.get("当前数据集"):
                plan["计算状态"] = "待重新计算"
        return plan, f"对比方案配置已更新（配置版本 V{plan['配置版本']}）；已发布的历史数据集不受影响"

    # ---------- 明细导入 ----------
    def _prepare_items(self, plan: dict[str, Any], items: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
        dims_required = [str(name) for name in plan.get("维度列表") or []]
        prepared: list[dict[str, Any]] = []
        errors: list[str] = []
        for index, item in enumerate(items, start=1):
            if not isinstance(item, dict):
                errors.append(f"第 {index} 条明细不是对象")
                continue
            code = _text(item.get("指标编码"))
            metric = METRIC_BY_CODE.get(code)
            if metric is None:
                errors.append(f"第 {index} 条：指标编码「{code}」不在指标目录中")
                continue
            day_text = _text(item.get("业务日期"))
            try:
                day = date.fromisoformat(day_text).isoformat()
            except ValueError:
                errors.append(f"第 {index} 条：业务日期「{day_text}」不是 YYYY-MM-DD")
                continue
            dims_raw = item.get("维度值") or {}
            if not isinstance(dims_raw, dict):
                errors.append(f"第 {index} 条：维度值应为对象")
                continue
            dims = {_text(key): _text(value) for key, value in dims_raw.items()}
            missing_dims = [name for name in dims_required if not dims.get(name)]
            if missing_dims:
                errors.append(f"第 {index} 条：缺少维度 {'、'.join(missing_dims)}")
                continue
            extra_dims = [name for name in dims if name not in dims_required]
            if extra_dims:
                errors.append(f"第 {index} 条：方案未选择维度 {'、'.join(extra_dims)}，请先调整方案维度")
                continue
            report_status = _text(item.get("上报状态")) or "已上报"
            if report_status not in REPORT_STATUSES:
                errors.append(f"第 {index} 条：上报状态应为 {'、'.join(REPORT_STATUSES)}")
                continue
            try:
                value = _numeric(item.get("指标值"))
                numerator = _numeric(item.get("分子值"))
                denominator = _numeric(item.get("分母值"))
            except ValueError as error:
                errors.append(f"第 {index} 条：{error}")
                continue
            if report_status == "未上报":
                if value is not None or numerator is not None or denominator is not None:
                    errors.append(f"第 {index} 条：未上报明细不能同时提供指标值/分子/分母，"
                                  "请把真实零值标记为已上报、指标值填 0")
                    continue
            else:
                if metric["指标类型"] == ADDITIVE and value is None:
                    errors.append(f"第 {index} 条：可加指标「{metric['指标名称']}」已上报时必须提供指标值")
                    continue
                if metric["指标类型"] == RATIO:
                    if numerator is None or denominator is None:
                        errors.append(f"第 {index} 条：比率指标「{metric['指标名称']}」必须同时提供分子和分母")
                        continue
                    if denominator < 0:
                        errors.append(f"第 {index} 条：分母不能为负数")
                        continue
                    if value is None and denominator:
                        value = round(numerator / denominator * 100, 6) if metric.get("比率口径") == "百分比" else round(numerator / denominator, 6)
            prepared.append({
                "指标编码": code,
                "业务日期": day,
                "维度值": dims,
                "维度键": _dim_key(dims),
                "指标值": value,
                "分子值": numerator,
                "分母值": denominator,
                "上报状态": report_status,
                "备注": _text(item.get("备注")) or None,
            })
        return prepared, errors

    def import_details(
        self,
        plan_id: int,
        items: list[dict[str, Any]],
        *,
        batch: str | None = None,
        source: str | None = None,
    ) -> tuple[dict[str, Any] | None, str]:
        plan = store.find(MODULE, plan_id)
        if plan is None:
            return None, f"对比方案 {plan_id} 不存在或已归档"
        if plan.get("status") == STATUS_ORDER[-1]:
            return None, "已归档的对比方案不能再导入明细"
        if not isinstance(items, list) or not items:
            return None, "请至少提供 1 条明细"
        prepared, errors = self._prepare_items(plan, items)
        if errors:
            return None, "明细未导入：\n" + "\n".join(errors[:20]) + ("\n…" if len(errors) > 20 else "")
        if not prepared:
            return None, "没有可导入的明细"

        selected = set(plan.get("指标列表") or [])
        outside = 0
        base_start, base_end = parse_period(plan["基准周期"])
        comp_start, comp_end = parse_period(plan["对比周期"])
        with store.lock:
            plan["导入批次计数"] = int(plan.get("导入批次计数") or 0) + 1
            revision = int(plan.get("明细修订号") or 0) + 1
            batch_id = batch or f"IMP-{plan_id}-{plan['导入批次计数']:03d}"
            inserted = corrected = 0
            for item in prepared:
                day = date.fromisoformat(item["业务日期"])
                if not (base_start <= day <= base_end or comp_start <= day <= comp_end):
                    outside += 1
                rows = store.rows(DETAILS)
                existing = next((row for row in rows
                                 if row.get("方案id") == plan_id
                                 and row.get("指标编码") == item["指标编码"]
                                 and row.get("业务日期") == item["业务日期"]
                                 and row.get("维度键") == item["维度键"]), None)
                if existing is None:
                    row = {
                        "id": store.next_id(DETAILS),
                        "方案id": plan_id,
                        "指标编码": item["指标编码"],
                        "业务日期": item["业务日期"],
                        "维度值": item["维度值"],
                        "维度键": item["维度键"],
                        "指标值": item["指标值"],
                        "分子值": item["分子值"],
                        "分母值": item["分母值"],
                        "上报状态": item["上报状态"],
                        "来源批次": batch_id,
                        "来源": source,
                        "录入时间": _now(),
                        "修订号": revision,
                        "修订历史": [],
                        "备注": item["备注"],
                    }
                    rows.append(row)
                    inserted += 1
                else:
                    history = existing.setdefault("修订历史", [])
                    history.append({
                        "修订号": existing.get("修订号"),
                        "来源批次": existing.get("来源批次"),
                        "指标值": existing.get("指标值"),
                        "分子值": existing.get("分子值"),
                        "分母值": existing.get("分母值"),
                        "上报状态": existing.get("上报状态"),
                        "录入时间": existing.get("录入时间"),
                        "本批次": batch_id,
                    })
                    existing.update({
                        "指标值": item["指标值"],
                        "分子值": item["分子值"],
                        "分母值": item["分母值"],
                        "上报状态": item["上报状态"],
                        "来源批次": batch_id,
                        "来源": source,
                        "录入时间": _now(),
                        "修订号": revision,
                        "备注": item["备注"],
                    })
                    corrected += 1
            plan["明细修订号"] = revision
            plan["导入明细数"] = sum(1 for row in store.rows(DETAILS) if row.get("方案id") == plan_id)
            plan["计算状态"] = "待重新计算" if plan.get("当前数据集") else "待计算"
            plan["pending"] = True
            not_selected = sorted({item["指标编码"] for item in prepared if item["指标编码"] not in selected})
        message = (f"批次 {batch_id} 已导入 {len(prepared)} 条：新增 {inserted} 条、更正 {corrected} 条；"
                   f"明细修订号升至 R{revision}")
        if outside:
            message += f"；其中 {outside} 条不在两期范围内，将列入未纳入部分"
        if not_selected:
            message += f"；指标 {'、'.join(not_selected)} 未在方案选中的指标列表中，本次计算不参与"
        if corrected:
            message += "；历史值已写入修订历史，重新计算会生成新版本，已有报告附件仍引用旧版本"
        return {"方案id": plan_id, "批次": batch_id, "修订号": revision,
                "导入条数": len(prepared), "新增": inserted, "更正": corrected,
                "周期外": outside, "未选指标": not_selected}, message

    def list_details(
        self,
        plan_id: int,
        *,
        metric: str | None = None,
        start: str | None = None,
        end: str | None = None,
        status: str | None = None,
        dimension: str | None = None,
        page: int = 1,
        size: int = 50,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = [row for row in store.rows(DETAILS) if row.get("方案id") == plan_id]
        if metric:
            rows = [row for row in rows if row.get("指标编码") == metric]
        if start:
            rows = [row for row in rows if str(row.get("业务日期")) >= start]
        if end:
            rows = [row for row in rows if str(row.get("业务日期")) <= end]
        if status:
            rows = [row for row in rows if row.get("上报状态") == status]
        if dimension:
            rows = [row for row in rows if dimension in str(row.get("维度键"))]
        rows = sorted(rows, key=lambda row: (str(row.get("业务日期")), int(row.get("id", 0))))
        total = len(rows)
        offset = max(page - 1, 0) * size
        return [copy.deepcopy(row) for row in rows[offset:offset + size]], total

    # ---------- 数据集 ----------
    @staticmethod
    def _dataset_brief(dataset: dict[str, Any] | None) -> dict[str, Any] | None:
        if dataset is None:
            return None
        return {
            "数据集id": dataset.get("id"),
            "数据集编号": dataset.get("数据集编号"),
            "版本": dataset.get("版本"),
            "状态": dataset.get("状态"),
            "生成时间": dataset.get("生成时间"),
            "计算批次": dataset.get("计算批次"),
            "明细修订号": dataset.get("明细修订号"),
            "配置版本": (dataset.get("来源") or {}).get("配置版本"),
            "基准周期": (dataset.get("周期") or {}).get("基准周期", {}).get("文本"),
            "对比周期": (dataset.get("周期") or {}).get("对比周期", {}).get("文本"),
            "对齐方式": dataset.get("对齐方式"),
        }

    def list_datasets(self, plan_id: int | None = None) -> list[dict[str, Any]]:
        rows = [row for row in store.rows(DATASETS) if plan_id is None or row.get("方案id") == plan_id]
        rows = sorted(rows, key=lambda row: (int(row.get("方案id", 0)), int(row.get("版本", 0))))
        return [self._dataset_brief(row) or {} for row in rows]

    def get_dataset(self, dataset_id: int) -> dict[str, Any] | None:
        dataset = store.find(DATASETS, dataset_id)
        return copy.deepcopy(dataset) if dataset is not None else None

    def list_runs(self, plan_id: int | None = None) -> list[dict[str, Any]]:
        rows = [row for row in store.rows(RUNS) if plan_id is None or row.get("方案id") == plan_id]
        return [copy.deepcopy(row) for row in sorted(rows, key=lambda row: int(row.get("id", 0)), reverse=True)]

    def trace(self, dataset_id: int, metric_code: str, dimension_key: str | None = None) -> tuple[dict[str, Any] | None, str]:
        dataset = store.find(DATASETS, dataset_id)
        if dataset is None:
            return None, f"对比数据集 {dataset_id} 不存在"
        metric = next((item for item in dataset.get("指标", []) if item.get("指标编码") == metric_code), None)
        if metric is None:
            return None, f"数据集 {dataset_id} 中没有指标「{metric_code}」"
        cells = metric.get("维度明细", [])
        if dimension_key:
            cells = [cell for cell in cells if _dim_key(cell.get("维度值") or {}) == dimension_key]
            if not cells:
                return None, f"数据集 {dataset_id} 中没有维度「{dimension_key}」"
        by_id = {int(row["id"]): row for row in store.rows(DETAILS)}
        dataset_revision = int(dataset.get("明细修订号") or 0)
        traces: list[dict[str, Any]] = []
        stale: list[int] = []
        for cell in cells:
            ids: list[int] = []
            for value in (cell.get("来源核对") or {}).values():
                if isinstance(value, list):
                    ids.extend(int(item) for item in value)
            details = []
            for detail_id in sorted(set(ids)):
                row = by_id.get(detail_id)
                if row is None:
                    continue
                state = self._detail_as_of(row, dataset_revision)
                if state is None:
                    continue
                restored = (
                    state["指标值"] != row.get("指标值")
                    or state["分子值"] != row.get("分子值")
                    or state["分母值"] != row.get("分母值")
                    or state["上报状态"] != row.get("上报状态")
                )
                if restored:
                    stale.append(detail_id)
                details.append({
                    "id": row["id"], "业务日期": row["业务日期"], "指标编码": row["指标编码"],
                    "维度值": row["维度值"], "指标值": state["指标值"],
                    "分子值": state["分子值"], "分母值": state["分母值"],
                    "上报状态": state["上报状态"], "来源批次": state.get("来源批次"),
                    "修订号": dataset_revision,
                    "当前值": {
                        "指标值": row["指标值"], "分子值": row["分子值"], "分母值": row["分母值"],
                        "上报状态": row["上报状态"], "来源批次": row.get("来源批次"),
                        "修订号": row.get("修订号"),
                    },
                    "已按数据集版本还原": restored,
                })
            traces.append({"维度标签": cell["维度标签"], "维度值": cell["维度值"],
                           "状态": cell["状态"], "比较结果": cell["比较结果"],
                           "基准期": cell["基准期"], "对比期": cell["对比期"],
                           "覆盖": cell["覆盖"], "未纳入部分": cell["未纳入部分"],
                           "来源核对": cell.get("来源核对"),
                           "原始明细": details})
        return {
            "数据集id": dataset_id,
            "数据集编号": dataset.get("数据集编号"),
            "版本": dataset.get("版本"),
            "计算批次": dataset.get("计算批次"),
            "指标": metric_code,
            "指标汇总": {key: metric.get(key) for key in
                     ("指标名称", "指标类型", "单位", "状态", "比较结果", "可比汇总", "覆盖说明", "可比天数")},
            "维度核对": traces,
            "明细还原说明": (
                "以下明细已按数据集生成时的修订号 R" f"{dataset_revision} 还原；"
                "如后续发生补报/更正，当前值单独列在「当前值」中，本数据集仍按原值核对"
            ) if stale else "以下明细与当前明细一致，无需还原",
            "存在后续修订的明细ids": sorted(set(stale)),
        }, ""

    @staticmethod
    def _detail_as_of(row: dict[str, Any], revision: int) -> dict[str, Any] | None:
        """把明细还原到指定修订号：从修订历史里找覆盖目标版本的那一版快照。"""
        current_revision = int(row.get("修订号") or 0)
        if current_revision <= revision:
            return {
                "指标值": row.get("指标值"), "分子值": row.get("分子值"),
                "分母值": row.get("分母值"), "上报状态": row.get("上报状态"),
                "来源批次": row.get("来源批次"),
            }
        # 每次更正都会把「被覆盖的旧值」压入修订历史，并按时间顺序追加。
        # 从后往前找第一条记录版本 <= 目标版本的快照，即目标版本当时的值。
        for entry in reversed(row.get("修订历史") or []):
            if int(entry.get("修订号") or 0) <= revision:
                return {
                    "指标值": entry.get("指标值"), "分子值": entry.get("分子值"),
                    "分母值": entry.get("分母值"), "上报状态": entry.get("上报状态"),
                    "来源批次": entry.get("来源批次"),
                }
        return None

    # ---------- 计算 ----------
    def _record_run(self, plan_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        with store.lock:
            run = {"id": store.next_id(RUNS), "方案id": plan_id, "开始时间": _now(), **payload}
            store.rows(RUNS).append(run)
            return run

    def _finish_run(self, run: dict[str, Any], *, status: str, message: str,
                    dataset_id: int | None = None) -> None:
        with store.lock:
            run.update({"状态": status, "消息": message, "结束时间": _now(),
                        "数据集id": dataset_id, "数据集版本": run.get("数据集版本")})

    def calculate(
        self,
        plan_id: int,
        *,
        alignment: str | None = None,
        failpoint: str | None = None,
        operator: str | None = None,
    ) -> tuple[dict[str, Any] | None, str]:
        """计算一次完整对比并发布新版本。

        failpoint 仅用于测试中断与重试，HTTP 接口不会透传。
        """
        plan = store.find(MODULE, plan_id)
        if plan is None:
            return None, f"对比方案 {plan_id} 不存在或已归档"
        if plan.get("status") == STATUS_ORDER[-1]:
            return None, "已归档的对比方案不能再计算"
        target_alignment = alignment or plan.get("对齐方式") or ALIGN_BY_DAY
        if target_alignment not in ALIGNMENT_OPTIONS:
            return None, f"对齐方式「{target_alignment}」不在支持范围：{'、'.join(ALIGNMENT_OPTIONS)}"
        try:
            metrics = [METRIC_BY_CODE[code] for code in plan.get("指标列表", []) if code in METRIC_BY_CODE]
        except KeyError:
            return None, "对比方案的指标列表已失效，请重新选择指标"
        if not metrics:
            return None, "对比方案没有选择任何指标，无法计算"

        with store.lock:
            snapshot = copy.deepcopy([row for row in store.rows(DETAILS) if row.get("方案id") == plan_id])
            revision = int(plan.get("明细修订号") or 0)
            config_revision = int(plan.get("配置版本") or 1)
            plan_snapshot = copy.deepcopy(plan)
            existing_versions = [int(row.get("版本", 0)) for row in store.rows(DATASETS)
                                 if row.get("方案id") == plan_id]
            version = max(existing_versions, default=0) + 1
        run = self._record_run(plan_id, {
            "对齐方式": target_alignment,
            "明细修订号": revision,
            "配置版本": config_revision,
            "数据集版本": version,
            "操作人": operator,
            "明细条数": len(snapshot),
        })

        generated_at = _now()
        run_id = f"RUN-{run['id']}-{uuid.uuid4().hex[:6]}"
        try:
            dataset = build_dataset(
                plan=plan_snapshot,
                metrics=metrics,
                details=snapshot,
                alignment=target_alignment,
                # 数据集 id 到发布时才分配：失败的计算不占用版本 id。
                dataset_id=0,
                version=version,
                run_id=run_id,
                generated_at=generated_at,
                revision=revision,
                failpoint=failpoint,
            )
        except (ValueError, PeriodError, RuntimeError) as error:
            self._finish_run(run, status="失败", message=str(error))
            with store.lock:
                plan["计算状态"] = "计算失败"
                plan["pending"] = False
                plan["abnormal"] = True
            current = store.find(DATASETS, int(plan.get("当前数据集") or 0))
            hint = (f"当前仍使用 {current['数据集编号']}" if current else "尚无可用数据集")
            return None, f"计算中断：{error}；本次未发布任何数据集，{hint}。修正后可重试"

        dataset["来源"].update({
            "配置版本": config_revision,
            "方案配置": {
                "指标列表": list(plan_snapshot.get("指标列表") or []),
                "维度列表": list(plan_snapshot.get("维度列表") or []),
                "基准周期": plan_snapshot.get("基准周期"),
                "对比周期": plan_snapshot.get("对比周期"),
                "对齐方式": target_alignment,
                "配置版本": config_revision,
            },
            "操作人": operator,
        })

        with store.lock:
            fresh = store.find(MODULE, plan_id)
            if fresh is None:
                self._finish_run(run, status="作废", message="方案在计算期间被删除")
                return None, "方案在计算期间被删除，本次结果作废"
            if int(fresh.get("明细修订号") or 0) != revision or int(fresh.get("配置版本") or 1) != config_revision:
                self._finish_run(run, status="作废",
                                 message=f"计算期间明细或配置发生变化（R{fresh.get('明细修订号')}/"
                                         f"配置V{fresh.get('配置版本')}），结果作废")
                fresh["计算状态"] = "待重新计算"
                return None, ("计算期间明细或方案配置发生变化，为避免拼接新旧两侧结果，本次计算作废；"
                              "请基于最新明细重试")
            # 发布：全量数据集一次性入表，随后才切换方案指针。
            dataset_id = store.next_id(DATASETS)
            dataset["id"] = dataset_id
            dataset["数据集编号"] = f"CMPDS-{plan_id}-V{version}"
            dataset["来源"]["数据集id"] = dataset_id
            store.rows(DATASETS).append(dataset)
            fresh.update({
                "当前数据集": dataset_id,
                "当前版本": version,
                "最近计算时间": generated_at,
                "计算状态": "已完成",
                "pending": False,
                "abnormal": False,
                "对齐方式": target_alignment,
            })
            primary = dataset["指标"][0]
            change = primary.get("比较结果") or {}
            fresh["变化幅度"] = self._format_change(primary)
            fresh["变化方向"] = change.get("方向") or change.get("状态")
            if fresh.get("status") == STATUS_ORDER[0]:
                fresh["status"] = STATUS_ORDER[1]
                fresh["方案状态"] = STATUS_ORDER[1]
            self._finish_run(run, status="成功", message="对比数据集已发布", dataset_id=dataset_id)
        return dataset, f"对比计算完成，已发布 {dataset['数据集编号']}（明细修订号 R{revision}）"

    @staticmethod
    def _format_change(metric: dict[str, Any]) -> str | None:
        change = metric.get("比较结果") or {}
        if change.get("状态") != "正常变化":
            return change.get("状态")
        if change.get("变化率") is None:
            return change.get("状态")
        return f"{change.get('变化率')}%"

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"对比方案 {entry_id} 不存在或已归档"
        if action == "重新计算":
            dataset, message = self.calculate(entry_id)
            if dataset is None:
                return None, message
            return store.find(MODULE, entry_id), message
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于同期对比分析可执行范围"
        if action == "执行对比":
            if entry.get("当前数据集"):
                return None, ("该方案已有对比数据集；如需按最新明细重算，请使用「重新计算」。"
                              "重新计算会生成新版本，已有报告附件仍引用原版本")
            dataset, message = self.calculate(entry_id)
            if dataset is None:
                return None, message
            return store.find(MODULE, entry_id), message
        target = ACTION_RULES[action]
        if action == "原因分析" and not entry.get("当前数据集"):
            return None, "尚未生成对比数据集，不能进入原因分析"
        with store.lock:
            entry["status"] = target
            entry["方案状态"] = target
            entry["pending"] = target != STATUS_ORDER[-1]
            entry["abnormal"] = False
        return entry, f"对比方案已{action}"
