"""同期对比与报告附件的回归测试。

运行：cd backend && .venv/bin/python -m unittest discover -s tests -v
覆盖：长短月份、缺报与真实零值、单侧新增/消失维度、不同分母的比率、
计算中断与重试、补报后重算与附件冻结。
"""
from __future__ import annotations

import copy
import unittest

from fastapi.testclient import TestClient

from app.main import app
from app.services import compare as compare_service
from app.services.compare import CompareService
from app.services.report import ReportService
from app.store import store

ORIGINAL_TABLES = copy.deepcopy(store._tables)
ORIGINAL_SEQUENCE = dict(store._sequence)


def reset_store() -> None:
    store._tables = copy.deepcopy(ORIGINAL_TABLES)
    store._sequence = dict(ORIGINAL_SEQUENCE)


def metric_of(plan_id: int, alignment: str | None = None):
    service = CompareService()
    dataset, message = service.calculate(plan_id, alignment=alignment)
    assert dataset is not None, message
    return dataset


def cell(dataset, code: str, label: str):
    metric = next(item for item in dataset["指标"] if item["指标编码"] == code)
    return next(item for item in metric["维度明细"] if item["维度标签"] == label)


def metric(dataset, code: str):
    return next(item for item in dataset["指标"] if item["指标编码"] == code)


