import { Cpu, FileText, Network, ShieldCheck } from 'lucide-react';

const TECHNOLOGIES = [
  {
    icon: Cpu,
    index: '01',
    title: '计算与推理分离',
    description:
      '指标计算交给确定性代码，判断与解释交给大模型；该算准的数字，不靠模型“猜”。',
    tags: ['确定性计算', '结果可复现', '降低幻觉'],
  },
  {
    icon: Network,
    index: '02',
    title: '多源数据交叉验证',
    description:
      '财务、法务、业务三路信息并行检索，关键结论互相印证；冲突观点会被明确标记。',
    tags: ['三路并行', '交叉印证', '冲突标记'],
  },
  {
    icon: FileText,
    index: '03',
    title: '极简呈现与 PDF 导出',
    description:
      '复杂分析收敛为结构化研报，核心结论、风险清单与数据依据分层呈现，支持一键导出 PDF 归档。',
    tags: ['结构化研报', '风险分层', 'PDF 导出'],
  },
];

export default function CoreTechnology() {
  return (
    <section id="core-technology" className="relative overflow-hidden bg-white py-20 lg:py-28">
      <div className="pointer-events-none absolute -left-24 top-1/3 h-72 w-72 rounded-full bg-primary-100/40 blur-[120px]" />

      <div className="relative mx-auto max-w-7xl px-5 lg:px-8">
        <div className="mx-auto max-w-3xl text-center">
          <span className="text-xs font-semibold uppercase tracking-widest text-gold-500">
            Core Technology
          </span>
          <h2 className="mt-3 text-3xl font-bold tracking-tight text-gray-900 sm:text-4xl">
            让 AI 的结论，经得起追问
          </h2>
          <p className="mt-4 text-base leading-relaxed text-gray-500">
            技术壁垒不在模型参数，而在数据、流程与验证方式。我们把“可信”拆成三层来做。
          </p>
        </div>

        <div className="mt-14 grid gap-5 md:grid-cols-3 lg:gap-6">
          {TECHNOLOGIES.map((technology, index) => (
            <article
              key={technology.title}
              className="group flex flex-col rounded-2xl border border-surface-200 bg-white p-7 shadow-sm transition-all duration-300 hover:-translate-y-1 hover:border-primary-200 hover:shadow-card-hover animate-fade-in"
              style={{ animationDelay: `${80 + index * 80}ms` }}
            >
              <div className="flex items-center justify-between">
                <div className="flex h-12 w-12 items-center justify-center rounded-xl bg-primary-50 text-primary-500 transition-colors duration-300 group-hover:bg-primary-500 group-hover:text-white">
                  <technology.icon className="h-6 w-6" strokeWidth={2} />
                </div>
                <span className="text-[11px] font-bold tracking-widest text-gray-300">
                  {technology.index}
                </span>
              </div>

              <h3 className="mt-6 text-lg font-bold text-gray-900">{technology.title}</h3>
              <p className="mt-2.5 flex-1 text-sm leading-relaxed text-gray-500">
                {technology.description}
              </p>

              <div className="mt-6 flex flex-wrap gap-2 border-t border-surface-100 pt-5">
                {technology.tags.map((tag) => (
                  <span
                    key={tag}
                    className="inline-flex rounded-md bg-primary-50 px-2.5 py-1 text-[11px] font-medium text-primary-600"
                  >
                    {tag}
                  </span>
                ))}
              </div>
            </article>
          ))}
        </div>

        <div className="mx-auto mt-10 flex max-w-3xl items-start gap-3 rounded-xl border border-primary-100 bg-primary-50 px-5 py-4 text-primary-600">
          <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0" />
          <p className="text-sm leading-relaxed">
            我们不用“黑盒”换效率：关键判断尽量给出依据，冲突与低置信度结论会被标注，方便分析师复核。
          </p>
        </div>
      </div>
    </section>
  );
}
