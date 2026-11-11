"""
公开网页搜索工具（Web Searcher，两轮深度搜索 + 解析路由版）。

职责：
- 根据用户输入的公司名称，执行“两轮深度搜索”：
  第一轮用固定专业模板查巨潮资讯网、官网/投资者关系和财务数据；
  第二轮让 DeepSeek 根据第一轮结果生成补搜词，查漏补缺；
- 每条查询向博查要 10 条候选，本地按权威度、官网匹配和新鲜度排序，
  过滤黑名单、文档分享站、去重后只保留前 3 条；
- 最多抓取 2 个重点网页的正文，识别新闻列表和其中的 PDF 年报链接；
- 对抓取到的内容按类型自动路由：
  HTML 用 lxml 提取正文；正文过短的动态页用 Playwright 重新渲染；
  直接指向 PDF 的链接用 pdfplumber 提取文字和表格并转成 Markdown；
- 网络失败、未配置密钥或没有结果时，返回中文友好提示，而不是让网页崩溃。

为什么这样设计：
- 只靠一组固定搜索词，容易漏掉诉讼、处罚或最新公告；
- 只靠大模型自由写搜索词，又可能漏掉财务方向或写出博查不支持的语法；
- 因此第一轮用固定模板“保底”，第二轮用模型“补漏”，代码再检查覆盖范围。
- 公开网页上的二手文档站（道客巴巴、人人文档、百度文库等）经常转载旧资料，
  本模块会自动排除，并优先巨潮、交易所、政府/法院和权威财经媒体。
- 单一 HTML 解析无法覆盖动态官网和 PDF 年报，所以把“怎么读”拆到
  utils/parser_router.py，由本模块负责网络请求和安全校验。

安全说明（很重要）：
- 博查 API Key 只从环境变量 BOCHA_API_KEY 读取；
- DeepSeek API Key 只由 query_optimizer 从 DEEPSEEK_API_KEY 读取；
- 本模块不打印、不拼接、不保存任何密钥；
- 最多抓取 2 个重点网页的正文，只读取有限字节，不递归抓取；
- PDF 只解析搜索结果或页面中检测到的链接，限制文件大小、页数和字符数；
- 抓取网页前会拒绝内网地址、localhost 和非标准端口，防止被恶意链接利用；
- Playwright 渲染时同样会检查每一个网络请求，拒绝内网地址；
- 不登录、不携带 Cookie、不绕过付费墙或访问控制；
- 抓到的正文只放在本次搜索的内存结果中，不写入文件、不保存数据库；
- 搜索结果属于未经核实的外部内容，开头会明确标注“未经核实”，
  供后续智能体按公开信息初筛模式使用；
- 外部摘要和网页正文中的尖括号会被替换为全角字符，防止伪造提示词标签。
"""

import ipaddress
import json
import os
import re
import socket
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import (
    parse_qsl,
    unquote,
    urlencode,
    urljoin,
    urlparse,
    urlunparse,
)
from urllib.request import (
    HTTPRedirectHandler,
    Request,
    build_opener,
    urlopen,
)

from bs4 import BeautifulSoup
from dotenv import load_dotenv
from pypinyin import Style, lazy_pinyin

from utils.parser_router import (
    BrowserRenderError,
    PdfParseError,
    extract_main_content,
    needs_browser_rendering,
    parse_pdf_to_markdown,
    render_dynamic_page,
)
from utils.query_optimizer import (
    SecondRoundPlan,
    optimize_second_round_queries,
    recent_years,
)

# 项目根目录：本文件在 utils/ 下，向上一级就是项目根目录。
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 读取 .env 中的环境变量。
# override=False 表示系统环境变量优先，不会覆盖启动页面前已经设置好的值。
load_dotenv(dotenv_path=PROJECT_ROOT / ".env")

# 博查网页搜索接口地址。
BOCHA_API_URL = "https://api.bochaai.com/v1/web-search"

# 保存博查密钥的环境变量名。
BOCHA_API_KEY_ENV = "BOCHA_API_KEY"

# 每条查询向博查要多少条候选。
# 多要候选不会增加博查的单次调用费，但可以给本地排序和过滤留出空间。
CANDIDATES_PER_QUERY = 10

# 排序过滤后，每条查询最终保留几条来源。
RESULTS_PER_QUERY = 3

# 同一个网站最多保留几条来源，避免一个网站刷屏。
MAX_SOURCES_PER_DOMAIN = 2

# 博查请求的时间范围：noLimit 表示不限发布时间。
# 财务和诉讼信息经常来自几年前，限制时间范围会漏掉重要材料；
# 旧资料的新鲜度会由本地排序降权，而不是直接丢弃。
BOCHA_FRESHNESS = "noLimit"

# 单次搜索请求的超时时间（秒）。
SEARCH_TIMEOUT_SECONDS = 15

# 标题、链接和摘要的最大保留长度。
MAX_TITLE_CHARS = 200
MAX_LINK_CHARS = 500
MAX_SNIPPET_CHARS = 600

# 官网和年报关键词的额外加分。
# 权威站点最高只有 100 分，这两个加分足以让官网和年报排到最前面。
OFFICIAL_SITE_BONUS = 300
ANNUAL_REPORT_URL_BONUS = 180

# 网页正文抓取限制。
# 只抓 2 个重点页面，HTML 单页最多读 1.5 MB，正文最多保留 4000 字，
# 避免因为某个网站很慢或页面很大而拖垮整个搜索。
MAX_BODY_PAGES = 2
PAGE_FETCH_TIMEOUT_SECONDS = 10
MAX_HTML_BYTES = 1_500_000
MAX_PAGE_BODY_CHARS = 4000
MAX_PAGE_LIST_ITEMS = 8
MAX_PAGE_PDF_LINKS = 5

# PDF 文件单独限制大小。
# 年报可能比普通网页大得多，但也不能无限读取：
# 20 MB 已经覆盖绝大多数 A 股年报，再大就容易拖慢网络和内存。
MAX_PDF_BYTES = 20_000_000

# 抓取网页时使用的浏览器标识。
# 有些网站会拒绝 Python 默认的 User-Agent；这里使用普通浏览器标识，
# 但不伪装成登录用户，也不绕过付费墙或访问限制。
PAGE_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36"
)

# 检测到 PDF 年报时展示给用户的固定提示。
# 系统最多自动解析 2 个重点网页/PDF；如果年报较多、是扫描图片
# 或超过大小限制，仍然建议用户下载后用 Tab 1 上传分析。
PDF_ANNUAL_REPORT_HINT = (
    "检测到官方 PDF 年报，系统会优先尝试自动解析其中的文字和表格；"
    "如链接较多或内容不完整，建议下载后使用 Tab 1 上传分析。"
)

# 判断链接或标题是否在说“年报”的关键词。
ANNUAL_REPORT_KEYWORDS = (
    "年报",
    "年度报告",
    "annual report",
    "annual-report",
    "annual_report",
    "annualreport",
)

# 新闻列表页常见的关键词，用于优先抓取这类页面。
LIST_PAGE_KEYWORDS = (
    "新闻",
    "资讯",
    "公告",
    "列表",
    "频道",
    "news",
    "list",
    "channel",
    "roll",
)

# 过于常见、容易误判官网的拼音词。
# 例如任何公司名都可能包含“科技”，不能拿 jishu 去匹配域名。
_GENERIC_PINYIN_WORDS = {
    "zhongguo",
    "gongsi",
    "youxian",
    "zeren",
    "gufen",
    "jituan",
    "konggu",
    "keji",
    "jishu",
    "shiye",
    "fazhan",
}

# 固定提示前缀。
# orchestrator.py 会通过 is_search_failure() 判断是否需要跳过智能体分析，
# 因此这些前缀不能随便改动。
SEARCH_FAILURE_PREFIX = "【搜索失败】"
SEARCH_EMPTY_PREFIX = "【未检索到结果】"
SEARCH_PARTIAL_PREFIX = "【部分查询失败】"

# 用户明确要求丢弃的站点。
# 雪球、知乎、百度以用户讨论和转载为主；东方财富整站删除，
# 会连它的公告页和财务数据页一起排除，这是用户确认后的选择。
BLACKLISTED_DOMAINS = (
    "xueqiu.com",
    "eastmoney.com",
    "zhihu.com",
    "baidu.com",
)

# 要排除的文档分享、文库类网站。
# 这些站点经常转载旧资料，标题看起来专业，但内容可能残缺或过时。
# 黑名单和文档站都放在这里，本地过滤和博查 exclude 参数会同时生效；
# 即使博查拒绝 exclude 参数，本地 _is_excluded_domain() 仍会兜底删除。
EXCLUDED_DOMAINS = BLACKLISTED_DOMAINS + (
    "doc88.com",
    "renrendoc.com",
    "book118.com",
    "docin.com",
    "360doc.com",
    "wenku.so.com",
    "doc.mbalib.com",
    "ishare.iask.sina.com.cn",
    "doc.quark.cn",
)

# 博查的 exclude 参数使用竖线分隔多个域名。
EXCLUDE_PARAM = "|".join(EXCLUDED_DOMAINS)

# 权威站点加分表。
# 分数不是“绝对真理”，只是让排序更偏向官方披露和主流财经媒体。
# 匹配时支持子域名，例如 wenshu.court.gov.cn 会命中 court.gov.cn。
AUTHORITY_DOMAIN_SCORES: dict[str, int] = {
    "cninfo.com.cn": 100,      # 巨潮资讯网：A 股法定披露平台
    "sse.com.cn": 95,          # 上海证券交易所
    "szse.cn": 95,             # 深圳证券交易所
    "bse.cn": 95,              # 北京证券交易所
    "hkexnews.hk": 90,         # 香港交易所披露易
    "sec.gov": 85,             # 美国 SEC
    "gov.cn": 90,              # 各级政府网站
    "court.gov.cn": 90,        # 法院系统（含裁判文书、执行信息）
    "spp.gov.cn": 85,          # 最高人民检察院
    "samr.gov.cn": 85,         # 市场监督管理总局
    "creditchina.gov.cn": 85,  # 信用中国
    "csrc.gov.cn": 90,         # 证监会
    "pbc.gov.cn": 85,          # 中国人民银行
    "mof.gov.cn": 85,          # 财政部
    "nfra.gov.cn": 85,         # 金融监管总局
    "chinatax.gov.cn": 80,     # 税务总局
    "stats.gov.cn": 75,        # 统计局
    "cs.com.cn": 80,           # 中证网
    "stcn.com": 80,            # 证券时报
    "cnstock.com": 80,         # 上海证券报
    "yicai.com": 75,           # 第一财经
    "caixin.com": 75,          # 财新
    "21jingji.com": 70,        # 21 世纪经济报道
    "jiemian.com": 70,         # 界面新闻
    "nbd.com.cn": 70,          # 每日经济新闻
    "xinhuanet.com": 70,       # 新华网
    "news.cn": 70,             # 新华社
    "people.com.cn": 70,       # 人民网
    "cctv.com": 60,            # 央视网
}

# 用于判断搜索词方向的关键词。
RISK_KEYWORDS = (
    "诉讼",
    "处罚",
    "行政处罚",
    "被执行",
    "失信",
    "股权冻结",
    "违约",
    "仲裁",
    "立案",
    "监管",
    "问询",
    "风险",
)
BUSINESS_KEYWORDS = (
    "主营业务",
    "业务",
    "客户",
    "供应商",
    "竞争",
    "行业",
    "政策",
    "产品",
    "市场",
)
FINANCE_KEYWORDS = (
    "营业收入",
    "营收",
    "净利润",
    "利润",
    "现金流",
    "财务",
    "年报",
    "财报",
    "年度报告",
    "公告",
)
OFFICIAL_KEYWORDS = ("官网", "官方网站", "投资者关系", "公告")

# 第一轮固定搜索模板。
# purpose 用于后续排序和结果说明；label 用于页面进度提示。
FIRST_ROUND_SPECS = (
    {
        "purpose": "cninfo",
        "label": "巨潮资讯网年报",
        "query_template": "{company_name} 年度报告 年报 财报 {years}",
        "include": "cninfo.com.cn",
        "allow_pdf_fallback": False,
    },
    {
        "purpose": "official",
        "label": "官网、投资者关系与 PDF 年报",
        "query_template": (
            "{company_name} 官网 投资者关系 年报 财报 "
            "{years} filetype:pdf"
        ),
        "include": "",
        "allow_pdf_fallback": True,
    },
    {
        "purpose": "finance",
        "label": "营业收入、利润与现金流",
        "query_template": (
            "{company_name} 营业收入 净利润 经营现金流 {years}"
        ),
        "include": "",
        "allow_pdf_fallback": False,
    },
)

# 匹配 filetype:pdf，用于在需要时去掉这个限定。
_PDF_OPERATOR_PATTERN = re.compile(r"(?i)filetype\s*:\s*pdf")

# 公司名称末尾的法定组织形式。
# 搜索时去掉“股份有限公司”等后缀，通常能提高搜索引擎的匹配效果；
# 例如“贵州茅台酒股份有限公司”用“贵州茅台酒”搜索更精准。
_LEGAL_SUFFIX_PATTERN = re.compile(
    r"(?:股份有限公司|有限责任公司|有限公司|集团有限公司|集团|公司)$"
)

# 括号中的补充说明，例如“某某科技（上海）有限公司”。
# 搜索时先去掉，避免括号干扰关键词匹配。
_PARENTHESES_PATTERN = re.compile(r"[（(][^）)]*[）)]")

# 过于宽泛、不适合作为公司别名开头的词。
# 例如“中国”会匹配大量公司，不能只凭这两个字判断相关性。
_GENERIC_ALIAS_WORDS = {
    "中国",
    "股份",
    "有限",
    "责任",
    "集团",
    "控股",
    "科技",
    "技术",
    "实业",
    "发展",
    "公司",
}


class BochaSearchError(RuntimeError):
    """
    博查搜索失败时使用的内部异常。

    参数：
        user_message: 可以直接展示给用户的中文原因，不能包含 API Key。
        category: 错误类别，供重试逻辑判断，例如
                  bad_request / auth / balance / rate_limit / server /
                  timeout / network / format / api_error。

    说明：
        本异常只在模块内部使用，最终会被转换成统一格式的友好提示。
    """

    def __init__(self, user_message: str, category: str = "unknown"):
        super().__init__(user_message)
        self.user_message = user_message
        self.category = category