class CompareEngineTest(unittest.TestCase):
    def setUp(self) -> None:
        reset_store()
        self.service = CompareService()

    def test_long_short_month_alignment_keeps_raw_ranges(self) -> None:
        """2 月 28 天对 3 月 31 天：对齐 28 天，多出的 3 天进入未纳入部分。"""
        dataset = metric_of(1)
        aligned = dataset["周期"]["对齐"]
        self.assertEqual(aligned["对齐天数"], 28)
        self.assertEqual(aligned["基准比较窗口"]["天数"], 28)
        self.assertEqual(aligned["对比比较窗口"]["天数"], 28)
        self.assertEqual(aligned["对比期未纳入日期"], ["2026-03-29", "2026-03-30", "2026-03-31"])
        # 原始统计范围仍保留整期天数，不能只留下对齐后的范围。
        self.assertEqual(dataset["周期"]["基准周期"]["自然日数"], 28)
        self.assertEqual(dataset["周期"]["对比周期"]["自然日数"], 31)
        self.assertTrue(any("28 天" in line and "31 天" in line for line in dataset["覆盖说明"]))
        # 周期长度不同不能被解释成经营变化：说明中明确写了对齐口径。
        tail_ids = {item["id"] for item in self.service.list_details(1, start="2026-03-29", end="2026-03-31", size=500)[0]}
        self.assertTrue(tail_ids)
        self.assertEqual(set(dataset["未纳入部分"]["对比期窗口外明细ids"]), tail_ids)
        # 窗口外明细不进入任何一期数值。
        east = cell(dataset, "SALES_AMOUNT", "区域=华东")
        self.assertEqual(east["对比期"]["统计范围"]["结束"], "2026-03-28")

    def test_whole_period_mode_refuses_additive_rate(self) -> None:
        """整期口径下周期天数不同，可加指标不计算变化率。"""
        dataset = metric_of(1, alignment="按整期")
        result = metric(dataset, "SALES_AMOUNT")["比较结果"]
        self.assertEqual(result["状态"], "周期天数不一致")
        self.assertIsNone(result["变化率"])
        # 原始汇总仍然给出，供人工核对。
        self.assertGreater(metric(dataset, "SALES_AMOUNT")["可比汇总"]["对比值"], 0)

    def test_missing_is_not_zero_and_zero_is_reported(self) -> None:
        """缺报、真实零值、两期都为零分别表达。"""
        dataset = metric_of(1)
        east = cell(dataset, "SALES_AMOUNT", "区域=华东")
        # 3-20 华东未上报：不进入参与比较的天数，也不按 0 累计。
        self.assertEqual(east["覆盖"]["可比天数"], 27)
        self.assertIn("2026-03-20", east["未纳入部分"]["对比期未上报日期"])
        self.assertEqual(east["对比期"]["未上报条数"], 1)
        # 未上报明细 ID 与参与比较 ID 不重叠。
        self.assertFalse(set(east["来源核对"]["参与比较对比期明细ids"])
                         & set(east["来源核对"]["对比期未上报明细ids"]))

        northwest = cell(dataset, "SALES_AMOUNT", "区域=西北")
        self.assertEqual(northwest["状态"], "基准为零")
        self.assertIsNone(northwest["比较结果"]["变化率"])
        self.assertGreater(northwest["比较结果"]["变化量"], 0)
        self.assertEqual(northwest["基准期"]["参与比较数值"], 0)

        northeast = cell(dataset, "SALES_AMOUNT", "区域=东北")
        self.assertEqual(northeast["状态"], "两期均为零")
        self.assertEqual(northeast["比较结果"]["变化量"], 0)
        self.assertIsNone(northeast["比较结果"]["变化率"])

        # 华中两期都有该维度，但对比期整期未上报：属于缺报不可比，不能当成对比期归零。
        central = cell(dataset, "SALES_AMOUNT", "区域=华中")
        self.assertEqual(central["状态"], "缺报不可比")
        self.assertEqual(central["覆盖"]["可比天数"], 0)
        self.assertEqual(central["对比期"]["参与比较明细条数"], 0)
        # 基准期原始统计范围保留在案，但不能把基期数值当成可比值。
        self.assertGreater(central["基准期"]["原始范围数值"], 0)
        self.assertEqual(central["基准期"]["参与比较明细条数"], 0)
        self.assertEqual(central["基准期"]["已上报条数"], 28)
        self.assertIn("对比期未上报", central["比较结果"]["说明"])
        self.assertIn("区域=华中", dataset["未纳入部分"]["缺报不可比维度"])
        self.assertIn("区域=华中", metric(dataset, "SALES_AMOUNT")["未参与汇总的维度"])

    def test_single_sided_dimensions_excluded_from_rollup(self) -> None:
        """只在单侧出现的维度分别标为新增/消失，且不并入可比汇总。"""
        dataset = metric_of(1)
        self.assertEqual(cell(dataset, "SALES_AMOUNT", "区域=华南")["状态"], "消失维度")
        self.assertEqual(cell(dataset, "SALES_AMOUNT", "区域=西南")["状态"], "新增维度")
        summary = metric(dataset, "SALES_AMOUNT")
        self.assertIn("区域=华南", summary["未参与汇总的维度"])
        self.assertIn("区域=西南", summary["未参与汇总的维度"])
        # 可比汇总只等于参与比较的维度之和。
        expected = sum(item["对比期"]["参与比较数值"] or 0 for item in summary["维度明细"]
                       if item["状态"] not in ("新增维度", "消失维度", "无数据"))
        self.assertAlmostEqual(summary["可比汇总"]["对比值"], expected, places=4)
        self.assertTrue(any("新增维度" in line for line in dataset["覆盖说明"]))
        self.assertTrue(any("消失维度" in line for line in dataset["覆盖说明"]))

    def test_ratio_recomputed_from_sums_not_average(self) -> None:
        """比率指标汇总分子分母后重算，不能等于明细百分比的平均。"""
        dataset = metric_of(2)
        summary = metric(dataset, "CONVERSION_RATE")
        base_num = summary["可比汇总"]["基准分子"]
        base_den = summary["可比汇总"]["基准分母"]
        comp_num = summary["可比汇总"]["对比分子"]
        comp_den = summary["可比汇总"]["对比分母"]
        self.assertAlmostEqual(summary["比较结果"]["基准值"], base_num / base_den * 100, places=4)
        self.assertAlmostEqual(summary["比较结果"]["对比值"], comp_num / comp_den * 100, places=4)
        self.assertEqual(summary["比较结果"]["变化单位"], "百分点")

        # 构造分母差异悬殊的两期数据：朴素平均百分比会得到明显不同的结果。
        from app.services.compare_engine import build_dataset
        from app.seed_compare import METRIC_BY_CODE
        plan = {"id": 99, "方案编号": "COMP-RATIO", "方案名称": "比率核对",
                "基准周期": "2026-01-01~2026-01-01", "对比周期": "2026-02-01~2026-02-01",
                "维度列表": ["渠道"]}
        details = [
            {"id": 1, "指标编码": "CONVERSION_RATE", "业务日期": "2026-01-01",
             "维度值": {"渠道": "大流量"}, "指标值": 1.0, "分子值": 100, "分母值": 10000,
             "上报状态": "已上报", "维度键": "渠道=大流量"},
            {"id": 2, "指标编码": "CONVERSION_RATE", "业务日期": "2026-01-01",
             "维度值": {"渠道": "小流量"}, "指标值": 50.0, "分子值": 50, "分母值": 100,
             "上报状态": "已上报", "维度键": "渠道=小流量"},
            {"id": 3, "指标编码": "CONVERSION_RATE", "业务日期": "2026-02-01",
             "维度值": {"渠道": "大流量"}, "指标值": 1.0, "分子值": 100, "分母值": 10000,
             "上报状态": "已上报", "维度键": "渠道=大流量"},
            {"id": 4, "指标编码": "CONVERSION_RATE", "业务日期": "2026-02-01",
             "维度值": {"渠道": "小流量"}, "指标值": 50.0, "分子值": 50, "分母值": 100,
             "上报状态": "已上报", "维度键": "渠道=小流量"},
        ]
        synthetic = build_dataset(plan=plan, metrics=[METRIC_BY_CODE["CONVERSION_RATE"]],
                                  details=details, dataset_id=0, version=1, run_id="R",
                                  generated_at="x", revision=1)
        summary = synthetic["指标"][0]
        pooled = summary["比较结果"]["基准值"]
        self.assertAlmostEqual(pooled, 150 / 10100 * 100, places=6)
        # 朴素平均：两期各维度百分比的平均 = (1% + 50%)/2 = 25.5%，与汇总重算结果显著不同。
        naive = (1.0 + 50.0) / 2
        self.assertAlmostEqual(naive, 25.5)
        self.assertNotAlmostEqual(naive, pooled, places=2)

    def test_dimension_change_does_not_mix_granularity(self) -> None:
        """方案改维度后，旧粒度明细不混入新口径，列为未纳入并提示重导。"""
        result, message = self.service.update_entry(1, {"维度列表": ["区域", "渠道"]})
        self.assertIsNotNone(result, message)
        self.assertEqual(self.service.get_entry(1)["配置版本"], 2)
        dataset = metric_of(1)
        summary = metric(dataset, "SALES_AMOUNT")
        self.assertEqual(summary["状态"], "无数据")
        self.assertTrue(dataset["未纳入部分"]["维度与方案不一致明细ids"])
        self.assertTrue(any("维度组合与方案当前维度" in line for line in dataset["覆盖说明"]))
        # 已归档方案不能再改配置。
        self.service.run_action(1, "原因分析")
        self.service.run_action(1, "归档方案")
        blocked, message = self.service.update_entry(1, {"维度列表": ["区域"]})
        self.assertIsNone(blocked)
        self.assertIn("已归档", message)

    def test_trace_reaches_original_details(self) -> None:
        """每个对比值都能回溯两侧统计范围、覆盖与原始明细。"""
        dataset = metric_of(1)
        result, message = self.service.trace(dataset["id"], "SALES_AMOUNT", "区域=华东")
        self.assertIsNotNone(result, message)
        first = result["维度核对"][0]
        used_ids = {
            int(item["id"]) for item in first["原始明细"]
        }
        expected = set(first["来源核对"]["参与比较基准期明细ids"]) | set(
            first["来源核对"]["参与比较对比期明细ids"])
        self.assertTrue(expected.issubset(used_ids))
        self.assertTrue(all("上报状态" in item for item in first["原始明细"]))
        self.assertEqual(first["覆盖"]["可比天数"], 27)

    def test_trace_restores_values_as_of_published_version(self) -> None:
        """补报后查看旧数据集，原始明细按当时版本还原，不展示被覆盖后的新值。"""
        dataset = metric_of(1)
        target = next(item for item in self.service.trace(dataset["id"], "SALES_AMOUNT", "区域=华东")[0]["维度核对"][0]["原始明细"]
                      if item["业务日期"] == "2026-03-20")
        self.assertIsNone(target["指标值"])
        self.assertEqual(target["上报状态"], "未上报")

        self.service.import_details(1, [{
            "指标编码": "SALES_AMOUNT", "业务日期": "2026-03-20",
            "维度值": {"区域": "华东"}, "指标值": 111, "上报状态": "已上报",
        }])
        result, _ = self.service.trace(dataset["id"], "SALES_AMOUNT", "区域=华东")
        restored = next(item for item in result["维度核对"][0]["原始明细"]
                        if item["业务日期"] == "2026-03-20")
        self.assertTrue(restored["已按数据集版本还原"])
        self.assertIsNone(restored["指标值"])
        self.assertEqual(restored["上报状态"], "未上报")
        self.assertEqual(restored["当前值"]["指标值"], 111.0)
        self.assertIn(restored["id"], result["存在后续修订的明细ids"])
        # 旧版本的其他明细没有被误标成已还原。
        self.assertEqual(len(result["存在后续修订的明细ids"]), 1)

        second = metric_of(1)
        latest, _ = self.service.trace(second["id"], "SALES_AMOUNT", "区域=华东")
        self.assertEqual(latest["存在后续修订的明细ids"], [])
        current = next(item for item in latest["维度核对"][0]["原始明细"]
                       if item["业务日期"] == "2026-03-20")
        self.assertEqual(current["指标值"], 111.0)
        self.assertFalse(current["已按数据集版本还原"])


