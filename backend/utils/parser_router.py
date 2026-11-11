"""
解析路由（Parser Router）。

职责：
- HTML 网页：用 lxml 删除脚本、导航、广告、页脚等噪声，再按“正文密度”
  从页面中挑出真正的文章区域；
- 动态网页：如果 lxml 提取到的正文过短，说明页面可能由 JavaScript 动态加载，
  此时用 Playwright 启动无头 Chromium 重新渲染，再提取一次；
- PDF 文件：用 pdfplumber 读取文字和表格，并转换成 Markdown。

为什么单独放在 utils/：
- 网页搜索模块负责“去哪里找”，本模块负责“找到后怎么读”；
- 解析逻辑可以单独测试，不会把网络、排序、博查接口的代码混在一起；
- 以后上传的 PDF、其他智能体的网页资料也可以复用这里的函数。

安全说明：
- 本模块不读取、不拼接、不输出任何 API Key；
- 所有内容只在内存中处理，不写入磁盘；
- Playwright 渲染时，每一个网络请求都要经过调用方传入的地址校验函数，
  内网、本机、元数据地址会被直接拦截；
- 不登录、不携带 Cookie、不绕过付费墙或访问控制；
- 解析失败只影响当前网页，不会让整次搜索崩溃。
"""

import logging
import re
from dataclasses import dataclass
from io import BytesIO
from typing import Callable
from urllib.parse import urlparse

from lxml import etree
from lxml import html as lxml_html

# pdfminer 在解析个别损坏 PDF 时会打印大量底层警告。
# 这些警告对用户没有帮助，也不代表整份 PDF 一定失败，
# 因此只保留错误级别以上的日志，避免刷屏。
logging.getLogger("pdfminer").setLevel(logging.ERROR)


# ---------------------------------------------------------------------------
# 可调参数
# ---------------------------------------------------------------------------

# “正文过短”的判断阈值。
# 中文字符信息密度高，正常新闻或公告正文通常远超 300 字；
# 如果清洗后不足 300 个非空白字符，大概率是动态加载的空壳页，
# 或者是只显示“请开启 JavaScript”的提示页。
MIN_MAIN_CONTENT_CHARS = 300

# 单个 HTML 页面最终保留的最大字符数。
# 太长的页面会挤占大模型的输入预算，因此只保留正文开头部分。
MAX_HTML_MAIN_CHARS = 4000

# HTML 页面中最多转换多少个表格、每个表格保留多少行和列。
# 财务报表通常列数不多，限制主要是防止布局表格把正文淹没。
MAX_HTML_TABLES = 6
MAX_HTML_TABLE_ROWS = 25
MAX_HTML_TABLE_COLS = 10

# PDF 解析限制。
# 年报动辄一两百页，全部解析会非常慢，也会产生大量文字。
# 这里采用“前 30 页 + 后 30 页”的策略：
# 前部通常有公司简介和主要财务指标，后部通常有三大财务报表。
MAX_PDF_PAGES = 60
PDF_FRONT_PAGES = 30
PDF_BACK_PAGES = 30
MAX_PDF_MARKDOWN_CHARS = 16000
MAX_PDF_TABLE_PAGES = 10
MAX_PDF_TABLES_PER_PAGE = 4
MAX_PDF_TABLE_ROWS = 35
MAX_PDF_TABLE_COLS = 10

# Playwright 渲染限制。
# 超时后自动放弃浏览器渲染，退回 lxml 的解析结果。
PLAYWRIGHT_TIMEOUT_MS = 15000
PLAYWRIGHT_NETWORK_IDLE_MS = 3000
PLAYWRIGHT_EXTRA_WAIT_MS = 800
MAX_RENDERED_HTML_CHARS = 3_000_000

# 浏览器标识。与 web_searcher 中的普通网页抓取保持一致，
# 表示这是普通浏览器访问，不伪装成登录用户。
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36"
)


class BrowserRenderError(RuntimeError):
    """
    动态网页渲染失败时抛出的中文异常。

    网页搜索模块会捕获它，保留 lxml 已经提取到的内容，
    并把“为什么没有用上浏览器”告诉用户。
    """


class PdfParseError(RuntimeError):
    """
    PDF 解析失败时抛出的中文异常。

    例如：文件不是 PDF、需要密码、是扫描图片版、没有文字层等。
    """


@dataclass
class HtmlMainContent:
    """
    HTML 正文提取结果。

    属性：
        body: 清洗后的正文；如果页面里有表格，表格会以 Markdown 形式追加在后面。
        title: 页面标题，来自 <title> 或第一个 <h1>；没有时为空字符串。
        table_count: 成功转换成 Markdown 的表格数量。
        error: 解析失败时的中文原因；成功时为空字符串。
    """

    body: str = ""
    title: str = ""
    table_count: int = 0
    error: str = ""


