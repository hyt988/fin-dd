import { ArrowRight, Upload, Bot, ShieldCheck, AlertTriangle, FileCheck } from 'lucide-react';

const WORKFLOW = [
  { label: '输入公司代码/上传材料', icon: Upload },
  { label: '多智能体规划', icon: Bot },
  { label: '深度数据验证', icon: ShieldCheck },
  { label: '风险洞察生成', icon: AlertTriangle },
  { label: '报告生成', icon: FileCheck },
];

type HeroProps = {
  onAnalyze: () => void;
};

export default function Hero({ onAnalyze }: HeroProps) {
  return (
    <section className="relative pt-36 pb-24 lg:pt-44 lg:pb-32 overflow-hidden">
      {/* Background: gradient + glow orbs + grid */}
      <div className="absolute inset-0 -z-10">
        <div className="absolute inset-0 bg-gradient-to-b from-surface-50 via-white to-white" />
        <div className="absolute top-0 left-1/4 w-[480px] h-[480px] bg-primary-200/15 rounded-full blur-[140px]" />
        <div className="absolute top-16 right-1/4 w-[400px] h-[400px] bg-gray-300/15 rounded-full blur-[140px]" />
        <div
          className="absolute inset-0 opacity-[0.04]"
          style={{
            backgroundImage:
              'linear-gradient(to right, #0F3D3E 1px, transparent 1px), linear-gradient(to bottom, #0F3D3E 1px, transparent 1px)',
            backgroundSize: '52px 52px',
            maskImage: 'radial-gradient(ellipse 75% 55% at 50% 0%, black 35%, transparent 100%)',
            WebkitMaskImage: 'radial-gradient(ellipse 75% 55% at 50% 0%, black 35%, transparent 100%)',
          }}
        />
      </div>

      <div className="max-w-5xl mx-auto px-5 lg:px-8 text-center">
        {/* Badge */}
        <div className="inline-flex items-center gap-2 px-4 py-1.5 rounded-full bg-white/80 backdrop-blur-sm border border-surface-200 shadow-sm animate-fade-in">
          <span className="text-sm">✨</span>
          <span className="text-xs sm:text-sm font-medium text-primary-600">
            基于本地多智能体协同的公开信息风险扫描平台
          </span>
        </div>

        {/* Main heading */}
        <h1 className="mt-8 text-3xl sm:text-4xl lg:text-5xl font-bold tracking-tight leading-[1.2] animate-fade-in" style={{ animationDelay: '60ms' }}>
          <span className="text-gray-900">多智能体协同的</span>
          <br />
          <span className="text-primary-500">公开信息风险扫描系统</span>
        </h1>

        {/* Subtitle */}
        <p className="mt-7 text-base sm:text-lg text-gray-500 max-w-2xl mx-auto leading-relaxed animate-fade-in" style={{ animationDelay: '120ms' }}>
          专为中小型 PE/VC、FA 及独立分析师打造，10分钟完成 A 股上市公司初步风险筛查。
        </p>

        {/* CTAs */}
        <div className="mt-10 flex flex-col sm:flex-row items-center justify-center gap-3.5 animate-fade-in" style={{ animationDelay: '180ms' }}>
          <button
            type="button"
            onClick={onAnalyze}
            className="group flex items-center justify-center gap-2 w-full sm:w-auto px-7 py-3.5 text-sm font-semibold text-white bg-gradient-to-br from-primary-500 to-primary-700 hover:from-primary-600 hover:to-primary-800 rounded-xl shadow-sm hover:shadow-md transition-all duration-300"
          >
            立即开启智能投研初筛
            <ArrowRight className="w-4 h-4 transition-transform duration-200 group-hover:translate-x-1" />
          </button>
        </div>

        {/* Trust indicator */}
        <p className="mt-4 text-xs text-gray-400 animate-fade-in" style={{ animationDelay: '220ms' }}>
          已支持分析 5,000+ 家 A 股上市公司
        </p>

        {/* Workflow strip */}
        <div className="mt-16 animate-fade-in" style={{ animationDelay: '280ms' }}>
          <div className="flex flex-wrap items-center justify-center gap-x-1 gap-y-3">
            {WORKFLOW.map((step, i) => (
              <div key={step.label} className="flex items-center gap-1">
                <div className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-white/70 backdrop-blur-sm border border-surface-200 shadow-sm">
                  <step.icon className="w-3.5 h-3.5 text-primary-400" />
                  <span className="text-xs sm:text-[13px] font-medium text-gray-600 whitespace-nowrap">
                    {step.label}
                  </span>
                </div>
                {i < WORKFLOW.length - 1 && (
                  <ArrowRight className="w-3.5 h-3.5 text-gray-300 mx-0.5 hidden sm:block" />
                )}
              </div>
            ))}
          </div>
        </div>
      </div>
    </section>
  );
}
