import { Clock, Database, EyeOff, Layers, ShieldCheck, Sparkles, XCircle } from 'lucide-react';

const PAINS = [
  {
    icon: Clock,
    title: '耗时数周',
    description: '资料搜集、数据核对、报告撰写全靠人工，项目周期被无限拉长。',
  },
  {
    icon: EyeOff,
    title: '肉眼排查易漏风险',
    description: '公告、财报动辄数百页，关键风险藏在细节里，人眼很难逐一覆盖。',
  },
  {
    icon: Database,
    title: '数据孤岛',
    description: '财务、法务、业务信息散落在不同系统，难以拼成完整的企业画像。',
  },
];

const GAINS = [
  {
    icon: Sparkles,
    title: '分钟级生成',
    description: '财务、法务、业务三位 AI 专家并行分析，快速形成结构化投研初筛报告。',
  },
  {
    icon: Layers,
    title: '多源数据融合',
    description: '把分散的财务、法务与业务信息统一检索、清洗，并做交叉引用。',
  },
  {
    icon: ShieldCheck,
    title: '深度防幻觉',
    description: '关键结论互相验证，冲突信号会被明确标记，而不是被模型悄悄抹平。',
  },
];

export default function WhyUs() {
  return (
    <section id="why-us" className="bg-surface-50 py-20 lg:py-28">
      <div className="mx-auto max-w-7xl px-5 lg:px-8">
        <div className="mx-auto max-w-3xl text-center">
          <span className="text-xs font-semibold uppercase tracking-widest text-gold-500">
            Why Us
          </span>
          <h2 className="mt-3 text-3xl font-bold tracking-tight text-gray-900 sm:text-4xl">
            把数周的公开信息风险扫描，变成分钟级的判断
          </h2>
          <p className="mt-4 text-base leading-relaxed text-gray-500">
            同样的公开信息，不同的分析方式。传统人工风险扫描受限于人力与数据孤岛，AI
            协同让效率与可靠性同时提升。
          </p>
        </div>

        <div className="mt-14 grid gap-5 lg:grid-cols-2 lg:gap-6">
          {/* 传统人工风险扫描 */}
          <article
            className="relative overflow-hidden rounded-2xl border border-red-100 bg-white p-7 shadow-sm animate-fade-in sm:p-8"
            style={{ animationDelay: '80ms' }}
          >
            <div className="absolute inset-x-0 top-0 h-1 bg-gradient-to-r from-red-300 via-red-400 to-red-500" />

            <div className="flex items-center justify-between gap-4">
              <div className="flex items-center gap-3">
                <div className="flex h-11 w-11 items-center justify-center rounded-xl bg-red-50 text-red-500">
                  <XCircle className="h-5 w-5" strokeWidth={2} />
                </div>
                <div>
                  <p className="text-[10px] font-bold uppercase tracking-widest text-red-400">
                    Traditional
                  </p>
                  <h3 className="text-lg font-bold text-gray-900">传统人工风险扫描</h3>
                </div>
              </div>
              <span className="shrink-0 rounded-full bg-red-50 px-2.5 py-1 text-[10px] font-semibold text-red-500">
                效率瓶颈
              </span>
            </div>

            <ul className="mt-7 space-y-5">
              {PAINS.map((item) => (
                <li key={item.title} className="flex gap-3">
                  <item.icon className="mt-0.5 h-4 w-4 shrink-0 text-red-400" strokeWidth={2} />
                  <div>
                    <p className="text-sm font-semibold text-gray-800">{item.title}</p>
                    <p className="mt-1 text-sm leading-relaxed text-gray-500">{item.description}</p>
                  </div>
                </li>
              ))}
            </ul>
          </article>

          {/* 智投明鉴 AI */}
          <article
            className="relative overflow-hidden rounded-2xl bg-primary-500 p-7 text-white shadow-card-hover animate-fade-in sm:p-8"
            style={{ animationDelay: '160ms' }}
          >
            <div className="pointer-events-none absolute -right-16 -top-16 h-48 w-48 rounded-full bg-gold-400/20 blur-3xl" />
            <div className="absolute inset-x-0 top-0 h-1 bg-gradient-to-r from-gold-300 via-gold-400 to-gold-500" />

            <div className="relative">
              <div className="flex items-center justify-between gap-4">
                <div className="flex items-center gap-3">
                  <div className="flex h-11 w-11 items-center justify-center rounded-xl bg-white/10 text-gold-300">
                    <Sparkles className="h-5 w-5" strokeWidth={2} />
                  </div>
                  <div>
                    <p className="text-[10px] font-bold uppercase tracking-widest text-gold-300">
                      With FinDD
                    </p>
                    <h3 className="text-lg font-bold text-white">智投明鉴 AI</h3>
                  </div>
                </div>
                <span className="shrink-0 rounded-full border border-gold-400/30 bg-gold-400/10 px-2.5 py-1 text-[10px] font-semibold text-gold-200">
                  推荐方案
                </span>
              </div>

              <ul className="mt-7 space-y-5">
                {GAINS.map((item) => (
                  <li key={item.title} className="flex gap-3">
                    <item.icon className="mt-0.5 h-4 w-4 shrink-0 text-gold-300" strokeWidth={2} />
                    <div>
                      <p className="text-sm font-semibold text-white">{item.title}</p>
                      <p className="mt-1 text-sm leading-relaxed text-primary-100">
                        {item.description}
                      </p>
                    </div>
                  </li>
                ))}
              </ul>
            </div>
          </article>
        </div>

        <p className="mx-auto mt-8 max-w-2xl text-center text-xs leading-relaxed text-gray-400">
          把时间留给判断，把重复劳动交给 AI。
        </p>
      </div>
    </section>
  );
}