@dataclass
class PdfParseResult:
    """
    PDF 解析结果。

    属性：
        markdown: 文字和表格转换后的 Markdown 文本。
        page_count: PDF 总页数。
        parsed_pages: 本次实际读取的页数。
        table_count: 成功转换成 Markdown 的表格数量。
        truncated: 是否因为页数或字符数限制而截断。
        note: 给用户看的中文说明，例如“仅解析前 30 页和后 30 页”。
    """

    markdown: str = ""
    page_count: int = 0
    parsed_pages: int = 0
    table_count: int = 0
    truncated: bool = False
    note: str = ""


# ---------------------------------------------------------------------------
# 通用文本和表格处理
# ---------------------------------------------------------------------------

# 这些标签在显示时应当换行。
# 如果没有这个集合，<p>第一段</p><p>第二段</p> 会被拼成“第一段第二段”，
# 大模型很难分清段落边界。
_BLOCK_TAGS = {
    "address",
    "article",
    "aside",
    "blockquote",
    "br",
    "dd",
    "div",
    "dl",
    "dt",
    "figcaption",
    "figure",
    "footer",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "header",
    "hr",
    "li",
    "main",
    "nav",
    "ol",
    "p",
    "pre",
    "section",
    "table",
    "tbody",
    "td",
    "tfoot",
    "th",
    "thead",
    "tr",
    "ul",
}

# 这些标签没有正文价值，或者会加载外部资源，统一删除。
_NOISE_TAGS = {
    "audio",
    "button",
    "canvas",
    "dialog",
    "embed",
    "footer",
    "form",
    "frame",
    "frameset",
    "header",
    "iframe",
    "input",
    "link",
    "meta",
    "nav",
    "noscript",
    "object",
    "script",
    "select",
    "source",
    "style",
    "svg",
    "template",
    "textarea",
    "video",
}

# 通过 class / id 识别的噪声区域。
# 英文使用“分词边界”匹配，避免把 “adventure” 误判成广告；
# 中文没有空格，直接用包含匹配。
_NOISE_ATTR_PATTERN = re.compile(
    r"(?i)(?:^|[\s_-])(?:"
    r"ad|ads|advert|advertisement|banner|"
    r"nav|navbar|navigation|menu|"
    r"footer|header|sidebar|side-bar|"
    r"breadcrumb|crumb|share|social|"
    r"comment|cookie|popup|modal|"
    r"login|signin|subscribe|newsletter|"
    r"related|recommend|toolbar|pagination|pager|"
    r"copyright|disclaimer|promo|sponsor|qrcode|wechat"
    r")(?:$|[\s_-])"
)

_NOISE_ATTR_CHINESE_WORDS = (
    "广告",
    "导航",
    "菜单",
    "侧栏",
    "侧边栏",
    "页脚",
    "页头",
    "分享",
    "评论",
    "推荐",
    "相关阅读",
    "面包屑",
    "登录",
    "注册",
    "弹窗",
    "二维码",
    "版权",
    "声明",
    "分页",
)

# 通过 class / id 识别“可能是正文容器”的区域。
# 这里只负责产生候选，最终还要比较文字长度和链接密度。
_CONTENT_ATTR_PATTERN = re.compile(
    r"(?i)(?:article|content|main|post|detail|news|text|body|"
    r"rich_media|trs_editor|ucap|zoom)"
)

# display:none 或 visibility:hidden 的内容用户看不到，也不应进入正文。
_HIDDEN_STYLE_PATTERN = re.compile(
    r"(?i)(?:display\s*:\s*none|visibility\s*:\s*hidden)"
)

# PDF 中需要重点提取表格的财务关键词。
_PDF_TABLE_KEYWORDS = (
    "资产负债表",
    "利润表",
    "现金流量表",
    "所有者权益变动表",
    "财务报表",
    "合并资产负债表",
    "合并利润表",
    "合并现金流量表",
    "营业收入",
    "营业成本",
    "货币资金",
    "应收账款",
    "资产总计",
    "负债合计",
    "所有者权益合计",
)


def _normalize_text(text: str) -> str:
    """
    清理文本中的多余空白，同时保留段落换行。

    参数：
        text: 原始文本。

    返回：
        str: 每行去掉首尾空白、连续空格压缩为一个空格、
             连续空行压缩为一个空行的文本。
    """
    if not text:
        return ""

    # 先把 Windows / 老式 Mac 换行统一成 \n，再逐行处理。
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\x00", " ")

    cleaned_lines: list[str] = []
    for line in text.split("\n"):
        line = re.sub(r"[ \t\f\v]+", " ", line).strip()
        if line:
            cleaned_lines.append(line)
        elif cleaned_lines and cleaned_lines[-1] != "":
            # 只保留一个空行，避免 PDF 页眉页脚造成大片空白。
            cleaned_lines.append("")

    return "\n".join(cleaned_lines).strip()


