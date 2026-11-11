"""
多智能体调度器（Orchestrator）。

职责：
- 把“上传资料分析”和“按公司名称搜索分析”两条入口分开处理；
- 上传资料走 strict（严格尽调）模式，保持原有分析行为；
- 公司名称先搜索公开网页摘要，再走 screening（公开信息初筛）模式；
- 支持一次选择一位或多位专家，并分别指定每位专家要分析的维度；
- 某一位专家失败时，只记录该专家的错误，不影响其他专家继续分析。

三个入口的分工：
1. analyze_uploaded_text()：材料由企业或用户提供，按正式尽调标准分析；
2. analyze_company_by_name()：输入公司名称，先搜索再分析，适合命令行或一次调用；
3. analyze_public_search_text()：分析已经搜索好的摘要，不再重复搜索，
   适合网页“搜索一次、三位专家共用同一份结果”的流程。

两条材料来源的区别（很重要）：
- 上传资料：企业或用户提供的材料，可信度较高，按正式尽调标准分析；
- 公开搜索：网页摘要，未经核实，只能做初步筛查，
  结果不能冒充正式尽调结论。

返回结构（两个入口基本一致）：
{
    "status": "ok" | "partial" | "failed" | "search_failed",
    "mode": "upload" | "company_search",
    "company_name": 公司名称或 None,
    "source_name": 资料来源说明,
    "search_text": 搜索结果原文；上传模式为空字符串,
    "web_search": {
        "legal": {
            "status": "ok" | "failed" | "skipped",
            "query": 实际搜索词,
            "summary": 搜索摘要或失败原因,
        },
        ...
    },
    "results": {
        "finance": {
            "agent_name": "财务专家",
            "result": "Markdown 分析结果",
            "dimensions": ["偿债能力", ...],
        },
        ...
    },
    "errors": {
        "legal": "该专家失败的中文原因",
        ...
    },
    "message": "给页面展示的总体说明",
}

状态说明：
- ok：所有被选中的专家都分析成功；
- partial：至少一位专家成功，但另有专家失败；
- failed：所有被选中的专家都失败；
- search_failed：公开信息没有搜到或搜索失败，已跳过全部智能体分析。

说明：
- 本模块不直接读写 API Key，密钥由各专家自己从环境变量读取；
- 本模块不负责网页渲染，只返回结构化结果，由 app.py 决定如何展示。
"""

from collections.abc import Mapping, Sequence

from agents.business_agent import (
    ANALYSIS_MODE_SCREENING,
    ANALYSIS_MODE_STRICT,
    AVAILABLE_BUSINESS_DIMENSIONS,
    BusinessAgentError,
    analyze_business,
)
from agents.finance_agent import (
    AVAILABLE_DIMENSIONS as AVAILABLE_FINANCE_DIMENSIONS,
    FinanceAgentError,
    analyze_finance,
)
from agents.legal_agent import (
    AVAILABLE_LEGAL_DIMENSIONS,
    LegalAgentError,
    analyze_legal,
)
from utils.web_searcher import (
    build_public_summary_query,
    is_search_failure,
    search_company_info,
    search_public_summaries,
)

# 调度结果的状态值。
# 页面可以根据这些固定字符串决定显示成功、部分失败还是搜索失败。
STATUS_OK = "ok"
STATUS_PARTIAL = "partial"
STATUS_FAILED = "failed"
STATUS_SEARCH_FAILED = "search_failed"

# 两种入口模式。
MODE_UPLOAD = "upload"
MODE_COMPANY_SEARCH = "company_search"


class OrchestratorError(RuntimeError):
    """
    调度器对外抛出的参数错误。

    只用于“调用方传错了参数”的情况，例如没有选择专家、专家名称拼错、
    dimensions_by_agent 不是字典。单个专家在分析过程中的失败不会抛出本异常，
    而是记录在返回结果的 errors 字段中，避免一位专家拖垮全部分析。
    """


