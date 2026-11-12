/** 工作台支持的两种输入方式：公司名称/代码，或上传 PDF/TXT 资料 */
export type AnalysisInputMode = 'company' | 'file';

/** 与后端 MAX_UPLOAD_BYTES 保持一致：单个文件最多 100 MB */
export const MAX_UPLOAD_BYTES = 100 * 1024 * 1024;

/** 后端目前只识别 PDF 和 TXT 两种文件 */
export const SUPPORTED_UPLOAD_EXTENSIONS = ['.pdf', '.txt'] as const;

/** 判断文件名后缀是否是后端支持的格式（忽略大小写） */
export function isSupportedUploadFile(fileName: string): boolean {
  const lowerName = fileName.toLowerCase();
  return SUPPORTED_UPLOAD_EXTENSIONS.some((extension) => lowerName.endsWith(extension));
}

/**
 * 把分析接口的失败状态码翻译成用户能看懂的中文提示。
 *
 * 优先展示后端返回的具体原因（例如“文件格式不支持”“匹配到多家公司”）；
 * 后端没给可读原因时（比如网关直接返回 413 HTML），再按状态码兜底。
 */
export function describeAnalyzeHttpError(status: number, detail?: string): string {
  const message = typeof detail === 'string' ? detail.trim() : '';
  if (message) return message;

  switch (status) {
    case 400:
      return '请求有误，请检查输入内容或文件格式后重试。';
    case 413:
      return '文件过大（超过 100 MB），请压缩或拆分后重新上传。';
    case 502:
      return '后端服务或数据源暂时不可用，请稍后重试。';
    case 503:
      return '分析服务暂未配置完成（API Key 未配置），请联系管理员。';
    case 500:
      return '服务器处理出错，请稍后重试。';
    default:
      return `请求失败（HTTP ${status}），请稍后重试。`;
  }
}