def _clean_table_cell(value: object) -> str:
    """
    把表格单元格转换成可以放进 Markdown 表格的安全文本。

    参数：
        value: pdfplumber 或 lxml 返回的单元格内容，可能是 None、数字或字符串。

    返回：
        str: 去掉了竖线、换行和多余空白的文本。

    说明：
        Markdown 表格用竖线分隔列，单元格里出现竖线会把表格拆坏，
        因此必须转义成 “\\|”；换行也会破坏表格，统一压成空格。
    """
    if value is None:
        return ""

    text = str(value)
    text = text.replace("\r\n", " ").replace("\r", " ").replace("\n", " ")
    text = re.sub(r"\s+", " ", text).strip()
    text = text.replace("|", "\\|")
    return text


def _rows_to_markdown(
    rows: list[list[object]] | None,
    max_rows: int,
    max_cols: int,
) -> str:
    """
    把二维表格数据转换成 Markdown 表格。

    参数：
        rows: 表格的原始行数据，每个单元格可以是 None 或任意对象。
        max_rows: 最多保留多少行数据（不含表头）。
        max_cols: 最多保留多少列。

    返回：
        str: Markdown 表格；表格为空、只有一行或有效内容太少时返回空字符串。

    说明：
        - 第一行当作表头；
        - 全空行会被删除；
        - 每行长度补齐到相同列数，否则 Markdown 表格会错位；
        - 表格过长时只保留前面部分，并在末尾注明。
    """
    if not rows:
        return ""

    cleaned_rows: list[list[str]] = []
    for raw_row in rows:
        if not isinstance(raw_row, (list, tuple)):
            continue

        cells = [
            _clean_table_cell(value)
            for value in list(raw_row)[:max_cols]
        ]

        # 去掉行尾的空单元格，避免表格右侧出现大片空白列。
        while cells and not cells[-1]:
            cells.pop()

        # 整行都没有内容时跳过。
        if any(cells):
            cleaned_rows.append(cells)

    # 至少要有表头和一行数据，单独的标题行没有分析价值。
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
# HTML 解析：lxml 正文提取
# ---------------------------------------------------------------------------


def _safe_drop(element: etree._Element) -> None:
    """
    从 DOM 中删除一个节点，并容忍节点已经被父节点一起删除的情况。

    参数：
        element: 要删除的 lxml 节点。

    说明：
        直接调用 element.drop_tree() 时，如果父节点已经被删除，
        lxml 可能抛出异常。这里先检查父节点是否存在，再删除。
    """
    parent = element.getparent()
    if parent is None:
        return

    try:
        parent.remove(element)
    except (ValueError, TypeError):
        # 节点已经不在父节点里时忽略即可，不需要中断整页解析。
        return


def _is_hidden_element(element: etree._Element) -> bool:
    """
    判断一个节点是否被 HTML 隐藏。

    参数：
        element: lxml 节点。

    返回：
        bool: 带 hidden 属性、aria-hidden="true"、
              或 style 中写了 display:none / visibility:hidden 时返回 True。
    """
    if element.get("hidden") is not None:
        return True

    if (element.get("aria-hidden") or "").strip().lower() == "true":
        return True

    style = element.get("style") or ""
    return bool(_HIDDEN_STYLE_PATTERN.search(style))


def _attribute_marks_noise(element: etree._Element) -> bool:
    """
    判断节点的 class / id 是否像导航、广告、评论等噪声区域。

    参数：
        element: lxml 节点。

    返回：
        bool: 命中英文或中文噪声词时返回 True。
    """
    marker = " ".join(
        part
        for part in (
            element.get("class") or "",
            element.get("id") or "",
        )
        if part
    ).strip()

    if not marker:
        return False

    if _NOISE_ATTR_PATTERN.search(marker):
        return True

    lowered_marker = marker.lower()
    return any(
        word.lower() in lowered_marker
        for word in _NOISE_ATTR_CHINESE_WORDS
    )


def _clean_html_tree(tree: etree._Element) -> None:
    """
    删除 HTML 中与正文无关的节点。

    参数：
        tree: lxml 解析后的文档树；函数会直接修改它。

    说明：
        先收集节点列表再删除，避免“边遍历边删除”导致漏删。
    """
    # HTML 注释可能包含脚本或提示词，统一删除。
    for comment in list(tree.xpath("//comment()")):
        _safe_drop(comment)

    for element in list(tree.iter()):
        # 注释等节点的 tag 不是字符串，跳过。
        if not isinstance(element.tag, str):
            continue

        tag = element.tag.lower()

        # html/body 是整页容器，不能因为 class 名叫 “body” 就被删掉。
        if tag in ("html", "body"):
            continue

        if tag in _NOISE_TAGS:
            _safe_drop(element)
            continue

        if _is_hidden_element(element):
            _safe_drop(element)
            continue

        if _attribute_marks_noise(element):
            _safe_drop(element)


