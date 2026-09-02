/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ["./local_coding_agent/desktop/**/*.py"],
  darkMode: "class",
  theme: {
    extend: {
      fontFamily: {
        sans: ["Segoe UI", "Roboto", "sans-serif"],
        mono: ["Cascadia Mono", "Consolas", "ui-monospace", "monospace"]
      },
      letterSpacing: {
        tighter: "-0.03em",
        tight: "-0.015em"
      }
    }
  },
  plugins: []
};
