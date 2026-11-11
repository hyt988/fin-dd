import { BarChart3, Scale, Briefcase, ArrowUpRight } from 'lucide-react';

const FEATURES = [
  {
    icon: BarChart3,
    title: '财务专家',
    tag: 'Financial',
    desc: '深度解析财务报表，识别盈利质量与现金流风险，自动交叉验证关键指标，让粉饰报表无所遁形。',
    highlights: ['三表联动分析', '异常指标预警', '现金流穿透'],
  },
  {
    icon: Scale,
    title: '法务专家',
    tag: 'Legal',
    desc: '全面扫描法律风险与合规问题，检索诉讼历史、股权质押与关联交易，构建企业法律风险全景图。',
    highlights: ['诉讼风险扫描', '股权穿透核查', '合规评级评估'],
  },
  {
    icon: Briefcase,
    title: '业务专家',
    tag: 'Business',
    desc: '评估商业模式与竞争壁垒，结合行业数据洞察市场地位与成长潜力，还原企业真实业务基本面。',
    highlights: ['商业模式解构', '竞争格局研判', '行业对标分析'],
  },
];

export default function Features() {
  return (
    <section id="features" className="py-20 lg:py-28 bg-surface-50">
      <div className="max-w-7xl mx-auto px-5 lg:px-8">
        {/* Section header */}
        <div className="max-w-2xl mx-auto text-center mb-14">
          <span className="text-xs font-semibold tracking-widest text-gold-500 uppercase">
            Core Capabilities
          </span>
          <h2 className="mt-3 text-3xl sm:text-4xl font-bold tracking-tight text-gray-900">
            三位AI专家，一个投研初筛团队
          </h2>
          <p className="mt-4 text-base text-gray-500 leading-relaxed">
            多智能体协同架构，每位专家独立分析、交叉验证，最终汇聚成一份全面、精准、可决策的投研初筛报告
          </p>
        </div>

        {/* Feature cards */}
        <div className="grid grid-cols-1 md:grid-cols-3 gap-5 lg:gap-6">
          {FEATURES.map((feature, i) => (
            <div
              key={feature.title}
              className="group relative bg-white rounded-2xl border border-surface-200 p-7 transition-all duration-300 hover:border-primary-200 hover:shadow-card-hover hover:-translate-y-1 animate-fade-in"
              style={{ animationDelay: `${i * 100}ms` }}
            >
              {/* Hover glow accent */}
              <div className="absolute inset-0 rounded-2xl bg-gradient-to-br from-primary-50/0 to-gold-50/0 group-hover:from-primary-50/50 group-hover:to-gold-50/30 transition-all duration-500 pointer-events-none" />

              <div className="relative">
                {/* Icon */}
                <div className="flex items-center justify-between mb-5">
                  <div className="flex items-center justify-center w-12 h-12 rounded-xl bg-gradient-to-br from-primary-50 to-primary-100 group-hover:from-primary-500 group-hover:to-primary-600 transition-all duration-300">
                    <feature.icon
                      className="w-6 h-6 text-primary-500 group-hover:text-white transition-colors duration-300"
                      strokeWidth={2}
                    />
                  </div>
                  <span className="text-[10px] font-bold tracking-widest text-gray-300 uppercase">
                    {feature.tag}
                  </span>
                </div>

                {/* Title + desc */}
                <h3 className="text-lg font-bold text-gray-900">{feature.title}</h3>
                <p className="mt-2.5 text-sm text-gray-500 leading-relaxed">
                  {feature.desc}
                </p>

                {/* Highlights */}
                <div className="mt-5 flex flex-wrap gap-2">
                  {feature.highlights.map((h) => (
                    <span
                      key={h}
                      className="inline-flex items-center px-2.5 py-1 text-[11px] font-medium text-primary-600 bg-primary-50 rounded-md"
                    >
                      {h}
                    </span>
                  ))}
                </div>

                {/* Learn more */}
                <a
                  href="#"
                  className="mt-6 inline-flex items-center gap-1 text-sm font-medium text-primary-500 opacity-0 group-hover:opacity-100 transition-all duration-300"
                >
                  了解更多
                  <ArrowUpRight className="w-4 h-4" />
                </a>
              </div>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
