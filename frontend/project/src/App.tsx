import { useEffect, useRef, useState } from 'react';
import { BrowserRouter, Route, Routes } from 'react-router-dom';
import Navbar from './components/Navbar';
import DiligenceModal from './components/DiligenceModal';
import HistoryDrawer from './components/HistoryDrawer';
import Footer from './components/Footer';
import ScrollToTop from './components/ScrollToTop';
import Home from './pages/Home';
import FeaturesPage from './pages/Features';
import Workflow from './pages/Workflow';
import Pricing from './pages/Pricing';
import About from './pages/About';
import NotFound from './pages/NotFound';
import type { HistoryItem } from './lib/history';
import { HISTORY_LIMIT, HISTORY_STORAGE_KEY, loadHistory, saveHistory } from './lib/history';
import { downloadReportPdf } from './lib/pdfDownload';
import type { SummaryCard } from './lib/report';
import { normalizeCoreConclusion, normalizeSummaryCards } from './lib/report';

const API_URL = 'http://127.0.0.1:8000/api/analyze';

export default function App() {
  const [companyName, setCompanyName] = useState('');
  const [showModal, setShowModal] = useState(false);
  const [reportText, setReportText] = useState('');
  const [summaryCards, setSummaryCards] = useState<SummaryCard[]>([]);
  const [coreConclusion, setCoreConclusion] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [errorMsg, setErrorMsg] = useState('');
  const [history, setHistory] = useState<HistoryItem[]>(() => loadHistory());
  const [showHistory, setShowHistory] = useState(false);
  const [isDownloadingPdf, setIsDownloadingPdf] = useState(false);
  const [pdfError, setPdfError] = useState('');
  const abortRef = useRef<AbortController | null>(null);
  const pdfAbortRef = useRef<AbortController | null>(null);

  // 历史记录发生变化时写回 localStorage（最多 10 条）
  useEffect(() => {
    saveHistory(history);
  }, [history]);

  // 同一浏览器的其他标签页清空/新增记录时，同步当前列表
  useEffect(() => {
    const handleStorage = (event: StorageEvent) => {
      if (event.key === HISTORY_STORAGE_KEY) {
        setHistory(loadHistory());
      }
    };
    window.addEventListener('storage', handleStorage);
    return () => window.removeEventListener('storage', handleStorage);
  }, []);

  const openModal = () => {
    setShowModal(true);
  };

  // 取消正在生成的 PDF，并清空下载状态
  const resetPdfState = () => {
    pdfAbortRef.current?.abort();
    pdfAbortRef.current = null;
    setIsDownloadingPdf(false);
    setPdfError('');
  };

  // 清空弹窗内的数据；如果请求还在进行，同时取消它
  const resetModal = () => {
    abortRef.current?.abort();
    abortRef.current = null;
    resetPdfState();
    setReportText('');
    setSummaryCards([]);
    setCoreConclusion('');
    setCompanyName('');
    setErrorMsg('');
    setIsLoading(false);
  };

  const closeModal = () => {
    resetModal();
    setShowModal(false);
  };

  const handleStartOver = () => {
    resetModal();
  };

  const openHistoryItem = (item: HistoryItem) => {
    abortRef.current?.abort();
    abortRef.current = null;
    resetPdfState();
    setIsLoading(false);
    setErrorMsg('');
    setCompanyName(item.companyName);
    setReportText(item.reportText);
    setSummaryCards(item.summaryCards ?? []);
    setCoreConclusion(item.coreConclusion ?? '');
    setShowHistory(false);
    setShowModal(true);
  };

  const clearHistory = () => {
    setHistory([]);
  };

  const handleAnalyze = async () => {
    const name = companyName.trim();
    if (!name || abortRef.current) return;

    resetPdfState();
    setIsLoading(true);
    setErrorMsg('');
    setReportText('');
    setSummaryCards([]);
    setCoreConclusion('');

    const controller = new AbortController();
    abortRef.current = controller;

    try {
      // 后端要求表单格式（multipart/form-data），不能用 JSON 提交
      const form = new FormData();
      form.append('company_name', name);

      const response = await fetch(API_URL, {
        method: 'POST',
        body: form,
        signal: controller.signal,
      });

      const data = await response.json().catch(() => null);

      if (!response.ok || data?.status === 'error') {
        const detail =
          data?.message ||
          (typeof data?.detail === 'string' ? data.detail : '') ||
          `请求失败（HTTP ${response.status}）`;
        throw new Error(detail);
      }

      if (!data?.report) {
        throw new Error('后端没有返回报告内容，请稍后重试。');
      }

      const report = data.report as string;
      const nextSummaryCards = normalizeSummaryCards(data.summary_cards);
      const nextCoreConclusion = normalizeCoreConclusion(data.core_conclusion);

      setReportText(report);
      setSummaryCards(nextSummaryCards);
      setCoreConclusion(nextCoreConclusion);
      setHistory((prev) =>
        [
          {
            companyName: name,
            reportText: report,
            timestamp: Date.now(),
            ...(nextSummaryCards.length > 0 ? { summaryCards: nextSummaryCards } : {}),
            ...(nextCoreConclusion ? { coreConclusion: nextCoreConclusion } : {}),
          },
          ...prev,
        ].slice(0, HISTORY_LIMIT)
      );
    } catch (error) {
      if (controller.signal.aborted) return;
      const message = error instanceof Error ? error.message : '';
      setErrorMsg(
        message === 'Failed to fetch'
          ? '无法连接后端服务，请确认 FastAPI 已在 http://127.0.0.1:8000 启动。'
          : message || '请求失败，请稍后重试。'
      );
    } finally {
      if (!controller.signal.aborted) {
        setIsLoading(false);
      }
      if (abortRef.current === controller) {
        abortRef.current = null;
      }
    }
  };

  const handleDownloadPdf = async () => {
    if (!reportText || pdfAbortRef.current) return;

    setIsDownloadingPdf(true);
    setPdfError('');

    const controller = new AbortController();
    pdfAbortRef.current = controller;

    try {
      await downloadReportPdf({
        reportText,
        companyName: companyName.trim() || '未命名公司',
        signal: controller.signal,
      });
    } catch (error) {
      if (controller.signal.aborted) return;
      const message = error instanceof Error ? error.message : '';
      setPdfError(
        message === 'Failed to fetch'
          ? '无法连接后端服务，请确认 FastAPI 已在 http://127.0.0.1:8000 启动。'
          : message || 'PDF 下载失败，请稍后重试。'
      );
    } finally {
      if (!controller.signal.aborted) {
        setIsDownloadingPdf(false);
      }
      if (pdfAbortRef.current === controller) {
        pdfAbortRef.current = null;
      }
    }
  };

  return (
    <BrowserRouter>
      <ScrollToTop />
      <div className="flex min-h-screen flex-col bg-white font-sans antialiased">
        <Navbar onOpenWorkbench={openModal} />

        <main className="flex-1">
          <Routes>
            <Route path="/" element={<Home onOpenWorkbench={openModal} />} />
            <Route path="/features" element={<FeaturesPage onOpenWorkbench={openModal} />} />
            <Route path="/workflow" element={<Workflow onOpenWorkbench={openModal} />} />
            <Route path="/pricing" element={<Pricing onOpenWorkbench={openModal} />} />
            <Route path="/about" element={<About onOpenWorkbench={openModal} />} />
            <Route path="*" element={<NotFound />} />
          </Routes>
        </main>

        <Footer />

        <HistoryDrawer
          open={showHistory}
          items={history}
          onOpen={() => setShowHistory(true)}
          onClose={() => setShowHistory(false)}
          onSelect={openHistoryItem}
          onClear={clearHistory}
        />
        <DiligenceModal
          open={showModal}
          companyName={companyName}
          onCompanyNameChange={setCompanyName}
          isLoading={isLoading}
          reportText={reportText}
          summaryCards={summaryCards}
          coreConclusion={coreConclusion}
          errorMsg={errorMsg}
          isDownloadingPdf={isDownloadingPdf}
          pdfError={pdfError}
          onAnalyze={handleAnalyze}
          onClose={closeModal}
          onStartOver={handleStartOver}
          onDownloadPdf={handleDownloadPdf}
        />
      </div>
    </BrowserRouter>
  );
}
