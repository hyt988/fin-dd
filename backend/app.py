"""
财务尽调多智能体系统 —— Streamlit 前端入口。

当前版本支持两种材料来源：
1. 上传 PDF / TXT 或粘贴文字：按正式尽调（strict）模式分析；
2. 公司查询：从全量沪深 A 股公司下拉框中选择公司，
   直连巨潮资讯网获取官方年度报告，同时通过 AKShare 取得近三年
   资产负债表、利润表和现金流量表，再按正式尽调模式分析。

页面结构：
- Tab 1 负责上传 PDF / TXT 或粘贴文字；
- Tab 2 负责“公司查询”：用可搜索的下拉框选择沪深 A 股公司，
  再选择年份，从巨潮下载官方年报并解析核心数据，
  并用三个子页签展示近三年三张表的详细科目；
- 页面用 session_state 记住用户最后一次操作的材料来源，
  避免多个 Tab 同时渲染时无法判断专家该分析哪一份材料；
- 官方年报只下载和解析一次，三位专家共用同一份“年报数据包”；
- 近三年三张表只取一次，页面展示和三位专家共用同一份数据；
- 每位专家有独立的维度选择和分析按钮；
- 法务、业务专家在识别到公司名时，会自动补充一条公开网络搜索，
  把“搜索摘要 + 年报/上传材料”一起交给模型，避免输出过于空洞；
- 三份分析结果分别保存在各自页签中，切换页签不会互相覆盖；
- 业务、法务专家的深度分析会在正文中内嵌标准 Markdown 表格，
  页面直接按表格渲染；PDF 下载会把表格转成三线表图片后嵌入，
  避免 PDF 里出现 <table> 乱码；
- 三位专家结果下方都提供 PDF 下载按钮，方便阅读、打印和转发；
- 结果下方可以展开查看本次实际使用的原文或年报数据包；
- 左侧边栏提供品牌标识、本地 history_reports 历史报告入口、
  运行状态，以及深色/浅色阅读模式切换；
- 历史报告只从项目根目录的 history_reports 文件夹读取，
  不调用任何 Agent 分析函数，也不会自动上传或发送到网络。

说明：
- 本文件只负责网页展示和用户交互，年报获取和调度逻辑在 utils/ 与 agents/ 中；
- API Key 由各智能体从环境变量读取，本文件不会打印或展示密钥内容；
- 巨潮年报和 AKShare 三张表都是公开数据，不需要 API Key，
  也不会被写入项目目录。
"""

import csv
import io
import os
import re
from datetime import datetime
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

from agents.business_agent import AVAILABLE_BUSINESS_DIMENSIONS
from agents.finance_agent import (
    AVAILABLE_DIMENSIONS as AVAILABLE_FINANCE_DIMENSIONS,
    MAX_INPUT_CHARS,
)
from agents.legal_agent import AVAILABLE_LEGAL_DIMENSIONS
from agents.orchestrator import (
    OrchestratorError,
    analyze_uploaded_text,
)
from utils.annual_report_service import (
    AnnualReportError,
    AnnualReportResult,
    fetch_annual_report,
    normalize_report_code,
)
from utils.file_reader import (
    FileTextExtractionError,
    extract_text_from_uploaded_file,
)
from utils.financial_statements import (
    FinancialStatementError,
    fetch_three_year_statements,
    merge_agent_contexts,
)
from utils.stock_list import (
    extract_stock_code,
    fetch_a_share_company_options,
)
from utils.trend_calculator import (
    build_trend_raw_data,
    calculate_trends,
)

# 项目根目录以 app.py 所在位置为准，确保从任何目录启动都能找到 .env。
PROJECT_ROOT = Path(__file__).resolve().parent
load_dotenv(dotenv_path=PROJECT_ROOT / ".env")

# 历史尽调报告目录：固定放在项目根目录下，和 app.py 同级。
# 只扫描这一层文件夹，不递归子目录，避免误读其他位置的同名文件。
HISTORY_REPORTS_DIR = PROJECT_ROOT / "history_reports"

# 允许在历史记录区展示的文件类型。
# 目前只支持 Markdown（网页直接渲染）和 PDF（提供下载按钮）。
HISTORY_REPORT_SUFFIXES = {".md", ".pdf"}

# 首次运行时自动创建历史报告文件夹，方便用户直接往里面放报告。
# 创建失败（例如项目目录只读）时静默跳过；
# 后面的扫描函数会把“文件夹不存在”当作“暂无历史记录”处理，
# 不会因为这里失败而影响网页启动。
try:
    HISTORY_REPORTS_DIR.mkdir(parents=True, exist_ok=True)
except OSError:
    pass

# 阅读模式选项文字。
# 集中写成常量，避免单选按钮和判断逻辑里的中文出现错别字。
READING_MODE_DARK = "深色模式"
READING_MODE_LIGHT = "浅色模式"

# Streamlit 页面基础配置。
# set_page_config 必须是第一个执行到的 Streamlit 页面命令。
# page_title 是浏览器标签页上显示的文字；
# page_icon 是标签页左侧的小图标，浏览器会把它显示成 favicon。
st.set_page_config(
    page_title="智投明鉴 FinDD - 多智能体金融尽调平台",
    page_icon="🌿",
    layout="wide",
)

# ==================== 投行研报级排版样式 ====================
# 业务、法务专家会在正文中内嵌 Markdown 表格，而 Streamlit 默认的表格
# 带灰色网格和浅色背景，更像普通网页表格。这里注入一段自定义 CSS，
# 把所有报告表格改成财务/投行报告常见的“三线表”：
#   1. 表格顶部、底部各一条较粗的金色横线；
#   2. 表头下方一条细金线；
#   3. 删除所有内部竖线和横线，靠单元格留白区分列；
#   4. 表格背景用深色，适配深色主题并突出白色/金色数据。
# 同时加大正文行距和三级标题上边距，让“分析一段文字、跟一张支撑表格”
# 的阅读节奏更清楚。
# 说明：Streamlit 无法给单个 Markdown 区块单独加类名，因此这里把选择器
# 限定在页面主区域（[data-testid="stMain"]）内，不影响左侧输入区。
_REPORT_CSS = """
/* 1. 正文段落：上下各留 16px，行高 1.8，取消首行缩进并保持左对齐。
   目的：让“段前段后空一行”在网页上真正有视觉间隔，避免文字墙。 */
[data-testid="stMain"] [data-testid="stMarkdownContainer"] p {
    line-height: 1.8 !important;
    margin-top: 16px !important;
    margin-bottom: 16px !important;
    text-indent: 0 !important;
    text-align: left !important;
}

/* 1.1 无序/有序列表整体：与上下文拉开距离，左侧留出 24px 缩进。
   目的：列表不要贴着页面最左边，读起来更有层次。 */
[data-testid="stMain"] [data-testid="stMarkdownContainer"] ul,
[data-testid="stMain"] [data-testid="stMarkdownContainer"] ol {
    margin-bottom: 16px !important;
    padding-left: 24px !important;
}

/* 1.2 列表项：行高与段落一致，每个分点之间留 8px。
   目的：多个分点连续排列时，点与点之间不粘连。 */
[data-testid="stMain"] [data-testid="stMarkdownContainer"] li {
    line-height: 1.8 !important;
    margin-bottom: 8px !important;
}

/* 1.3 列表项和表格单元格内部的段落：取消 16px 上下间距。
   原因：Markdown 的“松散列表”会把列表项内容包进 p 标签，
   如果这些 p 也加 16px，会和 li 的 8px 叠加成双重留白；
   表格单元格已有内边距，内部 p 再加 16px 会把表格行撑得过高。 */
[data-testid="stMain"] [data-testid="stMarkdownContainer"] li p,
[data-testid="stMain"] [data-testid="stMarkdownContainer"] table p {
    margin-top: 0 !important;
    margin-bottom: 0 !important;
}

/* 2. 二级标题（分析维度）：上方留白，下方一条细金线 */
[data-testid="stMain"] [data-testid="stMarkdownContainer"] h2 {
    margin-top: 2.2rem !important;
    margin-bottom: 0.9rem !important;
    padding-bottom: 0.45rem !important;
    border-bottom: 1px solid rgba(200, 169, 106, 0.45) !important;
}

/* 3. 三级标题（分析主题/风险点）：上方 32px、下方 16px，左侧金色竖线。
   目的：让每个大模块之间界限分明，不会和上一段挤在一起。 */
[data-testid="stMain"] [data-testid="stMarkdownContainer"] h3 {
    margin-top: 32px !important;
    margin-bottom: 16px !important;
    padding-left: 0.65rem !important;
    border-left: 3px solid #C8A96A !important;
}

/* 4. 表格本体：三线表外框，深色底，和正文之间留出间距 */
[data-testid="stMain"] [data-testid="stMarkdownContainer"] table {
    width: 100% !important;
    border-collapse: collapse !important;
    border-spacing: 0 !important;
    margin: 0.4rem 0 1.4rem 0 !important;
    background-color: #151A1F !important;
    color: #E8E8E8 !important;
    border: none !important;
    border-top: 2px solid #C8A96A !important;
    border-bottom: 2px solid #C8A96A !important;
    font-size: 0.95rem !important;
}

/* 5. 去掉所有内部横线和竖线，只靠留白分隔单元格 */
[data-testid="stMain"] [data-testid="stMarkdownContainer"] table tr,
[data-testid="stMain"] [data-testid="stMarkdownContainer"] table th,
[data-testid="stMain"] [data-testid="stMarkdownContainer"] table td {
    border: none !important;
}

/* 6. 表头：加深底色，下沿一条细金线（三线表的第二条线） */
[data-testid="stMain"] [data-testid="stMarkdownContainer"] table thead th {
    background-color: #1D242B !important;
    color: #F5F1E6 !important;
    border-bottom: 1px solid rgba(200, 169, 106, 0.65) !important;
    padding: 12px 16px !important;
    font-weight: 700 !important;
    text-align: left !important;
    /* 允许表头换行，避免 6 列表格把页面撑出横向滚动条 */
    white-space: normal !important;
}

/* 7. 表体单元格：加大内边距，横向读数字时不易串行 */
[data-testid="stMain"] [data-testid="stMarkdownContainer"] table tbody td {
    padding: 11px 16px !important;
    color: #E8E8E8 !important;
    vertical-align: top !important;
    line-height: 1.55 !important;
}

/* 8. 奇数行保持表格底色，偶数行轻微提亮，形成克制的斑马纹 */
[data-testid="stMain"] [data-testid="stMarkdownContainer"] table tbody tr:nth-child(odd) td {
    background-color: transparent !important;
}
[data-testid="stMain"] [data-testid="stMarkdownContainer"] table tbody tr:nth-child(even) td {
    background-color: rgba(255, 255, 255, 0.035) !important;
}

/* 9. 鼠标悬停整行轻微高亮，核对数据时更容易对齐 */
[data-testid="stMain"] [data-testid="stMarkdownContainer"] table tbody tr:hover td {
    background-color: rgba(200, 169, 106, 0.10) !important;
}

/* 10. 表格中的加粗数字用暖金色，和普通正文形成层次 */
[data-testid="stMain"] [data-testid="stMarkdownContainer"] table strong {
    color: #F0D58C !important;
}
"""