@dataclass
class QueryOutcome:
    """
    单条查询的执行结果。

    属性：
        round_label: “第一轮”或“第二轮”。
        purpose: 查询方向，例如 cninfo / official / finance / risk。
        query: 实际发送给博查的检索语句（可能因回退而调整）。
        include: 限定的站点范围；空字符串表示不限站点。
        results: 排序过滤后保留的来源列表。
        error: 搜索失败时的博查异常；成功时为 None。
        raw_count: 博查原始返回的候选数量。
        pdf_fallback: 是否因 PDF 限定无结果而自动去掉 filetype:pdf。
        relaxed_fallback: 是否因接口拒绝站点筛选而自动放宽后重搜。
        duplicates_removed: 汇总时因与前面来源重复而合并掉的数量。
    """

    round_label: str
    purpose: str
    query: str
    include: str
    results: list[dict]
    error: BochaSearchError | None = None
    raw_count: int = 0
    pdf_fallback: bool = False
    relaxed_fallback: bool = False
    duplicates_removed: int = 0


@dataclass
class PageExtract:
    """
    单个网页或 PDF 的正文提取结果。

    属性：
        url: 实际抓取的网页地址。
        is_list: 是否判断为新闻/资讯列表页。
        body: 提取到的正文文本；PDF 内容是 Markdown，已限制长度。
        list_items: 列表页中的条目，每项包含 title 和 url。
        pdf_links: 页面中检测到的 PDF 链接，每项包含 title 和 url。
        error: 抓取或解析失败时的中文原因；成功时为空字符串。
        parser: 实际使用的解析方式：lxml / playwright / pdfplumber。
        is_pdf: 该地址最终是否按 PDF 解析。
        pdf_page_count: PDF 总页数；非 PDF 时为 0。
        pdf_parsed_pages: PDF 实际读取的页数；非 PDF 时为 0。
        pdf_table_count: PDF 中转换成 Markdown 的表格数量。
        note: 给用户看的解析说明，例如“动态渲染失败，已退回普通解析”。
    """

    url: str
    is_list: bool = False
    body: str = ""
    list_items: list[dict] | None = None
    pdf_links: list[dict] | None = None
    error: str = ""
    parser: str = ""
    is_pdf: bool = False
    pdf_page_count: int = 0
    pdf_parsed_pages: int = 0
    pdf_table_count: int = 0
    note: str = ""


@dataclass
class _FetchedResource:
    """
    一次网络抓取的原始结果。

    属性：
        url: 请求的原始地址。
        final_url: 跟随重定向后的最终地址。
        content_type: HTTP 响应头中的内容类型，已转成小写。
        raw_bytes: 读取到的原始字节；超过上限时已截断。
        charset: 响应头声明的字符编码；可能为 None。
        truncated: 是否因为超过大小上限而截断。
    """

    url: str
    final_url: str
    content_type: str
    raw_bytes: bytes
    charset: str | None
    truncated: bool = False


def is_search_failure(text: str) -> bool:
    """
    判断搜索函数返回的是否为“失败”或“没有结果”的提示文本。

    参数：
        text: search_company_info() 的返回值，或任意待检查文本。

    返回：
        bool: True 表示搜索结果不可用，调用方不应继续把它们交给智能体；
              False 表示至少取得了一部分搜索结果。
    """
    if not isinstance(text, str):
        return True

    stripped_text = text.lstrip()
    return stripped_text.startswith(
        (SEARCH_FAILURE_PREFIX, SEARCH_EMPTY_PREFIX)
    )


def _clean_external_text(value: object, max_chars: int) -> str:
    """
    清理搜索引擎返回的文本，避免超长内容和伪造标签。

    参数：
        value: 原始字段值，正常应为字符串；其他类型会被转成空字符串。
        max_chars: 最多保留多少个字符。

    返回：
        str: 去掉首尾空白、压缩连续空白、限制长度后的安全文本。
    """
    if not isinstance(value, str):
        return ""

    text = " ".join(value.split())
    text = text.replace("<", "＜").replace(">", "＞")

    if len(text) > max_chars:
        text = text[: max_chars - 1].rstrip() + "…"

    return text


def _read_bocha_api_key() -> str:
    """
    从环境变量读取博查 API Key。

    返回：
        str: 清理过首尾空白的密钥；未配置或仍是示例占位文字时返回空字符串。
    """
    api_key = os.getenv(BOCHA_API_KEY_ENV, "")
    if not isinstance(api_key, str):
        return ""

    api_key = api_key.strip()
    placeholder_values = {
        "your_bocha_api_key_here",
        "your_bocha_api_key",
        "your-bocha-api-key",
    }
    if not api_key or api_key.lower() in placeholder_values:
        return ""

    return api_key


def _short_company_name(company_name: str) -> str:
    """
    去掉公司名称中的括号说明和法定组织形式，得到适合搜索的简称。

    参数：
        company_name: 用户输入的公司全称。

    返回：
        str: 去掉“股份有限公司”“有限公司”等后缀后的名称；
             去掉后为空时返回原名称。

    说明：
        搜索引擎对“贵州茅台酒股份有限公司”这种长全称的匹配效果，
        往往不如“贵州茅台酒”。这里只影响检索语句，页面仍显示用户输入的全称。
    """
    cleaned = " ".join(company_name.split())
    cleaned = _PARENTHESES_PATTERN.sub("", cleaned).strip()
    short_name = _LEGAL_SUFFIX_PATTERN.sub("", cleaned).strip()
    return short_name or cleaned


def _company_aliases(company_name: str) -> tuple[str, ...]:
    """
    生成用于判断搜索结果是否属于该公司的别名列表。

    参数：
        company_name: 用户输入的公司全称。

    返回：
        tuple[str, ...]: 至少包含全称和去掉组织形式后的简称；
                         中文名称还会加入前四个字和前两个字（过于宽泛的除外）。

    说明：
        博查的站点限定只能保证结果来自巨潮等网站，不能保证是这家公司。
        实测中，“贵州茅台”的巨潮查询曾返回江苏国茂、凌云工业的年报。
        因此需要用公司别名做一次相关性过滤，避免把别家公司的资料当成自己的。
    """
    full_name = " ".join(company_name.split())
    short_name = _short_company_name(full_name)
    compact_name = short_name.replace(" ", "")

    aliases: set[str] = {full_name, short_name}

    # 中文公司名加入前四个字，例如“贵州茅台酒”->“贵州茅台”。
    # 只有中文才这样截取，避免英文名称被截成无意义的字母组合。
    if re.search(r"[一-鿿]", compact_name):
        if len(compact_name) >= 4:
            aliases.add(compact_name[:4])
        if len(compact_name) >= 2:
            first_two = compact_name[:2]
            if first_two not in _GENERIC_ALIAS_WORDS:
                aliases.add(first_two)

    return tuple(
        alias for alias in aliases if len(alias) >= 2
    )


def _matches_company(
    text: str,
    company_aliases: tuple[str, ...],
) -> bool:
    """
    判断一段文本是否提到目标公司。

    参数：
        text: 网页标题和摘要拼成的文本。
        company_aliases: _company_aliases() 生成的别名列表。

    返回：
        bool: 任意别名出现（不区分大小写）时返回 True。
    """
    lowered_text = text.lower()
    return any(
        alias.lower() in lowered_text for alias in company_aliases
    )


def _company_pinyin_aliases(company_name: str) -> tuple[str, ...]:
    """
    把公司名转换成拼音别名，用于识别官网域名。

    参数：
        company_name: 用户输入的公司全称。

    返回：
        tuple[str, ...]: 全拼、连续 2～4 个汉字的拼音、全名首字母；
                         过滤掉过短或过于宽泛的词。

    示例：
        “华为技术有限公司”会生成 huawei、jishu、huaweijishu、hwjs 等；
        “贵州茅台酒股份有限公司”会生成 maotai、guizhou、guizhoumaotai 等。
        这样 huawei.com、moutai.com.cn 这类域名就有机会被识别为官网。

    说明：
        拼音转换完全在本地完成，不调用外部服务，也不会上传公司名。
    """
    short_name = _short_company_name(company_name).replace(" ", "")
    chinese_text = "".join(re.findall(r"[一-鿿]", short_name))

    if not chinese_text:
        return ()

    aliases: set[str] = set()

    # 全名拼音，例如“华为技术”-> huaweijishu。
    full_pinyin = "".join(
        lazy_pinyin(
            chinese_text,
            style=Style.NORMAL,
            errors="ignore",
        )
    ).lower()
    if 4 <= len(full_pinyin) <= 40:
        aliases.add(full_pinyin)

    # 连续 2～4 个汉字的拼音，例如“茅台”-> maotai。
    # 这样即使官网使用品牌名而不是公司全称，也能匹配到。
    for length in (2, 3, 4):
        for start in range(0, len(chinese_text) - length + 1):
            segment = chinese_text[start : start + length]
            pinyin = "".join(
                lazy_pinyin(
                    segment,
                    style=Style.NORMAL,
                    errors="ignore",
                )
            ).lower()
            if (
                4 <= len(pinyin) <= 30
                and pinyin not in _GENERIC_PINYIN_WORDS
            ):
                aliases.add(pinyin)

    # 全名首字母，例如“贵州茅台酒”-> gzmtj。
    initials = "".join(
        lazy_pinyin(
            chinese_text,
            style=Style.FIRST_LETTER,
            errors="ignore",
        )
    ).lower()
    if 4 <= len(initials) <= 20:
        aliases.add(initials)

    return tuple(sorted(aliases, key=len, reverse=True))


def _official_site_bonus(
    href: str,
    title: str,
    company_pinyin_aliases: tuple[str, ...],
) -> int:
    """
    给官网和年报链接计算额外加分。

    参数：
        href: 网页链接。
        title: 网页标题。
        company_pinyin_aliases: _company_pinyin_aliases() 生成的拼音别名。

    返回：
        int: 官网匹配返回 300 分；链接或标题含 investor / annual report
             等字眼返回 180 分；都不匹配返回 0。

    说明：
        官网域名通常直接使用公司名拼音，例如 huawei.com、xiaomi.com。
        先解码 URL、去掉点和横线，再用拼音别名匹配，
        可以同时覆盖 huawei.com 和 huawei-tech.com 这类写法。
    """
    if not href:
        return 0

    decoded_url = unquote(href).lower()
    parsed = urlparse(decoded_url)
    host_and_path = (
        f"{parsed.netloc}{parsed.path}"
        .replace(".", "")
        .replace("-", "")
        .replace("_", "")
    )

    for alias in company_pinyin_aliases:
        if alias and alias in host_and_path:
            return OFFICIAL_SITE_BONUS

    searchable_text = f"{decoded_url} {title}".lower()
    for keyword in (
        "investor",
        "annual report",
        "annual-report",
        "annual_report",
        "annualreport",
    ):
        if keyword in searchable_text:
            return ANNUAL_REPORT_URL_BONUS

    return 0


def _is_pdf_url(url: object) -> bool:
    """
    判断链接是否指向 PDF 文件。

    参数：
        url: 网页链接。

    返回：
        bool: URL 路径以 .pdf 结尾或包含 .pdf 时返回 True。
    """
    if not isinstance(url, str) or not url.strip():
        return False

    try:
        path = urlparse(url.strip()).path.lower()
    except ValueError:
        return False

    return path.endswith(".pdf") or ".pdf" in path


def _is_annual_report_text(text: object) -> bool:
    """
    判断标题或链接文字是否在说“年报”。

    参数：
        text: 网页标题、链接文字或摘要。

    返回：
        bool: 命中“年报”“年度报告”“annual report”等关键词时返回 True。
    """
    if not isinstance(text, str):
        return False

    lowered_text = text.lower()
    return any(
        keyword.lower() in lowered_text
        for keyword in ANNUAL_REPORT_KEYWORDS
    )


def _http_error_category(status_code: int) -> str:
    """
    把 HTTP 状态码转换成内部错误类别。

    参数：
        status_code: HTTP 响应状态码。

    返回：
        str: 供重试逻辑使用的类别名称。
    """
    if status_code == 400:
        return "bad_request"
    if status_code in (401, 403):
        return "auth"
    if status_code == 402:
        return "balance"
    if status_code == 429:
        return "rate_limit"
    if 500 <= status_code <= 599:
        return "server"
    return "http"


def _http_error_message(status_code: int) -> str:
    """
    把博查接口的 HTTP 状态码翻译成用户能看懂的中文提示。

    参数：
        status_code: HTTP 响应状态码。

    返回：
        str: 不包含密钥、可直接展示的中文说明。
    """
    if status_code in (401, 403):
        return (
            "博查 API Key 无效或没有访问权限。请检查项目根目录 .env 文件中的 "
            "BOCHA_API_KEY 是否填写正确、是否已在博查开放平台启用，"
            "保存后重启网页服务再试。"
        )
    if status_code == 402:
        return (
            "博查账户余额不足或免费额度已用完。"
            "请登录博查开放平台查看用量并充值后再试。"
        )
    if status_code == 429:
        return (
            "博查搜索请求过于频繁，已被暂时限流。"
            "请等待约一分钟后重试；如果连续出现，请检查账户用量。"
        )
    if status_code == 400:
        return (
            "博查拒绝了本次搜索请求，可能是检索语句格式或站点筛选参数"
            "不符合要求。"
        )
    if 500 <= status_code <= 599:
        return "博查搜索服务暂时异常，请稍后重试。"
    return f"博查搜索服务返回异常状态（HTTP {status_code}），请稍后重试。"


