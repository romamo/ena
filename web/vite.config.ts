/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The dev server proxies the API (`uv run ena-serve`), so the page and the API share one
// origin and the API needs no CORS. ENA_API points it at another address.
const api = process.env.ENA_API ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/chat": api,
      "/approvals": api,
      "/dashboard": api,
      "/offers": api,
      "/consents": api,
      "/bank": api,
      // BoC's screen sends the browser back here after the owner confirms a payment
      "/boc": api,
    },
  },
  test: {
    environment: "jsdom",
  },
});
