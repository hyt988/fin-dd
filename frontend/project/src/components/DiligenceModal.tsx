import { useEffect, useMemo, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {
  AlertTriangle,
  ArrowRight,
  Building2,
  Download,
  FileText,
  List,
  Loader2,
  Search,
  Sparkles,
  Upload,
  X,
} from 'lucide-react';
import type { AnalysisInputMode } from '../lib/analyze';
import { MAX_UPLOAD_BYTES } from '../lib/analyze';
import { extractTocHeadings, type TocHeading } from '../lib/markdownToc';
import type { SummaryCard, SummaryCardStatus } from '../lib/report';

const SUMMARY_STATUS_META: Record<
  SummaryCardStatus,
  { label: string; badgeClassName: string; dotClassName: string }
> = {
  up: {
    label: '向好',
    badgeClassName: 'bg-primary-50 text-primary-600',
    dotClassName: 'bg-primary-500',
  },
  down: {
    label: '恶化',
    badgeClassName: 'bg-red-50 text-red-600',
    dotClassName: 'bg-red-500',
  },
  normal: {
    label: '平稳',
    badgeClassName: 'bg-surface-100 text-gray-500',
    dotClassName: 'bg-gray-400',
  },
};

const MAX_UPLOAD_MB = Math.round(MAX_UPLOAD_BYTES / (1024 * 1024));

const INPUT_MODE_OPTIONS: {
  value: AnalysisInputMode;
  label: string;
  icon: typeof Building2;
}[] = [
  { value: 'company', label: '输入公司名称/代码', icon: Building2 },
  { value: 'file', label: '上传分析资料', icon: Upload },
];

/** 把字节数转成好读的文件大小，例如 1.2 MB */
function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

type DiligenceModalProps = {
  open: boolean;
  inputMode: AnalysisInputMode;
  onInputModeChange: (mode: AnalysisInputMode) => void;
  companyName: string;
  onCompanyNameChange: (value: string) => void;
  selectedFile: File | null;
  onSelectFile: (file: File | null) => void;
  isLoading: boolean;
  reportText: string;
  summaryCards: SummaryCard[];
  coreConclusion: string;
  errorMsg: string;
  isDownloadingPdf: boolean;
  pdfError: string;
  onAnalyze: () => void;
  onClose: () => void;
  onStartOver: () => void;
  onDownloadPdf: () => void;
};

export default function DiligenceModal({
  open,
  inputMode,
  onInputModeChange,
  companyName,
  onCompanyNameChange,
  selectedFile,
  onSelectFile,
  isLoading,
  reportText,
  summaryCards,
  coreConclusion,
  errorMsg,
  isDownloadingPdf,
  pdfError,
  onAnalyze,
  onClose,
  onStartOver,
  onDownloadPdf,
}: DiligenceModalProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const markdownContainerRef = useRef<HTMLDivElement>(null);
  const mobileTocRef = useRef<HTMLDetailsElement>(null);
  const [activeHeadingId, setActiveHeadingId] = useState('');
  const isResultView = Boolean(reportText);
  const headings = useMemo(() => extractTocHeadings(reportText), [reportText]);
  const renderedReport = useMemo(
    () => <ReactMarkdown remarkPlugins={[remarkGfm]}>{reportText}</ReactMarkdown>,
    [reportText]
  );
  const baseLevel = headings.length > 0 ? Math.min(...headings.map((heading) => heading.level)) : 1;
  const summaryGridClass =
    summaryCards.length === 1
      ? 'sm:grid-cols-1'
      : summaryCards.length === 2
        ? 'sm:grid-cols-2'
        : summaryCards.length >= 4
          ? 'sm:grid-cols-2 lg:grid-cols-4'
          : 'sm:grid-cols-3';
  // 公司模式要求名称非空，文件模式要求已经选好文件
  const canSubmit = inputMode === 'file' ? Boolean(selectedFile) : Boolean(companyName.trim());

  // 弹窗打开时锁定页面滚动，避免背景页面跟着滚动
  useEffect(() => {
    if (!open) return;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.body.style.overflow = previousOverflow;
    };
  }, [open]);

  // 支持按 Esc 关闭
  useEffect(() => {
    if (!open) return;
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [open, onClose]);

  // 打开弹窗后自动聚焦到输入框（只有公司名称模式才有输入框）
  useEffect(() => {
    if (!open || isResultView || inputMode !== 'company') return;
    const timer = window.setTimeout(() => inputRef.current?.focus(), 60);
    return () => window.clearTimeout(timer);
  }, [open, isResultView, inputMode]);

  // 报告渲染完成后，把目录条目与正文里的标题按顺序绑定锚点
  useEffect(() => {
    if (!open || !isResultView) return;
    const scrollContainer = scrollContainerRef.current;
    const markdownContainer = markdownContainerRef.current;
    if (!scrollContainer || !markdownContainer) return;

    const headingElements = Array.from(
      markdownContainer.querySelectorAll<HTMLElement>('h1, h2, h3, h4, h5, h6')
    );
    headingElements.forEach((element) => element.removeAttribute('id'));

    let searchFrom = 0;
    headings.forEach((heading) => {
      let matchIndex = -1;
      for (let index = searchFrom; index < headingElements.length; index += 1) {
        const elementText = (headingElements[index].textContent ?? '')
          .replace(/\s+/g, ' ')
          .trim();
        if (elementText === heading.text) {
          matchIndex = index;
          break;
        }
      }

      const targetIndex = matchIndex === -1 ? searchFrom : matchIndex;
      const target = headingElements[targetIndex];
      if (target) {
        target.id = heading.id;
        target.style.scrollMarginTop = '16px';
        searchFrom = targetIndex + 1;
      }
    });

    scrollContainer.scrollTop = 0;
    setActiveHeadingId('');
    mobileTocRef.current?.removeAttribute('open');
  }, [open, isResultView, headings]);

  if (!open) return null;

  const scrollToHeading = (id: string) => {
    // 手机端目录展开时会占位置，先收起再滚动，定位才准确
    mobileTocRef.current?.removeAttribute('open');
    const target = document.getElementById(id);
    if (!target) return;
    target.scrollIntoView({ behavior: 'smooth', block: 'start' });
    setActiveHeadingId(id);
  };

  const renderTocItem = (heading: TocHeading) => {
    const isActive = activeHeadingId === heading.id;
    return (
      <a
        key={heading.id}
        href={`#${heading.id}`}
        title={heading.text}
        aria-current={isActive ? 'location' : undefined}
        onClick={(event) => {
          event.preventDefault();
          scrollToHeading(heading.id);
        }}
        className={`block rounded-md py-1.5 pr-2 text-xs leading-relaxed transition-colors ${
          isActive
            ? 'bg-primary-50 font-semibold text-primary-600'
            : 'text-gray-500 hover:bg-white hover:text-primary-500'
        }`}
        style={{ paddingLeft: `${12 + (heading.level - baseLevel) * 12}px` }}
      >
        <span className="line-clamp-2">{heading.text}</span>
      </a>
    );
  };

  return (
    <div
      className="fixed inset-0 z-[100] flex items-center justify-center bg-primary-900/50 p-4 backdrop-blur-sm animate-fade-in sm:p-6"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="diligence-modal-title"
        className={`relative flex max-h-[92vh] w-full flex-col overflow-hidden rounded-2xl border border-white/60 bg-white shadow-2xl animate-modal-in transition-all duration-300 ${
          isResultView ? 'max-w-6xl' : 'max-w-2xl'
        }`}
      >
        <div className="h-1 shrink-0 bg-gradient-to-r from-primary-500 via-gold-400 to-primary-500" />

        <header className="relative shrink-0 border-b border-surface-200 bg-gradient-to-b from-surface-50 to-white px-6 pb-6 pt-8 text-center sm:px-10">
          <button
            type="button"
            onClick={onClose}
            aria-label="关闭"
            className="absolute right-4 top-4 rounded-full p-2 text-gray-400 transition-colors hover:bg-surface-100 hover:text-gray-700"
          >
            <X className="h-5 w-5" />
          </button>

          <div className="mx-auto mb-3 flex h-12 w-12 items-center justify-center rounded-xl bg-primary-500 text-gold-300 shadow-glow">
            <Sparkles className="h-6 w-6" />
          </div>
          <h2
            id="diligence-modal-title"
            className="text-xl font-bold tracking-tight text-primary-500 sm:text-2xl"
          >
            多智能体投研初筛工作台
          </h2>
          <p className="mt-2 text-sm text-gray-500">
            {isResultView
              ? `${companyName.trim()} · AI 投研初筛报告`
              : '财务 · 法务 · 业务三位 AI 专家协同分析'}
          </p>
        </header>

        {isResultView ? (
          <>
            <div className="flex min-h-0 max-h-[70vh] flex-1 overflow-hidden bg-white">
              {headings.length > 0 && (
                <aside className="hidden w-60 shrink-0 flex-col border-r border-surface-200 bg-surface-50/80 lg:flex">
                  <div className="flex items-center gap-2 border-b border-surface-200 px-4 py-3">
                    <List className="h-4 w-4 text-primary-500" />
                    <span className="text-[11px] font-semibold uppercase tracking-wider text-gray-500">
                      报告目录
                    </span>
                    <span className="ml-auto rounded-full bg-white px-2 py-0.5 text-[10px] font-medium text-gray-400">
                      {headings.length}
                    </span>
                  </div>
                  <nav aria-label="报告目录" className="min-h-0 flex-1 overflow-y-auto px-2 py-3">
                    {headings.map(renderTocItem)}
                  </nav>
                </aside>
              )}

              <div
                ref={scrollContainerRef}
                className="min-h-0 min-w-0 max-h-[70vh] flex-1 overflow-y-auto bg-white"
              >
                <div className="px-6 py-8 sm:px-10 sm:py-10">
                  {(coreConclusion || summaryCards.length > 0) && (
                    <div className="space-y-4">
                      {coreConclusion && (
                        <section
                          aria-label="核心结论"
                          className="overflow-hidden rounded-xl border border-gold-200 bg-gradient-to-br from-gold-50 via-gold-50/70 to-white p-5 shadow-sm sm:p-6"
                        >
                          <div className="flex gap-3.5">
                            <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-gold-400/20 text-gold-600">
                              <Sparkles className="h-5 w-5" />
                            </div>
                            <div className="min-w-0">
                              <p className="text-[11px] font-semibold uppercase tracking-widest text-gold-600">
                                核心结论
                              </p>
                              <p className="mt-1.5 whitespace-pre-line text-base font-bold leading-relaxed text-primary-800 sm:text-lg">
                                {coreConclusion}
                              </p>
                            </div>
                          </div>
                        </section>
                      )}

                      {summaryCards.length > 0 && (
                        <div className={`grid gap-3 ${summaryGridClass}`}>
                          {summaryCards.map((card, index) => {
                            const meta = SUMMARY_STATUS_META[card.status] ?? SUMMARY_STATUS_META.normal;
                            return (
                              <div
                                key={`${card.label}-${index}`}
                                className="rounded-xl border border-surface-200 bg-white p-4 shadow-sm sm:p-5"
                              >
                                <div className="flex flex-wrap items-center justify-between gap-2">
                                  <p className="text-xs font-medium text-gray-500">{card.label}</p>
                                  <span
                                    className={`inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-[10px] font-medium ${meta.badgeClassName}`}
                                  >
                                    <span
                                      className={`h-1.5 w-1.5 rounded-full ${meta.dotClassName}`}
                                    />
                                    {meta.label}
                                  </span>
                                </div>
                                <p className="mt-3 break-words text-2xl font-bold tracking-tight text-gray-900 sm:text-3xl">
                                  {card.value}
                                </p>
                              </div>
                            );
                          })}
                        </div>
                      )}
                    </div>
                  )}

                  {(coreConclusion || summaryCards.length > 0) && (
                    <div className="mt-7 mb-6 flex items-center gap-3">
                      <span className="shrink-0 text-[10px] font-semibold uppercase tracking-widest text-gray-400">
                        详细报告
                      </span>
                      <div className="h-px flex-1 bg-surface-200" />
                    </div>
                  )}

                  {headings.length > 0 && (
                    <details
                      ref={mobileTocRef}
                      className="mb-6 rounded-xl border border-surface-200 bg-surface-50 lg:hidden"
                    >
                      <summary className="flex cursor-pointer list-none items-center gap-2 px-4 py-3 text-sm font-semibold text-gray-700 select-none [&::-webkit-details-marker]:hidden">
                        <List className="h-4 w-4 text-primary-500" />
                        报告目录
                        <span className="ml-auto text-xs font-normal text-gray-400">点击展开</span>
                      </summary>
                      <nav
                        aria-label="报告目录（移动端）"
                        className="max-h-56 overflow-y-auto border-t border-surface-200 px-2 py-2"
                      >
                        {headings.map(renderTocItem)}
                      </nav>
                    </details>
                  )}

                  <div
                    ref={markdownContainerRef}
                    className="prose prose-lg max-w-none overflow-x-auto prose-headings:text-primary-500 prose-headings:font-semibold prose-a:text-primary-500 prose-strong:text-primary-700 prose-table:text-sm prose-th:bg-primary-50 prose-th:text-primary-700 prose-blockquote:border-l-gold-400 prose-blockquote:bg-gold-50/60 prose-blockquote:px-4 prose-blockquote:py-1 prose-blockquote:not-italic"
                  >
                    {renderedReport}
                  </div>
                </div>
              </div>
            </div>

            <footer className="shrink-0 border-t border-surface-200 bg-surface-50 px-6 py-4 sm:px-10">
              {pdfError && (
                <div
                  role="alert"
                  className="mb-3 flex items-start gap-2 rounded-lg border border-red-100 bg-red-50 px-3 py-2 text-red-600"
                >
                  <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                  <p className="text-xs leading-relaxed">{pdfError}</p>
                </div>
              )}

              <div className="flex flex-col-reverse items-stretch gap-3 sm:flex-row sm:items-center sm:justify-between">
                <p className="text-xs leading-relaxed text-gray-400">
                  报告由 AI 生成，仅供参考，不构成投资建议。
                </p>
                <div className="flex flex-col gap-2.5 sm:flex-row sm:items-center sm:gap-3">
                  <button
                    type="button"
                    onClick={onDownloadPdf}
                    disabled={isDownloadingPdf}
                    className="inline-flex w-full items-center justify-center gap-2 rounded-xl bg-gradient-to-br from-gold-400 to-gold-500 px-5 py-2.5 text-sm font-semibold text-primary-800 shadow-sm transition-all duration-300 hover:from-gold-300 hover:to-gold-400 hover:shadow-md disabled:cursor-not-allowed disabled:opacity-60 sm:w-auto"
                  >
                    {isDownloadingPdf ? (
                      <Loader2 className="h-4 w-4 animate-spin" />
                    ) : (
                      <Download className="h-4 w-4" />
                    )}
                    {isDownloadingPdf ? '正在生成 PDF...' : '下载 PDF 报告'}
                  </button>
                  <div className="flex gap-2.5">
                    <button
                      type="button"
                      onClick={onStartOver}
                      className="inline-flex flex-1 items-center justify-center rounded-xl border border-surface-300 bg-white px-5 py-2.5 text-sm font-semibold text-gray-600 transition-colors hover:border-primary-300 hover:text-primary-500 sm:flex-none"
                    >
                      分析另一家公司
                    </button>
                    <button
                      type="button"
                      onClick={onClose}
                      className="inline-flex flex-1 items-center justify-center rounded-xl bg-gradient-to-br from-primary-500 to-primary-700 px-5 py-2.5 text-sm font-semibold text-white shadow-sm transition-all duration-300 hover:from-primary-600 hover:to-primary-800 hover:shadow-md sm:flex-none"
                    >
                      关闭
                    </button>
                  </div>
                </div>
              </div>
            </footer>
          </>
        ) : (
          <form
            onSubmit={(event) => {
              event.preventDefault();
              onAnalyze();
            }}
            className="min-h-0 flex-1 overflow-y-auto px-6 py-8 sm:px-10 sm:py-10"
          >
            <div
              role="radiogroup"
              aria-label="选择分析方式"
              className="mb-6 grid grid-cols-2 gap-1 rounded-xl border border-surface-200 bg-surface-100 p-1"
            >
              {INPUT_MODE_OPTIONS.map((option) => {
                const Icon = option.icon;
                const selected = inputMode === option.value;
                return (
                  <label
                    key={option.value}
                    className={`flex items-center justify-center gap-2 rounded-lg px-3 py-2.5 text-center text-sm font-medium transition-all focus-within:ring-2 focus-within:ring-primary-500/30 ${
                      selected
                        ? 'bg-white text-primary-600 shadow-sm'
                        : 'text-gray-500 hover:text-gray-700'
                    } ${isLoading ? 'cursor-not-allowed opacity-60' : 'cursor-pointer'}`}
                  >
                    <input
                      type="radio"
                      name="analysis-input-mode"
                      value={option.value}
                      checked={selected}
                      onChange={() => onInputModeChange(option.value)}
                      disabled={isLoading}
                      className="sr-only"
                    />
                    <Icon className="h-4 w-4" />
                    {option.label}
                  </label>
                );
              })}
            </div>

            {inputMode === 'company' ? (
              <>
                <label
                  htmlFor="company-name"
                  className="mb-2.5 block text-sm font-medium text-gray-700"
                >
                  公司名称或股票代码
                </label>
                <div className="relative">
                  <Building2 className="pointer-events-none absolute left-4 top-1/2 h-5 w-5 -translate-y-1/2 text-gray-400" />
                  <input
                    id="company-name"
                    ref={inputRef}
                    type="text"
                    value={companyName}
                    onChange={(event) => onCompanyNameChange(event.target.value)}
                    placeholder="请输入股票代码或公司名称（如：600519 或 贵州茅台）"
                    disabled={isLoading}
                    autoComplete="off"
                    className="h-16 w-full rounded-xl border-2 border-surface-200 bg-surface-50 pl-12 pr-4 text-lg text-gray-900 transition-all placeholder:text-sm placeholder:text-gray-400 focus:border-primary-500 focus:outline-none focus:ring-4 focus:ring-primary-500/10 disabled:cursor-not-allowed disabled:opacity-60 sm:h-20 sm:text-xl sm:placeholder:text-base"
                  />
                </div>
              </>
            ) : (
              <div>
                <p className="mb-2.5 block text-sm font-medium text-gray-700">上传分析资料</p>
                <input
                  id="analysis-file"
                  type="file"
                  accept=".pdf,.txt,application/pdf,text/plain"
                  disabled={isLoading}
                  onChange={(event) => {
                    onSelectFile(event.target.files?.[0] ?? null);
                    // 清空 input 的值，方便用户重新选择同一个文件时也能触发变更
                    event.target.value = '';
                  }}
                  className="peer sr-only"
                />

                {selectedFile ? (
                  <div className="flex flex-col gap-3 rounded-xl border-2 border-primary-200 bg-primary-50/50 px-4 py-4 sm:flex-row sm:items-center">
                    <div className="flex min-w-0 flex-1 items-center gap-3">
                      <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-lg bg-white text-primary-500 shadow-sm">
                        <FileText className="h-5 w-5" />
                      </div>
                      <div className="min-w-0">
                        <p
                          className="truncate text-sm font-semibold text-gray-800"
                          title={selectedFile.name}
                        >
                          {selectedFile.name}
                        </p>
                        <p className="mt-0.5 text-xs text-gray-500">
                          已选择 · {formatFileSize(selectedFile.size)}
                        </p>
                      </div>
                    </div>
                    <div className="flex shrink-0 items-center gap-2 sm:pl-2">
                      <label
                        htmlFor="analysis-file"
                        className={`rounded-lg border border-surface-300 bg-white px-3 py-1.5 text-xs font-medium text-gray-600 transition-colors hover:border-primary-300 hover:text-primary-500 ${
                          isLoading ? 'pointer-events-none opacity-50' : 'cursor-pointer'
                        }`}
                      >
                        重新选择
                      </label>
                      <button
                        type="button"
                        onClick={() => onSelectFile(null)}
                        disabled={isLoading}
                        className="rounded-lg px-2.5 py-1.5 text-xs font-medium text-gray-400 transition-colors hover:bg-red-50 hover:text-red-500 disabled:cursor-not-allowed disabled:opacity-50"
                      >
                        移除
                      </button>
                    </div>
                  </div>
                ) : (
                  <label
                    htmlFor="analysis-file"
                    className="peer-focus-visible:ring-4 peer-focus-visible:ring-primary-500/10 flex cursor-pointer flex-col items-center justify-center gap-2 rounded-xl border-2 border-dashed border-surface-300 bg-surface-50 px-6 py-10 text-center transition-all hover:border-primary-400 hover:bg-primary-50/40"
                  >
                    <span className="flex h-11 w-11 items-center justify-center rounded-full bg-white text-primary-500 shadow-sm">
                      <Upload className="h-5 w-5" />
                    </span>
                    <span className="text-sm font-semibold text-gray-700">
                      点击选择要分析的资料
                    </span>
                    <span className="text-xs leading-relaxed text-gray-400">
                      支持 PDF、TXT 格式，单个文件不超过 {MAX_UPLOAD_MB} MB
                    </span>
                  </label>
                )}
              </div>
            )}

            {errorMsg && (
              <div
                role="alert"
                className="mt-4 flex items-start gap-2.5 rounded-lg border border-red-100 bg-red-50 px-4 py-3 text-red-600"
              >
                <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
                <p className="text-sm leading-relaxed">{errorMsg}</p>
              </div>
            )}

            {isLoading && (
              <div
                role="status"
                className="mt-5 flex items-start gap-3 rounded-lg bg-primary-50 px-4 py-3 text-primary-600"
              >
                <Loader2 className="mt-0.5 h-4 w-4 shrink-0 animate-spin" />
                <p className="text-sm leading-relaxed">
                  正在调度财务、法务、业务三位 AI 专家进行交叉验证，深度分析可能需要几分钟，关闭弹窗会取消本次分析。
                </p>
              </div>
            )}

            <div className="mt-8 flex flex-col-reverse gap-3 sm:flex-row sm:justify-end">
              <button
                type="button"
                onClick={onClose}
                className="inline-flex items-center justify-center rounded-xl border border-surface-300 bg-white px-6 py-3 text-sm font-semibold text-gray-600 transition-colors hover:border-gray-400 hover:text-gray-800"
              >
                取消
              </button>
              <button
                type="submit"
                disabled={!canSubmit || isLoading}
                className="group inline-flex items-center justify-center gap-2 rounded-xl bg-gradient-to-br from-primary-500 to-primary-700 px-7 py-3 text-sm font-semibold text-white shadow-sm transition-all duration-300 hover:from-primary-600 hover:to-primary-800 hover:shadow-md disabled:cursor-not-allowed disabled:opacity-50"
              >
                {isLoading ? (
                  <>
                    <Loader2 className="h-4 w-4 animate-spin" />
                    AI 深度分析中...
                  </>
                ) : (
                  <>
                    <Search className="h-4 w-4" />
                    开始分析
                    <ArrowRight className="h-4 w-4 transition-transform duration-200 group-hover:translate-x-0.5" />
                  </>
                )}
              </button>
            </div>
          </form>
        )}
      </div>
    </div>
  );
}
