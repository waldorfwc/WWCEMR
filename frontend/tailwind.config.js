/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,jsx,ts,tsx}'],
  theme: {
    extend: {
      colors: {
        primary: {
          50: '#e8f0fa',
          100: '#c5d8f3',
          500: '#1B4F8A',
          600: '#163f70',
          700: '#112f54',
          900: '#0a1e36',
        },
        success: '#2E7D32',
        danger: '#C62828',
        warning: '#F57C00',
      }
    }
  },
  plugins: [],
}
