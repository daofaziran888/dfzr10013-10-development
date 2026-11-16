"""经营分析报告业务规则：状态流转、字段校验、筛选口径与数据附件都在这里。

数据附件来自同期对比数据集；生成附件时把数据集内容连同周期、覆盖范围、
汇总分子分母和来源版本一起冻结成快照。之后无论明细补报还是方案重算，
已生成附件引用的对比依据都不会改变。
"""
from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime
from typing import Any

from app.store import store

MODULE = "report"
ATTACHMENTS = "report_attachments"
REQUIRED_FIELDS = ["报告编号", "报告主题", "分析周期"]
STATUS_ORDER = ["草稿", "已生成", "已分发", "已归档"]
ACTION_RULES = {"生成报告": "草稿", "分发报告": "已生成", "归档报告": "已分发"}
NEGATIVE_ACTIONS = []


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _digest(payload: Any) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _metric_attachment(metric: dict[str, Any]) -> dict[str, Any]:
    """附件里的指标行：保留周期、覆盖范围、汇总分子分母和来源版本。"""
    change = metric.get("比较结果") or {}
    summary = metric.get("可比汇总") or {}
    return {
        "指标编码": metric.get("指标编码"),
        "指标名称": metric.get("指标名称"),
        "指标类型": metric.get("指标类型"),
        "单位": metric.get("单位"),
        "分子名称": metric.get("分子名称"),
        "分母名称": metric.get("分母名称"),
        "状态": metric.get("状态"),
        "变化方向": change.get("方向"),
        "变化率": change.get("变化率"),
        "变化量": change.get("变化量"),
        "变化单位": change.get("变化单位"),
        "变化说明": change.get("说明"),
        "基准值": change.get("基准值", summary.get("基准值")),
        "对比值": change.get("对比值", summary.get("对比值")),
        "基准分子": summary.get("基准分子"),
        "基准分母": summary.get("基准分母"),
        "对比分子": summary.get("对比分子"),
        "对比分母": summary.get("对比分母"),
        "可比天数": metric.get("可比天数"),
        "对齐天数": metric.get("对齐天数"),
        "参与汇总的维度数": metric.get("参与汇总的维度数"),
        "未参与汇总的维度": list(metric.get("未参与汇总的维度") or []),
        "覆盖说明": list(metric.get("覆盖说明") or []),
        "原始范围汇总": copy.deepcopy(metric.get("原始范围汇总") or {}),
    }


def build_attachment(report: dict[str, Any], dataset: dict[str, Any], *, operator: str | None,
                     note: str | None) -> dict[str, Any]:
    period = dataset.get("周期") or {}
    source = dataset.get("来源") or {}
    payload = {
        "report_id": report["id"],
        "report_code": report.get("报告编号"),
        "dataset_id": dataset.get("id"),
        "dataset_code": dataset.get("数据集编号"),
        "version": dataset.get("版本"),
        "run_id": dataset.get("计算批次"),
        "revision": dataset.get("明细修订号"),
        "metrics": [_metric_attachment(metric) for metric in dataset.get("指标", [])],
    }
    return {
        "id": store.next_id(ATTACHMENTS),
        "报告id": report["id"],
        "报告编号": report.get("报告编号"),
        "附件类型": "同期对比差异数据附件",
        "数据集id": dataset.get("id"),
        "数据集编号": dataset.get("数据集编号"),
        "数据集版本": dataset.get("版本"),
        "计算批次": dataset.get("计算批次"),
        "明细修订号": dataset.get("明细修订号"),
        "生成时间": _now(),
        "生成人": operator,
        "备注": note,
        "摘要指纹": _digest(payload),
        "周期": {
            "基准周期": copy.deepcopy(period.get("基准周期") or {}),
            "对比周期": copy.deepcopy(period.get("对比周期") or {}),
            "对齐": copy.deepcopy(period.get("对齐") or {}),
        },
        "覆盖": copy.deepcopy(period.get("覆盖") or {}),
        "对齐方式": dataset.get("对齐方式"),
        "覆盖说明": list(dataset.get("覆盖说明") or []),
        "指标附件": payload["metrics"],
        "来源版本": {
            "数据集id": dataset.get("id"),
            "数据集编号": dataset.get("数据集编号"),
            "版本": dataset.get("版本"),
            "计算批次": dataset.get("计算批次"),
            "明细修订号": dataset.get("明细修订号"),
            "配置版本": source.get("配置版本"),
            "方案id": dataset.get("方案id"),
            "方案编号": dataset.get("方案编号"),
            "方案配置": copy.deepcopy(source.get("方案配置") or {}),
        },
        # 冻结数据集快照：即使后续明细补报、方案重算，附件仍可按当时的依据复核。
        "数据集快照": copy.deepcopy(dataset),
    }


