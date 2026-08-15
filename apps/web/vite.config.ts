import { fileURLToPath } from "node:url";
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
  },
  server: {
    port: 5173,
    // The API sets an httpOnly session cookie. Proxying keeps the browser on one
    // origin so the cookie is first-party — no CORS dance, no SameSite surprises.
    proxy: {
      "/api": {
        // 127.0.0.1, not "localhost": on Windows localhost resolves to ::1 first,
        // and uvicorn bound to 127.0.0.1 is not listening there — every proxied
        // request would fail with ECONNREFUSED.
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
  },
});
