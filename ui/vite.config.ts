import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Vite dev server config. The /api and /health prefixes proxy through
// to the FastAPI backend on :8000 so the React app can talk to it
// without CORS gymnastics in dev. In production the two are bundled
// behind one reverse proxy (or you point VITE_API_BASE elsewhere).
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": { target: "http://localhost:8000", changeOrigin: true },
      "/health": { target: "http://localhost:8000", changeOrigin: true },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: true,
  },
});
