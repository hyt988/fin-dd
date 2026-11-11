"""
巨潮资讯网公告查询与年报 PDF 下载客户端。

这个模块只做三件事：
1. 把用户输入的股票代码整理成 6 位数字，并判断是沪市还是深市；
2. 调用巨潮资讯网公开的公告查询接口，找到指定年份的年度报告；
3. 从巨潮官方静态地址下载年报 PDF，并做大小、文件头和安全检查。

重要说明：
- 巨潮没有公开承诺长期稳定的 API，本模块使用的是网页背后查询公告的接口。
  如果接口参数以后发生变化，只需要集中修改本文件，不影响页面和解析模块。
- 本模块不读取、不保存任何 API Key；巨潮公告是公开数据，不需要密钥。
- 所有网络请求都只允许访问巨潮官方域名，防止用户输入被利用去访问内网。
- 这里只负责“找到并下载”，不负责解析 PDF 内容；
  PDF 解析在 utils/annual_report_parser.py 中完成。
"""

from __future__ import annotations

import html
import json
import re
import time
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

# ---------------------------------------------------------------------------
# 巨潮接口地址和访问限制
# ---------------------------------------------------------------------------

# 公告查询接口。使用 POST 表单提交查询条件。
CNINFO_QUERY_URL = "https://www.cninfo.com.cn/new/hisAnnouncement/query"

# 巨潮公告 PDF 的静态文件根地址。
# 接口返回的 adjunctUrl 通常是 "finalpage/2026-04-17/xxxx.PDF" 这样的相对路径，
# 需要拼到这个地址后面才能下载。
CNINFO_STATIC_BASE = "https://static.cninfo.com.cn/"

# 巨潮首页，用作下载 PDF 时的 Referer，表示请求来自巨潮站内。
CNINFO_HOME_URL = "https://www.cninfo.com.cn/"

# 公告查询页地址，作为查询接口的 Referer。
CNINFO_REFERER = (
    "https://www.cninfo.com.cn/new/commonUrl/pageOfSearch"
    "?url=disclosure/list/search"
)

# 使用普通浏览器标识访问公开页面，不伪装成登录用户。
CNINFO_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36"
)

# 单次网络请求超时时间（秒）。
CNINFO_TIMEOUT_SECONDS = 15

# 公告查询接口返回的 JSON 最多读取多少字节。
# 正常情况下只有几十 KB，设置上限是为了防止异常响应占满内存。
CNINFO_MAX_RESPONSE_BYTES = 2_000_000

# 年报 PDF 最大允许下载大小（50 MB）。
# 年报通常 1～10 MB；超过 50 MB 的文件解析会非常慢，也容易拖垮页面。
CNINFO_MAX_PDF_BYTES = 50_000_000

# 巨潮公告列表每页最多返回约 30 条，传更大的数字也可能被服务器压回 30。
CNINFO_PAGE_SIZE = 30

# 查询一个年份的公告时最多翻多少页。
# 年报通常在 3～5 月披露，前几页就能找到；设置上限避免异常情况下无限翻页。
CNINFO_MAX_PAGES = 4

# 两次巨潮请求之间的最小间隔（秒）。
# 这是为了保护对方网站，避免用户连续点击时产生高频请求。
CNINFO_MIN_INTERVAL_SECONDS = 1.5

# 只允许访问这两个巨潮官方域名。
# 即使公告数据被篡改，也不能让程序去访问其他网站或内网地址。
ALLOWED_CNINFO_HOSTS = frozenset(
    {
        "www.cninfo.com.cn",
        "static.cninfo.com.cn",
    }
)

# 中国标准时间（北京时间）。巨潮的公告时间按北京时间显示。
CHINA_TIMEZONE = timezone(timedelta(hours=8))


class CninfoError(RuntimeError):
    """
    巨潮公告查询或 PDF 下载失败时抛出的中文异常。

    异常文字会直接展示给用户，因此只写“发生了什么、下一步怎么做”，
    不包含内部堆栈、Cookie、API Key 等敏感信息。
    """


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StockIdentity:
    """
    一只股票在巨潮查询接口中需要的身份信息。

    属性：
        code: 6 位股票代码，例如 "600519"。
        name: 公司简称，例如 "贵州茅台"；第一次查询前可能为空。
        org_id: 巨潮内部编号，例如 "gssh0600519"。
        column: 巨潮市场参数，沪市为 "sse"，深市为 "szse"。
        exchange: 给用户看的市场名称，例如 "沪市"、"深市"。
    """

    code: str
    name: str
    org_id: str
    column: str
    exchange: str


@dataclass
class Announcement:
    """
    一条巨潮公告的整理结果。

    属性：
        title: 清理掉 HTML 标签后的公告标题。
        announcement_time: 公告时间（北京时间）。
        pdf_url: 可以直接下载的巨潮 PDF 地址。
        sec_name: 公司简称。
        sec_code: 公司股票代码。
        raw_id: 巨潮公告编号，仅用于调试和去重。
        is_summary: 是否是“年度报告摘要”。
        is_english: 是否是英文版。
        is_corrected: 是否是更正后/修订版。
        is_full_text: 是否是完整年报正文。
        is_audit_report: 是否是单独的审计报告。
        is_notice: 是否是提示性公告、更正公告等非年报文件。
        score: 选择年报时的优先级分数，越大越优先。
    """

    title: str
    announcement_time: datetime
    pdf_url: str
    sec_name: str = ""
    sec_code: str = ""
    raw_id: str = ""
    is_summary: bool = False
    is_english: bool = False
    is_corrected: bool = False
    is_full_text: bool = False
    is_audit_report: bool = False
    is_notice: bool = False
    score: int = 0