def _extract_bocha_results(payload: object) -> list[dict]:
    """
    从博查接口返回的 JSON 中取出网页结果列表。

    参数：
        payload: 已经解析成 Python 对象的接口响应。

    返回：
        list[dict]: 统一成 title / href / body / published 四个字段的结果列表。
                    结构不符合预期时返回空列表。
    """
    if not isinstance(payload, dict):
        return []

    data = payload.get("data")
    if not isinstance(data, dict):
        return []

    web_pages = data.get("webPages")
    if not isinstance(web_pages, dict):
        return []

    raw_results = web_pages.get("value")
    if not isinstance(raw_results, list):
        return []

    normalized_results: list[dict] = []

    for item in raw_results:
        if not isinstance(item, dict):
            continue

        # summary 是请求 summary=true 后返回的较长摘要，通常比 snippet 信息更多。
        body = item.get("summary") or item.get("snippet") or ""
        title = item.get("name") or ""
        href = item.get("url") or item.get("displayUrl") or ""
        published = item.get("datePublished") or ""

        normalized_results.append(
            {
                "title": title,
                "href": href,
                "body": body,
                "published": published,
            }
        )

    return normalized_results


def _request_bocha(
    query: str,
    api_key: str,
    count: int = CANDIDATES_PER_QUERY,
    include: str = "",
    exclude: str = "",
    freshness: str = BOCHA_FRESHNESS,
) -> list[dict]:
    """
    向博查发送一次网页搜索请求，并返回原始结果列表。

    参数：
        query: 完整检索语句。
        api_key: 博查密钥；不会被写进异常提示。
        count: 请求返回多少条候选，博查允许 1～50。
        include: 只在这些站点内搜索；空字符串表示不限站点。
        exclude: 排除这些站点；空字符串表示不排除。
        freshness: 时间范围，默认 noLimit；可传 oneYear 等博查支持的值。

    返回：
        list[dict]: 整理后的搜索结果；接口正常但没有结果时返回空列表。

    异常：
        BochaSearchError: 网络失败、超时、密钥错误、余额不足、限流、
                          接口返回错误或数据格式异常时抛出。
    """
    # 防御性限制，避免调用方传入超出博查允许范围的数字。
    safe_count = max(1, min(int(count), 50))

    request_body: dict[str, object] = {
        "query": query,
        "count": safe_count,
        "summary": True,
        "freshness": freshness,
    }
    if include:
        request_body["include"] = include
    if exclude:
        request_body["exclude"] = exclude

    request_data = json.dumps(
        request_body, ensure_ascii=False
    ).encode("utf-8")

    request = Request(
        BOCHA_API_URL,
        data=request_data,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urlopen(request, timeout=SEARCH_TIMEOUT_SECONDS) as response:
            raw_body = response.read()
    except HTTPError as exc:
        raise BochaSearchError(
            _http_error_message(exc.code),
            _http_error_category(exc.code),
        ) from None
    except TimeoutError:
        raise BochaSearchError(
            "连接博查搜索服务超时。请检查网络后重试；"
            "如果持续超时，请检查代理或防火墙设置。",
            "timeout",
        ) from None
    except URLError as exc:
        if isinstance(exc.reason, TimeoutError):
            raise BochaSearchError(
                "连接博查搜索服务超时。请检查网络后重试。",
                "timeout",
            ) from None
        raise BochaSearchError(
            "无法连接博查搜索服务。请检查网络、代理或防火墙设置后重试。",
            "network",
        ) from None
    except OSError:
        raise BochaSearchError(
            "无法连接博查搜索服务。请检查网络、代理或防火墙设置后重试。",
            "network",
        ) from None

    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise BochaSearchError(
            "博查搜索服务返回的数据格式无法识别，请稍后重试。",
            "format",
        ) from None

    if not isinstance(payload, dict):
        raise BochaSearchError(
            "博查搜索服务返回的数据格式无法识别，请稍后重试。",
            "format",
        )

    api_code = payload.get("code")
    if api_code not in (None, 200, "200"):
        api_message = _clean_external_text(payload.get("msg"), 200)
        category = (
            "bad_request" if str(api_code) == "400" else "api_error"
        )
        if api_message:
            raise BochaSearchError(
                f"博查搜索服务返回错误（代码 {api_code}）：{api_message}",
                category,
            )
        raise BochaSearchError(
            f"博查搜索服务返回错误（代码 {api_code}），请稍后重试。",
            category,
        )

    return _extract_bocha_results(payload)


def _is_public_http_url(url: str) -> bool:
    """
    判断 URL 是否是安全的公网 HTTP/HTTPS 地址。

    参数：
        url: 待检查的网页链接。

    返回：
        bool: 只有公网、标准端口、没有用户名密码的 HTTP(S) 地址才返回 True。

    安全说明：
        搜索结果是外部数据，可能包含指向公司内网、路由器后台、云服务器
        元数据接口的恶意链接。抓取前必须解析域名并检查 IP，
        拒绝 localhost、内网、回环、保留地址和非 80/443 端口，
        防止服务器被利用去访问内部系统（SSRF 风险）。
    """
    if not isinstance(url, str) or not url.strip():
        return False

    try:
        parsed = urlparse(url.strip())
    except ValueError:
        return False

    if parsed.scheme not in ("http", "https"):
        return False
    if parsed.username or parsed.password:
        return False

    host = parsed.hostname or ""
    if not host or host.lower() in {"localhost", "localhost.localdomain"}:
        return False

    try:
        port = parsed.port
    except ValueError:
        return False

    if port not in (None, 80, 443):
        return False

    try:
        address_infos = socket.getaddrinfo(
            host,
            port or (443 if parsed.scheme == "https" else 80),
            type=socket.SOCK_STREAM,
        )
    except (socket.gaierror, OSError):
        return False

    if not address_infos:
        return False

    for address_info in address_infos:
        ip_text = address_info[4][0]
        try:
            ip_address = ipaddress.ip_address(ip_text)
        except ValueError:
            return False

        if (
            not ip_address.is_global
            or ip_address.is_private
            or ip_address.is_loopback
            or ip_address.is_link_local
            or ip_address.is_reserved
            or ip_address.is_multicast
            or ip_address.is_unspecified
        ):
            return False

    return True


class _SafeRedirectHandler(HTTPRedirectHandler):
    """
    只允许跳转到公网 HTTP(S) 地址的重定向处理器。

    说明：
        默认的 urllib 会自动跟随 301/302 跳转。如果第一个链接是公网地址，
        但跳转目标是 127.0.0.1 或公司内网，就会绕过前面的安全检查。
        这里在每次跳转前再次校验目标地址，校验失败就中止请求。
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        """在跟随重定向前检查新地址；不安全时抛出 URLError。"""
        if not _is_public_http_url(newurl):
            raise URLError("重定向目标不是公网 HTTP(S) 地址，已拒绝访问。")
        return super().redirect_request(
            req, fp, code, msg, headers, newurl
        )


# 网页编码识别相关常量。
# 中文网站常见 UTF-8、GBK、GB2312 三种编码，其中 GBK 和 GB2312
# 都可以用更完整的 GB18030 来解码，所以统一合并成 GB18030。
_CHARSET_ALIASES = {
    "gb2312": "gb18030",
    "gb_2312-80": "gb18030",
    "gbk": "gb18030",
    "x-gbk": "gb18030",
    "cp936": "gb18030",
    "ms936": "gb18030",
    "utf8": "utf-8",
}

# 只在网页开头这么多字节里寻找编码声明。
# <meta charset> 必须写在 <head> 中，通常出现在前几 KB。
_CHARSET_SCAN_BYTES = 8192

# 编码打分最多检查这么多个字符。
# 整页最多 1.5 MB，逐字打分没有必要；前 2 万个字符已经足够
# 判断“这是正常中文，还是编码读错后的乱码”。
_CHARSET_SCORE_SAMPLE_CHARS = 20000

# 常用汉字表：只用于给不同解码结果打分，不会过滤或改写正文。
# 正确解码出来的中文，绝大多数是下面这些高频字；
# 编码读错时会出现大量生僻字，命中率会明显下降。
# 末尾补充了一些财务、法律尽调常见字，提升专业材料的识别准确度。
_COMMON_CHINESE_CHARS = set(
    "的一是在不了有和人这中大为上个国我以要他时来用们生到作地于出就分对"
    "成会可主发年动同工也能下过子说产种面而方后多定行学法所民得经十三之"
    "进着等部度家电力里如水化高自二理起小物现实加量都两体制机当使点从业"
    "本去把性好应开它合还因由其些然前外天政四日那社义事平形相全表间样与"
    "关各重新线内数正心反你明看原又么利比或但质气第向道命此变条只没结解"
    "问意建月公无系军很情者最立代想已通并提直题党程展五果料象员革位入常"
    "文总次品式活设及管特件长求老头基资边流路级少图山统接知较将组见计别"
    "她手角期根论运农指几九区强放决西被干做必战先回则任取据处队南给色光"
    "门即保治北造百规热领七海口东导器压志世金增争济阶油思术极交受联什认"
    "六共权收证改清己美再采转更单风切打白教速花带安场身车例真务具万每目"
    "至达走积示议声报斗完类八离华名确才科张信马节话米整空元况今集温传土"
    "许步群广石记需段研界拉林律叫且究观越织装影算低持音众书布复容儿须际"
    "商非验连断深难近矿千周委素技备半办青省列习响约支般史感劳便团往酸历"
    "市克何除消构府称太准精值号率族维划选标写存候毛亲快效斯院查江型眼王"
    "按格养易置派层片始却专状育厂京识适属圆包火住调满县局照参红细引听该"
    "铁价严龙飞资产负债债务利润现金流营业收入净利润公司报告年度万元同比"
    "增减毛利率净资产收益率应收应付存货成本费用税金审计附注披露关联交易"
    "担保诉讼仲裁罚款亏损盈利偿债流动速动周转账款固定无形所有权益少数股"
    "东损益表现金流量表贷借款抵押质押租赁客户供应商采购销售研发专利商标"
    "许可合规处罚监管问询函"
)


def _normalize_charset_name(name: str) -> str:
    """
    统一编码名称的大小写和常见别名。

    参数：
        name: 响应头、meta 标签或 BOM 中读到的编码名。

    返回：
        str: 规范化后的 Python 编码名；空名称返回空字符串。

    说明：
        Python 能识别 gbk、gb2312，但 GB18030 是两者的超集，
        统一换成 gb18030 可以避免页面里个别生僻字导致解码失败。
    """
    cleaned = name.strip().strip("\"'").lower()
    if not cleaned:
        return ""
    return _CHARSET_ALIASES.get(cleaned, cleaned)


def _charsets_declared_in_html(raw_html: bytes) -> list[str]:
    """
    从网页开头的 <meta> 或 XML 声明里读取作者声明的编码。

    参数：
        raw_html: 网页原始字节。

    返回：
        list[str]: 按出现顺序排列的编码名，已去重；没有声明时返回空列表。

    说明：
        这里只扫描开头 8 KB，并用 latin-1 解码。latin-1 对任意字节
        都不会报错，适合在“还不知道编码”时安全地查看 ASCII 标记。
        识别两种常见写法：
            <meta charset="gb2312">
            <meta http-equiv="Content-Type"
                  content="text/html; charset=gbk">
            <?xml version="1.0" encoding="gbk"?>
    """
    head = raw_html[:_CHARSET_SCAN_BYTES].decode(
        "latin-1", errors="ignore"
    )
    found: list[str] = []

    patterns = (
        # 只匹配 <meta> 标签内部的 charset，避免把网页脚本里的
        # 普通字符串误当成编码声明。
        r"""<meta[^>]+charset\s*=\s*["']?\s*([a-zA-Z0-9._-]{1,40})""",
        # XML 声明里的 encoding="gbk"。
        r"""<\?xml[^>]*encoding\s*=\s*["']([a-zA-Z0-9._-]{1,40})""",
    )

    for pattern in patterns:
        for match in re.finditer(pattern, head, flags=re.IGNORECASE):
            charset = _normalize_charset_name(match.group(1))
            if charset and charset not in found:
                found.append(charset)

    return found


def _score_decoded_text(text: str) -> int:
    """
    给一种解码结果打分，用来判断哪种编码最可信。

    参数：
        text: 已经解码出来的网页文本。

    返回：
        int: 分数越高越可信。

    规则：
        - 常用汉字加分：正常中文页面会得到很高的正分；
        - 其他汉字给少量基础分：保证专业术语和生僻字不被误伤；
        - 替换符号（�）、拉丁扩展字母、希腊/西里尔字母等扣分：
          这些字符大量出现，通常说明“把 GBK 当成 UTF-8”之类的误读；
        - 纯英文页面汉字和乱码都很少，分数接近 0，
          此时由编码声明和候选顺序决定，不会被乱扣分。
    """
    sample = text[:_CHARSET_SCORE_SAMPLE_CHARS]
    cjk_count = 0
    common_count = 0
    suspicious_count = 0
    replacement_count = 0

    for char in sample:
        code = ord(char)
        if (
            0x4E00 <= code <= 0x9FFF
            or 0x3400 <= code <= 0x4DBF
            or 0x20000 <= code <= 0x2A6DF
        ):
            cjk_count += 1
            if char in _COMMON_CHINESE_CHARS:
                common_count += 1
        elif code == 0xFFFD:
            # Unicode 替换字符“�”，表示这个字节当前编码读不懂。
            replacement_count += 1
        elif (
            0x0080 <= code <= 0x024F
            or 0x0370 <= code <= 0x04FF
            or 0x0590 <= code <= 0x06FF
            or 0xE000 <= code <= 0xF8FF
        ):
            # 拉丁扩展字母、希腊/西里尔字母、希伯来/阿拉伯字母、
            # 私用区字符：这些是中文页面乱码时最常见的“替身”。
            suspicious_count += 1

    return (
        common_count * 3
        + (cjk_count - common_count)
        - suspicious_count * 2
        - replacement_count * 5
    )


def _decode_html(raw_html: bytes, charset: str | None) -> str:
    """
    把网页原始字节解码成文本。

    参数：
        raw_html: 最多 MAX_HTML_BYTES 的原始字节。
        charset: HTTP 响应头声明的编码；可能为空或不存在。

    返回：
        str: 解码后的 HTML 文本。

    说明：
        中文网站常见 UTF-8 和 GBK/GB18030 两种编码。实际网页经常出现：
        - 响应头不写编码；
        - 响应头写 ISO-8859-1，但正文其实是 GBK；
        - meta 写 gb2312，但正文用了 GBK 生僻字。
        因此这里不再“先到先得”，而是把所有可能编码都试一遍，
        按“哪种结果最像正常中文”打分选最优：
            1. 响应头声明的编码；
            2. 网页 <meta> / XML 声明的编码；
            3. UTF-8；
            4. GB18030（GBK/GB2312 的超集）。
        严格解码成功的结果优先；某编码严格解码失败时，
        再用“替换坏字节”的方式解一次作为兜底，但乱码会被扣分。
    """
    if not isinstance(raw_html, bytes) or not raw_html:
        return ""

    candidates: list[str] = []

    # 文件开头的 BOM 是最明确的编码标记，优先级最高。
    if raw_html.startswith(b"\xef\xbb\xbf"):
        candidates.append("utf-8-sig")
    elif raw_html.startswith((b"\xff\xfe", b"\xfe\xff")):
        candidates.append("utf-16")

    # 其次参考服务器响应头的声明。
    if isinstance(charset, str) and charset.strip():
        candidates.append(_normalize_charset_name(charset))

    # 再参考网页自己写的 <meta charset>。
    candidates.extend(_charsets_declared_in_html(raw_html))

    # 最后兜底尝试两种最常见的网页编码。
    candidates.extend(["utf-8", "gb18030"])

    # 去重并去掉空名称。set 会打乱顺序，所以用列表手工去重，
    # 保证“响应头 > meta > UTF-8 > GB18030”的优先顺序不变。
    unique_candidates: list[str] = []
    for encoding in candidates:
        if encoding and encoding not in unique_candidates:
            unique_candidates.append(encoding)

    best_text: str | None = None
    best_score = 0
    best_priority = len(unique_candidates)
    best_penalty = 1

    for priority, encoding in enumerate(unique_candidates):
        try:
            text = raw_html.decode(encoding, errors="strict")
            penalty = 0
        except LookupError:
            # Python 不认识这个编码名（例如网页写错了编码名），跳过。
            continue
        except UnicodeError:
            # 严格解码失败，说明“这个编码对不上”。
            # 仍用替换模式解一次作为兜底，但乱码会被显著扣分。
            text = raw_html.decode(encoding, errors="replace")
            penalty = 1

        score = _score_decoded_text(text)

        # 选择规则：
        # 1. 分数更高者优先；
        # 2. 分数相同时，严格解码优先于“替换坏字节”；
        # 3. 仍然相同时，按候选顺序（响应头 > meta > UTF-8 > GB18030）。
        if (
            best_text is None
            or score > best_score
            or (
                score == best_score
                and penalty < best_penalty
            )
            or (
                score == best_score
                and penalty == best_penalty
                and priority < best_priority
            )
        ):
            best_text = text
            best_score = score
            best_penalty = penalty
            best_priority = priority

    if best_text is not None:
        return best_text

    # 极端兜底：所有编码名 Python 都不认识时，仍保证返回字符串。
    return raw_html.decode("utf-8", errors="replace")


def _fetch_url_resource(url: str) -> _FetchedResource | None:
    """
    抓取一个网址的原始内容，并保留 Content-Type 供解析路由判断。

    参数：
        url: 网页或 PDF 链接。

    返回：
        _FetchedResource | None: 成功时返回原始字节和响应信息；
                                 地址不安全、网络失败、超时或状态码异常时返回 None。

    限制：
        - 只允许公网 HTTP(S) 和标准端口；
        - HTML 最多读取 MAX_HTML_BYTES；
        - PDF 或其他二进制内容最多读取 MAX_PDF_BYTES；
        - 超时时间 PAGE_FETCH_TIMEOUT_SECONDS；
        - 使用带安全重定向检查的 opener，防止跳到内网地址。
    """
    if not _is_public_http_url(url):
        return None

    request = Request(
        url,
        headers={
            "User-Agent": PAGE_USER_AGENT,
            "Accept": (
                "text/html,application/xhtml+xml,"
                "application/pdf;q=0.9,*/*;q=0.8"
            ),
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            # 明确不申请压缩，避免收到 gzip 后还要额外解压，
            # 也避免压缩内容被误判成乱码。
            "Accept-Encoding": "identity",
        },
    )

    # 使用带安全重定向检查的 opener，而不是直接 urlopen。
    opener = build_opener(_SafeRedirectHandler())

    try:
        with opener.open(
            request, timeout=PAGE_FETCH_TIMEOUT_SECONDS
        ) as response:
            status = getattr(response, "status", 200)
            if status < 200 or status >= 300:
                return None

            content_type = (
                response.headers.get("Content-Type", "") or ""
            ).lower()

            # 根据内容类型决定最多读多少字节：
            # HTML 通常较小；PDF 和未知二进制内容允许更大，但不能无限。
            if "html" in content_type or "xml" in content_type:
                read_limit = MAX_HTML_BYTES
            elif (
                "pdf" in content_type
                or _is_pdf_url(url)
                or not content_type
                or "octet-stream" in content_type
                or "binary" in content_type
            ):
                read_limit = MAX_PDF_BYTES
            else:
                # 图片、视频等即使读取也不会进入正文，按 HTML 上限读一点即可。
                read_limit = MAX_HTML_BYTES

            raw_bytes = response.read(read_limit + 1)
            truncated = len(raw_bytes) > read_limit
            if truncated:
                raw_bytes = raw_bytes[:read_limit]

            final_url = response.geturl() or url

            # 重定向处理器已经检查过每一跳，这里再检查最终地址，
            # 防止响应对象被中间层替换后绕过前面的校验。
            # 地址没有变化时不再重复做一次 DNS 解析，避免拖慢正常抓取。
            if final_url != url and not _is_public_http_url(final_url):
                return None

            charset = response.headers.get_content_charset()

            return _FetchedResource(
                url=url,
                final_url=final_url,
                content_type=content_type,
                raw_bytes=raw_bytes,
                charset=charset,
                truncated=truncated,
            )

    except (HTTPError, URLError, TimeoutError, OSError, ValueError):
        return None


def _is_pdf_resource(resource: _FetchedResource) -> bool:
    """
    判断抓取到的内容是否应按 PDF 解析。

    参数：
        resource: _fetch_url_resource() 的返回结果。

    返回：
        bool: Content-Type 是 application/pdf，
              或文件头是 %PDF- 时返回 True。

    说明：
        不能只看 URL 是否以 .pdf 结尾：有些网站会用 .pdf 链接
        跳转到登录页或 HTML 预览页，真正可靠的是响应类型和文件头。
    """
    if "application/pdf" in resource.content_type:
        return True

    header = resource.raw_bytes[:1024].lstrip()
    return header.startswith(b"%PDF-")


def _fetch_page_html(url: str) -> str:
    """
    抓取一个网页的 HTML 文本。

    参数：
        url: 网页链接。

    返回：
        str: 解码后的 HTML；地址不安全、网络失败、超时、
             不是 HTML 或页面为空时返回空字符串。

    限制：
        - 只允许公网 HTTP(S) 和标准端口；
        - 每次最多读取 MAX_HTML_BYTES；
        - 超时时间 PAGE_FETCH_TIMEOUT_SECONDS；
        - PDF 内容不会被当成 HTML 返回。
    """
    resource = _fetch_url_resource(url)
    if resource is None:
        return ""

    if _is_pdf_resource(resource):
        return ""

    # 空的 Content-Type 也尝试解析；但明确的图片、视频等直接跳过。
    if resource.content_type and (
        "html" not in resource.content_type
        and "xml" not in resource.content_type
    ):
        return ""

    return _decode_html(resource.raw_bytes, resource.charset)


def _extract_page_content(html: str, base_url: str) -> PageExtract:
    """
    从 HTML 中提取正文、新闻列表条目和 PDF 年报链接。

    参数：
        html: 已解码的 HTML 文本。
        base_url: 页面地址，用于把相对链接补成完整链接。

    返回：
        PageExtract: 正文、列表条目、PDF 链接和是否列表页的判断。

    说明：
        - 正文交给 utils/parser_router.py 的 lxml 路由提取，
          会删除脚本、导航、广告、页脚等噪声；
        - 如果 lxml 没有提取到内容，再用 BeautifulSoup 兜底；
        - 如果页面有 10 个以上同站链接，且链接文字较长，就判断为新闻列表；
        - 只记录“年报/年度报告”相关的 PDF 链接；
        - 本函数不下载 PDF，PDF 下载和解析由 _fetch_page_extract() 负责。
    """
    if not isinstance(html, str) or not html.strip():
        return PageExtract(url=base_url, error="页面内容为空。")

    soup = BeautifulSoup(html, "html.parser")

    # 删除不会出现在正文里的标签，减少导航和广告干扰。
    for tag in soup(
        [
            "script",
            "style",
            "noscript",
            "iframe",
            "svg",
            "form",
            "header",
            "footer",
            "nav",
            "aside",
        ]
    ):
        tag.decompose()

    base_domain = _domain_of(base_url)
    seen_links: set[str] = set()
    content_links: list[tuple[str, str]] = []
    pdf_links: list[dict] = []

    for anchor in soup.find_all("a", href=True):
        href = str(anchor.get("href", "")).strip()
        if not href or href.startswith(
            ("#", "javascript:", "mailto:", "tel:")
        ):
            continue

        absolute_url = urljoin(base_url, href)
        if not absolute_url.startswith(("http://", "https://")):
            continue

        link_text = _clean_external_text(
            anchor.get_text(" ", strip=True), 120
        )

        if _is_pdf_url(absolute_url):
            combined_text = f"{link_text} {absolute_url}"
            if (
                _is_annual_report_text(combined_text)
                and absolute_url not in seen_links
                and len(pdf_links) < MAX_PAGE_PDF_LINKS
            ):
                seen_links.add(absolute_url)
                pdf_links.append(
                    {
                        "title": link_text or "PDF 文件",
                        "url": absolute_url,
                    }
                )
            continue

        # 列表条目至少要有 8 个字的标题，过滤“首页”“更多”等短链接。
        if len(link_text) < 8:
            continue

        # 只保留同站链接，避免把友情链接当成新闻列表。
        link_domain = _domain_of(absolute_url)
        if base_domain and link_domain and not (
            link_domain == base_domain
            or link_domain.endswith("." + base_domain)
            or base_domain.endswith("." + link_domain)
        ):
            continue

        if absolute_url in seen_links:
            continue

        seen_links.add(absolute_url)
        content_links.append((link_text, absolute_url))

    is_list = len(content_links) >= 10
    list_items = (
        [
            {"title": title, "url": url}
            for title, url in content_links[:MAX_PAGE_LIST_ITEMS]
        ]
        if is_list
        else []
    )

    # 正文交给解析路由：lxml 会先删除导航、广告等噪声，
    # 再按正文密度选择最像文章的区域，并把页面表格转成 Markdown。
    body = ""
    try:
        main_content = extract_main_content(
            html,
            max_chars=MAX_PAGE_BODY_CHARS,
        )
        body = main_content.body
    except Exception:
        # 第三方库遇到极端 HTML 时可能抛出意外异常。
        # 正文解析失败不应该让整个网页作废，下面还有 BeautifulSoup 兜底。
        body = ""

    # 兜底方案：使用前面已经删除过脚本、导航的 BeautifulSoup 树。
    # 这只在 lxml 没有提取到正文时执行，正常页面不会重复解析。
    if not body:
        container = (
            soup.find("article")
            or soup.find("main")
            or soup.find(attrs={"role": "main"})
        )

        if container is None:
            for selector in (
                ".article-content",
                ".article_body",
                ".content",
                "#content",
                ".post-content",
            ):
                container = soup.select_one(selector)
                if container is not None:
                    break

        if container is None:
            container = soup.body or soup

        body = " ".join(container.get_text(" ", strip=True).split())
        if len(body) > MAX_PAGE_BODY_CHARS:
            body = body[: MAX_PAGE_BODY_CHARS - 1].rstrip() + "…"

    # 外部网页内容不可信，把尖括号换成全角字符，
    # 防止网页里伪造 <system>、<assistant> 之类的提示词标签。
    body = body.replace("<", "＜").replace(">", "＞")

    return PageExtract(
        url=base_url,
        is_list=is_list,
        body=body,
        list_items=list_items,
        pdf_links=pdf_links,
        parser="lxml",
    )


def _fetch_page_extract(url: str) -> PageExtract | None:
    """
    抓取并解析一个网页或 PDF，是解析路由的总入口。

    参数：
        url: 网页或 PDF 地址。

    返回：
        PageExtract | None: 成功时返回提取结果；
                            地址不安全、网络失败或内容类型不支持时返回 None。

    路由规则：
        1. 响应类型或文件头是 PDF：用 pdfplumber 提取文字和表格；
        2. 普通 HTML：用 lxml 提取正文；
        3. HTML 正文过短：用 Playwright 重新渲染后再提取；
        4. Playwright 未安装或渲染失败：保留 lxml 结果，并记录中文提示。

    说明：
        外部网页和 PDF 都属于不可信数据，任何解析异常都只影响当前地址，
        不能让整次搜索失败。
    """
    resource = _fetch_url_resource(url)
    if resource is None:
        return None

    page_url = resource.final_url or url

    # 第一条路由：PDF。
    if _is_pdf_resource(resource):
        if resource.truncated:
            return PageExtract(
                url=page_url,
                is_pdf=True,
                parser="pdfplumber",
                error=(
                    "PDF 文件超过 "
                    f"{MAX_PDF_BYTES // 1_000_000} MB，"
                    "为避免占用过多内存已跳过。"
                ),
            )

        try:
            pdf_result = parse_pdf_to_markdown(resource.raw_bytes)
        except PdfParseError as exc:
            return PageExtract(
                url=page_url,
                is_pdf=True,
                parser="pdfplumber",
                error=str(exc),
            )
        except Exception:
            return PageExtract(
                url=page_url,
                is_pdf=True,
                parser="pdfplumber",
                error="PDF 解析失败，已跳过这个文件。",
            )

        pdf_body = pdf_result.markdown.replace(
            "<", "＜"
        ).replace(">", "＞")

        return PageExtract(
            url=page_url,
            body=pdf_body,
            parser="pdfplumber",
            is_pdf=True,
            pdf_page_count=pdf_result.page_count,
            pdf_parsed_pages=pdf_result.parsed_pages,
            pdf_table_count=pdf_result.table_count,
            note=pdf_result.note,
        )

    # 非 HTML/XML 的内容（图片、视频、压缩包等）不进入正文解析。
    if resource.content_type and (
        "html" not in resource.content_type
        and "xml" not in resource.content_type
    ):
        return None

    html = _decode_html(resource.raw_bytes, resource.charset)
    if not html.strip():
        return None

    # 第二条路由：普通 HTML + lxml。
    try:
        extract = _extract_page_content(html, page_url)
    except Exception:
        return None

    # 第三条路由：正文过短，尝试 Playwright 动态渲染。
    if needs_browser_rendering(html, extract.body):
        try:
            rendered_html = render_dynamic_page(
                page_url,
                _is_public_http_url,
            )
        except BrowserRenderError as exc:
            # 浏览器内核没装、页面超时等都属于“可选能力不可用”，
            # 不影响已经拿到的 lxml 正文。
            extract.note = str(exc)
            return extract
        except Exception:
            extract.note = (
                "动态网页渲染失败，已保留普通网页解析结果。"
            )
            return extract

        if rendered_html:
            try:
                rendered_extract = _extract_page_content(
                    rendered_html, page_url
                )
            except Exception:
                rendered_extract = None

            if (
                rendered_extract is not None
                and len(rendered_extract.body)
                > len(extract.body)
            ):
                rendered_extract.parser = "playwright"
                rendered_extract.note = (
                    "普通解析正文过短，已用 Playwright 重新渲染。"
                )
                return rendered_extract

            extract.note = (
                "已尝试 Playwright 重新渲染，但正文没有明显增加，"
                "保留普通网页解析结果。"
            )
        else:
            extract.note = (
                "已尝试 Playwright 重新渲染，但没有取得内容，"
                "保留普通网页解析结果。"
            )

    if not extract.body and not extract.note:
        extract.error = "网页没有提取到可见正文。"

    return extract


def _looks_like_list_page(hit: dict) -> bool:
    """
    判断一个搜索结果是否更像新闻/资讯列表页。

    参数：
        hit: 搜索结果字典，包含 title、href。

    返回：
        bool: 标题或链接中出现新闻、资讯、列表、频道等关键词时返回 True。
    """
    text = f"{hit.get('title', '')} {hit.get('href', '')}".lower()
    return any(
        keyword.lower() in text for keyword in LIST_PAGE_KEYWORDS
    )


def _enrich_with_page_content(outcomes: list[QueryOutcome]) -> None:
    """
    抓取最多 2 个重点网页或 PDF，把正文、列表条目和 PDF 链接补充到结果中。

    参数：
        outcomes: 所有查询结果。函数会直接修改其中的 hit 字典。

    说明：
        - PDF 年报优先级最高，其次是新闻/资讯列表页，再次是普通网页；
        - 最多抓 MAX_BODY_PAGES 个，不递归抓列表里的每一篇文章；
        - 单个网页或 PDF 失败不影响其他结果，也不影响整个搜索；
        - 解析结果会记录实际使用的解析方式（lxml / playwright / pdfplumber）。
    """
    candidates: list[dict] = []
    seen_urls: set[str] = set()

    for outcome in outcomes:
        for hit in outcome.results:
            href = hit.get("href", "")
            if not href or href in seen_urls:
                continue
            seen_urls.add(href)
            candidates.append(hit)

    def _candidate_priority(hit: dict) -> tuple[int, int]:
        """
        计算抓取优先级。

        参数：
            hit: 单条搜索结果。

        返回：
            tuple[int, int]: 第一项是类型优先级，第二项是原有排序分数。

        说明：
            PDF 年报通常是尽调最有价值的材料，排在最前面；
            普通 PDF 可能只是公告附件，排在普通网页之后，
            避免把两个抓取名额都用在无关 PDF 上。
        """
        href = str(hit.get("href", ""))
        combined_text = (
            f"{hit.get('title', '')} "
            f"{hit.get('body', '')} "
            f"{href}"
        )

        # 年报相关的页面（无论链接是否以 .pdf 结尾）都可能包含核心财务数据，
        # 因此排在最前面；普通 PDF 附件排在普通网页之后。
        if _is_annual_report_text(combined_text):
            return (3, int(hit.get("score", 0)))

        if _is_pdf_url(href):
            return (0, int(hit.get("score", 0)))

        if _looks_like_list_page(hit):
            return (2, int(hit.get("score", 0)))

        return (1, int(hit.get("score", 0)))

    candidates.sort(key=_candidate_priority, reverse=True)

    for hit in candidates[:MAX_BODY_PAGES]:
        extract = _fetch_page_extract(hit["href"])
        if extract is None:
            continue

        hit["page_body"] = extract.body
        hit["page_is_list"] = extract.is_list
        hit["page_list_items"] = extract.list_items or []
        hit["page_pdf_links"] = extract.pdf_links or []
        hit["page_parser"] = extract.parser
        hit["page_is_pdf"] = extract.is_pdf
        hit["page_pdf_page_count"] = extract.pdf_page_count
        hit["page_pdf_parsed_pages"] = extract.pdf_parsed_pages
        hit["page_pdf_table_count"] = extract.pdf_table_count
        if extract.note:
            hit["page_note"] = extract.note
        if extract.error:
            hit["page_error"] = extract.error


def _collect_annual_pdf_links(
    outcomes: list[QueryOutcome],
) -> list[dict]:
    """
    汇总搜索结果和已抓网页中检测到的 PDF 年报链接。

    参数：
        outcomes: 所有查询结果。

    返回：
        list[dict]: 去重后的 PDF 链接，每项包含 title 和 url；
                    最多返回 MAX_PAGE_PDF_LINKS 条。

    说明：
        只收集确实是 PDF、且标题或链接文字包含“年报/年度报告”的链接。
        是否已经自动解析不影响这里收录链接：
        链接列表仍然保留，方便用户核对来源或手动下载。
    """
    collected: list[dict] = []
    seen_urls: set[str] = set()

    def add_link(title: object, url: object) -> None:
        """内部小函数：去重并限制数量。"""
        if not isinstance(url, str) or not url.strip():
            return
        if not _is_pdf_url(url):
            return
        # 黑名单站点的 PDF 同样不采用，保持整站排除规则一致。
        if _is_excluded_domain(_domain_of(url)):
            return
        if url in seen_urls:
            return
        if len(collected) >= MAX_PAGE_PDF_LINKS:
            return

        seen_urls.add(url)
        collected.append(
            {
                "title": _clean_external_text(title, 160) or "PDF 文件",
                "url": url,
            }
        )

    for outcome in outcomes:
        for hit in outcome.results:
            href = hit.get("href", "")
            combined_text = f"{hit.get('title', '')} {hit.get('body', '')}"
            if _is_pdf_url(href) and _is_annual_report_text(combined_text):
                add_link(hit.get("title"), href)

            for pdf_link in hit.get("page_pdf_links", []) or []:
                add_link(
                    pdf_link.get("title"),
                    pdf_link.get("url"),
                )

    return collected


def _domain_of(url: object) -> str:
    """
    从链接中提取域名，用于权威度评分和同站数量限制。

    参数：
        url: 网页链接。

    返回：
        str: 去掉 www. 的小写域名；无法解析时返回空字符串。
    """
    if not isinstance(url, str) or not url.strip():
        return ""

    try:
        host = urlparse(url.strip()).hostname or ""
    except ValueError:
        return ""

    host = host.lower().strip(".")
    if host.startswith("www."):
        host = host[4:]
    return host


def _normalize_url(url: object) -> str:
    """
    归一化链接，用于跨查询去重。

    参数：
        url: 网页链接。

    返回：
        str: 去掉常见跟踪参数和锚点后的链接；无效链接返回空字符串。
    """
    if not isinstance(url, str) or not url.strip():
        return ""

    try:
        parsed = urlparse(url.strip())
    except ValueError:
        return ""

    if parsed.scheme not in ("http", "https"):
        return ""

    host = parsed.netloc.lower().strip(".")
    if host.startswith("www."):
        host = host[4:]
    if not host:
        return ""

    kept_params: list[tuple[str, str]] = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        key_lower = key.lower()
        if key_lower.startswith("utm_"):
            continue
        if key_lower in {"spm", "from", "source", "share_token"}:
            continue
        kept_params.append((key, value))

    path = parsed.path.rstrip("/") or "/"
    return urlunparse(
        (
            parsed.scheme,
            host,
            path,
            parsed.params,
            urlencode(kept_params),
            "",
        )
    )


def _domain_matches(host: str, domain: str) -> bool:
    """
    判断域名是否等于目标域名，或属于其子域名。

    参数：
        host: 待检查的域名。
        domain: 目标根域名，例如 gov.cn。

    返回：
        bool: host 等于 domain 或以 ".domain" 结尾时返回 True。
    """
    if not host or not domain:
        return False
    return host == domain or host.endswith("." + domain)


def _is_excluded_domain(host: str) -> bool:
    """
    判断域名是否属于要排除的黑名单或文档分享、文库类网站。

    参数：
        host: 域名。

    返回：
        bool: 命中排除名单返回 True。

    说明：
        黑名单包括雪球、东方财富、知乎、百度；这些站点以用户讨论、
        股吧或问答为主，不适合作为尽调依据。
        文档分享站包括道客巴巴、人人文档、book118、百度文库等。
    """
    return any(
        _domain_matches(host, domain) for domain in EXCLUDED_DOMAINS
    )


def _authority_score(host: str) -> int:
    """
    根据域名计算权威度分数。

    参数：
        host: 域名。

    返回：
        int: 权威度分数；未知网站返回 0。
    """
    best_score = 0
    for domain, score in AUTHORITY_DOMAIN_SCORES.items():
        if _domain_matches(host, domain):
            best_score = max(best_score, score)
    return best_score


def _recency_score(published: object) -> int:
    """
    根据发布时间计算新鲜度分数。

    参数：
        published: ISO 格式的发布时间字符串，可能为空。

    返回：
        int: 越新分数越高；无法解析时给一个中性小分。
    """
    if not isinstance(published, str) or not published.strip():
        return 5

    try:
        parsed = datetime.fromisoformat(
            published.strip().replace("Z", "+00:00")
        )
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        age_days = (
            datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)
        ).days
    except (ValueError, TypeError):
        return 5

    if age_days < 0:
        # 未来日期通常是抓取时间误差，给一个中等偏上的分数，避免异常高。
        return 20
    if age_days <= 365:
        return 30
    if age_days <= 730:
        return 20
    if age_days <= 1095:
        return 10
    return 0


