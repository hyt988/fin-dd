import { Eye, Sparkles, Target, Users } from 'lucide-react';
import PageCta from '../components/PageCta';

const VALUES = [
  {
    icon: Target,
    title: '深度洞察',
    description: '不做数据搬运工，深入指标背后的经营逻辑与行业上下文。',
  },
  {
    icon: Eye,
    title: '可解释可信',
    description: '结论尽量给出依据，关键判断可追溯，拒绝无法验证的黑盒输出。',
  },
  {
    icon: Users,
    title: '人机协同',
    description: 'AI 负责提效与交叉验证，分析师保留最终判断与决策权。',
  },
];

type AboutProps = {
  onOpenWorkbench: () => void;
};

export default function About({ onOpenWorkbench }: AboutProps) {
  return (
    <>
      <section className="relative overflow-hidden bg-gradient-to-b from-surface-50 via-white to-white pt-36 pb-16 lg:pt-44 lg:pb-20">
        <div className="pointer-events-none absolute -top-24 right-0 h-80 w-80 rounded-full bg-primary-200/20 blur-[120px]" />

        <div className="relative max-w-7xl mx-auto px-5 lg:px-8">
          <div className="max-w-3xl animate-fade-in">
            <span className="text-xs font-semibold tracking-widest text-gold-500 uppercase">
              About FinDD
            </span>
            <h1 className="mt-3 text-3xl sm:text-4xl lg:text-5xl font-bold tracking-tight leading-[1.2] text-gray-900">
              关于智投明鉴
            </h1>
            <div className="mt-8 h-1 w-16 rounded-full bg-gradient-to-r from-primary-500 to-gold-400" />
          </div>

          <div className="mt-12 grid gap-5 lg:grid-cols-[1.4fr_1fr] lg:gap-6">
            <div className="rounded-2xl border border-surface-200 bg-white p-8 shadow-card sm:p-10">
              <span className="text-xs font-semibold tracking-widest text-gold-500 uppercase">
                Our Mission
              </span>
              <p className="mt-4 text-lg font-medium leading-relaxed text-gray-700 sm:text-xl">
                智投明鉴（FinDD）致力于用大语言模型技术重塑公开信息风险扫描效率。我们相信，数据的价值在于深度洞察，AI
                应当成为分析师的得力助手，而不是黑盒。
              </p>
              <div className="mt-6 h-px bg-gradient-to-r from-primary-200 to-transparent" />
              <p className="mt-5 text-sm leading-relaxed text-gray-500">
                我们希望把分析师从重复的信息搜集里解放出来，把时间留给判断、质疑与决策。
              </p>
            </div>

            <div className="relative overflow-hidden rounded-2xl bg-primary-500 p-8 text-white shadow-card-hover sm:p-10">
              <div className="pointer-events-none absolute -right-12 -top-12 h-48 w-48 rounded-full bg-gold-400/20 blur-3xl" />
              <div className="relative">
                <div className="flex h-11 w-11 items-center justify-center rounded-xl bg-white/10">
                  <Sparkles className="h-5 w-5 text-gold-300" />
                </div>
                <p className="mt-6 text-xl font-bold">以 AI 放大分析师的判断力</p>
                <p className="mt-2 text-sm leading-relaxed text-primary-100">
                  不是替代人，而是把人从信息搬运中解放出来。
                </p>
                <div className="mt-8 grid grid-cols-2 gap-6 border-t border-white/15 pt-6">
                  <div>
                    <p className="text-2xl font-bold text-gold-300">5,000+</p>
                    <p className="mt-1 text-xs text-primary-200">A 股覆盖</p>
                  </div>
                  <div>
                    <p className="text-2xl font-bold text-gold-300">3 位</p>
                    <p className="mt-1 text-xs text-primary-200">AI 专家协同</p>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>

      <section className="bg-surface-50 py-16 lg:py-20">
        <div className="max-w-7xl mx-auto px-5 lg:px-8">
          <div className="mx-auto max-w-2xl text-center">
            <span className="text-xs font-semibold tracking-widest text-gold-500 uppercase">
              Our Principles
            </span>
            <h2 className="mt-3 text-2xl sm:text-3xl font-bold tracking-tight text-gray-900">
              我们坚持的三件事
            </h2>
          </div>

          <div className="mt-10 grid gap-5 md:grid-cols-3 lg:gap-6">
            {VALUES.map((value) => (
              <article
                key={value.title}
                className="group rounded-2xl border border-surface-200 bg-white p-7 transition-all duration-300 hover:-translate-y-1 hover:border-primary-200 hover:shadow-card-hover"
              >
                <div className="flex h-11 w-11 items-center justify-center rounded-xl bg-primary-50 text-primary-500 transition-colors duration-300 group-hover:bg-primary-500 group-hover:text-white">
                  <value.icon className="h-5 w-5" strokeWidth={2} />
                </div>
                <h3 className="mt-5 text-lg font-bold text-gray-900">{value.title}</h3>
                <p className="mt-2 text-sm leading-relaxed text-gray-500">{value.description}</p>
              </article>
            ))}
          </div>
        </div>
      </section>

      <PageCta onOpenWorkbench={onOpenWorkbench} />
    </>
  );
}