def _element_text(element: etree._Element) -> str:
    """
    提取一个节点及其子节点的可见文字，并在块级标签之间插入换行。

    参数：
        element: lxml 节点。

    返回：
        str: 清理过空白的文本。

    说明：
        lxml 的 text_content() 会把所有文字直接拼在一起，
        这里用 iterwalk() 按开始/结束事件遍历，
        在 <p>、<div>、<li> 等块级标签前后加换行，保留段落结构。
    """
    parts: list[str] = []

    for event, node in etree.iterwalk(
        element, events=("start", "end")
    ):
        if not isinstance(node.tag, str):
            continue

        tag = node.tag.lower()
        is_block = tag in _BLOCK_TAGS

        if event == "start":
            if node.text:
                parts.append(node.text)
            if is_block:
                parts.append("\n")
        else:
            if is_block:
                parts.append("\n")
            if node.tail:
                parts.append(node.tail)

    return _normalize_text("".join(parts))


def _html_tables_to_markdown(
    container: etree._Element,
) -> tuple[str, int]:
    """
    把正文容器中的 HTML 表格转换成 Markdown，并从树中删除这些表格。

    参数：
        container: 已经选定的正文容器。

    返回：
        tuple[str, int]: Markdown 表格文本，以及成功转换的表格数量。

    说明：
        删除表格是为了避免后面的纯文本提取把表格内容再读一遍，
        造成同一份数据重复出现、浪费大模型输入长度。
    """
    markdown_tables: list[str] = []
    converted_count = 0

    # 先转成列表，避免删除表格时影响迭代器。
    tables = list(container.iter("table"))

    for table in tables:
        if converted_count >= MAX_HTML_TABLES:
            break

        # 表格可能已经被外层表格的删除操作一起移除。
        if table.getparent() is None:
            continue

        rows: list[list[object]] = []

        # 只取当前表格的直接行，避免把嵌套表格的行混进来。
        row_elements = table.xpath(
            "./tr | ./thead/tr | ./tbody/tr | ./tfoot/tr"
        )
        for row_element in row_elements:
            cells = row_element.xpath("./td | ./th")
            rows.append(
                [_element_text(cell) for cell in cells]
            )

        markdown_table = _rows_to_markdown(
            rows,
            max_rows=MAX_HTML_TABLE_ROWS,
            max_cols=MAX_HTML_TABLE_COLS,
        )
        if markdown_table:
            markdown_tables.append(markdown_table)
            converted_count += 1

        # 即使表格没有转成 Markdown，也从正文文本中删除，
        # 避免一长串数字把段落结构冲散。
        _safe_drop(table)

    return "\n\n".join(markdown_tables), converted_count


def _candidate_score(element: etree._Element) -> int:
    """
    给“可能是正文容器”的节点打分。

    参数：
        element: 候选节点。

    返回：
        int: 分数越高越像正文；文字太少时返回 -1。

    说明：
        分数主要由正文长度决定，并参考段落数量、链接文字占比。
        导航区域通常文字少、链接多，因此会被压低。
    """
    text = _element_text(element)
    compact_length = len(re.sub(r"\s+", "", text))

    # 少于 60 个非空白字符的容器基本不可能是一篇完整文章。
    if compact_length < 60:
        return -1

    link_text_length = sum(
        len(re.sub(r"\s+", "", _element_text(anchor)))
        for anchor in element.iter("a")
    )
    paragraph_count = sum(1 for _ in element.iter("p"))

    tag = element.tag.lower() if isinstance(element.tag, str) else ""
    semantic_bonus = 0
    if tag in ("article", "main"):
        semantic_bonus += 300
    if (element.get("role") or "").lower() == "main":
        semantic_bonus += 250

    return (
        compact_length
        - int(link_text_length * 0.8)
        + paragraph_count * 20
        + semantic_bonus
    )


