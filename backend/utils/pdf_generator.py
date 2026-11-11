"""
PDF 导出工具（Markdown 正文 + 表格图片版）。

职责：
- 把业务、法务、财务专家生成的 Markdown 报告转换成 PDF 字节流；
- 正文继续按段落排版，Markdown 表格先画成高分辨率三线表 PNG，
  再嵌入 PDF，避免 fpdf2 把 ``<table>`` 当成普通文字而出现乱码；
- 使用 Windows 自带的中文字体，避免中文显示成方块；
- 只在内存和系统临时目录中处理，不把财务资料永久写到磁盘，
  也不读取、打印或写入任何 API Key。

整体流程：
1. 按“连续以 | 开头、以 | 结尾的行”把报告切成文字块和表格块；
2. 文字块清洗 LaTeX 和 Markdown 符号后，按公文/研报习惯排版：
   普通段落首行缩进两个全角空格，段间留 5 毫米，
   列表项不缩进且每条之后留 2 毫米，再用 ``pdf.multi_cell`` 写入；
3. 表格块用 pandas 解析、matplotlib 画成三线表 PNG，
   再按页面宽高等比缩放后居中插入 PDF，图片上下各留 5 毫米；
4. 表格转图片失败时自动退回文字版表格，保证 PDF 仍能导出；
5. 临时图片统一放在系统临时目录，生成结束后自动删除。
"""

import csv
import logging
import os
import re
import shutil
import tempfile
import unicodedata
from io import StringIO
from pathlib import Path

from fpdf import FPDF

# 微软雅黑等 .ttc 字体集合里带有 fpdf2 不需要的 MERG 表。
# fontTools 在嵌入字体会打印 “MERG NOT subset” 警告，但不影响 PDF 内容。
# 这里只关闭 fontTools 的这类无关警告，避免 Streamlit 终端被重复提示刷屏。
logging.getLogger("fontTools.subset").setLevel(logging.ERROR)

# 本模块自己的日志只记录异常类型，不记录报告正文和财务数据，
# 避免把敏感内容写进终端或日志文件。
logger = logging.getLogger(__name__)


class PdfGenerationError(RuntimeError):
    """
    PDF 生成失败时抛出的统一异常。

    网页层只需要捕获这个异常，并把中文提示展示给用户；
    异常信息不会包含 API Key、财务原文等敏感内容。
    """


# Windows 字体目录。
# 大多数电脑是 C:\\Windows\\Fonts；如果系统装在别的盘，
# WINDIR 环境变量会指向正确位置。
_WINDOWS_FONT_DIR = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"

# 正文默认字号（磅）和行高（毫米）。
# 10.5 磅适合 A4 阅读；5.8 毫米行高约等于 1.55 倍行距，
# 比默认行距宽松，长报告连续阅读时不容易疲劳。
_BODY_FONT_SIZE = 10.5
_BODY_LINE_HEIGHT_MM = 5.8

# 页面底部保留 18 毫米，给页脚页码留出空间。
_PAGE_BOTTOM_MARGIN_MM = 18.0

# 判断“放不放得下”时允许的毫米级误差。
# fpdf2 内部用浮点数计算页面高度，图片刚好铺满一整页时，
# “当前 Y + 图片高度”可能比页面底线大 0.0000001 毫米；
# 如果直接比较，就会误判为放不下，多插一个空白页。
# 留 0.6 毫米容差，既不会把内容真的挤出页面，也能消除浮点误判。
_PAGE_BREAK_TOLERANCE_MM = 0.6

# 表格图片的分辨率。220 dpi 在屏幕上清晰，打印时也不会明显发虚。
_TABLE_IMAGE_DPI = 220

# 表格图片的预设宽度（英寸）。
# A4 纸去掉左右各 15 毫米页边距后，正文宽约 180 毫米（约 7.1 英寸），
# 因此图片按 7.1 英寸绘制，嵌入 PDF 时基本可以 1:1 显示，字号不会被缩得太小。
_TABLE_IMAGE_WIDTH_IN = 7.1

# 表格正文和表头的字号（磅）。
_TABLE_BODY_FONT_SIZE = 9.0
_TABLE_HEADER_FONT_SIZE = 9.5

# 表格图片上下额外留白（英寸）。
# 三线表的顶部和底部粗线如果紧贴图片边缘，插入 PDF 后容易被裁掉，
# 因此每张图片都在表格高度之外再加一点空白。
_TABLE_FIGURE_EXTRA_HEIGHT_IN = 0.18

# 当前页剩余空间至少要有这么多毫米，才允许长表格从当前页开始排。
# 如果只剩很小一块空间，硬塞几行表格反而显得零碎，
# 不如让表格从新的一页开始；60 毫米大约能放下表头和三四行数据。
_MIN_TABLE_FRAGMENT_HEIGHT_MM = 60.0

# 中文公文/研报常用的“首行缩进两字符”。
# 这里必须使用两个全角空格（Unicode 编码 U+3000），不能写成两个普通空格：
# 普通空格只有半个汉字宽，打印出来几乎看不出缩进；
# 全角空格与汉字等宽，两个正好是标准的“空两格”。
# 用字符编码明确写出 U+3000，再重复两次；
# 这样即使编辑器把全角空格显示得很像普通空格，代码含义也不会含糊。
_FIRST_LINE_INDENT = chr(0x3000) * 2

# 自然段之间的垂直间距（毫米）。
# 约等于空出一行，让段落之间“透气”；
# 这个间距只在遇到空行（Markdown 的 \n\n）或文字块结束时添加，
# 不会给每一行都加，否则短短一段会被拆得七零八落。
_PARAGRAPH_SPACING_MM = 5.0

# 每个列表项之后的额外垂直间距（毫米）。
# 列表项本身已经单独占行，再加 2 毫米可以让相邻两条不那么挤。
_LIST_ITEM_SPACING_MM = 2.0

# 表格图片上下的留白（毫米）。
# 图片上方留白用于和表标题/上一段正文隔开，下方留白用于和下一段正文隔开；
# 表格图片本身有边框，如果紧贴文字会显得非常拥挤。
_TABLE_CONTEXT_SPACING_MM = 5.0


# 中文字体候选列表，按优先级从高到低排列。
# 每一项是：(fpdf2 内部使用的字体名, 常规字体文件, 粗体文件)。
#
# 为什么首选微软雅黑（msyh.ttc）：
# - 它是 Windows 10/11 自带字体，覆盖常用汉字和标点；
# - 显示效果比宋体更适合屏幕阅读。
# 为什么保留宋体、黑体等备用：
# - 精简版 Windows 或某些系统可能缺少微软雅黑；
# - 只要有一个字体可用，PDF 就不会出现中文乱码。
_FONT_CANDIDATES = (
    (
        "CNYaHei",
        _WINDOWS_FONT_DIR / "msyh.ttc",
        _WINDOWS_FONT_DIR / "msyhbd.ttc",
    ),
    (
        "CNSimSun",
        _WINDOWS_FONT_DIR / "simsun.ttc",
        _WINDOWS_FONT_DIR / "simsunb.ttf",
    ),
    (
        "CNSimHei",
        _WINDOWS_FONT_DIR / "simhei.ttf",
        _WINDOWS_FONT_DIR / "simhei.ttf",
    ),
    (
        "CNKaiTi",
        _WINDOWS_FONT_DIR / "simkai.ttf",
        _WINDOWS_FONT_DIR / "simkai.ttf",
    ),
    (
        "CNFangSong",
        _WINDOWS_FONT_DIR / "simfang.ttf",
        _WINDOWS_FONT_DIR / "simfang.ttf",
    ),
)

# matplotlib 绘制表格图片时单独查找字体文件。
# 这里直接给 FontProperties 传文件路径，不依赖系统字体注册，
# 因此即使 matplotlib 的字体缓存没有刷新，也能正确显示中文。
_MATPLOTLIB_REGULAR_FONT_CANDIDATES = (
    _WINDOWS_FONT_DIR / "msyh.ttc",
    _WINDOWS_FONT_DIR / "simhei.ttf",
    _WINDOWS_FONT_DIR / "simsun.ttc",
)
_MATPLOTLIB_BOLD_FONT_CANDIDATES = (
    _WINDOWS_FONT_DIR / "msyhbd.ttc",
    _WINDOWS_FONT_DIR / "simhei.ttf",
    _WINDOWS_FONT_DIR / "simsun.ttc",
)

# Markdown 表格行：整行以 | 开头、以 | 结尾（允许前后有空格）。
_TABLE_ROW_RE = re.compile(r"^\s*\|.*\|\s*$")

# Markdown 标题：行首一到多个 #，后面的空格可有可无。
# 大模型有时会写成“###标题”（# 后面没有空格），
# 如果仍要求必须有空格，这一行就会被当成普通正文，把 # 原样印进 PDF。
_HEADING_RE = re.compile(r"^(#{1,})\s*(.*)$")

# 行首的 Markdown 标题符号（包括 # 后面没有空格的情况）。
# 用于最后兜底：标题、表格标题、普通段落写入前都再清一次，
# 确保任何情况下都不会把 # 号印在纸上。
_LEADING_HEADING_MARKS_RE = re.compile(r"^\s*#{1,}\s*")

# LaTeX 数学分隔符：只删除反斜杠括号本身，保留括号里的内容。
_LATEX_PAREN_RE = re.compile(r"\\[()]")

# LaTeX 转义字符：把 \% 变回 %、\_ 变回 _、\& 变回 & 等。
_LATEX_ESCAPE_RE = re.compile(r"\\([%_&#$])")

# 连续两个以上的空格或制表符，压缩成一个空格。
_MULTI_SPACE_RE = re.compile(r"[ \t]{2,}")

# Markdown 表格分隔行的单元格：---、:---、---:、:---:。
_SEPARATOR_CELL_RE = re.compile(r":?-+:?")

# 表标题：以“表1：”“表2.”“附表3：”等开头。
# 识别它，是为了让标题和后面的表格图片一起换页，避免标题孤零零留在上一页。
_TABLE_TITLE_RE = re.compile(
    r"^(?:附)?表\s*[0-9一二三四五六七八九十百]+\s*[：:.]"
)


class _DueDiligencePDF(FPDF):
    """
    尽职调查报告专用的 PDF 类。

    继承 fpdf2 的 FPDF，只增加一个页脚：
    - 在每页底部居中显示“第 X 页”；
    - 报告较长时，页码能帮助阅读和打印后装订。

    参数：
        cn_font_family: 当前已经注册好的中文字体名。
                        页脚也要用同一套字体，否则页码中的“第/页”会乱码。
    """

    # 默认字体名会在生成 PDF 时被实际选中的字体覆盖。
    cn_font_family: str = "CNYaHei"

    def footer(self) -> None:
        """
        绘制每页页脚。

        说明：
            fpdf2 在换页时自动调用 footer()，不需要手动触发。
            页脚只写页码，不写公司名或财务数据，避免把敏感信息带到页边。
        """
        # 距离页面底部 15 毫米，居中显示页码。
        self.set_y(-15)
        self.set_font(self.cn_font_family, "", 8)
        self.set_text_color(120, 120, 120)
        self.cell(0, 10, f"第 {self.page_no()} 页", align="C")