# ==================== 顶栏与底栏样式 ====================
# 这些样式只服务于页面框架（品牌顶栏、个人中心状态、免责声明），
# 不会改动三位专家的报告正文和表格。
# 颜色继续沿用报告里的暖金色，让顶栏和正文看起来像同一套设计。
_PAGE_CHROME_CSS = """
/* 1. 顶栏左侧：品牌名。字号比正文大、字重更粗，形成“Logo + 产品名”的视觉重心。 */
.fdd-topbar-brand {
    color: #C8A96A !important;
    font-size: 1.45rem !important;
    font-weight: 800 !important;
    letter-spacing: 0.04em !important;
    line-height: 1.35 !important;
    padding: 3px 0 !important;
}

/* 1.1 侧边栏头部：和顶栏使用同一套品牌色，但字号略小，避免侧边栏过挤。 */
.fdd-sidebar-brand {
    color: #C8A96A !important;
    font-size: 1.30rem !important;
    font-weight: 800 !important;
    letter-spacing: 0.04em !important;
    line-height: 1.35 !important;
    padding: 2px 0 0 0 !important;
}

/* 2. 顶栏右侧：用一层容器负责右对齐。
   原因：内部的胶囊标签是 inline-block，只给自己写 text-align: right 不会靠右。 */
.fdd-topbar-user-wrap {
    text-align: right !important;
}

/* 3. 个人中心状态：做成低调的胶囊标签。
   这里只是展示文字，不绑定真实登录、会员或设置功能。 */
.fdd-topbar-user {
    display: inline-block !important;
    padding: 7px 14px !important;
    border: 1px solid rgba(200, 169, 106, 0.30) !important;
    border-radius: 999px !important;
    background-color: rgba(255, 255, 255, 0.04) !important;
    color: #C9C9C9 !important;
    font-size: 0.90rem !important;
    line-height: 1.2 !important;
    white-space: nowrap !important;
}

/* 4. 顶栏下方细线：把导航区和正文区轻轻分开。 */
.fdd-topbar-divider {
    margin: 10px 0 20px 0 !important;
    border-top: 1px solid rgba(200, 169, 106, 0.30) !important;
}

/* 5. 底部免责声明：上下各一条细灰线，文字居中、字号很小、颜色偏灰。
   这样既能被看到，又不会抢走正文的注意力。 */
.fdd-footer {
    margin: 56px 0 14px 0 !important;
    padding: 15px 0 13px 0 !important;
    border-top: 1px solid rgba(160, 160, 160, 0.38) !important;
    border-bottom: 1px solid rgba(160, 160, 160, 0.38) !important;
    text-align: center !important;
}

/* 6. 免责声明文字：使用 div 而不是 p，
   避免被上方“正文段落 16px 间距、1.8 行高”的规则影响。 */
.fdd-footer-text {
    margin: 0 !important;
    color: #8C8C8C !important;
    font-size: 12px !important;
    line-height: 1.7 !important;
    letter-spacing: 0.02em !important;
}

/* 7. 首次进入的空状态：居中虚线卡片 + 大号图标 + 引导文字。
   最大宽度限制在 760px，避免在大屏上拉成一条很长的横幅；
   背景和边框都使用很淡的颜色，不抢正文的注意力。 */
.fdd-empty-state {
    max-width: 760px !important;
    margin: 30px auto 38px auto !important;
    padding: 34px 24px !important;
    text-align: center !important;
    border: 1px dashed rgba(200, 169, 106, 0.42) !important;
    border-radius: 16px !important;
    background-color: rgba(255, 255, 255, 0.025) !important;
}
.fdd-empty-icon {
    font-size: 3.2rem !important;
    line-height: 1.1 !important;
    margin-bottom: 12px !important;
}
.fdd-empty-title {
    color: #E8D3A2 !important;
    font-size: 1.18rem !important;
    font-weight: 700 !important;
    line-height: 1.6 !important;
}
.fdd-empty-hint {
    margin-top: 10px !important;
    color: #9AA0A6 !important;
    font-size: 0.88rem !important;
    line-height: 1.7 !important;
}
"""

# ==================== 浅色阅读模式（基础版） ====================
# Streamlit 官方主题只能在启动时通过配置切换，运行中调用
# st.set_option("theme.base", ...) 会直接报错，因此这里采用“CSS 覆盖”的方案：
# 用户选“浅色模式”时，才把这段样式注入页面；切回“深色模式”后样式不再输出，
# 页面会自动恢复成原来的深色外观。
#
# 这是“基础版”，不是完整的官方主题：
# - 主要阅读区域（背景、正文、标题、表格、侧边栏、顶栏、底栏）会变浅；
# - 少数 Streamlit 原生控件（例如部分 dataframe 画布）可能仍保留深色，
#   但不影响报告阅读和现有分析功能。
_LIGHT_MODE_CSS = """
/* 0. 覆盖 Streamlit 的主题变量，让尽可能多的原生控件自动跟着变浅。 */
html body,
html body .stApp {
    --background-color: #F7F5EF !important;
    --secondary-background-color: #EFECE4 !important;
    --text-color: #1F2937 !important;
    --primary-color: #B08D4F !important;
    color-scheme: light !important;
}

/* 1. 页面整体背景：米白色比纯白柔和，长时间阅读不刺眼。 */
html body .stApp,
html body [data-testid="stAppViewContainer"],
html body [data-testid="stMain"] {
    background-color: #F7F5EF !important;
    color: #1F2937 !important;
}

/* 2. 顶部工具栏和侧边栏背景。 */
html body [data-testid="stHeader"] {
    background-color: rgba(247, 245, 239, 0.96) !important;
}
html body [data-testid="stSidebar"] {
    background-color: #EFECE4 !important;
    border-right: 1px solid #DED8C8 !important;
}

/* 3. 文字颜色：标题、正文、列表、引用和说明文字。 */
html body [data-testid="stMain"] h1,
html body [data-testid="stMain"] h2,
html body [data-testid="stMain"] h3,
html body [data-testid="stMain"] h4,
html body [data-testid="stMain"] h5,
html body [data-testid="stMain"] h6,
html body [data-testid="stMain"] [data-testid="stMarkdownContainer"] p,
html body [data-testid="stMain"] [data-testid="stMarkdownContainer"] li,
html body [data-testid="stMain"] [data-testid="stMarkdownContainer"] blockquote,
html body [data-testid="stSidebar"] h1,
html body [data-testid="stSidebar"] h2,
html body [data-testid="stSidebar"] h3,
html body [data-testid="stSidebar"] h4,
html body [data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p,
html body [data-testid="stSidebar"] label {
    color: #1F2937 !important;
}
html body [data-testid="stMain"] [data-testid="stCaptionContainer"],
html body [data-testid="stSidebar"] [data-testid="stCaptionContainer"] {
    color: #5B6470 !important;
}

/* 4. 输入框、文本域、下拉框和弹出菜单。 */
html body [data-testid="stMain"] input,
html body [data-testid="stMain"] textarea,
html body [data-testid="stSidebar"] input,
html body [data-testid="stSidebar"] textarea,
html body [data-baseweb="input"],
html body [data-baseweb="textarea"],
html body [data-baseweb="select"] > div {
    background-color: #FFFFFF !important;
    color: #1F2937 !important;
    border-color: #D6D0C0 !important;
}
html body [data-baseweb="menu"],
html body [data-baseweb="popover"],
html body [data-baseweb="popover"] > div {
    background-color: #FFFFFF !important;
    color: #1F2937 !important;
}

/* 4.1 多选下拉框：Streamlit 1.66 的多选控件使用 role="group" 容器绘制底色，
   不再走 data-baseweb 选择器。这里单独覆盖，避免浅色页面中出现一块深色长条。 */
html body [data-testid="stMultiSelect"] div[role="group"],
html body [data-testid="stMultiSelect"] [data-testid="stMultiSelectTagsContainer"] {
    background-color: #FFFFFF !important;
    color: #1F2937 !important;
    border-color: #D6D0C0 !important;
}
html body [data-testid="stMultiSelect"] input {
    background-color: transparent !important;
    color: #1F2937 !important;
}

/* 5. 普通按钮白底深字；主按钮保持金底白字，避免看不清。 */
html body button[kind="secondary"],
html body button[data-testid="stBaseButton-secondary"],
html body button[kind="ghost"],
html body button[data-testid="stBaseButton-ghost"] {
    background-color: #FFFFFF !important;
    color: #1F2937 !important;
    border-color: #D6D0C0 !important;
}
html body button[kind="primary"],
html body button[data-testid="stBaseButton-primary"] {
    background-color: #B08D4F !important;
    color: #FFFFFF !important;
    border-color: #B08D4F !important;
}
/* 主按钮内部可能还有 p / span 等子元素，
   单独把子元素文字设为白色，避免被正文段落颜色覆盖成深灰。 */
html body button[kind="primary"] *,
html body button[data-testid="stBaseButton-primary"] * {
    color: #FFFFFF !important;
}

/* 6. 文件上传区。 */
html body [data-testid="stFileUploaderDropzone"] {
    background-color: #FFFFFF !important;
    border-color: #D6D0C0 !important;
}
html body [data-testid="stFileUploaderDropzone"] * {
    color: #1F2937 !important;
}

/* 7. 提示框和展开框。 */
html body [data-testid="stAlert"] {
    background-color: #FFF9EC !important;
    border: 1px solid #E7D8B8 !important;
}
html body [data-testid="stAlert"] p,
html body [data-testid="stAlert"] div {
    color: #3F3420 !important;
}
html body [data-testid="stExpander"] {
    background-color: #FFFFFF !important;
    border-color: #DDD6C6 !important;
}
html body [data-testid="stExpander"] summary {
    color: #1F2937 !important;
}

/* 8. 报告表格：从深色三线表改成浅色三线表，保留金色横线。 */
[data-testid="stMain"] [data-testid="stMarkdownContainer"] table {
    background-color: #FFFFFF !important;
    color: #1F2937 !important;
    border-top-color: #B08D4F !important;
    border-bottom-color: #B08D4F !important;
}
[data-testid="stMain"] [data-testid="stMarkdownContainer"] table thead th {
    background-color: #F2EEE4 !important;
    color: #1F2937 !important;
    border-bottom-color: rgba(176, 141, 79, 0.65) !important;
}
[data-testid="stMain"] [data-testid="stMarkdownContainer"] table tbody td {
    color: #1F2937 !important;
}
[data-testid="stMain"] [data-testid="stMarkdownContainer"] table tbody tr:nth-child(even) td {
    background-color: #FAF8F3 !important;
}
[data-testid="stMain"] [data-testid="stMarkdownContainer"] table tbody tr:hover td {
    background-color: rgba(176, 141, 79, 0.12) !important;
}
[data-testid="stMain"] [data-testid="stMarkdownContainer"] table strong {
    color: #8A6420 !important;
}

/* 9. 顶栏、底栏和分隔线。 */
html body .fdd-topbar-brand,
html body .fdd-sidebar-brand {
    color: #8A6420 !important;
}
html body .fdd-topbar-user {
    background-color: #FFFFFF !important;
    color: #4B5563 !important;
    border-color: #D6C8A8 !important;
}
html body .fdd-topbar-divider {
    border-top-color: #D6C8A8 !important;
}
html body .fdd-footer {
    border-top-color: #C9C4B8 !important;
    border-bottom-color: #C9C4B8 !important;
}
html body .fdd-footer-text {
    color: #6B7280 !important;
}
html body .fdd-empty-state {
    border-color: rgba(176, 141, 79, 0.45) !important;
    background-color: #FFFFFF !important;
}
html body .fdd-empty-title {
    color: #8A6420 !important;
}
html body .fdd-empty-hint {
    color: #6B7280 !important;
}
html body hr {
    border-color: #D1CBBF !important;
}

/* 10. 页签：未选中深灰，选中金色。 */
html body [data-testid="stTabs"] [data-baseweb="tab"] {
    color: #4B5563 !important;
}
html body [data-testid="stTabs"] [aria-selected="true"] {
    color: #8A6420 !important;
}

/* 11. 数据表格外框：内部画布由 Streamlit 自己绘制，
   这里至少让外框和背景与浅色页面一致。 */
html body [data-testid="stDataFrame"] {
    background-color: #FFFFFF !important;
}
"""

# unsafe_allow_html=True 是 Streamlit 允许注入自定义样式的开关；
# 这里只注入本文件写死的 CSS，不包含任何用户输入，因此不存在脚本注入风险。
st.markdown(
    "<style>" + _REPORT_CSS + _PAGE_CHROME_CSS + "</style>",
    unsafe_allow_html=True,
)

# ==================== 顶部品牌导航栏 ====================
# 用两列模拟网站顶栏：
#   左列放品牌 Logo 和产品名，右列放虚拟的个人中心状态。
# 这里只有展示文字，不涉及登录、会员权限或真实设置功能。
# 顶栏放在所有输入区和分析区之前，因此无论用户滚到哪里，
# 页面最上方始终能看到品牌标识。
_topbar_brand_col, _topbar_user_col = st.columns(
    [3, 1],
    vertical_alignment="center",
)

with _topbar_brand_col:
    st.markdown(
        '<div class="fdd-topbar-brand">🌿 智投明鉴 FinDD</div>',
        unsafe_allow_html=True,
    )

with _topbar_user_col:
    st.markdown(
        '<div class="fdd-topbar-user-wrap">'
        '<div class="fdd-topbar-user">'
        "👤 VIP分析师&nbsp;&nbsp;|&nbsp;&nbsp;⚙️ 设置"
        "</div>"
        "</div>",
        unsafe_allow_html=True,
    )

# 顶栏和正文之间的细线，让页面层级更清楚。
st.markdown(
    '<div class="fdd-topbar-divider"></div>',
    unsafe_allow_html=True,
)

# 材料输入方式使用固定文字。
# 后面统一使用这些常量比较，避免手写字符串时出现错别字。
INPUT_MODE_UPLOAD = "上传 PDF / TXT"
INPUT_MODE_PASTE = "粘贴文字"
INPUT_MODE_ANNUAL_REPORT = "公司查询年报"

# Tab 1 内部只有“上传”和“粘贴”两种方式，公司查询单独放在 Tab 2。
INPUT_MODES = (
    INPUT_MODE_UPLOAD,
    INPUT_MODE_PASTE,
)