def _select_main_container(tree: etree._Element) -> etree._Element:
    """
    从清洗后的 HTML 中选择最像正文的容器。

    参数：
        tree: 已删除噪声节点的文档树。

    返回：
        etree._Element: 选中的正文容器；找不到时退回 <body> 或整棵树。

    说明：
        优先看 <article>、<main>、role="main" 和常见 CMS 的 class/id；
        如果这些都没有，再比较所有 div/section 的正文密度。
    """
    candidates: list[etree._Element] = []

    for element in tree.iter():
        if not isinstance(element.tag, str):
            continue

        tag = element.tag.lower()
        if tag in ("html", "body", "head", "table", "tr", "td", "th"):
            continue

        if tag in ("article", "main"):
            candidates.append(element)
            continue

        if (element.get("role") or "").strip().lower() == "main":
            candidates.append(element)
            continue

        marker = " ".join(
            part
            for part in (
                element.get("class") or "",
                element.get("id") or "",
            )
            if part
        )
        if marker and _CONTENT_ATTR_PATTERN.search(marker):
            candidates.append(element)

    # 没有语义化标签时，再比较普通 div/section。
    if not candidates:
        candidates = [
            element
            for element in tree.iter()
            if isinstance(element.tag, str)
            and element.tag.lower() in ("div", "section")
        ]

    if not candidates:
        body = tree.find(".//body")
        return body if body is not None else tree

    best_element = max(candidates, key=_candidate_score)
    if _candidate_score(best_element) < 0:
        body = tree.find(".//body")
        return body if body is not None else tree

    return best_element


def _extract_title(tree: etree._Element) -> str:
    """
    提取页面标题。

    参数：
        tree: lxml 文档树。

    返回：
        str: 优先使用 <title>，没有时使用第一个 <h1>；都没有则为空字符串。
    """
    title_candidates = [
        tree.findtext(".//title"),
    ]
    h1_element = tree.find(".//h1")
    if h1_element is not None:
        title_candidates.append(_element_text(h1_element))

    for candidate in title_candidates:
        cleaned = _normalize_text(candidate or "")
        if cleaned:
            return re.sub(r"\s+", " ", cleaned)[:200]

    return ""


def extract_main_content(
    html_text: str,
    max_chars: int = MAX_HTML_MAIN_CHARS,
) -> HtmlMainContent:
    """
    用 lxml 从 HTML 中提取正文，并删除导航、广告等噪声。

    参数：
        html_text: 已解码的 HTML 字符串。
        max_chars: 最终正文最多保留多少个字符。

    返回：
        HtmlMainContent: 正文、标题、表格数量和错误说明。

    说明：
        - 先按标签、class/id、隐藏样式删除噪声；
        - 再按正文密度选择最像文章的容器；
        - 表格会转换成 Markdown 后追加到正文末尾；
        - 解析失败时返回带 error 的空结果，不向调用方抛异常。
    """
    if not isinstance(html_text, str) or not html_text.strip():
        return HtmlMainContent(error="页面内容为空。")

    try:
        # document_fromstring 会补全 html/body 结构，比 fragments 更稳定。
        tree = lxml_html.document_fromstring(html_text)
    except (etree.ParserError, ValueError, TypeError):
        return HtmlMainContent(error="网页 HTML 格式无法解析。")

    _clean_html_tree(tree)
    title = _extract_title(tree)
    container = _select_main_container(tree)

    tables_markdown, table_count = _html_tables_to_markdown(container)
    body_text = _element_text(container)

    body_parts: list[str] = []
    if body_text:
        body_parts.append(body_text)
    if tables_markdown:
        body_parts.append("【页面表格】\n" + tables_markdown)

    body = "\n\n".join(body_parts).strip()

    if len(body) > max_chars:
        body = body[: max_chars - 1].rstrip() + "…"

    if not body:
        return HtmlMainContent(
            title=title,
            table_count=table_count,
            error="没有从网页中提取到正文。",
        )

    return HtmlMainContent(
        body=body,
        title=title,
        table_count=table_count,
    )


def needs_browser_rendering(html_text: str, body_text: str) -> bool:
    """
    判断一个网页是否需要交给 Playwright 重新渲染。

    参数：
        html_text: 原始 HTML；保留这个参数是为了以后加入更多动态特征判断。
        body_text: lxml 提取到的正文。

    返回：
        bool: 正文非空白字符少于 MIN_MAIN_CONTENT_CHARS 时返回 True。

    说明：
        有些网站返回的 HTML 只有一个 <div id="app"></div>，
        真正的文字要等 JavaScript 执行后才出现。
        这类页面的 lxml 正文会非常短，因此用长度作为第一层判断。
    """
    compact_length = len(re.sub(r"\s+", "", body_text or ""))

    if compact_length >= MIN_MAIN_CONTENT_CHARS:
        return False

    # 页面本身几乎是空的，没有渲染价值。
    if not html_text or len(html_text.strip()) < 20:
        return False

    return True


# ---------------------------------------------------------------------------
# 动态网页：Playwright 重新渲染
# ---------------------------------------------------------------------------


