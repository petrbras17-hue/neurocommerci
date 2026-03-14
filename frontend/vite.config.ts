import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { VitePWA } from "vite-plugin-pwa";

export default defineConfig({
  base: "/app/",
  plugins: [
    react(),
    VitePWA({
      registerType: "autoUpdate",
      scope: "/app/",
      includeAssets: ["favicon.ico", "apple-touch-icon.png"],
      manifest: {
        name: "NEURO COMMENTING — Telegram Growth OS",
        short_name: "NEURO",
        description: "Платформа для нейрокомментинга и роста в Telegram",
        start_url: "/app/",
        scope: "/app/",
        display: "standalone",
        orientation: "any",
        theme_color: "#0a0a0b",
        background_color: "#0a0a0b",
        categories: ["productivity", "business"],
        lang: "ru",
        dir: "ltr",
        icons: [
          { src: "/static/pwa/icon-72.png", sizes: "72x72", type: "image/png" },
          { src: "/static/pwa/icon-96.png", sizes: "96x96", type: "image/png" },
          { src: "/static/pwa/icon-128.png", sizes: "128x128", type: "image/png" },
          { src: "/static/pwa/icon-144.png", sizes: "144x144", type: "image/png" },
          { src: "/static/pwa/icon-152.png", sizes: "152x152", type: "image/png" },
          { src: "/static/pwa/icon-192.png", sizes: "192x192", type: "image/png", purpose: "any" },
          { src: "/static/pwa/icon-384.png", sizes: "384x384", type: "image/png" },
          { src: "/static/pwa/icon-512.png", sizes: "512x512", type: "image/png", purpose: "any maskable" }
        ],
        shortcuts: [
          { name: "Дашборд", url: "/app/dashboard", icons: [{ src: "/static/pwa/icon-96.png", sizes: "96x96" }] },
          { name: "Ферма", url: "/app/farm", icons: [{ src: "/static/pwa/icon-96.png", sizes: "96x96" }] },
          { name: "Карта каналов", url: "/app/channel-map", icons: [{ src: "/static/pwa/icon-96.png", sizes: "96x96" }] }
        ]
      },
      workbox: {
        skipWaiting: true,
        clientsClaim: true,
        globPatterns: ["**/*.{js,css,html,woff,woff2,png,svg,ico}"],
        navigateFallback: "/app/index.html",
        navigateFallbackAllowlist: [/^\/app/],
        runtimeCaching: [
          {
            urlPattern: /^https?:\/\/.*\/auth\/.*/i,
            handler: "NetworkFirst",
            options: {
              cacheName: "auth-cache",
              expiration: { maxEntries: 10, maxAgeSeconds: 300 }
            }
          },
          {
            urlPattern: /^https?:\/\/.*\/v1\/.*/i,
            handler: "NetworkFirst",
            options: {
              cacheName: "api-cache",
              expiration: { maxEntries: 100, maxAgeSeconds: 600 }
            }
          },
          {
            urlPattern: /^https?:\/\/.*\/static\/.*/i,
            handler: "CacheFirst",
            options: {
              cacheName: "static-cache",
              expiration: { maxEntries: 50, maxAgeSeconds: 2592000 }
            }
          }
        ]
      }
    })
  ],
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
  resolve: {
    dedupe: ["three"],
  },
  optimizeDeps: {
    include: ["three"],
  },
  build: {
    outDir: "dist",
    emptyOutDir: true
  }
});