# 记住用户最后一次操作的材料来源。
# 两个 Tab 每次都会同时渲染，如果只看当前显示的是哪个 Tab，
# 程序无法知道专家应该分析上传文件、粘贴文字还是公司年报。
ACTIVE_INPUT_STATE_KEY = "active_input_mode"
UPLOAD_MODE_WIDGET_KEY = "upload_mode_radio"

# 公司列表缓存时间：24 小时。
# 新股上市、公司更名不需要秒级更新，24 小时足够；
# 缓存后页面反复刷新也不会每次都请求 AKShare。
COMPANY_LIST_CACHE_TTL_SECONDS = 24 * 60 * 60

# 官方年报结果的 session_state 键名。
# 结构是 AnnualReportResult.to_dict() 产生的普通字典，
# 其中包含 agent_context（给三位专家共用的年报数据包）。
ANNUAL_REPORT_STATE_KEY = "annual_report_state"

# 记录用户是否点击过任意一位专家的“开始分析”按钮。
# 用途：控制首次进入页面时的空状态引导。
# 从未点击过时显示引导；点击过之后不再显示，避免和加载动画或结果区重叠。
ANALYSIS_ATTEMPTED_STATE_KEY = "analysis_attempted"

# 页面标题。
st.title("📊 财务尽调多智能体系统")

# 按用户要求，在标题下方给出醒目的资料指南。
# st.info 支持 Markdown，因此两份星号中间的文字会显示为加粗。
st.info(
    "💡 尽调资料指南：为了获得精准分析，请上传包含"
    "**近三年审计报告、资产负债表、利润表、现金流量表及重要科目附注**"
    "的完整文档；法务和业务分析也请同时提供合同、诉讼、业务与行业资料。"
    "也可以直接在“公司查询”中选择沪深 A 股公司，"
    "系统会获取巨潮官方年报和近三年三张财务报表；"
    "官方年报也不能替代企业提供的完整尽调资料。"
)

# 额外说明三位专家共用材料和结果保留规则，降低第一次使用的理解成本。
st.caption(
    "资料只需提供一次，可在下方切换财务、法务、业务三位专家分别分析；"
    "已经生成的分析结果会分别保留，切换页签不会丢失。"
    "公司年报和三张表只需获取一次，三位专家共用同一份年报数据包。"
)

# 三个智能体的页面配置集中放在这里。
# 这样新增专家时，只需要补一段配置，而不必复制三套页面代码。
# 具体调用哪个分析函数由 agents/orchestrator.py 统一决定：
# 上传资料和公司年报都走严格尽调模式。
AGENT_CONFIGS = {
    "finance": {
        "tab_label": "财务专家",
        "dimension_label": "请选择财务分析维度",
        "button_label": "开始财务分析",
        "role_hint": (
            "分析范围：偿债能力、盈利能力、营运能力、现金流量、表外风险。"
            "建议提供近三年审计报告、三大报表及重要科目附注。"
        ),
        "dimensions": AVAILABLE_FINANCE_DIMENSIONS,
    },
    "legal": {
        "tab_label": "法务专家",
        "dimension_label": "请选择法务分析维度",
        "button_label": "开始法务分析",
        "role_hint": (
            "分析范围：诉讼风险、合同违约与纠纷、知识产权纠纷、合规性。"
            "建议提供合同文本、诉讼台账、知识产权清单和合规文件。"
        ),
        "dimensions": AVAILABLE_LEGAL_DIMENSIONS,
    },
    "business": {
        "tab_label": "业务专家",
        "dimension_label": "请选择业务分析维度",
        "button_label": "开始业务分析",
        "role_hint": (
            "分析范围：竞争格局、上下游依赖度、商业模式、行业政策风险。"
            "建议提供商业计划书、行业研究、客户供应商资料和政策文件。"
        ),
        "dimensions": AVAILABLE_BUSINESS_DIMENSIONS,
    },
}


@st.cache_data(
    ttl=COMPANY_LIST_CACHE_TTL_SECONDS,
    show_spinner=False,
)
def _load_a_share_company_options() -> list[str]:
    """
    加载沪深 A 股公司下拉选项，并缓存 24 小时。

    返回：
        list[str]: 形如 ["000001 - 平安银行", "600519 - 贵州茅台"]。

    说明：
        - 真正的取数和过滤逻辑放在 utils/stock_list.py，
          这样可以在不启动网页的情况下单独测试；
        - 这里只加 Streamlit 的缓存，避免每次页面刷新都请求 AKShare；
        - 缓存时间 24 小时；新股上市或公司更名最多一天后自动更新。
    """
    return fetch_a_share_company_options()


def _load_company_options_safe() -> tuple[list[str], str]:
    """
    安全加载公司列表：成功返回选项，失败返回中文错误说明。

    返回：
        tuple[list[str], str]:
            - 第一个值是下拉框选项；加载失败时为空列表；
            - 第二个值是错误提示；成功时为空字符串。

    说明：
        网络或 AKShare 接口临时失败时，不让整个网页报错崩溃，
        而是让页面显示中文提示，并提供“重新加载”按钮和手动输入代码的兜底入口。
    """
    try:
        return _load_a_share_company_options(), ""
    except Exception:
        # 不展示英文堆栈，也不暴露内部细节；
        # 用户只需要知道“暂时加载失败、可以重试或手动输入代码”。
        return [], (
            "暂时无法加载 A 股公司列表，可能是网络或 AKShare 接口"
            "暂时不可用。请检查网络后点击“重新加载公司列表”，"
            "或在下方手动输入 6 位股票代码。"
        )


def _load_source_text(
    input_mode: str,
    uploaded_file,
    pasted_text: str,
) -> tuple[str | None, str]:
    """
    根据当前输入方式，取得本次要发送给智能体的上传或粘贴文本。

    参数：
        input_mode: 上传文件或粘贴文字模式。
        uploaded_file: Streamlit 上传组件返回的文件对象，未上传时为 None。
        pasted_text: 用户在文本框里粘贴的原始文字。

    返回：
        tuple[str | None, str]:
            - 第一个值是清理后的文本；出现错误时为 None；
            - 第二个值是资料来源名称，用于结果页标注。

    说明：
        - 文件读取和字符数校验都放在这里，三位专家共用同一套规则；
        - 出现错误时直接调用 st.error 显示中文提示，避免每个页签重复写代码；
        - 公司查询模式不经过本函数，年报数据包由 _load_annual_report_text 处理。
    """
    if input_mode == INPUT_MODE_UPLOAD:
        if uploaded_file is None:
            st.error("请先上传一份 PDF 或 TXT 文件。")
            return None, ""

        try:
            with st.spinner("正在读取文件并提取文字……"):
                # 工具函数会根据 .pdf / .txt 后缀自动选择读取方式。
                source_text = extract_text_from_uploaded_file(
                    uploaded_file.name,
                    uploaded_file.getvalue(),
                )
        except FileTextExtractionError as exc:
            # 文件为空、损坏、加密、编码异常或没有文字层时，显示中文提示。
            st.error(str(exc))
            return None, ""

        source_name = uploaded_file.name
    else:
        # 粘贴模式下去掉首尾空白，避免只有空格的内容触发无效调用。
        source_text = pasted_text.strip()
        source_name = "粘贴的文字"

        if not source_text:
            st.error("请先粘贴需要分析的尽调文本。")
            return None, ""

    # 超过上限时不截断，而是明确要求用户拆分材料。
    # 悄悄截断可能丢失关键事实，进而产生错误结论。
    if len(source_text) > MAX_INPUT_CHARS:
        st.error(
            f"当前资料共 {len(source_text):,} 个字符，"
            f"超过单次上限 {MAX_INPUT_CHARS:,} 个字符。"
            "请拆分资料后分批分析。"
        )
        return None, ""

    return source_text, source_name


def _statement_rows_to_csv(
    rows: list[dict[str, str]],
    years: list[int],
) -> str:
    """
    把一张财务报表转换成 CSV 文本。

    参数：
        rows: 表格行，例如 [{"项目": "货币资金", "2025": "516.91"}]。
        years: 三个会计年度，从新到旧。

    返回：
        str: 带表头的 CSV 文本。调用方会用 utf-8-sig 编码下载，
             这样 Excel 打开时中文不会乱码。

    说明：
        这里只负责拼文字，不写文件；下载按钮拿到字节后由浏览器保存。
    """
    output = io.StringIO()
    writer = csv.writer(output)
    headers = ["项目"] + [str(year) for year in years]
    writer.writerow(headers)

    for row in rows:
        writer.writerow(
            [
                row.get(header, "")
                for header in headers
            ]
        )

    return output.getvalue()


def _render_financial_statements(payload: dict) -> None:
    """
    在公司查询模块中展示近三年三张表。

    参数：
        payload: ThreeYearStatements.to_dict() 产生的普通字典。

    说明：
        - 三张表放在三个子页签里，避免一次铺开几百行；
        - 每张表都提供 CSV 下载，方便用户自己核对或做二次分析；
        - 表格只展示接口三年内有数字的科目，没有数据的科目不显示，
          但接口本身缺失的年份会在表格下方明确提示。
    """
    years = [
        int(year)
        for year in payload.get("years", [])
        if str(year).isdigit()
    ]
    tables = payload.get("tables", {})
    if not isinstance(tables, dict):
        tables = {}

    st.markdown("### 近三年财务报表（AKShare）")
    st.caption(
        "数据来源：AKShare 的新浪财经接口。"
        "金额单位为亿元；基本每股收益、稀释每股收益为元/股。"
        "这是第三方整理数据，正式尽调结论请以上方巨潮年报原文为准。"
    )

    if payload.get("from_cache"):
        st.caption(
            "本次使用 24 小时内的三张表缓存（"
            f"{payload.get('fetched_at', '')} 获取）。"
        )
    elif payload.get("fetched_at"):
        st.caption(
            f"本次获取时间：{payload.get('fetched_at', '')}"
        )

    global_warnings = payload.get("warnings", [])
    if isinstance(global_warnings, list):
        for warning in global_warnings:
            if warning:
                st.warning(str(warning))

    # 三张表的顺序固定：资产负债表 → 利润表 → 现金流量表。
    statement_tabs = st.tabs(
        ["资产负债表", "利润表", "现金流量表"]
    )

    for tab, key in zip(
        statement_tabs,
        ("balance", "income", "cash_flow"),
    ):
        table = tables.get(key, {})
        if not isinstance(table, dict):
            table = {}

        title = str(table.get("title", key))
        rows = table.get("rows", [])
        if not isinstance(rows, list):
            rows = []

        with tab:
            if not rows:
                st.info(
                    f"{title}本次没有取到可展示的科目。"
                    "可以重新点击“获取官方年报与三年报表”重试。"
                )
                table_warnings = table.get("warnings", [])
                if isinstance(table_warnings, list):
                    for warning in table_warnings:
                        if warning:
                            st.caption(str(warning))
                continue

            table_years = [
                int(year)
                for year in table.get("years", years)
                if str(year).isdigit()
            ] or years

            # 行数多时给一个较高的滚动区域，避免页面被一张表撑得过长；
            # 行数少时自动缩短，不留下大片空白。
            table_height = min(
                700,
                max(320, 34 * len(rows) + 42),
            )

            st.dataframe(
                rows,
                use_container_width=True,
                hide_index=True,
                height=table_height,
            )
            st.caption(
                f"共 {len(rows)} 个有数据的科目。"
                "“-”表示接口没有返回该年份数字。"
            )

            csv_text = _statement_rows_to_csv(
                rows,
                table_years,
            )
            st.download_button(
                f"⬇️ 下载{title} CSV",
                data=csv_text.encode("utf-8-sig"),
                file_name=(
                    f"{payload.get('code', '')}_"
                    f"{key}_{table_years[0]}_"
                    f"{table_years[-1]}.csv"
                ),
                mime="text/csv",
                key=(
                    f"download_statement_{payload.get('code', '')}_"
                    f"{payload.get('year', '')}_{key}"
                ),
            )

            table_warnings = table.get("warnings", [])
            if isinstance(table_warnings, list):
                for warning in table_warnings:
                    if warning:
                        st.caption(str(warning))


def _annual_report_ready(
    stock_code: str,
    year: int,
    annual_report_entry: dict | None,
) -> bool:
    """
    判断当前是否已经有一份可以用于分析的官方年报数据包。

    参数：
        stock_code: 公司下拉框对应的股票代码。
        year: 年份下拉框中的会计年度。
        annual_report_entry: session_state 中缓存的年报结果。

    返回：
        bool: 代码、年份匹配，且数据包不为空时返回 True。

    说明：
        这个函数只做判断，不显示错误，供“分析按钮是否可点击”使用。
        真正点击分析时，_load_annual_report_text 会给出具体中文提示。
    """
    if not isinstance(annual_report_entry, dict):
        return False

    try:
        normalized_code = normalize_report_code(stock_code)
    except AnnualReportError:
        return False

    return (
        normalized_code == annual_report_entry.get("code")
        and year == annual_report_entry.get("requested_year")
        and isinstance(
            annual_report_entry.get("agent_context"),
            str,
        )
        and bool(
            annual_report_entry.get("agent_context", "").strip()
        )
    )


