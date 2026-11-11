import { useEffect, useState } from 'react';
import { Link, useLocation } from 'react-router-dom';
import { Sparkles, Menu, X, ArrowRight } from 'lucide-react';

const NAV_ITEMS = [
  { label: '产品特性', to: '/features' },
  { label: '工作流程', to: '/workflow' },
  { label: '定价', to: '/pricing' },
  { label: '关于我们', to: '/about' },
];

type NavbarProps = {
  onOpenWorkbench: () => void;
};

export default function Navbar({ onOpenWorkbench }: NavbarProps) {
  const [scrolled, setScrolled] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);
  const { pathname } = useLocation();
  const isHome = pathname === '/';
  const solid = scrolled || !isHome;

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 12);
    onScroll();
    window.addEventListener('scroll', onScroll);
    return () => window.removeEventListener('scroll', onScroll);
  }, []);

  // 切换页面时收起移动端菜单
  useEffect(() => {
    setMobileOpen(false);
  }, [pathname]);

  const handleWorkbench = () => {
    setMobileOpen(false);
    onOpenWorkbench();
  };

  const navLinkClass = (to: string) =>
    `px-4 py-2 text-sm font-medium rounded-lg transition-colors duration-200 ${
      pathname === to
        ? 'text-primary-500 bg-surface-50'
        : 'text-gray-600 hover:text-primary-500 hover:bg-surface-50'
    }`;

  return (
    <header
      className={`fixed top-0 left-0 right-0 z-50 transition-all duration-300 ${
        solid
          ? 'bg-white/80 backdrop-blur-xl border-b border-surface-200 shadow-sm'
          : 'bg-transparent'
      }`}
    >
      <nav className="max-w-7xl mx-auto px-5 lg:px-8">
        <div className="flex items-center justify-between h-16">
          {/* Logo */}
          <Link to="/" className="flex items-center gap-2.5 group">
            <div className="flex items-center justify-center w-9 h-9 rounded-xl bg-gradient-to-br from-primary-400 to-primary-600 shadow-glow transition-transform duration-300 group-hover:scale-105">
              <Sparkles className="w-5 h-5 text-gold-400" strokeWidth={2.2} />
            </div>
            <div className="flex items-baseline gap-1.5">
              <span className="text-lg font-bold tracking-tight text-primary-500">智投明鉴</span>
              <span className="text-sm font-semibold tracking-wider text-gold-400">FinDD</span>
            </div>
          </Link>

          {/* Center nav */}
          <div className="hidden md:flex items-center gap-1">
            {NAV_ITEMS.map((item) => (
              <Link key={item.to} to={item.to} className={navLinkClass(item.to)}>
                {item.label}
              </Link>
            ))}
          </div>

          {/* Right actions */}
          <div className="hidden md:flex items-center gap-3">
            <a
              href="#"
              className="px-4 py-2 text-sm font-medium text-gray-600 hover:text-primary-500 transition-colors"
            >
              登录
            </a>
            <button
              type="button"
              onClick={handleWorkbench}
              className="group flex items-center gap-1.5 px-5 py-2.5 text-sm font-semibold text-white bg-gradient-to-br from-primary-500 to-primary-700 rounded-lg shadow-sm hover:shadow-md transition-all duration-300 hover:from-primary-600 hover:to-primary-800"
            >
              进入工作台
              <ArrowRight className="w-4 h-4 transition-transform duration-200 group-hover:translate-x-0.5" />
            </button>
          </div>

          {/* Mobile toggle */}
          <button
            type="button"
            onClick={() => setMobileOpen(!mobileOpen)}
            aria-label={mobileOpen ? '关闭菜单' : '打开菜单'}
            aria-expanded={mobileOpen}
            className="md:hidden flex items-center justify-center w-9 h-9 rounded-lg text-gray-700 hover:bg-surface-50"
          >
            {mobileOpen ? <X className="w-5 h-5" /> : <Menu className="w-5 h-5" />}
          </button>
        </div>

        {/* Mobile menu */}
        {mobileOpen && (
          <div className="md:hidden pb-4 space-y-1 animate-slide-down">
            {NAV_ITEMS.map((item) => (
              <Link
                key={item.to}
                to={item.to}
                onClick={() => setMobileOpen(false)}
                className={`block px-4 py-2.5 text-sm font-medium rounded-lg transition-colors ${
                  pathname === item.to
                    ? 'text-primary-500 bg-surface-50'
                    : 'text-gray-600 hover:text-primary-500 hover:bg-surface-50'
                }`}
              >
                {item.label}
              </Link>
            ))}
            <div className="flex items-center gap-3 pt-2 px-4">
              <a href="#" className="text-sm font-medium text-gray-600">登录</a>
              <button
                type="button"
                onClick={handleWorkbench}
                className="flex items-center gap-1.5 px-4 py-2 text-sm font-semibold text-white bg-gradient-to-br from-primary-500 to-primary-700 rounded-lg"
              >
                进入工作台 <ArrowRight className="w-4 h-4" />
              </button>
            </div>
          </div>
        )}
      </nav>
    </header>
  );
}
