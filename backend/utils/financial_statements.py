"""
近三年财务报表服务：用 AKShare 的新浪财经接口取三张明细表。

为什么要单独一个模块：
- 巨潮年报是 PDF，解析出来的是“核心指标 + 原文摘录”，科目不够全；
- 新浪财经接口直接返回中文科目名，能补上资产负债表、利润表和
  现金流量表的完整明细；
- 两个来源互相独立：AKShare 取不到时，巨潮年报仍然可以正常展示，
  不会因为一个数据源失败就整页不可用。

数据口径（非常重要）：
1. 只取“合并期末”的年度报告，也就是每年 12 月 31 日那一行；
   季报、半年报和母公司报表都不取，避免把不同期间的数字混在一起。
2. 接口原始金额单位是“元”，展示时统一除以 1 亿，保留 2 位小数；
   基本每股收益、稀释每股收益本来就是“元/股”，不做亿元换算。
3. 接口没有返回的项目显示“-”，绝不用其他年份或模型知识补数字。
4. 数据来自第三方整理，正式尽调结论仍应以巨潮年报原文为准。

缓存说明：
- 结果放在系统临时目录，24 小时内重复点击不再访问新浪财经；
- 只保存公开财报数字，不保存 API Key、Cookie 或用户隐私；
- 三张表没有全部取到时，本次不写缓存，下次点击会自动重试。
"""

from __future__ import annotations

import json
import math
import re
import tempfile
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from utils.cninfo_client import CninfoError, normalize_stock_code

# ---------------------------------------------------------------------------
# 缓存和长度配置
# ---------------------------------------------------------------------------

# 三张表的缓存目录。和年报缓存分开，避免巨潮 PDF 与新浪数据互相覆盖。
CACHE_DIR = Path(tempfile.gettempdir()) / "dd_agent_financial_statements_cache"

# 缓存有效期：24 小时。财报数字不会频繁变化，一天内重复使用足够。
CACHE_TTL_SECONDS = 24 * 60 * 60

# 缓存格式版本号。以后调整取数或展示规则时加一，旧缓存会自动失效。
CACHE_VERSION = 1

# 放进专家提示词的表格最多占多少字符。
# 三个智能体单次输入上限是 50,000 字符，年报数据包本身最多约 24,000，
# 这里留出约 14,000 给三张表，合起来仍在安全范围内。
MAX_AGENT_CONTEXT_CHARS = 14_000

# 年报数据包和三张表合并后的总上限。
# 比智能体的 50,000 字符上限再留 5,000 字符余量，防止提示词其他部分超限。
MAX_COMBINED_CONTEXT_CHARS = 45_000

# 财务报表接口通常返回的元数据列，不属于会计科目，展示时全部去掉。
_METADATA_COLUMNS = frozenset(
    {
        "报告日",
        "数据源",
        "是否审计",
        "公告日期",
        "币种",
        "类型",
        "更新日期",
    }
)

# 资产负债表里的“章节标题”列，接口只在标题行留空，不是真正科目。
_BALANCE_SECTION_COLUMNS = frozenset(
    {
        "流动资产",
        "非流动资产",
        "流动负债",
        "非流动负债",
        "所有者权益",
    }
)

# 现金流量表里的三个章节标题，同样不是可展示的科目。
_CASH_FLOW_SECTION_COLUMNS = frozenset(
    {
        "经营活动产生的现金流量",
        "投资活动产生的现金流量",
        "筹资活动产生的现金流量",
    }
)

# 资产负债表中“汇总列可以省略”的对应关系。
# 例如接口同时给出“应收票据”“应收账款”和“应收票据及应收账款”：
# 前两项有数字时，第三项只是汇总，重复展示会让表变长；
# 但如果前两项都没有数字，就必须保留汇总列，否则会丢数据。
_REDUNDANT_AGGREGATES: dict[str, tuple[str, ...]] = {
    "应收票据及应收账款": ("应收票据", "应收账款"),
    "其他应收款(合计)": ("其他应收款",),
    "在建工程合计": ("在建工程",),
    "固定资产及清理合计": ("固定资产净额", "固定资产清理"),
    "应付票据及应付账款": ("应付票据", "应付账款"),
    "其他应付款合计": ("其他应付款",),
    "长期应付款合计": ("长期应付款",),
}

# 这些是明显的中间计算值，保留“原值 / 累计折旧 / 减值准备 / 净额”
# 已经足够还原固定资产，不再重复展示，避免同一笔钱出现三次。
_ALWAYS_HIDDEN_BALANCE_COLUMNS = frozenset(
    {
        "固定资产净值",
    }
)


