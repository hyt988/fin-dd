"""
法务与业务 Agent 的“逐维度资料缺失”真实调用测试。

用途：
- 法务材料提供诉讼事实和买卖合同质量纠纷，知识产权、合规资料故意缺失；
- 业务材料只提供竞争格局事实，上下游、商业模式和行业政策资料故意缺失；
- 检查有证据的维度是否正常分析，缺失维度是否只输出系统规定的固定提示；
- 额外检查法务报告的风险速览表、证据溯源和合同章节固定结论。

注意：
- 本脚本会真实调用 DeepSeek API 两次，因此会产生少量调用费用；
- 示例公司、金额和事实全部虚构，不对应任何真实企业；
- API Key 由两个 Agent 从环境变量读取，脚本中不保存任何密钥。
"""

import re

from agents.business_agent import (
    AVAILABLE_BUSINESS_DIMENSIONS,
    BusinessAgentError,
    _build_missing_message as build_business_missing,
    analyze_business,
)
from agents.legal_agent import (
    AVAILABLE_LEGAL_DIMENSIONS,
    LegalAgentError,
    _build_missing_message as build_legal_missing,
    analyze_legal,
)


# 法务材料保留诉讼事实和买卖合同质量纠纷。
# 末尾明确说明知识产权和合规材料没有提供，避免模型把“没写”误解为“没有风险”。
FAKE_PARTIAL_LEGAL_TEXT = """
星海智能装备有限公司法务材料摘要（虚构案例，仅用于系统测试）

2024 年 11 月，华辰精密部件有限公司以买卖合同质量纠纷起诉公司，
索赔 3,200 万元，案件已立案但尚未开庭。公司已提出反诉 800 万元。
除上述买卖合同纠纷外，材料未提供合同文本、对赌条款、知识产权资料和合规证照，
无法据此判断其他合同条款、知识产权或合规事项。
""".strip()


# 业务材料只保留竞争格局事实。
# 末尾明确说明其他材料没有提供，用来检验逐维度缺失提示。
FAKE_PARTIAL_BUSINESS_TEXT = """
星海智能装备有限公司业务材料摘要（虚构案例，仅用于系统测试）

国内同类系统集成商超过 200 家，前五名厂商合计市场份额约 35%，
公司市场份额约 4.8%。2024 年公司产品平均售价同比下降 8%，
主要竞品报价下降 10% 至 15%。
材料未提供客户、供应商、收入构成、商业模式和行业政策资料，
无法据此判断上下游依赖、商业模式或行业政策风险。
""".strip()


def parse_markdown_sections(markdown: str) -> dict[str, str]:
    """
    把模型返回的 Markdown 报告拆成“二级标题 -> 正文”。

    参数：
        markdown: 模型返回的完整报告。

    返回：
        dict[str, str]: 键是去掉序号后的维度名称，值是标题下方的正文。

    说明：
        只识别“## 一、维度名称”这种格式；如果模型没有按格式输出，
        对应维度就不会出现在字典中，测试会报告缺失。
    """
    heading_pattern = re.compile(
        r"^##\s+[一二三四五六七八九十]+、(.+?)\s*$",
        re.MULTILINE,
    )
    matches = list(heading_pattern.finditer(markdown))

    sections: dict[str, str] = {}
    for index, match in enumerate(matches):
        dimension = match.group(1).strip()
        body_start = match.end()
        body_end = (
            matches[index + 1].start()
            if index + 1 < len(matches)
            else len(markdown)
        )
        sections[dimension] = markdown[body_start:body_end].strip()

    return sections


def check_report(
    report: str,
    dimension_order: tuple[str, ...],
    analyzed_dimensions: tuple[str, ...],
    missing_dimensions: tuple[str, ...],
    missing_message_builder,
    required_fields: tuple[str, ...],
) -> list[str]:
    """
    检查一份报告是否满足“部分分析、部分缺失”的要求。

    参数：
        report: 模型返回的 Markdown 报告。
        dimension_order: 本次应输出的维度顺序。
        analyzed_dimensions: 应当正常分析的维度。
        missing_dimensions: 应当只输出固定缺失提示的维度。
        missing_message_builder: 生成固定缺失提示的函数。
        required_fields: 正常分析维度必须包含的加粗字段名。

    返回：
        list[str]: 发现的问题列表；空列表表示全部通过。
    """
    problems: list[str] = []
    sections = parse_markdown_sections(report)

    if list(sections) != list(dimension_order):
        problems.append(
            "二级标题不符合预期。"
            f"期望：{list(dimension_order)}；实际：{list(sections)}"
        )

    for dimension in analyzed_dimensions:
        body = sections.get(dimension, "")
        if not body:
            problems.append(f"“{dimension}”没有正文。")
            continue

        missing_fields = [
            field for field in required_fields
            if f"**{field}**" not in body
        ]
        if missing_fields:
            problems.append(
                f"“{dimension}”缺少字段：{'、'.join(missing_fields)}"
            )

    for dimension in missing_dimensions:
        expected_message = missing_message_builder(dimension)
        body = sections.get(dimension, "")
        if body != expected_message:
            problems.append(
                f"“{dimension}”的缺失提示不是规定的固定文字。\n"
                f"期望：{expected_message}\n"
                f"实际：{body or '（没有正文）'}"
            )

    return problems