def _contains_any(text: str, keywords: tuple[str, ...]) -> bool:
    """
    判断文本是否包含任意关键词。

    参数：
        text: 待检查文本。
        keywords: 关键词元组。

    返回：
        bool: 命中任意关键词返回 True。
    """
    return any(keyword in text for keyword in keywords)


def _score_hit(
    title: str,
    body: str,
    published: str,
    domain: str,
    purpose: str,
    href: str = "",
    company_pinyin_aliases: tuple[str, ...] = (),
) -> int:
    """
    给单条搜索结果打分，供本地排序使用。

    参数：
        title: 网页标题。
        body: 网页摘要。
        published: 发布时间。
        domain: 网页域名。
        purpose: 当前查询方向，例如 cninfo / official / finance / risk。
        href: 网页完整链接；用于识别官网域名和投资者关系页。
        company_pinyin_aliases: 公司名的拼音别名；命中官网域名时加最高权重。

    返回：
        int: 综合分数，越高越优先。

    说明：
        分数 = 权威度 + 新鲜度 + 查询方向匹配加分
              + 官网域名加分 + 投资者关系/年报链接加分。
        官网加分（300 分）远高于其他项，确保 huawei.com 这类企业官网
        始终排在结果最前面；investor、annual report 等链接加 180 分，
        让投资者关系和年报页面紧随其后。
    """
    text = f"{title} {body}"
    score = _authority_score(domain) + _recency_score(published)

    if purpose == "cninfo" and _domain_matches(domain, "cninfo.com.cn"):
        score += 20
    if purpose == "official" and _contains_any(text, OFFICIAL_KEYWORDS):
        score += 10
    if purpose == "finance" and _contains_any(text, FINANCE_KEYWORDS):
        score += 8
    if purpose == "risk" and _contains_any(text, RISK_KEYWORDS):
        score += 10
    if purpose == "business" and _contains_any(text, BUSINESS_KEYWORDS):
        score += 8
    if _contains_any(text, ("年报", "年度报告", "财报", "财务报告")):
        score += 5

    # 官网和投资者关系页面权重最高。
    # 先检查公司名拼音，再检查 investor / annual report 等英文关键词。
    score += _official_site_bonus(
        href=href,
        title=title,
        company_pinyin_aliases=company_pinyin_aliases,
    )

    return score


