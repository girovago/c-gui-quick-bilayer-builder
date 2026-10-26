import { defineConfig } from "vite";
import { svelte } from "@sveltejs/vite-plugin-svelte";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [svelte(), tailwindcss()],
  server: {
    proxy: {
      // sviluppo: `npm run dev` (default :5173) con backend `cgui serve --port 8756`
      "/api": { target: "http://127.0.0.1:8756", changeOrigin: true },
    },
  },
  build: { outDir: "dist" },
});