def run_legal_test() -> tuple[bool, list[str]]:
    """
    调用法务 Agent 并检查结果。

    返回：
        tuple[bool, list[str]]: 是否成功，以及发现的问题列表。
    """
    print("=" * 72)
    print("法务 Agent：只提供诉讼事实")
    print("=" * 72)

    try:
        report = analyze_legal(FAKE_PARTIAL_LEGAL_TEXT)
    except LegalAgentError as exc:
        print("调用失败：", str(exc))
        return False, ["法务 Agent 调用失败"]

    print(report)
    print("-" * 72)

    problems = check_report(
        report=report,
        dimension_order=AVAILABLE_LEGAL_DIMENSIONS,
        # 合同维度已改为“永不拒答”：材料里有买卖合同质量纠纷，
        # 因此它必须和诉讼维度一样正常分析。
        analyzed_dimensions=("诉讼风险", "合同违约与纠纷"),
        missing_dimensions=("知识产权纠纷", "合规性"),
        missing_message_builder=build_legal_missing,
        required_fields=(
            "风险等级",
            "核心事实",
            "法律影响",
            "应对建议",
        ),
    )

    # 以下检查针对“高管一页纸简报”的四项硬要求：
    # 1. 顶部只有一张四列风险速览表；
    # 2. 风险点用三级标题拆分，并带括号证据；
    # 3. 合同章节必须给出结论；
    # 4. 末尾必须有防幻觉声明，且全文不得出现“未提及/暂无数据”。
    sections = parse_markdown_sections(report)

    if "| 风险维度 | 风险等级 | 核心结论 | 溯源依据 |" not in report:
        problems.append("报告缺少规定的风险速览表格头。")

    if "###" not in report:
        problems.append("报告没有用三级标题拆分风险点。")

    if (
        "基于当前公开披露的简版文本，"
        "未展示深度工商股权及明细关联交易数据。"
    ) not in report:
        problems.append("报告缺少防幻觉强制声明。")

    contract_body = sections.get("合同违约与纠纷", "")
    if "**结论**" not in contract_body:
        problems.append("“合同违约与纠纷”缺少“**结论**”行。")
    if "【资料缺失】" in contract_body:
        problems.append("“合同违约与纠纷”被错误地整节拒答。")
    # 材料里存在买卖合同质量纠纷，模型不得套用“未发现重大违约”的结论。
    if "未发现重大违约或对赌协议" in contract_body:
        problems.append("“合同违约与纠纷”已有纠纷事实，却仍输出无违约结论。")

    if not re.search(r"[（(](见|依据|原文摘录)", report):
        problems.append("报告没有任何括号证据溯源。")

    for banned_word in ("未提及", "暂无数据"):
        if banned_word in report:
            problems.append(f"报告出现了禁用字眼“{banned_word}”。")

    return not problems, problems


def run_business_test() -> tuple[bool, list[str]]:
    """
    调用业务 Agent 并检查结果。

    返回：
        tuple[bool, list[str]]: 是否成功，以及发现的问题列表。
    """
    print("=" * 72)
    print("业务 Agent：只提供竞争格局事实")
    print("=" * 72)

    try:
        report = analyze_business(FAKE_PARTIAL_BUSINESS_TEXT)
    except BusinessAgentError as exc:
        print("调用失败：", str(exc))
        return False, ["业务 Agent 调用失败"]

    print(report)
    print("-" * 72)

    problems = check_report(
        report=report,
        dimension_order=AVAILABLE_BUSINESS_DIMENSIONS,
        analyzed_dimensions=("竞争格局",),
        missing_dimensions=(
            "上下游依赖度",
            "商业模式",
            "行业政策风险",
        ),
        missing_message_builder=build_business_missing,
        required_fields=(
            "风险等级",
            "核心分析",
            "数据依据",
            "行业参考",
            "风险判断",
        ),
    )
    return not problems, problems


def main() -> int:
    """
    依次执行两个补测，并汇总结果。

    返回：
        int: 0 表示两组测试全部通过；1 表示至少一组存在问题。
    """
    legal_passed, legal_problems = run_legal_test()
    business_passed, business_problems = run_business_test()

    print("=" * 72)
    print("补测汇总")
    print("=" * 72)
    print("法务 Agent：", "通过" if legal_passed else "未通过")
    for problem in legal_problems:
        print("  -", problem)
    print("业务 Agent：", "通过" if business_passed else "未通过")
    for problem in business_problems:
        print("  -", problem)

    if legal_passed and business_passed:
        print("结论：资料缺失提示完全符合规定。")
        return 0

    print("结论：存在不符合规定的输出，请检查上面的具体问题。")
    return 1


if __name__ == "__main__":
    # 只有直接运行本文件时才执行 API 调用；
    # 被其他程序 import 时不会自动产生费用。
    raise SystemExit(main())