@dataclass
class AnnualReportSelection:
    """
    “为某只股票选出一份年度报告”的结果。

    属性：
        stock: 股票身份信息，包含查询后得到的公司简称。
        requested_year: 用户想查的会计年度。
        report_year: 实际找到的会计年度；可能因为当年未披露而向前回退。
        announcement: 最终选中的公告。
        alternatives: 同一年的其他候选（摘要、英文版、旧版本等）。
        warning: 给用户看的中文提示，例如“已改用上一年度报告”。
    """

    stock: StockIdentity
    requested_year: int
    report_year: int
    announcement: Announcement
    alternatives: list[Announcement] = field(default_factory=list)
    warning: str = ""


# ---------------------------------------------------------------------------
# 股票代码与市场判断
# ---------------------------------------------------------------------------


def normalize_stock_code(raw_code: str | int) -> str:
    """
    把用户输入的股票代码整理成 6 位数字。

    参数：
        raw_code: 用户输入，允许 "600519"、"SH600519"、
                  "600519.SH"、"600519.SS" 等常见写法。

    返回：
        str: 6 位股票代码。

    异常：
        CninfoError: 输入为空、不是数字或不是 6 位时抛出。

    说明：
        用户可能从不同网站复制代码，写法不统一。
        先统一成 6 位数字，后面判断市场和拼巨潮内部编号才不会出错。
    """
    if isinstance(raw_code, int):
        # 允许调用方直接传整数；整数没有前导零，统一补成 6 位。
        raw_code = f"{raw_code:06d}"

    if not isinstance(raw_code, str):
        raise CninfoError("股票代码必须是 6 位数字，例如 600519。")

    code = raw_code.strip().upper().replace(" ", "")

    # 去掉 SH / SZ / BJ 前缀，例如 SH600519。
    code = re.sub(r"^(SH|SZ|BJ)", "", code)

    # 去掉 .SH / .SZ / .SS / .BJ 后缀，例如 600519.SH。
    code = re.sub(r"\.(SH|SZ|SS|BJ)$", "", code)

    # 去掉可能带进来的短横线或下划线。
    code = code.strip("-_")

    if not re.fullmatch(r"\d{6}", code):
        raise CninfoError(
            "股票代码应为 6 位数字，例如 600519；"
            "也支持 SH600519、600519.SH 这类写法。"
        )

    return code


def infer_exchange(code: str) -> tuple[str, str]:
    """
    根据 6 位代码判断市场和巨潮查询参数。

    参数：
        code: 已整理好的 6 位股票代码。

    返回：
        tuple[str, str]: (市场参数, 给用户看的市场名称)。
                         例如 ("sse", "沪市") 或 ("szse", "深市")。

    异常：
        CninfoError: 北交所、B 股、基金、债券等首期不支持的代码。

    说明：
        首期按推荐方案只支持沪深 A 股：
        - 沪市 A 股：6 开头（含 600/601/603/605/688 等）；
        - 深市 A 股：0 或 3 开头（含 000/001/002/003/300/301 等）。
        4/8 开头多为北交所，2 开头多为深市 B 股，9 开头多为沪市 B 股，
        1/5 开头多为基金或债券，这些暂时明确提示不支持，避免查错。
    """
    if not isinstance(code, str) or not re.fullmatch(r"\d{6}", code):
        raise CninfoError("股票代码必须是 6 位数字，例如 600519。")

    if code.startswith("6"):
        return "sse", "沪市"

    if code.startswith(("0", "3")):
        return "szse", "深市"

    if code.startswith(("4", "8")):
        raise CninfoError(
            "首期暂不支持北交所股票（代码 4/8 开头）。"
            "目前支持沪深 A 股，例如 600519、000888。"
        )

    if code.startswith("2"):
        raise CninfoError(
            "暂不支持深市 B 股（代码 2 开头）。"
            "目前支持沪深 A 股，例如 600519、000888。"
        )

    if code.startswith("9"):
        raise CninfoError(
            "暂不支持沪市 B 股（代码 9 开头）。"
            "目前支持沪深 A 股，例如 600519、000888。"
        )

    if code.startswith(("1", "5")):
        raise CninfoError(
            "暂不支持基金、债券等代码。"
            "目前只支持沪深 A 股股票，例如 600519、000888。"
        )

    raise CninfoError(
        "暂不支持该股票代码。目前支持沪深 A 股，例如 600519、000888。"
    )