def _register_chinese_font(pdf: FPDF) -> str:
    """
    按优先级尝试注册 Windows 中文字体。

    参数：
        pdf: 已经创建好的 FPDF 对象。

    返回：
        str: 注册成功的字体名，可直接传给 pdf.set_font()。

    异常：
        PdfGenerationError: 所有候选字体都不存在或无法读取时抛出。

    说明：
        fpdf2 对“常规、粗体、斜体、粗斜体”分别注册。
        报告标题、表头等位置需要粗体，如果只注册常规字体，
        set_font(..., "B") 会报错，所以四种样式都要注册。
        如果某个字体没有独立的粗体文件，就用常规文件代替，
        宁可字体不加粗，也不能让 PDF 生成失败。
    """
    for family, regular_path, bold_path in _FONT_CANDIDATES:
        # 常规字体文件不存在时，直接尝试下一个候选。
        if not regular_path.exists():
            continue

        # 粗体文件不存在时，退回常规文件。
        actual_bold_path = bold_path if bold_path.exists() else regular_path

        try:
            pdf.add_font(family, "", str(regular_path))
            pdf.add_font(family, "B", str(actual_bold_path))
            # 报告一般没有斜体；用常规字体占位，
            # 这样 Markdown 中偶尔出现 *斜体* 时也不会因为缺字体而报错。
            pdf.add_font(family, "I", str(regular_path))
            pdf.add_font(family, "BI", str(actual_bold_path))
            return family
        except Exception:
            # 某个字体文件损坏或格式不受支持时，继续尝试下一个候选。
            # 这里不把底层异常直接抛给用户，避免出现难懂的技术信息。
            continue

    # 所有候选都失败时，给出可操作的中文提示。
    raise PdfGenerationError(
        "未找到可用的 Windows 中文字体。请确认电脑中存在 "
        f"{_WINDOWS_FONT_DIR / 'msyh.ttc'}（微软雅黑）或 "
        f"{_WINDOWS_FONT_DIR / 'simsun.ttc'}（宋体），然后重试。"
    )


def _is_table_title_line(line: str) -> bool:
    """
    判断一行是不是表标题（例如“**表1：近两年合同负债对比**”）。

    参数：
        line: 待判断的一行文字。

    返回：
        bool: 去掉加粗标记后，以“表1：”“附表2.”等开头时返回 True。

    说明：
        识别表标题，是为了在排版时把它和后面的表格图片绑定，
        空间不足时一起换到下一页，避免标题留在上一页底部。
    """
    text = line.strip()
    if text.startswith("**") and text.endswith("**") and text.count("**") == 2:
        text = text[2:-2].strip()
    return _TABLE_TITLE_RE.match(text) is not None


def _split_markdown_blocks(markdown_text: str) -> list[tuple[str, str]]:
    """
    把 Markdown 报告切成“文字块”和“表格块”。

    参数：
        markdown_text: 专家返回的完整 Markdown 报告。

    返回：
        list[tuple[str, str]]:
            每个元素是 (块类型, 块文字)，块类型为 "text" 或 "table"。

    说明：
        Markdown 表格的特征是连续多行都以 | 开头、以 | 结尾。
        只要遇到这种行，就先把前面累积的文字块收起来，
        再把连续的表格行合并成一个表格块。
        如果表格正上方紧贴着一行“表1：……”标题，
        会把标题从文字块里拿出来，一起放进表格块，
        这样 PDF 排版时标题和表格图片就能作为整体换页。
    """
    blocks: list[tuple[str, str]] = []
    text_lines: list[str] = []
    table_lines: list[str] = []
    pending_table_title = ""

    def flush_text() -> None:
        """把已经累积的文字行保存成一个文字块。"""
        if text_lines:
            blocks.append(("text", "\n".join(text_lines)))
            text_lines.clear()

    def flush_table() -> None:
        """把已经累积的表格行保存成一个表格块。"""
        nonlocal pending_table_title

        if table_lines:
            table_content = "\n".join(table_lines)
            if pending_table_title:
                # 表标题放在表格内容最前面；
                # 后续解析表格时会自动忽略不是 | 开头的标题行。
                table_content = pending_table_title + "\n" + table_content
            blocks.append(("table", table_content))
            table_lines.clear()

        # 无论本次有没有表格，都要清空待用标题，
        # 避免它被错误地带到下一张表。
        pending_table_title = ""

    for raw_line in markdown_text.splitlines():
        line = raw_line.rstrip()

        if _TABLE_ROW_RE.match(line):
            # 先去掉标题与表格之间的空行，便于检查上一行是不是表标题。
            while text_lines and not text_lines[-1].strip():
                text_lines.pop()

            if text_lines and _is_table_title_line(text_lines[-1]):
                pending_table_title = text_lines.pop()
                # 如果标题前面还有正文，补一个空行，
                # 让正文和后面的表格之间保留正常间距。
                if text_lines:
                    text_lines.append("")

            # 表格开始前，先把前面的文字落盘。
            flush_text()
            table_lines.append(line)
        else:
            # 离开表格后，先把表格落盘，再继续累积文字。
            flush_table()
            text_lines.append(line)

    flush_text()
    flush_table()
    return blocks


def _clean_latex(text: str) -> str:
    """
    清理大模型输出中的 LaTeX 符号和多余空白。

    参数：
        text: 原始 Markdown 文本。

    返回：
        str: 适合写入 PDF 的普通文本。

    处理规则：
        - ``\\(`` 和 ``\\)`` 是公式分隔符，直接删除，保留括号里的内容；
        - ``\\%``、``\\_``、``\\&`` 等转义字符还原成普通字符；
        - 删除单独成行的 ``$$`` 公式标记；
        - 连续空格压缩成一个，行尾空格删除；
        - 三个以上连续空行压缩成两个，避免 PDF 出现大片空白。
    """
    if not isinstance(text, str):
        return ""

    cleaned = text.replace("\r\n", "\n").replace("\r", "\n")

    # 公式分隔符只删符号，不删公式内容。
    cleaned = _LATEX_PAREN_RE.sub("", cleaned)

    # \% -> %，\_ -> _，\& -> & 等。
    cleaned = _LATEX_ESCAPE_RE.sub(r"\1", cleaned)

    # \[ 和 \] 是另一种公式分隔符，同样只删符号。
    cleaned = cleaned.replace("\\[", "").replace("\\]", "")

    # 删除单独成行的 $$ 标记，但保留公式内容。
    cleaned = re.sub(r"^\s*\$\$\s*$", "", cleaned, flags=re.MULTILINE)

    cleaned_lines: list[str] = []
    for line in cleaned.splitlines():
        line = line.replace("\t", " ")
        line = _MULTI_SPACE_RE.sub(" ", line).rstrip()
        cleaned_lines.append(line)

    cleaned = "\n".join(cleaned_lines)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _strip_inline_markdown(text: str) -> str:
    """
    删除正文中的 Markdown 行内标记，得到适合 multi_cell 的纯文本。

    参数：
        text: 可能包含加粗、行内代码等标记的一行文字。

    返回：
        str: 去掉标记后的文字。

    说明：
        不直接全部删除 “__”，因为变量名里也可能出现下划线；
        这里只删除成对的 __文字__，避免把 DEEPSEEK_API_KEY 改坏。
    """
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text, flags=re.DOTALL)
    text = re.sub(r"__(.+?)__", r"\1", text, flags=re.DOTALL)
    text = re.sub(r"~~(.+?)~~", r"\1", text, flags=re.DOTALL)
    # 处理模型偶尔留下的未配对标记。
    text = text.replace("**", "").replace("`", "")
    # 兜底删除行首的 # 号，确保标题符号不会原样出现在 PDF 里。
    # 只删除开头的 #，正文中间的“#”或链接锚点不会被误删。
    text = _LEADING_HEADING_MARKS_RE.sub("", text, count=1)
    return text.strip()


def _is_separator_row(cells: list[str]) -> bool:
    """
    判断一行是不是 Markdown 表格的分隔行（例如 |---|---|）。

    参数：
        cells: 已经按 | 拆开的单元格列表。

    返回：
        bool: 全部非空单元格都符合 ---、:---、---:、:---: 时返回 True。
    """
    non_empty_cells = [cell.strip() for cell in cells if cell.strip()]
    if not non_empty_cells:
        return False
    return all(
        _SEPARATOR_CELL_RE.fullmatch(cell) is not None
        for cell in non_empty_cells
    )


def _clean_table_cell(value: str) -> str:
    """
    清洗表格单元格中的文字。

    参数：
        value: 原始单元格内容。

    返回：
        str: 去掉 HTML 换行、Markdown 标记、LaTeX 符号后的单行文字；
             空单元格统一写成“未披露”，避免 PDF 表格出现空白格。

    说明：
        表格是图片，不会参与 PDF 文本搜索，因此这里不需要保留粗体等格式，
        只要保证文字完整、没有乱码即可。
    """
    text = str(value).replace("\r", " ").replace("\n", " ")
    text = re.sub(r"<br\s*/?>", " ", text, flags=re.IGNORECASE)
    text = _clean_latex(text)
    text = text.replace("**", "").replace("`", "")
    text = re.sub(r"\s+", " ", text)
    return text.strip() if text.strip() else "未披露"


def _extract_table_rows(
    md_table_str: str,
) -> tuple[list[str], list[list[str]]] | None:
    """
    从 Markdown 表格文本中提取表头和数据行（纯 Python 版本）。

    参数：
        md_table_str: 连续的 Markdown 表格文本。

    返回：
        (表头列表, 数据行列表)；表格格式不完整时返回 None。

    说明：
        这个函数既作为 pandas 解析失败时的备用方案，
        也负责在交给 pandas 之前把每一行的列数补齐，
        避免某一行的竖线数量不一致导致 read_csv 直接报错。
    """
    rows: list[list[str]] = []

    for raw_line in md_table_str.splitlines():
        line = raw_line.strip()
        if not line or not line.startswith("|") or not line.endswith("|"):
            continue

        # 去掉最外层两个竖线，再按竖线拆分。
        cells = [cell.strip() for cell in line[1:-1].split("|")]

        # 分隔行（|---|）不是数据，跳过。
        if _is_separator_row(cells):
            continue

        rows.append([_clean_table_cell(cell) for cell in cells])

    # 至少要有表头和一行数据，单行表格无法判断列含义。
    if len(rows) < 2:
        return None

    column_count = len(rows[0])
    if column_count < 1:
        return None

    normalized_rows: list[list[str]] = []
    for cells in rows:
        if len(cells) < column_count:
            # 少写的单元格补“未披露”，保证每一行列数一致。
            cells = cells + ["未披露"] * (column_count - len(cells))
        elif len(cells) > column_count:
            # 多出来的内容合并到最后一列，避免直接丢字。
            cells = cells[: column_count - 1] + [
                " ".join(cells[column_count - 1 :])
            ]
        normalized_rows.append(cells)

    headers = normalized_rows[0]
    # 表头为空时给一个占位名，避免 pandas 生成“Unnamed”列名。
    headers = [
        header if header != "未披露" else f"列{index + 1}"
        for index, header in enumerate(headers)
    ]

    return headers, normalized_rows[1:]


