import type { SummaryCard } from './report';
import { normalizeCoreConclusion, normalizeSummaryCards } from './report';

export type HistoryItem = {
  companyName: string;
  reportText: string;
  timestamp: number;
  summaryCards?: SummaryCard[];
  coreConclusion?: string;
};

export const HISTORY_STORAGE_KEY = 'findd-diligence-history-v1';
export const HISTORY_LIMIT = 10;

// 保留旧记录的必填字段，同时校验新摘要字段；缺少摘要的旧记录仍然可以正常打开
function toHistoryItem(value: unknown): HistoryItem | null {
  if (!value || typeof value !== 'object') return null;
  const item = value as Record<string, unknown>;

  if (
    typeof item.companyName !== 'string' ||
    typeof item.reportText !== 'string' ||
    typeof item.timestamp !== 'number' ||
    !Number.isFinite(item.timestamp)
  ) {
    return null;
  }

  const summaryCards = normalizeSummaryCards(item.summaryCards);
  const coreConclusion = normalizeCoreConclusion(item.coreConclusion);

  return {
    companyName: item.companyName,
    reportText: item.reportText,
    timestamp: item.timestamp,
    ...(summaryCards.length > 0 ? { summaryCards } : {}),
    ...(coreConclusion ? { coreConclusion } : {}),
  };
}

// 从 localStorage 读取历史记录；数据损坏时当作空列表处理，避免页面崩溃
export function loadHistory(): HistoryItem[] {
  try {
    const raw = window.localStorage.getItem(HISTORY_STORAGE_KEY);
    if (!raw) return [];

    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];

    return parsed
      .map(toHistoryItem)
      .filter((item): item is HistoryItem => item !== null)
      .sort((a, b) => b.timestamp - a.timestamp)
      .slice(0, HISTORY_LIMIT);
  } catch {
    return [];
  }
}

// 只保留最近 HISTORY_LIMIT 条；浏览器禁用存储或空间不足时静默失败
export function saveHistory(items: HistoryItem[]): void {
  try {
    window.localStorage.setItem(
      HISTORY_STORAGE_KEY,
      JSON.stringify(items.slice(0, HISTORY_LIMIT))
    );
  } catch {
    // 不影响分析主流程
  }
}