def _rank_and_select(
    raw_results: list[dict],
    purpose: str,
    limit: int = RESULTS_PER_QUERY,
    company_aliases: tuple[str, ...] | None = None,
    strict_relevance: bool = False,
    company_pinyin_aliases: tuple[str, ...] = (),
) -> list[dict]:
    """
    清理、过滤、排序并截取单条查询的结果。

    参数：
        raw_results: _extract_bocha_results() 返回的原始结果列表。
        purpose: 查询方向，用于方向匹配加分。
        limit: 最多保留几条。
        company_aliases: 公司别名列表；传入后会优先保留真正提到该公司的结果。
        strict_relevance: 为 True 时，如果没有任何结果提到该公司，
                          就返回空列表，而不是保留别家公司的资料。
                          巨潮查询使用严格模式，避免把别家年报当成目标公司。
        company_pinyin_aliases: 公司名拼音别名；用于识别企业官网域名
                                并给它最高的排序权重。

    返回：
        list[dict]: 排好序的结果，每项新增 domain 和 score 字段。

    处理顺序：
        1. 清理标题、链接、摘要，跳过空结果；
        2. 过滤黑名单和文档分享站；
        3. 按归一化链接去重；
        4. 如果传入了公司别名，优先保留标题或摘要提到该公司的结果；
        5. 按权威度 + 新鲜度 + 官网加分降序排序；
        6. 同一网站最多保留 2 条，再截取前 limit 条。
    """
    candidates: list[dict] = []
    seen_keys: set[str] = set()

    for raw_result in raw_results:
        if not isinstance(raw_result, dict):
            continue

        title = _clean_external_text(
            raw_result.get("title"), MAX_TITLE_CHARS
        )
        href = _clean_external_text(
            raw_result.get("href"), MAX_LINK_CHARS
        )
        body = _clean_external_text(
            raw_result.get("body"), MAX_SNIPPET_CHARS
        )
        published = _clean_external_text(
            raw_result.get("published"), 60
        )

        if not title and not body:
            continue

        domain = _domain_of(href)
        if _is_excluded_domain(domain):
            continue

        # 同一链接只保留一次。链接无效时用标题做去重键，
        # 避免同一个网页因为没有链接而重复出现。
        dedup_key = _normalize_url(href)
        if not dedup_key:
            dedup_key = "title:" + title.strip().lower()
        if not dedup_key or dedup_key in seen_keys:
            continue
        seen_keys.add(dedup_key)

        candidates.append(
            {
                "title": title,
                "href": href,
                "body": body,
                "published": published,
                "domain": domain,
                "score": _score_hit(
                    title=title,
                    body=body,
                    published=published,
                    domain=domain,
                    purpose=purpose,
                    href=href,
                    company_pinyin_aliases=company_pinyin_aliases,
                ),
            }
        )

    # 公司相关性过滤：
    # 只要有一部分结果明确提到目标公司，就丢掉完全没提到的结果；
    # 如果一条都没提到，普通查询保留原结果（可能是摘要没写全名），
    # 巨潮查询则直接返回空，避免把别家公司的年报当成目标公司的资料。
    if company_aliases:
        relevant_candidates = [
            candidate
            for candidate in candidates
            if _matches_company(
                f"{candidate['title']} {candidate['body']}",
                company_aliases,
            )
        ]
        if relevant_candidates:
            candidates = relevant_candidates
        elif strict_relevance:
            candidates = []

    # 先按分数排序；分数相同时，发布时间较新的排前面。
    candidates.sort(
        key=lambda item: (item["score"], item["published"]),
        reverse=True,
    )

    selected: list[dict] = []
    domain_counts: dict[str, int] = {}

    for item in candidates:
        domain = item["domain"]
        if domain_counts.get(domain, 0) >= MAX_SOURCES_PER_DOMAIN:
            continue
        selected.append(item)
        domain_counts[domain] = domain_counts.get(domain, 0) + 1
        if len(selected) >= limit:
            break

    return selected


