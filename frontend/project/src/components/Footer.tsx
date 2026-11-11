import { Link } from 'react-router-dom';
import { Sparkles } from 'lucide-react';

export default function Footer() {
  return (
    <footer className="bg-primary-700 text-white">
      <div className="max-w-7xl mx-auto px-5 lg:px-8 py-12">
        <div className="flex flex-col lg:flex-row items-start justify-between gap-8">
          {/* Brand */}
          <div className="max-w-sm">
            <Link to="/" className="inline-flex items-center gap-2.5">
              <div className="flex items-center justify-center w-9 h-9 rounded-xl bg-white/10">
                <Sparkles className="w-5 h-5 text-gold-400" strokeWidth={2.2} />
              </div>
              <div className="flex items-baseline gap-1.5">
                <span className="text-lg font-bold tracking-tight">智投明鉴</span>
                <span className="text-sm font-semibold tracking-wider text-gold-400">FinDD</span>
              </div>
            </Link>
            <p className="mt-4 text-sm text-primary-200 leading-relaxed">
              基于本地多智能体协同的公开信息风险扫描平台，让每一次投资决策都有据可依。
            </p>
          </div>

          {/* Links */}
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-8">
            <div>
              <h4 className="text-xs font-semibold tracking-wider text-primary-300 uppercase mb-3">产品</h4>
              <ul className="space-y-2.5">
                <li><Link to="/features" className="text-sm text-primary-200 hover:text-white transition-colors">产品特性</Link></li>
                <li><Link to="/workflow" className="text-sm text-primary-200 hover:text-white transition-colors">工作流程</Link></li>
                <li><Link to="/pricing" className="text-sm text-primary-200 hover:text-white transition-colors">定价方案</Link></li>
              </ul>
            </div>
            <div>
              <h4 className="text-xs font-semibold tracking-wider text-primary-300 uppercase mb-3">公司</h4>
              <ul className="space-y-2.5">
                <li><Link to="/about" className="text-sm text-primary-200 hover:text-white transition-colors">关于我们</Link></li>
                <li><a href="#" className="text-sm text-primary-200 hover:text-white transition-colors">加入我们</a></li>
                <li><a href="#" className="text-sm text-primary-200 hover:text-white transition-colors">联系方式</a></li>
              </ul>
            </div>
            <div>
              <h4 className="text-xs font-semibold tracking-wider text-primary-300 uppercase mb-3">支持</h4>
              <ul className="space-y-2.5">
                <li><a href="#" className="text-sm text-primary-200 hover:text-white transition-colors">帮助中心</a></li>
                <li><a href="#" className="text-sm text-primary-200 hover:text-white transition-colors">API 文档</a></li>
                <li><a href="#" className="text-sm text-primary-200 hover:text-white transition-colors">隐私政策</a></li>
              </ul>
            </div>
          </div>
        </div>

        {/* Disclaimer + copyright */}
        <div className="mt-10 pt-6 border-t border-primary-600/40 space-y-2">
          <p className="text-xs text-primary-300 leading-relaxed">
            免责声明：本平台生成内容由AI辅助生成，仅供内部研究参考，不构成任何投资建议。投资有风险，决策需谨慎。
          </p>
          <div className="flex flex-col sm:flex-row items-center justify-between gap-2">
            <p className="text-xs text-primary-300">
              © 2026 智投明鉴 FinDD. 保留所有权利.
            </p>
            <p className="text-xs text-primary-300">
              ICP 备案号：沪ICP备2026XXXXXX号
            </p>
          </div>
        </div>
      </div>
    </footer>
  );
}
