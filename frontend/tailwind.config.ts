import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./app/**/*.{js,ts,jsx,tsx,mdx}",
    "./components/**/*.{js,ts,jsx,tsx,mdx}",
    "./lib/**/*.{js,ts,jsx,tsx,mdx}"
  ],
  theme: {
    extend: {
      colors: {
        ink: "#17202a",
        line: "#d8dee8",
        panel: "#f8fafc",
        brand: "#1f7a68"
      }
    }
  },
  plugins: []
};

export default config;