# 三位专家的统一配置。
# 页面、调度器和测试都从这里读取专家名称、可用维度和调用函数，
# 避免同一个专家在多个文件里出现不同写法。
AGENT_DEFINITIONS: dict[str, dict] = {
    "finance": {
        "agent_name": "财务专家",
        "dimensions": AVAILABLE_FINANCE_DIMENSIONS,
        "analyze": analyze_finance,
        "error_type": FinanceAgentError,
    },
    "legal": {
        "agent_name": "法务专家",
        "dimensions": AVAILABLE_LEGAL_DIMENSIONS,
        "analyze": analyze_legal,
        "error_type": LegalAgentError,
    },
    "business": {
        "agent_name": "业务专家",
        "dimensions": AVAILABLE_BUSINESS_DIMENSIONS,
        "analyze": analyze_business,
        "error_type": BusinessAgentError,
    },
}

# 专家固定顺序：财务、法务、业务。
# 无论调用方按什么顺序传入，返回值都按这个顺序排列，方便页面稳定展示。
AGENT_KEYS = ("finance", "legal", "business")

# 法务、业务专家各自补充的公开网络搜索主题。
# 财务专家已经有年报和三年报表，不需要再做网页搜索。
WEB_SEARCH_TOPICS: dict[str, str] = {
    "legal": "涉诉 行政处罚",
    "business": "行业竞争格局 主营业务研报",
}

# 每位专家最多把几条公开网页摘要放进提示词。
# 3 条足以补充关键事实，又不会把上下文塞满。
WEB_SEARCH_TOP_N = 3


def _normalize_agents(agents: Sequence[str] | None) -> list[str]:
    """
    校验并整理本次要运行的专家列表。

    参数：
        agents: 专家标识列表，可选值为 AGENT_KEYS；
                传入 None 时表示三位专家全部运行。

    返回：
        list[str]: 按固定顺序排列、去重后的专家标识列表。

    异常：
        OrchestratorError: 未选择专家，或包含不支持的专家标识时抛出。
    """
    if agents is None:
        return list(AGENT_KEYS)

    # 防止调用方误传单个字符串，否则 Python 会把字符串拆成一个个字符。
    if isinstance(agents, str):
        requested_agents = [agents]
    else:
        requested_agents = list(agents)

    # 先检查类型，避免后续拼错误提示时出现英文类型错误。
    if any(not isinstance(key, str) for key in requested_agents):
        raise OrchestratorError("专家标识必须是文字名称，请检查选择内容。")

    unknown_agents = [
        key for key in requested_agents if key not in AGENT_DEFINITIONS
    ]
    if unknown_agents:
        raise OrchestratorError(
            "存在不支持的专家标识：" + "、".join(unknown_agents)
        )

    requested_set = set(requested_agents)
    ordered_agents = [key for key in AGENT_KEYS if key in requested_set]

    if not ordered_agents:
        raise OrchestratorError("请至少选择一位专家进行分析。")

    return ordered_agents


def _get_dimensions_for_agent(
    agent_key: str,
    dimensions_by_agent: Mapping[str, Sequence[str]] | None,
) -> list[str] | None:
    """
    取出某位专家本次要分析的维度。

    参数：
        agent_key: 专家标识，例如 "finance"。
        dimensions_by_agent: 形如 {"finance": ["偿债能力"]} 的映射。
                             传入 None，或映射中没有这位专家时，
                             返回 None，交给专家默认全选。

    返回：
        list[str] | None: 维度列表；None 表示使用该专家的默认全部维度。

    异常：
        OrchestratorError: dimensions_by_agent 不是映射类型时抛出。
                           至于维度名称是否有效，由各专家自己校验；
                           这样不同专家的错误提示可以保持各自的中文措辞。
    """
    if dimensions_by_agent is None:
        return None

    if not isinstance(dimensions_by_agent, Mapping):
        raise OrchestratorError(
            "dimensions_by_agent 必须是字典，例如 "
            '{"finance": ["偿债能力"], "legal": ["诉讼风险"]}。'
        )

    dimensions = dimensions_by_agent.get(agent_key)
    if dimensions is None:
        return None

    if isinstance(dimensions, str):
        # 允许调用方只写一个维度名称，避免字符串被拆成单个字符。
        return [dimensions]

    return list(dimensions)


