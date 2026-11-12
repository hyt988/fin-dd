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
import type { AnalysisInputMode } from './lib/analyze';
import {
  MAX_UPLOAD_BYTES,
  describeAnalyzeHttpError,
  isSupportedUploadFile,
} from './lib/analyze';
import type { SummaryCard } from './lib/report';
import { normalizeCoreConclusion, normalizeSummaryCards } from './lib/report';

const API_URL = 'https://fin-dd.onrender.com/api/analyze';

export default function App() {
  const [inputMode, setInputMode] = useState<AnalysisInputMode>('company');
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
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
    setInputMode('company');
    setSelectedFile(null);
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
    setInputMode('company');
    setSelectedFile(null);
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

  // 切换输入方式时清掉上一条错误提示，避免提示和当前方式对不上
  const handleInputModeChange = (mode: AnalysisInputMode) => {
    setInputMode(mode);
    setErrorMsg('');
  };

  // 选择文件时先在浏览器里校验格式、空文件和大小，不合格就直接提示，不浪费一次网络请求
  const handleSelectFile = (file: File | null) => {
    setErrorMsg('');

    if (!file) {
      setSelectedFile(null);
      return;
    }

    if (!isSupportedUploadFile(file.name)) {
      setSelectedFile(null);
      setErrorMsg('暂不支持这种文件格式，请上传 PDF 或 TXT 文件。');
      return;
    }

    if (file.size === 0) {
      setSelectedFile(null);
      setErrorMsg('选择的文件是空的，请重新选择。');
      return;
    }

    if (file.size > MAX_UPLOAD_BYTES) {
      setSelectedFile(null);
      setErrorMsg('文件过大（超过 100 MB），请压缩或拆分后重新上传。');
      return;
    }

    setSelectedFile(file);
  };

  const handleAnalyze = async () => {
    if (abortRef.current) return;

    const name = companyName.trim();
    const file = inputMode === 'file' ? selectedFile : null;

    // 两种方式各自必填：公司名称不能为空，文件模式下必须先选文件
    if (inputMode === 'file' ? !file : !name) return;

    resetPdfState();
    setIsLoading(true);
    setErrorMsg('');
    setReportText('');
    setSummaryCards([]);
    setCoreConclusion('');

    const controller = new AbortController();
    abortRef.current = controller;

    try {
      // 后端用 Form(...) / File(...) 接收，必须使用 multipart/form-data 表单；
      // company_name 和 file 只能二选一，不能同时提交。
      const form = new FormData();
      if (file) {
        form.append('file', file);
      } else {
        form.append('company_name', name);
      }

      const response = await fetch(API_URL, {
        method: 'POST',
        body: form,
        signal: controller.signal,
      });

      // 有些网关（例如 413 请求体过大）会直接返回 HTML，这里解析失败时当作没有 JSON
      const data = await response.json().catch(() => null);

      if (!response.ok || data?.status === 'error') {
        const detail =
          (typeof data?.message === 'string' ? data.message : '') ||
          (typeof data?.detail === 'string' ? data.detail : '');
        throw new Error(describeAnalyzeHttpError(response.status, detail));
      }

      if (!data?.report) {
        throw new Error('后端没有返回报告内容，请稍后重试。');
      }

      const report = data.report as string;
      const nextSummaryCards = normalizeSummaryCards(data.summary_cards);
      const nextCoreConclusion = normalizeCoreConclusion(data.core_conclusion);

      // 文件模式没有公司名称输入框，用文件名（去掉后缀）作为报告标题和下载文件名
      const displayName = file
        ? file.name.replace(/\.[^.]+$/, '').trim() || file.name
        : name;

      setCompanyName(displayName);
      setReportText(report);
      setSummaryCards(nextSummaryCards);
      setCoreConclusion(nextCoreConclusion);
      setHistory((prev) =>
        [
          {
            companyName: displayName,
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
          ? '无法连接后端服务，请稍后重试。'
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
          ? '无法连接后端服务，请稍后重试。'
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
          inputMode={inputMode}
          onInputModeChange={handleInputModeChange}
          companyName={companyName}
          onCompanyNameChange={setCompanyName}
          selectedFile={selectedFile}
          onSelectFile={handleSelectFile}
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