def _load_annual_report_text(
    stock_code: str,
    year: int,
    annual_report_entry: dict | None,
) -> tuple[str | None, str]:
    """
    取出缓存的官方年报数据包，并校验它是否可以用于专家分析。

    参数：
        stock_code: 公司下拉框对应的股票代码。
        year: 年份下拉框中的会计年度。
        annual_report_entry: session_state 中缓存的年报结果。

    返回：
        tuple[str | None, str]:
            - 第一个值是给专家使用的年报数据包；不可用时为 None；
            - 第二个值是资料来源名称，例如“巨潮官方年报：贵州茅台……”。

    说明：
        年报只需获取一次，三位专家共用同一份数据包。
        如果用户修改了股票代码或年份，必须重新获取，避免拿旧公司
        的年报去回答新公司的问题。
    """
    if not isinstance(annual_report_entry, dict):
        st.error(
            "请先在 Tab 2“公司查询”中选择公司并点击“获取官方年报与三年报表”。"
        )
        return None, ""

    try:
        normalized_code = normalize_report_code(stock_code)
    except AnnualReportError as exc:
        st.error(str(exc))
        return None, ""

    if (
        normalized_code != annual_report_entry.get("code")
        or year != annual_report_entry.get("requested_year")
    ):
        st.error(
            "股票代码或年份已修改，请重新点击“获取官方年报与三年报表”后再分析。"
        )
        return None, ""

    agent_context = annual_report_entry.get("agent_context", "")
    if not isinstance(agent_context, str) or not agent_context.strip():
        st.error(
            "年报数据包为空，请重新获取官方年报与三年报表。"
        )
        return None, ""

    source_name = annual_report_entry.get("source_name", "")
    if not isinstance(source_name, str) or not source_name.strip():
        source_name = (
            f"巨潮官方年报：{annual_report_entry.get('company_name', '')}"
            f"（{annual_report_entry.get('code', '')}）"
            f"{annual_report_entry.get('year', '')} 年年度报告"
        )

    return agent_context, source_name


def _build_finance_trend_summary(
    annual_report_entry: dict | None,
) -> str:
    """
    从缓存的三年三张表中生成给财务专家的趋势摘要。

    参数：
        annual_report_entry: session_state 中缓存的官方年报结果，
                             其中 financial_statements 是三年三张表字典。

    返回：
        str: 纯 Python 计算的趋势摘要；没有三张表或计算失败时返回空字符串。

    说明：
        - 摘要只在点击“分析”时临时计算，不写回缓存，避免缓存结构变更；
        - 三张表本身已经换算成亿元，build_trend_raw_data 会还原成数字；
        - 计算失败不阻断分析：财务专家会退回到原来的单年资料分析模式，
          不会因为一个辅助功能让整个按钮不可用。
    """
    if not isinstance(annual_report_entry, dict):
        return ""

    statements_payload = annual_report_entry.get(
        "financial_statements"
    )
    if (
        not isinstance(statements_payload, dict)
        or not statements_payload.get("tables")
    ):
        return ""

    try:
        raw_data = build_trend_raw_data(statements_payload)
        return calculate_trends(raw_data)
    except Exception:
        # 趋势摘要属于增强功能，失败时静默退回原有分析流程。
        # 不把内部异常展示给用户，也不影响年报和专家分析。
        return ""


def _get_finance_trend_years(
    annual_report_entry: dict | None,
) -> list[int]:
    """
    从缓存的三年三张表中取出实际会计年度。

    参数：
        annual_report_entry: session_state 中缓存的官方年报结果。

    返回：
        list[int]: 升序的三个年份，例如 [2023, 2024, 2025]；
                   没有三张表或年份不足三年时返回空列表。

    说明：
        财务专家需要用这三个年份生成“2023 | 2024 | 2025”表头。
        年份不足三年时返回空列表，让财务专家退回“按材料填写年份”的模式，
        不编造不存在的年份。
    """
    if not isinstance(annual_report_entry, dict):
        return []

    statements_payload = annual_report_entry.get(
        "financial_statements"
    )
    if not isinstance(statements_payload, dict):
        return []

    raw_years = statements_payload.get("years", [])
    if not isinstance(raw_years, (list, tuple)):
        return []

    normalized: set[int] = set()
    for value in raw_years:
        try:
            year = int(value)
        except (TypeError, ValueError):
            continue
        if 1900 <= year <= 2100:
            normalized.add(year)

    if len(normalized) < 3:
        return []
    return sorted(normalized)[-3:]


# Windows 文件名中不允许出现的字符。
# 公司名称或上传文件名里如果带这些字符，浏览器可能无法保存下载文件。
_INVALID_FILE_NAME_CHARS = '\\/:*?"<>|'


def _build_report_file_name(report_label: str, company_name: str) -> str:
    """
    生成带公司名的 PDF 下载文件名。

    参数：
        report_label: 报告类型，例如“法务尽调简报”。
        company_name: 公司简称或上传文件名；为空时使用“未命名公司”。

    返回：
        str: 形如“【法务尽调简报】贵州茅台.pdf”的文件名。

    说明：
        Windows 不允许文件名包含 \\ / : * ? " < > | 等字符，
        这里先清理再拼接，避免点击下载后浏览器报错。
    """
    cleaned = (company_name or "").strip()
    for char in _INVALID_FILE_NAME_CHARS:
        cleaned = cleaned.replace(char, "")

    # 文件名过长在部分系统上会保存失败，这里保留前 60 个字符。
    cleaned = cleaned[:60].strip() or "未命名公司"
    return f"【{report_label}】{cleaned}.pdf"


def _extract_company_name_from_source_name(source_name: str) -> str:
    """
    从资料来源名称中尽量提取公司名。

    参数：
        source_name: 结果页保存的资料来源名称。

    返回：
        str: 提取到的公司名；无法判断时返回空字符串。

    说明：
        新版分析结果会把公司名单独保存；这个函数只用于兼容旧结果，
        避免用户刷新前已经生成的报告没有公司名可用。
    """
    text = (source_name or "").strip()
    if not text:
        return ""

    # 年报来源格式：“巨潮官方年报：贵州茅台（600519）2025 年年度报告……”
    prefix = "巨潮官方年报："
    if text.startswith(prefix):
        remainder = text[len(prefix):].strip()
        for separator in ("（", "("):
            if separator in remainder:
                return remainder.split(separator, 1)[0].strip()
        return remainder.split(" ", 1)[0].strip()

    # 上传文件：去掉 .pdf / .txt 后缀，作为可识别的报告名称。
    if text.lower().endswith((".pdf", ".txt")):
        return Path(text).stem.strip()

    return ""


@st.cache_data(show_spinner=False, max_entries=12)
def _resolve_report_company_name(
    input_mode: str,
    source_kind: str,
    source_name: str,
    annual_report_entry: dict | None,
) -> str:
    """
    取本次报告对应的公司名称，用于公开搜索和 PDF 文件名。

    参数：
        input_mode: 当前材料输入方式。
        source_kind: "annual_report" 或 "upload"。
        source_name: 资料来源名称，例如上传文件名。
        annual_report_entry: 公司查询模式下缓存的官方年报结果。

    返回：
        str: 公司名称；无法识别时返回空字符串。

    说明：
        - 公司查询模式优先使用巨潮返回的公司简称；
        - 上传模式使用去掉扩展名的文件名；
        - 粘贴模式没有可靠公司名，返回空字符串，调用方会跳过公开搜索。
    """
    if source_kind == "annual_report" and isinstance(
        annual_report_entry, dict
    ):
        raw_company_name = annual_report_entry.get("company_name", "")
        if isinstance(raw_company_name, str) and raw_company_name.strip():
            return raw_company_name.strip()

    if input_mode == INPUT_MODE_UPLOAD and isinstance(source_name, str):
        cleaned_name = Path(source_name).stem.strip()
        if cleaned_name:
            return cleaned_name

    return ""


@st.cache_data(show_spinner=False, max_entries=12)
def _generate_pdf_bytes(report_text: str, company_name: str) -> bytes:
    """
    生成 PDF 字节流，并按“报告文字 + 公司名”缓存。

    参数：
        report_text: 专家生成的 Markdown 报告。
        company_name: 公司名称，用于 PDF 顶部标题；没有公司名时传空字符串。

    返回：
        bytes: 可直接交给下载按钮的 PDF 内容。

    说明：
        Streamlit 每次交互都会重新执行页面代码；三位专家同时有结果时，
        如果不缓存，每次点击都会重新排版三份 PDF，页面会明显变慢。
        报告文字和公司名不变时直接复用缓存，内容变化后自动重新生成。
    """
    # 延迟导入：只有真正需要下载 PDF 时才加载排版库。
    # 这样即使电脑缺少 fpdf2 / matplotlib，网页其他部分仍能正常使用。
    from utils.pdf_generator import generate_pdf

    return generate_pdf(report_text, company_name)


def _render_pdf_download_button(
    key: str,
    report_text: str,
    file_name: str,
    button_label: str,
    company_name: str = "",
) -> None:
    """
    在专家报告下方显示 PDF 下载按钮。

    参数：
        key: Streamlit 控件唯一键，避免三位专家的按钮互相冲突。
        report_text: 专家生成的 Markdown 报告；为空时不显示按钮。
        file_name: 浏览器保存时使用的文件名。
        button_label: 按钮上显示的文字。
        company_name: 公司名称，用于 PDF 顶部标题；没有时传空字符串。

    说明：
        PDF 生成失败只影响下载按钮，不会影响上方已经显示的网页报告。
    """
    if not report_text.strip():
        return

    try:
        # 只导入异常类型；真正的生成函数放在带缓存的 _generate_pdf_bytes 里。
        from utils.pdf_generator import PdfGenerationError
    except ImportError:
        st.warning(
            "PDF 导出组件尚未安装。请在项目虚拟环境中运行："
            "`python -m pip install -r requirements.txt`，"
            "安装完成后刷新页面。"
        )
        return

    try:
        with st.spinner("正在生成 PDF 文件..."):
            pdf_bytes = _generate_pdf_bytes(report_text, company_name)
    except PdfGenerationError as exc:
        # 字体缺失、排版失败等问题只影响 PDF 下载，
        # 上方的网页报告仍然保留，所以用黄色警告而不是红色报错。
        st.warning(str(exc))
        return

    st.download_button(
        label=button_label,
        data=pdf_bytes,
        file_name=file_name,
        mime="application/pdf",
        key=key,
        help="PDF 内容与上方报告一致，适合阅读、打印和转发。",
    )


def _strip_math_delimiters(text: str) -> str:
    r"""
    删除模型输出中可能出现的 LaTeX 数学分隔符 \( 和 \)。

    为什么需要这一步：
    大模型偶尔会把普通中文内容包在 \( ... \) 里。Streamlit 的 Markdown
    渲染器会把这对符号当成数学公式解析，网页上容易出现乱码或排版错位。
    这里只删除分隔符本身，保留括号里的公式内容，避免误删分析文字。

    参数：
        text: 模型返回的原始 Markdown 文本。

    返回：
        删除 \( 和 \) 之后的文本。传入非字符串时返回空字符串，
        防止调用方拿到 None 等异常值时直接报错。
    """
    if not isinstance(text, str):
        return ""
    # 正则含义：匹配一个反斜杠，后面紧跟左括号或右括号。
    # 只替换这两个分隔符，不碰普通括号和括号内的公式内容。
    return re.sub(r"\\[()]", "", text)


def _mark_analysis_attempted() -> None:
    """
    点击任意一位专家的“开始分析”按钮时，记录“已经开始过分析”。

    说明：
        Streamlit 的按钮回调会在页面重新运行之前执行。
        因此首页的空状态引导可以在本轮渲染时就判断出“用户已经点击过”，
        立即隐藏，不会和 st.status 加载动画同时出现。
        这里只记录一个界面状态，不参与任何分析计算。
    """
    st.session_state[ANALYSIS_ATTEMPTED_STATE_KEY] = True


