import { useEffect } from 'react';
import { useLocation } from 'react-router-dom';

// 切换页面时回到顶部，避免新页面停留在上一页的滚动位置
export default function ScrollToTop() {
  const { pathname } = useLocation();

  useEffect(() => {
    window.scrollTo(0, 0);
  }, [pathname]);

  return null;
}
