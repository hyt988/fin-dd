"""
A 股公司下拉框数据测试。

这个脚本验证“公司查询”下拉框背后的取数逻辑是否正确：
1. 只保留沪深 A 股（6/0/3 开头），过滤北交所、B 股等暂不支持的代码；
2. 公司名称中的多余空格会被清理，例如“万  科Ａ”变成“万科Ａ”；
3. 下拉框文字统一为“股票代码 - 公司名称”，并按代码升序排列；
4. 从下拉框文字中提取股票代码时，非法内容不会混进查询逻辑；
5. 接口返回重复代码时只保留一条。

运行方式：
- 只跑离线测试（不联网，几秒钟完成）：
      python test_company_list.py
- 额外真实请求一次 AKShare，检查全量数据是否正确：
      python test_company_list.py --live

说明：
- 离线测试使用假数据，不会访问网络；
- --live 会请求 AKShare 的公开接口，不需要 API Key，也不产生费用；
- AKShare 的公司列表是公开数据，不涉及任何密钥。
"""

from __future__ import annotations

import argparse
import sys
import unittest
from unittest.mock import patch

import pandas as pd

from utils.stock_list import (
    extract_stock_code,
    fetch_a_share_company_options,
    is_supported_a_share_code,
)


# ---------------------------------------------------------------------------
# 离线测试：用假数据验证过滤和格式化规则
# ---------------------------------------------------------------------------


class StockListOfflineTests(unittest.TestCase):
    """不联网，用构造的 DataFrame 验证公司列表处理逻辑。"""

    @staticmethod
    def build_fake_dataframe() -> pd.DataFrame:
        """
        构造一份覆盖各种边界情况的假公司列表。

        返回：
            pandas.DataFrame: 列名与 AKShare 接口一致（code、name）。

        说明：
            故意混入北交所、B 股、带空格名称、空名称和重复代码，
            用来确认程序会按要求过滤和清理，而不是原样塞进下拉框。
        """
        return pd.DataFrame(
            [
                # 正常沪深 A 股：沪市主板、深市主板、创业板、科创板。
                {"code": "600519", "name": "贵州茅台"},
                {"code": "000001", "name": "平安银行"},
                {"code": "300750", "name": "宁德时代"},
                {"code": "688981", "name": "中芯国际"},
                # 名称中间带对齐空格，应该被清理掉。
                {"code": "000002", "name": "万  科Ａ"},
                # 北交所代码：920 开头，应该被过滤。
                {"code": "920992", "name": "中科美菱"},
                {"code": "830799", "name": "艾融软件"},
                # B 股代码：900/200 开头，应该被过滤。
                {"code": "900901", "name": "云赛Ｂ股"},
                {"code": "200002", "name": "万科Ｂ"},
                # 代码格式不对：位数不足、含字母，应该被过滤。
                {"code": "60051", "name": "位数不足"},
                {"code": "SH600000", "name": "带市场前缀"},
                # 名称为空或是 nan 文字，应该被过滤。
                {"code": "600001", "name": ""},
                {"code": "600002", "name": "nan"},
                # 重复代码：只保留一条，后出现的名称覆盖前面的。
                {"code": "600519", "name": "贵州茅台"},
            ]
        )

    def test_is_supported_a_share_code(self) -> None:
        """只接受 6 位数字且以 6、0、3 开头的代码。"""
        valid_codes = ["600519", "000001", "300750", "688981"]
        invalid_codes = [
            "920992",  # 北交所
            "830799",  # 北交所
            "900901",  # 沪市 B 股
            "200002",  # 深市 B 股
            "60051",   # 位数不足
            "SH600000",  # 带字母
            "60051A",  # 含字母
            "",
            "   ",
        ]

        for code in valid_codes:
            with self.subTest(code=code):
                self.assertTrue(is_supported_a_share_code(code))

        for code in invalid_codes:
            with self.subTest(code=code):
                self.assertFalse(is_supported_a_share_code(code))

    def test_extract_stock_code(self) -> None:
        """能从“代码 - 名称”中取出代码，非法内容返回空字符串。"""
        self.assertEqual(
            extract_stock_code("600519 - 贵州茅台"),
            "600519",
        )
        self.assertEqual(
            extract_stock_code("000001 - 平安银行"),
            "000001",
        )
        # 公司名称中本身带横杠时，只按第一个分隔符切分。
        self.assertEqual(
            extract_stock_code("300750 - 宁德时代-新能源"),
            "300750",
        )
        # 空值、非法代码、格式混乱的内容一律返回空字符串。
        for invalid_option in [
            "",
            "贵州茅台",
            "920992 - 中科美菱",
            "60051 - 位数不足",
            None,
        ]:
            with self.subTest(option=invalid_option):
                self.assertEqual(extract_stock_code(invalid_option), "")

    def test_fetch_filters_and_formats_options(self) -> None:
        """真实取数函数只保留沪深 A 股，并整理成下拉框文字。"""
        fake_data = self.build_fake_dataframe()

        # 用假数据替换 AKShare 的接口调用，测试时不联网。
        with patch(
            "akshare.stock_info_a_code_name",
            return_value=fake_data,
        ):
            options = fetch_a_share_company_options()

        self.assertEqual(
            options,
            [
                "000001 - 平安银行",
                "000002 - 万科Ａ",
                "300750 - 宁德时代",
                "600519 - 贵州茅台",
                "688981 - 中芯国际",
            ],
        )

        # 排序必须是升序，保证每次加载顺序一致。
        self.assertEqual(options, sorted(options))

        # 过滤后的列表里不能出现北交所或 B 股。
        joined = "\n".join(options)
        for excluded in ["920992", "830799", "900901", "200002"]:
            with self.subTest(excluded=excluded):
                self.assertNotIn(excluded, joined)

    def test_fetch_raises_readable_error_when_empty(self) -> None:
        """接口返回空表时，抛出可直接展示给用户的中文错误。"""
        with patch(
            "akshare.stock_info_a_code_name",
            return_value=pd.DataFrame(columns=["code", "name"]),
        ):
            with self.assertRaises(RuntimeError) as context:
                fetch_a_share_company_options()

        self.assertIn("为空", str(context.exception))

    def test_fetch_raises_readable_error_when_network_fails(self) -> None:
        """接口抛异常时，转成中文提示，而不是把英文堆栈丢给用户。"""
        with patch(
            "akshare.stock_info_a_code_name",
            side_effect=ConnectionError("network down"),
        ):
            with self.assertRaises(RuntimeError) as context:
                fetch_a_share_company_options()

        message = str(context.exception)
        self.assertIn("暂时无法", message)
        self.assertIn("请检查网络", message)
        # 不应该把原始英文异常文字直接展示出来。
        self.assertNotIn("network down", message)