def _render_agent_panel(
    agent_key: str,
    config: dict,
    api_key_ready: bool,
    input_mode: str,
    uploaded_file,
    pasted_text: str,
    annual_report_entry: dict | None,
    annual_stock_code: str,
    annual_report_year: int,
) -> None:
    """
    渲染一位专家的维度选择、分析按钮和结果区域。

    参数：
        agent_key: 专家标识，例如 "finance"、"legal"、"business"，
                   用于给控件和结果设置互不冲突的键。
        config: 这位专家的页面配置，来自 AGENT_CONFIGS。
        api_key_ready: 是否已经配置 DeepSeek API Key。
        input_mode: 当前材料输入方式。
        uploaded_file: 用户上传的文件对象；未上传时为 None。
        pasted_text: 用户粘贴的文字。
        annual_report_entry: 公司查询模式下缓存的官方年报结果。
        annual_stock_code: 公司查询模式下的股票代码。
        annual_report_year: 公司查询模式下选择的会计年度。

    说明：
        三个页签共用同一个上传文件或同一份年报数据包，
        但每位专家有独立的维度选择、分析按钮和 session_state 结果，
        所以互相不会覆盖。
    """
    tab_label = config["tab_label"]

    if input_mode == INPUT_MODE_ANNUAL_REPORT:
        # 官方年报是正式公开披露材料，按严格尽调模式分析；
        # 但年报中的数字由程序提取，仍需要提醒用户以原文为准。
        st.caption(
            "当前材料来源：巨潮资讯网官方年度报告，"
            "并附带 AKShare 新浪财经的近三年三张表。"
            "年报数字由程序从 PDF 提取并保留页码，三张表属于第三方整理数据；"
            "分析结果仍请以巨潮原文为准。"
        )
    else:
        # 用一句话说明这位专家看什么、建议提供什么材料。
        st.caption(config["role_hint"])

    # 每位专家使用独立的控件 key，避免三个页签中的同名控件发生冲突。
    selected_dimensions = st.multiselect(
        config["dimension_label"],
        options=list(config["dimensions"]),
        default=list(config["dimensions"]),
        key=f"dimensions_{agent_key}",
        help="默认全选。取消勾选可以缩短分析报告，并减少不需要的维度。",
    )

    if selected_dimensions:
        st.caption(f"本次将分析 {len(selected_dimensions)} 个维度。")
    else:
        st.caption("请至少选择一个分析维度。")

    # 不同材料来源有各自的“准备完成”条件：
    # - 公司查询：必须有与所选公司、年份匹配的官方年报数据包；
    # - 上传/粘贴：点击分析时再读取和校验。
    source_ready = True
    if input_mode == INPUT_MODE_ANNUAL_REPORT:
        source_ready = _annual_report_ready(
            annual_stock_code,
            annual_report_year,
            annual_report_entry,
        )
        if not source_ready:
            st.info(
                "请先在“公司查询”中选择公司并完成"
                "“获取官方年报与三年报表”，然后再点击本专家的分析按钮。"
            )

    # 没有 Key、没有选择维度、或材料未准备好时禁用按钮，
    # 避免无效点击和无效调用。
    analyze_clicked = st.button(
        config["button_label"],
        type="primary",
        use_container_width=True,
        disabled=(
            not api_key_ready
            or not selected_dimensions
            or not source_ready
        ),
        key=f"analyze_{agent_key}",
        # 点击时先记录“已经尝试分析”，用于隐藏首页空状态引导。
        # 回调只改界面状态，不参与分析逻辑。
        on_click=_mark_analysis_attempted,
    )

    if not api_key_ready:
        st.caption("配置好 DeepSeek API Key 并重启网页后，此按钮才可使用。")

    if analyze_clicked:
        # 每次开始新一轮分析前，先清除这位专家的旧结果，
        # 避免新问题和旧结果混在一起。其他专家的结果不受影响。
        agent_results = dict(st.session_state.get("agent_results", {}))
        agent_results.pop(agent_key, None)
        st.session_state["agent_results"] = agent_results

        if input_mode == INPUT_MODE_ANNUAL_REPORT:
            source_text, source_name = _load_annual_report_text(
                stock_code=annual_stock_code,
                year=annual_report_year,
                annual_report_entry=annual_report_entry,
            )
            source_kind = "annual_report"
        else:
            source_text, source_name = _load_source_text(
                input_mode=input_mode,
                uploaded_file=uploaded_file,
                pasted_text=pasted_text,
            )
            source_kind = "upload"

        if source_text is None:
            # 上面的读取函数已经显示过具体错误，这里不再重复提示。
            return

        # 记录本次报告对应的公司名，供公开搜索和 PDF 文件名使用。
        # 这份名称必须在分析前确定，因为法务、业务专家需要用它拼搜索词。
        report_company_name = _resolve_report_company_name(
            input_mode=input_mode,
            source_kind=source_kind,
            source_name=source_name,
            annual_report_entry=annual_report_entry,
        )

        # 只有法务、业务专家需要补充公开网络搜索；
        # 财务专家已经有年报和三年报表，不需要再搜网页。
        # 粘贴模式识别不到公司名，因此不启用搜索，避免用错误名称查询。
        web_search_enabled = (
            agent_key in ("legal", "business")
            and bool(report_company_name)
        )

        # 财务专家专属：从三年三张表生成 Python 趋势摘要。
        # 法务、业务专家不需要这些数字，所以只有 agent_key == "finance"
        # 且材料来自官方年报时才计算。
        finance_trend_summary = ""
        finance_trend_years: list[int] = []
        if agent_key == "finance" and source_kind == "annual_report":
            finance_trend_summary = _build_finance_trend_summary(
                annual_report_entry
            )
            # 把三张表的实际年份一起交给财务专家，
            # 让报告开头的三大报表表头固定为 2023/2024/2025 三列。
            finance_trend_years = _get_finance_trend_years(
                annual_report_entry
            )

        # 用 st.status 取代原来的单一 spinner。
        # 分析本身仍然只调用一次 analyze_uploaded_text，
        # 这里只在调用前后更新界面上的阶段文字，不改变任何分析逻辑。
        try:
            with st.status(
                "正在启动尽调引擎...",
                expanded=True,
            ) as status:
                # 阶段一：材料已经读取完成，准备进入分析。
                # 不同材料来源给出不同的说明，让用户知道当前在处理什么。
                if web_search_enabled:
                    status.update(label="正在抓取数据...")
                    status.write(
                        "正在为法务/业务专家检索公开网络信息……"
                    )
                elif source_kind == "annual_report":
                    status.update(label="正在读取年报数据...")
                    status.write(
                        "已加载巨潮官方年报和近三年三张表，准备分析。"
                    )
                else:
                    status.update(label="正在读取分析材料...")
                    status.write(
                        "已读取本轮上传或粘贴的材料，准备提交给分析引擎。"
                    )

                # 阶段二：进入专家分析。
                # 这个调用会持续几十秒，期间状态框会一直显示
                # “财务专家分析中...”这样的文字，避免用户以为页面卡住。
                status.update(label=f"{tab_label}分析中...")

                try:
                    # 上传、粘贴或官方年报都走严格尽调模式；
                    # 年报数据包已经包含结构化指标和原文摘录。
                    # 法务、业务会额外收到一条公开网络搜索摘要。
                    orchestration = analyze_uploaded_text(
                        text=source_text,
                        agents=[agent_key],
                        dimensions_by_agent={
                            agent_key: list(selected_dimensions)
                        },
                        source_name=source_name,
                        finance_trend_summary=finance_trend_summary,
                        finance_trend_years=finance_trend_years,
                        company_name=report_company_name,
                        enable_web_search=web_search_enabled,
                    )
                except OrchestratorError:
                    # 参数错误（例如资料为空、维度配置异常）时，
                    # 先把加载框标成失败，再交给外层统一显示中文错误。
                    status.update(
                        label="分析失败",
                        state="error",
                        expanded=True,
                    )
                    raise
                except Exception:
                    # 未预期异常也先把状态框标成失败，
                    # 避免页面报错后仍停留在“分析中...”的假状态。
                    # 这里不吞异常，继续向上抛出，保持原有错误处理行为。
                    status.update(
                        label="分析出现异常",
                        state="error",
                        expanded=True,
                    )
                    raise

                # 阶段三：分析完成。
                status.update(
                    label="分析完成！",
                    state="complete",
                    expanded=False,
                )
        except OrchestratorError as exc:
            # 参数错误（例如资料为空、维度配置异常）在这里展示。
            st.error(str(exc))
            return

        # 调度器会隔离单个专家的失败，因此这里检查是否拿到了结果。
        agent_result = orchestration.get("results", {}).get(agent_key)
        if not agent_result:
            # 引擎调用已经结束，但没有拿到可用结果；
            # 同步把状态框标成失败，避免上方仍显示“分析完成！”。
            status.update(
                label="分析未取得结果",
                state="error",
                expanded=True,
            )
            error_message = orchestration.get("errors", {}).get(
                agent_key,
                orchestration.get("message", "分析失败，请稍后重试。"),
            )
            st.error(error_message)
            return

        # 取出本次公开搜索的状态，供结果区域展示。
        # 搜索失败不会让分析失败，但必须让用户知道本次没有用上网络信息。
        web_search_meta: dict = {}
        raw_web_search_meta = orchestration.get("web_search", {})
        if isinstance(raw_web_search_meta, dict):
            candidate_meta = raw_web_search_meta.get(agent_key, {})
            if isinstance(candidate_meta, dict):
                web_search_meta = candidate_meta

        # 结果按专家分别保存。
        # 这样切换页签后仍能看到各自的结果，也不必重复调用 API。
        agent_results = dict(st.session_state.get("agent_results", {}))
        agent_results[agent_key] = {
            "result": agent_result["result"],
            "source_name": source_name,
            "source_text": source_text,
            "dimensions": list(selected_dimensions),
            "source_kind": source_kind,
            # 保存公司名，确保 PDF 文件名与本次分析的公司一致；
            # 即使之后又查询了别的公司，旧报告也不会被改名。
            "company_name": report_company_name,
            # 保存本次实际使用的趋势摘要，方便排查“模型有没有自己乱算”。
            # 当前只在内部保存，不直接展示在页面上。
            "trend_summary": finance_trend_summary,
            # 保存公开搜索状态和摘要，刷新页面后仍能核对本次用了哪些来源。
            "web_search": web_search_meta,
        }
        st.session_state["agent_results"] = agent_results

    # 读取这位专家最近一次成功分析的结果。
    saved_entry = st.session_state.get("agent_results", {}).get(agent_key)

    if not saved_entry:
        st.caption("点击上面的按钮后，分析结果会显示在这里。")
        return

    st.divider()
    st.subheader(f"{tab_label}分析结果")

    saved_source_name = saved_entry.get("source_name", "未记录")
    saved_source_text = saved_entry.get("source_text", "")
    saved_dimensions = saved_entry.get("dimensions", [])
    saved_source_kind = saved_entry.get("source_kind", "upload")

    # 下载文件名中的公司名：优先使用分析时保存的值；
    # 如果页面刷新前已经生成过旧结果，则尝试从资料来源名称中补取。
    saved_company_name = saved_entry.get("company_name", "")
    if not isinstance(saved_company_name, str) or not saved_company_name.strip():
        saved_company_name = _extract_company_name_from_source_name(
            saved_source_name
        )

    st.caption(f"资料来源：{saved_source_name}")
    if saved_source_kind == "annual_report":
        st.caption(
            "分析模式：官方年报。核心数字由程序从巨潮 PDF 提取，"
            "并保留原文页码；结果仍请以巨潮原文为准。"
        )
    if saved_dimensions:
        st.caption("本次分析维度：" + "、".join(saved_dimensions))

    # 展示本次公开网络搜索的状态。
    # 成功时给出搜索词和摘要原文，方便核对模型引用的来源；
    # 失败时明确说明“本次只用了正式材料”，避免用户误以为搜过了。
    saved_web_search = saved_entry.get("web_search", {})
    if isinstance(saved_web_search, dict) and saved_web_search:
        search_status = saved_web_search.get("status", "")
        search_query = saved_web_search.get("query", "")
        search_summary = saved_web_search.get("summary", "")

        if search_status == "ok":
            st.caption(
                "公开网络搜索：已纳入网页摘要"
                + (f"（搜索词：{search_query}）" if search_query else "")
                + "。"
            )
            if isinstance(search_summary, str) and search_summary.strip():
                with st.expander("查看本次使用的公开网络搜索摘要"):
                    st.text_area(
                        "公开网络搜索摘要",
                        value=search_summary,
                        height=260,
                        disabled=True,
                        label_visibility="collapsed",
                        key=f"web_search_preview_{agent_key}",
                    )
        elif search_status == "failed":
            st.warning(
                "本次公开网络搜索未取得可用结果，分析仅基于正式材料；"
                "报告不会声称已经检索过公开网络信息。"
            )
            if isinstance(search_summary, str) and search_summary.strip():
                with st.expander("查看公开网络搜索失败原因"):
                    st.text_area(
                        "搜索失败原因",
                        value=search_summary,
                        height=180,
                        disabled=True,
                        label_visibility="collapsed",
                        key=f"web_search_error_{agent_key}",
                    )
        elif search_status == "skipped":
            st.caption(
                "本次未启用公开网络搜索：未识别到公司名称。"
            )

    # 让大模型生成的 Markdown 按格式化内容展示。
    # 业务和法务专家现在都在正文中内嵌标准 Markdown 表格，
    # Streamlit 会直接按表格渲染，不需要额外的图表组件。
    # 展示前先清掉 \( 和 \) 这两个数学分隔符，避免网页把它们当作公式
    # 解析后出现乱码；清洗后的文本同时交给下方 PDF 下载，保证两处一致。
    result_text = _strip_math_delimiters(saved_entry.get("result", ""))
    st.markdown(result_text)

    # 三位专家都在报告下方提供 PDF 下载。
    # 财务沿用原来的文件名和按钮文字，法务、业务按本次要求带上公司名。
    if agent_key == "finance":
        pdf_file_name = "财务尽调报告.pdf"
        pdf_button_label = "⬇️ 下载财务尽调报告 PDF"
    elif agent_key == "legal":
        pdf_file_name = _build_report_file_name(
            "法务尽调简报",
            saved_company_name,
        )
        pdf_button_label = "⬇️ 下载法务尽调简报 PDF"
    else:
        pdf_file_name = _build_report_file_name(
            "业务尽调简报",
            saved_company_name,
        )
        pdf_button_label = "⬇️ 下载业务尽调简报 PDF"

    # PDF 直接使用页面上的 Markdown 正文，
    # 内嵌的标准 Markdown 表格会转成三线表图片后嵌入，
    # 公司名用于 PDF 顶部标题。
    _render_pdf_download_button(
        key=f"download_{agent_key}_pdf",
        report_text=result_text,
        file_name=pdf_file_name,
        button_label=pdf_button_label,
        company_name=saved_company_name,
    )

    # 原文默认收起，避免长文本挤占结果页面；
    # 但保留可展开预览，方便核对模型引用的数据是否真的来自材料。
    if saved_source_kind == "annual_report":
        preview_label = "查看本次分析使用的年报数据包"
    else:
        preview_label = "查看本次分析使用的原文"
    with st.expander(
        f"{preview_label}（共 {len(saved_source_text):,} 个字符）"
    ):
        st.text_area(
            "材料内容预览",
            value=saved_source_text,
            height=320,
            disabled=True,
            label_visibility="collapsed",
            key=f"source_preview_{agent_key}",
        )


