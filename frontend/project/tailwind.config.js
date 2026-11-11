import typography from '@tailwindcss/typography';

/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      colors: {
        primary: {
          50: '#E8F0F0',
          100: '#C5D9D9',
          200: '#9AB8B8',
          300: '#6E9797',
          400: '#3D6B6C',
          500: '#0F3D3E',
          600: '#0D3637',
          700: '#0A2C2D',
          800: '#072223',
          900: '#051819',
        },
        gold: {
          50: '#FBF7E9',
          100: '#F5ECC9',
          200: '#EBD999',
          300: '#E0C669',
          400: '#D4AF37',
          500: '#BF9E2F',
          600: '#A08526',
          700: '#806A1E',
          800: '#604F16',
          900: '#40350E',
        },
        surface: {
          50: '#F8F9FA',
          100: '#F1F3F5',
          200: '#E5E8EB',
          300: '#D1D6DB',
        },
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', '-apple-system', 'PingFang SC', 'Microsoft YaHei', 'sans-serif'],
      },
      boxShadow: {
        'card': '0 1px 3px 0 rgba(15, 61, 62, 0.06), 0 4px 16px -2px rgba(15, 61, 62, 0.08)',
        'card-hover': '0 4px 6px -1px rgba(15, 61, 62, 0.08), 0 12px 32px -4px rgba(15, 61, 62, 0.12)',
        'glow': '0 0 20px rgba(212, 175, 55, 0.15)',
      },
      animation: {
        'breathe': 'breathe 2.5s ease-in-out infinite',
        'shimmer': 'shimmer 1.8s linear infinite',
        'fade-in': 'fadeIn 0.4s ease-out',
        'slide-down': 'slideDown 0.2s ease-out',
        'modal-in': 'modalIn 0.24s cubic-bezier(0.16, 1, 0.3, 1)',
        'drawer-in': 'drawerIn 0.3s cubic-bezier(0.16, 1, 0.3, 1)',
      },
      keyframes: {
        breathe: {
          '0%, 100%': { opacity: '1', transform: 'scale(1)' },
          '50%': { opacity: '0.5', transform: 'scale(0.85)' },
        },
        shimmer: {
          '0%': { backgroundPosition: '-468px 0' },
          '100%': { backgroundPosition: '468px 0' },
        },
        fadeIn: {
          '0%': { opacity: '0', transform: 'translateY(8px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
        slideDown: {
          '0%': { opacity: '0', transform: 'translateY(-6px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
        modalIn: {
          '0%': { opacity: '0', transform: 'translateY(16px) scale(0.97)' },
          '100%': { opacity: '1', transform: 'translateY(0) scale(1)' },
        },
        drawerIn: {
          '0%': { transform: 'translateX(100%)' },
          '100%': { transform: 'translateX(0)' },
        },
      },
    },
  },
  plugins: [typography],
};