def _playwright_install_message() -> str:
    """
    返回 Playwright 浏览器内核未安装时的中文提示。

    返回：
        str: 包含安装命令的说明。
    """
    return (
        "Playwright 浏览器内核尚未安装。请在项目虚拟环境中运行："
        "python -m playwright install chromium，"
        "安装完成后重新搜索；本次已退回普通网页解析。"
    )


def render_dynamic_page(
    url: str,
    url_validator: Callable[[str], bool],
    timeout_ms: int = PLAYWRIGHT_TIMEOUT_MS,
) -> str:
    """
    用 Playwright 启动无头 Chromium，重新渲染动态网页并返回 HTML。

    参数：
        url: 要渲染的网页地址。
        url_validator: 地址安全校验函数，返回 True 才允许访问；
                       由 web_searcher 传入，复用现有的公网/SSRF 检查。
        timeout_ms: 页面加载超时时间（毫秒）。

    返回：
        str: 渲染后的 HTML 文本。

    异常：
        BrowserRenderError: Playwright 未安装、浏览器内核未安装、
                            页面加载超时或渲染失败时抛出。
                            异常文字为中文，可直接展示给用户。

    安全说明：
        - 不登录、不携带 Cookie、不接受下载；
        - 页面发出的每个 HTTP(S) 请求都会经过 url_validator 检查，
          内网、本机、元数据地址会被拦截；
        - 图片、视频、字体等资源直接阻止，减少流量和外部请求面；
        - 证书校验保持开启，不忽略 HTTPS 证书错误。
    """
    # 初始地址必须安全，不能等到浏览器打开后才检查。
    if not callable(url_validator) or not url_validator(url):
        raise BrowserRenderError("网页地址不是公网 HTTP(S) 地址，已拒绝渲染。")

    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise BrowserRenderError(
            "Playwright 组件尚未安装。请在项目虚拟环境中运行："
            "python -m pip install -r requirements.txt，"
            "然后运行 python -m playwright install chromium。"
            "本次已退回普通网页解析。"
        ) from exc

    browser = None
    context = None

    try:
        with sync_playwright() as playwright:
            try:
                browser = playwright.chromium.launch(
                    headless=True,
                )
            except Exception as exc:
                message = str(exc)
                if (
                    "Executable doesn't exist" in message
                    or "playwright install" in message
                ):
                    raise BrowserRenderError(
                        _playwright_install_message()
                    ) from exc
                raise BrowserRenderError(
                    "启动无头浏览器失败，本次已退回普通网页解析。"
                ) from exc

            try:
                context = browser.new_context(
                    user_agent=BROWSER_USER_AGENT,
                    locale="zh-CN",
                    viewport={"width": 1365, "height": 900},
                    accept_downloads=False,
                    ignore_https_errors=False,
                    java_script_enabled=True,
                    # 禁止 Service Worker，避免它绕过下面的请求拦截。
                    service_workers="block",
                )
                page = context.new_page()
                page.set_default_timeout(timeout_ms)

                # 按“来源”缓存安全校验结果，避免同一网站的几十个资源
                # 每个都做一次 DNS 查询，拖慢渲染速度。
                origin_allowed: dict[str, bool] = {}

                def _route_handler(route) -> None:
                    """
                    拦截浏览器发出的请求，阻止内网地址和重型媒体资源。

                    参数：
                        route: Playwright 的请求路由对象。
                    """
                    request = route.request
                    request_url = request.url

                    # data/blob/about 是页面内部资源，不产生外部网络请求。
                    if request_url.startswith(
                        ("data:", "blob:", "about:")
                    ):
                        route.continue_()
                        return

                    try:
                        parsed = urlparse(request_url)
                    except ValueError:
                        route.abort()
                        return

                    if parsed.scheme not in ("http", "https"):
                        route.abort()
                        return

                    origin = f"{parsed.scheme}://{parsed.netloc}"
                    if origin not in origin_allowed:
                        origin_allowed[origin] = bool(
                            url_validator(request_url)
                        )

                    if not origin_allowed[origin]:
                        route.abort()
                        return

                    # 图片、视频、字体对提取文字没有帮助，
                    # 阻止它们可以显著减少加载时间和外部请求。
                    if request.resource_type in {
                        "image",
                        "media",
                        "font",
                    }:
                        route.abort()
                        return

                    route.continue_()

                # 在 context 上注册路由，而不是只注册到当前 page：
                # 网站在页面里弹出的新窗口也属于同一个 context，
                # 这样弹窗发出的内网请求同样会被拦截。
                context.route("**/*", _route_handler)

                page.goto(
                    url,
                    wait_until="domcontentloaded",
                    timeout=timeout_ms,
                )

                # 有些页面在 DOM 加载完成后才异步填充数据。
                # networkidle 超时不算失败，继续读取当前内容即可。
                try:
                    page.wait_for_load_state(
                        "networkidle",
                        timeout=min(
                            PLAYWRIGHT_NETWORK_IDLE_MS,
                            timeout_ms,
                        ),
                    )
                except Exception:
                    pass

                page.wait_for_timeout(PLAYWRIGHT_EXTRA_WAIT_MS)

                # 触发一次滚动，让部分懒加载内容进入视口。
                try:
                    page.evaluate(
                        "window.scrollTo(0, document.body.scrollHeight)"
                    )
                    page.wait_for_timeout(
                        PLAYWRIGHT_EXTRA_WAIT_MS
                    )
                except Exception:
                    pass

                rendered_html = page.content()

            finally:
                if context is not None:
                    try:
                        context.close()
                    except Exception:
                        pass
                if browser is not None:
                    try:
                        browser.close()
                    except Exception:
                        pass

    except BrowserRenderError:
        raise

    except Exception as exc:
        message = str(exc).lower()
        if "asyncio" in message:
            raise BrowserRenderError(
                "当前运行环境不支持同步浏览器渲染，"
                "本次已退回普通网页解析。"
            ) from exc
        raise BrowserRenderError(
            "动态网页渲染失败，本次已退回普通网页解析。"
        ) from exc

    if not rendered_html or not rendered_html.strip():
        raise BrowserRenderError("动态网页渲染后没有取得内容。")

    if len(rendered_html) > MAX_RENDERED_HTML_CHARS:
        rendered_html = rendered_html[:MAX_RENDERED_HTML_CHARS]

    return rendered_html