# ==================== 历史报告读取与展示 ====================
# 这一组函数只负责三件事：
#   1. 扫描项目根目录下的 history_reports 文件夹；
#   2. 读取用户选中的 .md 或 .pdf 文件；
#   3. 在主区域展示内容。
# 它们不调用任何 Agent 分析函数，也不会修改分析结果、上传文件或发送网络请求。

def _format_file_size(size_bytes: int) -> str:
    """
    把字节数转成方便阅读的文件大小文字。

    参数：
        size_bytes: 文件大小，单位是字节。

    返回：
        str: 例如 "860 B"、"12.4 KB"、"1.8 MB"。

    说明：
        只用于历史报告列表下方的一行说明，不影响文件读取本身。
    """
    if size_bytes < 1024:
        return f"{size_bytes} B"
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    return f"{size_bytes / (1024 * 1024):.1f} MB"


def _list_history_reports(directory: Path) -> list[Path]:
    """
    扫描历史报告文件夹，返回所有 .md 和 .pdf 文件。

    参数：
        directory: 历史报告文件夹路径，通常是项目根目录下的 history_reports。

    返回：
        list[Path]: 按修改时间从新到旧排列的文件路径；
                    文件夹不存在、没有权限或没有符合条件的文件时返回空列表。

    说明：
        - 只扫描当前这一层，不进入子文件夹，避免误读其他目录中的文件；
        - 后缀统一转成小写再比较，因此 .MD、.PDF 也能识别；
        - 扫描失败不抛异常，页面会显示“暂无历史记录”，
          不会因为一个本地文件夹权限问题让整个网页崩溃。
    """
    try:
        if not directory.is_dir():
            return []

        candidates = [
            path
            for path in directory.iterdir()
            if path.is_file()
            and path.suffix.lower() in HISTORY_REPORT_SUFFIXES
        ]
    except OSError:
        # 文件夹被占用、没有读取权限等情况都按“没有历史记录”处理。
        return []

    def _modified_time(path: Path) -> float:
        """安全地取文件修改时间；取不到时当作最旧的文件。"""
        try:
            return path.stat().st_mtime
        except OSError:
            return 0.0

    # 最近修改的报告排在最前面，用户通常更关心最新一份。
    candidates.sort(
        key=lambda path: (_modified_time(path), path.name.lower()),
        reverse=True,
    )
    return candidates


def _read_history_text(path: Path) -> str:
    """
    读取 Markdown 历史报告的文字内容。

    参数：
        path: 历史报告文件路径。

    返回：
        str: 文件文字内容。

    异常：
        OSError: 文件无法读取时抛出，由调用方转成中文页面提示。

    说明：
        历史报告可能来自不同电脑或不同软件，编码不一定统一。
        这里先按 UTF-8（兼容带 BOM 的 UTF-8）读取，
        失败后再尝试 Windows 中文环境常见的 GB18030；
        两种都失败时用替换字符兜底，保证页面不会因为一个坏字节直接报错。
    """
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            # 当前编码不合适，换下一种编码继续尝试。
            continue
        except OSError as exc:
            raise OSError("无法读取该历史报告文件。") from exc

    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise OSError("无法读取该历史报告文件。") from exc


def _history_file_meta(path: Path) -> str:
    """
    生成历史报告的一行辅助信息：文件大小 + 最后修改时间。

    参数：
        path: 历史报告文件路径。

    返回：
        str: 例如 "12.4 KB · 修改于 2026-11-10 10:30"；
             文件信息取不到时返回空字符串。
    """
    try:
        stat = path.stat()
    except OSError:
        return ""

    modified_at = datetime.fromtimestamp(stat.st_mtime).strftime(
        "%Y-%m-%d %H:%M"
    )
    return f"{_format_file_size(stat.st_size)} · 修改于 {modified_at}"


def _render_history_report_preview(path: Path) -> None:
    """
    在主区域展示用户选中的历史报告。

    参数：
        path: 用户在侧边栏选中的历史报告路径。

    说明：
        - .md 文件：读取文字后按 Markdown 渲染，保留标题、列表和表格；
        - .pdf 文件：不在网页里逐页解析，只提供下载按钮，
          避免大文件拖慢页面，也避免重复实现 PDF 渲染；
        - 读取失败时只影响这一块预览，不影响下方任何分析功能。
    """
    suffix = path.suffix.lower()

    with st.container(border=True):
        st.subheader(f"📄 历史报告预览：{path.name}")

        meta = _history_file_meta(path)
        if meta:
            st.caption(meta)

        if suffix == ".md":
            try:
                content = _read_history_text(path)
            except OSError as exc:
                st.error(f"读取历史报告失败：{exc}")
                return

            if not content.strip():
                st.info("这份 Markdown 历史报告是空文件。")
                return

            st.caption("以下为 Markdown 报告的网页渲染效果。")
            st.markdown(content)
            return

        if suffix == ".pdf":
            st.caption(
                "PDF 报告不在网页内逐页显示，"
                "请下载后用 PDF 阅读器查看。"
            )
            try:
                pdf_bytes = path.read_bytes()
            except OSError as exc:
                st.error(f"读取历史 PDF 失败：{exc}")
                return

            st.download_button(
                "⬇️ 下载这份历史报告 PDF",
                data=pdf_bytes,
                file_name=path.name,
                mime="application/pdf",
                key=f"download_history_pdf_{path.name}",
            )
            return

        # 正常不会走到这里，因为扫描时已经限制了后缀；
        # 保留兜底提示，方便以后扩展文件类型时排查问题。
        st.warning("暂不支持预览这种文件类型。")


# ==================== 左侧边栏 ====================
# 侧边栏从上到下依次是：
#   1. 品牌标识；
#   2. 过往尽调报告（只扫描本地 history_reports 文件夹）；
#   3. 运行状态（原有内容，保持不变）；
#   4. 偏好设置（阅读模式，放在侧边栏最底部）。
# 这里只增加展示和文件读取，不调用任何 Agent 分析函数。
selected_history_path: Path | None = None

with st.sidebar:
    # ----- 边栏头部：品牌标识 -----
    st.markdown(
        '<div class="fdd-sidebar-brand">🌿 智投明鉴</div>',
        unsafe_allow_html=True,
    )
    st.caption("多智能体金融尽调平台")
    st.divider()

    # ----- 历史记录区 -----
    st.subheader("📂 过往尽调报告")
    history_reports = _list_history_reports(HISTORY_REPORTS_DIR)

    if not history_reports:
        # 文件夹不存在、为空或没有 md/pdf 时，都显示同一句提示，
        # 避免用户看到英文报错或一片空白。
        st.caption("暂无历史记录")
        st.caption(
            "把 .md 或 .pdf 报告放入项目根目录的 "
            "history_reports 文件夹后，刷新页面即可在这里选择。"
        )
    else:
        # 用“文件名 -> 文件路径”的映射取值，
        # 不根据用户输入拼接路径，从源头上避免 ../ 之类的路径穿越问题。
        history_by_name = {
            path.name: path
            for path in history_reports
        }
        selected_history_name = st.selectbox(
            "选择历史报告",
            options=list(history_by_name.keys()),
            index=None,
            placeholder="请选择一份报告",
            key="history_report_selectbox",
            help=(
                "只列出 history_reports 文件夹中的 .md 和 .pdf 文件，"
                "按修改时间从新到旧排列。"
            ),
        )
        if selected_history_name:
            selected_history_path = history_by_name.get(
                selected_history_name
            )
            if selected_history_path is not None:
                history_meta = _history_file_meta(
                    selected_history_path
                )
                if history_meta:
                    st.caption(history_meta)

        st.caption(
            f"共 {len(history_reports)} 份报告；"
            "选中后会在右侧主区域显示。"
        )

    st.divider()

    # ----- 运行状态（原有内容） -----
    st.header("运行状态")

    # 从环境变量判断 Key 是否存在。这里只判断“有没有值”，不打印 Key。
    api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    api_key_ready = bool(api_key)

    if api_key_ready:
        st.success("DeepSeek API Key 已配置")
    else:
        st.warning("尚未配置 DeepSeek API Key")
        st.caption(
            "请打开项目根目录的 .env 文件，"
            "在 DEEPSEEK_API_KEY= 后面填入真实 Key，然后重启网页。"
        )

    st.divider()
    st.caption(f"单次分析上限：{MAX_INPUT_CHARS:,} 个字符")
    st.caption(
        "支持格式：PDF、TXT；可搜索的沪深 A 股公司下拉框，"
        "直连巨潮年报，并获取近三年三张表"
    )
    st.caption("当前阶段：财务 / 法务 / 业务智能体")
    st.caption(
        "三位专家共用同一份上传资料"
        "或同一份官方年报 + 近三年三张表数据包。"
    )
    st.caption(
        "官方年报来自巨潮资讯网公开披露，三张表来自 AKShare 新浪财经接口，"
        "都不需要 API Key；北交所、港股、美股暂不支持。"
    )
    st.caption("分析文字会发送给 DeepSeek；请确认公司允许使用第三方大模型。")
    st.caption("提示：修改 .env 后需要重启网页服务才会生效。")

    st.divider()

    # ----- 设置区：固定在侧边栏最底部 -----
    # 使用 st.radio 做阅读模式切换。
    # 默认深色，与当前报告样式一致；选择浅色后才注入浅色 CSS。
    st.subheader("⚙️ 偏好设置")
    reading_mode = st.radio(
        "阅读模式",
        options=(READING_MODE_DARK, READING_MODE_LIGHT),
        index=0,
        horizontal=True,
        key="reading_mode",
        help=(
            "浅色模式为基础版：页面主区域、侧边栏和报告表格会变浅，"
            "少数 Streamlit 原生控件可能仍保留深色样式。"
        ),
    )

    if reading_mode == READING_MODE_LIGHT:
        st.caption("已切换到浅色模式（基础版）。")
        # 只有选择浅色时才输出这段样式；切回深色后样式标签消失，
        # 页面会恢复成原来的深色外观，不需要重启网页。
        st.markdown(
            "<style>" + _LIGHT_MODE_CSS + "</style>",
            unsafe_allow_html=True,
        )
    else:
        st.caption("已使用深色模式，与现有报告样式一致。")


# ==================== 主区域：历史报告预览 ====================
# 只有在侧边栏真正选中一份历史报告时才显示。
# 这段代码位于所有分析函数之外，不参与专家分析流程，
# 也不会改变当前材料来源或任何分析结果。
if selected_history_path is not None:
    _render_history_report_preview(selected_history_path)

