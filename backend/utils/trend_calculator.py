"""
三年核心财务趋势计算器。

为什么要有这个模块：
- 大模型做加减乘除时容易算错，尤其是百分比、复合增长率和正负号；
- 财务尽调报告的结论必须能复核，所以把“算数”交给 Python，
  把“解释原因、判断经营含义”交给大模型；
- 本模块只输出客观的数学结果，不写“可能是价格战”“说明经营恶化”
  这类主观判断，避免程序先替模型下结论。

输入与输出：
- 输入：近三年的核心财务数据，金额可以是“元”“万元”或“亿元”；
- 输出：一段结构化纯文本，包含每年数值、同比变化、三年 CAGR、
  数学特征和关键背离事实；
- 所有无法计算的项统一显示“—”，绝不用其他年份或模型知识补数字。

使用方式：
1. 直接调用 calculate_trends(raw_data)；
2. 如果数据来自页面缓存的 AKShare 三张表，先调用
   build_trend_raw_data(三张表字典)，再把结果交给 calculate_trends。
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Mapping


# ---------------------------------------------------------------------------
# 基础配置
# ---------------------------------------------------------------------------

# 输入数据允许的单位，以及换算成“亿元”时要除以多少。
# 例如输入单位是“元”，除以 1 亿就得到亿元；输入已经是“亿元”则除以 1。
_UNIT_DIVISORS: dict[str, float] = {
    "元": 100_000_000.0,
    "千元": 100_000.0,
    "万元": 10_000.0,
    "亿元": 1.0,
}

# raw_data 里除了指标本身之外的说明字段，解析时不能把它们当成财务指标。
_METADATA_KEYS = frozenset(
    {
        "unit",
        "years",
        "metrics",
        "source",
        "code",
        "company_name",
        "报告期",
    }
)

# 表示“没有数据”的常见写法。统一按缺失处理，不能当成 0。
_MISSING_TEXTS = frozenset(
    {
        "",
        "-",
        "--",
        "—",
        "–",
        "/",
        "n/a",
        "na",
        "none",
        "null",
        "nan",
    }
)


@dataclass(frozen=True)
class _MetricDefinition:
    """
    一个原始财务指标的取数规则。

    属性：
        key: 程序内部使用的统一名称，例如“营业收入”。
        label: 报告里展示的中文名称。
        aliases: 新浪接口可能使用的科目名，按优先级排列。
        table: 到哪张表里找，取值为 balance / income / cash_flow。
    """

    key: str
    label: str
    aliases: tuple[str, ...]
    table: str


# 需要从三张表里抽取的核心指标。
# 这里只放“趋势分析真正会用到的科目”，不是把整张表搬过来，
# 否则摘要会太长，反而挤占专家分析正文的空间。
_METRIC_DEFINITIONS: tuple[_MetricDefinition, ...] = (
    _MetricDefinition(
        key="营业收入",
        label="营业收入",
        aliases=("营业收入", "营业总收入"),
        table="income",
    ),
    _MetricDefinition(
        key="营业成本",
        label="营业成本",
        aliases=("营业成本",),
        table="income",
    ),
    _MetricDefinition(
        key="归母净利润",
        label="归母净利润",
        aliases=(
            "归属于母公司所有者的净利润",
            "归属于母公司股东的净利润",
        ),
        table="income",
    ),
    _MetricDefinition(
        key="经营活动现金流量净额",
        label="经营活动现金流量净额",
        aliases=("经营活动产生的现金流量净额",),
        table="cash_flow",
    ),
    _MetricDefinition(
        key="资产总计",
        label="资产总计",
        aliases=("资产总计",),
        table="balance",
    ),
    _MetricDefinition(
        key="负债合计",
        label="负债合计",
        aliases=("负债合计",),
        table="balance",
    ),
    _MetricDefinition(
        key="流动资产合计",
        label="流动资产合计",
        aliases=("流动资产合计",),
        table="balance",
    ),
    _MetricDefinition(
        key="流动负债合计",
        label="流动负债合计",
        aliases=("流动负债合计",),
        table="balance",
    ),
    _MetricDefinition(
        key="所有者权益合计",
        label="所有者权益合计",
        aliases=(
            "所有者权益(或股东权益)合计",
            "所有者权益（或股东权益）合计",
            "所有者权益合计",
            "股东权益合计",
        ),
        table="balance",
    ),
    _MetricDefinition(
        key="应收账款",
        label="应收账款",
        aliases=("应收账款",),
        table="balance",
    ),
    _MetricDefinition(
        key="存货",
        label="存货",
        aliases=("存货",),
        table="balance",
    ),
    _MetricDefinition(
        key="利润总额",
        label="利润总额",
        aliases=("利润总额",),
        table="income",
    ),
    _MetricDefinition(
        key="利息费用",
        label="利息费用",
        aliases=("利息费用",),
        table="income",
    ),
)


# ---------------------------------------------------------------------------
# 数字与年份解析
# ---------------------------------------------------------------------------


def _normalize_unit(unit: Any) -> str:
    """
    把用户或调用方传入的单位统一成四种标准写法。

    参数：
        unit: 例如“元”“人民币元”“亿元”“亿”。

    返回：
        str: “元”“千元”“万元”“亿元”之一；无法识别时默认“亿元”。

    说明：
        页面缓存的 AKShare 三张表已经换算成亿元，所以默认“亿元”；
        如果调用方直接传新浪接口的原始数字，应显式写 unit="元"。
    """
    text = str(unit or "").strip()
    if text in {"元", "人民币元", "CNY", "cny"}:
        return "元"
    if text in {"千元", "人民币千元"}:
        return "千元"
    if text in {"万元", "人民币万元"}:
        return "万元"
    if text in {"亿元", "亿", "人民币亿元"}:
        return "亿元"
    return "亿元"


def _to_year(value: Any) -> int | None:
    """
    从各种年份写法中提取四位年份。

    参数：
        value: 例如 2025、\"2025\"、\"2025年\"、\"20251231\"。

    返回：
        int | None: 四位年份；无法识别时返回 None。
    """
    digits = re.sub(r"\D", "", str(value))
    if len(digits) < 4:
        return None

    try:
        year = int(digits[:4])
    except ValueError:
        return None

    # 财务数据一般不会早于 1900 年，也不会晚于 2200 年。
    # 这个范围检查可以过滤掉“0000”之类的脏数据。
    return year if 1900 <= year <= 2200 else None


def _parse_number(value: Any) -> float | None:
    """
    把单元格转成浮点数，无法识别时返回 None。

    参数：
        value: 可能是数字、带千分位的字符串、“-”、None 或 NaN。

    返回：
        float | None: 可用数字；缺失或无效值返回 None。

    说明：
        - “-”“—”“N/A”等表示接口没有返回，绝不能当成 0；
        - 兼容“(123.45)”这种会计负数写法；
        - 去掉“元/万元/亿元”等单位后缀，金额单位由 raw_data 的 unit 统一决定。
    """
    if value is None or isinstance(value, bool):
        return None

    if isinstance(value, (int, float)):
        number = float(value)
    else:
        text = str(value).strip()
        if text.lower() in _MISSING_TEXTS:
            return None

        # 会计上常用括号表示负数，例如 (123.45) 表示 -123.45。
        negative = text.startswith("(") and text.endswith(")")
        if negative:
            text = text[1:-1].strip()

        text = text.replace(",", "").replace("，", "")
        text = text.replace("%", "")
        text = re.sub(
            r"(?:人民币)?(?:亿元|万元|千元|元)$",
            "",
            text,
        ).strip()

        if text.lower() in _MISSING_TEXTS:
            return None

        try:
            number = float(text)
        except (TypeError, ValueError):
            return None

        if negative:
            number = -number

    if math.isnan(number) or math.isinf(number):
        return None

    return number


def _normalize_raw_data(
    raw_data: Mapping[str, Any],
) -> tuple[list[int], dict[str, dict[int, float]], str, str]:
    """
    校验并整理 calculate_trends 的输入。

    参数：
        raw_data: 形如：
            {
                "unit": "亿元",
                "years": [2023, 2024, 2025],
                "metrics": {
                    "营业收入": {2023: 100, 2024: 110, 2025: 115},
                    "归母净利润": [18, 20, 19],
                },
                "source": "AKShare",
            }

    返回：
        tuple:
            - 按升序排列的年份列表；
            - 指标字典，值为 {年份: 数字}；
            - 标准化后的单位；
            - 数据来源说明。

    异常：
        ValueError: 输入不是字典、没有指标，或少于两个年度时抛出。
                    错误文字是中文，页面可以直接展示。

    说明：
        兼容两种指标写法：
        1. 字典：{2023: 100, 2024: 110}；
        2. 列表：[100, 110]，按 years 的顺序一一对应。
    """
    if not isinstance(raw_data, Mapping):
        raise ValueError("趋势计算的输入必须是字典。")

    unit = _normalize_unit(raw_data.get("unit", "亿元"))
    source = str(raw_data.get("source", "")).strip()

    # 先读取调用方明确给出的年份；没有时再从指标里推断。
    declared_years: list[int] = []
    raw_years = raw_data.get("years", [])
    if isinstance(raw_years, (list, tuple)):
        for item in raw_years:
            year = _to_year(item)
            if year is not None and year not in declared_years:
                declared_years.append(year)
    declared_years.sort()

    raw_metrics = raw_data.get("metrics")
    if raw_metrics is None:
        # 兼容最简写法：{"营业收入": {...}, "归母净利润": {...}}
        raw_metrics = {
            key: value
            for key, value in raw_data.items()
            if key not in _METADATA_KEYS
        }

    if not isinstance(raw_metrics, Mapping):
        raise ValueError("趋势计算的 metrics 必须是字典。")

    metrics: dict[str, dict[int, float]] = {}
    inferred_years: set[int] = set(declared_years)

    for name, series in raw_metrics.items():
        label = str(name).strip()
        if not label:
            continue

        parsed: dict[int, float] = {}

        if isinstance(series, Mapping):
            for year_value, number_value in series.items():
                year = _to_year(year_value)
                number = _parse_number(number_value)
                if year is None or number is None:
                    continue
                # 如果调用方已经声明年份，只保留声明范围内的数据，
                # 避免混入其他报告期的数字。
                if declared_years and year not in declared_years:
                    continue
                parsed[year] = number
                inferred_years.add(year)

        elif isinstance(series, (list, tuple)):
            # 列表写法必须依赖 years 才能知道每个数字属于哪一年。
            if not declared_years:
                continue
            for year, number_value in zip(declared_years, series):
                number = _parse_number(number_value)
                if number is None:
                    continue
                parsed[year] = number

        if parsed:
            metrics[label] = parsed

    years = sorted(inferred_years)

    if not metrics:
        raise ValueError("没有可计算的财务指标。")

    if len(years) < 2:
        raise ValueError("至少需要两个年度的数据才能计算趋势。")

    return years, metrics, unit, source


# ---------------------------------------------------------------------------
# 从 AKShare 三张表中抽取指标
# ---------------------------------------------------------------------------


def build_trend_raw_data(payload: Any) -> dict:
    """
    从缓存的三年三张表中抽取 calculate_trends 需要的原始数据。

    参数：
        payload: 可以是 ThreeYearStatements 对象，也可以是它的 to_dict()
                 结果；页面 session_state 中保存的是字典。

    返回：
        dict: 形如
            {
                "unit": "亿元",
                "years": [2023, 2024, 2025],
                "metrics": {"营业收入": {2023: 100.0, ...}, ...},
                "source": "AKShare（新浪财经）近三年合并报表",
            }

    异常：
        ValueError: 传入的不是三张表结果，或表中没有任何可用指标时抛出。

    说明：
        页面展示的三张表已经换算成亿元，单元格是带千分位的字符串；
        本函数负责把这些字符串还原成数字，交给 calculate_trends。
        如果某一年是“-”，该年份不会出现在指标里，后续计算会自动跳过。
    """
    # ThreeYearStatements 是 dataclass，带 to_dict()；直接转成普通字典，
    # 后面的逻辑就不依赖具体类，方便测试时传入假数据。
    if hasattr(payload, "to_dict"):
        payload = payload.to_dict()

    if not isinstance(payload, Mapping):
        raise ValueError("三张表数据格式不正确。")

    tables = payload.get("tables")
    if not isinstance(tables, Mapping):
        # 兼容直接传 {"balance": ..., "income": ..., "cash_flow": ...}
        if any(key in payload for key in ("balance", "income", "cash_flow")):
            tables = payload
        else:
            raise ValueError("三张表数据中缺少 tables 字段。")

    # 年份优先取外层 years；外层没有时再从每张表的 years 里补。
    years: list[int] = []
    raw_years = payload.get("years", [])
    if isinstance(raw_years, (list, tuple)):
        for item in raw_years:
            year = _to_year(item)
            if year is not None and year not in years:
                years.append(year)

    rows_by_table: dict[str, dict[str, Mapping[str, Any]]] = {}

    for table_key in ("balance", "income", "cash_flow"):
        table = tables.get(table_key)
        if not isinstance(table, Mapping):
            continue

        table_years = table.get("years", [])
        if isinstance(table_years, (list, tuple)):
            for item in table_years:
                year = _to_year(item)
                if year is not None and year not in years:
                    years.append(year)

        rows = table.get("rows", [])
        if not isinstance(rows, (list, tuple)):
            continue

        table_rows: dict[str, Mapping[str, Any]] = {}
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            item_name = str(row.get("项目", "")).strip()
            if item_name:
                table_rows[item_name] = row
        if table_rows:
            rows_by_table[table_key] = table_rows

    years.sort()

    metrics: dict[str, dict[int, float]] = {}
    for definition in _METRIC_DEFINITIONS:
        table_rows = rows_by_table.get(definition.table, {})

        # 按别名优先级找科目。例如优先“营业收入”，
        # 找不到时才退回“营业总收入”。
        row = None
        for alias in definition.aliases:
            candidate = table_rows.get(alias)
            if candidate is not None:
                row = candidate
                break

        if row is None:
            continue

        series: dict[int, float] = {}
        for year in years:
            cell = row.get(str(year))
            if cell is None:
                cell = row.get(year)
            number = _parse_number(cell)
            if number is not None:
                series[year] = number

        if series:
            metrics[definition.key] = series

    if not metrics:
        raise ValueError("三张表中没有可用于趋势计算的核心指标。")

    return {
        "unit": "亿元",
        "years": years,
        "metrics": metrics,
        "source": "AKShare（新浪财经）近三年合并报表",
    }


# ---------------------------------------------------------------------------
# 计算与格式化小工具
# ---------------------------------------------------------------------------


def _format_amount(value: float | None) -> str:
    """把亿元金额格式化成带千分位的两位小数；无数据返回“—”。"""
    if value is None:
        return "—"

    # 避免显示“-0.00”这种看着像负数的零。
    if abs(value) < 0.005:
        value = 0.0

    return f"{value:,.2f}"


def _format_signed_amount(value: float) -> str:
    """
    把“金额变化”格式化成带正负号的两位小数。

    参数：
        value: 变化额，正数表示增加，负数表示减少。

    说明：
        和 _format_amount 分开，是因为绝对值只写“10.00”，
        而变化额必须写成“+10.00”或“-8.00”，让模型知道方向。
    """
    if abs(value) < 0.005:
        value = 0.0
    return f"{value:+,.2f}"


def _format_amount_change_line(
    points: list[tuple[int, float]],
) -> str:
    """
    生成“本年较上年增减多少亿元”的客观描述。

    参数：
        points: 按年份升序排列的 [(年份, 金额), ...]。

    返回：
        str: 例如“2024 年较 2023 年 +10.00 亿元；2025 年较 2024 年 +5.00 亿元”；
             没有相邻年份时返回“—”。

    说明：
        大模型看到“两年金额”时，常会自己心算差额，再写成“增加 10 亿元”。
        这里由 Python 直接给出差额，模型只需要引用，不需要做减法。
    """
    parts: list[str] = []
    for (prev_year, prev_value), (curr_year, curr_value) in zip(
        points, points[1:]
    ):
        parts.append(
            f"{curr_year} 年较 {prev_year} 年 "
            f"{_format_signed_amount(curr_value - prev_value)} 亿元"
        )
    return "；".join(parts) if parts else "—"


def _format_percent(value: float | None, signed: bool = True) -> str:
    """
    把百分比格式化成两位小数。

    参数：
        value: 百分比数值，例如 7.238 表示 7.238%。
        signed: 是否强制显示正负号。同比和 CAGR 用 True，
                毛利率、净利率这类水平值用 False。
    """
    if value is None:
        return "—"

    if abs(value) < 0.005:
        value = 0.0

    if signed:
        return f"{value:+.2f}%"
    return f"{value:.2f}%"


def _format_ratio(value: float | None) -> str:
    """把“倍”数格式化成两位小数；无数据返回“—”。"""
    if value is None:
        return "—"

    if abs(value) < 0.005:
        value = 0.0

    return f"{value:.2f} 倍"


def _format_pp(value: float | None) -> str:
    """把百分点变化格式化成带正负号的两位小数。"""
    if value is None:
        return "—"

    if abs(value) < 0.005:
        value = 0.0

    return f"{value:+.2f} 个百分点"


def _format_ratio_change(value: float | None, unit: str) -> str:
    """
    格式化比率指标的年度变化。

    参数：
        value: 变化量。
        unit: “%”表示百分比指标，用“个百分点”；
              其他单位（例如“倍”）直接使用该单位。

    说明：
        毛利率从 40% 降到 38%，变化是 -2 个百分点，不能写成 -2%；
        经营现金流/净利润从 1.39 倍降到 1.00 倍，变化是 -0.39 倍，
        也不能写成 -0.39 个百分点。
    """
    if unit == "%":
        return _format_pp(value)

    if value is None:
        return "—"

    if abs(value) < 0.005:
        value = 0.0

    return f"{value:+.2f} {unit}"


def _available_points(
    years: list[int],
    series: Mapping[int, float] | None,
) -> list[tuple[int, float]]:
    """按年份升序取出有实际数值的数据点。"""
    if not series:
        return []
    return [
        (year, series[year])
        for year in years
        if year in series
    ]


def _change_points(
    points: list[tuple[int, float]],
) -> list[tuple[int, int, float]]:
    """
    计算相邻数据点的变化百分比。

    参数：
        points: 按年份升序排列的 [(年份, 数值), ...]。

    返回：
        list[tuple[int, int, float]]:
            每项为 (本年, 上年, 变化百分比)。

    说明：
        - 上年为 0 时无法计算，直接跳过；
        - 分母取绝对值，保证“上年为负数”时正负号仍然符合直觉：
          例如 -10 → -5，计算为 +50%，表示亏损收窄。
    """
    changes: list[tuple[int, int, float]] = []
    for (prev_year, prev_value), (curr_year, curr_value) in zip(
        points, points[1:]
    ):
        if prev_value == 0:
            continue
        change = (curr_value - prev_value) / abs(prev_value) * 100
        changes.append((curr_year, prev_year, change))
    return changes


def _calculate_cagr(
    points: list[tuple[int, float]],
) -> tuple[float | None, int | None, int | None]:
    """
    计算首末两个数据点之间的复合增长率（CAGR）。

    参数：
        points: 按年份升序排列的 [(年份, 数值), ...]。

    返回：
        tuple:
            - CAGR 百分比；无法计算时为 None；
            - 起始年份；
            - 结束年份。

    说明：
        公式为 (末年 ÷ 首年) ^ (1 ÷ 间隔年数) - 1。
        只有首年和末年都大于 0 时才计算；负数或零做分母在数学上
        没有稳定的复合增长率含义，硬算会得出误导性结论。
    """
    if len(points) < 2:
        return None, None, None

    start_year, start_value = points[0]
    end_year, end_value = points[-1]
    periods = end_year - start_year

    if periods <= 0 or start_value <= 0 or end_value <= 0:
        return None, start_year, end_year

    try:
        cagr = ((end_value / start_value) ** (1 / periods) - 1) * 100
    except (OverflowError, ValueError, ZeroDivisionError):
        return None, start_year, end_year

    if math.isnan(cagr) or math.isinf(cagr):
        return None, start_year, end_year

    return cagr, start_year, end_year


def _describe_amount_trend(points: list[tuple[int, float]]) -> str:
    """
    用客观语言描述金额指标是连续增长、连续下降还是有升有降。

    参数：
        points: 按年份升序排列的 [(年份, 数值), ...]。

    返回：
        str: 不含原因判断的数学特征描述。
    """
    if len(points) < 2:
        return "只有 1 个年度有数据，无法判断连续变化。"

    values = [value for _, value in points]
    differences = [
        current - previous
        for previous, current in zip(values, values[1:])
    ]

    if all(diff > 0 for diff in differences):
        direction = f"连续 {len(differences)} 个年度增长"
    elif all(diff < 0 for diff in differences):
        direction = f"连续 {len(differences)} 个年度下降"
    elif all(diff == 0 for diff in differences):
        direction = f"连续 {len(differences)} 个年度持平"
    else:
        direction = "年度之间有升有降"

    parts = [direction]

    changes = _change_points(points)
    if len(changes) >= 2:
        last_change = changes[-1][2]
        previous_change = changes[-2][2]
        difference = last_change - previous_change
        if abs(difference) >= 0.005:
            word = "上升" if difference > 0 else "下降"
            parts.append(
                f"最近一次变化幅度较上次{word} "
                f"{abs(difference):.2f} 个百分点"
            )
        else:
            parts.append("最近两次变化幅度基本持平")

    return "；".join(parts) + "。"


def _describe_ratio_trend(
    points: list[tuple[int, float]],
    unit: str = "%",
) -> str:
    """
    用客观语言描述比率指标（毛利率、资产负债率等）的年度变化。

    参数：
        points: 按年份升序排列的 [(年份, 比率数值), ...]。
        unit: “%”表示百分比指标，用“个百分点”；
              其他单位（例如“倍”）直接使用该单位。

    返回：
        str: 以“百分点”或对应单位描述变化，不做原因判断。
    """
    if len(points) < 2:
        return "只有 1 个年度有数据，无法判断连续变化。"

    change_unit = "个百分点" if unit == "%" else unit

    changes: list[float] = []
    for (_, previous), (_, current) in zip(points, points[1:]):
        changes.append(current - previous)

    if all(change < 0 for change in changes):
        total = sum(changes)
        return (
            f"连续 {len(changes)} 个年度下降，"
            f"累计下降 {abs(total):.2f} {change_unit}。"
        )

    if all(change > 0 for change in changes):
        total = sum(changes)
        return (
            f"连续 {len(changes)} 个年度上升，"
            f"累计上升 {abs(total):.2f} {change_unit}。"
        )

    if all(abs(change) < 0.005 for change in changes):
        return f"连续 {len(changes)} 个年度基本持平。"

    last_change = changes[-1]
    word = "上升" if last_change > 0 else "下降"
    return (
        "年度之间有升有降；"
        f"最近一次较上次{word} {abs(last_change):.2f} {change_unit}。"
    )


def _format_change_line(
    changes: list[tuple[int, int, float]],
) -> str:
    """
    把变化百分比列表格式化成一行文字。

    参数：
        changes: [(本年, 上年, 变化百分比), ...]。

    返回：
        str: 例如“2024 年同比 +10.00%；2025 年同比 +4.55%”。
    """
    if not changes:
        return "—"

    parts: list[str] = []
    for curr_year, prev_year, value in changes:
        if curr_year == prev_year + 1:
            label = f"{curr_year} 年同比"
        else:
            label = f"{curr_year} 年较 {prev_year} 年"
        parts.append(f"{label} {_format_percent(value)}")

    return "；".join(parts)


def _first_series(
    metrics: Mapping[str, Mapping[int, float]],
    *keys: str,
) -> Mapping[int, float] | None:
    """按顺序返回第一个存在的指标序列。"""
    for key in keys:
        series = metrics.get(key)
        if series:
            return series
    return None


def _latest_change(
    points: list[tuple[int, float]],
) -> tuple[int, int, float] | None:
    """返回最后一次变化；没有可计算的变化时返回 None。"""
    changes = _change_points(points)
    return changes[-1] if changes else None


def _average_balance(
    series: Mapping[int, float],
    year: int,
    years: list[int],
) -> float | None:
    """
    计算某一年度的平均余额。

    参数：
        series: 余额指标，例如应收账款、存货、所有者权益。
        year: 当前年份。
        years: 全部年份（升序）。

    返回：
        float | None: 有上年数时返回（上年 + 本年）÷ 2；
                      没有上年数时返回本年期末数；本年没有数时返回 None。

    说明：
        周转率和 ROE 用平均余额更符合财务分析口径；
        但如果只有期末数，使用期末数并在指标名称中注明，
        不能用一个不存在的历史数字硬凑平均值。
    """
    if year not in series:
        return None

    current = series[year]
    try:
        index = years.index(year)
    except ValueError:
        return current

    if index > 0:
        previous_year = years[index - 1]
        previous = series.get(previous_year)
        if previous is not None:
            return (previous + current) / 2

    return current


def _safe_divide(numerator: float, denominator: float) -> float | None:
    """安全除法：分母为 0 或结果不是有限数时返回 None。"""
    if denominator == 0:
        return None
    result = numerator / denominator
    if math.isnan(result) or math.isinf(result):
        return None
    return result


def _format_ratio_value(value: float | None, unit: str) -> str:
    """按指标单位格式化比率数值。"""
    if value is None:
        return "—"

    if unit == "%":
        return _format_percent(value, signed=False)
    if unit == "倍":
        return _format_ratio(value)
    if unit == "次":
        return f"{value:.2f} 次"
    if unit == "天":
        return f"{value:.2f} 天"
    return f"{value:.2f}"


def _build_auxiliary_ratios(
    years: list[int],
    metrics: Mapping[str, Mapping[int, float]],
) -> list[tuple[str, dict[int, float], str]]:
    """
    计算各分析维度会用到的其他比率指标。

    参数：
        years: 年份升序列表。
        metrics: 已换算成亿元的指标字典。

    返回：
        list[tuple[str, dict[int, float], str]]:
            每项为 (指标名称, {年份: 数值}, 单位)。

    说明：
        这些指标都是现有财务专家模板会要求的：
        - 偿债能力：流动比率、速动比率、利息保障倍数；
        - 盈利能力：ROE；
        - 营运能力：应收账款/存货周转率与周转天数。
        把它们提前算好，模型就不需要自己列公式。
        任何分子或分母缺失的年份都跳过，不用 0 代替。
    """
    ratios: list[tuple[str, dict[int, float], str]] = []

    revenue = metrics.get("营业收入")
    cost = metrics.get("营业成本")
    net_profit = metrics.get("归母净利润")
    current_assets = metrics.get("流动资产合计")
    current_liabilities = metrics.get("流动负债合计")
    inventory = metrics.get("存货")
    receivables = metrics.get("应收账款")
    total_assets = metrics.get("资产总计")
    total_liabilities = metrics.get("负债合计")
    equity = metrics.get("所有者权益合计")
    profit_total = metrics.get("利润总额")
    interest_expense = metrics.get("利息费用")

    # 如果没有单独披露所有者权益合计，用“资产总计 − 负债合计”兜底。
    # 这是会计恒等式，属于确定性计算，不是估算。
    if not equity and total_assets and total_liabilities:
        equity = {}
        for year in years:
            if year in total_assets and year in total_liabilities:
                equity[year] = (
                    total_assets[year] - total_liabilities[year]
                )

    # 流动比率 = 流动资产合计 ÷ 流动负债合计。
    if current_assets and current_liabilities:
        values: dict[int, float] = {}
        for year in years:
            if year not in current_assets or year not in current_liabilities:
                continue
            value = _safe_divide(
                current_assets[year],
                current_liabilities[year],
            )
            if value is not None:
                values[year] = value
        if values:
            ratios.append(("流动比率", values, "倍"))

    # 速动比率 =（流动资产合计 − 存货）÷ 流动负债合计。
    if current_assets and current_liabilities and inventory:
        values = {}
        for year in years:
            if (
                year not in current_assets
                or year not in current_liabilities
                or year not in inventory
            ):
                continue
            value = _safe_divide(
                current_assets[year] - inventory[year],
                current_liabilities[year],
            )
            if value is not None:
                values[year] = value
        if values:
            ratios.append(("速动比率", values, "倍"))

    # ROE = 归母净利润 ÷ 平均所有者权益合计。
    # 所有者权益为负时 ROE 没有稳定含义，直接跳过。
    if net_profit and equity:
        values = {}
        for year in years:
            if year not in net_profit:
                continue
            average_equity = _average_balance(equity, year, years)
            if average_equity is None or average_equity <= 0:
                continue
            value = _safe_divide(
                net_profit[year] * 100,
                average_equity,
            )
            if value is not None:
                values[year] = value
        if values:
            ratios.append(
                ("ROE（平均所有者权益合计）", values, "%")
            )

    # 应收账款周转率 = 营业收入 ÷ 平均应收账款。
    if revenue and receivables:
        turnover_values: dict[int, float] = {}
        day_values: dict[int, float] = {}
        for year in years:
            if year not in revenue:
                continue
            average_receivables = _average_balance(
                receivables, year, years
            )
            if average_receivables is None or average_receivables <= 0:
                continue
            value = _safe_divide(
                revenue[year],
                average_receivables,
            )
            if value is None:
                continue
            turnover_values[year] = value
            day_values[year] = 365 / value
        if turnover_values:
            ratios.append(
                ("应收账款周转率（次）", turnover_values, "次")
            )
            ratios.append(
                ("应收账款周转天数", day_values, "天")
            )

    # 存货周转率 = 营业成本 ÷ 平均存货。
    if cost and inventory:
        turnover_values = {}
        day_values = {}
        for year in years:
            if year not in cost:
                continue
            average_inventory = _average_balance(
                inventory, year, years
            )
            if average_inventory is None or average_inventory <= 0:
                continue
            value = _safe_divide(cost[year], average_inventory)
            if value is None:
                continue
            turnover_values[year] = value
            day_values[year] = 365 / value
        if turnover_values:
            ratios.append(
                ("存货周转率（次）", turnover_values, "次")
            )
            ratios.append(
                ("存货周转天数", day_values, "天")
            )

    # 利息保障倍数 =（利润总额 + 利息费用）÷ 利息费用。
    if profit_total and interest_expense:
        values = {}
        for year in years:
            if year not in profit_total or year not in interest_expense:
                continue
            interest = interest_expense[year]
            if interest <= 0:
                continue
            value = _safe_divide(
                profit_total[year] + interest,
                interest,
            )
            if value is not None:
                values[year] = value
        if values:
            ratios.append(("利息保障倍数", values, "倍"))

    return ratios


# ---------------------------------------------------------------------------
# 主函数
# ---------------------------------------------------------------------------


def calculate_trends(raw_data: dict) -> str:
    """
    计算近三年核心财务趋势，并输出一段结构化的纯文本摘要。

    参数：
        raw_data: 近三年核心财务数据。推荐格式：
            {
                "unit": "亿元",
                "years": [2023, 2024, 2025],
                "metrics": {
                    "营业收入": {2023: 100.0, 2024: 110.0, 2025: 115.0},
                    "归母净利润": {2023: 18.0, 2024: 20.0, 2025: 19.0},
                    "经营活动现金流量净额": {
                        2023: 25.0, 2024: 20.0, 2025: 12.0
                    },
                },
                "source": "AKShare",
            }
            金额单位支持“元”“千元”“万元”“亿元”，默认“亿元”。

    返回：
        str: 一段 Markdown 纯文本摘要，包含：
            - 每年数值；
            - 同比变化；
            - 三年 CAGR（仅金额指标）；
            - 数学特征（连续增长、连续下降、有升有降）；
            - 关键数学事实（例如营收与经营现金流方向相反）。

    异常：
        ValueError: 输入为空、没有指标或少于两个年度时抛出。

    重要说明：
        本函数不写任何主观分析：
        - 不判断“价格战”“成本失控”“经营恶化”；
        - 不推测原因，不给建议；
        - 只陈述可复核的数字和数学关系。
        这些原因判断留给大模型，但大模型只能引用这里的数字。
    """
    years, metrics, input_unit, source = _normalize_raw_data(raw_data)

    # 统一换算成亿元。输入已经是亿元时 divisor 为 1，不会改变数值。
    divisor = _UNIT_DIVISORS.get(input_unit, 1.0)
    converted: dict[str, dict[int, float]] = {}
    for name, series in metrics.items():
        converted[name] = {
            year: value / divisor
            for year, value in series.items()
        }

    lines: list[str] = [
        "【三年核心财务趋势摘要（由 Python 精确计算，禁止模型重算）】",
        f"数据来源：{source or '调用方提供的三年财务数据'}",
        "数据期间：" + "、".join(str(year) for year in years) + "（年份升序）",
        "金额单位：亿元；比率单位：百分比（%）；倍数单位：倍。",
        "计算口径：",
        "- 同比/年度变化 =（本年 − 上年）÷ |上年| × 100%，保留 2 位小数；",
        "- 金额变化额 = 本年 − 上年，单位为亿元，正数表示增加、负数表示减少；",
        "- CAGR =（末年 ÷ 首年）^(1 ÷ 间隔年数) − 1；"
        "仅当首末两年都大于 0 时计算；",
        "- 比率类指标的年度变化用“百分点”表示；",
        "- “—”表示基期为 0、年度缺失或无法计算，"
        "不得猜测，也不得用其他年份数字替代。",
        "",
    ]

    # 先输出三个最重要的金额指标，再输出盈利质量、偿债和营运指标。
    amount_sections: tuple[tuple[str, str], ...] = (
        ("营业收入", "营业收入"),
        ("营业成本", "营业成本"),
        ("归母净利润", "归母净利润"),
        ("经营活动现金流量净额", "经营活动现金流量净额"),
    )

    section_number = 0
    for key, label in amount_sections:
        series = converted.get(key)
        if not series:
            continue

        section_number += 1
        points = _available_points(years, series)
        changes = _change_points(points)
        cagr, start_year, end_year = _calculate_cagr(points)

        lines.append(f"## {section_number}、{label}（亿元）")
        lines.append(
            "- 年度值：" + "；".join(
                f"{year}={_format_amount(series.get(year))}"
                for year in years
            )
        )
        lines.append(f"- 年度变化：{_format_change_line(changes)}")
        lines.append(
            f"- 年度变化额：{_format_amount_change_line(points)}"
        )

        if cagr is not None and start_year is not None and end_year is not None:
            periods = end_year - start_year
            lines.append(
                f"- {start_year}→{end_year} CAGR："
                f"{_format_percent(cagr)}（间隔 {periods} 年）"
            )
        else:
            lines.append(f"- {years[0]}→{years[-1]} CAGR：—")

        lines.append(f"- 数学特征：{_describe_amount_trend(points)}")
        lines.append("")

    # 派生比率：毛利率、归母净利率、经营现金流/归母净利润、资产负债率。
    revenue = _first_series(converted, "营业收入")
    cost = _first_series(converted, "营业成本")
    net_profit = _first_series(converted, "归母净利润")
    operating_cash = _first_series(converted, "经营活动现金流量净额")
    total_assets = _first_series(converted, "资产总计")
    total_liabilities = _first_series(converted, "负债合计")

    ratio_sections: list[tuple[str, dict[int, float], str, str]] = []

    if revenue and cost:
        gross_margin: dict[int, float] = {}
        for year in years:
            if year not in revenue or year not in cost:
                continue
            if revenue[year] <= 0:
                continue
            gross_margin[year] = (
                (revenue[year] - cost[year]) / revenue[year] * 100
            )
        if gross_margin:
            ratio_sections.append(
                ("毛利率", gross_margin, "%", "毛利率")
            )

    if revenue and net_profit:
        net_margin: dict[int, float] = {}
        for year in years:
            if year not in revenue or year not in net_profit:
                continue
            if revenue[year] <= 0:
                continue
            net_margin[year] = net_profit[year] / revenue[year] * 100
        if net_margin:
            ratio_sections.append(
                ("归母净利率", net_margin, "%", "归母净利率")
            )

    if net_profit and operating_cash:
        cash_quality: dict[int, float] = {}
        for year in years:
            if year not in net_profit or year not in operating_cash:
                continue
            # 净利润为 0 或负数时，倍数没有稳定含义，直接跳过。
            if net_profit[year] <= 0:
                continue
            cash_quality[year] = (
                operating_cash[year] / net_profit[year]
            )
        if cash_quality:
            ratio_sections.append(
                (
                    "经营现金流净额/归母净利润",
                    cash_quality,
                    "倍",
                    "经营现金流净额/归母净利润",
                )
            )

    if total_assets and total_liabilities:
        debt_ratio: dict[int, float] = {}
        for year in years:
            if year not in total_assets or year not in total_liabilities:
                continue
            if total_assets[year] <= 0:
                continue
            debt_ratio[year] = (
                total_liabilities[year] / total_assets[year] * 100
            )
        if debt_ratio:
            ratio_sections.append(
                ("资产负债率", debt_ratio, "%", "资产负债率")
            )

    for label, series, unit, _ in ratio_sections:
        section_number += 1
        points = _available_points(years, series)
        lines.append(f"## {section_number}、{label}（{unit}）")

        if unit == "%":
            values_text = "；".join(
                f"{year}={_format_percent(series.get(year), signed=False)}"
                for year in years
            )
        else:
            values_text = "；".join(
                f"{year}={_format_ratio(series.get(year))}"
                for year in years
            )

        lines.append(f"- 年度值：{values_text}")

        # 比率指标的变化用“百分点”描述，不能写成“同比 +5%”，
        # 否则会把“从 40% 降到 38%”误读成“下降 5%”。
        if len(points) >= 2:
            ratio_changes = [
                f"{curr_year} 年较 {prev_year} 年 "
                f"{_format_ratio_change(curr_value - prev_value, unit)}"
                for (prev_year, prev_value), (curr_year, curr_value)
                in zip(points, points[1:])
            ]
            lines.append("- 年度变化：" + "；".join(ratio_changes))
        else:
            lines.append("- 年度变化：—")

        lines.append(
            f"- 数学特征：{_describe_ratio_trend(points, unit)}"
        )
        lines.append("")

    # 营运相关金额指标：应收账款和存货。
    # 单独放在后面，方便模型把“营收增长”与“应收/存货增长”做对照。
    for key, label in (("应收账款", "应收账款"), ("存货", "存货")):
        series = converted.get(key)
        if not series:
            continue

        section_number += 1
        points = _available_points(years, series)
        changes = _change_points(points)
        cagr, start_year, end_year = _calculate_cagr(points)

        lines.append(f"## {section_number}、{label}（亿元）")
        lines.append(
            "- 年度值：" + "；".join(
                f"{year}={_format_amount(series.get(year))}"
                for year in years
            )
        )
        lines.append(f"- 年度变化：{_format_change_line(changes)}")
        lines.append(
            f"- 年度变化额：{_format_amount_change_line(points)}"
        )

        if cagr is not None and start_year is not None and end_year is not None:
            periods = end_year - start_year
            lines.append(
                f"- {start_year}→{end_year} CAGR："
                f"{_format_percent(cagr)}（间隔 {periods} 年）"
            )
        else:
            lines.append(f"- {years[0]}→{years[-1]} CAGR：—")

        lines.append(f"- 数学特征：{_describe_amount_trend(points)}")
        lines.append("")

    # ------------------------------------------------------------------
    # 其他维度指标：把偿债、ROE、周转率提前算好。
    # 用一张紧凑表格展示，模型可以直接引用，不需要自己列公式。
    # ------------------------------------------------------------------
    auxiliary_ratios = _build_auxiliary_ratios(years, converted)
    if auxiliary_ratios:
        lines.append("## 其他维度指标（由 Python 计算，禁止重算）")
        lines.append(
            "| 指标 | "
            + " | ".join(str(year) for year in years)
            + " | 最近一次变化 |"
        )
        lines.append(
            "| --- | "
            + " | ".join("---:" for _ in years)
            + " | --- |"
        )

        for label, series, unit in auxiliary_ratios:
            values_text = " | ".join(
                _format_ratio_value(series.get(year), unit)
                for year in years
            )
            points = _available_points(years, series)
            if len(points) >= 2:
                (prev_year, prev_value), (curr_year, curr_value) = (
                    points[-2],
                    points[-1],
                )
                change_text = (
                    f"{curr_year} 年较 {prev_year} 年 "
                    f"{_format_ratio_change(curr_value - prev_value, unit)}"
                )
            else:
                change_text = "—"

            lines.append(
                f"| {label} | {values_text} | {change_text} |"
            )

        lines.append("")
        lines.append(
            "口径：ROE 优先按平均所有者权益合计计算，没有上年数时使用期末数；"
            "周转率优先按平均余额计算。"
        )
        lines.append("")

    # ------------------------------------------------------------------
    # 关键数学事实：只陈述“谁和谁方向相反/谁高于谁”，不解释原因。
    # 这些事实是模型做深度分析时的抓手，避免它自己乱算。
    # ------------------------------------------------------------------
    facts: list[str] = []

    revenue_points = _available_points(years, revenue)
    operating_cash_points = _available_points(years, operating_cash)
    net_profit_points = _available_points(years, net_profit)

    revenue_change = _latest_change(revenue_points)
    operating_cash_change = _latest_change(operating_cash_points)
    net_profit_change = _latest_change(net_profit_points)

    if revenue_change and operating_cash_change:
        if revenue_change[2] > 0 > operating_cash_change[2]:
            facts.append(
                "最近一次年度变化：营业收入 "
                f"{_format_percent(revenue_change[2])}，"
                "经营活动现金流量净额 "
                f"{_format_percent(operating_cash_change[2])}，"
                "两者方向相反。"
            )
        elif revenue_change[2] < 0 < operating_cash_change[2]:
            facts.append(
                "最近一次年度变化：营业收入 "
                f"{_format_percent(revenue_change[2])}，"
                "经营活动现金流量净额 "
                f"{_format_percent(operating_cash_change[2])}，"
                "两者方向相反。"
            )

    if net_profit_change and operating_cash_change:
        if net_profit_change[2] > 0 > operating_cash_change[2]:
            facts.append(
                "最近一次年度变化：归母净利润 "
                f"{_format_percent(net_profit_change[2])}，"
                "经营活动现金流量净额 "
                f"{_format_percent(operating_cash_change[2])}，"
                "两者方向相反。"
            )
        elif net_profit_change[2] < 0 < operating_cash_change[2]:
            facts.append(
                "最近一次年度变化：归母净利润 "
                f"{_format_percent(net_profit_change[2])}，"
                "经营活动现金流量净额 "
                f"{_format_percent(operating_cash_change[2])}，"
                "两者方向相反。"
            )

    gross_margin_series = next(
        (series for label, series, _, _ in ratio_sections if label == "毛利率"),
        None,
    )
    if gross_margin_series:
        gross_margin_points = _available_points(years, gross_margin_series)
        gross_margin_changes = [
            current - previous
            for (_, previous), (_, current)
            in zip(gross_margin_points, gross_margin_points[1:])
        ]
        if len(gross_margin_changes) >= 2 and all(
            change < 0 for change in gross_margin_changes
        ):
            total_change = sum(gross_margin_changes)
            facts.append(
                f"毛利率连续 {len(gross_margin_changes)} 个年度下降，"
                f"累计下降 {abs(total_change):.2f} 个百分点。"
            )

    if net_profit_points:
        net_profit_changes = [
            current - previous
            for (_, previous), (_, current)
            in zip(net_profit_points, net_profit_points[1:])
        ]
        if len(net_profit_changes) >= 2 and all(
            change < 0 for change in net_profit_changes
        ):
            facts.append(
                f"归母净利润连续 {len(net_profit_changes)} 个年度下降。"
            )

    cash_quality_series = next(
        (
            series
            for label, series, _, _ in ratio_sections
            if label == "经营现金流净额/归母净利润"
        ),
        None,
    )
    if cash_quality_series:
        latest_cash_year = max(cash_quality_series)
        latest_cash_value = cash_quality_series[latest_cash_year]
        if latest_cash_value < 1:
            facts.append(
                f"{latest_cash_year} 年经营现金流净额/归母净利润="
                f"{latest_cash_value:.2f} 倍，低于 1 倍。"
            )

    receivable = converted.get("应收账款")
    if revenue and receivable:
        receivable_points = _available_points(years, receivable)
        receivable_change = _latest_change(receivable_points)
        if receivable_change and revenue_change:
            if receivable_change[2] > revenue_change[2]:
                facts.append(
                    "最近一次年度变化：应收账款 "
                    f"{_format_percent(receivable_change[2])}，"
                    "高于营业收入 "
                    f"{_format_percent(revenue_change[2])}。"
                )

    inventory = converted.get("存货")
    if revenue and inventory:
        inventory_points = _available_points(years, inventory)
        inventory_change = _latest_change(inventory_points)
        if inventory_change and revenue_change:
            if inventory_change[2] > revenue_change[2]:
                facts.append(
                    "最近一次年度变化：存货 "
                    f"{_format_percent(inventory_change[2])}，"
                    "高于营业收入 "
                    f"{_format_percent(revenue_change[2])}。"
                )

    # 应收账款与存货的“合计增量”也由 Python 算好。
    # 模型分析营运资金占用时，常会自己把两项增量相加；
    # 这里直接给出合计值，并和经营现金流净额做客观比较，
    # 避免模型在“合计”“超过”这类表述中偷偷做加法。
    if receivable and inventory:
        receivable_points = _available_points(years, receivable)
        inventory_points = _available_points(years, inventory)
        if len(receivable_points) >= 2 and len(inventory_points) >= 2:
            (rec_prev_year, rec_prev), (rec_curr_year, rec_curr) = (
                receivable_points[-2:]
            )
            (inv_prev_year, inv_prev), (inv_curr_year, inv_curr) = (
                inventory_points[-2:]
            )
            if (
                rec_prev_year == inv_prev_year
                and rec_curr_year == inv_curr_year
            ):
                receivable_delta = rec_curr - rec_prev
                inventory_delta = inv_curr - inv_prev
                combined_delta = receivable_delta + inventory_delta
                fact = (
                    f"{rec_curr_year} 年较 {rec_prev_year} 年：应收账款变化 "
                    f"{_format_signed_amount(receivable_delta)} 亿元，"
                    f"存货变化 {_format_signed_amount(inventory_delta)} 亿元，"
                    f"两者合计变化 {_format_signed_amount(combined_delta)} 亿元。"
                )
                if operating_cash and rec_curr_year in operating_cash:
                    latest_cash = operating_cash[rec_curr_year]
                    difference = combined_delta - latest_cash
                    if difference >= 0:
                        comparison = (
                            f"同期经营活动现金流量净额 "
                            f"{_format_amount(latest_cash)} 亿元，"
                            f"合计变化额比经营现金流净额高 "
                            f"{_format_amount(difference)} 亿元。"
                        )
                    else:
                        comparison = (
                            f"同期经营活动现金流量净额 "
                            f"{_format_amount(latest_cash)} 亿元，"
                            f"合计变化额比经营现金流净额低 "
                            f"{_format_amount(abs(difference))} 亿元。"
                        )
                    fact += comparison
                facts.append(fact)

    if facts:
        lines.append("## 关键数学事实（只陈述数字关系，不做因果判断）")
        lines.extend(f"- {fact}" for fact in facts)
        lines.append("")
    else:
        lines.append("## 关键数学事实（只陈述数字关系，不做因果判断）")
        lines.append("- 本期没有检测到上述指标之间的方向背离或连续同向变化。")
        lines.append("")

    return "\n".join(lines).strip() + "\n"
