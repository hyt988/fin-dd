import { Bot, Database, FileText, ShieldCheck, Upload } from 'lucide-react';
import PageCta from '../components/PageCta';

const STEPS = [
  {
    icon: Upload,
    title: '输入代码或上传文件',
    description: '输入 6 位股票代码或公司名称，也可以上传公告、研报等 PDF / TXT 材料。',
    detail: '股票代码 · 公司名称 · PDF / TXT',
  },
  {
    icon: Database,
    title: '多源数据混合检索',
    description: '聚合行情、财务、公告、诉讼与公开资讯，统一清洗、去重并建立检索索引。',
    detail: '多源检索 · 自动清洗 · 交叉引用',
  },
  {
    icon: Bot,
    title: '多智能体并行分析',
    description: '财务、法务、业务三位 AI 专家并行工作，各自独立完成研究并形成初步结论。',
    detail: '并行执行 · 独立研判 · 各自成稿',
  },
  {
    icon: ShieldCheck,
    title: '风险交叉验证',
    description: '三方结论相互校验，标记冲突信号与低置信度判断，尽可能降低模型幻觉。',
    detail: '交叉验证 · 冲突标记 · 置信度评估',
  },
  {
    icon: FileText,
    title: '生成PDF研报',
    description: '将核心结论、风险清单与数据依据整理为结构化研报，便于归档与投决会讨论。',
    detail: '结构化报告 · 风险清单 · 数据附录',
  },
];

type WorkflowProps = {
  onOpenWorkbench: () => void;
};

export default function Workflow({ onOpenWorkbench }: WorkflowProps) {
  return (
    <>
      <section className="relative overflow-hidden bg-gradient-to-b from-surface-50 via-white to-white pt-36 pb-16 lg:pt-44 lg:pb-20">
        <div className="pointer-events-none absolute -top-24 right-0 h-80 w-80 rounded-full bg-primary-200/20 blur-[120px]" />

        <div className="relative max-w-7xl mx-auto px-5 lg:px-8">
          <div className="max-w-3xl animate-fade-in">
            <span className="text-xs font-semibold tracking-widest text-gold-500 uppercase">
              Workflow
            </span>
            <h1 className="mt-3 text-3xl sm:text-4xl lg:text-5xl font-bold tracking-tight leading-[1.2] text-gray-900">
              工作流程 - 从数据到洞察
            </h1>
            <p className="mt-5 text-base sm:text-lg text-gray-500 leading-relaxed">
              从一条股票代码到一份可验证的研报，五个步骤完成一次完整的多智能体投研初筛。
            </p>
            <div className="mt-8 h-1 w-16 rounded-full bg-gradient-to-r from-primary-500 to-gold-400" />
          </div>

          <ol className="mx-auto mt-14 max-w-3xl">
            {STEPS.map((step, index) => (
              <li key={step.title} className="relative flex gap-5 pb-8 last:pb-0 sm:gap-7">
                {index < STEPS.length - 1 && (
                  <span
                    aria-hidden="true"
                    className="absolute left-6 top-12 -bottom-8 w-px bg-gradient-to-b from-primary-200 via-surface-300 to-surface-200"
                  />
                )}

                <div className="relative z-10 flex h-12 w-12 shrink-0 items-center justify-center rounded-full border border-primary-100 bg-white text-primary-500 shadow-sm">
                  <step.icon className="h-5 w-5" strokeWidth={2} />
                </div>

                <div className="min-w-0 flex-1 rounded-2xl border border-surface-200 bg-white p-5 shadow-sm transition-all duration-300 hover:border-primary-200 hover:shadow-card sm:p-6">
                  <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                    <span className="text-[10px] font-bold uppercase tracking-widest text-gold-500">
                      Step 0{index + 1}
                    </span>
                    <h2 className="text-lg font-bold text-gray-900">{step.title}</h2>
                  </div>
                  <p className="mt-2 text-sm leading-relaxed text-gray-500">{step.description}</p>
                  <p className="mt-3 inline-flex rounded-md bg-surface-50 px-2.5 py-1.5 text-xs text-gray-500">
                    {step.detail}
                  </p>
                </div>
              </li>
            ))}
          </ol>

          <div className="mx-auto mt-10 flex max-w-3xl items-start gap-3 rounded-xl border border-primary-100 bg-primary-50 px-5 py-4 text-primary-600">
            <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0" />
            <p className="text-sm leading-relaxed">
              全流程可追溯：关键结论附数据来源，冲突观点会被明确标记，方便分析师复核。
            </p>
          </div>
        </div>
      </section>

      <PageCta onOpenWorkbench={onOpenWorkbench} />
    </>
  );
}
