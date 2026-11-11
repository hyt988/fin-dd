import { ArrowRight, Sparkles } from 'lucide-react';

type PageCtaProps = {
  onOpenWorkbench: () => void;
};

export default function PageCta({ onOpenWorkbench }: PageCtaProps) {
  return (
    <section className="pb-24 lg:pb-32">
      <div className="max-w-7xl mx-auto px-5 lg:px-8">
        <div className="relative overflow-hidden rounded-3xl bg-primary-500 px-6 py-12 text-center shadow-card-hover sm:px-12 sm:py-16">
          <div className="pointer-events-none absolute -right-20 -top-20 h-64 w-64 rounded-full bg-gold-400/20 blur-3xl" />
          <div className="pointer-events-none absolute -bottom-24 -left-20 h-64 w-64 rounded-full bg-primary-300/20 blur-3xl" />

          <div className="relative">
            <span className="inline-flex items-center gap-2 rounded-full border border-white/15 bg-white/10 px-4 py-1.5 text-xs font-medium text-gold-200">
              <Sparkles className="h-3.5 w-3.5" />
              三位 AI 专家，随时待命
            </span>
            <h2 className="mt-5 text-2xl font-bold tracking-tight text-white sm:text-3xl">
              让 AI 成为您的投研初筛搭档
            </h2>
            <p className="mx-auto mt-3 max-w-xl text-sm leading-relaxed text-primary-100 sm:text-base">
              输入公司名称或股票代码，几分钟内获得一份可追踪、可验证的投研初筛报告。
            </p>
            <button
              type="button"
              onClick={onOpenWorkbench}
              className="group mt-8 inline-flex items-center gap-2 rounded-xl bg-white px-7 py-3.5 text-sm font-semibold text-primary-600 shadow-lg transition-all duration-300 hover:bg-gold-50 hover:shadow-xl"
            >
              立即开启智能投研初筛
              <ArrowRight className="h-4 w-4 transition-transform duration-200 group-hover:translate-x-1" />
            </button>
          </div>
        </div>
      </div>
    </section>
  );
}
