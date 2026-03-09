import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  base: "/app/",
  plugins: [react()],
  server: {
    port: 5173,
    host: "0.0.0.0",
    proxy: {
      "/auth": "http://127.0.0.1:8081",
      "/v1": "http://127.0.0.1:8081",
      "/static": "http://127.0.0.1:8081",
      "/api": "http://127.0.0.1:8081"
    }
  },
  build: {
    outDir: "dist",
    emptyOutDir: true
  }
});