def _remove_pdf_operator(query: str) -> str:
    """
    去掉搜索词中的 filetype:pdf 限定。

    参数：
        query: 原始搜索词。

    返回：
        str: 去掉 PDF 限定并压缩空白后的搜索词。
    """
    return " ".join(_PDF_OPERATOR_PATTERN.sub(" ", query).split())


def _infer_purpose(query: str) -> str:
    """
    根据搜索词内容推断它属于哪个方向。

    参数：
        query: 第二轮补搜词。

    返回：
        str: risk / business / finance / gap 之一。
    """
    if _contains_any(query, RISK_KEYWORDS):
        return "risk"
    if _contains_any(query, BUSINESS_KEYWORDS):
        return "business"
    if _contains_any(query, FINANCE_KEYWORDS):
        return "finance"
    return "gap"


def _purpose_label(purpose: str) -> str:
    """
    把内部方向名称翻译成中文，供进度提示使用。

    参数：
        purpose: 内部方向名称。

    返回：
        str: 中文标签。
    """
    labels = {
        "cninfo": "巨潮资讯网",
        "official": "官网与投资者关系",
        "finance": "财务数据",
        "risk": "法务与经营风险",
        "business": "经营与行业",
        "gap": "补漏信息",
    }
    return labels.get(purpose, "补充信息")


def _run_query(
    round_label: str,
    purpose: str,
    query: str,
    include: str,
    allow_pdf_fallback: bool,
    api_key: str,
    company_aliases: tuple[str, ...] | None = None,
    company_pinyin_aliases: tuple[str, ...] = (),
) -> QueryOutcome:
    """
    执行单条查询，并处理 PDF 限定和站点筛选的回退。

    参数：
        round_label: “第一轮”或“第二轮”。
        purpose: 查询方向。
        query: 检索语句。
        include: 限定站点；空字符串表示不限。
        allow_pdf_fallback: 是否允许在 PDF 限定无结果时去掉限定重搜。
        api_key: 博查密钥。
        company_aliases: 公司别名；用于优先保留真正提到该公司的结果。
        company_pinyin_aliases: 公司名拼音别名；用于把企业官网排到最前。

    返回：
        QueryOutcome: 包含最终搜索词、结果或错误的结构化对象。

    回退规则：
        - 接口拒绝站点筛选或 PDF 语法时，去掉这些限定重搜一次；
        - 带 filetype:pdf 的查询没有结果时，去掉该限定重搜一次；
        - 其他错误（密钥、余额、限流、网络）不重试，避免浪费调用次数。
    """
    outcome = QueryOutcome(
        round_label=round_label,
        purpose=purpose,
        query=query,
        include=include,
        results=[],
    )

    has_pdf_operator = bool(_PDF_OPERATOR_PATTERN.search(query))

    try:
        raw_results = _request_bocha(
            query=query,
            api_key=api_key,
            count=CANDIDATES_PER_QUERY,
            include=include,
            exclude=EXCLUDE_PARAM,
        )
    except BochaSearchError as exc:
        # 博查明确拒绝请求时，可能是 exclude 或 filetype:pdf 不被接受。
        # 去掉这两个限定重搜一次；其他错误不重试。
        if exc.category == "bad_request" and (
            EXCLUDE_PARAM or (allow_pdf_fallback and has_pdf_operator)
        ):
            relaxed_query = (
                _remove_pdf_operator(query)
                if allow_pdf_fallback
                else query
            )
            try:
                raw_results = _request_bocha(
                    query=relaxed_query,
                    api_key=api_key,
                    count=CANDIDATES_PER_QUERY,
                    include=include,
                    exclude="",
                )
            except BochaSearchError as retry_exc:
                outcome.error = retry_exc
                return outcome

            outcome.query = relaxed_query
            outcome.relaxed_fallback = True
            has_pdf_operator = bool(
                _PDF_OPERATOR_PATTERN.search(relaxed_query)
            )
        else:
            outcome.error = exc
            return outcome

    selected_results = _rank_and_select(
        raw_results,
        purpose,
        company_aliases=company_aliases,
        strict_relevance=(purpose == "cninfo"),
        company_pinyin_aliases=company_pinyin_aliases,
    )

    # 带 filetype:pdf 的查询没有结果时，自动去掉限定再搜一次。
    # 这样即使博查不支持 filetype 语法，也不会让整条官方资料查询作废。
    if allow_pdf_fallback and has_pdf_operator and not selected_results:
        fallback_query = _remove_pdf_operator(query)

        try:
            fallback_results = _request_bocha(
                query=fallback_query,
                api_key=api_key,
                count=CANDIDATES_PER_QUERY,
                include=include,
                exclude=EXCLUDE_PARAM,
            )
        except BochaSearchError as exc:
            outcome.query = fallback_query
            outcome.pdf_fallback = True
            outcome.error = exc
            return outcome

        outcome.query = fallback_query
        outcome.pdf_fallback = True
        outcome.raw_count = len(fallback_results)
        outcome.results = _rank_and_select(
            fallback_results,
            purpose,
            company_aliases=company_aliases,
            strict_relevance=(purpose == "cninfo"),
            company_pinyin_aliases=company_pinyin_aliases,
        )
        return outcome

    outcome.raw_count = len(raw_results)
    outcome.results = selected_results
    return outcome


def _notify(
    progress_callback: Callable[[str], None] | None,
    message: str,
) -> None:
    """
    向页面报告当前搜索阶段。

    参数：
        progress_callback: 页面传入的回调函数；可以为 None。
        message: 要展示的中文进度说明。

    说明：
        进度回调只是锦上添花，即使它出错也不能影响搜索本身，
        因此这里吞掉回调异常。
    """
    if not callable(progress_callback):
        return

    try:
        progress_callback(message)
    except Exception:
        pass


def _format_error_reasons(
    errors: list[BochaSearchError | None],
) -> str:
    """
    合并多次查询的错误原因，去重后用中文分号连接。

    参数：
        errors: 可能包含 None 的异常列表。

    返回：
        str: 去重后的中文错误说明；没有有效错误时返回空字符串。
    """
    unique_messages: list[str] = []

    for error in errors:
        if error is None:
            continue
        message = error.user_message.strip()
        if message and message not in unique_messages:
            unique_messages.append(message)

    return "；".join(unique_messages)


def _format_no_results_text(
    company_name: str,
    outcomes: list[QueryOutcome],
) -> str:
    """
    构造“全部失败”或“全部没有结果”的提示文本。

    参数：
        company_name: 公司名称。
        outcomes: 本次所有查询结果。

    返回：
        str: 以【搜索失败】或【未检索到结果】开头的友好提示。
    """
    failed_outcomes = [
        outcome for outcome in outcomes if outcome.error is not None
    ]

    if failed_outcomes and len(failed_outcomes) == len(outcomes):
        error_reasons = _format_error_reasons(
            [outcome.error for outcome in failed_outcomes]
        )
        failed_labels = "；".join(
            outcome.query for outcome in failed_outcomes
        )
        return (
            f"{SEARCH_FAILURE_PREFIX}{error_reasons}\n\n"
            f"本次失败的检索：{failed_labels}\n\n"
            "搜索失败不代表该公司没有风险，只是本次未能获取公开信息。"
            "也可以改用上传 PDF / TXT 资料的方式分析。"
        )

    empty_labels = "；".join(
        outcome.query
        for outcome in outcomes
        if outcome.error is not None or not outcome.results
    )
    lines = [
        f"{SEARCH_EMPTY_PREFIX}未能获取“{company_name}”的公开网页摘要。",
        "",
        "可能的原因：",
        "1. 公司名称不是常用全称，或该公司公开信息较少；",
        "2. 搜索服务暂时限流或服务异常；",
        "3. 当前网络对部分搜索请求不稳定。",
        "",
        f"本次未返回结果的检索：{empty_labels or '（无）'}",
    ]

    error_reasons = _format_error_reasons(
        [outcome.error for outcome in outcomes]
    )
    if error_reasons:
        lines.extend(["", f"部分检索失败原因：{error_reasons}"])

    lines.extend(
        [
            "",
            "说明：未检索到公开信息不代表该公司没有风险。"
            "建议检查公司全称或网络后重试，"
            "或改用上传 PDF / TXT 资料的方式分析。",
        ]
    )

    return "\n".join(lines)


def _deduplicate_outcomes(
    outcomes: list[QueryOutcome],
) -> None:
    """
    在所有查询之间做一次全局链接去重。

    参数：
        outcomes: 所有查询结果。函数会直接修改每个结果的 results 字段。

    说明：
        同一条查询内部已经在 _rank_and_select() 中去重过；
        但第一轮和第二轮、或两条不同查询之间，仍可能搜到同一个网页。
        这里按“先出现者优先”保留，并记录每条查询被合并掉的数量，
        便于在结果中用一句说明解释“为什么这条查询看起来变少了”。
    """
    seen_keys: set[str] = set()

    for outcome in outcomes:
        unique_results: list[dict] = []
        removed_count = 0

        for hit in outcome.results:
            key = _normalize_url(hit.get("href"))
            if not key:
                key = "title:" + str(hit.get("title", "")).strip().lower()

            if key in seen_keys:
                removed_count += 1
                continue

            seen_keys.add(key)
            unique_results.append(hit)

        outcome.results = unique_results
        outcome.duplicates_removed = removed_count


def _format_header(
    company_name: str,
    outcomes: list[QueryOutcome],
    plan: SecondRoundPlan,
    annual_pdf_links: list[dict] | None = None,
) -> str:
    """
    构造搜索结果开头的说明部分。

    参数：
        company_name: 公司名称。
        outcomes: 所有查询结果。
        plan: 第二轮补漏计划，用于说明搜索词来源。
        annual_pdf_links: 检测到的 PDF 年报链接；为空表示没有检测到。

    返回：
        str: 带检索方式、筛选规则、免责声明和部分失败提示的 Markdown 文本。
    """
    total_sources = sum(len(outcome.results) for outcome in outcomes)
    search_time = datetime.now().strftime("%Y-%m-%d %H:%M")

    lines = [
        "# 公开网页信息摘要（未经核实）",
        "",
        f"- 查询公司：{company_name}",
        f"- 检索时间：{search_time}（本机时间）",
        "- 检索方式：两轮深度搜索（第一轮固定专业模板 + 第二轮大模型补漏）",
        "- 检索来源：博查（Bocha）Web Search API 网页搜索结果摘要",
        f"- 本次取得来源：{total_sources} 条（已去重）",
        (
            f"- 候选筛选：每条查询最多取 {CANDIDATES_PER_QUERY} 条候选，"
            "按权威度和新鲜度排序后保留前 "
            f"{RESULTS_PER_QUERY} 条；同一链接在整份结果中只保留一次，"
            f"每条查询中同一网站最多 {MAX_SOURCES_PER_DOMAIN} 条。"
        ),
        (
            "- 已排除：雪球、东方财富、知乎、百度等黑名单站点，"
            "以及道客巴巴、人人文档、book118、百度文库等文档分享站。"
        ),
        (
            "- 排序规则：企业官网（域名含公司名拼音）权重最高，"
            "投资者关系、年报页面次之；其余按权威度和新鲜度排序。"
        ),
        (
            "- 正文抓取：最多读取 2 个重点网页或 PDF（PDF 年报优先）；"
            "HTML 用 lxml 提取正文，正文过短的动态页用 Playwright 重新渲染，"
            "PDF 用 pdfplumber 提取文字和表格并转成 Markdown。"
        ),
        f"- 第二轮补漏：{plan.note}",
        "",
        "> 以下内容来自公开网页，可能过时、残缺、由媒体或第三方转述，"
        "也可能混入同名公司的信息。它未经企业提供资料核实，"
        "只能用于初步风险筛查，不能替代正式财务、法务和业务尽职调查。",
        "",
    ]

    # 巨潮查询是“尽量争取”的可选来源：
    # 博查的站内检索对巨潮覆盖有限，空结果不代表搜索失败，也不代表不是 A 股。
    # 因此它单独用一段说明，不计入“部分查询失败”。
    cninfo_outcome = next(
        (
            outcome
            for outcome in outcomes
            if outcome.purpose == "cninfo"
        ),
        None,
    )
    cninfo_optional_empty = (
        cninfo_outcome is not None
        and cninfo_outcome.error is None
        and not cninfo_outcome.results
    )

    # 只要有任何一条查询失败或没有结果，就在开头明确标注部分失败。
    failed_outcomes = [
        outcome
        for outcome in outcomes
        if (
            outcome.error is not None
            or (not outcome.results and outcome.duplicates_removed == 0)
        )
        and not (
            outcome is cninfo_outcome and cninfo_optional_empty
        )
    ]
    if failed_outcomes:
        lines.extend(
            [
                f"> {SEARCH_PARTIAL_PREFIX}以下检索未返回可用结果：",
                "> " + "；".join(
                    outcome.query for outcome in failed_outcomes
                ),
                "> 缺少结果不代表对应方面没有风险，"
                "只表示本次未检索到公开信息。",
            ]
        )
        error_reasons = _format_error_reasons(
            [outcome.error for outcome in failed_outcomes]
        )
        if error_reasons:
            lines.append(f"> 失败原因：{error_reasons}")
        lines.append("")

    # 巨潮查询为空时单独说明，避免用户误以为整个搜索失败。
    if cninfo_optional_empty:
        lines.extend(
            [
                "> 巨潮资讯网未检索到与该公司直接相关的披露。"
                "博查的巨潮站内检索覆盖有限，这不代表公司没有披露；"
                "已用官网、财经媒体等结果补充，也可能不是 A 股公司。",
                "",
            ]
        )

    # 检测到 PDF 年报时给出明确的操作提示：
    # 本系统不会自动下载 PDF，需要用户自行下载后切换到 Tab 1 上传分析。
    if annual_pdf_links:
        lines.extend(
            [
                f"> {PDF_ANNUAL_REPORT_HINT}",
                "> 本次检测到的 PDF 年报链接：",
            ]
        )
        for pdf_link in annual_pdf_links:
            lines.append(
                f"> - [{pdf_link['title']}]({pdf_link['url']})"
            )
        lines.append("")

    return "\n".join(lines)


