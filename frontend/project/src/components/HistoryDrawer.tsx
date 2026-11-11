import { useEffect, useRef, useState } from 'react';
import { ArrowUpRight, FileText, History, Trash2, X } from 'lucide-react';
import type { HistoryItem } from '../lib/history';
import { HISTORY_LIMIT } from '../lib/history';

type HistoryDrawerProps = {
  open: boolean;
  items: HistoryItem[];
  onOpen: () => void;
  onClose: () => void;
  onSelect: (item: HistoryItem) => void;
  onClear: () => void;
};

function formatTimestamp(timestamp: number) {
  return new Date(timestamp).toLocaleString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  });
}

export default function HistoryDrawer({
  open,
  items,
  onOpen,
  onClose,
  onSelect,
  onClear,
}: HistoryDrawerProps) {
  const [confirmingClear, setConfirmingClear] = useState(false);
  const panelRef = useRef<HTMLElement>(null);
  const onCloseRef = useRef(onClose);

  useEffect(() => {
    onCloseRef.current = onClose;
  }, [onClose]);

  // 抽屉打开时锁定背景滚动、支持 Esc 关闭，并聚焦到面板
  useEffect(() => {
    if (!open) {
      setConfirmingClear(false);
      return;
    }

    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';

    const timer = window.setTimeout(() => panelRef.current?.focus(), 60);
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onCloseRef.current();
    };
    document.addEventListener('keydown', handleKeyDown);

    return () => {
      window.clearTimeout(timer);
      document.body.style.overflow = previousOverflow;
      document.removeEventListener('keydown', handleKeyDown);
    };
  }, [open]);

  useEffect(() => {
    if (items.length === 0) setConfirmingClear(false);
  }, [items.length]);

  const handleClear = () => {
    onClear();
    setConfirmingClear(false);
  };

  return (
    <>
      <button
        type="button"
        onClick={onOpen}
        aria-label={`打开历史投研初筛记录，共 ${items.length} 条`}
        title="历史投研初筛记录"
        className="group fixed right-0 top-1/2 z-40 flex -translate-y-1/2 flex-col items-center gap-1.5 rounded-l-xl bg-primary-500 px-2 py-3 text-white shadow-lg shadow-primary-900/20 transition-all hover:bg-primary-600 hover:px-2.5 sm:gap-2 sm:px-2.5 sm:py-4"
      >
        <History className="h-4 w-4" />
        <span className="hidden text-[10px] font-medium tracking-[0.18em] [writing-mode:vertical-rl] sm:block">
          历史记录
        </span>
        {items.length > 0 && (
          <span className="flex h-4 min-w-[16px] items-center justify-center rounded-full bg-gold-400 px-1 text-[9px] font-bold leading-none text-primary-800">
            {items.length}
          </span>
        )}
      </button>

      {open && (
        <div className="fixed inset-0 z-[90]">
          <div
            className="absolute inset-0 bg-primary-900/40 backdrop-blur-sm animate-fade-in"
            onClick={onClose}
            aria-hidden="true"
          />

          <aside
            ref={panelRef}
            tabIndex={-1}
            role="dialog"
            aria-modal="true"
            aria-labelledby="history-drawer-title"
            className="absolute right-0 top-0 flex h-full w-full max-w-md flex-col bg-white shadow-2xl outline-none animate-drawer-in"
          >
            <div className="h-1 shrink-0 bg-gradient-to-r from-primary-500 via-gold-400 to-primary-500" />

            <header className="flex items-start justify-between gap-4 border-b border-surface-200 bg-gradient-to-b from-surface-50 to-white px-6 pb-5 pt-6">
              <div>
                <h2
                  id="history-drawer-title"
                  className="text-lg font-bold tracking-tight text-gray-900"
                >
                  📜 历史投研初筛记录
                </h2>
                <p className="mt-1 text-xs text-gray-400">
                  保存在本机浏览器 · 最多保留最近 {HISTORY_LIMIT} 条
                </p>
              </div>
              <button
                type="button"
                onClick={onClose}
                aria-label="关闭历史记录"
                className="rounded-full p-2 text-gray-400 transition-colors hover:bg-surface-100 hover:text-gray-700"
              >
                <X className="h-5 w-5" />
              </button>
            </header>

            {items.length > 0 && (
              <div className="flex min-h-[44px] items-center justify-between gap-3 border-b border-surface-200 px-6 py-2.5">
                <span className="text-xs text-gray-400">共 {items.length} 条</span>
                {confirmingClear ? (
                  <div className="flex items-center gap-3">
                    <span className="text-xs text-gray-500">确定清空全部记录？</span>
                    <button
                      type="button"
                      onClick={handleClear}
                      className="text-xs font-semibold text-red-600 transition-colors hover:text-red-700"
                    >
                      确认清空
                    </button>
                    <button
                      type="button"
                      onClick={() => setConfirmingClear(false)}
                      className="text-xs text-gray-500 transition-colors hover:text-gray-700"
                    >
                      取消
                    </button>
                  </div>
                ) : (
                  <button
                    type="button"
                    onClick={() => setConfirmingClear(true)}
                    className="inline-flex items-center gap-1.5 text-xs font-medium text-gray-400 transition-colors hover:text-red-600"
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                    清空历史
                  </button>
                )}
              </div>
            )}

            {items.length === 0 ? (
              <div className="flex flex-1 flex-col items-center justify-center px-8 pb-20 text-center">
                <div className="mb-4 flex h-16 w-16 items-center justify-center rounded-2xl bg-surface-50 text-surface-300">
                  <History className="h-7 w-7" />
                </div>
                <p className="text-sm font-medium text-gray-700">暂无历史记录</p>
                <p className="mt-1.5 max-w-xs text-xs leading-relaxed text-gray-400">
                  完成一次智能投研初筛后，报告会自动保存在这里，下次点击即可直接查看。
                </p>
              </div>
            ) : (
              <ul className="flex-1 space-y-2.5 overflow-y-auto px-4 py-4">
                {items.map((item, index) => (
                  <li key={`${item.timestamp}-${index}`}>
                    <button
                      type="button"
                      onClick={() => onSelect(item)}
                      className="group flex w-full items-center gap-3 rounded-xl border border-surface-200 bg-white p-3.5 text-left transition-all hover:border-primary-200 hover:bg-surface-50 hover:shadow-sm"
                    >
                      <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-primary-50 text-primary-500">
                        <FileText className="h-5 w-5" />
                      </div>
                      <div className="min-w-0 flex-1">
                        <p className="truncate text-sm font-semibold text-gray-900 transition-colors group-hover:text-primary-600">
                          {item.companyName}
                        </p>
                        <p className="mt-0.5 text-xs text-gray-400">
                          {formatTimestamp(item.timestamp)}
                        </p>
                      </div>
                      <ArrowUpRight className="h-4 w-4 shrink-0 text-gray-300 transition-colors group-hover:text-primary-500" />
                    </button>
                  </li>
                ))}
              </ul>
            )}

            <footer className="shrink-0 border-t border-surface-200 px-6 py-4">
              <p className="text-[11px] leading-relaxed text-gray-400">
                历史记录只保存在当前浏览器，清理浏览器数据后会一并删除。
              </p>
            </footer>
          </aside>
        </div>
      )}
    </>
  );
}