class FinancialStatementError(RuntimeError):
    """
    三张表获取失败时抛出的中文异常。

    页面只需要捕获这一种异常，就能把“代码不支持、年份不合理、
    AKShare 未安装、网络失败”等情况展示成用户看得懂的提示。
    """


# ---------------------------------------------------------------------------
# 三张表的配置
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _StatementSpec:
    """
    一张报表的取数配置。

    属性：
        key: 程序内部使用的英文标识，例如 "balance"。
        title: 中文标题，例如 "资产负债表"。
        sina_symbol: 新浪接口认识的报表名称。
        hidden_columns: 不参与展示的列。
        per_share_items: 需要按“元/股”展示、不能除以 1 亿的科目。
    """

    key: str
    title: str
    sina_symbol: str
    hidden_columns: frozenset[str]
    per_share_items: frozenset[str] = frozenset()


# 三张表的顺序就是页面子页签和专家数据包的顺序：
# 先看家底（资产负债表），再看经营成果（利润表），最后看现金进出。
STATEMENT_SPECS: tuple[_StatementSpec, ...] = (
    _StatementSpec(
        key="balance",
        title="资产负债表",
        sina_symbol="资产负债表",
        hidden_columns=(
            _METADATA_COLUMNS
            | _BALANCE_SECTION_COLUMNS
            | _ALWAYS_HIDDEN_BALANCE_COLUMNS
        ),
    ),
    _StatementSpec(
        key="income",
        title="利润表",
        sina_symbol="利润表",
        hidden_columns=_METADATA_COLUMNS,
        # 每股收益的单位是元/股，除以 1 亿会变成 0.00，必须单独格式化。
        per_share_items=frozenset({"基本每股收益", "稀释每股收益"}),
    ),
    _StatementSpec(
        key="cash_flow",
        title="现金流量表",
        sina_symbol="现金流量表",
        hidden_columns=_METADATA_COLUMNS | _CASH_FLOW_SECTION_COLUMNS,
    ),
)

# 页面按这个顺序展示三张表。
STATEMENT_KEYS: tuple[str, ...] = tuple(
    spec.key for spec in STATEMENT_SPECS
)


# ---------------------------------------------------------------------------
# 结果数据结构
# ---------------------------------------------------------------------------