def _normalize_publish_date(value: object) -> str:
    """
    把搜索接口返回的发布时间整理成“年-月-日”。

    参数：
        value: 博查返回的 datePublished，可能是
               "2025-08-23"、"2025-08-23T10:00:00+08:00"、
               "2025年8月23日" 等格式，也可能为空。

    返回：
        str: 能识别时返回 "YYYY-MM-DD"；无法识别但原文非空时返回原文
             （最多 40 个字符）；完全没有日期时返回空字符串。

    说明：
        引用格式中的日期需要简短、统一，否则大模型可能把一整段
        ISO 时间戳照抄进链接文字，导致报告显得杂乱。
        这里只做“尽量识别”，识别不了就保留原文，不猜日期。
    """
    if not isinstance(value, str) or not value.strip():
        return ""

    text = value.strip()

    # 优先处理标准 ISO 格式，例如 2025-08-23T10:00:00+08:00。
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed.strftime("%Y-%m-%d")
    except ValueError:
        pass

    # 兼容中文日期和斜杠日期，例如 2025年8月23日、2025/08/23。
    match = re.search(
        r"(\d{4})\s*[-/.年]\s*(\d{1,2})\s*[-/.月]\s*(\d{1,2})",
        text,
    )
    if match:
        year, month, day = (int(part) for part in match.groups())
        try:
            return datetime(year, month, day).strftime("%Y-%m-%d")
        except ValueError:
            # 例如 2025-02-30 这种不存在的日期，退回原文，
            # 不把它强行改成另一个日期。
            pass

    return text[:40]


def _escape_markdown_link_text(value: str) -> str:
    """
    转义 Markdown 链接文字中的方括号和反斜杠。

    参数：
        value: 准备放进 [链接文字](URL) 的文字。

    返回：
        str: 不会破坏 Markdown 链接语法的文字。

    说明：
        网页标题里偶尔会出现 [ ] 或反斜杠，如果直接拼进 Markdown，
        链接会被截断或显示错乱。这里只做最小转义，不改变标题含义。
    """
    return (
        value.replace("\\", "\\\\")
        .replace("[", "\\[")
        .replace("]", "\\]")
    )


def _format_source_citation(
    title: object,
    published: object,
    href: object,
) -> str:
    """
    为一条搜索结果生成可直接复制的 Markdown 引用。

    参数：
        title: 网页标题。
        published: 搜索接口返回的发布日期。
        href: 网页 URL。

    返回：
        str: 形如
             “[数据来源：巨潮资讯公告 | 2025-08-23](https://...)”
             的 Markdown 链接；缺少 URL 时返回纯文字提示，
             不会编造链接。

    说明：
        这个函数供搜索结果的“引用格式”一行使用。
        大模型可以直接照抄整行，减少自己拼接标题、日期和链接时
        漏字段、写错 URL 的概率。
    """
    clean_title = _clean_external_text(title, MAX_TITLE_CHARS)
    clean_published = _clean_external_text(published, 60)
    clean_href = _clean_external_text(href, MAX_LINK_CHARS)

    date_text = _normalize_publish_date(clean_published)
    safe_title = _escape_markdown_link_text(
        clean_title or "未命名网页"
    )
    safe_date = _escape_markdown_link_text(
        date_text or "发布日期未披露"
    )
    link_text = f"数据来源：{safe_title} | {safe_date}"

    # 只有明确是 http/https 的链接才做成可点击链接。
    # 防止把 javascript:、file: 等危险协议带进报告或前端。
    try:
        scheme = urlparse(clean_href).scheme.lower()
    except ValueError:
        scheme = ""

    if scheme not in ("http", "https"):
        return f"{link_text}（网页未提供可点击链接）"

    # 空格和圆括号会破坏 Markdown 的 (...)
    # 链接语法，这里做百分号编码；编码后仍然是同一个网址。
    safe_url = (
        clean_href.replace(" ", "%20")
        .replace("(", "%28")
        .replace(")", "%29")
    )

    return f"[{link_text}]({safe_url})"


def _format_query_section(
    outcome: QueryOutcome,
    start_index: int,
) -> tuple[str, int]:
    """
    把一条查询的结果整理成 Markdown 片段。

    参数：
        outcome: 单条查询结果。
        start_index: 下一条来源的编号。

    返回：
        tuple[str, int]: 整理后的文本，以及下一条来源应使用的编号。
    """
    lines = [f"#### 检索语句：{outcome.query}"]

    if outcome.include:
        lines.append(f"- 限定站点：{outcome.include}")

    if outcome.relaxed_fallback:
        lines.append(
            "- 说明：站点排除或 filetype:pdf 限定未被接口接受，"
            "已自动放宽后重搜。"
        )
    elif outcome.pdf_fallback:
        lines.append(
            "- 说明：带 filetype:pdf 的查询未返回结果，"
            "已自动去掉该限定重搜。"
        )

    if outcome.error is not None:
        lines.append(f"> 该查询失败：{outcome.error.user_message}")
        return "\n".join(lines), start_index

    if not outcome.results:
        if outcome.duplicates_removed:
            lines.append(
                f"> 该查询另有 {outcome.duplicates_removed} 条来源"
                "与前面的结果重复，已合并。"
            )
        elif outcome.purpose == "cninfo":
            lines.append(
                "> 该查询未返回与该公司直接相关的结果"
                "（博查的巨潮站内检索覆盖有限）。"
            )
        else:
            lines.append("> 该查询未返回可用结果。")
        return "\n".join(lines), start_index

    if outcome.duplicates_removed:
        lines.append(
            f"- 说明：另有 {outcome.duplicates_removed} 条来源"
            "与前面的结果重复，已合并。"
        )

    current_index = start_index

    for hit in outcome.results:
        lines.append("")
        lines.append(f"### 来源 {current_index}")
        lines.append(
            f"- 标题：{hit['title'] or '（网页未提供标题）'}"
        )
        # 发布日期即使缺失也要明确写出来，方便大模型在引用中如实标注
        # “发布日期未披露”，而不是自己猜一个日期。
        lines.append(
            "- 发布日期："
            f"{_normalize_publish_date(hit.get('published')) or '未披露'}"
        )
        lines.append(
            f"- 链接：{hit['href'] or '（网页未提供链接）'}"
        )
        if hit.get("domain"):
            lines.append(f"- 站点：{hit['domain']}")
        # 这一行是给大模型直接照抄的引用模板，
        # 格式固定为 [数据来源：标题 | 日期](URL)。
        lines.append(
            "- 引用格式："
            + _format_source_citation(
                hit.get("title"),
                hit.get("published"),
                hit.get("href"),
            )
        )
        lines.append(
            f"- 摘要：{hit['body'] or '（网页未提供摘要）'}"
        )

        # 以下字段只在抓取或解析过网页/PDF 后才会出现。
        parser_labels = {
            "lxml": "普通 HTML 解析（lxml）",
            "playwright": "动态网页渲染（Playwright）",
            "pdfplumber": "PDF 自动解析（pdfplumber）",
        }
        if hit.get("page_parser"):
            lines.append(
                "- 解析方式："
                + parser_labels.get(
                    hit["page_parser"],
                    str(hit["page_parser"]),
                )
            )

        if hit.get("page_is_pdf") and hit.get("page_pdf_page_count"):
            lines.append(
                f"- PDF 页数：{hit.get('page_pdf_page_count', 0)}；"
                f"本次解析：{hit.get('page_pdf_parsed_pages', 0)} 页；"
                f"提取表格：{hit.get('page_pdf_table_count', 0)} 个"
            )

        if hit.get("page_body"):
            if hit.get("page_is_pdf"):
                lines.append("- PDF 解析摘录（Markdown）：")
            else:
                page_type = (
                    "新闻/资讯列表页"
                    if hit.get("page_is_list")
                    else "普通网页"
                )
                lines.append(f"- 网页类型：{page_type}")
                lines.append("- 正文摘录：")
            lines.append(hit["page_body"])

        if hit.get("page_note"):
            lines.append(f"- 解析提示：{hit['page_note']}")

        if hit.get("page_error"):
            lines.append(f"- 解析未完成：{hit['page_error']}")

        if hit.get("page_list_items"):
            lines.append("- 列表中的文章：")
            for list_item in hit["page_list_items"]:
                lines.append(
                    f"  - [{list_item['title']}]({list_item['url']})"
                )

        if hit.get("page_pdf_links"):
            lines.append("- 该页面内检测到的 PDF 年报：")
            for pdf_link in hit["page_pdf_links"]:
                lines.append(
                    f"  - [{pdf_link['title']}]({pdf_link['url']})"
                )

        current_index += 1

    return "\n".join(lines), current_index


def _format_sections(outcomes: list[QueryOutcome]) -> str:
    """
    按第一轮、第二轮整理所有查询片段。

    参数：
        outcomes: 所有查询结果。

    返回：
        str: 带轮次标题、连续来源编号的 Markdown 文本。
    """
    first_round = [
        outcome
        for outcome in outcomes
        if outcome.round_label == "第一轮"
    ]
    second_round = [
        outcome
        for outcome in outcomes
        if outcome.round_label == "第二轮"
    ]

    lines = ["## 第一轮：官方资料与财务数据", ""]
    source_index = 1

    for outcome in first_round:
        section, source_index = _format_query_section(
            outcome, source_index
        )
        lines.append(section)
        lines.append("")

    lines.extend(["## 第二轮：大模型补漏", ""])

    for outcome in second_round:
        section, source_index = _format_query_section(
            outcome, source_index
        )
        lines.append(section)
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


# 按指定关键词搜索时，默认只取前 3 条摘要。
# 法务和业务专家各用一组关键词，避免把一次公司深度搜索的 18 条结果
# 全部塞进提示词，既浪费上下文，也容易让模型抓不住重点。
DEFAULT_PUBLIC_SUMMARY_TOP_N = 3

# 公开搜索摘要的固定标题。
# 智能体提示词会根据这个标题识别“这段是网络摘要，不是正式材料”。
PUBLIC_SUMMARY_HEADING = "【公开网络搜索摘要】"

# 合并材料时用来标记“公开网页摘要”的标签。
# 财务专家会把整份材料当作分析上下文；
# 法务、业务专家会在分析前用 split_public_summary_block() 把这段摘要拆出来，
# 按“未经核实的公开信息”规则处理，避免把网页内容误当成企业提供的正式材料。
PUBLIC_SUMMARY_BLOCK_OPEN = "<public_web_summaries>"
PUBLIC_SUMMARY_BLOCK_CLOSE = "</public_web_summaries>"


def wrap_public_summary_block(text: str) -> str:
    """
    用固定标签包住一段公开网页摘要。

    参数：
        text: 已经整理好的公开网页摘要 Markdown。

    返回：
        str: 带 <public_web_summaries> 标签的文本；输入为空时返回空字符串。

    说明：
        标签只是给程序识别用的边界，不是给用户看的；
        智能体提示词里本来就有同名标签，因此不会改变模型的理解方式。
    """
    clean_text = text.strip() if isinstance(text, str) else ""
    if not clean_text:
        return ""

    return (
        f"{PUBLIC_SUMMARY_BLOCK_OPEN}\n"
        f"{clean_text}\n"
        f"{PUBLIC_SUMMARY_BLOCK_CLOSE}"
    )