def _build_summary_message(
    status: str,
    results: dict[str, dict],
    errors: dict[str, str],
) -> str:
    """
    根据执行结果生成一句给用户看的总体说明。

    参数：
        status: 调度状态。
        results: 成功的结果字典。
        errors: 失败的错误字典。

    返回：
        str: 可直接展示在页面上的中文总结。
    """
    if status == STATUS_OK:
        return f"已完成 {len(results)} 位专家的分析，结果均可用。"

    if status == STATUS_PARTIAL:
        failed_names = "、".join(
            AGENT_DEFINITIONS[key]["agent_name"] for key in errors
        )
        return (
            f"已完成 {len(results)} 位专家的分析；"
            f"{failed_names}本次失败，具体原因见下方错误提示。"
            "成功的分析结果仍然可以查看。"
        )

    if status == STATUS_SEARCH_FAILED:
        return (
            "本次公开信息搜索未取得可用结果，已跳过智能体分析。"
            "搜索失败或未检索到信息，不代表该公司没有风险。"
        )

    return (
        "本次所有专家分析均失败，请根据下方错误提示检查网络、"
        "API Key 或输入资料后重试。"
    )


def _run_web_search_for_agent(
    company_name: str,
    agent_key: str,
) -> tuple[str, dict]:
    """
    为某位专家补充一条公开网络搜索，并返回摘要和状态。

    参数：
        company_name: 已清理的公司名称，必须非空。
        agent_key: 专家标识；只有 legal 和 business 有搜索主题。

    返回：
        tuple[str, dict]:
            - 第一个值是要交给专家的摘要文本；搜索失败时为空字符串；
            - 第二个值是搜索状态，包含 status、query、summary 三个字段，
              status 只可能是 ok / failed / skipped。

    说明：
        搜索失败不会抛异常，也不会阻断专家分析；
        专家会退回到只使用正式材料，页面可以据状态给出提示。
    """
    topic = WEB_SEARCH_TOPICS.get(agent_key)
    if not topic:
        return "", {
            "status": "skipped",
            "reason": "该专家不需要公开网络搜索",
            "query": "",
            "summary": "",
        }

    query = build_public_summary_query(company_name, topic)

    try:
        summary = search_public_summaries(
            company_name=company_name,
            topic_query=topic,
            top_n=WEB_SEARCH_TOP_N,
        )
    except Exception:
        # search_public_summaries 已经把网络错误转换成中文提示；
        # 这里只兜底处理真正未预期的异常，不把英文堆栈暴露给用户。
        return "", {
            "status": "failed",
            "reason": "公开网络搜索出现未预期错误",
            "query": query,
            "summary": (
                "【搜索失败】公开网络搜索出现未预期错误，"
                "本次仅使用正式材料分析。"
            ),
        }

    if is_search_failure(summary):
        return "", {
            "status": "failed",
            "reason": "公开网络搜索未取得可用结果",
            "query": query,
            "summary": summary,
        }

    return summary, {
        "status": "ok",
        "reason": "",
        "query": query,
        "summary": summary,
    }


