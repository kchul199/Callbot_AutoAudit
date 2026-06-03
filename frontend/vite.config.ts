import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 개발 시 백엔드(FastAPI 8000)로 /api 프록시
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8010",
        changeOrigin: true,
      },
    },
  },
  build: {
    rollupOptions: {
      output: {
        // 벤더 청크 분리로 초기 번들 크기 축소 (recharts가 가장 무거움)
        manualChunks: {
          react: ["react", "react-dom", "react-router-dom"],
          charts: ["recharts"],
        },
      },
    },
  },
});
