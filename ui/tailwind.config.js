/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        canvas: "#0b1220",
        panel: "#111a2e",
        accent: "#6ea8ff",
      },
    },
  },
  plugins: [],
};