def _run_agents(
    text: str,
    agents: list[str],
    dimensions_by_agent: Mapping[str, Sequence[str]] | None,
    analysis_mode: str,
    finance_trend_summary: str = "",
    finance_trend_years: Sequence[int] | None = None,
    web_search_enabled: bool = False,
    company_name: str = "",
) -> tuple[dict[str, dict], dict[str, str], dict[str, dict]]:
    """
    依次运行选中的专家，并隔离单个专家的失败。

    参数：
        text: 要分析的材料文本。
        agents: 已校验、已排序的专家标识列表。
        dimensions_by_agent: 每位专家的维度选择；None 表示全部使用默认维度。
        analysis_mode: strict 或 screening，由两个入口函数决定，
                       调用方不能通过网页随意切换。
        finance_trend_summary: 由 Python 计算好的三年趋势摘要。
                               只传给财务专家，法务和业务专家拿不到，
                               避免无关数据挤占它们的分析空间。
        finance_trend_years: 三年报表的实际年份，例如 [2025, 2024, 2023]。
                             只传给财务专家，用于把三大报表表头写成
                             2023/2024/2025 三列，避免退化成两列。
        web_search_enabled: 是否给法务、业务专家补充公开网络搜索。
                            只有 app.py 在识别到公司名时才设为 True。
        company_name: 用于公开网络搜索的公司名称；为空时跳过搜索。

    返回：
        tuple[dict[str, dict], dict[str, str], dict[str, dict]]:
            - 成功结果字典，键是专家标识；
            - 失败错误字典，键是专家标识，值是中文错误原因；
            - 公开搜索状态字典，键是专家标识，值是搜索状态说明。
    """
    results: dict[str, dict] = {}
    errors: dict[str, str] = {}
    web_search_meta: dict[str, dict] = {}

    for agent_key in agents:
        definition = AGENT_DEFINITIONS[agent_key]
        dimensions = _get_dimensions_for_agent(agent_key, dimensions_by_agent)

        try:
            # 先准备所有专家共用的参数。
            # 分析模式由两个入口函数决定：上传年报固定 strict，
            # 公开搜索固定 screening，避免两种材料的可信度被混淆。
            analyze_kwargs = {
                "dimensions": dimensions,
                "analysis_mode": analysis_mode,
            }

            # 法务、业务专家在识别到公司名时，各补充一条公开网络搜索。
            # 财务专家已有年报和三年报表，不需要搜索；
            # 其他参数也不能硬塞给财务专家，否则会报类型错误。
            if (
                web_search_enabled
                and agent_key in WEB_SEARCH_TOPICS
                and isinstance(company_name, str)
                and company_name.strip()
            ):
                web_summary, web_meta = _run_web_search_for_agent(
                    company_name=company_name.strip(),
                    agent_key=agent_key,
                )
                web_search_meta[agent_key] = web_meta
                if web_summary:
                    analyze_kwargs["web_search_summary"] = web_summary
            elif web_search_enabled and agent_key in WEB_SEARCH_TOPICS:
                web_search_meta[agent_key] = {
                    "status": "skipped",
                    "reason": "未识别到公司名称，未执行公开网络搜索",
                    "query": "",
                    "summary": "",
                }

            # 趋势摘要和三年年份只给财务专家。
            # 其他专家没有这些参数，不能硬塞给它们，否则会报类型错误。
            if agent_key == "finance" and finance_trend_summary:
                analyze_kwargs["trend_summary"] = finance_trend_summary
            if agent_key == "finance" and finance_trend_years:
                analyze_kwargs["trend_years"] = list(finance_trend_years)

            result = definition["analyze"](text, **analyze_kwargs)
        except definition["error_type"] as exc:
            # 各专家自己的业务错误，提示文字已经是中文且不含密钥，
            # 直接记录，供页面按专家展示。
            errors[agent_key] = str(exc)
            continue
        except Exception:
            # 防御性兜底：第三方库或未知问题抛出的意外错误。
            # 不把原始英文堆栈展示给用户，也不让一位专家中断整轮分析。
            errors[agent_key] = (
                f"{definition['agent_name']}分析过程中出现未预期错误，"
                "请稍后重试；如果持续失败，请检查网络或输入材料。"
            )
            continue

        results[agent_key] = {
            "agent_name": definition["agent_name"],
            "result": result,
            "dimensions": (
                dimensions
                if dimensions is not None
                else list(definition["dimensions"])
            ),
        }

    return results, errors, web_search_meta


def _build_status(
    results: dict[str, dict],
    errors: dict[str, str],
) -> str:
    """
    根据成功和失败数量，计算本次调度的总体状态。

    参数：
        results: 成功的专家结果。
        errors: 失败的专家错误。

    返回：
        str: STATUS_OK / STATUS_PARTIAL / STATUS_FAILED 之一。
    """
    if results and not errors:
        return STATUS_OK
    if results and errors:
        return STATUS_PARTIAL
    return STATUS_FAILED


