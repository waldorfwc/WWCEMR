/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,jsx,ts,tsx}'],
  theme: {
    extend: {
      colors: {
        // Plum scale — sampled from the WWC Gynecology & Aesthetics logo.
        // Full 50–900 scale. The 200/500/800 shades were previously
        // referenced from components but undefined, so the page's
        // most attention-grabbing moments (e.g. PainPointPanel
        // highlight, LarcCheckoutCard ready state) silently rendered
        // unstyled. (Fable UX critique.)
        plum: {
          50:  '#FBF6FC',
          100: '#F3E4F6',
          200: '#E4C7E8',
          300: '#D4AED9',
          400: '#A876AB',
          500: '#91609B',
          600: '#7B4A8A',
          700: '#6A3876',
          800: '#572D60',
          900: '#4A2552',
          ink: '#3D1F45',
          tinted: '#F3E4F6',  // alias for plum.100 (a few callers)
        },
        // (legacy `primary` alias removed — codebase uses plum-* directly.)
        border: {
          subtle: '#E6D3EA',
        },
        ink: '#3D1F45',
        muted: '#6B5A70',
        success: '#2E7D32',
        danger: '#C62828',
        warning: '#F57C00',
        info: '#1976D2',
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', '-apple-system', 'Segoe UI', 'sans-serif'],
        serif: ['Fraunces', 'Georgia', '"Times New Roman"', 'serif'],
      },
      letterSpacing: {
        wordmark: '0.12em',
      },
    },
  },
  plugins: [],
}
