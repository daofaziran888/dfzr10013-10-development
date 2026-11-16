"""同期对比分析接口：方案配置、明细导入、周期对比计算、历史版本与可核对明细。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, CompareCalculatePayload, CompareImportPayload, EntryPayload, PageResult
from app.services.compare import REQUIRED_FIELDS, CompareService

router = APIRouter(prefix="/api/compare", tags=["同期对比分析"])

service = CompareService()

LIST_FIELDS = ["方案编号", "对比指标", "基准周期", "对比周期", "变化幅度", "变化方向", "原因分析", "方案状态"]
STATUSES = ["草稿", "已对比", "已分析", "已归档"]
ACTION_RULES = ["执行对比", "重新计算", "原因分析", "归档方案"]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按方案编号检索"),
    status: str | None = Query(default=None, description="草稿、已对比、已分析、已归档"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按方案编号与状态过滤同期对比分析列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/metrics", response_model=dict)
def list_metrics() -> dict[str, Any]:
    """指标目录：列出可用指标及其可加/比率类型与分子分母定义。"""
    return {"items": service.list_metrics(), "total": len(service.list_metrics())}


@router.get("/datasets", response_model=dict)
def list_datasets(plan_id: int | None = Query(default=None, description="按方案过滤")) -> dict[str, Any]:
    """历史对比数据集版本清单；已发布版本不可修改。"""
    items = service.list_datasets(plan_id=plan_id)
    return {"items": items, "total": len(items)}


@router.get("/datasets/{dataset_id}", response_model=dict)
def get_dataset(dataset_id: int) -> dict:
    """读取一份完整对比数据集：两期统计范围、覆盖、参与比较范围与未纳入部分。"""
    dataset = service.get_dataset(dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail=f"对比数据集 {dataset_id} 不存在")
    return dataset


@router.get("/datasets/{dataset_id}/trace", response_model=dict)
def trace_dataset(
    dataset_id: int,
    metric: str = Query(description="指标编码"),
    dimension: str | None = Query(default=None, description="维度键，例如 区域=华东"),
) -> dict:
    """按指标与维度回溯某个对比值：两侧统计范围、覆盖情况与原始明细。"""
    result, message = service.trace(dataset_id, metric, dimension)
    if result is None:
        raise HTTPException(status_code=404, detail=message or f"对比数据集 {dataset_id} 不可核对")
    return result


@router.get("/runs", response_model=dict)
def list_runs(plan_id: int | None = Query(default=None, description="按方案过滤")) -> dict[str, Any]:
    """计算批次记录：成功、失败与作废都会留痕，便于确认重试是否基于完整结果。"""
    items = service.list_runs(plan_id=plan_id)
    return {"items": items, "total": len(items)}


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出同期对比分析清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "compare", "total": total, "items": items}


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条对比方案，缺字段或指标周期不合法时说明原因而不是静默丢弃。"""
    entry, problems = service.create_entry(payload.values)
    if entry is None:
        if all(problem in REQUIRED_FIELDS for problem in problems):
            return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(problems)}")
        return ActionResult(ok=False, message="；".join(problems))
    return ActionResult(ok=True, message="对比方案已登记", entry=entry)


@router.put("/{entry_id}", response_model=ActionResult)
def update_entry(entry_id: int, payload: EntryPayload) -> ActionResult:
    """修改方案的指标、维度、周期或对齐方式；已发布的历史数据集不受影响。"""
    entry, message = service.update_entry(entry_id, payload.values)
    return ActionResult(ok=entry is not None, message=message, entry=entry)


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条对比方案明细，含当前数据集摘要与版本历史。"""
    entry = service.get_plan_detail(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"对比方案 {entry_id} 不存在或已归档")
    return entry


@router.get("/{entry_id}/details", response_model=dict)
def list_details(
    entry_id: int,
    metric: str | None = Query(default=None, description="指标编码"),
    start: str | None = Query(default=None, description="业务日期起，YYYY-MM-DD"),
    end: str | None = Query(default=None, description="业务日期止，YYYY-MM-DD"),
    report_status: str | None = Query(default=None, alias="status", description="已上报、未上报"),
    dimension: str | None = Query(default=None, description="维度键包含的文本，例如 区域=华东"),
    page: int = 1,
    size: int = 50,
) -> dict[str, Any]:
    """查看方案明细：业务日期、维度值、指标值、分子分母与上报状态。"""
    if size > 500:
        raise HTTPException(status_code=400, detail="每页最多 500 条，请缩小分页范围")
    if service.get_entry(entry_id) is None:
        raise HTTPException(status_code=404, detail=f"对比方案 {entry_id} 不存在或已归档")
    items, total = service.list_details(entry_id, metric=metric, start=start, end=end,
                                        status=report_status, dimension=dimension,
                                        page=page, size=size)
    return {"items": items, "total": total, "page": page, "size": size}


@router.post("/{entry_id}/details", response_model=ActionResult)
def import_details(entry_id: int, payload: CompareImportPayload) -> ActionResult:
    """导入明细：整体校验通过才写入，补报/更正会抬高明细修订号。"""
    items = payload.items or (payload.values.get("items") if payload.values else None) or []
    if not isinstance(items, list):
        return ActionResult(ok=False, message="明细应为数组")
    result, message = service.import_details(entry_id, items, batch=payload.batch, source=payload.source)
    return ActionResult(ok=result is not None, message=message, entry=result)


@router.post("/{entry_id}/calculate", response_model=ActionResult)
def calculate(entry_id: int, payload: CompareCalculatePayload | None = None) -> ActionResult:
    """按明细执行周期对齐与差异计算，成功后发布一份不可变的数据集版本。"""
    data = payload or CompareCalculatePayload()
    dataset, message = service.calculate(entry_id, alignment=data.alignment, operator=data.operator)
    if dataset is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry={
        "数据集id": dataset.get("id"),
        "数据集编号": dataset.get("数据集编号"),
        "版本": dataset.get("版本"),
        "状态": dataset.get("状态"),
        "指标": [{"指标编码": item.get("指标编码"), "状态": item.get("状态"),
                  "比较结果": item.get("比较结果")} for item in dataset.get("指标", [])],
        "覆盖说明": dataset.get("覆盖说明"),
    })


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """执行对比、重新计算、原因分析、归档方案；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