def analyze_uploaded_text(
    text: str,
    agents: Sequence[str] | None = None,
    dimensions_by_agent: Mapping[str, Sequence[str]] | None = None,
    source_name: str = "上传或粘贴的投研初筛资料",
    finance_trend_summary: str = "",
    finance_trend_years: Sequence[int] | None = None,
    company_name: str = "",
    enable_web_search: bool = False,
) -> dict:
    """
    分析用户上传、粘贴或官方年报资料，固定使用 strict（正式尽调）模式。

    参数：
        text: 从 PDF、TXT、粘贴框或年报数据包中得到的资料文字。
        agents: 要运行的专家标识列表；None 表示财务、法务、业务全部运行。
        dimensions_by_agent: 每位专家要分析的维度，例如
                             {"finance": ["偿债能力"], "legal": ["诉讼风险"]}。
                             某位专家没有出现在字典中时默认全选。
        source_name: 资料来源名称，用于页面标注，例如上传的文件名。
        finance_trend_summary: 由 Python 计算好的三年趋势摘要。
                               只有财务专家会收到；留空时行为与改造前一致。
        finance_trend_years: 三年报表的实际年份，例如 [2025, 2024, 2023]。
                             只有财务专家会收到，用于把报表表头写成
                             2023、2024、2025 三列；留空时由模型按材料填写。
        company_name: 本次分析对应的公司名称，用于给法务、业务专家
                      补充公开网络搜索；为空时跳过搜索。
        enable_web_search: 是否启用公开网络搜索。只有 app.py 在识别到
                           公司名、且当前专家是法务或业务时才会设为 True；
                           默认 False，保持原有调用行为不变。

    返回：
        dict: 结构化调度结果，详见模块开头说明。
             额外包含 web_search 字段，记录法务、业务的公开搜索状态：
             {"legal": {"status": "ok"|"failed"|"skipped",
                        "query": "搜索词", "summary": "摘要或失败原因"}}。
             单个专家失败会记录在 errors 中，不会向上抛异常。

    异常：
        OrchestratorError: 资料为空、未选择专家、专家标识无效，
                           或 dimensions_by_agent 不是字典时抛出。
    """
    if not isinstance(text, str) or not text.strip():
        raise OrchestratorError("待分析的材料为空，请先上传或粘贴有效内容。")

    if not isinstance(source_name, str) or not source_name.strip():
        source_name = "上传或粘贴的投研初筛资料"

    clean_company_name = (
        " ".join(company_name.split())
        if isinstance(company_name, str)
        else ""
    )
    clean_text = text.strip()
    selected_agents = _normalize_agents(agents)

    results, errors, web_search_meta = _run_agents(
        text=clean_text,
        agents=selected_agents,
        dimensions_by_agent=dimensions_by_agent,
        analysis_mode=ANALYSIS_MODE_STRICT,
        finance_trend_summary=finance_trend_summary,
        finance_trend_years=finance_trend_years,
        web_search_enabled=bool(enable_web_search),
        company_name=clean_company_name,
    )
    status = _build_status(results, errors)

    return {
        "status": status,
        "mode": MODE_UPLOAD,
        "company_name": clean_company_name or None,
        "source_name": source_name.strip(),
        "search_text": "",
        "web_search": web_search_meta,
        "results": results,
        "errors": errors,
        "message": _build_summary_message(status, results, errors),
    }


def _build_search_failed_result(
    company_name: str,
    search_text: str,
) -> dict:
    """
    构造“搜索失败或没有结果”的统一返回值。

    参数：
        company_name: 已清理的公司名称，允许为空字符串。
        search_text: 搜索工具返回的友好失败提示，保留下来供页面展示。

    返回：
        dict: status="search_failed" 的结构化结果，results 和 errors 均为空。
    """
    return {
        "status": STATUS_SEARCH_FAILED,
        "mode": MODE_COMPANY_SEARCH,
        "company_name": company_name or None,
        "source_name": (
            f"公开网页搜索：{company_name}" if company_name else "公开网页搜索"
        ),
        "search_text": search_text,
        "web_search": {},
        "results": {},
        "errors": {},
        "message": _build_summary_message(STATUS_SEARCH_FAILED, {}, {}),
    }


