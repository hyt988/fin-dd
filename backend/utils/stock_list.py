"""
A 股公司列表工具。

为什么要有这个模块：
- 网页需要一个“可搜索的公司下拉框”，让用户输入代码或名称就能选公司；
- 公司列表来自 AKShare 的 stock_info_a_code_name() 接口；
- 接口返回 5000 多家公司，其中包含北交所、B 股等当前年报逻辑不支持的代码，
  如果全部塞进下拉框，用户可能选中一个“查不了”的公司，体验很差。

本模块只负责三件事：
1. 调用 AKShare 获取全量公司列表；
2. 过滤出当前支持的沪深 A 股（沪市 6 开头、深市 0 或 3 开头）；
3. 把结果整理成“股票代码 - 公司名称”的文字，供 Streamlit 下拉框直接使用。

说明：
- 本模块不依赖 Streamlit，方便单独写测试；
- 不读写 API Key，AKShare 的公司列表是公开数据；
- 不缓存结果，缓存由网页层的 @st.cache_data 负责。
"""

from __future__ import annotations


# 当前年报和三张表逻辑支持的沪深 A 股代码前缀：
# - 6 开头：沪市 A 股（含 600/601/603/605/688 等）；
# - 0 开头：深市主板 A 股（含 000/001/002/003 等）；
# - 3 开头：创业板（300/301 等）。
# 4/8/920 开头多为北交所，2/9 开头多为 B 股，1/5 开头多为基金，
# 这些都不在本次下拉框中展示，避免用户选中后才发现无法查询。
SUPPORTED_A_SHARE_PREFIXES = ("6", "0", "3")


def is_supported_a_share_code(code: str) -> bool:
    """
    判断一个股票代码是否属于当前支持的沪深 A 股。

    参数：
        code: 6 位股票代码，例如 "600519"、"000001"、"300750"。

    返回：
        bool: 是 6 位数字且以 6、0、3 开头时返回 True，否则返回 False。

    说明：
        这里只做“代码格式和市场范围”的判断，不校验公司是否真实存在；
        公司是否真实存在由 AKShare 返回的列表本身保证。
    """
    if not isinstance(code, str):
        return False

    clean_code = code.strip()
    return (
        len(clean_code) == 6
        and clean_code.isdigit()
        and clean_code.startswith(SUPPORTED_A_SHARE_PREFIXES)
    )


def extract_stock_code(option: str) -> str:
    """
    从下拉框的“代码 - 名称”文字中取出股票代码。

    参数：
        option: 下拉框选项，例如 "600519 - 贵州茅台"。

    返回：
        str: 股票代码，例如 "600519"；格式不正确时返回空字符串。

    说明：
        只按第一个“ - ”分隔，避免公司名称里本身带横杠时把名称误当成代码。
        返回值会再次校验，防止下拉框内容被意外篡改。
    """
    if not isinstance(option, str):
        return ""

    code = option.split(" - ", 1)[0].strip()
    return code if is_supported_a_share_code(code) else ""


def fetch_a_share_company_options() -> list[str]:
    """
    从 AKShare 获取沪深 A 股公司列表，并整理成下拉框选项。

    返回：
        list[str]: 形如 ["000001 - 平安银行", "600519 - 贵州茅台"]，
                   按股票代码升序排列。

    异常：
        RuntimeError: AKShare 返回空数据、接口不可用，或过滤后没有可用公司时抛出。
                      异常文字为中文，调用方可以直接展示给用户。

    说明：
        - 使用 AKShare 的 stock_info_a_code_name()，一次取得全量 A 股代码和名称；
        - 只保留 6/0/3 开头的沪深 A 股，过滤北交所、B 股和基金；
        - 公司名称中的空格会被去掉，例如接口里的“万  科Ａ”显示为“万科Ａ”；
        - 同一代码只保留一条，避免接口重复数据导致下拉框出现重复项。
    """
    # 延迟导入 AKShare：只有真正需要加载公司列表时才导入，
    # 避免网页刚启动就加载这个比较大的第三方库。
    import akshare as ak

    try:
        data = ak.stock_info_a_code_name()
    except Exception as exc:
        # AKShare 可能抛出网络、解析或第三方库异常。
        # 这里统一转成中文提示，不把英文堆栈直接展示给用户。
        raise RuntimeError(
            "暂时无法从 AKShare 获取 A 股公司列表，"
            "请检查网络后重试。"
        ) from exc

    if data is None or getattr(data, "empty", True):
        raise RuntimeError("AKShare 返回的 A 股公司列表为空。")

    # 用字典按代码去重：同一个代码出现多次时，后面的名称会覆盖前面的。
    companies: dict[str, str] = {}

    for record in data.to_dict("records"):
        raw_code = record.get("code", "")
        raw_name = record.get("name", "")

        # 统一转成文字并清理首尾空白。
        code = str(raw_code).strip()

        # 公司名称去掉所有空白字符，避免接口里用于对齐的空格
        # 让下拉框显示成“万  科Ａ”这种不自然的格式。
        name = "".join(str(raw_name).split())

        if not is_supported_a_share_code(code):
            continue
        if not name or name.lower() == "nan":
            continue

        companies[code] = name

    if not companies:
        raise RuntimeError(
            "AKShare 返回的公司列表中没有可用的沪深 A 股。"
        )

    # 按代码升序排列，方便用户按代码查找，也让每次加载顺序保持一致。
    return [
        f"{code} - {companies[code]}"
        for code in sorted(companies)
    ]
