"""
三年财务趋势链路测试。

这个脚本验证三件事：
1. Python 能把三年数据算成正确的同比、CAGR 和关键数学事实；
2. 财务专家的系统提示词确实加入了“禁止自行计算增长率”的规则；
3. 趋势摘要只传给财务专家，不会误传给法务和业务专家。

运行方式：
- 只跑离线测试（不联网、不花钱）：
      python test_trend_pipeline.py
- 额外真实调用一次 DeepSeek，查看模型是否引用 Python 算出的数字：
      python test_trend_pipeline.py --live

说明：
- 离线测试使用假数据，不会发送任何真实财务材料；
- --live 会调用 DeepSeek，产生少量 API 费用；
- 脚本不会打印 API Key，也不会把 Key 写入文件。
"""

from __future__ import annotations

import argparse
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from agents import finance_agent
from agents.finance_agent import FinanceAgentError, analyze_finance
from agents.orchestrator import analyze_uploaded_text
from utils.financial_statements import StatementTable, ThreeYearStatements
from utils.trend_calculator import build_trend_raw_data, calculate_trends


# ---------------------------------------------------------------------------
# 构造假数据
# ---------------------------------------------------------------------------


def build_fake_statements() -> ThreeYearStatements:
    """
    构造一家“营收还在涨、但增速放缓、现金流和毛利率持续下滑”的假公司。

    返回：
        ThreeYearStatements: 三张表的完整结果，金额单位为亿元。

    说明：
        数据故意设计成一组典型的尽调信号：
        - 营业收入 100 → 110 → 115，增长但增速从 10% 降到 4.55%；
        - 归母净利润 18 → 20 → 19，最近一年转为下降；
        - 经营现金流 25 → 20 → 12，连续两年下降；
        - 毛利率 40% → 38.18% → 35.65%，连续两年下降；
        - 应收账款 20 → 26 → 36，增速远高于营收；
        - 存货 30 → 36 → 45，增速也高于营收；
        - 资产负债率 40% → 45.45% → 52.08%，连续上升。
        这些数字只用于测试，不代表任何真实公司。
    """
    years = [2025, 2024, 2023]

    balance_rows = [
        {
            "项目": "资产总计",
            "2025": "240.00",
            "2024": "220.00",
            "2023": "200.00",
        },
        {
            "项目": "负债合计",
            "2025": "125.00",
            "2024": "100.00",
            "2023": "80.00",
        },
        {
            "项目": "应收账款",
            "2025": "36.00",
            "2024": "26.00",
            "2023": "20.00",
        },
        {
            "项目": "存货",
            "2025": "45.00",
            "2024": "36.00",
            "2023": "30.00",
        },
    ]

    income_rows = [
        {
            "项目": "营业收入",
            "2025": "115.00",
            "2024": "110.00",
            "2023": "100.00",
        },
        {
            "项目": "营业成本",
            "2025": "74.00",
            "2024": "68.00",
            "2023": "60.00",
        },
        {
            "项目": "归属于母公司所有者的净利润",
            "2025": "19.00",
            "2024": "20.00",
            "2023": "18.00",
        },
    ]

    cash_flow_rows = [
        {
            "项目": "经营活动产生的现金流量净额",
            "2025": "12.00",
            "2024": "20.00",
            "2023": "25.00",
        },
    ]

    return ThreeYearStatements(
        code="000001",
        year=2025,
        years=years,
        tables={
            "balance": StatementTable(
                key="balance",
                title="资产负债表",
                years=years,
                rows=balance_rows,
            ),
            "income": StatementTable(
                key="income",
                title="利润表",
                years=years,
                rows=income_rows,
            ),
            "cash_flow": StatementTable(
                key="cash_flow",
                title="现金流量表",
                years=years,
                rows=cash_flow_rows,
            ),
        },
        source="测试假数据",
        fetched_at="2026-11-09 12:00:00",
    )


def build_fake_summary() -> str:
    """生成测试用的趋势摘要。"""
    statements = build_fake_statements()
    raw_data = build_trend_raw_data(statements.to_dict())
    return calculate_trends(raw_data)


# ---------------------------------------------------------------------------
# 离线测试：算数是否正确
# ---------------------------------------------------------------------------