def _org_id_for(code: str, column: str) -> str:
    """
    根据股票代码和市场参数，拼出巨潮内部编号（orgId）。

    参数：
        code: 6 位股票代码。
        column: "sse"（沪市）或 "szse"（深市）。

    返回：
        str: 巨潮内部编号。

    说明：
        巨潮查询公告时，stock 参数必须是“股票代码,内部编号”。
        如果只传股票代码，接口不会按公司过滤，而是返回全市场最新公告。
        目前实测的规律是：
        - 沪市：gssh0 + 6 位代码，例如 600519 → gssh0600519；
        - 深市：gssz0 + 6 位代码，例如 000888 → gssz0000888。
        这是网页接口的内部编号，不是官方公开标准；如果以后变化，
        查询结果会按 secCode 校验，校验失败时再走全文搜索兜底。
    """
    if column == "sse":
        return f"gssh0{code}"

    if column == "szse":
        return f"gssz0{code}"

    raise CninfoError("无法判断该股票属于沪市还是深市。")


# ---------------------------------------------------------------------------
# URL 与文本清理
# ---------------------------------------------------------------------------


def _is_allowed_cninfo_url(url: str) -> bool:
    """
    判断地址是否是允许访问的巨潮官方 HTTPS 地址。

    参数：
        url: 待检查的 URL。

    返回：
        bool: 域名属于巨潮官方、使用 HTTPS、没有用户名密码时返回 True。

    安全说明：
        公告数据来自外部网站，不能直接信任其中的链接。
        只允许访问巨潮官方域名，可以防止恶意链接把程序引向内网
        （这类风险通常称为 SSRF：让服务器替攻击者访问内部系统）。
    """
    if not isinstance(url, str) or not url.strip():
        return False

    try:
        parsed = urlparse(url.strip())
    except ValueError:
        return False

    if parsed.scheme != "https":
        return False

    if parsed.username or parsed.password:
        return False

    host = (parsed.hostname or "").lower()
    if host not in ALLOWED_CNINFO_HOSTS:
        return False

    try:
        port = parsed.port
    except ValueError:
        return False

    # 巨潮官方地址使用标准 443 端口；显式写其他端口一律拒绝。
    return port in (None, 443)