def split_public_summary_block(text: str) -> tuple[str, str]:
    """
    从合并材料中拆出被 <public_web_summaries> 包住的公开网页摘要。

    参数：
        text: 可能包含公开网页摘要标签的材料文本。

    返回：
        tuple[str, str]:
            - 去掉网页摘要后的正式材料；
            - 网页摘要正文（不含外层标签）；没有标签时为空字符串。

    说明：
        - 只有 api.py 的“公司双通道”材料会带这对标签；
          普通上传文件没有标签，原样返回，行为完全不变；
        - 如果材料里出现多段标签，会按出现顺序合并；
        - 标签没有闭合时，保守地把剩余内容都留在正式材料里，
          宁可多给模型看，也不误删用户资料。
    """
    if not isinstance(text, str):
        return "", ""
    if not text:
        return "", ""

    formal_parts: list[str] = []
    web_parts: list[str] = []
    cursor = 0

    while True:
        start = text.find(PUBLIC_SUMMARY_BLOCK_OPEN, cursor)
        if start < 0:
            formal_parts.append(text[cursor:])
            break

        formal_parts.append(text[cursor:start])
        content_start = start + len(PUBLIC_SUMMARY_BLOCK_OPEN)
        end = text.find(PUBLIC_SUMMARY_BLOCK_CLOSE, content_start)

        if end < 0:
            # 标签没闭合：不冒险删除，整段保留在正式材料中。
            formal_parts.append(text[start:])
            break

        web_parts.append(text[content_start:end].strip())
        cursor = end + len(PUBLIC_SUMMARY_BLOCK_CLOSE)

    formal_text = "".join(formal_parts).strip()
    web_text = "\n\n".join(
        part for part in web_parts if part
    ).strip()

    return formal_text, web_text


def build_public_summary_query(
    company_name: str,
    topic_query: str,
) -> str:
    """
    拼装“公司简称 + 指定主题”的搜索词。

    参数：
        company_name: 用户输入的公司名称，例如“贵州茅台酒股份有限公司”。
        topic_query: 主题关键词，例如“涉诉 行政处罚”。

    返回：
        str: 实际发送给博查的检索语句；公司名和主题都为空时返回空字符串。

    说明：
        公司名会去掉“股份有限公司”等后缀，提高搜索命中率；
        页面展示时仍使用用户输入的完整名称。
    """
    clean_name = (
        " ".join(company_name.split())
        if isinstance(company_name, str)
        else ""
    )
    clean_topic = (
        " ".join(topic_query.split())
        if isinstance(topic_query, str)
        else ""
    )

    search_name = _short_company_name(clean_name) if clean_name else ""
    return " ".join(
        part for part in (search_name, clean_topic) if part
    ).strip()


def search_public_summaries(
    company_name: str,
    topic_query: str,
    top_n: int = DEFAULT_PUBLIC_SUMMARY_TOP_N,
) -> str:
    """
    按指定主题搜索一家公司的公开网页摘要，只返回前 top_n 条。

    参数：
        company_name: 公司名称，必须非空，例如“贵州茅台酒股份有限公司”。
        topic_query: 主题关键词，例如法务用“涉诉 行政处罚”，
                     业务用“行业竞争格局 主营业务研报”。
        top_n: 最多保留几条摘要，默认 3；调用方传入时会被限制在 1～10。

    返回：
        str:
            - 搜索成功：以“# 【公开网络搜索摘要】（未经核实）”开头的 Markdown，
              每条来源包含标题、发布日期、链接、站点、可直接复制的
              Markdown 引用格式和摘要；
            - 未配置密钥、网络失败、接口报错：以“【搜索失败】”开头的提示；
            - 没有搜到任何结果：以“【未检索到结果】”开头的提示。

    异常：
        本函数不向外抛出网络异常。搜索失败会转换成中文提示，
        并且不会包含任何 API Key。

    说明：
        与 search_company_info() 的两轮深度搜索不同，本函数只执行一条
        指定查询，适合“法务、业务专家各自补充一个专门方向”的场景。
    """
    if not isinstance(company_name, str) or not company_name.strip():
        return (
            f"{SEARCH_FAILURE_PREFIX}请先提供公司名称，"
            "才能检索公开网络信息。"
        )

    clean_name = " ".join(company_name.split())
    if len(clean_name) > 100:
        return (
            f"{SEARCH_FAILURE_PREFIX}公司名称过长（{len(clean_name)} 个字符），"
            "请只填写公司全称，不要粘贴整段资料。"
        )

    if not isinstance(topic_query, str) or not topic_query.strip():
        return (
            f"{SEARCH_FAILURE_PREFIX}搜索主题为空，"
            "无法检索公开网络信息。"
        )

    clean_topic = " ".join(topic_query.split())
    if len(clean_topic) > 100:
        return (
            f"{SEARCH_FAILURE_PREFIX}搜索主题过长（{len(clean_topic)} 个字符），"
            "请缩短为少量关键词。"
        )

    # 防御性限制，避免调用方传入不合理的结果条数。
    try:
        safe_top_n = max(1, min(int(top_n), 10))
    except (TypeError, ValueError):
        safe_top_n = DEFAULT_PUBLIC_SUMMARY_TOP_N

    api_key = _read_bocha_api_key()
    if not api_key:
        return (
            f"{SEARCH_FAILURE_PREFIX}未配置博查搜索 API Key。\n\n"
            "配置方法：\n"
            "1. 打开 https://open.bochaai.com/ 注册并登录；\n"
            "2. 在“API KEY 管理”中创建一个 Key；\n"
            "3. 在项目根目录的 .env 文件中新增一行：\n"
            "   BOCHA_API_KEY=你的博查密钥\n"
            "4. 保存后重启网页服务。\n\n"
            "请勿把密钥发到聊天、截图或代码仓库中。"
            "搜索失败不代表该公司没有风险，只是本次未能获取公开信息。"
        )

    query = build_public_summary_query(clean_name, clean_topic)

    try:
        raw_results = _request_bocha(
            query=query,
            api_key=api_key,
            count=CANDIDATES_PER_QUERY,
        )
    except BochaSearchError as exc:
        return (
            f"{SEARCH_FAILURE_PREFIX}公开网络搜索失败：{exc.user_message}\n\n"
            "搜索失败不代表该公司没有风险，只是本次未能获取公开信息。"
        )

    purpose = _infer_purpose(query)
    selected = _rank_and_select(
        raw_results,
        purpose=purpose,
        limit=safe_top_n,
        company_aliases=_company_aliases(clean_name),
        company_pinyin_aliases=_company_pinyin_aliases(clean_name),
    )

    if not selected:
        return (
            f"{SEARCH_EMPTY_PREFIX}未能用“{query}”检索到公开网页摘要。\n\n"
            "没有检索到结果不代表对应方面没有风险，"
            "只表示本次公开搜索未取得可用信息。"
        )

    search_time = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [
        f"# {PUBLIC_SUMMARY_HEADING}（未经核实）",
        "",
        f"- 查询公司：{clean_name}",
        f"- 搜索词：{query}",
        f"- 检索时间：{search_time}（本机时间）",
        f"- 本次采用：{len(selected)} 条公开网页摘要",
        "- 来源：博查（Bocha）Web Search API",
        "",
        "> 以下内容来自公开网页，可能过时、残缺、由媒体或第三方转述，"
        "也可能混入同名公司的信息。它未经企业提供资料核实，"
        "只能用于初步风险筛查，不能替代正式尽职调查。",
        "",
    ]

    for index, hit in enumerate(selected, start=1):
        lines.extend(
            [
                f"### 来源 {index}",
                f"- 标题：{hit['title'] or '（网页未提供标题）'}",
                # 发布日期固定展示，缺失时明确写“未披露”，
                # 避免大模型在引用中凭空补一个日期。
                "- 发布日期："
                f"{_normalize_publish_date(hit.get('published')) or '未披露'}",
                f"- 链接：{hit['href'] or '（网页未提供链接）'}",
            ]
        )
        if hit.get("domain"):
            lines.append(f"- 站点：{hit['domain']}")
        # 给大模型一条可直接复制的 Markdown 引用，
        # 减少它自己拼接标题、日期、URL 时出错。
        lines.append(
            "- 引用格式："
            + _format_source_citation(
                hit.get("title"),
                hit.get("published"),
                hit.get("href"),
            )
        )
        lines.append(
            f"- 摘要：{hit['body'] or '（网页未提供摘要）'}"
        )
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def search_company_info(
    company_name: str,
    progress_callback: Callable[[str], None] | None = None,
) -> str:
    """
    根据公司名称执行两轮深度搜索，返回公开信息摘要。

    参数：
        company_name: 用户输入的公司名称，例如“某某科技有限公司”。
        progress_callback: 可选的进度回调，接收中文阶段说明；
                           页面用它显示“第一轮搜索中”等状态。

    返回：
        str:
            - 搜索成功：带轮次、查询语句、来源编号和摘要的 Markdown 文本；
              最多 2 个重点网页或 PDF 还会附带正文摘录和 PDF 年报链接；
            - 检测到 PDF 年报：开头提示系统已尝试自动解析，
              如内容不完整可下载后使用 Tab 1 上传分析；
            - 未配置密钥、网络失败或全部查询失败：以“【搜索失败】”开头的提示；
            - 没有搜到任何结果：以“【未检索到结果】”开头的提示；
            - 只有部分查询失败：正常结果中额外标注“【部分查询失败】”。

    异常：
        本函数不会向外抛出网络异常。无论博查或 DeepSeek 返回什么错误，
        都会转换成可直接展示给用户的中文提示，避免网页因外部服务故障而崩溃。
    """
    # 第 1 步：校验公司名称。
    if not isinstance(company_name, str) or not company_name.strip():
        return (
            f"{SEARCH_FAILURE_PREFIX}请先输入要查询的公司名称，"
            "例如“某某科技有限公司”。"
        )

    clean_name = " ".join(company_name.split())

    if len(clean_name) > 100:
        return (
            f"{SEARCH_FAILURE_PREFIX}公司名称过长（{len(clean_name)} 个字符），"
            "请只填写公司全称，不要粘贴整段资料。"
        )

    # 第 2 步：读取博查 API Key。
    # 没有密钥时不发起任何网络请求，直接给出配置步骤。
    api_key = _read_bocha_api_key()
    if not api_key:
        return (
            f"{SEARCH_FAILURE_PREFIX}未配置博查搜索 API Key。\n\n"
            "配置方法：\n"
            "1. 打开 https://open.bochaai.com/ 注册并登录；\n"
            "2. 在“API KEY 管理”中创建一个 Key；\n"
            "3. 打开项目根目录的 .env 文件，新增一行：\n"
            "   BOCHA_API_KEY=你的博查密钥\n"
            "4. 保存后重启网页服务，再重新搜索。\n\n"
            "请勿把密钥发到聊天、截图或代码仓库中。"
            "搜索失败不代表该公司没有风险，只是本次未能获取公开信息。"
        )

    years = recent_years()

    # 搜索用简称：去掉“股份有限公司”等后缀，通常能提高匹配效果。
    # 页面展示仍然使用用户输入的完整名称。
    search_name = _short_company_name(clean_name)
    company_aliases = _company_aliases(clean_name)

    # 公司名拼音别名：例如“华为”生成 huawei，
    # 用来识别 huawei.com 这类企业官网，并在排序时给它最高权重。
    company_pinyin_aliases = _company_pinyin_aliases(clean_name)

    outcomes: list[QueryOutcome] = []

    # 第 3 步：第一轮固定搜索。
    # 三条查询分别负责巨潮、官网/PDF、财务数据，保证基础信息不漏。
    _notify(
        progress_callback,
        "第一轮：正在搜索巨潮资讯网、官网和财务数据（共 3 条）…",
    )

    for index, spec in enumerate(FIRST_ROUND_SPECS, start=1):
        _notify(
            progress_callback,
            f"第一轮 {index}/3：{spec['label']}…",
        )
        query = spec["query_template"].format(
            company_name=search_name,
            years=years,
        )
        outcomes.append(
            _run_query(
                round_label="第一轮",
                purpose=spec["purpose"],
                query=query,
                include=spec["include"],
                allow_pdf_fallback=spec["allow_pdf_fallback"],
                api_key=api_key,
                company_aliases=company_aliases,
                company_pinyin_aliases=company_pinyin_aliases,
            )
        )

    first_round_outcomes = list(outcomes)
    first_round_hits: list[dict] = []
    for outcome in first_round_outcomes:
        for hit in outcome.results:
            first_round_hits.append({**hit, "query": outcome.query})

    # 第一轮全部失败时，第二轮大概率也会失败。
    # 直接返回错误，避免多花 3 次博查调用和一次模型调用。
    if not first_round_hits and all(
        outcome.error is not None for outcome in first_round_outcomes
    ):
        _notify(
            progress_callback,
            "第一轮全部失败，已停止后续搜索以节省费用。",
        )
        return _format_no_results_text(clean_name, outcomes)

    # 第 4 步：让 DeepSeek 根据第一轮结果生成第二轮补搜词。
    # 模型不可用时，query_optimizer 会自动返回内置补搜词。
    _notify(
        progress_callback,
        "第一轮完成，正在让 DeepSeek 分析信息缺口…",
    )
    plan = optimize_second_round_queries(search_name, first_round_hits)

    # 第 5 步：第二轮补漏搜索。
    _notify(progress_callback, f"第二轮：{plan.note}")

    for index, query in enumerate(plan.queries, start=1):
        purpose = _infer_purpose(query)
        _notify(
            progress_callback,
            f"第二轮 {index}/3：{_purpose_label(purpose)}…",
        )
        outcomes.append(
            _run_query(
                round_label="第二轮",
                purpose=purpose,
                query=query,
                include="",
                allow_pdf_fallback=False,
                api_key=api_key,
                company_aliases=company_aliases,
                company_pinyin_aliases=company_pinyin_aliases,
            )
        )

    # 第 6 步：整理输出。
    _notify(progress_callback, "正在整理、去重和排序搜索结果…")

    # 跨查询再去重一次：同一个网页可能同时被第一轮和第二轮搜到。
    _deduplicate_outcomes(outcomes)

    if not any(outcome.results for outcome in outcomes):
        return _format_no_results_text(clean_name, outcomes)

    # 第 7 步：抓取最多 2 个重点网页的正文和列表。
    # 优先抓新闻/资讯列表页，并把页面里的 PDF 年报链接记下来。
    # 单个网页失败不影响整体结果，也不会递归抓取更多页面。
    _notify(
        progress_callback,
        "正在读取重点网页和 PDF 年报（最多 2 个，可能稍慢）…",
    )
    _enrich_with_page_content(outcomes)
    annual_pdf_links = _collect_annual_pdf_links(outcomes)

    header = _format_header(
        clean_name,
        outcomes,
        plan,
        annual_pdf_links=annual_pdf_links,
    )
    sections = _format_sections(outcomes)

    return header + "\n" + sections