class TrendCalculatorTests(unittest.TestCase):
    """验证纯 Python 趋势计算的数字和措辞边界。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.statements = build_fake_statements()
        cls.summary = build_fake_summary()

    def test_revenue_yoy_and_cagr_are_correct(self) -> None:
        """营收同比应为 2024 +10.00%、2025 +4.55%，CAGR 应为 +7.24%。"""
        self.assertIn("2024 年同比 +10.00%", self.summary)
        self.assertIn("2025 年同比 +4.55%", self.summary)
        self.assertIn("2023→2025 CAGR：+7.24%", self.summary)
        # 绝对变化额也应由 Python 算好，避免模型自己做减法。
        self.assertIn("2024 年较 2023 年 +10.00 亿元", self.summary)
        self.assertIn("2025 年较 2024 年 +5.00 亿元", self.summary)

    def test_cash_flow_decline_is_correct(self) -> None:
        """经营现金流应连续下降：2024 -20.00%、2025 -40.00%。"""
        self.assertIn("2024 年同比 -20.00%", self.summary)
        self.assertIn("2025 年同比 -40.00%", self.summary)
        self.assertIn("经营活动现金流量净额", self.summary)
        self.assertIn("2025 年较 2024 年 -8.00 亿元", self.summary)

    def test_gross_margin_uses_percentage_points(self) -> None:
        """毛利率变化应使用“个百分点”，不能误写成百分比。"""
        self.assertIn("2025=35.65%", self.summary)
        self.assertIn("2025 年较 2024 年 -2.53 个百分点", self.summary)
        self.assertIn("毛利率连续 2 个年度下降", self.summary)

    def test_cash_quality_uses_times_not_percentage_points(self) -> None:
        """经营现金流/净利润是倍数，变化应写“倍”，不能写“个百分点”。"""
        self.assertIn("2025=0.63 倍", self.summary)
        self.assertIn("2025 年较 2024 年 -0.37 倍", self.summary)

        # 单独截出“经营现金流净额/归母净利润”这一节，
        # 确认节内没有误用“个百分点”。
        section = self.summary.split(
            "经营现金流净额/归母净利润（倍）",
            maxsplit=1,
        )[1].split("##", maxsplit=1)[0]
        self.assertNotIn("个百分点", section)

    def test_key_facts_capture_divergence(self) -> None:
        """关键事实应指出营收涨、现金流跌，以及应收增速高于营收。"""
        self.assertIn("两者方向相反", self.summary)
        self.assertIn("应收账款 +38.46%", self.summary)
        self.assertIn("高于营业收入 +4.55%", self.summary)
        # 营运资金合计增量也必须由 Python 算好，避免模型自己相加。
        self.assertIn("应收账款变化 +10.00 亿元", self.summary)
        self.assertIn("存货变化 +9.00 亿元", self.summary)
        self.assertIn("两者合计变化 +19.00 亿元", self.summary)
        self.assertIn("合计变化额比经营现金流净额高 7.00 亿元", self.summary)

    def test_summary_has_no_subjective_analysis(self) -> None:
        """摘要只能陈述数学事实，不能提前写原因判断。"""
        for word in ("可能", "说明", "原因", "建议", "价格战", "成本失控"):
            self.assertNotIn(word, self.summary)

    def test_missing_values_are_not_treated_as_zero(self) -> None:
        """缺少年份时应显示“—”，不能把缺失当成 0 计算。"""
        raw_data = {
            "unit": "亿元",
            "years": [2023, 2024, 2025],
            "metrics": {
                "营业收入": {2023: 100.0, 2025: 120.0},
            },
        }
        summary = calculate_trends(raw_data)
        self.assertIn("2024=—", summary)
        # 2025 与 2023 之间缺 2024，不能冒充“同比”。
        self.assertIn("2025 年较 2023 年 +20.00%", summary)

    def test_cagr_is_not_guessed_when_base_is_negative(self) -> None:
        """首年为负数时 CAGR 应显示“—”，不能硬算。"""
        raw_data = {
            "unit": "亿元",
            "years": [2023, 2024, 2025],
            "metrics": {
                "归母净利润": {2023: -10.0, 2024: 2.0, 2025: 5.0},
            },
        }
        summary = calculate_trends(raw_data)
        self.assertIn("CAGR：—", summary)

    def test_build_raw_data_reads_statement_tables(self) -> None:
        """从三张表抽取指标时，应识别“营业收入”和归母净利润。"""
        raw_data = build_trend_raw_data(self.statements.to_dict())
        self.assertEqual(raw_data["unit"], "亿元")
        self.assertEqual(raw_data["years"], [2023, 2024, 2025])
        self.assertEqual(
            raw_data["metrics"]["营业收入"][2025],
            115.0,
        )
        self.assertEqual(
            raw_data["metrics"]["归母净利润"][2023],
            18.0,
        )


# ---------------------------------------------------------------------------
# 离线测试：提示词和调度是否正确
# ---------------------------------------------------------------------------


class _FakeCompletions:
    """假的 DeepSeek 聊天接口，只记录请求内容，不联网。"""

    def __init__(self) -> None:
        self.kwargs: dict | None = None

    def create(self, **kwargs):
        """保存调用参数，并返回一段最小可用的假报告。"""
        self.kwargs = kwargs
        content = (
            "## 一、三大财务报表数据摘录\n"
            "### （一）资产负债表\n"
            "| 科目 | 期末余额 | 期初余额 | 来源 |\n"
            "|---|---|---|---|\n"
            "| 营业收入 | 115.00 | 110.00 | 原文 |\n\n"
            "## 二、盈利能力\n"
            "- **风险等级**：高\n"
            "- **数据基础**：营业收入 115.00 亿元。\n"
            "- **核心指标计算**：毛利率 35.65%。\n"
            "- **风险判断**：测试用假报告，仅用于验证提示词传递。\n"
        )
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=content)
                )
            ]
        )


class PromptWiringTests(unittest.TestCase):
    """验证趋势摘要确实进入了财务专家的提示词，且只给财务专家。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.statements = build_fake_statements()
        cls.summary = build_fake_summary()
        cls.context = cls.statements.to_agent_context()

    def test_finance_prompt_contains_summary_and_no_calc_rule(self) -> None:
        """系统提示词要有“禁止重算”，用户消息要有趋势摘要标签。"""
        fake_completions = _FakeCompletions()

        with patch.object(
            finance_agent,
            "_build_client",
            return_value=SimpleNamespace(
                chat=SimpleNamespace(completions=fake_completions)
            ),
        ), patch.object(
            finance_agent,
            "_enforce_report_rules",
            side_effect=lambda report, **kwargs: report,
        ):
            analyze_finance(
                self.context,
                dimensions=["盈利能力", "现金流量"],
                trend_summary=self.summary,
            )

        self.assertIsNotNone(fake_completions.kwargs)
        messages = fake_completions.kwargs["messages"]
        system_prompt = messages[0]["content"]
        user_prompt = messages[1]["content"]

        self.assertIn("绝对禁止你自己计算", system_prompt)
        self.assertIn("CAGR", system_prompt)
        self.assertIn("<trend_summary>", user_prompt)
        self.assertIn("2023→2025 CAGR：+7.24%", user_prompt)
        # 摘要必须出现在原始材料之前，方便模型先看结论再看明细。
        self.assertLess(
            user_prompt.index("<trend_summary>"),
            user_prompt.index("<financial_material>"),
        )

    def test_calculation_trace_ignores_slash_in_metric_name(self) -> None:
        """指标名称里的斜杠不是除法，不能误删整句分析。"""
        citation = "经营现金流净额/归母净利润 0.63 倍（趋势摘要）。"
        formula = "经营现金流净额÷归母净利润＝12.00 ÷ 19.00 = 0.63 倍。"

        self.assertFalse(finance_agent._contains_calculation_trace(citation))
        self.assertTrue(finance_agent._contains_calculation_trace(formula))

    def test_trend_mode_keeps_risk_header_on_its_own_line(self) -> None:
        """“风险判断”标题单独一行时，正文必须保留，不能整段删掉。"""
        report = (
            "## 一、三大财务报表数据摘录\n\n"
            "### （一）资产负债表\n"
            "| 科目 | 期末余额 | 期初余额 | 来源 |\n"
            "|---|---|---|---|\n"
            "| 资产总计 | 240.00 | 220.00 | 原文 |\n\n"
            "### （二）利润表\n"
            "| 科目 | 本期金额 | 上期金额 | 来源 |\n"
            "|---|---|---|---|\n"
            "| 营业收入 | 115.00 | 110.00 | 原文 |\n"
            "| 营业成本 | 74.00 | 68.00 | 原文 |\n"
            "| 归属于母公司所有者的净利润 | 19.00 | 20.00 | 原文 |\n\n"
            "### （三）现金流量表\n"
            "| 科目 | 本期金额 | 上期金额 | 来源 |\n"
            "|---|---|---|---|\n"
            "| 经营活动产生的现金流量净额 | 12.00 | 20.00 | 原文 |\n\n"
            "## 二、盈利能力\n"
            "- **风险等级**：中\n"
            "- **数据基础**：营业收入 115.00 亿元（趋势摘要）。\n"
            "- **核心指标引用**：毛利率 35.65%（趋势摘要）。\n"
            "- **风险判断**：\n"
            "  毛利率连续下降，盈利承压。"
            "经营现金流净额/归母净利润 0.63 倍（趋势摘要）。\n"
        )

        cleaned = finance_agent._enforce_report_rules(
            report,
            trend_mode=True,
        )

        self.assertIn("## 二、盈利能力", cleaned)
        self.assertIn("毛利率连续下降", cleaned)
        self.assertIn("0.63 倍", cleaned)

    def test_trend_mode_removes_self_calculation_sentences(self) -> None:
        """趋势模式下，模型自己写的公式和等号必须被程序删掉。"""
        report = (
            "## 一、三大财务报表数据摘录\n\n"
            "### （一）资产负债表\n"
            "| 科目 | 期末余额 | 期初余额 | 来源 |\n"
            "|---|---|---|---|\n"
            "| 资产总计 | 240.00 | 220.00 | 原文 |\n\n"
            "### （二）利润表\n"
            "| 科目 | 本期金额 | 上期金额 | 来源 |\n"
            "|---|---|---|---|\n"
            "| 营业收入 | 115.00 | 110.00 | 原文 |\n"
            "| 营业成本 | 74.00 | 68.00 | 原文 |\n\n"
            "### （三）现金流量表\n"
            "| 科目 | 本期金额 | 上期金额 | 来源 |\n"
            "|---|---|---|---|\n"
            "| 经营活动产生的现金流量净额 | 12.00 | 20.00 | 原文 |\n\n"
            "## 二、盈利能力\n"
            "- **风险等级**：中\n"
            "- **数据基础**：营业收入 115.00 亿元（趋势摘要）。\n"
            "- **核心指标引用**：毛利率 35.65%（趋势摘要）。\n"
            "- **风险判断**：毛利率连续下降，盈利承压。"
            "经营现金流净额÷归母净利润＝12.00 ÷ 19.00 = 0.63 倍，"
            "说明现金支撑弱。\n"
        )

        cleaned = finance_agent._enforce_report_rules(
            report,
            trend_mode=True,
        )

        self.assertNotIn("÷", cleaned)
        self.assertNotIn("根据原文数据计算", cleaned)
        self.assertIn("毛利率连续下降", cleaned)

    def test_summary_is_not_sent_to_other_agents(self) -> None:
        """调度器只能把 trend_summary 传给财务专家，法务不能收到。"""
        records: dict[str, str] = {}

        def fake_finance(
            text: str,
            dimensions=None,
            analysis_mode=None,
            trend_summary: str = "",
        ) -> str:
            records["finance"] = trend_summary
            return "财务专家假结果"

        def fake_legal(
            text: str,
            dimensions=None,
            analysis_mode=None,
        ) -> str:
            records["legal"] = "未收到趋势摘要"
            return "法务专家假结果"

        with patch.dict(
            "agents.orchestrator.AGENT_DEFINITIONS",
            {
                "finance": {
                    "agent_name": "财务专家",
                    "dimensions": ["盈利能力"],
                    "analyze": fake_finance,
                    "error_type": FinanceAgentError,
                },
                "legal": {
                    "agent_name": "法务专家",
                    "dimensions": ["诉讼风险"],
                    "analyze": fake_legal,
                    "error_type": FinanceAgentError,
                },
            },
            clear=True,
        ):
            result = analyze_uploaded_text(
                "测试材料",
                agents=["finance", "legal"],
                finance_trend_summary="PYTHON_SUMMARY",
            )

        self.assertEqual(records["finance"], "PYTHON_SUMMARY")
        self.assertEqual(records["legal"], "未收到趋势摘要")
        self.assertEqual(result["status"], "ok")