def _parse_markdown_table(
    md_table_str: str,
) -> tuple[list[str], list[list[str]]] | None:
    """
    使用 pandas.read_csv 解析 Markdown 表格。

    参数：
        md_table_str: 连续的 Markdown 表格文本。

    返回：
        (表头列表, 数据行列表)；无法解析时返回 None。

    说明：
        按用户要求使用 pandas.read_csv，并用 StringIO 把字符串
        包装成“文件”交给 pandas。
        pandas 解析失败时自动退回纯 Python 版本，
        保证表格转图片失败也不会影响 PDF 导出。
    """
    manual_result = _extract_table_rows(md_table_str)
    if manual_result is None:
        return None

    try:
        # 延迟导入 pandas：
        # 只有真正下载 PDF 时才需要它，网页其他部分可以更快启动。
        import pandas as pd

        headers, data_rows = manual_result
        csv_text = "\n".join(
            "|".join(row) for row in [headers] + data_rows
        )

        frame = pd.read_csv(
            StringIO(csv_text),
            sep="|",
            dtype=str,
            keep_default_na=False,
            skipinitialspace=True,
            quoting=csv.QUOTE_NONE,
            engine="python",
        )

        # 如果 pandas 解析出的列数和表头不一致，说明表格里有特殊字符，
        # 此时使用纯 Python 的结果更稳妥。
        if frame.empty or len(frame.columns) != len(headers):
            return manual_result

        parsed_headers = [
            str(column).strip() or f"列{index + 1}"
            for index, column in enumerate(frame.columns)
        ]
        parsed_rows = [
            [
                str(value).strip() if str(value).strip() else "未披露"
                for value in row
            ]
            for row in frame.itertuples(index=False, name=None)
        ]
        return parsed_headers, parsed_rows

    except Exception:
        # pandas 缺失、解析异常时都退回纯 Python 结果。
        # 不记录表格内容，避免把财务数据写进日志。
        return manual_result


def _find_existing_font(candidates: tuple[Path, ...]) -> Path | None:
    """
    从字体候选列表中找出第一个真实存在的文件。

    参数：
        candidates: 字体文件路径元组。

    返回：
        Path | None: 找到的字体路径；都不存在时返回 None。
    """
    for path in candidates:
        if path.exists():
            return path
    return None


def _display_width(text: str) -> int:
    """
    估算一段文字在等宽环境下的显示宽度。

    参数：
        text: 待估算的文字。

    返回：
        int: 中文、全角标点按 2 个宽度计算，英文、数字按 1 个宽度计算。

    说明：
        这个宽度只用于决定表格列的相对宽度和换行位置，
        不需要非常精确；把中文当成两个英文字符宽，
        可以避免中文列被算得过窄而频繁换行。
    """
    width = 0
    for char in str(text):
        if unicodedata.east_asian_width(char) in ("W", "F", "A"):
            width += 2
        else:
            width += 1
    return width


def _wrap_text(text: str, max_units: int) -> list[str]:
    """
    按显示宽度手动换行，供 matplotlib 表格单元格使用。

    参数：
        text: 单元格文字，可能包含模型写入的换行符。
        max_units: 一行最多容纳的显示宽度。

    返回：
        list[str]: 换行后的每一行文字。

    说明：
        matplotlib 的表格单元格不会自动折行；
        如果直接把长文字放进去，会超出单元格甚至被裁掉。
        这里先按字符宽度切好，再用换行符拼回去，
        中英文混排也能保持整齐。
    """
    max_units = max(2, int(max_units))
    wrapped_lines: list[str] = []

    for paragraph in str(text).split("\n"):
        current_line = ""
        current_width = 0
        for char in paragraph:
            char_width = (
                2
                if unicodedata.east_asian_width(char) in ("W", "F", "A")
                else 1
            )
            if current_line and current_width + char_width > max_units:
                wrapped_lines.append(current_line)
                current_line = char
                current_width = char_width
            else:
                current_line += char
                current_width += char_width
        wrapped_lines.append(current_line)

    return wrapped_lines or [""]


def _balance_column_fractions(
    column_widths: list[int],
    column_count: int,
) -> list[float]:
    """
    根据每列内容的宽度，计算一组平衡的列宽比例。

    参数：
        column_widths: 每列最长内容的显示宽度。
        column_count: 列数。

    返回：
        list[float]: 每列占总宽度的比例，所有元素之和为 1。

    说明：
        如果完全按内容宽度分配，一个很长的“变动原因”列
        会把“2024年”“2025年”这类数字列挤得很窄，导致数字被拆行。
        因此先用 0.75 次方削弱长列的独占效应，
        再给每列设置最小、最大宽度，最后反复归一化。
    """
    if column_count <= 0:
        return []
    if column_count == 1:
        return [1.0]

    # 0.75 次方相当于“压缩差距”：长列仍然更宽，但不会无限膨胀。
    weights = [max(1, width) ** 0.75 for width in column_widths]
    weight_sum = sum(weights)
    if weight_sum <= 0:
        return [1.0 / column_count] * column_count

    fractions = [weight / weight_sum for weight in weights]

    if column_count <= 3:
        min_fraction, max_fraction = 0.12, 0.55
    elif column_count == 4:
        min_fraction, max_fraction = 0.10, 0.45
    else:
        min_fraction, max_fraction = 0.08, 0.34

    # 反复“夹紧再归一化”，让比例最终既满足上下限、总和又为 1。
    for _ in range(5):
        fractions = [
            min(max(fraction, min_fraction), max_fraction)
            for fraction in fractions
        ]
        fraction_sum = sum(fractions)
        fractions = [fraction / fraction_sum for fraction in fractions]

    return fractions


def _looks_numeric(value: str) -> bool:
    """
    判断一个单元格是不是数字（含百分比、千分位、负数）。

    参数：
        value: 单元格文字。

    返回：
        bool: 可以转换成数字时返回 True。

    说明：
        这个判断只影响对齐方式：
        数字列靠右、文字列靠左，是财务报表常见的排版习惯。
    """
    text = str(value).strip()
    if not text:
        return False

    # 去掉常见占位符和括号、百分号、千分位逗号后再尝试转换。
    if text in {"-", "—", "未披露", "不适用", "N/A", "n/a"}:
        return False

    cleaned = text.replace(",", "").replace("，", "")
    cleaned = cleaned.strip("()（）[]【】 ")
    cleaned = cleaned.rstrip("%")
    try:
        float(cleaned)
        return True
    except ValueError:
        return False


def _detect_numeric_columns(data_rows: list[list[str]]) -> list[bool]:
    """
    找出哪些列是“数字列”。

    参数：
        data_rows: 表格数据行。

    返回：
        list[bool]: 每一列是否为数字列。

    说明：
        只要该列至少有一个数字，且所有非占位符内容都能转成数字，
        就认为它是数字列。这样“12.5%”“1,200.00”“(300)”都能正确识别。
    """
    if not data_rows:
        return []

    column_count = len(data_rows[0])
    numeric_columns: list[bool] = []

    for column_index in range(column_count):
        values = [str(row[column_index]).strip() for row in data_rows]
        non_placeholder_values = [
            value
            for value in values
            if value not in {"", "-", "—", "未披露", "不适用", "N/A", "n/a"}
        ]
        numeric_values = [
            value for value in non_placeholder_values if _looks_numeric(value)
        ]
        numeric_columns.append(
            bool(numeric_values)
            and len(numeric_values) == len(non_placeholder_values)
        )

    return numeric_columns


def _build_table_image_layout(md_table_str: str) -> dict | None:
    """
    解析 Markdown 表格，并计算绘制三线表所需的全部布局数据。

    参数：
        md_table_str: 连续的 Markdown 表格文本。

    返回：
        dict | None: 成功时返回布局数据；表格格式不合法、
                     找不到中文字体或计算失败时返回 None。

    说明：
        把“解析表格、分配列宽、计算每行高度”集中到这里，
        后面的 _render_table_chunk 就只需要负责“画指定行的表格”。
        同一张长表格按页拆成多段时，列宽、字号和换行方式都来自同一份
        布局数据，因此多张图片拼起来仍然像同一张表。
    """
    try:
        parsed_table = _parse_markdown_table(md_table_str)
        if parsed_table is None:
            return None

        headers, data_rows = parsed_table
        column_count = len(headers)
        if column_count < 1 or not data_rows:
            return None

        # 延迟导入 matplotlib：它体积较大、启动较慢，
        # 只有真正需要画表格图片时才加载。
        from matplotlib.font_manager import FontProperties

        regular_font_path = _find_existing_font(
            _MATPLOTLIB_REGULAR_FONT_CANDIDATES
        )
        if regular_font_path is None:
            logger.warning("表格转图片失败：未找到可用的中文字体。")
            return None

        bold_font_path = (
            _find_existing_font(_MATPLOTLIB_BOLD_FONT_CANDIDATES)
            or regular_font_path
        )
        regular_font = FontProperties(fname=str(regular_font_path))
        bold_font = FontProperties(fname=str(bold_font_path))

        all_rows = [headers] + data_rows

        # 计算每一列最长内容的显示宽度，作为分配列宽的依据。
        column_widths = [
            max(_display_width(row[column_index]) for row in all_rows)
            for column_index in range(column_count)
        ]
        column_fractions = _balance_column_fractions(
            column_widths,
            column_count,
        )

        # 估算图片宽度内大约能放多少个“显示宽度单位”。
        # 一个显示单位约等于半个字号，7.1 英寸宽、9 磅字，
        # 大约能容纳 106 个单位；再预留 8% 给单元格内边距。
        total_capacity_units = int(
            (_TABLE_IMAGE_WIDTH_IN * 72)
            / (_TABLE_BODY_FONT_SIZE * 0.5)
            * 0.92
        )
        column_capacities = [
            max(4, int(total_capacity_units * fraction))
            for fraction in column_fractions
        ]

        # 表头用粗体，实际占用略宽，因此容量再打九折。
        wrapped_headers = [
            _wrap_text(
                headers[column_index],
                max(4, int(column_capacities[column_index] * 0.9)),
            )
            for column_index in range(column_count)
        ]
        wrapped_data_rows: list[list[list[str]]] = []
        for row in data_rows:
            wrapped_data_rows.append(
                [
                    _wrap_text(
                        row[column_index],
                        column_capacities[column_index],
                    )
                    for column_index in range(column_count)
                ]
            )

        # matplotlib 的表格用换行符表示单元格内换行，
        # 不能直接传“行列表”，否则会显示成 ['文字'] 这样的列表字符串。
        header_cell_text = ["\n".join(lines) for lines in wrapped_headers]
        data_cell_text = [
            ["\n".join(cell_lines) for cell_lines in row]
            for row in wrapped_data_rows
        ]

        header_line_count = max(len(lines) for lines in wrapped_headers)
        data_row_line_counts = [
            max(len(cell_lines) for cell_lines in row)
            for row in wrapped_data_rows
        ]

        # 每行高度 = 行数 × 单行高度 + 上下内边距。
        # 单行高度按 1.42 倍字号估算，保证中文字符上下不粘连。
        single_line_height_in = (
            _TABLE_BODY_FONT_SIZE * 1.42 / 72
        )
        header_row_height_in = (
            header_line_count * single_line_height_in + 0.16
        )
        data_row_heights_in = [
            line_count * single_line_height_in + 0.16
            for line_count in data_row_line_counts
        ]

        return {
            "header_cell_text": header_cell_text,
            "data_cell_text": data_cell_text,
            "header_row_height_in": header_row_height_in,
            "data_row_heights_in": data_row_heights_in,
            "column_fractions": column_fractions,
            "column_count": column_count,
            "numeric_columns": _detect_numeric_columns(data_rows),
            "regular_font": regular_font,
            "bold_font": bold_font,
            "figure_width_in": _TABLE_IMAGE_WIDTH_IN,
        }

    except Exception as exc:
        # 只记录异常类型，不记录表格内容，避免财务数据进入日志。
        logger.warning(
            "Markdown 表格转图片失败（%s）。",
            type(exc).__name__,
        )
        return None


