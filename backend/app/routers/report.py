"""经营分析报告接口：维护分析报告，覆盖生成报告、分发报告、归档报告与数据附件生成。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult, ReportAttachmentPayload
from app.services.report import ReportService

router = APIRouter(prefix="/api/report", tags=["经营分析报告"])

service = ReportService()

LIST_FIELDS = ["报告编号", "报告主题", "分析周期", "报告模板", "关键发现", "数据附件", "分发对象", "报告状态"]
STATUSES = ["草稿", "已生成", "已分发", "已归档"]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按报告编号检索"),
    status: str | None = Query(default=None, description="草稿、已生成、已分发、已归档"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按报告编号与状态过滤经营分析报告列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/attachments/{attachment_id}", response_model=dict)
def get_attachment(attachment_id: int) -> dict:
    """读取一份数据附件快照：周期、覆盖范围、汇总分子分母、来源版本与冻结的数据集。"""
    attachment = service.get_attachment(attachment_id)
    if attachment is None:
        raise HTTPException(status_code=404, detail=f"数据附件 {attachment_id} 不存在")
    return attachment


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出经营分析报告清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "report", "total": total, "items": items}


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条分析报告，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="分析报告已登记", entry=entry)


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条分析报告明细与已生成的数据附件。"""
    entry = service.get_entry_detail(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"分析报告 {entry_id} 不存在或已归档")
    return entry


@router.get("/{entry_id}/attachments", response_model=dict)
def list_attachments(entry_id: int) -> dict[str, Any]:
    """报告已生成的数据附件清单。"""
    if service.get_entry(entry_id) is None:
        raise HTTPException(status_code=404, detail=f"分析报告 {entry_id} 不存在或已归档")
    items = service.list_attachments(entry_id)
    return {"items": items, "total": len(items)}


@router.post("/{entry_id}/attachments", response_model=ActionResult)
def create_attachment(entry_id: int, payload: ReportAttachmentPayload) -> ActionResult:
    """从同期对比差异数据集生成数据附件；附件冻结当时的周期、覆盖与来源版本。"""
    attachment, message = service.attach_dataset(
        entry_id, payload.dataset_id, operator=payload.operator, note=payload.note,
    )
    if attachment is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry={
        "附件id": attachment["id"],
        "数据集编号": attachment["数据集编号"],
        "数据集版本": attachment["数据集版本"],
        "摘要指纹": attachment["摘要指纹"],
    })


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条分析报告执行生成报告、分发报告、归档报告；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