def summarize_average(values: list[float]) -> float:
    return sum(values) / len(values)


def ratio_dataset(details: list[dict], plan: dict | None = None, metric_code: str = "CONVERSION_RATE"):
    from app.services.compare_engine import build_dataset
    from app.seed_compare import METRIC_BY_CODE
    plan = plan or {"id": 90, "方案编号": "COMP-EDGE", "方案名称": "边界核对",
                    "基准周期": "2026-01-01~2026-01-01",
                    "对比周期": "2026-02-01~2026-02-01", "维度列表": ["渠道"]}
    return build_dataset(plan=plan, metrics=[METRIC_BY_CODE[metric_code]], details=details,
                         dataset_id=0, version=1, run_id="R", generated_at="x", revision=1)


def ratio_row(row_id: int, day: str, numerator, denominator, status: str = "已上报") -> dict:
    return {"id": row_id, "指标编码": "CONVERSION_RATE", "业务日期": day,
            "维度值": {"渠道": "线上"}, "指标值": numerator, "分子值": numerator,
            "分母值": denominator, "上报状态": status, "维度键": "渠道=线上"}


class RatioEdgeCaseTest(unittest.TestCase):
    """比率指标的分母为零、分子为零、分子缺失必须分别表达。"""

    def status(self, details: list[dict]) -> tuple[str, dict]:
        dataset = ratio_dataset(details)
        summary = dataset["指标"][0]
        return summary["状态"], summary["比较结果"]

    def test_both_denominators_zero(self) -> None:
        status, result = self.status([ratio_row(1, "2026-01-01", 0, 0),
                                      ratio_row(2, "2026-02-01", 0, 0)])
        self.assertEqual(status, "两期分母均为零")
        self.assertIsNone(result["变化率"])

    def test_base_denominator_zero(self) -> None:
        status, result = self.status([ratio_row(1, "2026-01-01", 0, 0),
                                      ratio_row(2, "2026-02-01", 5, 100)])
        self.assertEqual(status, "基准分母为零")
        self.assertIsNone(result["变化率"])
        self.assertIsNone(result["基准值"])
        self.assertAlmostEqual(result["对比值"], 5.0)

    def test_compare_denominator_zero(self) -> None:
        status, result = self.status([ratio_row(1, "2026-01-01", 5, 100),
                                      ratio_row(2, "2026-02-01", 0, 0)])
        self.assertEqual(status, "对比分母为零")
        self.assertIsNone(result["变化率"])
        self.assertIsNone(result["对比值"])

    def test_both_numerators_zero(self) -> None:
        status, result = self.status([ratio_row(1, "2026-01-01", 0, 100),
                                      ratio_row(2, "2026-02-01", 0, 120)])
        self.assertEqual(status, "两期均为零")
        self.assertIsNone(result["变化率"])
        self.assertEqual(result["变化量"], 0.0)

    def test_base_numerator_zero_gives_difference_only(self) -> None:
        status, result = self.status([ratio_row(1, "2026-01-01", 0, 100),
                                      ratio_row(2, "2026-02-01", 5, 100)])
        self.assertEqual(status, "基准为零")
        self.assertIsNone(result["变化率"])
        self.assertAlmostEqual(result["变化量"], 5.0)

    def test_missing_numerator_is_not_silently_dropped(self) -> None:
        status, result = self.status([ratio_row(1, "2026-01-01", 5, 100),
                                      ratio_row(2, "2026-02-01", 6, 100),
                                      ratio_row(3, "2026-02-01", None, 100)])
        self.assertEqual(status, "分子分母缺失")
        self.assertIsNone(result["变化率"])
        self.assertIn("分子或分母", result["说明"])

    def test_unreported_ratio_row_does_not_trigger_division(self) -> None:
        status, _ = self.status([ratio_row(1, "2026-01-01", 5, 100),
                                 ratio_row(2, "2026-02-01", 6, 100),
                                 ratio_row(3, "2026-02-01", None, None, "未上报")])
        self.assertEqual(status, "正常变化")


