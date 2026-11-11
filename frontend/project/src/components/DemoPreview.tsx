import { ArrowRight, Download, FileText, ShieldCheck, Sparkles } from 'lucide-react';

const METRICS = [
  { label: '营收增速', value: '+18.4%' },
  { label: '毛利率', value: '42.1%' },
  { label: '资产负债率', value: '31.7%' },
];

const RISKS = [
  {
    label: '应收账款周转天数上升',
    level: '关注',
    dotClass: 'bg-gold-400',
    badgeClass: 'bg-gold-50 text-gold-700',
  },
  {
    label: '2 条诉讼记录，涉案金额较小',
    level: '低',
    dotClass: 'bg-red-400',
    badgeClass: 'bg-red-50 text-red-600',
  },
  {
    label: '股权质押比例 18.6%',
    level: '正常',
    dotClass: 'bg-primary-400',
    badgeClass: 'bg-primary-50 text-primary-600',
  },
];

const TOC_ITEMS = ['核心结论', '财务健康度', '风险清单', '数据附录'];

type DemoPreviewProps = {
  onOpenWorkbench: () => void;
};

export default function DemoPreview({ onOpenWorkbench }: DemoPreviewProps) {
  return (
    <section id="demo" className="relative overflow-hidden bg-surface-50 py-20 lg:py-28">
      <div className="pointer-events-none absolute left-1/2 top-1/2 h-[460px] w-[720px] -translate-x-1/2 -translate-y-1/2 rounded-full bg-primary-200/25 blur-[130px]" />

      <div className="relative mx-auto max-w-7xl px-5 lg:px-8">
        <div className="mx-auto max-w-2xl text-center">
          <span className="text-xs font-semibold uppercase tracking-widest text-gold-500">
            See it in action
          </span>
          <h2 className="mt-3 text-3xl font-bold tracking-tight text-gray-900 sm:text-4xl">
            一份投研初筛报告，长什么样？
          </h2>
          <p className="mt-4 text-base leading-relaxed text-gray-500">
            不是数据的堆砌，而是把核心结论、风险信号与数据依据分层呈现，方便快速判断。
          </p>
        </div>

        <div className="relative mx-auto mt-12 max-w-5xl">
          <div className="absolute -left-8 top-24 z-10 hidden items-center gap-2 rounded-xl border border-white/70 bg-white/90 px-3 py-2 text-[11px] font-medium text-gray-600 shadow-card backdrop-blur xl:flex">
            <ShieldCheck className="h-3.5 w-3.5 text-primary-500" />
            3 位 AI 专家交叉验证
          </div>
          <div className="absolute -right-8 bottom-24 z-10 hidden items-center gap-2 rounded-xl border border-white/70 bg-white/90 px-3 py-2 text-[11px] font-medium text-gray-600 shadow-card backdrop-blur xl:flex">
            <Download className="h-3.5 w-3.5 text-gold-500" />
            支持 PDF 一键导出
          </div>

          <div className="overflow-hidden rounded-2xl border border-surface-200 bg-white shadow-2xl animate-fade-in">
            {/* 浏览器窗口栏 */}
            <div className="flex items-center gap-2 border-b border-surface-200 bg-surface-50 px-4 py-3">
              <span className="h-2.5 w-2.5 rounded-full bg-red-300" />
              <span className="h-2.5 w-2.5 rounded-full bg-gold-300" />
              <span className="h-2.5 w-2.5 rounded-full bg-primary-300" />
              <div className="ml-3 flex min-w-0 flex-1 items-center gap-1.5 rounded-md bg-white px-3 py-1.5 text-[10px] text-gray-400">
                <FileText className="h-3 w-3 shrink-0 text-primary-400" />
                <span className="truncate">findd.local/report/示例科技</span>
              </div>
              <span className="shrink-0 rounded-full bg-gold-50 px-2 py-0.5 text-[10px] font-medium text-gold-700">
                示例数据
              </span>
            </div>

            <div className="grid lg:grid-cols-[176px_1fr]">
              {/* 报告内目录 */}
              <aside className="hidden border-r border-surface-200 bg-surface-50/60 p-4 lg:block">
                <p className="px-2 text-[10px] font-semibold uppercase tracking-widest text-gray-400">
                  报告目录
                </p>
                <div className="mt-3 space-y-1">
                  {TOC_ITEMS.map((item, index) => (
                    <div
                      key={item}
                      className={`rounded-md px-2 py-1.5 text-[11px] ${
                        index === 0
                          ? 'bg-primary-50 font-semibold text-primary-600'
                          : 'text-gray-500'
                      }`}
                    >
                      {item}
                    </div>
                  ))}
                </div>
                <div className="mt-6 rounded-lg border border-surface-200 bg-white p-2.5">
                  <p className="text-[9px] leading-relaxed text-gray-400">
                    报告由 AI 生成，仅供参考，不构成投资建议。
                  </p>
                </div>
              </aside>

              {/* 报告正文 */}
              <div className="min-w-0 p-5 sm:p-7">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div>
                    <p className="text-[10px] font-semibold uppercase tracking-widest text-gold-500">
                      示例科技股份有限公司
                    </p>
                    <h3 className="mt-1 text-lg font-bold text-gray-900 sm:text-xl">
                      多智能体投研初筛报告
                    </h3>
                  </div>
                  <span className="inline-flex items-center gap-1.5 rounded-full bg-primary-50 px-2.5 py-1 text-[10px] font-medium text-primary-600">
                    <span className="h-1.5 w-1.5 rounded-full bg-primary-400" />
                    交叉验证完成
                  </span>
                </div>

                <div className="mt-4 rounded-xl border border-surface-200 bg-surface-50 p-4">
                  <div className="flex items-center gap-1.5">
                    <Sparkles className="h-3.5 w-3.5 text-gold-500" />
                    <p className="text-[11px] font-semibold text-gray-700">核心结论</p>
                  </div>
                  <p className="mt-1.5 text-[11px] leading-relaxed text-gray-500">
                    主业增长稳健，现金流与利润匹配度较高；需关注应收账款周转放缓及小额诉讼事项。
                  </p>
                </div>

                <div className="mt-4 grid grid-cols-3 gap-2.5">
                  {METRICS.map((metric) => (
                    <div
                      key={metric.label}
                      className="rounded-lg border border-surface-200 bg-white px-3 py-2.5"
                    >
                      <p className="text-[9px] text-gray-400">{metric.label}</p>
                      <p className="mt-1 text-sm font-bold text-primary-600">{metric.value}</p>
                    </div>
                  ))}
                </div>

                <div className="mt-4">
                  <p className="text-[11px] font-semibold text-gray-700">风险提示</p>
                  <ul className="mt-2 space-y-2">
                    {RISKS.map((risk) => (
                      <li
                        key={risk.label}
                        className="flex items-center gap-2 rounded-lg border border-surface-100 px-3 py-2"
                      >
                        <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${risk.dotClass}`} />
                        <span className="min-w-0 flex-1 truncate text-[10px] text-gray-600">
                          {risk.label}
                        </span>
                        <span
                          className={`shrink-0 rounded-full px-2 py-0.5 text-[9px] font-medium ${risk.badgeClass}`}
                        >
                          {risk.level}
                        </span>
                      </li>
                    ))}
                  </ul>
                </div>

                <div className="mt-4 flex flex-wrap items-center justify-between gap-2 border-t border-surface-100 pt-3">
                  <p className="text-[9px] text-gray-400">财务 · 法务 · 业务 三专家交叉验证</p>
                  <span className="inline-flex items-center gap-1 rounded-md bg-primary-50 px-2 py-1 text-[9px] font-medium text-primary-600">
                    <Download className="h-3 w-3" />
                    PDF 报告
                  </span>
                </div>
              </div>
            </div>
          </div>
        </div>

        <div className="mt-10 text-center">
          <button
            type="button"
            onClick={onOpenWorkbench}
            className="group inline-flex items-center justify-center gap-2 rounded-xl bg-gradient-to-br from-primary-500 to-primary-700 px-7 py-3.5 text-sm font-semibold text-white shadow-lg transition-all duration-300 hover:from-primary-600 hover:to-primary-800 hover:shadow-xl"
          >
            立即体验智能分析
            <ArrowRight className="h-4 w-4 transition-transform duration-200 group-hover:translate-x-1" />
          </button>
          <p className="mt-3 text-xs text-gray-400">
            输入公司名称或股票代码即可开始 · 报告保存在本机浏览器
          </p>
        </div>
      </div>
    </section>
  );
}