# ==================== 第一部分：提供材料 ====================
st.header("1. 提供尽调资料或选择公司")

# 默认使用上传方式；用户切换材料来源后再更新。
if ACTIVE_INPUT_STATE_KEY not in st.session_state:
    st.session_state[ACTIVE_INPUT_STATE_KEY] = INPUT_MODE_UPLOAD

# 这个值决定下方三位专家分析哪一份材料。
# Tab 内的按钮点击后才能最终确定，因此先用占位符留出说明位置。
input_mode = st.session_state[ACTIVE_INPUT_STATE_KEY]
source_caption_placeholder = st.empty()

# 这些变量在页面重跑时都会重新初始化；
# 只有点击某位专家的“开始分析”按钮后，才会真正读取并校验材料。
uploaded_file = None
pasted_text = ""

# 官方年报直取相关的默认值。
# 年份默认取“上一年”，因为当年年报通常要到下一年 1～4 月才披露。
annual_report_entry = st.session_state.get(ANNUAL_REPORT_STATE_KEY)
annual_stock_code = ""
default_annual_year = datetime.now().year - 1
annual_report_year = default_annual_year
annual_year_options = list(
    range(default_annual_year, 1999, -1)
)

# 两个 Tab 每次都会同时渲染。
# 程序不根据“当前打开的是哪个 Tab”判断材料来源，
# 而是根据用户最后一次上传、粘贴或成功获取年报的动作，
# 记录在 session_state 中。
upload_tab, company_tab = st.tabs(
    [
        "📂 Tab 1：上传 / 粘贴资料",
        "🏢 Tab 2：公司查询",
    ]
)

with upload_tab:
    st.caption(
        "适合您手里已经有企业提供的财报、合同等资料。"
        "上传或粘贴后，三位专家都使用这一份材料。"
    )

    def _activate_upload_mode() -> None:
        """单选按钮变化时，把当前材料来源切到上传或粘贴。"""
        st.session_state[ACTIVE_INPUT_STATE_KEY] = st.session_state[
            UPLOAD_MODE_WIDGET_KEY
        ]

    def _activate_upload_file() -> None:
        """用户选择文件时，自动把材料来源切到“上传 PDF / TXT”。"""
        st.session_state[UPLOAD_MODE_WIDGET_KEY] = INPUT_MODE_UPLOAD
        st.session_state[ACTIVE_INPUT_STATE_KEY] = INPUT_MODE_UPLOAD

    def _activate_paste_text() -> None:
        """用户修改粘贴文字时，自动把材料来源切到“粘贴文字”。"""
        st.session_state[UPLOAD_MODE_WIDGET_KEY] = INPUT_MODE_PASTE
        st.session_state[ACTIVE_INPUT_STATE_KEY] = INPUT_MODE_PASTE

    upload_mode = st.radio(
        "资料输入方式",
        options=INPUT_MODES,
        horizontal=True,
        key=UPLOAD_MODE_WIDGET_KEY,
        on_change=_activate_upload_mode,
    )

    if upload_mode == INPUT_MODE_UPLOAD:
        # 第一版一次只处理一份文件，三位专家共用这份文件。
        uploaded_file = st.file_uploader(
            "📂 拖拽或点击上传您的尽调文本（支持 PDF / TXT）",
            type=["pdf", "txt"],
            accept_multiple_files=False,
            help="PDF 需带文字层；TXT 支持 UTF-8、UTF-16 和 GB18030 编码。",
            key="due_diligence_file",
            on_change=_activate_upload_file,
        )
        st.caption(
            "一份文件可供三位专家分别分析。"
            "PDF 必须带文字层；扫描图片版请先做 OCR。"
            "TXT 会自动尝试常见中文编码。"
        )
    else:
        # 粘贴文字模式同样受字符上限约束。
        # max_chars 会在输入框层面阻止继续输入过多内容；后面仍会再次校验。
        pasted_text = st.text_area(
            "粘贴需要分析的尽调文本",
            height=320,
            max_chars=MAX_INPUT_CHARS,
            placeholder="例如：营业收入、负债率、重大合同、诉讼案件、客户与供应商情况……",
            key="pasted_due_diligence_text",
            on_change=_activate_paste_text,
        )
        st.caption(
            f"已输入 {len(pasted_text):,} / {MAX_INPUT_CHARS:,} 个字符"
        )

with company_tab:
    st.caption(
        "适合已上市的沪深 A 股公司。先用可搜索的下拉框选择公司，"
        "系统会直接查询巨潮资讯网的公开公告，找到指定年份的年度报告，"
        "下载官方 PDF 并提取核心数据；同时从 AKShare 的新浪财经接口"
        "取得近三年资产负债表、利润表和现金流量表。"
        "官方年报比网页搜索更准确，但仍不能替代企业提供的完整尽调资料。"
    )

    st.caption(
        "年份指会计年度：例如“2025 年报”通常在 2026 年 1～4 月披露。"
        "三张表会自动取该年度及前两年；首期只支持沪深 A 股，"
        "北交所、港股、美股暂不支持。"
    )
    st.caption(
        "获取年报和三张表不会调用 DeepSeek，也不会产生模型费用；"
        "只有点击下方专家按钮开始分析时，资料才会发送给 DeepSeek。"
        "三张表来自第三方整理数据，正式结论请对照巨潮年报原文。"
    )

    # 加载沪深 A 股公司列表。列表约 5000 多条，首次请求 AKShare
    # 可能需要几秒钟；加载成功后由 @st.cache_data 缓存 24 小时，
    # 之后页面刷新会直接使用缓存，不会重复联网。
    with st.spinner("正在加载沪深 A 股公司列表……"):
        company_options, company_list_error = _load_company_options_safe()

    annual_input_col, annual_year_col = st.columns([2, 1])

    with annual_input_col:
        if company_options:
            # Streamlit 的 selectbox 自带搜索：
            # 用户可以直接输入“600519”或“贵州茅台”，下拉列表会实时筛选。
            selected_company = st.selectbox(
                "选择公司（可输入股票代码或公司名称搜索）",
                options=company_options,
                index=None,
                placeholder=(
                    "输入股票代码或公司名称，例如 600519 或 贵州茅台"
                ),
                help=(
                    "下拉框支持打字搜索：输入股票代码或公司名称的一部分即可筛选。"
                    "列表只包含沪深 A 股，北交所、B 股、港股、美股暂不支持。"
                ),
                key="annual_company_selectbox",
            )

            # 从“600519 - 贵州茅台”这样的文字中提取出纯股票代码，
            # 再交给底层的巨潮年报和 AKShare 抓取逻辑。
            annual_stock_code = extract_stock_code(selected_company or "")
            st.caption(
                f"已加载 {len(company_options):,} 家沪深 A 股公司。"
                "列表缓存 24 小时，新股上市或公司更名最多一天后自动更新。"
                "选择公司后，请点击下方“获取官方年报与三年报表”。"
            )
        else:
            # 列表加载失败时的兜底方案：
            # 用户仍然可以手动输入股票代码，不影响年报查询。
            st.warning(company_list_error)
            if st.button(
                "🔄 重新加载公司列表",
                key="reload_company_list_button",
            ):
                # 清除 24 小时缓存，下一次页面运行会重新请求 AKShare。
                _load_a_share_company_options.clear()
                st.rerun()

            annual_stock_code = st.text_input(
                "股票代码（手动输入）",
                placeholder="例如：600519",
                help=(
                    "公司列表暂时不可用时，可以直接输入 6 位沪深 A 股代码，"
                    "也支持 SH600519、600519.SH 这类写法。"
                ),
                key="annual_stock_code_input",
            )

    with annual_year_col:
        annual_report_year = st.selectbox(
            "报告年份（会计年度）",
            options=annual_year_options,
            index=0,
            key="annual_report_year",
            help=(
                "默认选择上一年。当年年报通常尚未披露；"
                "例如 2026 年默认查 2025 年报。"
            ),
        )

    force_refresh = st.checkbox(
        "强制重新下载最新版本",
        value=False,
        help=(
            "默认使用 24 小时内的年报和三张表缓存，重复分析更快。"
            "如果担心年报刚发布了更正后版本，可以勾选后重新获取。"
        ),
        key="annual_report_force_refresh",
    )

    fetch_annual_clicked = st.button(
        "📑 获取官方年报与三年报表",
        type="secondary",
        use_container_width=True,
        disabled=not annual_stock_code,
        key="fetch_annual_report_button",
    )
    if not annual_stock_code:
        st.caption("请先在上方下拉框中选择一家公司，按钮才可使用。")

    if fetch_annual_clicked:
        had_previous_results = bool(
            st.session_state.get("agent_results")
        )

        # 三张表是可选的补充数据：取不到时继续用巨潮年报，
        # 不能因为新浪财经临时不可用就让整份年报失败。
        statements_result = None
        statements_error = ""

        try:
            normalized_annual_code = normalize_report_code(
                annual_stock_code
            )

            with st.status(
                "正在查询巨潮公告、下载 PDF 并解析年报，"
                "同时获取近三年三张表，请稍候（通常 10～60 秒，"
                "首次下载或需要向前回退年份时可能更久）……",
                expanded=True,
            ) as annual_status:

                def _on_annual_progress(message: str) -> None:
                    """把年报服务报告的中文阶段说明写到页面上。"""
                    st.write(message)

                try:
                    annual_result = fetch_annual_report(
                        normalized_annual_code,
                        annual_report_year,
                        force_refresh=force_refresh,
                        progress_callback=_on_annual_progress,
                    )
                except AnnualReportError:
                    # 把进度框标成失败，避免页面一直显示“正在处理”。
                    annual_status.update(
                        label="官方年报获取失败",
                        state="error",
                        expanded=True,
                    )
                    raise

                annual_status.update(
                    label="官方年报已就绪，正在获取近三年三张表……",
                    state="running",
                    expanded=True,
                )

                try:
                    statements_result = fetch_three_year_statements(
                        normalized_annual_code,
                        annual_result.year,
                        force_refresh=force_refresh,
                        progress_callback=_on_annual_progress,
                    )
                except FinancialStatementError as exc:
                    statements_error = str(exc)
                except Exception as exc:
                    # AKShare 依赖较多，可能抛出它自己没有包装过的异常。
                    # 这里兜底成中文提示，保证巨潮年报仍能正常保存和展示。
                    statements_error = (
                        f"三张表获取出现未预期错误：{exc}"
                    )

                statements_partial = (
                    statements_result is not None
                    and any(
                        not table.rows
                        for table in statements_result.tables.values()
                    )
                )

                if statements_result is not None:
                    status_label = (
                        f"官方年报与近三年三张表已就绪："
                        f"{annual_result.company_name}"
                        f"（{annual_result.code}）"
                        f"{annual_result.report_title}"
                    )
                    if statements_partial:
                        status_label += "（部分表未取到）"
                else:
                    status_label = (
                        f"官方年报已就绪："
                        f"{annual_result.company_name}"
                        f"（{annual_result.code}）"
                        f"{annual_result.report_title}"
                        "（三张表本次未取到，年报仍可正常分析）"
                    )

                annual_status.update(
                    label=status_label,
                    state="complete",
                    expanded=False,
                )

            # 把三张表挂到年报结果上，页面展示和三位专家共用同一份数据。
            # 失败时只记录原因，不覆盖已经拿到的年报指标和原文摘录。
            annual_result.financial_statements = (
                statements_result.to_dict()
                if statements_result is not None
                else {}
            )
            annual_result.financial_statements_error = statements_error

            if statements_result is not None:
                annual_result.agent_context = merge_agent_contexts(
                    annual_result.agent_context,
                    statements_result.to_agent_context(),
                )
                statements_suffix = "；含 AKShare 近三年三张表"
                if statements_partial:
                    statements_suffix += "（部分）"
                annual_result.source_name = (
                    f"{annual_result.source_name}"
                    f"{statements_suffix}"
                )

            # 只有获取成功后才切换当前材料来源，避免失败时把旧的
            # 上传资料或搜索结果意外替换掉。
            st.session_state[ANNUAL_REPORT_STATE_KEY] = (
                annual_result.to_dict()
            )
            annual_report_entry = st.session_state[
                ANNUAL_REPORT_STATE_KEY
            ]
            st.session_state[ACTIVE_INPUT_STATE_KEY] = (
                INPUT_MODE_ANNUAL_REPORT
            )
            input_mode = INPUT_MODE_ANNUAL_REPORT

            if had_previous_results:
                # 新股/新年报意味着材料已经更换，清空旧分析结果，
                # 避免不同公司的结论混在同一个页签里。
                st.session_state["agent_results"] = {}
                st.caption(
                    "已获取新的官方年报，旧的分析结果已清空。"
                )

        except AnnualReportError as exc:
            # 代码错误、没有年报、下载失败、扫描件等都在这里显示。
            # 失败时保留上一次的年报和分析结果，避免用户误以为数据被删除。
            st.error(str(exc))
            if had_previous_results:
                st.caption(
                    "本次获取未成功，已保留上一次的分析结果。"
                )

    # 展示当前缓存中的官方年报结果。
    # 直接读 session_state，确保点击获取后的同一次页面运行也能看到结果。
    cached_annual_entry = st.session_state.get(
        ANNUAL_REPORT_STATE_KEY
    )

    if cached_annual_entry:
        try:
            cached_annual_result = AnnualReportResult.from_dict(
                cached_annual_entry
            )
        except (ValueError, TypeError, KeyError):
            cached_annual_result = None
            st.warning(
                "上次保存的年报结果格式异常，请重新点击“获取官方年报与三年报表”。"
            )

        if cached_annual_result is not None:
            try:
                current_annual_code = normalize_report_code(
                    annual_stock_code
                )
            except AnnualReportError:
                current_annual_code = ""

            if not str(annual_stock_code).strip():
                st.info(
                    "请先在上方下拉框中选择一家公司，然后点击“获取官方年报与三年报表”。"
                )
            elif current_annual_code != cached_annual_result.code:
                st.warning(
                    "所选公司已修改，请重新点击“获取官方年报与三年报表”，"
                    "再让专家分析。"
                )
            elif annual_report_year != cached_annual_result.requested_year:
                st.warning(
                    "报告年份已修改，请重新点击“获取官方年报与三年报表”，"
                    "再让专家分析。"
                )
            else:
                st.success(
                    f"{cached_annual_result.company_name}"
                    f"（{cached_annual_result.code}）"
                    f"{cached_annual_result.report_title}已就绪"
                )

                info_col_1, info_col_2, info_col_3 = st.columns(3)
                info_col_1.caption(
                    f"披露日期：{cached_annual_result.disclosure_date}"
                )
                info_col_2.caption(
                    f"PDF 页数：{cached_annual_result.page_count}"
                )
                info_col_3.caption(
                    f"审计意见：{cached_annual_result.audit_opinion}"
                )

                st.markdown(
                    f"[打开巨潮原文 PDF]({cached_annual_result.pdf_url})"
                )

                if cached_annual_result.from_cache:
                    st.caption(
                        "本次使用 24 小时内的解析缓存（"
                        f"{cached_annual_result.fetched_at} 获取）。"
                        "如需最新版本，请勾选“强制重新下载最新版本”后重试。"
                    )
                else:
                    st.caption(
                        f"本次获取时间：{cached_annual_result.fetched_at}"
                    )

                if cached_annual_result.warnings:
                    for warning in cached_annual_result.warnings:
                        st.warning(warning)

                if cached_annual_result.metrics:
                    st.markdown("### 核心财务数据")
                    st.table(
                        [
                            {
                                "指标": metric.label,
                                "本期": metric.display_value or "-",
                                "上期": metric.prior_display_value or "-",
                                "同比": metric.change or "-",
                                "单位": metric.unit or "-",
                                "原文页码": metric.page or "-",
                            }
                            for metric in cached_annual_result.metrics
                        ]
                    )
                    st.caption(
                        "数字由程序从官网 PDF 提取，未识别的项目显示“-”；"
                        "请点击上方原文链接核对重要数字。"
                    )

                # 近三年三张表是巨潮 PDF 之外的补充明细。
                # 取不到时只提示这一块失败，不影响上面的年报数据。
                statements_payload = (
                    cached_annual_result.financial_statements
                )
                if (
                    isinstance(statements_payload, dict)
                    and isinstance(
                        statements_payload.get("tables"),
                        dict,
                    )
                    and statements_payload.get("tables")
                ):
                    _render_financial_statements(
                        statements_payload
                    )
                elif cached_annual_result.financial_statements_error:
                    st.warning(
                        "巨潮年报已获取，但近三年三张表没有取到："
                        f"{cached_annual_result.financial_statements_error}"
                        " 可以重新点击“获取官方年报与三年报表”重试；"
                        "年报会使用缓存，不会重复下载。"
                    )
                else:
                    st.caption(
                        "本次年报结果不含近三年三张表。"
                        "请重新点击“获取官方年报与三年报表”补充；"
                        "年报会使用缓存，不会重复下载。"
                    )

                if cached_annual_result.quarterly:
                    st.markdown("### 分季度数据")
                    st.table(cached_annual_result.quarterly)

                with st.expander("查看审计意见、分红与关键章节"):
                    if cached_annual_result.audit_opinion:
                        st.markdown(
                            f"**审计意见：** "
                            f"{cached_annual_result.audit_opinion}"
                        )
                    if cached_annual_result.auditor:
                        st.markdown(
                            f"**会计师事务所：** "
                            f"{cached_annual_result.auditor}"
                        )
                    if cached_annual_result.audit_date:
                        st.markdown(
                            f"**审计报告日期：** "
                            f"{cached_annual_result.audit_date}"
                        )
                    if cached_annual_result.dividend_text:
                        st.markdown("**分红方案摘录：**")
                        st.text(cached_annual_result.dividend_text)

                    section_labels = (
                        ("audit", "审计意见"),
                        ("dividend", "利润分配与分红"),
                        ("business_discussion", "经营情况讨论与分析"),
                        ("risk", "风险因素"),
                        ("related_party", "关联交易"),
                        ("guarantee", "对外担保"),
                        ("litigation", "诉讼与仲裁"),
                    )
                    for key, label in section_labels:
                        section = cached_annual_result.sections.get(key)
                        if section is None or not section.text.strip():
                            continue
                        st.markdown(
                            f"#### {label}"
                            f"（第 {section.page_start}～{section.page_end} 页）"
                        )
                        st.text(section.text)

                if cached_annual_result.tables:
                    with st.expander(
                        f"查看年报原文表格摘录"
                        f"（共 {len(cached_annual_result.tables)} 张）"
                    ):
                        for table in cached_annual_result.tables:
                            st.markdown(
                                f"#### {table.title}"
                                f"（第 {table.page} 页）"
                            )
                            st.markdown(table.markdown)
                else:
                    st.caption(
                        "本次没有自动识别到原文表格；"
                        "核心指标仍显示在上方。"
                    )

                # 年报结果会一直留在 Tab 2 里，但用户之后可能又去
                # 上传或粘贴其他材料。只有当前材料来源确实是年报时，
                # 才能提示“已设为当前分析材料”，否则会误导用户。
                if (
                    st.session_state.get(ACTIVE_INPUT_STATE_KEY)
                    == INPUT_MODE_ANNUAL_REPORT
                ):
                    st.info(
                        "官方年报已设为当前分析材料。"
                        "如果本次已取得近三年三张表，它们会一起提供给专家；"
                        "请在下方“2. 选择专家并开始分析”中，"
                        "选择财务、法务或业务专家开始分析。"
                    )
                else:
                    st.caption(
                        "这份年报已缓存，但当前分析材料不是它。"
                        "如需改用年报，请重新点击“获取官方年报与三年报表”。"
                    )

    else:
        st.caption(
            "在上方下拉框选择公司和年份后，点击“获取官方年报与三年报表”。"
            "系统会优先使用 24 小时缓存；首次获取会下载并解析官方 PDF。"
        )
        st.caption(
            "年报和三张表只在当前网页会话和系统临时缓存中使用，"
            "不会写入项目目录，也不会把 PDF 文件上传到其他网站；"
            "查询三张表会把股票代码发送给新浪财经公开接口；"
            "点击专家分析时，提取出的文字会按页面提示发送给 DeepSeek。"
        )
        st.caption(
            "巨潮公告接口由网页后台提供，并非正式公开 API；"
            "如果接口暂时不可用，可以点击巨潮原文链接手动下载，"
            "再通过 Tab 1 上传分析。"
        )

