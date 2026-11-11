export type SummaryCardStatus = 'up' | 'down' | 'normal';

export type SummaryCard = {
  label: string;
  value: string;
  status: SummaryCardStatus;
};

/** 后端只约定 up / down / normal，其他值一律按“平稳”处理，避免样式异常 */
function normalizeSummaryStatus(value: unknown): SummaryCardStatus {
  if (typeof value !== 'string') return 'normal';
  const status = value.toLowerCase();
  return status === 'up' || status === 'down' ? status : 'normal';
}

/** 校验并清洗后端返回的 summary_cards，坏数据直接跳过 */
export function normalizeSummaryCards(value: unknown): SummaryCard[] {
  if (!Array.isArray(value)) return [];

  const cards: SummaryCard[] = [];
  for (const item of value) {
    if (!item || typeof item !== 'object') continue;
    const record = item as Record<string, unknown>;

    const label = typeof record.label === 'string' ? record.label.trim() : '';
    const cardValue =
      typeof record.value === 'string'
        ? record.value.trim()
        : typeof record.value === 'number'
          ? String(record.value)
          : '';

    if (!label || !cardValue) continue;

    cards.push({
      label,
      value: cardValue,
      status: normalizeSummaryStatus(record.status),
    });
  }

  return cards;
}

export function normalizeCoreConclusion(value: unknown): string {
  return typeof value === 'string' ? value.trim() : '';
}