def analyze_public_search_text(
    search_text: str,
    company_name: str = "",
    agents: Sequence[str] | None = None,
    dimensions_by_agent: Mapping[str, Sequence[str]] | None = None,
) -> dict:
    """
    分析一份已经取得的公开搜索摘要，不再重复调用搜索引擎。

    使用场景：
        网页采用“搜索一次、三位专家共用”的流程。用户在页面顶部搜索一次后，
        搜索结果会缓存在页面状态中；之后每位专家分析时都调用本函数，
        直接复用同一份摘要，避免每位专家都重新搜索三组关键词。

    参数：
        search_text: search_company_info() 已经返回的公开摘要文本。
        company_name: 公司名称，用于结果标注；允许为空。
        agents: 要运行的专家标识列表；None 表示三位专家全部运行。
        dimensions_by_agent: 每位专家要分析的维度；未指定时默认全选。

    返回：
        dict: 结构化调度结果，详见模块开头说明。
              - 如果 search_text 本身是搜索失败提示，返回 status="search_failed"，
                不调用任何智能体；
              - 否则用 screening（公开信息初筛）模式分析，并在结果中保留
                search_text，方便页面展示来源。

    异常：
        OrchestratorError: search_text 为空、未选择专家、专家标识无效，
                           或 dimensions_by_agent 不是字典时抛出。
    """
    selected_agents = _normalize_agents(agents)

    if not isinstance(search_text, str) or not search_text.strip():
        raise OrchestratorError(
            "公开搜索摘要为空，请先重新搜索公司公开信息。"
        )

    clean_search_text = search_text.strip()
    clean_name = (
        " ".join(company_name.split())
        if isinstance(company_name, str)
        else ""
    )

    # 搜索失败提示不是公司材料，不能交给模型分析，
    # 否则模型可能对一句错误提示做出无意义的推断。
    if is_search_failure(clean_search_text):
        return _build_search_failed_result(clean_name, clean_search_text)

    results, errors, _ = _run_agents(
        text=clean_search_text,
        agents=selected_agents,
        dimensions_by_agent=dimensions_by_agent,
        analysis_mode=ANALYSIS_MODE_SCREENING,
    )
    status = _build_status(results, errors)

    return {
        "status": status,
        "mode": MODE_COMPANY_SEARCH,
        "company_name": clean_name or None,
        "source_name": (
            f"公开网页搜索：{clean_name}" if clean_name else "公开网页搜索"
        ),
        "search_text": clean_search_text,
        "web_search": {},
        "results": results,
        "errors": errors,
        "message": _build_summary_message(status, results, errors),
    }


def analyze_company_by_name(
    company_name: str,
    agents: Sequence[str] | None = None,
    dimensions_by_agent: Mapping[str, Sequence[str]] | None = None,
) -> dict:
    """
    根据公司名称搜索公开信息，并用 screening（公开信息初筛）模式分析。

    参数：
        company_name: 用户输入的公司名称。
        agents: 要运行的专家标识列表；None 表示三位专家全部运行。
        dimensions_by_agent: 每位专家要分析的维度；某位专家未指定时默认全选。

    返回：
        dict: 结构化调度结果，详见模块开头说明。
              - 搜索失败或没有结果时，status 为 "search_failed"，
                不调用任何智能体，避免在空材料上浪费 API 费用；
              - 搜索成功时，每位专家的结果都来自公开网页摘要，
                提示词要求其标注来源并区分事实与推断。

    异常：
        OrchestratorError: 未选择专家、专家标识无效，
                           或 dimensions_by_agent 不是字典时抛出。
                           公司名称为空或搜索失败不会抛异常，而是返回
                           status="search_failed" 的友好结果。
    """
    selected_agents = _normalize_agents(agents)
    clean_name = (
        " ".join(company_name.split())
        if isinstance(company_name, str)
        else ""
    )

    # 先搜索；搜索失败时由 analyze_public_search_text 统一返回友好结果，
    # 绝不把“搜索失败”的提示当成公司材料交给智能体分析。
    search_text = search_company_info(clean_name)

    return analyze_public_search_text(
        search_text=search_text,
        company_name=clean_name,
        agents=selected_agents,
        dimensions_by_agent=dimensions_by_agent,
    )
