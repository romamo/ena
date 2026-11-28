/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The dev server proxies the API (`uv run ena-serve`), so the page and the API share one
// origin and the API needs no CORS. ENA_API points it at another address.
const api = process.env.ENA_API ?? "http://127.0.0.1:8000";

// The shorthand `"/chat": api` sets `changeOrigin: true`, so the API would see its own
// address as the Host and any redirect it answers (a trailing slash on `/boc/consent`)
// would send the browser to port 8000 instead of the page. Keep the page's Host.
const toApi = { target: api, changeOrigin: false };

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/chat": toApi,
      "/approvals": toApi,
      "/dashboard": toApi,
      "/offers": toApi,
      "/consents": toApi,
      "/bank": toApi,
      // BoC's screen sends the browser back here after the owner confirms a payment
      "/boc": toApi,
      "/jinius": toApi,
    },
  },
  test: {
    environment: "jsdom",
  },
});