# ---------------------------------------------------------------------------
# 联网检查：确认真实接口能加载全量沪深 A 股
# ---------------------------------------------------------------------------


def run_live_check() -> int:
    """
    真实请求一次 AKShare，检查下拉框数据是否完整。

    返回：
        int: 0 表示检查通过，1 表示发现异常。

    说明：
        这个检查会联网，但不需要 API Key，也不会产生费用。
        输出只包含公司代码、名称和数量，不涉及任何密钥。
    """
    print("正在请求 AKShare 获取全量 A 股公司列表……")
    try:
        options = fetch_a_share_company_options()
    except RuntimeError as exc:
        print(f"加载失败：{exc}")
        return 1

    print(f"加载成功：共 {len(options):,} 家沪深 A 股公司。")
    print(f"第一条：{options[0]}")
    print(f"最后一条：{options[-1]}")

    # 基本完整性检查：真实市场应有 5000 家左右沪深 A 股。
    failures: list[str] = []

    if len(options) < 5000:
        failures.append(
            f"公司数量只有 {len(options):,} 家，低于预期的 5000 家。"
        )

    if "600519 - 贵州茅台" not in options:
        failures.append("列表中没有找到“600519 - 贵州茅台”。")

    if "000001 - 平安银行" not in options:
        failures.append("列表中没有找到“000001 - 平安银行”。")

    # 下拉框中的每一项都必须是“代码 - 名称”，且代码属于沪深 A 股。
    for option in options:
        code = extract_stock_code(option)
        if not code:
            failures.append(f"发现无法解析的选项：{option}")
            break

    # 确认北交所代码没有混进来（例如 920992）。
    if any(option.startswith("920") for option in options):
        failures.append("列表里混入了北交所 920 开头的代码。")

    if failures:
        print("\n检查发现以下问题：")
        for failure in failures:
            print(f"- {failure}")
        return 1

    print("\n联网检查通过：下拉框数据覆盖全量沪深 A 股。")
    print(
        "提示：在网页里输入“600519”或“贵州茅台”能搜到对应公司，"
        "就说明下拉框搜索功能正常。"
    )
    return 0


def main() -> int:
    """先跑离线测试，用户加了 --live 时再联网检查真实数据。"""
    parser = argparse.ArgumentParser(
        description="A 股公司下拉框数据测试",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="真实请求一次 AKShare，检查全量沪深 A 股数据",
    )
    args = parser.parse_args()

    # 离线测试不联网，几秒钟完成。
    suite = unittest.defaultTestLoader.loadTestsFromModule(
        sys.modules[__name__]
    )
    result = unittest.TextTestRunner(verbosity=2).run(suite)

    if not result.wasSuccessful():
        return 1

    if args.live:
        return run_live_check()

    print(
        "\n离线测试全部通过。若要检查真实全量数据，"
        "请运行：python test_company_list.py --live"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