class AtomicCalculationTest(unittest.TestCase):
    def setUp(self) -> None:
        reset_store()
        self.service = CompareService()

    def test_interrupted_calculation_publishes_nothing_then_retry_succeeds(self) -> None:
        """中断后不发布半份结果；重试基于完整两侧重新计算。"""
        dataset, message = self.service.calculate(1, failpoint="after_first_metric")
        self.assertIsNone(dataset)
        self.assertIn("未发布任何数据集", message)
        self.assertEqual(self.service.list_datasets(1), [])
        runs = self.service.list_runs(1)
        self.assertEqual(runs[0]["状态"], "失败")
        self.assertEqual(self.service.get_entry(1)["计算状态"], "计算失败")

        dataset, message = self.service.calculate(1)
        self.assertIsNotNone(dataset, message)
        self.assertEqual(dataset["版本"], 1)
        self.assertEqual(self.service.get_entry(1)["当前版本"], 1)
        self.assertEqual(len(self.service.list_datasets(1)), 1)

    def test_concurrent_detail_change_invalidates_run(self) -> None:
        """计算期间明细变化则整次作废，不能把一侧新结果与另一侧旧结果拼接。"""
        original = compare_service.build_dataset
        service = self.service

        def racing_build(**kwargs):
            # 模拟另一侧写入：在计算完成前补报一条明细，抬高修订号。
            service.import_details(1, [{
                "指标编码": "SALES_AMOUNT", "业务日期": "2026-03-21",
                "维度值": {"区域": "华东"}, "指标值": 12345, "上报状态": "已上报",
            }])
            return original(**kwargs)

        compare_service.build_dataset = racing_build
        try:
            dataset, message = self.service.calculate(1)
        finally:
            compare_service.build_dataset = original
        self.assertIsNone(dataset)
        self.assertIn("作废", message)
        self.assertEqual(self.service.list_datasets(1), [])
        self.assertEqual(self.service.get_entry(1)["计算状态"], "待重新计算")
        self.assertEqual(self.service.list_runs(1)[0]["状态"], "作废")

    def test_detail_import_is_all_or_nothing(self) -> None:
        """任一行不合法则整批不写入，修订号不变。"""
        before = self.service.get_entry(1)["明细修订号"]
        result, message = self.service.import_details(1, [
            {"指标编码": "SALES_AMOUNT", "业务日期": "2026-03-05", "维度值": {"区域": "华东"},
             "指标值": 100, "上报状态": "已上报"},
            {"指标编码": "SALES_AMOUNT", "业务日期": "2026-03-06", "维度值": {"区域": "华东"},
             "上报状态": "未上报", "指标值": 0},
        ])
        self.assertIsNone(result)
        self.assertIn("未上报", message)
        self.assertEqual(self.service.get_entry(1)["明细修订号"], before)

    def test_ratio_import_requires_numerator_and_denominator(self) -> None:
        result, message = self.service.import_details(2, [
            {"指标编码": "CONVERSION_RATE", "业务日期": "2026-02-05", "维度值": {"渠道": "线上"},
             "指标值": 4.2, "上报状态": "已上报"},
        ])
        self.assertIsNone(result)
        self.assertIn("分子和分母", message)

    def test_recalculation_appends_version_and_keeps_history(self) -> None:
        """补报后重算生成新版本，旧版本数值保持不变。"""
        first = metric_of(1)
        first_value = cell(first, "SALES_AMOUNT", "区域=华东")["对比期"]["参与比较数值"]
        result, message = self.service.import_details(1, [{
            "指标编码": "SALES_AMOUNT", "业务日期": "2026-03-20",
            "维度值": {"区域": "华东"}, "指标值": 99999, "上报状态": "已上报",
        }])
        self.assertIsNotNone(result, message)
        self.assertIn("更正", message)
        second = metric_of(1)
        self.assertEqual(second["版本"], 2)
        self.assertEqual(second["明细修订号"], 2)
        preserved = self.service.get_dataset(first["id"])
        self.assertEqual(cell(preserved, "SALES_AMOUNT", "区域=华东")["对比期"]["参与比较数值"], first_value)
        self.assertGreater(cell(second, "SALES_AMOUNT", "区域=华东")["对比期"]["参与比较数值"], first_value)


