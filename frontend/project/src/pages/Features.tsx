import { BarChart3, Briefcase, Check, Scale, ShieldCheck } from 'lucide-react';
import PageCta from '../components/PageCta';

const EXPERTS = [
  {
    icon: BarChart3,
    tag: 'Financial',
    title: '财务专家',
    subtitle: '深挖三大表',
    description:
      '从资产负债表、利润表到现金流量表，逐项拆解盈利质量、偿债能力与现金流真实性，让粉饰报表无处遁形。',
    points: ['三表联动与勾稽校验', '异常科目与现金流预警', '盈利质量与偿债评分'],
  },
  {
    icon: Scale,
    tag: 'Legal',
    title: '法务专家',
    subtitle: '精准识别诉讼与合规',
    description:
      '扫描诉讼、行政处罚、股权质押与关联交易等风险信号，构建企业法律与合规风险的全景图。',
    points: ['诉讼、执行与失信记录', '股权穿透与关联交易核查', '合规风险评级与提示'],
  },
  {
    icon: Briefcase,
    tag: 'Business',
    title: '业务专家',
    subtitle: '洞察竞争格局',
    description:
      '结合行业数据与公开信息，拆解商业模式、竞争壁垒与增长驱动，还原企业真实业务基本面。',
    points: ['商业模式与收入结构', '行业对标与竞争壁垒', '增长质量与可持续性'],
  },
];

type FeaturesPageProps = {
  onOpenWorkbench: () => void;
};

export default function FeaturesPage({ onOpenWorkbench }: FeaturesPageProps) {
  return (
    <>
      <section className="relative overflow-hidden bg-gradient-to-b from-surface-50 via-white to-white pt-36 pb-16 lg:pt-44 lg:pb-20">
        <div className="pointer-events-none absolute -top-24 right-0 h-80 w-80 rounded-full bg-primary-200/20 blur-[120px]" />

        <div className="relative max-w-7xl mx-auto px-5 lg:px-8">
          <div className="max-w-3xl animate-fade-in">
            <span className="text-xs font-semibold tracking-widest text-gold-500 uppercase">
              Product Features
            </span>
            <h1 className="mt-3 text-3xl sm:text-4xl lg:text-5xl font-bold tracking-tight leading-[1.2] text-gray-900">
              产品特性 - 多智能体协同
            </h1>
            <p className="mt-5 text-base sm:text-lg text-gray-500 leading-relaxed">
              财务、法务、业务三位 AI 专家各司其职、独立研究，再通过交叉验证汇聚成一份可决策的投研初筛报告。
            </p>
            <div className="mt-8 h-1 w-16 rounded-full bg-gradient-to-r from-primary-500 to-gold-400" />
          </div>

          <div className="mt-14 grid gap-5 md:grid-cols-3 lg:gap-6">
            {EXPERTS.map((expert, index) => (
              <article
                key={expert.title}
                className="group relative flex flex-col overflow-hidden rounded-2xl border border-surface-200 bg-white p-7 shadow-sm transition-all duration-300 hover:-translate-y-1 hover:border-primary-200 hover:shadow-card-hover animate-fade-in"
                style={{ animationDelay: `${index * 100}ms` }}
              >
                <div className="absolute inset-0 bg-gradient-to-br from-primary-50/0 to-gold-50/0 transition-all duration-500 group-hover:from-primary-50/60 group-hover:to-gold-50/40" />

                <div className="relative flex flex-1 flex-col">
                  <div className="flex items-center justify-between">
                    <div className="flex h-12 w-12 items-center justify-center rounded-xl bg-gradient-to-br from-primary-50 to-primary-100 text-primary-500 transition-all duration-300 group-hover:from-primary-500 group-hover:to-primary-600 group-hover:text-white">
                      <expert.icon className="h-6 w-6" strokeWidth={2} />
                    </div>
                    <span className="text-[10px] font-bold uppercase tracking-widest text-gray-300">
                      {expert.tag}
                    </span>
                  </div>

                  <h2 className="mt-6 text-xl font-bold text-gray-900">{expert.title}</h2>
                  <p className="mt-1 text-sm font-semibold text-gold-600">{expert.subtitle}</p>
                  <p className="mt-3 flex-1 text-sm leading-relaxed text-gray-500">
                    {expert.description}
                  </p>

                  <ul className="mt-6 space-y-2.5 border-t border-surface-100 pt-5">
                    {expert.points.map((point) => (
                      <li key={point} className="flex items-start gap-2 text-sm text-gray-600">
                        <Check className="mt-0.5 h-4 w-4 shrink-0 text-primary-500" />
                        <span>{point}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              </article>
            ))}
          </div>

          <div className="mt-10 flex items-start justify-center gap-2 text-center text-xs text-gray-400">
            <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0 text-primary-400" />
            <p>三位专家结论相互校验，显著降低单一模型幻觉风险</p>
          </div>
        </div>
      </section>

      <PageCta onOpenWorkbench={onOpenWorkbench} />
    </>
  );
}