# ---------------------------------------------------------------------------
# 真实调用 DeepSeek：检查模型是否引用 Python 的数字
# ---------------------------------------------------------------------------


def run_live_demo() -> int:
    """
    真实调用一次 DeepSeek，观察模型是否按 Python 摘要分析。

    返回：
        int: 0 表示调用成功，1 表示调用失败。

    说明：
        - 只发送本脚本构造的假数据，不发送任何真实公司材料；
        - 会产生少量 API 费用；
        - 这里不强制模型必须写出某个数字（模型可能选择不同维度），
          但会打印一个“引用检查”结果，方便人工复核。
    """
    statements = build_fake_statements()
    summary = build_fake_summary()
    context = statements.to_agent_context()

    print("=" * 78)
    print("一、Python 计算出的三年趋势摘要")
    print("=" * 78)
    print(summary)

    print("=" * 78)
    print("二、正在调用 DeepSeek（会产生少量费用）……")
    print("=" * 78)

    try:
        report = analyze_finance(
            context,
            dimensions=["盈利能力", "现金流量"],
            trend_summary=summary,
        )
    except FinanceAgentError as exc:
        print(f"调用失败：{exc}")
        return 1

    print("=" * 78)
    print("三、DeepSeek 返回的财务分析")
    print("=" * 78)
    print(report)

    print("=" * 78)
    print("四、引用检查（人工复核用）")
    print("=" * 78)
    markers = (
        "+7.24%",
        "35.65%",
        "0.63 倍",
        "+4.55%",
        "-40.00%",
    )
    for marker in markers:
        if marker in report:
            print(f"[找到] {marker}")
        else:
            print(f"[未出现] {marker}")

    print("\n维度章节检查：")
    for heading in ("## 二、盈利能力", "## 三、现金流量"):
        if heading in report:
            print(f"[找到] {heading}")
        else:
            print(f"[未出现] {heading}")

    print("\n自行计算痕迹检查：")
    forbidden_markers = (
        "根据原文数据计算",
        "根据公开摘要计算",
        "代入",
        "计算公式",
        "＝",
        "=",
        "÷",
        "×",
    )
    for marker in forbidden_markers:
        if marker in report:
            print(f"[发现痕迹] {marker}")
        else:
            print(f"[干净] 未出现 {marker}")

    print(
        "\n提示：如果报告里的增长率与上面的 Python 摘要不一致，"
        "说明模型仍在自行计算，需要继续收紧提示词。"
    )
    return 0