class ReportAttachmentTest(unittest.TestCase):
    def setUp(self) -> None:
        reset_store()
        self.compare = CompareService()
        self.report = ReportService()
        self.dataset, message = self.compare.calculate(1)
        assert self.dataset is not None, message

    def test_attachment_keeps_period_coverage_sums_and_version(self) -> None:
        attachment, message = self.report.attach_dataset(1, self.dataset["id"], operator="测试员")
        self.assertIsNotNone(attachment, message)
        self.assertEqual(attachment["数据集编号"], "CMPDS-1-V1")
        self.assertEqual(attachment["数据集版本"], 1)
        self.assertEqual(attachment["明细修订号"], 1)
        self.assertEqual(attachment["计算批次"], self.dataset["计算批次"])
        self.assertEqual(attachment["周期"]["基准周期"]["文本"], "2026-02-01~2026-02-28")
        self.assertEqual(attachment["周期"]["对比周期"]["文本"], "2026-03-01~2026-03-31")
        self.assertIn("基准期", attachment["覆盖"])
        sales = next(item for item in attachment["指标附件"] if item["指标编码"] == "SALES_AMOUNT")
        self.assertEqual(sales["状态"], "正常变化")
        self.assertIsNotNone(sales["基准值"])
        self.assertIsNotNone(sales["对比值"])
        self.assertTrue(any("28 天" in line for line in attachment["覆盖说明"]))
        self.assertEqual(self.report.get_entry(1)["数据附件"], "CMPDS-1-V1 同期对比差异附件（共 1 份）")

    def test_attachment_frozen_after_correction_and_recalculation(self) -> None:
        attachment, _ = self.report.attach_dataset(1, self.dataset["id"])
        frozen_value = attachment["数据集快照"]["指标"][0]["可比汇总"]["对比值"]
        frozen_fingerprint = attachment["摘要指纹"]

        self.compare.import_details(1, [{
            "指标编码": "SALES_AMOUNT", "业务日期": "2026-03-20",
            "维度值": {"区域": "华东"}, "指标值": 88888, "上报状态": "已上报",
        }])
        second, message = self.compare.calculate(1)
        self.assertIsNotNone(second, message)
        self.assertEqual(second["版本"], 2)

        stored = self.report.get_attachment(attachment["id"])
        self.assertEqual(stored["数据集快照"]["指标"][0]["可比汇总"]["对比值"], frozen_value)
        self.assertEqual(stored["摘要指纹"], frozen_fingerprint)
        self.assertEqual(stored["数据集编号"], "CMPDS-1-V1")
        self.assertGreater(second["指标"][0]["可比汇总"]["对比值"], frozen_value)

    def test_ratio_attachment_keeps_numerator_and_denominator(self) -> None:
        dataset, message = self.compare.calculate(2)
        self.assertIsNotNone(dataset, message)
        attachment, message = self.report.attach_dataset(2, dataset["id"])
        self.assertIsNotNone(attachment, message)
        ratio = next(item for item in attachment["指标附件"] if item["指标编码"] == "CONVERSION_RATE")
        for key in ("基准分子", "基准分母", "对比分子", "对比分母"):
            self.assertIsNotNone(ratio[key], key)
        self.assertEqual(ratio["变化单位"], "百分点")
        self.assertAlmostEqual(ratio["基准值"], ratio["基准分子"] / ratio["基准分母"] * 100, places=4)
        self.assertAlmostEqual(ratio["对比值"], ratio["对比分子"] / ratio["对比分母"] * 100, places=4)

    def test_attachment_rejects_missing_dataset(self) -> None:
        attachment, message = self.report.attach_dataset(1, 999)
        self.assertIsNone(attachment)
        self.assertIn("不存在", message)


