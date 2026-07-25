import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev-time only: `npm run dev` serves this app on its own port with hot
// reload, but the app itself still needs a real avg.api.app instance to
// call. Start one on a fixed port (see the Task 9 report for the one-liner)
// and point AVG_DEV_API_PORT at it if it's not the 8756 default - this
// proxies /api and /ws straight through, so the dev server works with real
// data and hot reload at the same time.
const devApiPort = process.env.AVG_DEV_API_PORT ?? "8756";

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": `http://127.0.0.1:${devApiPort}`,
      "/ws": { target: `ws://127.0.0.1:${devApiPort}`, ws: true },
    },
  },
});
