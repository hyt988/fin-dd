const PDF_API_URL = 'http://127.0.0.1:8000/api/download_pdf';

type DownloadReportPdfParams = {
  reportText: string;
  companyName: string;
  signal?: AbortSignal;
};

type ErrorPayload = {
  message?: unknown;
  detail?: unknown;
};

/** 去掉 Windows / macOS 文件名不允许的字符，避免浏览器拒绝保存 */
function toSafeFileName(name: string): string {
  const withoutControlChars = Array.from(name)
    .filter((char) => char.charCodeAt(0) >= 32)
    .join('');
  const safe = withoutControlChars.replace(/[\\/:*?"<>|]/g, '_').trim();
  return (safe || '未命名公司').slice(0, 80);
}

function withPdfExtension(name: string): string {
  return /\.pdf$/i.test(name) ? name : `${name}.pdf`;
}

/** 优先使用后端 Content-Disposition 里的文件名，读不到就使用公司名兜底 */
function resolveFileName(disposition: string | null, fallback: string): string {
  if (!disposition) return fallback;

  const utf8Match = disposition.match(/filename\*=UTF-8''([^;]+)/i);
  if (utf8Match) {
    try {
      return withPdfExtension(toSafeFileName(decodeURIComponent(utf8Match[1])));
    } catch {
      // 文件名编码异常时继续尝试普通 filename
    }
  }

  const plainMatch = disposition.match(/filename="?([^";]+)"?/i);
  return plainMatch ? withPdfExtension(toSafeFileName(plainMatch[1])) : fallback;
}

/** 把 Markdown 报告交给后端生成 PDF，并用 Blob 触发浏览器下载 */
export async function downloadReportPdf({
  reportText,
  companyName,
  signal,
}: DownloadReportPdfParams): Promise<void> {
  const titleName = companyName.trim() || '未命名公司';

  const response = await fetch(PDF_API_URL, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      report_text: reportText,
      company_name: titleName,
    }),
    signal,
  });

  if (!response.ok) {
    const data = (await response.json().catch(() => null)) as ErrorPayload | null;
    const message =
      (typeof data?.message === 'string' && data.message) ||
      (typeof data?.detail === 'string' && data.detail) ||
      `PDF 生成失败（HTTP ${response.status}）`;
    throw new Error(message);
  }

  const blob = await response.blob();
  if (!blob.size) {
    throw new Error('后端返回的 PDF 文件为空，请稍后重试。');
  }

  const objectUrl = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = objectUrl;
  link.download = resolveFileName(
    response.headers.get('Content-Disposition'),
    `${toSafeFileName(titleName)}-投研初筛报告.pdf`
  );
  document.body.appendChild(link);
  link.click();
  link.remove();

  // 等浏览器开始读取 Blob 后再释放，避免个别浏览器下载中断
  window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
}