def _render_table_chunk(
    layout: dict,
    output_path: str | Path,
    data_row_start: int,
    data_row_end: int,
) -> bool:
    """
    把表格的指定数据行画成一张三线表 PNG。

    参数：
        layout: _build_table_image_layout 返回的布局数据。
        output_path: PNG 输出路径。
        data_row_start: 起始数据行下标（包含）。
        data_row_end: 结束数据行下标（不包含）。

    返回：
        bool: 成功返回 True；绘图或保存失败返回 False。

    说明：
        每一段都会重新绘制表头，因此表格跨页后读者仍能看清每一列的含义。
        列宽、字号和单元格换行方式全部来自 layout，
        所以多段图片拼起来仍然像同一张表。
    """
    figure = None

    try:
        from matplotlib.backends.backend_agg import FigureCanvasAgg
        from matplotlib.figure import Figure

        data_cell_text = layout["data_cell_text"][
            data_row_start:data_row_end
        ]
        if not data_cell_text:
            return False

        row_heights_in = (
            [layout["header_row_height_in"]]
            + layout["data_row_heights_in"][data_row_start:data_row_end]
        )
        figure_height_in = (
            sum(row_heights_in) + _TABLE_FIGURE_EXTRA_HEIGHT_IN
        )

        # 创建纯白画布。dpi 只影响屏幕预览，
        # 最终保存时还会按 _TABLE_IMAGE_DPI 重新渲染。
        figure = Figure(
            figsize=(layout["figure_width_in"], figure_height_in),
            dpi=_TABLE_IMAGE_DPI,
            facecolor="white",
        )
        FigureCanvasAgg(figure)
        axes = figure.add_axes([0, 0, 1, 1])
        axes.set_facecolor("white")
        axes.set_xlim(0, 1)
        axes.set_ylim(0, 1)
        axes.axis("off")

        # bbox 留出 1.2% 的边距，避免三线表的粗线贴到图片边缘。
        bbox_x, bbox_y = 0.012, 0.012
        bbox_width, bbox_height = 0.976, 0.976

        table = axes.table(
            cellText=data_cell_text,
            colLabels=layout["header_cell_text"],
            cellLoc="left",
            colLoc="center",
            loc="center",
            colWidths=layout["column_fractions"],
            bbox=[bbox_x, bbox_y, bbox_width, bbox_height],
        )
        table.auto_set_font_size(False)

        numeric_columns = layout["numeric_columns"]
        column_count = layout["column_count"]
        height_sum = sum(row_heights_in)
        y_top = bbox_y + bbox_height

        # 逐行设置高度和位置：
        # 长文本行会自动变高，短文本行保持紧凑。
        for row_index, row_height_in in enumerate(row_heights_in):
            row_height_fraction = row_height_in / height_sum
            row_y = (
                y_top
                - sum(row_heights_in[: row_index + 1])
                / height_sum
                * bbox_height
            )
            for column_index in range(column_count):
                cell = table[(row_index, column_index)]
                cell.set_y(row_y)
                cell.set_height(row_height_fraction * bbox_height)
                cell.set_facecolor("white")
                cell.set_edgecolor("none")
                cell.set_linewidth(0)
                # 清空四个方向的边线，配合后面手动画三条横线，
                # 保证表格内部没有任何竖线或横线。
                cell.visible_edges = ""
                cell.PAD = 0.06

                is_header = row_index == 0
                if is_header:
                    horizontal_alignment = "center"
                elif numeric_columns[column_index]:
                    horizontal_alignment = "right"
                else:
                    horizontal_alignment = "left"

                cell.set_text_props(
                    fontproperties=(
                        layout["bold_font"]
                        if is_header
                        else layout["regular_font"]
                    ),
                    color="black",
                    ha=horizontal_alignment,
                    va="center",
                )
                cell.get_text().set_fontsize(
                    _TABLE_HEADER_FONT_SIZE
                    if is_header
                    else _TABLE_BODY_FONT_SIZE
                )
                cell.get_text().set_wrap(True)

        # 手动画三条横线：
        # 1）表格顶部粗线；2）表头底部细线；3）表格底部粗线。
        line_x_start = bbox_x
        line_x_end = bbox_x + bbox_width
        header_bottom_y = (
            y_top - row_heights_in[0] / height_sum * bbox_height
        )
        bottom_y = bbox_y

        axes.plot(
            [line_x_start, line_x_end],
            [y_top, y_top],
            color="black",
            linewidth=1.8,
            transform=axes.transAxes,
            clip_on=False,
            solid_capstyle="butt",
        )
        axes.plot(
            [line_x_start, line_x_end],
            [header_bottom_y, header_bottom_y],
            color="black",
            linewidth=0.7,
            transform=axes.transAxes,
            clip_on=False,
            solid_capstyle="butt",
        )
        axes.plot(
            [line_x_start, line_x_end],
            [bottom_y, bottom_y],
            color="black",
            linewidth=1.8,
            transform=axes.transAxes,
            clip_on=False,
            solid_capstyle="butt",
        )

        image_path = Path(output_path)
        image_path.parent.mkdir(parents=True, exist_ok=True)
        figure.savefig(
            str(image_path),
            dpi=_TABLE_IMAGE_DPI,
            facecolor="white",
        )
        return True

    except Exception as exc:
        # 只记录异常类型，不记录表格内容，避免财务数据进入日志。
        logger.warning(
            "Markdown 表格转图片失败（%s）。",
            type(exc).__name__,
        )
        return False

    finally:
        # 及时释放 matplotlib 图片对象占用的内存。
        if figure is not None:
            try:
                figure.clear()
            except Exception:
                pass


def _table_fits_within_height(
    md_table_str: str,
    max_height_mm: float,
    display_width_mm: float,
) -> bool | None:
    """
    快速判断整张表格能否放进指定高度，不真正绘图。

    参数：
        md_table_str: 连续的 Markdown 表格文本。
        max_height_mm: 允许占用的最大显示高度（毫米）。
        display_width_mm: 图片在 PDF 中实际显示的宽度（毫米）。

    返回：
        bool | None: 放得下返回 True，放不下返回 False；
                     表格格式不合法或无法计算时返回 None。

    说明：
        PDF 排版时用它决定“表格能不能从当前页接着排”。
        如果当前页只剩很小一块空间，而表格又放不下，
        就可以提前换页，避免先画出一小段表格又被迫拆开。
    """
    if max_height_mm <= 0 or display_width_mm <= 0:
        return False

    layout = _build_table_image_layout(md_table_str)
    if layout is None:
        return None

    figure_width_in = float(layout["figure_width_in"])
    total_figure_height_in = (
        float(layout["header_row_height_in"])
        + sum(layout["data_row_heights_in"])
        + _TABLE_FIGURE_EXTRA_HEIGHT_IN
    )
    max_figure_height_in = (
        max_height_mm * figure_width_in / display_width_mm
    )
    return total_figure_height_in <= max_figure_height_in


def _split_table_rows_for_pages(
    layout: dict,
    max_height_mm: float | None,
    display_width_mm: float | None,
    first_chunk_max_height_mm: float | None = None,
) -> list[tuple[int, int]]:
    """
    按页面可用高度，把表格数据行切分成若干段。

    参数：
        layout: _build_table_image_layout 返回的布局数据。
        max_height_mm: 续表页每段图片允许占用的最大显示高度（毫米）。
        display_width_mm: 图片在 PDF 中实际显示的宽度（毫米）。
        first_chunk_max_height_mm: 第一段图片单独使用的最大高度；
                                   不传时和 max_height_mm 相同。

    返回：
        list[tuple[int, int]]: 每一段的（起始行, 结束行），结束行不包含。

    说明：
        - 第一页有原表标题，续表页有“（续表）”标题，两者占用的高度
          可能不同，所以第一段和后续段可以分别限制高度；
        - 每段至少包含一行数据，保证切分一定向前推进；
        - 单行本身就比一整页还高时，这一行会单独成段，
          由 PDF 层等比缩小，程序不会陷入死循环。
    """
    data_row_count = len(layout["data_cell_text"])
    if data_row_count <= 0:
        return []

    first_limit_mm = (
        first_chunk_max_height_mm
        if first_chunk_max_height_mm is not None
        and first_chunk_max_height_mm > 0
        else max_height_mm
    )
    continuation_limit_mm = (
        max_height_mm
        if max_height_mm is not None and max_height_mm > 0
        else first_limit_mm
    )

    if (
        first_limit_mm is None
        or first_limit_mm <= 0
        or continuation_limit_mm is None
        or continuation_limit_mm <= 0
    ):
        # 没有传高度限制：整张表画成一段。
        return [(0, data_row_count)]

    figure_width_in = float(layout["figure_width_in"])
    if display_width_mm is None or display_width_mm <= 0:
        # 没给 PDF 显示宽度时，按图片自身宽度 1:1 处理。
        display_width_mm = figure_width_in * 25.4

    def _mm_to_figure_inches(height_mm: float) -> float:
        """把 PDF 里的显示高度换算成图片内部的英寸高度。"""
        return height_mm * figure_width_in / display_width_mm

    first_max_figure_in = _mm_to_figure_inches(first_limit_mm)
    continuation_max_figure_in = _mm_to_figure_inches(
        continuation_limit_mm
    )

    header_height_in = float(layout["header_row_height_in"])
    data_heights_in = layout["data_row_heights_in"]

    chunk_ranges: list[tuple[int, int]] = []
    start = 0
    chunk_index = 0

    while start < data_row_count:
        max_figure_height_in = (
            first_max_figure_in
            if chunk_index == 0
            else continuation_max_figure_in
        )
        current_height_in = (
            header_height_in + _TABLE_FIGURE_EXTRA_HEIGHT_IN
        )
        end = start

        while end < data_row_count:
            row_height_in = data_heights_in[end]
            # 每段至少放一行；即使这一行比整页还高，也先单独成段，
            # 后面由 PDF 层等比缩小，避免程序陷入死循环。
            if end == start:
                current_height_in += row_height_in
                end += 1
                continue
            if (
                current_height_in + row_height_in
                <= max_figure_height_in
            ):
                current_height_in += row_height_in
                end += 1
                continue
            break

        chunk_ranges.append((start, end))
        start = end
        chunk_index += 1

    return chunk_ranges