# ---------------------------------------------------------------------------
# PDF 解析：pdfplumber 提取文字和表格
# ---------------------------------------------------------------------------


def _contains_pdf_table_keyword(text: str) -> bool:
    """
    判断一页文字是否包含财务报表关键词。

    参数：
        text: PDF 单页提取出的文字。

    返回：
        bool: 包含“资产负债表”“利润表”“营业收入”等词时返回 True。
    """
    return any(keyword in text for keyword in _PDF_TABLE_KEYWORDS)


def _select_pdf_page_indices(
    total_pages: int,
    max_pages: int = MAX_PDF_PAGES,
) -> list[int]:
    """
    选择本次要解析的 PDF 页码（从 0 开始）。

    参数：
        total_pages: PDF 总页数。
        max_pages: 最多解析多少页。

    返回：
        list[int]: 去重、升序排列的页码列表。

    说明：
        年报的财务数据通常集中在开头（主要财务指标）和结尾（财务报表）。
        页数超过上限时，取前 30 页和后 30 页，中间部分跳过。
    """
    if total_pages <= 0:
        return []

    if total_pages <= max_pages:
        return list(range(total_pages))

    front_count = min(PDF_FRONT_PAGES, max_pages)
    back_count = min(PDF_BACK_PAGES, max_pages - front_count)

    indices = list(range(front_count))
    indices.extend(
        range(total_pages - back_count, total_pages)
    )

    return sorted(set(indices))


def _extract_pdf_tables(
    page,
    page_text: str,
) -> list[str]:
    """
    从 PDF 单页中提取表格并转换成 Markdown。

    参数：
        page: pdfplumber 的页面对象。
        page_text: 该页已经提取出的文字，用于判断是否值得找表格。

    返回：
        list[str]: Markdown 表格列表；没有可用表格时返回空列表。

    说明：
        默认使用“线条”识别表格，适合大多数财报；
        如果页面含财务关键词但没有识别到表格，再用“文字位置”策略试一次，
        以覆盖没有画表格线的财报。
    """
    if not page_text and not _contains_pdf_table_keyword(page_text):
        return []

    raw_tables: list[list[list[object]]] = []

    try:
        raw_tables = page.extract_tables() or []
    except Exception:
        raw_tables = []

    if not raw_tables and _contains_pdf_table_keyword(page_text):
        try:
            raw_tables = page.extract_tables(
                table_settings={
                    "vertical_strategy": "text",
                    "horizontal_strategy": "text",
                }
            ) or []
        except Exception:
            raw_tables = []

    markdown_tables: list[str] = []
    for raw_table in raw_tables[:MAX_PDF_TABLES_PER_PAGE]:
        markdown_table = _rows_to_markdown(
            raw_table,
            max_rows=MAX_PDF_TABLE_ROWS,
            max_cols=MAX_PDF_TABLE_COLS,
        )
        if markdown_table:
            markdown_tables.append(markdown_table)

    return markdown_tables