class ReportService:
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
            rows = [row for row in rows if keyword in str(row.get("报告编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def get_entry_detail(self, entry_id: int) -> dict[str, Any] | None:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None
        detail = copy.deepcopy(entry)
        detail["数据附件清单"] = self.list_attachments(entry_id)
        return detail

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def list_attachments(self, report_id: int) -> list[dict[str, Any]]:
        return [
            {
                "id": row["id"],
                "附件类型": row.get("附件类型"),
                "数据集id": row.get("数据集id"),
                "数据集编号": row.get("数据集编号"),
                "数据集版本": row.get("数据集版本"),
                "计算批次": row.get("计算批次"),
                "明细修订号": row.get("明细修订号"),
                "生成时间": row.get("生成时间"),
                "摘要指纹": row.get("摘要指纹"),
                "周期": row.get("周期"),
                "指标": [
                    {key: item.get(key) for key in
                     ("指标编码", "指标名称", "状态", "变化方向", "变化率", "变化量",
                      "变化单位", "基准值", "对比值", "基准分子", "基准分母",
                      "对比分子", "对比分母")}
                    for item in row.get("指标附件", [])
                ],
            }
            for row in store.rows(ATTACHMENTS) if row.get("报告id") == report_id
        ]

    def get_attachment(self, attachment_id: int) -> dict[str, Any] | None:
        row = store.find(ATTACHMENTS, attachment_id)
        return copy.deepcopy(row) if row is not None else None

    def attach_dataset(self, report_id: int, dataset_id: int, *,
                       operator: str | None = None, note: str | None = None
                       ) -> tuple[dict[str, Any] | None, str]:
        report = store.find(MODULE, report_id)
        if report is None:
            return None, f"分析报告 {report_id} 不存在或已归档"
        if report.get("status") == STATUS_ORDER[-1]:
            return None, "已归档的分析报告不能再添加数据附件"
        dataset = store.find("compare_datasets", dataset_id)
        if dataset is None:
            return None, f"同期对比数据集 {dataset_id} 不存在"
        if dataset.get("状态") != "已完成":
            return None, f"对比数据集 {dataset_id} 尚未完成，不能生成附件"
        with store.lock:
            attachment = build_attachment(report, dataset, operator=operator, note=note)
            store.rows(ATTACHMENTS).append(attachment)
            count = sum(1 for row in store.rows(ATTACHMENTS) if row.get("报告id") == report_id)
            report["数据附件"] = f"{dataset.get('数据集编号')} 同期对比差异附件（共 {count} 份）"
            primary = (dataset.get("指标") or [{}])[0]
            change = primary.get("比较结果") or {}
            report["关键发现"] = (
                f"{primary.get('指标名称', '指标')}：{change.get('状态')}"
                + (f"，变化 {change.get('变化率')}%" if change.get("变化率") is not None else "")
            )
        return attachment, (f"已生成数据附件：{dataset.get('数据集编号')} V{dataset.get('版本')}，"
                            f"明细修订号 R{dataset.get('明细修订号')}；后续重算不会改变本附件")

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"分析报告 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于经营分析报告可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"分析报告已{action}"
