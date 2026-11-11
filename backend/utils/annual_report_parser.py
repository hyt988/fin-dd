"""
官方年报 PDF 解析模块。

职责：
1. 用 pdfplumber 读取巨潮年报 PDF 的文字和表格；
2. 从“主要会计数据和财务指标”等章节提取核心数字；
3. 识别审计意见、会计师事务所、分红方案；
4. 截取关联交易、担保、诉讼、经营讨论、风险等章节，供法务/业务专家使用；
5. 给每个数字和章节保留原文页码，方便人工核对。

设计原则：
- 数字优先由程序按固定规则提取，不交给大模型“凭印象”生成；
- 提取不到就明确留空或标注“未识别”，绝不猜数字；
- 只读取 PDF，不执行 PDF 里的任何脚本，也不访问外部链接；
- 控制页数、表格数量和文字长度，避免一份 200 页年报拖垮页面。

说明：
- 本模块不调用 DeepSeek，也不读取 API Key；
- 本模块只负责“把 PDF 变成结构化数据”，
  给三个智能体拼接提示词的工作在 utils/annual_report_service.py 中完成。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from io import BytesIO
from typing import Any

# ---------------------------------------------------------------------------
# 解析限制
# ---------------------------------------------------------------------------

# 最多解析多少页。沪深年报通常 100～250 页，300 页可以覆盖绝大多数。
MAX_PDF_PAGES = 300

# 超过 300 页时，前 150 页和后 150 页各取一部分。
# 年报的主要财务数据通常在开头，财务报表和审计报告通常在结尾。
PDF_FRONT_PAGES = 150
PDF_BACK_PAGES = 150

# 一份 PDF 至少要有这么多有效文字，否则大概率是扫描图片版。
MIN_TEXT_CHARS = 500

# 单个页面最多保留多少字符，防止某一页异常长。
MAX_PAGE_TEXT_CHARS = 20_000

# 最多保留多少张表格、每页最多几张、每张最多多少行多少列。
MAX_TABLES = 12
MAX_TABLES_PER_PAGE = 3
MAX_TABLE_ROWS = 30
MAX_TABLE_COLS = 8

# 每个章节最多截取多少字符。
MAX_SECTION_CHARS = 3_000

# 审计意见段落通常更长一些，单独放宽到 4,000 字符。
MAX_AUDIT_SECTION_CHARS = 4_000


class AnnualReportParseError(RuntimeError):
    """
    年报 PDF 解析失败时抛出的中文异常。

    例如：文件为空、不是 PDF、需要密码、是扫描图片版、
    没有文字层或解析过程出现异常。异常文字可直接展示给用户。
    """


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------


@dataclass
class FinancialMetric:
    """
    一个财务指标。

    属性：
        label: 指标名称，例如“营业收入”。
        value: 本期数值；没有识别到时为 None。
        display_value: 给页面显示的本期数值，例如 "1,688,381,025.79"。
        prior_value: 上期数值；没有识别到时为 None。
        prior_display_value: 给页面显示的上期数值。
        change: 同比变化，例如 "-1.21%" 或 "-3.49 个百分点"。
        unit: 单位，例如“元”“%”“元/股”。
        page: 原文页码，从 1 开始；未识别时为 0。
        note: 解析说明，例如“未识别到该指标”。
    """

    label: str
    value: float | None
    display_value: str
    prior_value: float | None = None
    prior_display_value: str = ""
    change: str = ""
    unit: str = "元"
    page: int = 0
    note: str = ""


@dataclass
class ReportTable:
    """
    从年报中提取的一张表格。

    属性：
        title: 表格标题，例如“营业收入构成”。
        markdown: 转换成 Markdown 的表格内容。
        page: 原文页码。
    """

    title: str
    markdown: str
    page: int


@dataclass
class ReportSection:
    """
    从年报中截取的一段文字。

    属性：
        title: 章节名称，例如“审计意见”。
        text: 截取后的正文。
        page_start: 起始页码。
        page_end: 结束页码。
    """

    title: str
    text: str
    page_start: int
    page_end: int


@dataclass
class ParsedAnnualReport:
    """
    一份年报解析后的完整结果。

    属性：
        page_count: PDF 总页数。
        parsed_pages: 实际读取到文字的页数。
        text_char_count: 提取到的有效文字总字符数。
        metrics: 核心财务指标列表。
        quarterly: 分季度数据；无法结构化时为空列表，原始表格仍会保留在 tables 中。
        tables: 关键表格列表。
        sections: 关键章节字典，键例如 "audit"、"risk"。
        audit_opinion: 审计意见类型。
        auditor: 会计师事务所名称。
        audit_date: 审计报告日期。
        dividend_text: 分红方案摘录。
        warnings: 给用户看的中文提示。
    """

    page_count: int
    parsed_pages: int
    text_char_count: int
    metrics: list[FinancialMetric] = field(default_factory=list)
    quarterly: list[dict[str, str]] = field(default_factory=list)
    tables: list[ReportTable] = field(default_factory=list)
    sections: dict[str, ReportSection] = field(default_factory=dict)
    audit_opinion: str = "未识别"
    auditor: str = ""
    audit_date: str = ""
    dividend_text: str = ""
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 文本与数字工具
# ---------------------------------------------------------------------------


def _normalize_text(text: str) -> str:
    """
    清理 PDF 提取出的文字。

    参数：
        text: pdfplumber 返回的原始文字。

    返回：
        str: 统一换行、删除空字符、压缩多余空白的文字。

    说明：
        PDF 文字里可能混入 \x00、连续空格和大量空行。
        不清理会影响正则匹配，也会让页面预览出现大片空白。
    """
    if not text:
        return ""

    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\x00", " ")

    lines: list[str] = []
    for line in text.split("\n"):
        clean_line = re.sub(r"[ \t\f\v]+", " ", line).strip()
        if clean_line:
            lines.append(clean_line)
        elif lines and lines[-1] != "":
            lines.append("")

    return "\n".join(lines).strip()


def _clean_cell(value: Any) -> str:
    """
    把表格单元格转换成可以放进 Markdown 的文本。

    参数：
        value: 单元格内容，可能是 None、数字或字符串。

    返回：
        str: 去掉换行、压缩空白、转义竖线后的文本。

    说明：
        Markdown 表格用竖线分列，单元格里的竖线必须转义，
        否则整张表会错位。
    """
    if value is None:
        return ""

    text = str(value)
    text = text.replace("\r\n", " ").replace("\r", " ").replace("\n", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text.replace("|", "\\|")


def _table_to_markdown(
    rows: list[list[Any]] | None,
    max_rows: int = MAX_TABLE_ROWS,
    max_cols: int = MAX_TABLE_COLS,
) -> str:
    """
    把二维表格转换成 Markdown。

    参数：
        rows: pdfplumber 返回的二维列表。
        max_rows: 最多保留多少行数据（不含表头）。
        max_cols: 最多保留多少列。

    返回：
        str: Markdown 表格；有效行不足时返回空字符串。

    说明：
        第一行作为表头，全空行会删除，过长的表格只保留前面部分。
        这样既能让大模型看懂表格，又不会让提示词无限增长。
    """
    if not rows:
        return ""

    cleaned_rows: list[list[str]] = []

    for raw_row in rows:
        if not isinstance(raw_row, (list, tuple)):
            continue

        cells = [
            _clean_cell(value)
            for value in list(raw_row)[:max_cols]
        ]

        # 去掉行尾空单元格，避免表格右边出现大片空白列。
        while cells and not cells[-1]:
            cells.pop()

        if any(cells):
            cleaned_rows.append(cells)

    if len(cleaned_rows) < 2:
        return ""

    width = max(len(row) for row in cleaned_rows)
    if width < 2:
        return ""

    normalized_rows = [
        row + [""] * (width - len(row))
        for row in cleaned_rows
    ]

    header = normalized_rows[0]
    data_rows = normalized_rows[1:]

    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join(["---"] * width) + " |",
    ]
    lines.extend(
        "| " + " | ".join(row) + " |"
        for row in data_rows[:max_rows]
    )

    if len(data_rows) > max_rows:
        lines.append(
            f"\n（表格较长，仅保留前 {max_rows} 行数据。）"
        )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 财务指标提取
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _MetricSpec:
    """
    一个财务指标的提取规则。

    属性：
        label: 输出给页面看的指标名称。
        aliases: 年报里可能出现的写法，按长度从长到短匹配。
        unit: 单位。
        is_percent: 是否是百分比指标（例如净资产收益率）。
        exclude_keywords: 行内出现这些词时跳过，避免选错相似指标。
    """

    label: str
    aliases: tuple[str, ...]
    unit: str = "元"
    is_percent: bool = False
    exclude_keywords: tuple[str, ...] = ()


# 核心财务指标规则。
# 这些指标覆盖财务尽调最常用的偿债、盈利、现金流和规模数据。
_METRIC_SPECS: tuple[_MetricSpec, ...] = (
    _MetricSpec(
        label="营业总收入",
        aliases=("营业总收入",),
    ),
    _MetricSpec(
        label="营业收入",
        aliases=("营业收入", "其中：营业收入"),
    ),
    _MetricSpec(
        label="利润总额",
        aliases=("利润总额",),
    ),
    _MetricSpec(
        label="归属于上市公司股东的净利润",
        aliases=(
            "归属于上市公司股东的净利润",
            "归属于母公司所有者的净利润",
            "归属于母公司股东的净利润",
            "归母净利润",
        ),
    ),
    _MetricSpec(
        label="归属于上市公司股东的扣除非经常性损益的净利润",
        aliases=(
            "归属于上市公司股东的扣除非经常性损益的净利润",
            "归属于母公司所有者的扣除非经常性损益的净利润",
            "扣除非经常性损益的净利润",
            "扣非净利润",
        ),
    ),
    _MetricSpec(
        label="经营活动产生的现金流量净额",
        aliases=(
            "经营活动产生的现金流量净额",
            "经营活动现金流量净额",
        ),
    ),
    _MetricSpec(
        label="基本每股收益",
        aliases=("基本每股收益", "每股收益"),
        unit="元/股",
        # “扣除非经常性损益后的基本每股收益”是另一个指标，
        # 不能混进基本每股收益，否则会把扣非每股收益当成基本每股收益。
        exclude_keywords=("扣除",),
    ),
    _MetricSpec(
        label="加权平均净资产收益率",
        aliases=("加权平均净资产收益率", "净资产收益率"),
        unit="%",
        is_percent=True,
        exclude_keywords=("扣除",),
    ),
    _MetricSpec(
        label="总资产",
        aliases=("总资产", "资产总额", "资产总计"),
        exclude_keywords=("周转率", "收益率"),
    ),
    _MetricSpec(
        label="归属于上市公司股东的净资产",
        aliases=(
            "归属于上市公司股东的净资产",
            "归属于母公司所有者权益",
            "归属于母公司股东的净资产",
            "归母净资产",
        ),
    ),
)


# 货币数字：允许千分位逗号、小数、括号负数。
# 前后加边界，避免把“2025年”“第3页”中的数字当成金额。
_CURRENCY_TOKEN_RE = re.compile(
    r"(?<![\dA-Za-z])"
    r"(?:\(?[-+]?\d[\d,，]*(?:\.\d+)?\)?)"
    r"(?![\d.])"
)

# 百分比数字：例如 "32.53%"、"-1.21%"、"(3.49%)"。
_PERCENT_TOKEN_RE = re.compile(
    r"(?:\(?[-+]?\d[\d,，]*(?:\.\d+)?\)?)\s*%"
)

# 财务表格行首常见的编号或前缀，例如“一、”“（一）”“其中：”。
# 匹配指标名之前先去掉这些前缀，并要求指标名出现在行首，
# 避免把“金额 占利润总额比例”这种表头误当成“利润总额”数据行。
_ROW_PREFIX_RE = re.compile(
    r"^(?:"
    r"[一二三四五六七八九十]+[、.．]"
    r"|[（(][一二三四五六七八九十0-9]+[）)]"
    r"|其中[：:]"
    r"|加[：:]"
    r"|减[：:]"
    r"|[、.．:：]"
    r")+"
)


def _token_to_float(raw_token: str) -> float | None:
    """
    把带逗号、括号、百分号的数字文本转换成浮点数。

    参数：
        raw_token: 例如 "168,838,102,514.79"、"(3.49)"、"32.53%"。

    返回：
        float | None: 无法识别时返回 None。

    说明：
        财务报表中的负数常用括号表示，例如 (3.49) 表示 -3.49。
        这里统一转成标准浮点数，后续计算和显示都更方便。
    """
    if not isinstance(raw_token, str):
        return None

    token = raw_token.strip().replace(",", "").replace("，", "")
    token = token.replace("%", "").strip()

    is_negative = token.startswith("(") and token.endswith(")")
    token = token.strip("()").strip()
    token = token.replace("+", "")

    if not token or token in {"-", "—", "–", "不适用"}:
        return None

    try:
        value = float(token)
    except ValueError:
        return None

    return -abs(value) if is_negative else value


def _extract_currency_tokens(line: str) -> list[float]:
    """
    从一行文字中提取货币/数量数字。

    参数：
        line: 年报中的一行文字。

    返回：
        list[float]: 按出现顺序排列的数字；跳过百分比、年份和页码。

    说明：
        这一行通常形如：
        “营业收入 168,838,102,514.79 170,899,152,276.34 -1.21%”
        需要拿到前两个金额，但不能把最后的 -1.21% 也当成金额。
    """
    tokens: list[float] = []

    for match in _CURRENCY_TOKEN_RE.finditer(line):
        raw = match.group(0).strip()

        # 如果数字后面紧跟着百分号，它是同比变化，不是金额。
        rest = line[match.end():].lstrip()
        if rest.startswith("%"):
            continue

        # 跳过“2025年”“第3页”“2026年4月”等日期/页码数字。
        if rest.startswith(("年", "月", "日", "页", "号")):
            continue

        value = _token_to_float(raw)
        if value is None:
            continue

        # 没有小数和千分位的 1900～2100 多半是年份；
        # 真正的金额即使很小，年报中通常也会带小数或千分位。
        if (
            1900 <= value <= 2100
            and "." not in raw
            and "," not in raw
            and "，" not in raw
        ):
            continue

        tokens.append(value)

    return tokens


def _extract_percent_tokens(line: str) -> list[float]:
    """
    从一行文字中提取百分比数字。

    参数：
        line: 年报中的一行文字。

    返回：
        list[float]: 百分比数值，例如 32.53、-3.49。
    """
    tokens: list[float] = []

    for match in _PERCENT_TOKEN_RE.finditer(line):
        value = _token_to_float(match.group(0))
        if value is not None:
            tokens.append(value)

    return tokens


def _format_value(value: float | None, unit: str) -> str:
    """
    把数值格式化成适合页面显示的文本。

    参数：
        value: 数值；None 表示未识别。
        unit: 单位。

    返回：
        str: 格式化后的文本；未识别时返回 "-"。
    """
    if value is None:
        return "-"

    if unit == "%":
        return f"{value:.2f}%"

    if unit == "元/股":
        # 每股收益通常保留 4 位小数，直接四舍五入到 2 位会把
        # 0.4176 显示成 0.42，损失尽调需要的精度。
        # 这里先保留 4 位，再去掉末尾多余的 0，例如 65.66 仍显示 65.66。
        text = f"{value:,.4f}"
        return text.rstrip("0").rstrip(".")

    return f"{value:,.2f}"


def _format_change(
    current: float | None,
    prior: float | None,
    values: list[float],
    percent_tokens: list[float],
    line: str,
    is_percent_metric: bool,
) -> str:
    """
    生成同比变化文本。

    参数：
        current: 本期数值。
        prior: 上期数值。
        values: 本行按出现顺序提取到的数值（含同比列）。
        percent_tokens: 本行中出现的百分比数值。
        line: 原始行，用于识别“减少 3.49 个百分点”。
        is_percent_metric: 当前指标本身是否是百分比。

    返回：
        str: 例如 "-1.21%"、"-3.49 个百分点"；无法计算时返回 "-"。

    说明：
        年报中的同比列有时写百分比，有时写“减少 X 个百分点”。
        优先使用原文中的变化，不重复计算；原文没有时才用本期/上期估算。
    """
    # 先识别文字形式的“减少 3.49 个百分点”“增加 1.20 个百分点”。
    # 这种写法优先级最高，因为后面的 2023 年数值没有百分号，
    # 如果先按百分比个数取数，可能把 2023 年数值误当成同比变化。
    point_match = re.search(
        r"(减少|下降|增加|增长)\s*([\d.]+)\s*个百分点",
        line,
    )
    if point_match:
        sign = "-" if point_match.group(1) in {"减少", "下降"} else "+"
        return f"{sign}{point_match.group(2)} 个百分点"

    # 货币/每股指标：本行只有一个百分比时，通常就是同比变化。
    if not is_percent_metric and percent_tokens:
        return f"{percent_tokens[-1]:+.2f}%"

    # 净资产收益率等百分比指标：表格通常有“本期、上期、同比增减、
    # 上上期”四列，第三个数才是同比变化；不能用最后一个，
    # 否则会把 2023 年的收益率误当成同比。
    # 这里用 values 而不是 percent_tokens，是为了兼容表格里的数值
    # 有的带“%”、有的不带“%”两种写法。
    if is_percent_metric and len(values) >= 3:
        return f"{values[2]:+.2f}%"

    # 百分比指标的变化不能用“相对变化”代替，否则会误读；没有原文就留空。
    if is_percent_metric:
        return "-"

    if current is None or prior in (None, 0):
        return "-"

    change = (current - prior) / abs(prior) * 100
    return f"{change:+.2f}%"


def _label_text_for_match(line: str) -> str:
    """
    去掉数字和货币单位，只保留用于匹配指标名的文字。

    参数：
        line: 年报中的一行文字。

    返回：
        str: 去掉金额、百分比、货币单位和空白后的文字。

    说明：
        年报 PDF 的排版有时会把指标名拆到数字两侧，例如：
        “归属于上市公司股东”
        “的扣除非经常性损益 221,003,752.75 238,202,229.47 ...”
        “的净利润（元）”
        如果只按整行判断，永远拼不出完整指标名。
        这里把每一行的数字去掉后再拼接，就能还原出
        “归属于上市公司股东的扣除非经常性损益的净利润（元）”。
    """
    if not line or not line.strip():
        return ""

    # 先去掉百分比和金额，再去掉“元/万元/亿元”等货币单位。
    cleaned = _PERCENT_TOKEN_RE.sub("", line)
    cleaned = _CURRENCY_TOKEN_RE.sub("", cleaned)
    cleaned = re.sub(
        r"(?:人民币)?(?:亿元|百万元|千万元|万元|千元|元)",
        "",
        cleaned,
    )

    # 去掉空白，方便处理 PDF 在汉字之间插入空格的情况。
    return re.sub(r"\s+", "", cleaned)


def _find_metric_line(
    page_text: str,
    spec: _MetricSpec,
) -> tuple[str, str] | None:
    """
    在一页文字中寻找某个指标的数值行。

    参数：
        page_text: 单页文字。
        spec: 指标规则。

    返回：
        tuple[str, str] | None:
            - 第一个值是用于提取数字的文本窗口；
            - 第二个值是原始匹配行；
            - 没有找到时返回 None。

    说明：
        PDF 可能把指标名和数字拆成多行，甚至把指标名拆到数字两侧。
        因此这里按“最多连续 3 行”的窗口查找：
        - 拼接名称时先去掉每行里的数字，兼容名称被数字隔开的版式；
        - 提取数字时保留整个窗口，确保数字不会丢；
        - 要求指标名出现在行首（允许“一、”“其中：”等前缀），
          避免把“占利润总额比例”这类表头误当成数据行；
        - 同样有数字时，优先选择行数更少的窗口，减少误匹配。
    """
    lines = page_text.split("\n")
    aliases = sorted(spec.aliases, key=len, reverse=True)

    # 候选格式：(数字个数, 负的行数, 文本窗口, 原始匹配行)
    # “负的行数”用于在数字个数相同时，优先选择跨度更小的窗口。
    best: tuple[int, int, str, str] | None = None

    for index, line in enumerate(lines):
        if any(keyword in line for keyword in spec.exclude_keywords):
            continue

        max_span = min(3, len(lines) - index)

        # 从当前行开始，依次尝试 1 行、2 行、3 行的窗口。
        for span in range(1, max_span + 1):
            window_lines = lines[index:index + span]
            window = " ".join(window_lines)

            # 拼接指标名称时去掉每行的数字，但数字仍保留在 window 中。
            label_text = "".join(
                _label_text_for_match(part)
                for part in window_lines
            )
            # 去掉“一、”“（一）”“其中：”等行首前缀后，
            # 要求指标名出现在行首；表头或正文句子里的指标名不算。
            label_text = _ROW_PREFIX_RE.sub("", label_text)

            if not any(label_text.startswith(alias) for alias in aliases):
                continue

            if spec.is_percent:
                # 正常写法是“32.53% 36.02%”；部分年报会省略百分号，
                # 写成“32.53 36.02”。前者优先，后者作为兜底。
                numbers = _extract_percent_tokens(window)
                if not numbers:
                    numbers = _extract_currency_tokens(window)
            else:
                numbers = _extract_currency_tokens(window)

            if not numbers:
                # 当前窗口有指标名但还没有数字，继续扩大窗口再试。
                continue

            # 有两个以上数字的候选行更像完整数据行，优先级更高；
            # 数字个数相同时，行数越少越可信。
            score = 2 if len(numbers) >= 2 else 1
            candidate = (score, -span, window, line)
            if best is None or candidate[:2] > best[:2]:
                best = candidate

            # 当前行已经找到可用数值，不再为这一行扩大窗口。
            break

    if best is None:
        return None

    return best[2], best[3]


def _looks_like_financial_statement(text: str) -> bool:
    """
    判断一页文字是否包含正式财务报表的标题。

    参数：
        text: 单页文字。

    返回：
        bool: 含“合并利润表/合并资产负债表/合并现金流量表”时返回 True。

    说明：
        有些指标（例如“营业总收入”）只出现在后面的合并利润表里，
        前面的“主要会计数据”表并不列示。补找时只认正式报表页，
        因为报表里的金额单位统一是“元”，不会出现“亿元”导致数量级错误。
    """
    if not text:
        return False

    return any(
        keyword in text
        for keyword in (
            "合并利润表",
            "合并资产负债表",
            "合并现金流量表",
        )
    )


def extract_financial_metrics(
    page_texts: dict[int, str],
) -> list[FinancialMetric]:
    """
    从年报各页文字中提取核心财务指标。

    参数：
        page_texts: 页码（从 1 开始）到该页文字的字典。

    返回：
        list[FinancialMetric]: 按固定顺序排列的指标；
                               找不到的指标也会保留，值显示为 "-"。

    说明：
        - 优先在含“主要会计数据/主要财务指标”的页面查找；
        - 如果那里没有，再到前 20 页查找；
        - 仍然没有时，到合并利润表/资产负债表/现金流量表等正式报表页补找；
        - 每个指标记录原文页码，方便用户回看 PDF；
        - 找不到时明确标记“未识别”，不猜测、不补全。
    """
    if not isinstance(page_texts, dict) or not page_texts:
        return []

    sorted_pages = sorted(
        page for page, text in page_texts.items()
        if isinstance(page, int) and isinstance(text, str) and text
    )
    if not sorted_pages:
        return []

    main_pages = [
        page
        for page in sorted_pages
        if (
            "主要会计数据" in page_texts[page]
            or "主要财务指标" in page_texts[page]
        )
        # 目录页也会出现“主要会计数据”等章节名，但后面跟的是页码，
        # 如果把目录页当成数据页，可能把“7”误读成金额 7 元。
        and not _looks_like_table_of_contents(page_texts[page])
    ]

    # 主要数据页优先；再补充前 20 页，避免有些年报标题写法不同。
    # 目录页同样跳过，只从正文页提取数字。
    search_pages = list(main_pages)
    for page in sorted_pages[:20]:
        if (
            page not in search_pages
            and not _looks_like_table_of_contents(page_texts[page])
        ):
            search_pages.append(page)

    # 有些指标只出现在后面的正式财务报表里，例如“营业总收入”
    # 在贵州茅台年报的“主要会计数据”表中就没有列示。
    # 这里把报表起始页及其后 2 页加入补找范围；只认正式报表页，
    # 是为了避免在管理层讨论里把“总资产 300 亿元”误读成“300 元”。
    statement_start_pages = [
        page
        for page in sorted_pages
        if (
            _looks_like_financial_statement(page_texts[page])
            and not _looks_like_table_of_contents(page_texts[page])
        )
    ]
    statement_pages: list[int] = []
    for start_page in statement_start_pages:
        for page in sorted_pages:
            if (
                start_page <= page <= start_page + 2
                and page not in search_pages
                and page not in statement_pages
            ):
                statement_pages.append(page)

    fallback_pages = search_pages + statement_pages

    metrics: list[FinancialMetric] = []

    for spec in _METRIC_SPECS:
        found_page = 0
        found_line: tuple[str, str] | None = None

        for page in fallback_pages:
            result = _find_metric_line(page_texts[page], spec)
            if result is not None:
                found_page = page
                found_line = result
                break

        if found_line is None:
            metrics.append(
                FinancialMetric(
                    label=spec.label,
                    value=None,
                    display_value="-",
                    unit=spec.unit,
                    note="未识别到该指标",
                )
            )
            continue

        window, original_line = found_line

        if spec.is_percent:
            # 优先使用带百分号的数字；没有百分号时按普通数字提取。
            # percent_tokens 仍只保存真正带“%”的数字，避免把
            # “减少 3.49 个百分点”误读成 +3.49%。
            percent_tokens = _extract_percent_tokens(window)
            values = percent_tokens or _extract_currency_tokens(window)
        else:
            values = _extract_currency_tokens(window)
            percent_tokens = _extract_percent_tokens(window)

        current = values[0] if values else None
        prior = values[1] if len(values) >= 2 else None

        change = _format_change(
            current=current,
            prior=prior,
            values=values,
            percent_tokens=percent_tokens,
            line=window,
            is_percent_metric=spec.is_percent,
        )

        metrics.append(
            FinancialMetric(
                label=spec.label,
                value=current,
                display_value=_format_value(current, spec.unit),
                prior_value=prior,
                prior_display_value=_format_value(prior, spec.unit),
                change=change,
                unit=spec.unit,
                page=found_page,
                note="" if current is not None else "未识别到有效数值",
            )
        )

    return metrics


# ---------------------------------------------------------------------------
# 审计意见与章节提取
# ---------------------------------------------------------------------------


def _looks_like_table_of_contents(text: str) -> bool:
    """
    判断一页文字是否像目录页。

    参数：
        text: 单页文字。

    返回：
        bool: 像目录时返回 True。

    说明：
        “审计报告”“关联交易”等词会出现在目录里。
        如果直接按关键词取第一页，可能拿到目录而不是正文，
        所以要先跳过带大量点线或“目录”字样的页面。
    """
    if not text:
        return False

    if "目录" in text[:400]:
        return True

    return text.count("....") >= 4 or text.count("……") >= 4


def _infer_audit_opinion(audit_text: str) -> str:
    """
    根据审计意见段落判断审计意见类型。

    参数：
        audit_text: 审计报告相关文字。

    返回：
        str: 审计意见类型；无法判断时返回“未识别”。

    说明：
        先判断更严重的非标准意见，再判断无保留意见。
        顺序不能反，否则“无法表示意见”可能被“保留意见”误匹配。
    """
    if not isinstance(audit_text, str) or not audit_text.strip():
        return "未识别"

    text = audit_text.replace(" ", "")

    if "无法表示意见" in text:
        return "无法表示意见"
    if "否定意见" in text:
        return "否定意见"
    if "带强调事项段的无保留意见" in text:
        return "带强调事项段的无保留意见"
    if "带持续经营重大不确定性段落的无保留意见" in text:
        return "带持续经营重大不确定性段落的无保留意见"
    if "标准无保留意见" in text:
        return "标准无保留意见"
    if "无保留意见" in text:
        return "无保留意见"
    if "保留意见" in text:
        return "保留意见"

    return "未识别"


def _find_audit_section(
    page_texts: dict[int, str],
    total_pages: int,
) -> ReportSection | None:
    """
    找到审计报告正文并截取。

    参数：
        page_texts: 页码到文字的字典。
        total_pages: PDF 总页数，用于优先在靠后位置寻找审计报告。

    返回：
        ReportSection | None: 找到时返回审计章节，否则返回 None。

    说明：
        审计报告通常在年报靠后的位置。
        先找同时含“审计意见”和“会计师事务所/我们认为”的页面；
        找不到再退回含“审计报告”的正文页面，并跳过目录页。
    """
    sorted_pages = sorted(page_texts)

    preferred_pages = [
        page
        for page in sorted_pages
        if (
            "审计意见" in page_texts[page]
            and (
                "我们认为" in page_texts[page]
                or "审计意见类型" in page_texts[page]
                or "会计师事务所" in page_texts[page]
            )
            and not _looks_like_table_of_contents(page_texts[page])
        )
    ]

    if not preferred_pages:
        preferred_pages = [
            page
            for page in sorted_pages
            if (
                "审计报告" in page_texts[page]
                and not _looks_like_table_of_contents(page_texts[page])
            )
        ]

    if not preferred_pages:
        return None

    # 审计报告一般在后半部分；优先选后半部分的第一处，
    # 避免把前面的“审计意见类型”摘要页当成完整审计报告。
    later_pages = [
        page for page in preferred_pages
        if page >= max(1, total_pages // 2)
    ]
    start_page = later_pages[0] if later_pages else preferred_pages[0]

    parts: list[str] = []
    end_page = start_page

    for page in sorted_pages:
        if page < start_page:
            continue
        if page > start_page + 3:
            break

        parts.append(f"【第 {page} 页】\n{page_texts[page]}")
        end_page = page

        if sum(len(part) for part in parts) >= MAX_AUDIT_SECTION_CHARS:
            break

    text = "\n\n".join(parts).strip()
    if not text:
        return None

    return ReportSection(
        title="审计意见",
        text=text[:MAX_AUDIT_SECTION_CHARS],
        page_start=start_page,
        page_end=end_page,
    )


def _find_keyword_section(
    page_texts: dict[int, str],
    keywords: tuple[str, ...],
    title: str,
    max_chars: int = MAX_SECTION_CHARS,
) -> ReportSection | None:
    """
    按关键词找到一个章节并截取后续内容。

    参数：
        page_texts: 页码到文字的字典。
        keywords: 章节可能使用的标题关键词。
        title: 输出给页面看的章节名称。
        max_chars: 最多截取多少字符。

    返回：
        ReportSection | None: 找到时返回章节，否则返回 None。

    说明：
        跳过目录页；从命中页开始，最多向后取 3 页，
        直到达到字数上限。页码会一起保留，方便人工核对。
    """
    for page in sorted(page_texts):
        text = page_texts[page]
        if _looks_like_table_of_contents(text):
            continue

        if not any(keyword in text for keyword in keywords):
            continue

        parts: list[str] = []
        end_page = page

        for next_page in sorted(page_texts):
            if next_page < page:
                continue
            if next_page > page + 3:
                break

            parts.append(
                f"【第 {next_page} 页】\n{page_texts[next_page]}"
            )
            end_page = next_page

            if sum(len(part) for part in parts) >= max_chars:
                break

        section_text = "\n\n".join(parts).strip()
        if not section_text:
            return None

        return ReportSection(
            title=title,
            text=section_text[:max_chars],
            page_start=page,
            page_end=end_page,
        )

    return None


def _extract_auditor(audit_text: str) -> str:
    """
    从审计报告中提取会计师事务所名称。

    参数：
        audit_text: 审计报告相关文字。

    返回：
        str: 事务所名称；未识别时返回空字符串。
    """
    if not audit_text:
        return ""

    matches = re.findall(
        # “会计师事务所”前至少 2 个字即可，例如“天健”“立信”“大华”；
        # 如果要求 4 个字，短名称的事务所会被漏掉。
        r"[一-龥（）()]{2,40}会计师事务所"
        r"(?:（特殊普通合伙）|\(特殊普通合伙\))?",
        audit_text,
    )
    if not matches:
        return ""

    # 取最长的一个，通常是完整名称，例如“天健会计师事务所（特殊普通合伙）”。
    return max(matches, key=len).strip()


def _infer_report_year(page_texts: dict[int, str]) -> int:
    """
    从年报页眉推断报告年度。

    参数：
        page_texts: 页码到文字的字典。

    返回：
        int: 例如 2025；无法判断时返回 0。

    说明：
        年报每页页眉通常都有“公司名称 2025年年度报告”。
        知道报告年度后，才能判断审计报告日期是否合理。
    """
    for page in sorted(page_texts)[:3]:
        match = re.search(
            r"(20\d{2})\s*年\s*年度报告",
            page_texts[page],
        )
        if match:
            return int(match.group(1))

    return 0


def _extract_audit_date(
    audit_text: str,
    report_year: int = 0,
) -> str:
    """
    从审计报告中提取日期。

    参数：
        audit_text: 审计报告相关文字。
        report_year: 年报所属年度，例如 2025；0 表示未知。

    返回：
        str: 例如 "2026年04月17日"；未识别时返回空字符串。

    说明：
        审计报告落款日期通常在该章节末尾，因此取最后一个日期。
        但审计报告里也会出现资产负债表日（如 2025年12月31日）
        和上期比较数（如 2024年12月31日），这些都不是签署日期。
        知道报告年度时，只保留年份大于报告年度的日期。
    """
    if not audit_text:
        return ""

    matches = re.findall(
        r"20\d{2}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日",
        audit_text,
    )
    if not matches:
        return ""

    if report_year > 0:
        valid_matches = []
        for match in matches:
            year_match = re.search(r"20\d{2}", match)
            if year_match and int(year_match.group()) > report_year:
                valid_matches.append(match)

        if not valid_matches:
            return ""
        matches = valid_matches

    return re.sub(r"\s+", "", matches[-1])


def _find_auditor_in_pages(page_texts: dict[int, str]) -> str:
    """
    在整份年报中查找会计师事务所名称。

    参数：
        page_texts: 页码到文字的字典。

    返回：
        str: 事务所名称；未识别时返回空字符串。

    说明：
        审计报告正文可能只写“我们审计了……”，事务所名称和盖章
        在签名页，而签名页有时刚好超出审计章节的截取范围。
        因此再从“会计师事务所名称”这类信息行补找。
        只认带“名称”字样的行，避免把“公司聘请的会计师事务所”
        这种标签本身当成事务所名称。
    """
    for page in sorted(page_texts):
        text = page_texts[page]
        if "会计师事务所" not in text:
            continue

        for line in text.split("\n"):
            if "会计师事务所" not in line or "名称" not in line:
                continue

            # 取“名称”后面的内容，例如：
            # “会计师事务所名称 四川华信（集团）会计师事务所（特殊普通合伙）”
            candidate_text = line.split("名称", 1)[1]
            name = _extract_auditor(candidate_text)
            if name:
                return name

    return ""


def _extract_sections(
    page_texts: dict[int, str],
    total_pages: int,
) -> dict[str, ReportSection]:
    """
    提取给法务、业务和财务专家使用的关键章节。

    参数：
        page_texts: 页码到文字的字典。
        total_pages: PDF 总页数。

    返回：
        dict[str, ReportSection]: 章节字典；没有找到的章节不会出现。
    """
    sections: dict[str, ReportSection] = {}

    audit_section = _find_audit_section(page_texts, total_pages)
    if audit_section is not None:
        sections["audit"] = audit_section

    section_specs: tuple[
        tuple[str, tuple[str, ...], str],
        ...,
    ] = (
        (
            "related_party",
            ("关联交易",),
            "关联交易",
        ),
        (
            "guarantee",
            ("对外担保", "担保事项", "重大担保"),
            "对外担保",
        ),
        (
            "litigation",
            ("诉讼", "仲裁"),
            "诉讼与仲裁",
        ),
        (
            "business_discussion",
            ("经营情况讨论与分析", "管理层讨论与分析"),
            "经营情况讨论与分析",
        ),
        (
            "risk",
            ("可能面对的风险", "风险因素", "风险提示"),
            "风险因素",
        ),
        (
            "dividend",
            ("利润分配", "现金分红"),
            "利润分配与分红",
        ),
    )

    for key, keywords, title in section_specs:
        section = _find_keyword_section(
            page_texts,
            keywords,
            title,
            max_chars=(
                MAX_AUDIT_SECTION_CHARS
                if key == "audit"
                else MAX_SECTION_CHARS
            ),
        )
        if section is not None:
            sections[key] = section

    return sections


# ---------------------------------------------------------------------------
# 表格与分季度数据
# ---------------------------------------------------------------------------


# 含这些关键词的页面才值得识别表格，避免每页都做耗时的表格分析。
_TABLE_PAGE_KEYWORDS = (
    "主要会计数据",
    "主要财务指标",
    "分季度",
    "营业收入构成",
    "主营业务分",
    "利润分配",
    "合并资产负债表",
    "合并利润表",
    "合并现金流量表",
    "审计意见",
)


def _should_extract_tables(page_text: str) -> bool:
    """
    判断当前页是否值得提取表格。

    参数：
        page_text: 单页文字。

    返回：
        bool: 含财务/分红/审计关键词时返回 True。
    """
    return any(keyword in page_text for keyword in _TABLE_PAGE_KEYWORDS)


def _safe_extract_tables(page: Any) -> list[list[list[Any]]]:
    """
    安全地调用 pdfplumber 的表格识别。

    参数：
        page: pdfplumber 页面对象。

    返回：
        list[list[list[Any]]]: 表格列表；失败时返回空列表。

    说明：
        表格识别偶尔会因为页面线条复杂而报错。
        表格提取失败不应该导致整份年报解析失败，因此这里吞掉异常。
    """
    try:
        return page.extract_tables() or []
    except Exception:
        return []


def _guess_table_title(page_text: str, page_number: int) -> str:
    """
    根据页面文字猜测表格标题。

    参数：
        page_text: 单页文字。
        page_number: 页码。

    返回：
        str: 表格标题。
    """
    title_keywords = (
        ("主要会计数据", "主要会计数据"),
        ("主要财务指标", "主要财务指标"),
        ("分季度", "分季度主要财务数据"),
        ("营业收入构成", "营业收入构成"),
        ("主营业务分", "主营业务构成"),
        ("利润分配", "利润分配"),
        ("合并资产负债表", "合并资产负债表"),
        ("合并利润表", "合并利润表"),
        ("合并现金流量表", "合并现金流量表"),
        ("审计意见", "审计意见相关表格"),
    )

    for keyword, title in title_keywords:
        if keyword in page_text:
            return title

    return f"第 {page_number} 页表格"


def _extract_quarterly_rows(
    raw_table: list[list[Any]],
) -> list[dict[str, str]]:
    """
    尝试从一张表格中提取分季度数据。

    参数：
        raw_table: pdfplumber 返回的表格。

    返回：
        list[dict[str, str]]: 每行一个字典，例如
                              {"项目": "营业收入", "第一季度": "..."}；
                              不是分季度表时返回空列表。

    说明：
        分季度表通常有“项目、第一季度、第二季度、第三季度、第四季度”
        五列。这里只在表头明显匹配时才结构化，避免误读其他表格。
    """
    if not raw_table or len(raw_table) < 2:
        return []

    header = [_clean_cell(cell) for cell in raw_table[0]]
    if not any("第一季度" in cell for cell in header):
        return []
    if not any("第四季度" in cell for cell in header):
        return []

    quarter_labels = ("第一季度", "第二季度", "第三季度", "第四季度")
    label_index = 0

    results: list[dict[str, str]] = []

    for raw_row in raw_table[1:]:
        cells = [_clean_cell(cell) for cell in raw_row]
        if not any(cells):
            continue

        label = cells[label_index] if cells else ""
        if not label:
            continue

        row_data: dict[str, str] = {"项目": label}

        for quarter in quarter_labels:
            try:
                column_index = header.index(quarter)
            except ValueError:
                continue
            row_data[quarter] = (
                cells[column_index]
                if column_index < len(cells)
                else ""
            )

        if any(row_data.get(quarter) for quarter in quarter_labels):
            results.append(row_data)

    return results


# ---------------------------------------------------------------------------
# 主解析函数
# ---------------------------------------------------------------------------


def _select_page_indices(total_pages: int) -> list[int]:
    """
    选择本次要读取的页码（从 0 开始）。

    参数：
        total_pages: PDF 总页数。

    返回：
        list[int]: 升序页码列表。

    说明：
        300 页以内全部读取；超过 300 页时取前 150 页和后 150 页，
        兼顾开头的主要财务数据和结尾的财务报表/审计报告。
    """
    if total_pages <= 0:
        return []

    if total_pages <= MAX_PDF_PAGES:
        return list(range(total_pages))

    front = list(range(PDF_FRONT_PAGES))
    back = list(
        range(total_pages - PDF_BACK_PAGES, total_pages)
    )
    return sorted(set(front + back))


def parse_annual_report_pdf(pdf_bytes: bytes) -> ParsedAnnualReport:
    """
    解析一份官方年报 PDF。

    参数：
        pdf_bytes: PDF 文件的原始字节。

    返回：
        ParsedAnnualReport: 页码、核心指标、表格、章节、审计意见等。

    异常：
        AnnualReportParseError: 文件为空、不是 PDF、需要密码、
                                 是扫描图片版或解析失败时抛出。

    说明：
        - 只读取文字和表格，不执行 PDF 中的任何内容；
        - 表格只在含关键词的页面识别，避免整份年报解析过慢；
        - 所有数字都保留页码，方便用户回原文核对；
        - 扫描图片版没有文字层，会明确提示需要 OCR，而不是假装解析成功。
    """
    if not isinstance(pdf_bytes, (bytes, bytearray)) or not pdf_bytes:
        raise AnnualReportParseError("年报 PDF 内容为空，无法解析。")

    header = bytes(pdf_bytes[:1024]).lstrip()
    if not header.startswith(b"%PDF-"):
        raise AnnualReportParseError("下载到的内容不是 PDF 文件。")

    try:
        import pdfplumber
    except ImportError as exc:
        raise AnnualReportParseError(
            "PDF 解析组件尚未安装。请在项目虚拟环境中运行："
            "python -m pip install -r requirements.txt。"
        ) from exc

    try:
        with pdfplumber.open(BytesIO(bytes(pdf_bytes))) as pdf:
            total_pages = len(pdf.pages)
            if total_pages == 0:
                raise AnnualReportParseError(
                    "这份 PDF 没有可读取的页面。"
                )

            selected_indices = _select_page_indices(total_pages)

            page_texts: dict[int, str] = {}
            tables: list[ReportTable] = []
            quarterly: list[dict[str, str]] = []

            for page_index in selected_indices:
                page = pdf.pages[page_index]
                page_number = page_index + 1

                try:
                    page_text = page.extract_text() or ""
                except Exception:
                    page_text = ""

                page_text = _normalize_text(page_text)
                if not page_text:
                    continue

                page_texts[page_number] = page_text[:MAX_PAGE_TEXT_CHARS]

                if (
                    len(tables) < MAX_TABLES
                    and _should_extract_tables(page_text)
                ):
                    raw_tables = _safe_extract_tables(page)

                    for raw_table in raw_tables[:MAX_TABLES_PER_PAGE]:
                        if len(tables) >= MAX_TABLES:
                            break

                        markdown = _table_to_markdown(raw_table)
                        if not markdown:
                            continue

                        tables.append(
                            ReportTable(
                                title=_guess_table_title(
                                    page_text,
                                    page_number,
                                ),
                                markdown=markdown,
                                page=page_number,
                            )
                        )

                        if not quarterly and "分季度" in page_text:
                            quarterly = _extract_quarterly_rows(raw_table)

            text_char_count = sum(
                len(text) for text in page_texts.values()
            )

            if text_char_count < MIN_TEXT_CHARS:
                raise AnnualReportParseError(
                    "没有从这份年报中提取到足够文字，"
                    "它可能是扫描图片版，需要先做 OCR 识别。"
                )

            metrics = extract_financial_metrics(page_texts)
            sections = _extract_sections(page_texts, total_pages)

            report_year = _infer_report_year(page_texts)

            audit_section = sections.get("audit")
            audit_text = audit_section.text if audit_section else ""
            audit_opinion = _infer_audit_opinion(audit_text)

            # 优先从审计正文提取事务所；正文没有签名页时，
            # 再从“会计师事务所名称”信息行补找。
            auditor = _extract_auditor(audit_text)
            if not auditor:
                auditor = _find_auditor_in_pages(page_texts)

            # 只保留晚于报告年度的日期，避免把资产负债表日
            # （例如 2025年12月31日）误当成审计报告签署日期。
            audit_date = _extract_audit_date(
                audit_text,
                report_year=report_year,
            )

            dividend_section = sections.get("dividend")
            dividend_text = (
                dividend_section.text[:1_200]
                if dividend_section
                else ""
            )

            warnings: list[str] = []

            if total_pages > len(selected_indices):
                warnings.append(
                    f"年报共 {total_pages} 页，"
                    f"为控制时间只解析了前 {PDF_FRONT_PAGES} 页"
                    f"和后 {PDF_BACK_PAGES} 页。"
                )

            if not any(
                metric.value is not None for metric in metrics
            ):
                warnings.append(
                    "没有自动识别到主要财务数据，"
                    "请打开原文核对，或通过 Tab 1 上传后分析。"
                )

            if audit_opinion == "未识别":
                warnings.append(
                    "没有自动识别到审计意见类型，"
                    "请打开原文审计报告核对。"
                )

            if not tables:
                warnings.append(
                    "没有自动识别到报表表格，"
                    "财务分析将主要依据核心指标文字。"
                )

            return ParsedAnnualReport(
                page_count=total_pages,
                parsed_pages=len(page_texts),
                text_char_count=text_char_count,
                metrics=metrics,
                quarterly=quarterly,
                tables=tables,
                sections=sections,
                audit_opinion=audit_opinion,
                auditor=auditor,
                audit_date=audit_date,
                dividend_text=dividend_text,
                warnings=warnings,
            )

    except AnnualReportParseError:
        raise

    except Exception as exc:
        message = str(exc).lower()
        if "password" in message or "encrypted" in message:
            raise AnnualReportParseError(
                "这份年报 PDF 需要密码才能打开，已停止解析。"
            ) from exc
        raise AnnualReportParseError(
            "年报 PDF 解析失败，可能是文件损坏或格式特殊。"
            "请打开巨潮原文链接核对，或通过 Tab 1 上传后分析。"
        ) from exc
