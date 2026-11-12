# 智投明鉴 (FinDD) - 多智能体金融尽调与风险扫描平台

## 🌟 项目简介

一个基于大语言模型（LLM）和 React + FastAPI 全栈架构的金融尽调初筛工具。专为投资人与分析师打造，一键输入公司代码或名称，即可自动生成包含财务、法务、业务三重视角的深度尽调报告。

## 🔗 在线体验

- 前端 Demo: https://myfindd.top/
- 后端 API: https://fin-dd.onrender.com/docs

## 🧠 核心亮点与架构设计

1. **多智能体协同 (Multi-Agent)**: 财务、法务、业务独立拆分，Prompt 隔离，避免上下文过载。
2. **防幻觉架构 (Anti-Hallucination)**: Python（Pandas/NumPy）负责底层数学计算（同比、复合增长率），大模型仅负责商业洞察与定性分析。
3. **多源数据融合**: 打通 AKShare 获取结构化财务报表，结合 Web Search 获取非结构化公开舆情与法务信息。
4. **极简交付体验**: 前端采用渐进式信息展示（高管摘要卡片 + 折叠明细表），支持一键导出投行级排版的 PDF。

## 🛠️ 技术栈

- 前端：React, TypeScript, Vite, Tailwind CSS, React Router, React Markdown
- 后端：Python, FastAPI, Uvicorn
- 模型：DeepSeek API
- 部署：Cloudflare Workers (前端), Render (后端)
