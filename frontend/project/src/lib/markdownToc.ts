export type TocHeading = {
  id: string;
  level: number;
  text: string;
};

/** 匹配 Markdown 的 # 标题；行首最多允许 3 个空格 */
const HEADING_PATTERN = /^\s{0,3}(#{1,6})[ \t]+(.+?)(?:[ \t]+#+)?[ \t]*$/;
/** 匹配代码块围栏，避免把代码里的 # 当成标题 */
const FENCE_PATTERN = /^\s{0,3}(`{3,}|~{3,})/;

/** 去掉标题里的行内 Markdown 标记，让目录显示为纯文本 */
function cleanHeadingText(text: string): string {
  return text
    .replace(/!\[([^\]]*)\]\([^)]*\)/g, '$1')
    .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
    .replace(/<[^>]+>/g, '')
    .replace(/[`*_~]/g, '')
    .replace(/\s+/g, ' ')
    .trim();
}

/**
 * 从完整 Markdown 报告中提取目录标题。
 * 只识别行首的 # 标题，并跳过 ``` / ~~~ 代码块里的内容。
 */
export function extractTocHeadings(markdown: string): TocHeading[] {
  if (!markdown) return [];

  const headings: TocHeading[] = [];
  let fenceChar: '`' | '~' | null = null;

  for (const line of markdown.split(/\r?\n/)) {
    const fence = line.match(FENCE_PATTERN);
    if (fence) {
      const char = fence[1][0] as '`' | '~';
      if (fenceChar === null) {
        fenceChar = char;
      } else if (fenceChar === char) {
        fenceChar = null;
      }
      continue;
    }

    if (fenceChar !== null) continue;

    const match = line.match(HEADING_PATTERN);
    if (!match) continue;

    const text = cleanHeadingText(match[2]);
    if (!text) continue;

    headings.push({
      id: `report-heading-${headings.length}`,
      level: match[1].length,
      text,
    });
  }

  return headings;
}