@dataclass
class StatementTable:
    """
    一张财务报表的展示结果。

    属性：
        key: 英文标识，例如 "balance"。
        title: 中文标题，例如 "资产负债表"。
        years: 三个会计年度，按从新到旧排列，例如 [2025, 2024, 2023]。
        rows: 表格行，每行形如
              {"项目": "货币资金", "2025": "516.91", "2024": "..."}。
        warnings: 这张表自己的提示，例如某一年没有数据。
    """

    key: str
    title: str
    years: list[int]
    rows: list[dict[str, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        """转换成可以写入 JSON、放进 session_state 的普通字典。"""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "StatementTable":
        """从缓存字典恢复一张表；格式不对时抛出 ValueError。"""
        if not isinstance(data, dict):
            raise ValueError("财务报表缓存格式不正确。")

        years: list[int] = []
        for item in data.get("years", []):
            try:
                years.append(int(item))
            except (TypeError, ValueError):
                continue

        rows = [
            row
            for row in data.get("rows", [])
            if isinstance(row, dict)
        ]

        return cls(
            key=str(data.get("key", "")),
            title=str(data.get("title", "")),
            years=years,
            rows=rows,
            warnings=[
                str(item)
                for item in data.get("warnings", [])
                if item
            ],
        )


@dataclass
class ThreeYearStatements:
    """
    某家公司近三年三张表的完整结果。

    属性：
        code: 6 位股票代码。
        year: 用户请求的最近一个会计年度。
        years: [year, year-1, year-2]。
        tables: 以 "balance" / "income" / "cash_flow" 为键的三张表。
        source: 数据来源说明。
        fetched_at: 获取时间，格式 YYYY-MM-DD HH:MM:SS。
        warnings: 给用户看的总体提示。
        from_cache: 本次结果是否来自 24 小时缓存。
    """

    code: str
    year: int
    years: list[int]
    tables: dict[str, StatementTable] = field(default_factory=dict)
    source: str = "AKShare（新浪财经）"
    fetched_at: str = ""
    warnings: list[str] = field(default_factory=list)
    from_cache: bool = False

    def to_dict(self) -> dict:
        """转换成可以写入 JSON、放进 session_state 的普通字典。"""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "ThreeYearStatements":
        """从缓存字典恢复完整结果；格式不对时抛出 ValueError。"""
        if not isinstance(data, dict):
            raise ValueError("三张表缓存格式不正确。")

        years: list[int] = []
        for item in data.get("years", []):
            try:
                years.append(int(item))
            except (TypeError, ValueError):
                continue

        tables: dict[str, StatementTable] = {}
        raw_tables = data.get("tables", {})
        if isinstance(raw_tables, dict):
            for key, item in raw_tables.items():
                if isinstance(item, dict):
                    tables[str(key)] = StatementTable.from_dict(item)

        return cls(
            code=str(data.get("code", "")),
            year=int(data.get("year", 0)),
            years=years,
            tables=tables,
            source=str(data.get("source", "AKShare（新浪财经）")),
            fetched_at=str(data.get("fetched_at", "")),
            warnings=[
                str(item)
                for item in data.get("warnings", [])
                if item
            ],
            from_cache=bool(data.get("from_cache", False)),
        )

    def to_agent_context(
        self,
        max_chars: int = MAX_AGENT_CONTEXT_CHARS,
    ) -> str:
        """
        把三张表整理成给三位专家共用的 Markdown 数据包。

        参数：
            max_chars: 数据包最大字符数，防止挤占年报正文。

        返回：
            str: 带来源、单位、年份和“不得猜测”规则的文本。

        说明：
            - 用 <financial_statements> 标签包住，提醒模型这是数据不是指令；
            - 只放表格，不放大段解释，尽量把字符留给具体科目；
            - 超长时截断并保留结束标签，页面仍可查看完整表格。
        """
        lines: list[str] = [
            "## 近三年财务报表（AKShare / 新浪财经）",
            f"- 股票代码：{self.code}",
            "- 会计年度：" + "、".join(
                f"{year} 年" for year in self.years
            ),
            "- 金额单位：亿元；基本每股收益、稀释每股收益为元/股。",
            "- 数据来源：AKShare 新浪财经接口，属于第三方整理数据；",
            "  与巨潮年报原文冲突时，以巨潮年报原文为准。",
            "- “-”表示接口没有返回，不得猜测或用其他年份数字补全。",
            "",
            "<financial_statements>",
        ]

        for key in STATEMENT_KEYS:
            table = self.tables.get(key)
            if table is None or not table.rows:
                continue

            headers = ["项目"] + [str(year) for year in table.years]
            lines.append("")
            lines.append(f"### {table.title}")
            lines.append("| " + " | ".join(headers) + " |")
            lines.append(
                "| "
                + " | ".join(
                    ["---"] + ["---:"] * len(table.years)
                )
                + " |"
            )

            for row in table.rows:
                cells = [
                    _markdown_cell(row.get(header, "-"))
                    for header in headers
                ]
                lines.append("| " + " | ".join(cells) + " |")

            if table.warnings:
                lines.append("")
                lines.append(
                    "提示：" + "；".join(table.warnings)
                )

        if self.warnings:
            lines.extend(["", "### 获取提示"])
            lines.extend(
                f"- {warning}" for warning in self.warnings
            )

        lines.extend(["", "</financial_statements>"])
        context = "\n".join(lines).strip()

        if len(context) > max_chars:
            # 截断时重新补上结束标签，确保模型始终能看出
            # “这是资料，不是指令”的边界。
            context = (
                context[: max_chars - 120].rstrip()
                + "\n\n（三张表数据包过长，已截断；"
                "完整数据请在页面表格查看。）"
                + "\n</financial_statements>"
            )

        return context


# ---------------------------------------------------------------------------
# 小工具：文本、日期和数字处理
# ---------------------------------------------------------------------------


def _clean_text(value) -> str:
    """
    把接口返回的单元格整理成干净字符串。

    参数：
        value: 原始值，可能是字符串、数字、None 或 NaN。

    返回：
        str: 去掉首尾空格后的文字；空值和 NaN 返回空字符串。

    说明：
        pandas 读到的空单元格可能是 None，也可能是 NaN。
        如果直接 str(NaN)，会得到 "nan"，后面判断“合并”“期末”
        时就会误判，所以这里统一把空值变成空字符串。
    """
    if value is None:
        return ""

    if isinstance(value, float) and math.isnan(value):
        return ""

    text = str(value).strip()
    if text.lower() in {"nan", "none", "null", "nat"}:
        return ""

    return text


def _normalize_date(value) -> str:
    """
    把各种写法的报告日期统一成 YYYYMMDD。

    参数：
        value: 例如 "20251231"、"2025-12-31"、20251231.0。

    返回：
        str: 8 位日期；无法识别时返回空字符串。
    """
    text = _clean_text(value)
    digits = re.sub(r"\D", "", text)
    return digits[:8] if len(digits) >= 8 else ""


def _to_float(value) -> float | None:
    """
    把接口单元格转成浮点数，空值和无法识别的文字返回 None。

    参数：
        value: 原始值，可能是数字、带千分位的字符串或空值。

    返回：
        float | None: 可用数字；无效值返回 None。
    """
    if value is None:
        return None

    if isinstance(value, bool):
        # True/False 不是财务数字，避免被当成 1/0。
        return None

    try:
        if isinstance(value, (int, float)):
            number = float(value)
        else:
            text = _clean_text(value)
            if not text:
                return None

            text = text.replace(",", "").replace("，", "")
            # 兼容“人民币元”“万元”等少见写法；新浪接口通常直接给数字，
            # 这里做兜底，避免一个单位后缀导致整格变成“-”。
            text = re.sub(
                r"(?:人民币)?(?:亿元|万元|千元|元)$",
                "",
                text,
            )
            number = float(text)
    except (TypeError, ValueError):
        return None

    if math.isnan(number) or math.isinf(number):
        return None

    return number


def _format_amount(value: float | None, per_share: bool) -> str:
    """
    把“元”换算成页面展示文字。

    参数：
        value: 原始金额，单位为元；None 表示接口没有数据。
        per_share: 是否为每股指标（元/股）。

    返回：
        str: 亿元金额或元/股金额；没有数据时返回 "-"。

    说明：
        每股收益通常有 4 位小数，例如 0.4176。如果先除以 1 亿，
        会变成 0.00，所以必须单独处理，并且保留 4 位小数。
    """
    if value is None:
        return "-"

    if per_share:
        text = f"{value:,.4f}"
        return text.rstrip("0").rstrip(".")

    # 避免出现 "-0.00" 这种看着像负数的零。
    if value == 0:
        value = 0.0

    return f"{value / 100_000_000:,.2f}"


def _markdown_cell(value) -> str:
    """把表格单元格转成 Markdown 安全文字，避免竖线破坏表格。"""
    text = str(value) if value is not None else "-"
    if not text:
        return "-"
    return text.replace("|", "｜").replace("\n", " ")


def _deduplicate(items: list[str]) -> list[str]:
    """按原顺序去重，避免同一句提示重复出现。"""
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def _notify(
    callback: Callable[[str], None] | None,
    message: str,
) -> None:
    """安全地调用页面进度回调；回调本身出错时不打断取数。"""
    if callback is None:
        return
    try:
        callback(message)
    except Exception:
        # 页面回调只负责显示进度，不能因为它出错就丢掉已经取到的数据。
        return


# ---------------------------------------------------------------------------
# 缓存读写
# ---------------------------------------------------------------------------


def _cache_path(code: str, year: int) -> Path:
    """生成某只股票某一年三张表的缓存文件路径。"""
    return CACHE_DIR / f"{code}_{year}.json"


def _load_cache(
    code: str,
    year: int,
) -> ThreeYearStatements | None:
    """
    读取 24 小时内的三张表缓存。

    参数：
        code: 6 位股票代码。
        year: 最近一个会计年度。

    返回：
        ThreeYearStatements | None: 缓存有效时返回结果，否则返回 None。

    说明：
        缓存损坏、过期、版本不一致都按“没有缓存”处理，
        下次会重新访问新浪财经，不影响页面使用。
    """
    path = _cache_path(code, year)
    if not path.is_file():
        return None

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return None

        if payload.get("version") != CACHE_VERSION:
            return None

        saved_at = float(payload.get("saved_at", 0))
        if time.time() - saved_at > CACHE_TTL_SECONDS:
            return None

        result_data = payload.get("result")
        if not isinstance(result_data, dict):
            return None

        result = ThreeYearStatements.from_dict(result_data)
        result.from_cache = True
        return result

    except (
        OSError,
        ValueError,
        TypeError,
        KeyError,
        json.JSONDecodeError,
    ):
        return None


def _save_cache(result: ThreeYearStatements) -> None:
    """
    保存三张表结果到系统临时目录。

    参数：
        result: 要保存的结果。

    说明：
        先写临时文件再替换，避免写到一半程序退出留下坏文件；
        缓存写入失败不影响本次展示，下次会重新取数。
    """
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path = _cache_path(result.code, result.year)
        temp_path = path.with_suffix(".tmp")

        payload = {
            "version": CACHE_VERSION,
            "saved_at": time.time(),
            "result": result.to_dict(),
        }

        temp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temp_path.replace(path)

    except (OSError, TypeError, ValueError):
        return


# ---------------------------------------------------------------------------
# AKShare 取数和整理
# ---------------------------------------------------------------------------


def _import_akshare():
    """
    延迟导入 AKShare。

    返回：
        module: 已导入的 akshare 模块。

    异常：
        FinancialStatementError: 未安装 AKShare 时抛出。

    说明：
        AKShare 依赖较多、导入较慢，放在函数里按需导入，
        可以让网页启动和只查巨潮年报时不必等待它。
    """
    try:
        import akshare as ak
    except ImportError as exc:
        raise FinancialStatementError(
            "未安装 AKShare，无法获取近三年三张表。"
            "请在项目虚拟环境中运行："
            "python -m pip install -r requirements.txt"
        ) from exc

    return ak


def _friendly_akshare_error(exc: Exception) -> str:
    """
    把 AKShare/网络异常翻译成不吓人的中文提示。

    参数：
        exc: 原始异常。

    返回：
        str: 适合直接展示给用户的简短说明。
    """
    text = str(exc).strip()
    lowered = text.lower()

    if "timeout" in lowered or "timed out" in lowered:
        return "网络超时，请稍后重试。"
    if "connection" in lowered or "max retries" in lowered:
        return "无法连接新浪财经，请检查网络后重试。"
    if "http" in lowered or "status" in lowered:
        return "新浪财经接口返回异常，请稍后重试。"

    # 只保留前 200 个字符，避免把很长的技术堆栈直接铺到页面上。
    return (text[:200] + "……") if len(text) > 200 else text


def _sina_stock_code(code: str) -> str:
    """
    把 6 位股票代码转成新浪接口需要的“市场前缀 + 代码”。

    参数：
        code: 6 位沪深 A 股代码。

    返回：
        str: 例如 sh600519、sz000888。

    异常：
        FinancialStatementError: 不是沪深 A 股代码时抛出。
    """
    if code.startswith("6"):
        return f"sh{code}"
    if code.startswith(("0", "3")):
        return f"sz{code}"

    raise FinancialStatementError(
        "近三年三张表目前只支持沪深 A 股（60、68、00、30 开头）。"
        "北交所、港股、美股暂不支持。"
    )


def _normalize_code(code: str | int) -> str:
    """整理股票代码；输入不合法时转换成页面能直接展示的中文异常。"""
    try:
        return normalize_stock_code(code)
    except CninfoError as exc:
        raise FinancialStatementError(str(exc)) from exc


def _validate_year(year: int) -> None:
    """
    检查会计年度是否合理。

    参数：
        year: 用户请求的最近一个会计年度。

    异常：
        FinancialStatementError: 年份不是整数、太早或尚未披露时抛出。
    """
    if not isinstance(year, int):
        raise FinancialStatementError(
            "年份必须是整数，例如 2025。"
        )

    current_year = datetime.now().year
    max_supported_year = current_year - 1

    if year < 2000:
        raise FinancialStatementError(
            "年份太早，暂只支持 2000 年及以后的三张表。"
        )

    if year > max_supported_year:
        raise FinancialStatementError(
            f"{year} 年年度报告通常尚未披露。"
            f"请选择 {max_supported_year} 年或更早的年份。"
        )


def _is_consolidated_annual(record: dict) -> bool:
    """
    判断一行数据是否是“合并期末”的年度报告。

    参数：
        record: 新浪接口返回的一行字典。

    返回：
        bool: 是合并年报返回 True。

    说明：
        - 报告日必须以 1231 结尾，排除一季报、半年报和三季报；
        - 类型含“母公司”的一律排除，避免母公司与合并报表混用；
        - 类型缺失时，只有明确标记“是否审计=是”才接受，
          宁可少取，也不把口径不明的数字放进尽调表。
    """
    date_text = _normalize_date(record.get("报告日"))
    if not date_text.endswith("1231"):
        return False

    type_text = _clean_text(record.get("类型"))
    if "母公司" in type_text:
        return False
    if "合并" in type_text:
        return True

    if not type_text:
        return _clean_text(record.get("是否审计")) == "是"

    return False


def _record_priority(record: dict) -> tuple:
    """
    同一报告日出现多行时，决定优先使用哪一行。

    参数：
        record: 新浪接口返回的一行字典。

    返回：
        tuple: 供 max() 比较的优先级。

    说明：
        优先“合并期末”“已审计”“定期报告”和较新的公告日期。
        年报偶尔会有更正后版本，公告日期更新的那一行更可信。
    """
    type_text = _clean_text(record.get("类型"))
    return (
        1 if "合并" in type_text else 0,
        1 if "期末" in type_text else 0,
        1 if _clean_text(record.get("是否审计")) == "是" else 0,
        1 if _clean_text(record.get("数据源")) == "定期报告" else 0,
        _clean_text(record.get("公告日期")),
    )


def _pick_record(
    records: list[dict],
    year: int,
) -> dict | None:
    """
    从接口返回的所有期间里，挑出指定年份的合并年报。

    参数：
        records: 新浪接口返回的行列表。
        year: 目标会计年度。

    返回：
        dict | None: 找到时返回该行，否则返回 None。
    """
    target_date = f"{year}1231"
    candidates = [
        record
        for record in records
        if isinstance(record, dict)
        and _is_consolidated_annual(record)
        and _normalize_date(record.get("报告日")) == target_date
    ]

    if not candidates:
        return None

    return max(candidates, key=_record_priority)


def _call_sina_statement(
    ak,
    stock: str,
    spec: _StatementSpec,
) -> tuple[list[str], list[dict]]:
    """
    调用 AKShare 的新浪财报接口，取一张表。

    参数：
        ak: 已导入的 akshare 模块。
        stock: 带市场前缀的代码，例如 sh600519。
        spec: 这张表的取数配置。

    返回：
        tuple[list[str], list[dict]]:
            - 原始列名顺序；
            - 每一行的字典。

    异常：
        FinancialStatementError: 接口调用失败或返回格式异常时抛出。
    """
    try:
        frame = ak.stock_financial_report_sina(
            stock=stock,
            symbol=spec.sina_symbol,
        )
    except Exception as exc:
        raise FinancialStatementError(
            f"{spec.title}获取失败：{_friendly_akshare_error(exc)}"
        ) from exc

    if frame is None:
        raise FinancialStatementError(
            f"{spec.title}获取失败：接口没有返回数据。"
        )

    columns = [
        str(column).strip()
        for column in getattr(frame, "columns", [])
    ]

    try:
        records = frame.to_dict("records")
    except (AttributeError, TypeError, ValueError) as exc:
        raise FinancialStatementError(
            f"{spec.title}获取失败：接口返回格式无法识别。"
        ) from exc

    if not isinstance(records, list):
        raise FinancialStatementError(
            f"{spec.title}获取失败：接口返回格式无法识别。"
        )

    return columns, [
        record
        for record in records
        if isinstance(record, dict)
    ]


def _drop_redundant_columns(
    columns: list[str],
    value_matrix: dict[str, list[float | None]],
) -> list[str]:
    """
    去掉能被明细列替代的汇总列。

    参数：
        columns: 当前保留的列名，按接口原顺序。
        value_matrix: 每列三年原始数值。

    返回：
        list[str]: 去掉重复汇总后的列名。

    说明：
        例如“应收票据及应收账款”只是“应收票据 + 应收账款”的合计。
        只有明细列至少有一个数字时才删除汇总列；如果明细全空，
        保留汇总列，避免把公司真实存在的金额删掉。
    """
    kept: list[str] = []

    for column in columns:
        detail_columns = _REDUNDANT_AGGREGATES.get(column)

        if detail_columns is None:
            kept.append(column)
            continue

        has_detail_value = any(
            any(
                value is not None
                for value in value_matrix.get(detail, [])
            )
            for detail in detail_columns
        )

        if not has_detail_value:
            kept.append(column)

    return kept


def _display_label(column: str) -> str:
    """
    把接口列名整理成更适合页面阅读的科目名。

    参数：
        column: 接口原始列名。

    返回：
        str: 展示用名称。

    说明：
        新浪接口沿用老式写法“减:库存股”，中文冒号更符合阅读习惯；
        其余科目保持原名，方便和巨潮年报逐项对照。
    """
    label = column.strip()
    if label == "减:库存股":
        return "减：库存股"
    return label


def _build_table_rows(
    spec: _StatementSpec,
    columns: list[str],
    records_by_year: dict[int, dict | None],
    years: list[int],
) -> tuple[list[dict[str, str]], list[str]]:
    """
    把接口的宽表整理成“项目 + 三年三列”的长表。

    参数：
        spec: 这张表的配置。
        columns: 接口原始列顺序。
        records_by_year: 每个年度对应的原始行；缺失为 None。
        years: 三个会计年度，从新到旧。

    返回：
        tuple[list[dict[str, str]], list[str]]:
            - 展示行；
            - 这张表的提示。

    说明：
        - 三年都没有数字的科目直接隐藏，避免几十行“-”；
        - 有数字的科目全部保留，满足“尽可能详细”；
        - 列顺序沿用接口顺序，和正式报表的排列基本一致。
    """
    # 先按接口原顺序保留候选列，去掉元数据、章节标题和固定隐藏列。
    candidate_columns = [
        column
        for column in columns
        if column and column not in spec.hidden_columns
    ]

    # 计算每列三年原始数值，后续用它判断“整列为空”和“汇总列去重”。
    value_matrix: dict[str, list[float | None]] = {}
    for column in candidate_columns:
        value_matrix[column] = [
            _to_float(record.get(column)) if record else None
            for record in (
                records_by_year[year] for year in years
            )
        ]

    # 三年全空的列没有展示价值，先删除，表格会短很多。
    candidate_columns = [
        column
        for column in candidate_columns
        if any(
            value is not None
            for value in value_matrix[column]
        )
    ]

    # 再去掉能用明细替代的汇总列，避免同一笔钱重复出现。
    candidate_columns = _drop_redundant_columns(
        candidate_columns,
        value_matrix,
    )

    rows: list[dict[str, str]] = []
    for column in candidate_columns:
        row = {"项目": _display_label(column)}
        for year, value in zip(
            years,
            value_matrix[column],
        ):
            row[str(year)] = _format_amount(
                value,
                per_share=column in spec.per_share_items,
            )
        rows.append(row)

    warnings: list[str] = []
    missing_years = [
        year
        for year in years
        if records_by_year.get(year) is None
    ]
    if missing_years:
        warnings.append(
            "未找到以下年份的合并年报："
            + "、".join(f"{year} 年" for year in missing_years)
            + "（可能未上市、尚未披露或接口未收录）。"
        )

    return rows, warnings


def _collect_currencies(records: list[dict]) -> set[str]:
    """
    收集接口返回的币种。

    参数：
        records: 三张表各自的年度行。

    返回：
        set[str]: 例如 {"CNY"}；没有币种信息时返回空集合。

    说明：
        A 股财报通常是人民币。如果接口返回其他币种，
        除以 1 亿只能当数量级参考，页面必须提示用户没有做汇率换算。
    """
    currencies: set[str] = set()
    for record in records:
        text = _clean_text(record.get("币种")).upper()
        if text:
            currencies.add(text)
    return currencies


# ---------------------------------------------------------------------------
# 对外入口
# ---------------------------------------------------------------------------


def fetch_three_year_statements(
    code: str | int,
    year: int,
    force_refresh: bool = False,
    progress_callback: Callable[[str], None] | None = None,
) -> ThreeYearStatements:
    """
    获取某家公司近三年的资产负债表、利润表和现金流量表。

    参数：
        code: 股票代码，支持 "600519"、"SH600519"、"600519.SH" 等写法。
        year: 最近一个会计年度，例如 2025；会自动取 2025/2024/2023。
        force_refresh: 为 True 时忽略 24 小时缓存，重新访问新浪财经。
        progress_callback: 页面进度回调，接收中文阶段说明。

    返回：
        ThreeYearStatements: 三张表的结构化结果，可直接展示和给专家使用。

    异常：
        FinancialStatementError: 代码不支持、年份不合理、AKShare 未安装、
                                网络失败或三张表全部取不到时抛出。

    说明：
        - 默认优先使用 24 小时缓存；
        - 单张表失败不会丢掉其他两张表，失败原因放在 warnings 中；
        - 只有三张表都取到数据时才写缓存，避免把临时失败固化一天；
        - 只取合并年报，不取季报和母公司报表。
    """
    normalized_code = _normalize_code(code)
    _validate_year(year)

    if not force_refresh:
        cached = _load_cache(normalized_code, year)
        if cached is not None:
            _notify(
                progress_callback,
                "已读取 24 小时内的三张表缓存，无需重新访问新浪财经。",
            )
            return cached

    years = [year, year - 1, year - 2]
    stock = _sina_stock_code(normalized_code)
    ak = _import_akshare()

    tables: dict[str, StatementTable] = {}
    warnings: list[str] = []
    all_currencies: set[str] = set()
    failed_count = 0

    for spec in STATEMENT_SPECS:
        _notify(
            progress_callback,
            f"正在从新浪财经获取{spec.title}……",
        )

        try:
            columns, records = _call_sina_statement(
                ak,
                stock,
                spec,
            )
        except FinancialStatementError as exc:
            failed_count += 1
            message = str(exc)
            warnings.append(message)
            tables[spec.key] = StatementTable(
                key=spec.key,
                title=spec.title,
                years=years,
                rows=[],
                warnings=[message],
            )
            continue

        records_by_year = {
            target_year: _pick_record(records, target_year)
            for target_year in years
        }

        all_currencies.update(
            _collect_currencies(
                [
                    record
                    for record in records_by_year.values()
                    if record is not None
                ]
            )
        )

        rows, table_warnings = _build_table_rows(
            spec,
            columns,
            records_by_year,
            years,
        )

        tables[spec.key] = StatementTable(
            key=spec.key,
            title=spec.title,
            years=years,
            rows=rows,
            warnings=table_warnings,
        )

    if failed_count == len(STATEMENT_SPECS):
        raise FinancialStatementError(
            "三张表都没有取到数据。"
            + " ".join(warnings)
            + " 巨潮年报不受影响，可以稍后重试。"
        )

    if all_currencies and all_currencies != {"CNY"}:
        warnings.append(
            "接口返回的币种为 "
            + "、".join(sorted(all_currencies))
            + "，本表按原数字除以 1 亿展示，未做汇率换算。"
        )

    complete = (
        failed_count == 0
        and all(
            tables.get(spec.key) is not None
            and bool(tables[spec.key].rows)
            for spec in STATEMENT_SPECS
        )
    )

    if not complete:
        warnings.append(
            "本次三张表没有全部取到，未写入缓存；"
            "下次点击“获取官方年报与三年报表”会自动重试。"
        )

    result = ThreeYearStatements(
        code=normalized_code,
        year=year,
        years=years,
        tables=tables,
        source="AKShare（新浪财经）",
        fetched_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        warnings=_deduplicate(warnings),
        from_cache=False,
    )

    if complete:
        _save_cache(result)

    _notify(progress_callback, "近三年三张表已就绪。")
    return result


def merge_agent_contexts(
    annual_context: str,
    statements_context: str,
    max_total_chars: int = MAX_COMBINED_CONTEXT_CHARS,
) -> str:
    """
    把三张表数据包合并进巨潮年报数据包。

    参数：
        annual_context: 巨潮年报数据包，末尾带 </annual_report>。
        statements_context: 三张表数据包。
        max_total_chars: 合并后的总字符上限。

    返回：
        str: 合并后的专家输入文本。

    说明：
        - 三张表放在 </annual_report> 之前，仍然处于“资料不是指令”的
          标签边界内；
        - 如果 session_state 里已经有旧的三张表段落，先删除再追加，
          避免重复叠加；
        - 年报本身已经很长时，三张表会自动缩短，优先保证年报正文。
    """
    annual = (annual_context or "").strip()
    statements = (statements_context or "").strip()

    if not statements:
        return annual
    if not annual:
        return statements

    # 删除上一次合并留下的三张表段落，保证重复获取时不会越叠越长。
    annual = re.sub(
        r"\n*<financial_statements>.*?</financial_statements>\n*",
        "\n",
        annual,
        flags=re.DOTALL,
    ).strip()

    available = max_total_chars - len(annual) - 40

    if available < 2_000:
        # 年报正文已经接近上限，不再硬塞表格；
        # 明确告诉模型三张表存在但没放进输入，避免它编造。
        statements = (
            "（三张表已获取，但年报数据包已接近输入上限，"
            "未把三张表放进本次提示词；完整表格可在页面查看。）"
        )
    elif len(statements) > available:
        statements = (
            statements[: max(0, available - 120)].rstrip()
            + "\n\n（三张表数据包过长，已截断；"
            "完整数据请在页面表格查看。）"
            + "\n</financial_statements>"
        )

    block = "\n\n" + statements + "\n\n"

    if "</annual_report>" in annual:
        return annual.replace(
            "</annual_report>",
            block + "</annual_report>",
            1,
        )

    return annual + block