def parse_pdf_to_markdown(
    pdf_bytes: bytes,
    max_chars: int = MAX_PDF_MARKDOWN_CHARS,
) -> PdfParseResult:
    """
    用 pdfplumber 把 PDF 的文字和表格转换成 Markdown。

    参数：
        pdf_bytes: PDF 文件的原始字节，必须是完整的 PDF 内容。
        max_chars: 最终 Markdown 最多保留多少个字符。

    返回：
        PdfParseResult: Markdown 正文、页数、表格数量和截断说明。

    异常：
        PdfParseError: 文件为空、不是 PDF、需要密码、
                       没有文字层（扫描图片版）或解析失败时抛出。
                       异常文字为中文，可直接展示给用户。

    说明：
        - 为防止年报太大导致搜索卡住，只解析前 30 页和后 30 页；
        - 表格只在含财务关键词的页面重点提取；
        - 如果整份 PDF 没有文字也没有表格，提示可能是扫描图片版。
    """
    if not isinstance(pdf_bytes, (bytes, bytearray)) or not pdf_bytes:
        raise PdfParseError("PDF 文件内容为空，无法解析。")

    # 检查 PDF 文件头。前面允许有少量空白或 BOM。
    header = bytes(pdf_bytes[:1024]).lstrip()
    if not header.startswith(b"%PDF-"):
        raise PdfParseError("该链接返回的不是 PDF 文件，已跳过。")

    try:
        import pdfplumber
    except ImportError as exc:
        raise PdfParseError(
            "PDF 解析组件尚未安装。请在项目虚拟环境中运行："
            "python -m pip install -r requirements.txt。"
        ) from exc

    try:
        with pdfplumber.open(BytesIO(bytes(pdf_bytes))) as pdf:
            total_pages = len(pdf.pages)
            if total_pages == 0:
                raise PdfParseError("这个 PDF 没有可读取的页面。")

            selected_indices = _select_pdf_page_indices(total_pages)

            markdown_parts: list[str] = []
            table_count = 0
            parsed_pages = 0
            truncated = False

            for page_index in selected_indices:
                page = pdf.pages[page_index]
                page_number = page_index + 1

                try:
                    page_text = page.extract_text() or ""
                except Exception:
                    page_text = ""

                page_text = _normalize_text(page_text)

                # 只有前几页或含财务关键词的页面才重点找表格，
                # 避免每一页都做表格识别而拖慢速度。
                should_extract_tables = (
                    table_count < MAX_PDF_TABLE_PAGES
                    and (
                        _contains_pdf_table_keyword(page_text)
                        or parsed_pages < 3
                    )
                )

                table_markdowns: list[str] = []
                if should_extract_tables:
                    table_markdowns = _extract_pdf_tables(
                        page, page_text
                    )
                    table_count += len(table_markdowns)

                if not page_text and not table_markdowns:
                    continue

                parsed_pages += 1

                page_parts: list[str] = [f"【第 {page_number} 页】"]
                if page_text:
                    page_parts.append(page_text)

                for table_index, table_markdown in enumerate(
                    table_markdowns,
                    start=1,
                ):
                    page_parts.append(
                        f"【第 {page_number} 页表格 {table_index}】\n"
                        f"{table_markdown}"
                    )

                page_block = "\n\n".join(page_parts)

                current_length = sum(
                    len(part) for part in markdown_parts
                )
                remaining = max_chars - current_length

                if remaining <= 0:
                    truncated = True
                    break

                if len(page_block) > remaining:
                    # 至少保留一点有效内容，避免只剩一个页码标题。
                    if remaining > 200:
                        markdown_parts.append(
                            page_block[: remaining - 1].rstrip()
                            + "…"
                        )
                    truncated = True
                    break

                markdown_parts.append(page_block)

                if current_length + len(page_block) >= max_chars:
                    truncated = True
                    break

            if not markdown_parts:
                raise PdfParseError(
                    "没有从这份 PDF 中提取到文字或表格，"
                    "它可能是扫描图片版，需要先做 OCR 识别。"
                )

            note_parts: list[str] = []
            if total_pages > len(selected_indices):
                note_parts.append(
                    f"PDF 共 {total_pages} 页，"
                    f"为控制时间只解析前 {PDF_FRONT_PAGES} 页"
                    f"和后 {PDF_BACK_PAGES} 页。"
                )
            if truncated:
                note_parts.append(
                    "提取内容已达到长度上限，后面的页面未纳入。"
                )

            return PdfParseResult(
                markdown="\n\n".join(markdown_parts),
                page_count=total_pages,
                parsed_pages=parsed_pages,
                table_count=table_count,
                truncated=truncated,
                note="".join(note_parts),
            )

    except PdfParseError:
        raise

    except Exception as exc:
        message = str(exc).lower()
        if "password" in message or "encrypted" in message:
            raise PdfParseError(
                "这个 PDF 需要密码才能打开，已跳过。"
            ) from exc
        raise PdfParseError(
            "PDF 解析失败，可能是文件损坏或格式特殊，已跳过。"
        ) from exc