class ApiTest(unittest.TestCase):
    def setUp(self) -> None:
        reset_store()
        self.client = TestClient(app)

    def test_plan_and_calculation_api(self) -> None:
        created = self.client.post("/api/compare", json={"values": {
            "方案编号": "COMP-TEST", "方案名称": "API 测试方案",
            "指标列表": ["SALES_AMOUNT"], "维度列表": ["区域"],
            "基准周期开始": "2026-06-01", "基准周期结束": "2026-06-30",
            "对比周期开始": "2026-07-01", "对比周期结束": "2026-07-31",
        }}).json()
        self.assertTrue(created["ok"], created["message"])
        plan_id = created["entry"]["id"]

        imported = self.client.post(f"/api/compare/{plan_id}/details", json={"items": [
            {"指标编码": "SALES_AMOUNT", "业务日期": "2026-06-01", "维度值": {"区域": "华东"},
             "指标值": 100, "上报状态": "已上报"},
            {"指标编码": "SALES_AMOUNT", "业务日期": "2026-07-01", "维度值": {"区域": "华东"},
             "指标值": 150, "上报状态": "已上报"},
        ]}).json()
        self.assertTrue(imported["ok"], imported["message"])

        calculated = self.client.post(f"/api/compare/{plan_id}/calculate", json={}).json()
        self.assertTrue(calculated["ok"], calculated["message"])
        dataset_id = calculated["entry"]["数据集id"]

        dataset = self.client.get(f"/api/compare/datasets/{dataset_id}").json()
        self.assertEqual(dataset["指标"][0]["比较结果"]["变化率"], 50.0)
        trace = self.client.get(f"/api/compare/datasets/{dataset_id}/trace",
                                params={"metric": "SALES_AMOUNT", "dimension": "区域=华东"}).json()
        self.assertEqual(len(trace["维度核对"][0]["原始明细"]), 2)

        report = self.client.post("/api/report", json={"values": {
            "报告编号": "REPO-TEST", "报告主题": "API 测试报告", "分析周期": "2026-07",
        }}).json()
        report_id = report["entry"]["id"]
        attached = self.client.post(f"/api/report/{report_id}/attachments",
                                    json={"dataset_id": dataset_id}).json()
        self.assertTrue(attached["ok"], attached["message"])
        snapshot = self.client.get(f"/api/report/attachments/{attached['entry']['附件id']}").json()
        self.assertEqual(snapshot["数据集版本"], 1)
        self.assertEqual(snapshot["周期"]["对比周期"]["文本"], "2026-07-01~2026-07-31")

    def test_invalid_period_reports_reason(self) -> None:
        response = self.client.post("/api/compare", json={"values": {
            "方案编号": "COMP-BAD", "基准周期": "2026-03-31~2026-03-01", "对比周期": "2026-04-01~2026-04-30",
        }}).json()
        self.assertFalse(response["ok"])
        self.assertIn("结束日期早于开始日期", response["message"])

    def test_duplicate_plan_code_rejected(self) -> None:
        response = self.client.post("/api/compare", json={"values": {
            "方案编号": "COMP-0001", "基准周期": "2026-02-01~2026-02-28", "对比周期": "2026-03-01~2026-03-31",
        }}).json()
        self.assertFalse(response["ok"])
        self.assertIn("已存在", response["message"])


if __name__ == "__main__":
    unittest.main()