class StatementYearHeaderTests(unittest.TestCase):
    """验证三大报表表头始终是三个实际年份，而不是期末/期初两列。"""

    def test_finance_prompt_uses_dynamic_year_headers(self) -> None:
        """提示词必须把实际年份写进表头，并覆盖所有三张表。"""
        prompt = finance_agent._build_system_prompt(
            ["盈利能力"],
            has_trend_summary=True,
            trend_years=[2025, 2024, 2023],
        )

        # 三张表都使用同一个年份表头。
        self.assertEqual(
            prompt.count("| 科目 | 2023 | 2024 | 2025 | 来源 |"),
            3,
        )
        self.assertIn("| 货币资金 | - | - | - | - |", prompt)
        self.assertNotIn("| 科目 | 期末余额 | 期初余额 |", prompt)
        self.assertIn("2023、2024、2025", prompt)

    def test_two_column_table_is_expanded_to_three_years(self) -> None:
        """模型退回两列时，程序要补成三列，最早一年填“-”。"""
        section = (
            "### （一）资产负债表\n"
            "| 科目 | 期末余额 | 期初余额 | 来源 |\n"
            "|---|---|---|---|\n"
            "| 货币资金 | 100.00 | 90.00 | 原文 |\n"
        )

        cleaned = finance_agent._normalize_statement_table_years(
            section,
            [2025, 2024, 2023],
        )

        self.assertIn("| 科目 | 2023 | 2024 | 2025 | 来源 |", cleaned)
        self.assertIn("| 货币资金 | - | 90.00 | 100.00 | 原文 |", cleaned)
        self.assertNotIn("期末余额", cleaned)
        self.assertNotIn("期初余额", cleaned)

    def test_three_year_table_is_sorted_ascending(self) -> None:
        """模型按 2025/2024/2023 倒序输出时，程序统一改成升序。"""
        # 故意用“项目”而不是“科目”，验证模型换表头词时也能识别。
        section = (
            "| 项目 | 2025 | 2024 | 2023 | 来源 |\n"
            "|---|---|---|---|---|\n"
            "| 营业收入 | 115.00 | 110.00 | 100.00 | 原文 |\n"
        )

        cleaned = finance_agent._normalize_statement_table_years(
            section,
            [2025, 2024, 2023],
        )

        self.assertIn("| 科目 | 2023 | 2024 | 2025 | 来源 |", cleaned)
        self.assertIn("| 营业收入 | 100.00 | 110.00 | 115.00 | 原文 |", cleaned)

    def test_enforce_report_normalizes_statement_headers(self) -> None:
        """完整报告质检后，三大报表表头仍是三个年份列。"""
        report = (
            "## 一、三大财务报表数据摘录\n\n"
            "### （一）资产负债表\n"
            "| 科目 | 期末余额 | 期初余额 | 来源 |\n"
            "|---|---|---|---|\n"
            "| 资产总计 | 240.00 | 220.00 | 原文 |\n\n"
            "### （二）利润表\n"
            "| 科目 | 本期金额 | 上期金额 | 来源 |\n"
            "|---|---|---|---|\n"
            "| 营业收入 | 115.00 | 110.00 | 原文 |\n"
            "| 营业成本 | 74.00 | 68.00 | 原文 |\n"
            "| 归属于母公司所有者的净利润 | 19.00 | 20.00 | 原文 |\n\n"
            "### （三）现金流量表\n"
            "| 科目 | 本期金额 | 上期金额 | 来源 |\n"
            "|---|---|---|---|\n"
            "| 经营活动产生的现金流量净额 | 12.00 | 20.00 | 原文 |\n\n"
            "## 二、盈利能力\n"
            "- **风险等级**：中\n"
            "- **数据基础**：营业收入 115.00 亿元。\n"
            "- **核心指标引用**：毛利率 35.65%（趋势摘要）。\n"
            "- **风险判断**：毛利率连续下降，盈利承压。\n"
        )

        cleaned = finance_agent._enforce_report_rules(
            report,
            trend_mode=True,
            trend_years=[2025, 2024, 2023],
        )

        self.assertEqual(
            cleaned.count("| 科目 | 2023 | 2024 | 2025 | 来源 |"),
            3,
        )
        self.assertIn("| 资产总计 | - | 220.00 | 240.00 | 原文 |", cleaned)
        self.assertIn("## 二、盈利能力", cleaned)
        self.assertNotIn("| 科目 | 期末余额 | 期初余额 |", cleaned)

    def test_analyze_finance_wires_trend_years(self) -> None:
        """analyze_finance 要把年份传给提示词，并改写模型返回的表头。"""
        fake_completions = _FakeCompletions()

        with patch.object(
            finance_agent,
            "_build_client",
            return_value=SimpleNamespace(
                chat=SimpleNamespace(completions=fake_completions)
            ),
        ):
            result = analyze_finance(
                "营业收入 115.00 亿元，营业成本 74.00 亿元。",
                dimensions=["盈利能力"],
                trend_summary=build_fake_summary(),
                trend_years=[2025, 2024, 2023],
            )

        system_prompt = fake_completions.kwargs["messages"][0]["content"]
        self.assertIn("| 科目 | 2023 | 2024 | 2025 | 来源 |", system_prompt)
        self.assertIn("| 科目 | 2023 | 2024 | 2025 | 来源 |", result)

    def test_normalize_statement_years_keeps_latest_three(self) -> None:
        """传四个年份时只保留最近三年；不足三年时返回空列表。"""
        self.assertEqual(
            finance_agent._normalize_statement_years(
                [2022, 2025, 2024, 2023]
            ),
            [2023, 2024, 2025],
        )
        self.assertEqual(
            finance_agent._normalize_statement_years([2025, 2024]),
            [],
        )
        self.assertEqual(
            finance_agent._normalize_statement_years(["2025", "2024", "2023"]),
            [2023, 2024, 2025],
        )


def main() -> int:
    """先跑离线测试，用户加了 --live 时再跑真实调用。"""
    parser = argparse.ArgumentParser(
        description="三年财务趋势链路测试",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="真实调用一次 DeepSeek，产生少量 API 费用",
    )
    args = parser.parse_args()

    # 先跑离线测试。离线测试全部不联网，失败时直接返回非零退出码。
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    result = unittest.TextTestRunner(verbosity=2).run(suite)

    if not result.wasSuccessful():
        return 1

    if args.live:
        return run_live_demo()

    print(
        "\n离线测试全部通过。若要查看真实 DeepSeek 分析，"
        "请运行：python test_trend_pipeline.py --live"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
