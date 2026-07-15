import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const target = env.VITE_API_TARGET || "http://localhost:8000";
  return {
    plugins: [react()],
    server: {
      port: 5173,
      proxy: {
        // Proxy API calls to the FastAPI backend during dev.
        // Regex so SPA routes like /api-explorer are NOT proxied.
        "^/api/": { target, changeOrigin: true },
        "/health": { target, changeOrigin: true },
        "/openapi.json": { target, changeOrigin: true },
      },
    },
  };
});
