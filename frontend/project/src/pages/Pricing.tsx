import { ArrowRight, Check } from 'lucide-react';

const PLANS = [
  {
    name: '基础版',
    price: '免费',
    suffix: '',
    description: '适合个人投资者和分析新人，先体验多智能体投研初筛。',
    features: [
      '每月 3 次智能投研初筛',
      '财务 / 法务 / 业务基础报告',
      '本地历史记录（最近 10 条）',
      'Markdown 报告在线查看',
    ],
    cta: '免费开始',
    highlighted: false,
  },
  {
    name: '专业版',
    price: '¥99',
    suffix: '/月',
    description: '适合高频研究的专业分析师，覆盖完整投研初筛流程。',
    features: [
      '每月 50 次智能投研初筛',
      '完整版多智能体深度报告',
      '优先分析队列',
      '更长报告与数据附录',
    ],
    cta: '开始专业版',
    highlighted: true,
  },
  {
    name: '企业版',
    price: '定制',
    suffix: '',
    description: '适合机构团队与私募基金，按业务规模灵活配置。',
    features: [
      '不限次数投研初筛',
      '私有化部署与数据隔离',
      '自定义专家与知识库',
      '专属客户成功支持',
    ],
    cta: '预约演示',
    highlighted: false,
  },
];

type PricingProps = {
  onOpenWorkbench: () => void;
};

export default function Pricing({ onOpenWorkbench }: PricingProps) {
  return (
    <section className="relative overflow-hidden bg-gradient-to-b from-surface-50 via-white to-white pt-36 pb-24 lg:pt-44 lg:pb-32">
      <div className="pointer-events-none absolute -top-24 right-0 h-80 w-80 rounded-full bg-primary-200/20 blur-[120px]" />

      <div className="relative max-w-7xl mx-auto px-5 lg:px-8">
        <div className="max-w-3xl animate-fade-in">
          <span className="text-xs font-semibold tracking-widest text-gold-500 uppercase">
            Pricing
          </span>
          <h1 className="mt-3 text-3xl sm:text-4xl lg:text-5xl font-bold tracking-tight leading-[1.2] text-gray-900">
            定价方案
          </h1>
          <p className="mt-5 text-base sm:text-lg text-gray-500 leading-relaxed">
            从个人研究到机构协作，选择适合您的投研初筛方案。
          </p>
          <div className="mt-8 h-1 w-16 rounded-full bg-gradient-to-r from-primary-500 to-gold-400" />
        </div>

        <div className="mt-14 grid gap-5 lg:grid-cols-3 lg:items-stretch lg:gap-6">
          {PLANS.map((plan) => {
            const highlighted = plan.highlighted;
            return (
              <article
                key={plan.name}
                className={`relative flex flex-col rounded-2xl border p-7 transition-all duration-300 hover:-translate-y-1 ${
                  highlighted
                    ? 'border-primary-500 bg-primary-500 text-white shadow-card-hover'
                    : 'border-surface-200 bg-white shadow-sm hover:border-primary-200 hover:shadow-card-hover'
                }`}
              >
                {highlighted && (
                  <span className="absolute -top-3 left-1/2 -translate-x-1/2 rounded-full bg-gold-400 px-3 py-1 text-[10px] font-bold uppercase tracking-widest text-primary-800 shadow-sm">
                    最受欢迎
                  </span>
                )}

                <h2 className={`text-lg font-bold ${highlighted ? 'text-white' : 'text-gray-900'}`}>
                  {plan.name}
                </h2>
                <p
                  className={`mt-2 text-sm leading-relaxed ${
                    highlighted ? 'text-primary-100' : 'text-gray-500'
                  }`}
                >
                  {plan.description}
                </p>

                <div className="mt-6 flex items-baseline gap-1">
                  <span
                    className={`text-4xl font-bold tracking-tight ${
                      highlighted ? 'text-white' : 'text-gray-900'
                    }`}
                  >
                    {plan.price}
                  </span>
                  {plan.suffix && (
                    <span
                      className={`text-sm ${
                        highlighted ? 'text-primary-200' : 'text-gray-400'
                      }`}
                    >
                      {plan.suffix}
                    </span>
                  )}
                </div>

                <button
                  type="button"
                  onClick={onOpenWorkbench}
                  className={`mt-6 inline-flex items-center justify-center gap-2 rounded-xl px-5 py-3 text-sm font-semibold transition-all duration-300 ${
                    highlighted
                      ? 'bg-white text-primary-600 hover:bg-gold-50'
                      : 'bg-gradient-to-br from-primary-500 to-primary-700 text-white hover:from-primary-600 hover:to-primary-800'
                  }`}
                >
                  {plan.cta}
                  <ArrowRight className="h-4 w-4" />
                </button>

                <ul
                  className={`mt-7 space-y-3 border-t pt-6 ${
                    highlighted ? 'border-white/15' : 'border-surface-100'
                  }`}
                >
                  {plan.features.map((feature) => (
                    <li
                      key={feature}
                      className={`flex items-start gap-2.5 text-sm ${
                        highlighted ? 'text-primary-100' : 'text-gray-600'
                      }`}
                    >
                      <Check
                        className={`mt-0.5 h-4 w-4 shrink-0 ${
                          highlighted ? 'text-gold-300' : 'text-primary-500'
                        }`}
                      />
                      <span>{feature}</span>
                    </li>
                  ))}
                </ul>
              </article>
            );
          })}
        </div>

        <p className="mt-8 text-center text-xs text-gray-400">
          当前为演示定价，暂未接入在线支付；所有套餐均可在本地体验核心分析能力。
        </p>
      </div>
    </section>
  );
}
