/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,jsx,ts,tsx}'],
  theme: {
    extend: {
      colors: {
        // Plum scale — sampled from the WWC Gynecology & Aesthetics logo.
        // `primary` kept as an alias so existing `text-primary-500` etc. still work.
        plum: {
          50:  '#FBF6FC',
          100: '#F3E4F6',
          300: '#D4AED9',
          400: '#A876AB',
          600: '#7B4A8A',
          700: '#6A3876',
          900: '#4A2552',
          ink: '#3D1F45',
        },
        primary: {
          50:  '#FBF6FC',
          100: '#F3E4F6',
          300: '#D4AED9',
          400: '#A876AB',
          500: '#6A3876',  // historical `primary-500` now maps to plum.700
          600: '#7B4A8A',
          700: '#6A3876',
          900: '#4A2552',
        },
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