class _CninfoRedirectHandler(HTTPRedirectHandler):
    """
    只允许跳转到巨潮官方域名的重定向处理器。

    默认的 urllib 会自动跟随 301/302。如果第一次请求是巨潮地址，
    但服务器把跳转目标指向其他网站或内网，就可能绕过域名检查。
    这里在每次跳转前再次检查目标地址。
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        """在跟随重定向前检查新地址；不安全时抛出 URLError。"""
        # 服务器可能返回相对跳转地址（例如 /finalpage/a.PDF）。
        # 先按当前请求地址补全，再做域名检查；否则合法的相对跳转
        # 会被误判成“非巨潮地址”，导致正常下载失败。
        target_url = urljoin(req.full_url, newurl)
        if not _is_allowed_cninfo_url(target_url):
            raise URLError("重定向目标不是巨潮官方地址，已拒绝访问。")
        return super().redirect_request(
            req,
            fp,
            code,
            msg,
            headers,
            newurl,
        )


def _open_request(request: Request, timeout: int):
    """
    使用带安全重定向检查的 opener 打开请求。

    参数：
        request: 已经构造好的 urllib Request。
        timeout: 超时秒数。

    返回：
        响应对象（支持 with 和 read）。

    说明：
        单独封装成函数，方便离线测试时替换成假响应，
        也避免每个请求都重复创建 opener。
    """
    opener = build_opener(_CninfoRedirectHandler())
    return opener.open(request, timeout=timeout)


def _clean_external_text(value: Any, max_chars: int = 500) -> str:
    """
    清理从巨潮接口取回的文本。

    参数：
        value: 可能是字符串、数字或 None。
        max_chars: 最多保留多少个字符。

    返回：
        str: 去掉 HTML 标签、反转义实体、压缩空白后的文本。

    说明：
        公告标题里可能带 <em> 高亮标签，也可能带 &amp; 这类 HTML 实体。
        这些内容如果不清理，会直接显示在页面上，影响阅读。
    """
    if value is None:
        return ""

    text = html.unescape(str(value))
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_chars]


def _build_pdf_url(adjunct_url: str) -> str:
    """
    把公告接口返回的相对路径拼成完整的巨潮 PDF 地址。

    参数：
        adjunct_url: 接口中的 adjunctUrl 字段。

    返回：
        str: 完整 HTTPS 地址；地址不合法时返回空字符串。

    说明：
        - 相对路径要拼到 https://static.cninfo.com.cn/ 后面；
        - 少数接口可能返回 http 地址，这里统一升级为 https；
        - 拼好后再次校验域名，防止被诱导到其他网站。
    """
    clean_url = _clean_external_text(adjunct_url, max_chars=1000)
    if not clean_url:
        return ""

    if clean_url.startswith("//"):
        clean_url = "https:" + clean_url
    elif clean_url.startswith("http://"):
        clean_url = "https://" + clean_url[len("http://"):]

    if clean_url.startswith("https://"):
        final_url = clean_url
    else:
        final_url = urljoin(CNINFO_STATIC_BASE, clean_url.lstrip("/"))

    if not _is_allowed_cninfo_url(final_url):
        return ""

    return final_url


# ---------------------------------------------------------------------------
# 公告查询
# ---------------------------------------------------------------------------


# 记录上一次请求时间，用于限制请求频率。
_last_request_at = 0.0


def _throttle() -> None:
    """
    保证两次巨潮请求之间至少间隔一段时间。

    说明：
        用户连续点击或程序翻页时，请求可能非常密集。
        加一个很小的等待，可以降低被对方限制访问的概率。
        离线测试会把这个函数替换成空函数，避免测试变慢。
    """
    global _last_request_at

    now = time.monotonic()
    wait_seconds = CNINFO_MIN_INTERVAL_SECONDS - (now - _last_request_at)
    if wait_seconds > 0:
        time.sleep(wait_seconds)
    _last_request_at = time.monotonic()


def _notify(callback: Callable[[str], None] | None, message: str) -> None:
    """
    安全地调用进度回调。

    参数：
        callback: 页面传入的进度回调；可以为 None。
        message: 要显示给用户的中文进度说明。

    说明：
        即使页面回调本身出错，也不应该中断年报查询，
        因此这里吞掉回调异常，只保留主流程。
    """
    if callback is None:
        return
    try:
        callback(message)
    except Exception:
        pass


def _http_error_message(status_code: int, action: str) -> str:
    """
    把 HTTP 状态码翻译成用户能理解的中文提示。

    参数：
        status_code: 服务器返回的状态码。
        action: 当前动作，例如“查询公告”或“下载年报”。

    返回：
        str: 中文错误说明。
    """
    if status_code == 403:
        return f"巨潮拒绝了本次{action}请求，请稍后重试。"
    if status_code == 404:
        return f"巨潮没有找到对应的{action}文件，可能已下架。"
    if status_code == 429:
        return f"巨潮访问过于频繁，请等待约 1 分钟后再试。"
    if 500 <= status_code < 600:
        return f"巨潮服务暂时不可用（{action}），请稍后重试。"
    return f"巨潮{action}失败（HTTP {status_code}），请稍后重试。"


def _request_json(form: dict[str, str]) -> dict:
    """
    向巨潮公告查询接口提交表单并解析 JSON。

    参数：
        form: 查询参数，例如 pageNum、stock、seDate 等。

    返回：
        dict: 接口返回的 JSON 对象。

    异常：
        CninfoError: 网络失败、状态码异常、返回内容不是 JSON 时抛出。

    说明：
        - 使用 POST 表单，和浏览器页面查询公告时的方式一致；
        - 只重试一次，避免网络持续故障时让用户等待过久；
        - 4xx 错误一般不重试（429 除外），因为参数问题重试也不会成功。
    """
    _throttle()

    data = urlencode(form).encode("utf-8")
    request = Request(
        CNINFO_QUERY_URL,
        data=data,
        headers={
            "User-Agent": CNINFO_USER_AGENT,
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Origin": "https://www.cninfo.com.cn",
            "Referer": CNINFO_REFERER,
            "X-Requested-With": "XMLHttpRequest",
        },
        method="POST",
    )

    last_error: Exception | None = None

    for attempt in range(2):
        try:
            with _open_request(
                request,
                timeout=CNINFO_TIMEOUT_SECONDS,
            ) as response:
                status = getattr(response, "status", 200)
                if status < 200 or status >= 300:
                    raise CninfoError(
                        _http_error_message(status, "查询公告")
                    )

                raw_bytes = response.read(
                    CNINFO_MAX_RESPONSE_BYTES + 1
                )
                if len(raw_bytes) > CNINFO_MAX_RESPONSE_BYTES:
                    raise CninfoError(
                        "巨潮返回的公告数据异常过大，已停止读取。"
                    )

                text = raw_bytes.decode("utf-8", errors="replace")
                try:
                    payload = json.loads(text)
                except json.JSONDecodeError as exc:
                    raise CninfoError(
                        "巨潮返回的内容不是有效 JSON，"
                        "可能是接口暂时变化或被访问限制。"
                    ) from exc

                if not isinstance(payload, dict):
                    raise CninfoError("巨潮返回的公告数据格式异常。")

                return payload

        except HTTPError as exc:
            # 把状态码翻译成中文后保存下来；500/429 在第二次失败时
            # 仍然把这条具体提示抛给用户，而不是变成笼统的“网络失败”。
            error = CninfoError(
                _http_error_message(exc.code, "查询公告")
            )
            last_error = error
            if 400 <= exc.code < 500 and exc.code != 429:
                raise error from exc
        except CninfoError as exc:
            last_error = exc
            # 数据格式错误、响应过大等问题重试也没有意义，直接向上传递。
            if "格式异常" in str(exc) or "不是有效 JSON" in str(exc):
                raise
        except (URLError, TimeoutError, OSError) as exc:
            last_error = exc

        # 第一次失败后短暂等待，再试一次。
        if attempt == 0:
            time.sleep(1.0)

    if isinstance(last_error, CninfoError):
        raise last_error

    raise CninfoError(
        "无法连接巨潮公告查询服务。请检查网络后重试；"
        "也可以打开 https://www.cninfo.com.cn/ 手动查询。"
    ) from last_error


def _parse_announcement_time(value: Any) -> datetime:
    """
    把巨潮返回的公告时间转换成北京时间。

    参数：
        value: 通常是毫秒时间戳，也可能是秒时间戳或数字字符串。

    返回：
        datetime: 北京时间；无法识别时返回 1970-01-01。

    说明：
        巨潮接口的 announcementTime 通常是 13 位毫秒时间戳。
        这里兼容 10 位秒时间戳，避免接口格式变化时排序全部出错。
    """
    try:
        if isinstance(value, str) and value.strip():
            timestamp = float(value.strip())
        elif isinstance(value, (int, float)):
            timestamp = float(value)
        else:
            return datetime(1970, 1, 1, tzinfo=CHINA_TIMEZONE)
    except (TypeError, ValueError, OverflowError):
        return datetime(1970, 1, 1, tzinfo=CHINA_TIMEZONE)

    # 13 位毫秒时间戳除以 1000；10 位秒时间戳保持不变。
    if timestamp > 100_000_000_000:
        timestamp /= 1000.0

    try:
        return datetime.fromtimestamp(timestamp, tz=CHINA_TIMEZONE)
    except (OverflowError, OSError, ValueError):
        return datetime(1970, 1, 1, tzinfo=CHINA_TIMEZONE)


def _parse_announcement(item: Any) -> Announcement | None:
    """
    把巨潮接口的一条原始公告转换成 Announcement。

    参数：
        item: 接口 announcements 列表中的一项。

    返回：
        Announcement | None: 信息完整时返回整理后的对象；
                             标题或 PDF 地址为空时返回 None。

    说明：
        - 标题可能带 <em> 标签，需要清理；
        - adjunctUrl 是相对路径，需要拼成完整地址；
        - 同时判断摘要、英文版、更正后版本等属性，供年报选择使用。
    """
    if not isinstance(item, dict):
        return None

    title = _clean_external_text(item.get("announcementTitle"), 400)
    if not title:
        return None

    pdf_url = _build_pdf_url(item.get("adjunctUrl", ""))
    if not pdf_url:
        return None

    lower_title = title.lower()
    is_summary = "摘要" in title

    # 英文版可能写成“英文版”，也可能整条标题都是英文
    # （例如“2025 Annual Report”）。后者用“没有中文字符 +
    # annual report”判断，避免把中文年报误判成英文版。
    has_chinese = bool(re.search(r"[一-鿿]", title))
    is_english = (
        "英文" in title
        or "english" in lower_title
        or ("annual report" in lower_title and not has_chinese)
    )
    # 只把“单独的审计报告”视为审计报告。
    # 少数公告标题会写成“年度报告及审计报告”，这种仍然是年报正文，
    # 不能因为标题里出现“审计报告”就被排除。
    is_audit_report = (
        "审计报告" in title and "年度报告" not in title
    )
    is_corrected = any(
        keyword in title
        for keyword in ("更正后", "修订", "更新后", "更正版")
    )
    is_notice = any(
        keyword in title
        for keyword in (
            "提示性公告",
            "预约披露",
            "披露时间",
            "披露公告",
            "更正公告",
            "补充公告",
            "补充版",
            "取消",
            "终止",
            "问询",
            "回复",
            "说明",
        )
    )

    # 完整年报正文：既不是摘要、英文版，也不是单独的审计报告或提示性公告。
    is_full_text = not (
        is_summary
        or is_english
        or is_audit_report
        or is_notice
    )

    # 选择优先级：
    # 全文优先，其次排除摘要/英文版，再看是否更正后版本；
    # 分数只用于同一年的候选排序，不会改变公告本身的内容。
    score = 0
    if is_full_text:
        score += 100
    if not is_summary:
        score += 20
    if not is_english:
        score += 10
    if "全文" in title:
        score += 10
    if is_corrected:
        score += 30
    if "年度报告" in title:
        score += 10

    return Announcement(
        title=title,
        announcement_time=_parse_announcement_time(
            item.get("announcementTime")
        ),
        pdf_url=pdf_url,
        sec_name=_clean_external_text(item.get("secName"), 100),
        sec_code=_clean_external_text(item.get("secCode"), 20),
        raw_id=_clean_external_text(
            item.get("announcementId") or item.get("id"),
            100,
        ),
        is_summary=is_summary,
        is_english=is_english,
        is_corrected=is_corrected,
        is_full_text=is_full_text,
        is_audit_report=is_audit_report,
        is_notice=is_notice,
        score=score,
    )


def query_announcements(
    stock: StockIdentity,
    start_date: date,
    end_date: date,
    page_num: int = 1,
    page_size: int = CNINFO_PAGE_SIZE,
) -> list[Announcement]:
    """
    查询某只股票在一段时间内的公告列表。

    参数：
        stock: 股票身份信息，包含巨潮内部编号。
        start_date: 公告日期范围开始（含当天）。
        end_date: 公告日期范围结束（含当天）。
        page_num: 第几页，从 1 开始。
        page_size: 每页条数；巨潮通常最多返回 30 条。

    返回：
        list[Announcement]: 整理后的公告列表；没有公告时返回空列表。

    异常：
        CninfoError: 网络失败、接口返回格式异常时抛出。

    说明：
        stock 参数必须写成“代码,内部编号”。
        如果只写代码，接口会返回全市场最新公告，无法按公司过滤。
    """
    safe_page_size = max(1, min(int(page_size), CNINFO_PAGE_SIZE))

    form = {
        "pageNum": str(max(1, int(page_num))),
        "pageSize": str(safe_page_size),
        "column": stock.column,
        "tabName": "fulltext",
        "plate": "",
        "stock": f"{stock.code},{stock.org_id}",
        "searchkey": "",
        "secDate": "",
        "tdate": "",
        "type": "",
        "trade": "",
        "seDate": (
            f"{start_date.isoformat()}~{end_date.isoformat()}"
        ),
        "sortName": "",
        "sortType": "",
        "isHLtitle": "true",
    }

    payload = _request_json(form)
    raw_items = payload.get("announcements")

    if raw_items is None:
        return []
    if not isinstance(raw_items, list):
        raise CninfoError("巨潮返回的公告列表格式异常。")

    announcements: list[Announcement] = []
    for item in raw_items:
        parsed = _parse_announcement(item)
        if parsed is not None:
            announcements.append(parsed)

    return announcements


def _query_searchkey_page(
    code: str,
    start_date: date,
    end_date: date,
    page_num: int,
) -> list[Announcement]:
    """
    用全文搜索方式按股票代码查公告，作为内部编号失效时的兜底。

    参数：
        code: 6 位股票代码。
        start_date: 公告日期范围开始。
        end_date: 公告日期范围结束。
        page_num: 页码。

    返回：
        list[Announcement]: 只保留 secCode 与目标代码一致的公告。

    说明：
        巨潮的内部编号不是公开标准，未来可能变化。
        如果推导出的编号查不到目标公司，就用 searchkey 搜索代码，
        再在本地按 secCode 过滤。这个兜底只在主查询为空时使用。
    """
    form = {
        "pageNum": str(max(1, int(page_num))),
        "pageSize": str(CNINFO_PAGE_SIZE),
        "column": "",
        "tabName": "fulltext",
        "plate": "",
        "stock": "",
        "searchkey": code,
        "secDate": "",
        "tdate": "",
        "type": "",
        "trade": "",
        "seDate": (
            f"{start_date.isoformat()}~{end_date.isoformat()}"
        ),
        "sortName": "",
        "sortType": "",
        "isHLtitle": "true",
    }

    payload = _request_json(form)
    raw_items = payload.get("announcements")
    if not isinstance(raw_items, list):
        return []

    results: list[Announcement] = []
    for item in raw_items:
        parsed = _parse_announcement(item)
        if parsed is None:
            continue
        # 全文搜索可能混入其他公司，必须严格按股票代码过滤。
        # 如果接口没有返回 secCode，宁可不采用，也不能冒险拿别家公司的年报。
        if parsed.sec_code != code:
            continue
        results.append(parsed)

    return results


def _deduplicate_announcements(
    announcements: list[Announcement],
) -> list[Announcement]:
    """
    去掉重复公告。

    参数：
        announcements: 原始公告列表。

    返回：
        list[Announcement]: 按“标题 + PDF 地址 + 公告时间”去重后的列表。

    说明：
        翻页或主查询/兜底查询可能返回同一条公告。
        去重可以避免同一年报出现多次，影响版本判断。
    """
    seen: set[tuple[str, str, str]] = set()
    results: list[Announcement] = []

    for item in announcements:
        key = (
            item.title,
            item.pdf_url,
            item.announcement_time.isoformat(),
        )
        if key in seen:
            continue
        seen.add(key)
        results.append(item)

    return results


def _query_year_announcements(
    stock: StockIdentity,
    year: int,
    progress_callback: Callable[[str], None] | None = None,
) -> list[Announcement]:
    """
    查询某只股票在“某个会计年度的年报披露窗口”内的公告。

    参数：
        stock: 股票身份信息。
        year: 会计年度。例如 2025 年报通常在 2026 年披露。
        progress_callback: 可选的进度回调。

    返回：
        list[Announcement]: 该披露窗口内、属于目标公司的公告。

    说明：
        2025 年年度报告通常在 2026 年 1～12 月披露，
        因此查询窗口是 2026-01-01 到 2026-12-31。
        这样即使公司披露较晚，也不会漏掉。
    """
    start_date = date(year + 1, 1, 1)
    end_date = date(year + 1, 12, 31)

    _notify(
        progress_callback,
        f"正在查询巨潮 {year} 年年度报告公告……",
    )

    collected: list[Announcement] = []

    for page_num in range(1, CNINFO_MAX_PAGES + 1):
        try:
            page_items = query_announcements(
                stock,
                start_date,
                end_date,
                page_num=page_num,
            )
        except CninfoError:
            # 第一页就失败说明网络或接口有问题，直接向上传递；
            # 后续页失败时保留已经拿到的前几页，避免整次查询白费。
            if page_num == 1:
                raise
            break

        # 主查询可能因为内部编号不对而返回其他公司的公告，
        # 这里必须按 secCode 严格过滤，不能把别家公司的年报拿来用。
        # 缺少 secCode 的条目无法核实归属，也一并丢弃。
        matched = [
            item
            for item in page_items
            if item.sec_code == stock.code
        ]
        collected.extend(matched)

        if len(page_items) < CNINFO_PAGE_SIZE:
            break

    return _deduplicate_announcements(collected)


def _query_searchkey_year(
    code: str,
    year: int,
    progress_callback: Callable[[str], None] | None = None,
) -> list[Announcement]:
    """
    用全文搜索兜底查询某个年度的公告。

    参数：
        code: 6 位股票代码。
        year: 会计年度。
        progress_callback: 可选的进度回调。

    返回：
        list[Announcement]: 属于目标公司的公告。

    说明：
        只在主查询没有结果时调用，避免每次查询都多花时间。
    """
    start_date = date(year + 1, 1, 1)
    end_date = date(year + 1, 12, 31)

    _notify(
        progress_callback,
        f"正在用股票代码搜索 {year} 年公告……",
    )

    collected: list[Announcement] = []

    for page_num in range(1, CNINFO_MAX_PAGES + 1):
        try:
            page_items = _query_searchkey_page(
                code,
                start_date,
                end_date,
                page_num,
            )
        except CninfoError:
            if page_num == 1:
                raise
            break

        collected.extend(page_items)

        if len(page_items) < CNINFO_PAGE_SIZE:
            break

    return _deduplicate_announcements(collected)


# ---------------------------------------------------------------------------
# 年报筛选
# ---------------------------------------------------------------------------


def _is_annual_report_title(title: str, year: int) -> bool:
    """
    判断公告标题是否可能是“某会计年度的年度报告”。

    参数：
        title: 已清理的公告标题。
        year: 会计年度。

    返回：
        bool: 标题包含该年份且包含“年度报告/年报”时返回 True。

    说明：
        这里只做粗筛，后面的 _choose_annual_report 会继续区分
        全文、摘要、英文版、审计报告等版本。
    """
    if not isinstance(title, str) or not title:
        return False

    year_text = str(year)
    if year_text not in title:
        return False

    if "年度报告" not in title and "年报" not in title:
        return False

    # 半年度报告也包含“年度报告”四个字，必须明确排除。
    if "半年度报告" in title or "半年度" in title:
        return False

    # 季度报告、业绩预告等不是年度报告。
    excluded_keywords = (
        "季度报告",
        "业绩预告",
        "业绩快报",
        "ESG",
        "社会责任",
        "内部控制",
        "募集说明书",
        "上市公告书",
        "招股说明书",
        "债券",
        "评级",
        "跟踪评级",
    )
    return not any(keyword in title for keyword in excluded_keywords)


def _choose_annual_report(
    announcements: list[Announcement],
    year: int,
) -> tuple[Announcement | None, list[Announcement]]:
    """
    从一年的公告中选出最合适的年度报告。

    参数：
        announcements: 该年的公告列表。
        year: 会计年度。

    返回：
        tuple[Announcement | None, list[Announcement]]:
            - 第一个值是选中的年报；没有候选时为 None；
            - 第二个值是其他候选，按优先级从高到低排列。

    说明：
        选择顺序：
        1. 先排除单独的审计报告、提示性公告和英文版；
        2. 优先完整年报全文；
        3. 如果只有摘要，则退而求其次；
        4. 同类型中优先“更正后/修订版”；
        5. 再按公告时间从新到旧。
    """
    candidates = [
        item
        for item in announcements
        if _is_annual_report_title(item.title, year)
        and not item.is_audit_report
        # 更正公告、提示性公告、问询回复等不是年报正文，
        # 即使标题里带“年度报告”也不能拿来分析。
        and not item.is_notice
        # 英文版不是中文年报正文，不能因为找不到中文版就拿英文版
        # 冒充“年度报告摘要”；找不到中文版时宁可明确提示没有找到。
        and not item.is_english
    ]

    if not candidates:
        return None, []

    full_text_candidates = [
        item for item in candidates if item.is_full_text
    ]
    pool = full_text_candidates or candidates

    chosen = max(
        pool,
        key=lambda item: (item.score, item.announcement_time),
    )

    alternatives = sorted(
        (item for item in candidates if item is not chosen),
        key=lambda item: (item.score, item.announcement_time),
        reverse=True,
    )

    return chosen, alternatives


def select_annual_report(
    code: str,
    year: int,
    lookback_years: int = 3,
    progress_callback: Callable[[str], None] | None = None,
) -> AnnualReportSelection:
    """
    为指定股票和会计年度选出一份官方年度报告。

    参数：
        code: 用户输入的股票代码，允许带 SH/SZ 前后缀。
        year: 想要的会计年度，例如 2025。
        lookback_years: 当年找不到时，最多向前回退几年。
        progress_callback: 可选的进度回调，用于页面显示。

    返回：
        AnnualReportSelection: 选中的年报及版本说明。

    异常：
        CninfoError: 股票代码不支持、网络失败，或回退范围内都找不到年报。

    说明：
        - 先按推导出的巨潮内部编号查询；
        - 如果内部编号失效，自动改用全文搜索兜底；
        - 当年没有年报时，向前回退查找最新可用年报；
        - 返回的 warning 会明确告诉用户“使用了哪一年、是全文还是摘要”。
    """
    normalized_code = normalize_stock_code(code)
    column, exchange_name = infer_exchange(normalized_code)
    stock = StockIdentity(
        code=normalized_code,
        name="",
        org_id=_org_id_for(normalized_code, column),
        column=column,
        exchange=exchange_name,
    )

    if not isinstance(year, int):
        raise CninfoError("年份必须是整数，例如 2025。")

    # 回退范围：用户要求的年份，再往前最多 lookback_years 年。
    years_to_try = [year]
    years_to_try.extend(
        range(
            year - 1,
            max(year - lookback_years - 1, 1999),
            -1,
        )
    )

    for query_year in years_to_try:
        announcements = _query_year_announcements(
            stock,
            query_year,
            progress_callback=progress_callback,
        )

        chosen, alternatives = _choose_annual_report(
            announcements,
            query_year,
        )

        # 主查询没有选中合适年报时，再用全文搜索兜底。
        # 不能只在“主查询完全没有公告”时才兜底：
        # 有些公司一年公告超过翻页上限，主查询会返回一堆其他公告，
        # 但年报恰好被挤到后面；此时全文搜索仍可能把年报找出来。
        if chosen is None:
            try:
                fallback = _query_searchkey_year(
                    normalized_code,
                    query_year,
                    progress_callback=progress_callback,
                )
            except CninfoError:
                # 主查询已经有目标公司的公告时，兜底失败不应让整次
                # 查询直接报错；继续向前一年回退，尽量拿到可用年报。
                # 如果主查询本身就是空的，说明网络或接口可能有问题，
                # 这时仍把具体错误抛给用户，避免误以为“没有年报”。
                if not announcements:
                    raise
                fallback = []

            if fallback:
                announcements = _deduplicate_announcements(
                    list(announcements) + list(fallback)
                )
                chosen, alternatives = _choose_annual_report(
                    announcements,
                    query_year,
                )

        if announcements:
            # 从公告里补全公司简称。
            for item in announcements:
                if item.sec_name:
                    stock = replace(stock, name=item.sec_name)
                    break

        if chosen is not None:
            warning_parts: list[str] = []

            if query_year != year:
                warning_parts.append(
                    f"未找到 {year} 年年度报告，"
                    f"已改用 {query_year} 年年度报告。"
                )

            if not chosen.is_full_text:
                warning_parts.append(
                    "未找到年报全文，当前使用年度报告摘要，"
                    "部分明细可能缺失。"
                )

            if chosen.is_corrected:
                warning_parts.append(
                    "该报告存在更正后版本，已优先采用最新版本。"
                )

            return AnnualReportSelection(
                stock=stock,
                requested_year=year,
                report_year=query_year,
                announcement=chosen,
                alternatives=alternatives,
                warning=" ".join(warning_parts),
            )

    raise CninfoError(
        f"未找到 {normalized_code} 在 {year} 年及之前 "
        f"{lookback_years} 年内的年度报告。"
        "请确认股票代码和年份是否正确；"
        "北交所、港股和美股暂不支持。"
    )


# ---------------------------------------------------------------------------
# PDF 下载
# ---------------------------------------------------------------------------


def download_pdf(pdf_url: str) -> bytes:
    """
    下载巨潮年报 PDF。

    参数：
        pdf_url: 公告中的官方 PDF 地址。

    返回：
        bytes: PDF 原始字节。

    异常：
        CninfoError: 地址不是巨潮官方地址、网络失败、文件过大、
                     返回内容不是 PDF 时抛出。

    安全说明：
        - 只允许 https 的巨潮官方域名；
        - 最多读取 CNINFO_MAX_PDF_BYTES；
        - 检查文件头是否为 %PDF-，防止把 HTML 错误页当成 PDF 解析。
    """
    if not _is_allowed_cninfo_url(pdf_url):
        raise CninfoError(
            "年报 PDF 地址不是巨潮官方地址，已拒绝下载。"
        )

    # 和公告查询共用同一个限速器，避免查询后立刻下载造成连续请求。
    _throttle()

    request = Request(
        pdf_url,
        headers={
            "User-Agent": CNINFO_USER_AGENT,
            "Accept": "application/pdf,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Accept-Encoding": "identity",
            "Referer": CNINFO_HOME_URL,
        },
    )

    try:
        with _open_request(
            request,
            timeout=CNINFO_TIMEOUT_SECONDS,
        ) as response:
            status = getattr(response, "status", 200)
            if status < 200 or status >= 300:
                raise CninfoError(
                    _http_error_message(status, "下载年报")
                )

            headers = getattr(response, "headers", {}) or {}
            content_type = ""
            try:
                content_type = (headers.get("Content-Type", "") or "").lower()
            except AttributeError:
                content_type = ""

            # 如果服务器明确返回 HTML，说明多半是错误页或登录页，
            # 不要把它当 PDF 读取。
            if "text/html" in content_type:
                raise CninfoError(
                    "巨潮返回的是网页而不是 PDF，"
                    "年报文件可能已下架或需要重新获取。"
                )

            raw_bytes = response.read(CNINFO_MAX_PDF_BYTES + 1)
            if len(raw_bytes) > CNINFO_MAX_PDF_BYTES:
                raise CninfoError(
                    "年报 PDF 超过 50 MB，已停止自动下载。"
                    "请打开巨潮原文链接手动下载后，"
                    "再通过 Tab 1 上传分析。"
                )

            if not raw_bytes.lstrip().startswith(b"%PDF-"):
                raise CninfoError(
                    "下载到的内容不是 PDF 文件，"
                    "可能是巨潮返回了错误页。请稍后重试。"
                )

            return raw_bytes

    except HTTPError as exc:
        raise CninfoError(
            _http_error_message(exc.code, "下载年报")
        ) from exc
    except CninfoError:
        raise
    except (URLError, TimeoutError, OSError) as exc:
        raise CninfoError(
            "下载年报 PDF 失败，请检查网络后重试；"
            "也可以打开巨潮原文链接手动下载。"
        ) from exc
