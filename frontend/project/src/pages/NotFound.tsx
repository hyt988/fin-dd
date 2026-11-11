import { Link } from 'react-router-dom';
import { ArrowLeft } from 'lucide-react';

export default function NotFound() {
  return (
    <section className="relative overflow-hidden bg-gradient-to-b from-surface-50 via-white to-white pt-36 pb-28 lg:pt-44 lg:pb-36">
      <div className="pointer-events-none absolute -top-24 right-0 h-80 w-80 rounded-full bg-primary-200/20 blur-[120px]" />
      <div className="relative max-w-7xl mx-auto px-5 lg:px-8 text-center">
        <span className="text-xs font-semibold tracking-widest text-gold-500 uppercase">404</span>
        <h1 className="mt-3 text-3xl sm:text-4xl font-bold tracking-tight text-gray-900">
          页面未找到
        </h1>
        <p className="mt-4 text-base text-gray-500">
          您访问的页面不存在，可能已被移动或删除。
        </p>
        <Link
          to="/"
          className="mt-8 inline-flex items-center gap-2 px-6 py-3 text-sm font-semibold text-white bg-gradient-to-br from-primary-500 to-primary-700 rounded-xl shadow-sm hover:shadow-md transition-all duration-300 hover:from-primary-600 hover:to-primary-800"
        >
          <ArrowLeft className="w-4 h-4" />
          返回首页
        </Link>
      </div>
    </section>
  );
}