def markdown_table_to_images(
    md_table_str: str,
    temp_dir: str | Path,
    file_prefix: str,
    max_height_mm: float | None = None,
    display_width_mm: float | None = None,
    first_chunk_max_height_mm: float | None = None,
) -> list[Path]:
    """
    把 Markdown 表格按页面高度拆成多张带表头的三线表 PNG。

    参数：
        md_table_str: 连续的 Markdown 表格文本。
        temp_dir: 存放临时 PNG 的目录。
        file_prefix: 文件名前缀，生成的文件名形如 table_1_1.png。
        max_height_mm: 续表页每段图片允许占用的最大显示高度（毫米）。
                       None 表示不限制，整张表画成一张图。
        display_width_mm: 图片在 PDF 中实际显示的宽度（毫米）。
                          用来把“页面剩余毫米数”换算成图片内部的英寸高度。
        first_chunk_max_height_mm: 第一段图片单独使用的最大高度；
                                   不传时和 max_height_mm 相同。

    返回：
        list[Path]: 按顺序排列的分段图片路径；
                    任何一段生成失败都会清理临时文件并返回空列表。

    说明：
        表格超过一页时，会按数据行切开，每段都重复表头。
        调用方在续表页顶部写“原表名（续表）”，读者不会丢失列的上下文。
    """
    layout = _build_table_image_layout(md_table_str)
    if layout is None:
        return []

    chunk_ranges = _split_table_rows_for_pages(
        layout,
        max_height_mm=max_height_mm,
        display_width_mm=display_width_mm,
        first_chunk_max_height_mm=first_chunk_max_height_mm,
    )
    if not chunk_ranges:
        return []

    output_dir = Path(temp_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    image_paths: list[Path] = []
    for chunk_index, (start, end) in enumerate(
        chunk_ranges,
        start=1,
    ):
        image_path = output_dir / f"{file_prefix}_{chunk_index}.png"
        if not _render_table_chunk(layout, image_path, start, end):
            # 任何一段失败都清理已经生成的分段，
            # 让调用方统一走文字版兜底，避免只插入半张表。
            for generated_path in image_paths:
                try:
                    generated_path.unlink(missing_ok=True)
                except OSError:
                    pass
            return []
        image_paths.append(image_path)

    return image_paths


def markdown_table_to_image(
    md_table_str: str,
    temp_img_path: str | Path,
) -> bool:
    """
    把一段 Markdown 表格画成一张白底黑字的三线表 PNG。

    参数：
        md_table_str: 连续的 Markdown 表格文本。
        temp_img_path: 生成的临时 PNG 文件路径。

    返回：
        bool: 成功生成图片返回 True；解析失败、字体缺失、绘图失败
              等任何异常都返回 False，绝不向外抛出。

    说明：
        - 这是 PDF 排版的关键兜底：即使表格画不出来，
          调用方也会退回文字版表格，PDF 仍然可以导出；
        - 图片使用纯白背景，适合打印；
        - 表头顶部粗线、表头底部细线、表格底部粗线，构成经典三线表；
        - 不画任何内部竖线，靠单元格留白区分列；
        - 本函数不限制高度，整张表画成一张图；
          需要按页拆成多张“续表”时，使用 markdown_table_to_images。
    """
    layout = _build_table_image_layout(md_table_str)
    if layout is None:
        return False

    data_row_count = len(layout["data_cell_text"])
    if data_row_count <= 0:
        return False

    return _render_table_chunk(
        layout,
        Path(temp_img_path),
        0,
        data_row_count,
    )


def _page_bottom_limit(pdf: FPDF) -> float:
    """
    返回页面内容区的下边界（毫米）。

    参数：
        pdf: 当前 FPDF 对象。

    说明：
        底部要留出页码空间，因此不能直接用纸张高度 pdf.h，
        而是减去 _PAGE_BOTTOM_MARGIN_MM。
    """
    return pdf.h - _PAGE_BOTTOM_MARGIN_MM


def _full_page_content_height(pdf: FPDF) -> float:
    """
    返回一张全新页面从顶部页边距到底部安全线的可用高度（毫米）。

    参数：
        pdf: 当前 FPDF 对象。

    说明：
        新页面写入内容前，Y 坐标位于上页边距 pdf.t_margin，
        因此一整页真正能放内容的高度是“底部安全线 - 上页边距”。
        图片缩放和换页判断都以这个高度为上限。
    """
    return max(0.0, _page_bottom_limit(pdf) - pdf.t_margin)


def _get_remaining_height(pdf: FPDF) -> float:
    """
    返回当前页底部安全线以上还剩多少毫米。

    参数：
        pdf: 当前 FPDF 对象。

    返回：
        float: 剩余高度；Y 已经超过安全线时返回 0，不返回负数。
    """
    return max(0.0, _page_bottom_limit(pdf) - pdf.get_y())


def _ensure_space(pdf: FPDF, needed_mm: float) -> None:
    """
    检查当前页剩余空间，不够时最多换一页。

    参数：
        pdf: 当前 FPDF 对象。
        needed_mm: 接下来要写入的内容大约需要多少毫米高度。

    说明：
        这是修复“PDF 开头出现空白页”的关键函数之一。旧版只要
        “当前 Y + 需要高度 > 页面底线”就无条件 add_page()；
        当图片刚好铺满整页时，浮点误差会误判，连续调用就会插入空白页。

        新逻辑：
        - 留出 _PAGE_BREAK_TOLERANCE_MM 容差，避免浮点误判；
        - 如果需要的空间比一整页还高，换页也放不下，
          这里不换页，交给调用方缩放图片或拆分内容；
        - 如果当前页已经在顶部（刚换页、还没写内容），
          再换页只会产生空白页，因此也不换。
    """
    if needed_mm <= 0:
        return

    bottom_limit = _page_bottom_limit(pdf)
    current_y = pdf.get_y()

    # 加上容差后能放下，就直接留在当前页。
    if current_y + needed_mm <= bottom_limit + _PAGE_BREAK_TOLERANCE_MM:
        return

    # 需要的空间超过一整页正文高度，换页同样放不下。
    # 这种情况由调用方按剩余空间缩放，不在这里制造空白页。
    if needed_mm > _full_page_content_height(pdf) + _PAGE_BREAK_TOLERANCE_MM:
        return

    # 当前页已经在顶部却仍放不下，说明这页本来就是空页；
    # 再 add_page() 只会留下一个只有页码的空白页。
    if current_y <= pdf.t_margin + _PAGE_BREAK_TOLERANCE_MM:
        return

    pdf.add_page()


def _write_heading(
    pdf: FPDF,
    level: int,
    heading_text: str,
    font_family: str,
) -> None:
    """
    写入一个 Markdown 标题。

    参数：
        pdf: 当前 FPDF 对象。
        level: 标题级别（1～6）。
        heading_text: 标题文字（不含 # 号）。
        font_family: 已注册的中文字体名。

    说明：
        标题用粗体，并按级别设置字号；
        二级标题下方加一条浅灰色细线，让“维度标题”更醒目。
    """
    heading_sizes = {
        1: 16.0,
        2: 13.5,
        3: 11.8,
        4: 11.0,
        5: 10.5,
        6: 10.5,
    }
    font_size = heading_sizes.get(level, _BODY_FONT_SIZE)
    clean_text = _strip_inline_markdown(_clean_latex(heading_text))
    if not clean_text:
        return

    # 标题上方至少留出“标题本身 + 10 毫米”的空间，
    # 避免标题孤零零地留在页面最底部。
    _ensure_space(pdf, font_size * 0.7 + 10)

    if pdf.get_y() > pdf.t_margin + 2:
        pdf.ln(4.0 if level <= 2 else 2.8)

    pdf.set_font(font_family, "B", font_size)
    pdf.set_text_color(0, 0, 0)
    pdf.multi_cell(
        0,
        font_size * 0.58,
        clean_text,
        align="L",
        wrapmode="CHAR",
        new_x="LMARGIN",
        new_y="NEXT",
    )

    if level == 2:
        # 二级标题是报告的主维度，下面加一条浅灰细线。
        pdf.set_draw_color(180, 180, 180)
        pdf.set_line_width(0.25)
        line_y = pdf.get_y() + 1.0
        pdf.line(pdf.l_margin, line_y, pdf.w - pdf.r_margin, line_y)
        pdf.ln(2.2)
    else:
        pdf.ln(1.4)


def _measure_bold_line_height(
    pdf: FPDF,
    bold_text: str,
    font_family: str,
) -> float:
    """
    精确测量一行加粗文字（可能自动换行成多行）会占多少毫米。

    参数：
        pdf: 当前 FPDF 对象。
        bold_text: 已经去掉 ** 标记的文字。
        font_family: 已注册的中文字体名。

    返回：
        float: 文字高度 + 下方 1.2 毫米间距；空文字返回 0。

    说明：
        表格标题和表格图片必须作为整体换页。如果只凭空估算一个固定值
        （旧版固定预留 24 毫米），标题很长时会估小，标题和图片被拆到两页；
        标题很短时又会估大，白白触发换页。fpdf2 的 multi_cell 支持
        dry_run=True、output="HEIGHT"，可以在不真正写字的条件下量出真实高度。
    """
    clean_text = _strip_inline_markdown(_clean_latex(bold_text))
    if not clean_text:
        return 0.0

    pdf.set_font(font_family, "B", _BODY_FONT_SIZE)
    try:
        measured = pdf.multi_cell(
            0,
            _BODY_LINE_HEIGHT_MM,
            clean_text,
            dry_run=True,
            output="HEIGHT",
            wrapmode="CHAR",
        )
        height = float(measured) if isinstance(measured, (int, float)) else 0.0
    except Exception:
        # 极少数 fpdf2 版本不支持 dry_run 的 HEIGHT 输出，
        # 用“按字符数估算行数”的保守办法兜底，保证程序不中断。
        estimated_lines = max(1, len(clean_text) // 35 + 1)
        height = _BODY_LINE_HEIGHT_MM * estimated_lines

    # 加上 _write_bold_line 末尾固定写入的 1.2 毫米间距。
    return height + 1.2


def _write_bold_line(
    pdf: FPDF,
    bold_text: str,
    font_family: str,
) -> None:
    """
    写入一整行加粗文字（例如 “**表1：近两年合同负债对比**”）。

    参数：
        pdf: 当前 FPDF 对象。
        bold_text: 已经去掉 ** 标记的文字。
        font_family: 已注册的中文字体名。

    说明：
        表格标题、字段名经常单独成行并加粗；
        这里用粗体整行输出，比把星号原样写进 PDF 更清楚。
    """
    clean_text = _strip_inline_markdown(_clean_latex(bold_text))
    if not clean_text:
        return

    # 只预留“这一行文字自己的高度”，不再固定预留 24 毫米。
    # 表格标题与图片的整体换页由 generate_pdf 在调用本函数前统一判断，
    # 避免这里和调用方各换一次页，产生只含标题的空白页。
    _ensure_space(
        pdf,
        _measure_bold_line_height(pdf, clean_text, font_family),
    )
    pdf.set_font(font_family, "B", _BODY_FONT_SIZE)
    pdf.set_text_color(0, 0, 0)
    pdf.multi_cell(
        0,
        _BODY_LINE_HEIGHT_MM,
        clean_text,
        align="L",
        wrapmode="CHAR",
        new_x="LMARGIN",
        new_y="NEXT",
    )
    pdf.ln(1.2)


def _write_paragraph(
    pdf: FPDF,
    paragraph_text: str,
    font_family: str,
    indent_mm: float = 0.0,
    first_line_indent: bool = False,
) -> None:
    """
    写入一个普通段落或列表项。

    参数：
        pdf: 当前 FPDF 对象。
        paragraph_text: 段落文字。
        font_family: 已注册的中文字体名。
        indent_mm: 整段左侧缩进（毫米），用于列表和引用。
        first_line_indent: 是否在段落开头加“两个全角空格”的首行缩进。

    说明：
        使用 multi_cell 自动换行；
        wrapmode="CHAR" 让中文在没有空格时也能逐字换行，
        不会出现一长串中文冲出页面右边缘的情况。

        首行缩进只加在普通正文段落上：
        - 列表项前面已经有“•”或“1.”，再加缩进会显得错位；
        - 标题、加粗字段、引用块也不加，保持它们自己的视觉层级。
    """
    clean_text = _strip_inline_markdown(_clean_latex(paragraph_text))
    if not clean_text:
        return

    # 首行缩进必须在清理 Markdown 之后再加，
    # 因为 _strip_inline_markdown 会去掉文字开头和结尾的空白字符；
    # 如果先加缩进，两个全角空格会被一起清掉，缩进就失效了。
    if first_line_indent:
        clean_text = _FIRST_LINE_INDENT + clean_text

    pdf.set_font(font_family, "", _BODY_FONT_SIZE)
    pdf.set_text_color(0, 0, 0)

    writing_width = pdf.epw - indent_mm
    if writing_width <= 10:
        # 缩进异常时退回整行宽度，保证文字仍然写得出。
        indent_mm = 0
        writing_width = pdf.epw

    if indent_mm:
        pdf.set_x(pdf.l_margin + indent_mm)

    pdf.multi_cell(
        writing_width,
        _BODY_LINE_HEIGHT_MM,
        clean_text,
        align="L",
        wrapmode="CHAR",
        new_x="LMARGIN",
        new_y="NEXT",
    )


def _write_quote(
    pdf: FPDF,
    quote_text: str,
    font_family: str,
) -> None:
    """
    写入 Markdown 引用块（以 > 开头的行）。

    参数：
        pdf: 当前 FPDF 对象。
        quote_text: 去掉 > 之后的引用文字。
        font_family: 已注册的中文字体名。

    说明：
        引用块常见于防幻觉声明和来源提示；
        用灰色、略小字号、左侧缩进区分，避免和正文混在一起。
    """
    clean_text = _strip_inline_markdown(_clean_latex(quote_text))
    if not clean_text:
        return

    pdf.set_font(font_family, "", _BODY_FONT_SIZE - 0.5)
    pdf.set_text_color(90, 90, 90)
    pdf.set_x(pdf.l_margin + 4)
    pdf.multi_cell(
        pdf.epw - 4,
        _BODY_LINE_HEIGHT_MM,
        clean_text,
        align="L",
        wrapmode="CHAR",
        new_x="LMARGIN",
        new_y="NEXT",
    )
    # 引用结束后恢复正文颜色，避免后面的段落继续变灰。
    pdf.set_text_color(0, 0, 0)


def _write_text_block(
    pdf: FPDF,
    block_text: str,
    font_family: str,
) -> None:
    """
    把一个文字块写入 PDF。

    参数：
        pdf: 当前 FPDF 对象。
        block_text: 不含 Markdown 表格的文字块。
        font_family: 已注册的中文字体名。

    说明：
        逐行识别标题、分隔线、加粗字段、列表、引用和普通段落；
        先清理 LaTeX 和 Markdown 标记，再调用对应的写入函数。
        这里刻意不使用 fpdf2 的 markdown=True：
        实测该参数在“中文 + 加粗”组合下会卡死，
        因此改为自己识别少量常用格式，稳定优先。

        排版上遵循“传统公文/研报”习惯：
        - 普通正文段落首行缩进两个全角空格；
        - 段落之间（遇到空行或文字块结束）留 5 毫米；
        - 每个列表项之后留 2 毫米，让分点更清楚；
        - 标题、列表项、加粗字段和引用不加首行缩进。
    """
    # _clean_latex 内部已经把三个以上连续换行压缩成两个（即一个空行）。
    # 这里再用正则兜底一次：万一其他调用路径传入了未压缩的文本，
    # 也绝不会在 PDF 里留下连续好几个空行的大片空白。
    cleaned_text = re.sub(r"\n{3,}", "\n\n", _clean_latex(block_text))
    if not cleaned_text.strip():
        return

    last_line_was_blank = False

    for raw_line in cleaned_text.splitlines():
        line = raw_line.strip()

        if not line:
            # 连续空行只保留一次间距，避免报告中间出现大片空白。
            # 5 毫米大约等于正文行高，视觉上就是“空了一行”。
            if not last_line_was_blank:
                pdf.ln(_PARAGRAPH_SPACING_MM)
                last_line_was_blank = True
            continue

        last_line_was_blank = False

        heading_match = _HEADING_RE.match(line)
        if heading_match:
            _write_heading(
                pdf,
                len(heading_match.group(1)),
                heading_match.group(2),
                font_family,
            )
            continue

        # 分隔线：---、***、___ 等单独成行。
        if re.fullmatch(r"[-*_]{3,}", line):
            _ensure_space(pdf, 8)
            pdf.set_draw_color(180, 180, 180)
            pdf.set_line_width(0.3)
            line_y = pdf.get_y() + 1.0
            pdf.line(pdf.l_margin, line_y, pdf.w - pdf.r_margin, line_y)
            pdf.ln(4)
            continue

        # 整行加粗：**表1：……** 或 **核心事实**。
        if (
            line.startswith("**")
            and line.endswith("**")
            and line.count("**") == 2
        ):
            _write_bold_line(pdf, line[2:-2], font_family)
            continue

        # 无序列表：-、*、+ 开头。
        bullet_match = re.match(r"^[-*+]\s+(.*)$", line)
        if bullet_match:
            _write_paragraph(
                pdf,
                "• " + bullet_match.group(1),
                font_family,
                indent_mm=3.0,
                # 列表项前面已经有“•”，不再加首行缩进，避免文字被推得太靠右。
                first_line_indent=False,
            )
            # 每个列表项之后额外留 2 毫米，让相邻两条不挤在一起。
            pdf.ln(_LIST_ITEM_SPACING_MM)
            continue

        # 有序列表：1.、2.、1) 等开头。
        numbered_match = re.match(r"^(\d+[.)])\s+(.*)$", line)
        if numbered_match:
            _write_paragraph(
                pdf,
                f"{numbered_match.group(1)} {numbered_match.group(2)}",
                font_family,
                indent_mm=3.0,
                # 序号本身已经起到缩进作用，不再加首行缩进。
                first_line_indent=False,
            )
            pdf.ln(_LIST_ITEM_SPACING_MM)
            continue

        # 引用块：> 开头。
        if line.startswith(">"):
            _write_quote(pdf, line.lstrip(">").strip(), font_family)
            continue

        # 普通正文段落：首行缩进两个全角空格。
        _write_paragraph(
            pdf,
            line,
            font_family,
            first_line_indent=True,
        )

    # 一个文字块结束，相当于长文本的自然段结束，统一留 5 毫米。
    # 下一个表格块或文字块会从这个位置继续写，不会和本段粘在一起。
    pdf.ln(_PARAGRAPH_SPACING_MM)


def _write_plain_table_fallback(
    pdf: FPDF,
    table_text: str,
    font_family: str,
) -> None:
    """
    表格图片生成失败时，把表格写成文字版。

    参数：
        pdf: 当前 FPDF 对象。
        table_text: 原始 Markdown 表格文本。
        font_family: 已注册的中文字体名。

    说明：
        文字版不如图片美观，但能保证表格内容不丢失；
        每行用“ | ”分隔，表头加粗，阅读时仍能分辨列。
    """
    parsed_table = _parse_markdown_table(table_text)

    if parsed_table is None:
        # 连表格都解析不出来时，至少把原文以小字写进去。
        plain_text = _strip_inline_markdown(_clean_latex(table_text))
        if plain_text:
            pdf.set_font(font_family, "", 8)
            pdf.set_text_color(80, 80, 80)
            pdf.multi_cell(
                0,
                4.5,
                plain_text,
                align="L",
                wrapmode="CHAR",
                new_x="LMARGIN",
                new_y="NEXT",
            )
        return

    headers, data_rows = parsed_table
    _ensure_space(pdf, 20)

    pdf.set_font(font_family, "", 8)
    pdf.set_text_color(120, 120, 120)
    pdf.multi_cell(
        0,
        4.2,
        "（表格图片生成失败，以下为表格文字版）",
        align="L",
        wrapmode="CHAR",
        new_x="LMARGIN",
        new_y="NEXT",
    )

    pdf.set_text_color(0, 0, 0)
    pdf.set_font(font_family, "B", 8.5)
    pdf.multi_cell(
        0,
        4.8,
        " | ".join(headers),
        align="L",
        wrapmode="CHAR",
        new_x="LMARGIN",
        new_y="NEXT",
    )

    pdf.set_font(font_family, "", 8.5)
    for row in data_rows:
        pdf.multi_cell(
            0,
            4.8,
            " | ".join(row),
            align="L",
            wrapmode="CHAR",
            new_x="LMARGIN",
            new_y="NEXT",
        )

    pdf.ln(2)


def _safe_write_table_fallback(
    pdf: FPDF,
    table_text: str,
    font_family: str,
) -> None:
    """
    安全地写入表格文字版，任何异常都不向外抛出。

    参数：
        pdf: 当前 FPDF 对象。
        table_text: 原始 Markdown 表格文本。
        font_family: 已注册的中文字体名。

    说明：
        这是 PDF 导出的最后一道保险：
        表格转图片失败已经很不幸，不能因为文字版也出错就让整个 PDF 失败。
    """
    try:
        _write_plain_table_fallback(pdf, table_text, font_family)
    except Exception:
        # 最后再尝试一次最简单的纯文本写入；仍然失败就跳过这个表格。
        try:
            plain_text = _strip_inline_markdown(_clean_latex(table_text))
            if plain_text:
                pdf.set_font(font_family, "", 8)
                pdf.set_text_color(80, 80, 80)
                pdf.multi_cell(
                    0,
                    4.5,
                    plain_text,
                    align="L",
                    wrapmode="CHAR",
                    new_x="LMARGIN",
                    new_y="NEXT",
                )
        except Exception:
            pass


def _safe_write_text_fallback(
    pdf: FPDF,
    text: str,
    font_family: str,
) -> None:
    """
    文字块排版失败时，用最简单的纯文本方式写入。

    参数：
        pdf: 当前 FPDF 对象。
        text: 原始文字块。
        font_family: 已注册的中文字体名。

    说明：
        某个特殊字符或异常格式可能让文字块排版失败；
        这时宁可丢失加粗和标题样式，也要保留文字内容。
    """
    try:
        plain_text = _strip_inline_markdown(_clean_latex(text))
        if not plain_text:
            return
        pdf.set_font(font_family, "", 9.5)
        pdf.set_text_color(0, 0, 0)
        pdf.multi_cell(
            0,
            5.2,
            plain_text,
            align="L",
            wrapmode="CHAR",
            new_x="LMARGIN",
            new_y="NEXT",
        )
    except Exception:
        # 如果连纯文本都写不进去，只能跳过这个文字块，
        # 但绝不能因此中断整份 PDF 的生成。
        pass


def _calculate_image_display_size(
    pdf: FPDF,
    image_path: str | Path,
    max_height_mm: float | None = None,
) -> tuple[float, float] | None:
    """
    计算表格图片在 PDF 中的显示宽高（毫米）。

    参数：
        pdf: 当前 FPDF 对象，用来读取页面尺寸和页边距。
        image_path: 表格 PNG 图片路径。
        max_height_mm: 图片允许占用的最大高度（毫米）。
                       传入“整页正文高度 - 表标题高度 - 间距”后，
                       标题和图片就一定能放在同一页；
                       不传时按整页正文高度计算。

    返回：
        (显示宽度, 显示高度)；图片损坏或尺寸异常时返回 None。

    说明：
        先按正文宽度铺满，再按原始宽高比计算高度；
        如果图片比允许的最大高度还高，再按最大高度等比缩小。
        这个函数只计算尺寸，不插入图片，
        方便排版前先判断“标题 + 图片”能否放在同一页。
    """
    try:
        # Pillow 是 fpdf2 的依赖，用来读取图片像素尺寸，计算等比缩放。
        from PIL import Image

        with Image.open(image_path) as image:
            pixel_width, pixel_height = image.size

        if pixel_width <= 0 or pixel_height <= 0:
            return None

        available_width = pdf.w - pdf.l_margin - pdf.r_margin
        available_height = (
            pdf.h - pdf.t_margin - _PAGE_BOTTOM_MARGIN_MM
        )
        if max_height_mm is not None:
            # 表格标题也要占高度，因此图片必须为标题让出空间。
            # max(0, ...) 防止调用方传入负数导致图片被算成 0 高。
            available_height = min(
                available_height,
                max(0.0, max_height_mm),
            )

        if available_width <= 0 or available_height <= 0:
            return None

        display_width = available_width
        display_height = display_width * pixel_height / pixel_width

        if display_height > available_height:
            scale = available_height / display_height
            display_width *= scale
            display_height = available_height

        return display_width, display_height

    except Exception:
        return None


def _insert_table_image(
    pdf: FPDF,
    image_path: str | Path,
    display_size: tuple[float, float] | None = None,
    *,
    allow_page_break: bool = True,
    gap_mm: float = 4.0,
) -> bool:
    """
    把表格图片按页面宽度等比缩放后居中插入 PDF。

    参数：
        pdf: 当前 FPDF 对象。
        image_path: 已经生成的 PNG 图片路径。
        display_size: 已经计算好的（显示宽度, 显示高度），单位为毫米。
                      传入它可以避免重复打开图片；不传时本函数自行计算。
        allow_page_break: 是否允许本函数在空间不足时换页。
                          调用方已经为“表标题 + 图片”预留空间时传 False，
                          防止标题刚写完又在这里换页，产生只含标题的空白页。
        gap_mm: 图片下方要留出的间距（毫米）。
                默认 4 毫米，保留给直接调用本函数的旧逻辑；
                generate_pdf 会显式传入 5 毫米，实现表格上下文留白。

    返回：
        bool: 插入成功返回 True；图片损坏、尺寸异常或完全没有空间返回 False。

    说明：
        - 始终使用“缩放后的真实高度”判断，而不是图片原始像素高度；
        - 最多只换一页；换页后仍放不下就等比缩小，绝不连续 add_page()；
        - 图片下方按 gap_mm 留出间距，并把 Y 更新到图片真实底部；
        - Y 不会超过页面底部安全线，避免后续内容被写到页面外面。
    """
    if display_size is None:
        display_size = _calculate_image_display_size(pdf, image_path)
    if display_size is None:
        return False

    display_width, display_height = display_size
    if display_width <= 0 or display_height <= 0:
        return False

    # 防御性处理：负数或非数字间距会让后续高度计算出错，
    # 统一退回到 0，至少保证图片本身能插入。
    try:
        gap_mm = max(0.0, float(gap_mm))
    except (TypeError, ValueError):
        gap_mm = 0.0

    try:
        available_width = pdf.w - pdf.l_margin - pdf.r_margin
        bottom_limit = _page_bottom_limit(pdf)
        current_y = pdf.get_y()
        remaining_height = max(0.0, bottom_limit - current_y)

        # 当前页放不下“图片 + 底部间距”时，先判断整页能否放下。
        if (
            remaining_height
            < display_height + gap_mm - _PAGE_BREAK_TOLERANCE_MM
        ):
            full_page_height = _full_page_content_height(pdf)
            fits_on_new_page = (
                display_height + gap_mm
                <= full_page_height + _PAGE_BREAK_TOLERANCE_MM
            )

            if allow_page_break and fits_on_new_page:
                # 只在这里换页一次。换页后重新读取 Y，
                # 后续所有尺寸计算都基于新页面的真实剩余高度。
                pdf.add_page()
                current_y = pdf.get_y()
                bottom_limit = _page_bottom_limit(pdf)
                remaining_height = max(0.0, bottom_limit - current_y)

        # 换页后（或调用方不允许换页时）仍然放不下，
        # 就按当前真实剩余高度等比缩小，绝不再调用 add_page()。
        usable_height = max(0.0, remaining_height - gap_mm)
        if (
            display_height
            > usable_height + _PAGE_BREAK_TOLERANCE_MM
        ):
            # 剩余空间小到连一条表格线都放不下时，宁可让调用方走文字兜底，
            # 也不把图片压缩成看不清的细条。
            if usable_height < 10.0:
                return False
            scale = usable_height / display_height
            display_width *= scale
            display_height = usable_height

        if display_width <= 0 or display_height <= 0:
            return False

        # 水平居中：在左右页边距之间居中。
        image_x = pdf.l_margin + (available_width - display_width) / 2
        pdf.image(
            str(image_path),
            x=image_x,
            y=current_y,
            w=display_width,
            h=display_height,
        )

        # 用“实际显示的图片高度”更新 Y，而不是图片原始高度。
        # 如果图片刚好铺到页面底部，Y 停在安全线，避免 set_y 超过页面；
        # 下一段内容写入前的 _ensure_space 会正常换页。
        next_y = current_y + display_height + gap_mm
        pdf.set_y(min(next_y, bottom_limit))
        return True

    except Exception as exc:
        # 只记录异常类型，不记录图片路径或表格内容。
        logger.warning(
            "PDF 插入表格图片失败（%s）。",
            type(exc).__name__,
        )
        return False


def _write_document_title(
    pdf: FPDF,
    company_name: str,
    font_family: str,
) -> None:
    """
    在 PDF 第一页顶部写入报告标题和系统名称。

    参数：
        pdf: 当前 FPDF 对象。
        company_name: 公司名称；为空时使用通用标题。
        font_family: 已注册的中文字体名。

    说明：
        - 有公司名时显示“XX公司 投研初筛报告”；
        - 没有公司名时显示“投研初筛报告”；
        - 标题下方加一条黑色横线，和正文分开。
    """
    company_name = company_name.strip() if isinstance(company_name, str) else ""
    title = (
        f"{company_name} 投研初筛报告"
        if company_name
        else "投研初筛报告"
    )

    pdf.set_font(font_family, "B", 17)
    pdf.set_text_color(0, 0, 0)
    pdf.multi_cell(
        0,
        9,
        title,
        align="C",
        wrapmode="CHAR",
        new_x="LMARGIN",
        new_y="NEXT",
    )
    pdf.ln(1.5)

    pdf.set_font(font_family, "", 9)
    pdf.set_text_color(100, 100, 100)
    pdf.cell(
        0,
        5,
        "财务投研初筛多智能体系统",
        align="C",
        new_x="LMARGIN",
        new_y="NEXT",
    )

    pdf.set_text_color(0, 0, 0)
    pdf.ln(3)
    pdf.set_draw_color(0, 0, 0)
    pdf.set_line_width(0.5)
    line_y = pdf.get_y()
    pdf.line(pdf.l_margin, line_y, pdf.w - pdf.r_margin, line_y)
    pdf.ln(5)


def generate_pdf(
    markdown_text: str,
    company_name: str = "",
) -> bytes:
    """
    把 Markdown 报告转换成 PDF 字节流。

    参数：
        markdown_text: 专家返回的 Markdown 报告，必须是非空字符串。
        company_name: 公司名称，用于 PDF 顶部标题；可以为空字符串。

    返回：
        bytes: 完整的 PDF 文件内容，可直接交给 Streamlit 的下载按钮。

    异常：
        PdfGenerationError: 报告为空、找不到中文字体、
                            或 PDF 底层排版失败时抛出。
                            异常文字为中文，可直接展示给用户。

    说明：
        - A4 纸、15 毫米左右页边距，适合打印和屏幕阅读；
        - 文字用 multi_cell 自动换行，行距比默认更宽松；
        - 普通正文段落首行缩进两个全角空格，段间留 5 毫米；
        - 列表项不缩进，每个列表项之后留 2 毫米；
        - Markdown 标题符号会被清理，不会把 # 号印进 PDF；
        - Markdown 表格转成三线表图片后插入，图片上下各留 5 毫米；
        - 连续多个空行会压缩成一个空行，避免出现大片空白；
        - 表格转图片失败时自动退回文字版表格；
        - 纯空格、纯换行的文本块会被跳过，不占用版面；
        - “表标题 + 表格图片”作为整体只做一次分页判断，
          连续长表格不会反复 add_page() 产生空白页；
        - 表格超过一整页时按行拆成多段，每段重复表头，
          后续页标题写成“原表名（续表）”，不会把整张表压小；
        - 所有临时图片放在系统临时目录，生成结束后自动删除；
        - 只返回内存中的字节，不把 PDF 永久写入磁盘。
    """
    # 先做输入校验，避免把空内容交给排版库后出现难懂的内部错误。
    if not isinstance(markdown_text, str) or not markdown_text.strip():
        raise PdfGenerationError("报告内容为空，无法生成 PDF。")

    if not isinstance(company_name, str):
        company_name = ""

    try:
        # 第一步：把报告切成文字块和表格块。
        blocks = _split_markdown_blocks(markdown_text)

        # 第二步：创建 A4 尺寸的 PDF。
        pdf = _DueDiligencePDF(unit="mm", format="A4")

        # 第三步：注册中文字体。必须放在写任何文字之前，
        # 否则 fpdf2 会使用默认字体，中文会显示成方块或乱码。
        font_family = _register_chinese_font(pdf)
        pdf.cn_font_family = font_family

        # 自动分页：距离底部 18 毫米时换页，避免文字压到页脚。
        pdf.set_auto_page_break(
            auto=True,
            margin=_PAGE_BOTTOM_MARGIN_MM,
        )
        pdf.set_margins(15, 15, 15)

        # 写入 PDF 元数据，阅读器标题栏会显示报告名称。
        pdf.set_title(
            f"{company_name.strip()} 投研初筛报告"
            if company_name.strip()
            else "投研初筛报告"
        )
        pdf.set_author("财务投研初筛多智能体系统")
        pdf.set_creator("财务投研初筛多智能体系统")

        # 添加第一页并写报告大标题。
        pdf.add_page()
        _write_document_title(pdf, company_name, font_family)

        # 临时目录只存放表格 PNG，生成结束（或出错）后统一删除。
        # 使用 mkdtemp + rmtree(ignore_errors=True)，
        # 即使 Windows 上某个图片句柄还没释放，也不会让清理失败影响 PDF 返回。
        temp_dir = tempfile.mkdtemp(prefix="dd_agent_pdf_")
        try:
            table_index = 0

            for block_type, block_text in blocks:
                # 纯空白块（连续回车、空格、制表符）不占任何版面，直接跳过。
                # 否则每次排版都会消耗行高，多段空白累积起来会把后面的内容
                # 顶到下一页，形成“开头一大片空白”的观感。
                if not block_text.strip():
                    continue

                if block_type != "table":
                    # 文字块排版失败时退回纯文本，保留内容。
                    try:
                        _write_text_block(pdf, block_text, font_family)
                    except Exception:
                        _safe_write_text_fallback(
                            pdf,
                            block_text,
                            font_family,
                        )
                    continue

                # 表格块里可能夹着一行表标题（切分函数会把标题绑到表格上）。
                # 这里把它拆出来：标题先写，图片后放，二者作为整体换页。
                table_title_lines: list[str] = []
                table_content_lines: list[str] = []
                for line in block_text.splitlines():
                    if _TABLE_ROW_RE.match(line):
                        table_content_lines.append(line)
                    elif line.strip():
                        table_title_lines.append(line)

                table_title = "\n".join(table_title_lines).strip()
                table_content = "\n".join(table_content_lines)

                # 只有标题、没有表格行：按普通加粗小标题处理，
                # 不生成空表格图片，也不占用图片预留空间。
                if not table_content:
                    if table_title:
                        try:
                            _write_bold_line(
                                pdf,
                                table_title,
                                font_family,
                            )
                        except Exception:
                            _safe_write_text_fallback(
                                pdf,
                                table_title,
                                font_family,
                            )
                    continue

                # 先量出原表标题和“（续表）”标题的真实高度。
                # 两个标题可能换行成不同行数，分开量才能精确预留空间。
                title_height = 0.0
                if table_title:
                    title_height = _measure_bold_line_height(
                        pdf,
                        table_title,
                        font_family,
                    )

                # 续表标题统一写成“原表名（续表）”。
                # 先清掉 Markdown 标记，避免拼出 **表1**（续表）这种半成品。
                continuation_title = (
                    f"{_strip_inline_markdown(_clean_latex(table_title))}"
                    "（续表）"
                    if table_title
                    else "（续表）"
                )
                continuation_height = _measure_bold_line_height(
                    pdf,
                    continuation_title,
                    font_family,
                )

                # 第一页有原表标题，续表页有“（续表）”标题，
                # 分别计算两页最多能给表格图片留多少高度。
                # 图片上下各留 5 毫米，因此从整页高度里扣除 10 毫米；
                # 这样“标题 + 上方留白 + 图片 + 下方留白”一定能放进一页。
                table_context_spacing = _TABLE_CONTEXT_SPACING_MM
                first_page_max_height = max(
                    0.0,
                    _full_page_content_height(pdf)
                    - title_height
                    - table_context_spacing * 2,
                )
                continuation_page_max_height = max(
                    0.0,
                    _full_page_content_height(pdf)
                    - continuation_height
                    - table_context_spacing * 2,
                )

                content_width_mm = (
                    pdf.w - pdf.l_margin - pdf.r_margin
                )

                # 当前页还能给表格图片留多少高度
                # （已扣除表标题、图片上方 5 毫米和图片下方 5 毫米）。
                current_page_available = max(
                    0.0,
                    _get_remaining_height(pdf)
                    - title_height
                    - table_context_spacing * 2,
                )

                # 默认第一段按“一整页减去原表标题”来排。
                first_chunk_max_height = first_page_max_height

                if (
                    current_page_available
                    >= _MIN_TABLE_FRAGMENT_HEIGHT_MM
                ):
                    # 当前页还有足够空间，就从当前页开始排第一段，
                    # 避免报告开头出现“只有标题、下面一大片空白”的页面。
                    # 和整页高度取较小值，保证一段不会超过一页。
                    first_chunk_max_height = min(
                        current_page_available,
                        first_page_max_height
                        + _PAGE_BREAK_TOLERANCE_MM,
                    )
                elif current_page_available > 0:
                    # 当前页只剩一小块空间：
                    # 整张表能塞进去就接着排，塞不进去就整表另起一页，
                    # 避免只放两三行表格就断开的零碎感。
                    fits_current_page = _table_fits_within_height(
                        table_content,
                        current_page_available,
                        content_width_mm,
                    )
                    if fits_current_page:
                        first_chunk_max_height = (
                            current_page_available
                        )
                    else:
                        _ensure_space(
                            pdf,
                            _full_page_content_height(pdf),
                        )
                        first_chunk_max_height = (
                            first_page_max_height
                        )

                table_index += 1
                if (
                    first_page_max_height <= 10.0
                    or continuation_page_max_height <= 10.0
                ):
                    # 页面高度异常小，图片没有可用空间，直接走文字兜底。
                    if table_title:
                        try:
                            _write_bold_line(
                                pdf,
                                table_title,
                                font_family,
                            )
                        except Exception:
                            _safe_write_text_fallback(
                                pdf,
                                table_title,
                                font_family,
                            )
                    _safe_write_table_fallback(
                        pdf,
                        table_content,
                        font_family,
                    )
                    continue

                # 按页面高度把表格切成多段图片。
                # 如果整张表放得下一页，这里只会返回一张图片；
                # 只有超过一页时才会拆成“首段 + 续表段”。
                image_paths = markdown_table_to_images(
                    table_content,
                    temp_dir,
                    f"table_{table_index}",
                    max_height_mm=continuation_page_max_height,
                    display_width_mm=content_width_mm,
                    first_chunk_max_height_mm=first_chunk_max_height,
                )

                # 先检查所有分段图片都能读出尺寸，
                # 避免插了一半才发现某张图片损坏。
                segment_sizes: list[tuple[float, float]] = []
                for segment_index, image_path in enumerate(image_paths):
                    segment_max_height = (
                        first_chunk_max_height
                        if segment_index == 0
                        else continuation_page_max_height
                    )
                    display_size = _calculate_image_display_size(
                        pdf,
                        image_path,
                        max_height_mm=segment_max_height,
                    )
                    if display_size is None:
                        segment_sizes = []
                        break
                    segment_sizes.append(display_size)

                if not image_paths or not segment_sizes:
                    # 表格转图片失败：用文字版兜底，绝不让整份 PDF 失败。
                    # 如果前面拆出了标题，先写标题，保留上下文。
                    if table_title:
                        try:
                            _write_bold_line(
                                pdf,
                                table_title,
                                font_family,
                            )
                        except Exception:
                            _safe_write_text_fallback(
                                pdf,
                                table_title,
                                font_family,
                            )
                    _safe_write_table_fallback(
                        pdf,
                        table_content,
                        font_family,
                    )
                    continue

                for segment_index, (
                    image_path,
                    display_size,
                ) in enumerate(zip(image_paths, segment_sizes)):
                    is_first_segment = segment_index == 0
                    segment_title = (
                        table_title
                        if is_first_segment
                        else continuation_title
                    )
                    segment_title_height = (
                        title_height
                        if is_first_segment
                        else continuation_height
                    )

                    if not is_first_segment:
                        # 续表从新的一页开始，视觉上和上一页的分段接上。
                        # 如果当前已经是空白新页，_ensure_space 不会重复换页，
                        # 因此不会因为这里多插一页而产生空白页。
                        _ensure_space(
                            pdf,
                            _full_page_content_height(pdf),
                        )

                    # 只做一次分页判断：
                    # 预留“标题 + 图片上方 5 毫米 + 缩放后图片 + 图片下方 5 毫米”。
                    _ensure_space(
                        pdf,
                        segment_title_height
                        + table_context_spacing
                        + display_size[1]
                        + table_context_spacing,
                    )

                    if segment_title:
                        try:
                            _write_bold_line(
                                pdf,
                                segment_title,
                                font_family,
                            )
                        except Exception:
                            _safe_write_text_fallback(
                                pdf,
                                segment_title,
                                font_family,
                            )

                    # 图片上方留 5 毫米，避免表格粗线紧贴表标题或上一段正文。
                    pdf.ln(table_context_spacing)

                    # allow_page_break=False：标题已经写完，这里绝不能再换页，
                    # 否则会出现“只有标题的空白页”。
                    # gap_mm=5：图片下方同样留 5 毫米，和下一段正文隔开。
                    inserted = _insert_table_image(
                        pdf,
                        image_path,
                        display_size=display_size,
                        allow_page_break=False,
                        gap_mm=table_context_spacing,
                    )
                    if not inserted:
                        # 极少数情况下图片插入失败，退回文字版，保证内容不丢。
                        _safe_write_table_fallback(
                            pdf,
                            table_content,
                            font_family,
                        )
                        break

            # 在临时目录还存在时完成输出，确保图片已经嵌入 PDF。
            pdf_bytes = bytes(pdf.output())
        finally:
            # ignore_errors=True 保证清理临时图片时不会抛出异常。
            shutil.rmtree(temp_dir, ignore_errors=True)

        return pdf_bytes

    except PdfGenerationError:
        # 这是本模块自己抛出的中文提示，直接向上传递。
        raise

    except Exception as exc:
        # 底层库的报错可能是英文或包含技术细节，这里统一翻译成中文提示；
        # 原始异常通过 from exc 保留在控制台，方便以后排查。
        raise PdfGenerationError(
            "生成 PDF 失败，请稍后重试；"
            "如果反复失败，请缩短报告内容后再试。"
        ) from exc
