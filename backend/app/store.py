"""内存数据仓库：给每个业务模块准备一份可筛选、可流转的示例数据。

真实项目里这里会换成数据库访问层；当前实现只依赖标准库，保证克隆下来就能起。
同期对比的指标目录、方案、明细与历史数据集单独放在 seed_compare / compare_store 里，
因为它们需要版本化与原子发布，不能按普通业务表随意改写。
"""
from __future__ import annotations

import threading
from typing import Any

from app.seed import SEED_ROWS
from app.seed_compare import DETAILS, METRICS, PLANS


class Store:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._tables: dict[str, list[dict[str, Any]]] = {
            name: [dict(row) for row in rows] for name, rows in SEED_ROWS.items()
        }
        self._tables["compare"] = [dict(row) for row in PLANS]
        self._tables["compare_metrics"] = [dict(row) for row in METRICS]
        self._tables["compare_details"] = [dict(row) for row in DETAILS]
        self._tables["compare_datasets"] = []
        self._tables["compare_runs"] = []
        self._tables["report_attachments"] = []
        self._sequence: dict[str, int] = {
            "compare": max((int(row.get("id", 0)) for row in self._tables["compare"]), default=0),
            "compare_details": max((int(row.get("id", 0)) for row in self._tables["compare_details"]), default=0),
            "compare_datasets": 0,
            "compare_runs": 0,
            "report_attachments": 0,
        }

    @property
    def lock(self) -> threading.RLock:
        """对比计算与附件生成都跨多张表，调用方用同一把锁保证原子发布。"""
        return self._lock

    def module_names(self) -> list[str]:
        """对外展示的业务模块；内部支撑表（明细、版本、附件）不进入概览。"""
        internal = {"compare_metrics", "compare_details", "compare_datasets", "compare_runs",
                    "report_attachments"}
        return sorted(name for name in self._tables if name not in internal)

    def rows(self, module: str) -> list[dict[str, Any]]:
        return self._tables.setdefault(module, [])

    def find(self, module: str, entry_id: int) -> dict[str, Any] | None:
        for row in self.rows(module):
            if int(row.get("id", 0)) == entry_id:
                return row
        return None

    def next_id(self, module: str) -> int:
        with self._lock:
            rows = self.rows(module)
            current = self._sequence.get(module)
            if current is None:
                current = max((int(row.get("id", 0)) for row in rows), default=0)
            current += 1
            self._sequence[module] = current
            return current

    def overview(self) -> dict[str, object]:
        modules: list[dict[str, object]] = []
        for name in self.module_names():
            rows = self.rows(name)
            modules.append({
                "name": name,
                "created": len(rows),
                "pending": sum(1 for row in rows if row.get("pending")),
                "abnormal": sum(1 for row in rows if row.get("abnormal")),
            })
        cards = [
            {"label": "业务模块", "value": len(modules)},
            {"label": "今日新增", "value": sum(int(item["created"]) for item in modules)},
            {"label": "待处理", "value": sum(int(item["pending"]) for item in modules)},
            {"label": "异常量", "value": sum(int(item["abnormal"]) for item in modules)},
        ]
        return {"cards": cards, "modules": modules}


store = Store()