# Tab 里的回调可能刚刚修改了材料来源（例如上传文件、粘贴文字、
# 成功获取年报）。这里重新读取一次，避免本次页面运行仍使用旧值。
input_mode = st.session_state[ACTIVE_INPUT_STATE_KEY]

# 最后再显示当前材料来源，确保获取年报后的同一次页面运行也能显示正确。
_source_text = {
    INPUT_MODE_UPLOAD: "上传的 PDF / TXT 文件",
    INPUT_MODE_PASTE: "粘贴的文字",
    INPUT_MODE_ANNUAL_REPORT: "巨潮官方年报 + 近三年三张表",
}.get(input_mode, "上传的 PDF / TXT 文件")
source_caption_placeholder.caption(
    f"当前将分析：**{_source_text}**。"
    "切换 Tab 不会自动改变材料来源，"
    "以您最后一次上传、粘贴或成功获取年报为准。"
)

# ==================== 首次进入的空状态引导 ====================
# 显示条件：
#   1. 用户从未点击过任意一位专家的“开始分析”按钮；
#   2. 当前也没有任何已保存的分析结果。
# 点击分析按钮的回调会在页面重跑前把 analysis_attempted 设为 True，
# 所以加载动画出现时，这段引导会立即隐藏，不会和 st.status 叠在一起。
_analysis_attempted = bool(
    st.session_state.get(ANALYSIS_ATTEMPTED_STATE_KEY, False)
)
_has_agent_results = bool(
    st.session_state.get("agent_results")
)

if not _analysis_attempted and not _has_agent_results:
    st.markdown(
        '<div class="fdd-empty-state">'
        '<div class="fdd-empty-icon">📄</div>'
        '<div class="fdd-empty-title">'
        "请输入股票代码或公司名称，开启多智能体智能尽调"
        "</div>"
        '<div class="fdd-empty-hint">'
        "也可以在“Tab 1：上传 / 粘贴资料”中提供 PDF、TXT 或文字材料。"
        "</div>"
        "</div>",
        unsafe_allow_html=True,
    )

# ==================== 第二部分：选择专家并分析 ====================
st.header("2. 选择专家并开始分析")
st.caption(
    "点击哪位专家的按钮，就只使用这位专家的分析逻辑；"
    "三份结果分别保存，互不覆盖。"
    "使用公司查询时，请先在 Tab 2 选择公司并获取年报，再点击专家按钮。"
    "“企业概况”和“管理层尽调”是规划中的模块，当前仅展示占位说明。"
)

# 三位专家各自有独立的维度选择和分析按钮；
# “企业概况”“管理层尽调”是路线图占位页签，不调用任何分析逻辑。
finance_tab, legal_tab, business_tab, profile_tab, management_tab = (
    st.tabs(
        [
            "财务专家",
            "法务专家",
            "业务专家",
            "企业概况",
            "管理层尽调",
        ]
    )
)

with finance_tab:
    _render_agent_panel(
        agent_key="finance",
        config=AGENT_CONFIGS["finance"],
        api_key_ready=api_key_ready,
        input_mode=input_mode,
        uploaded_file=uploaded_file,
        pasted_text=pasted_text,
        annual_report_entry=annual_report_entry,
        annual_stock_code=annual_stock_code,
        annual_report_year=annual_report_year,
    )

with legal_tab:
    _render_agent_panel(
        agent_key="legal",
        config=AGENT_CONFIGS["legal"],
        api_key_ready=api_key_ready,
        input_mode=input_mode,
        uploaded_file=uploaded_file,
        pasted_text=pasted_text,
        annual_report_entry=annual_report_entry,
        annual_stock_code=annual_stock_code,
        annual_report_year=annual_report_year,
    )

with business_tab:
    _render_agent_panel(
        agent_key="business",
        config=AGENT_CONFIGS["business"],
        api_key_ready=api_key_ready,
        input_mode=input_mode,
        uploaded_file=uploaded_file,
        pasted_text=pasted_text,
        annual_report_entry=annual_report_entry,
        annual_stock_code=annual_stock_code,
        annual_report_year=annual_report_year,
    )

# 企业概况、管理层尽调：产品路线图占位。
# 这里只显示一句说明，不调用任何 Agent、数据接口或分析函数。
with profile_tab:
    st.info("该模块正在联调第三方工商数据接口，敬请期待...")

with management_tab:
    st.info("该模块正在联调第三方工商数据接口，敬请期待...")

# ==================== 底部免责声明 ====================
# 放在整页最后，位于所有输入区、公司查询和三位专家结果之后。
# 无论用户是否生成了报告，页面滚到最底部都能看到这段声明。
# 文字使用很小的灰色字号，上下各一条细灰线，与正文明确隔开；
# 这里只做展示，不包含链接或按钮，避免误触。
st.markdown(
    '<div class="fdd-footer">'
    '<div class="fdd-footer-text">'
    "免责声明：本平台生成内容由AI辅助生成，仅供内部研究参考，"
    "不构成任何投资建议。数据来源：AKShare、巨潮资讯及公开网络。"
    "</div>"
    "</div>",
    unsafe_allow_html=True,
)
