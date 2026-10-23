import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev-time only: `npm run dev` serves this app on its own port with hot
// reload, but the app itself still needs a real avg.api.app instance to
// call. Start one on a fixed port (see the Task 9 report for the one-liner)
// and point AVG_DEV_API_PORT at it if it's not the 8756 default - this
// proxies /api and /ws straight through, so the dev server works with real
// data and hot reload at the same time.
//
// AVG_DEV_API_URL (a full origin, e.g. http://api:8756) replaces the
// loopback-guess above when the bridge runs in another container; unset, the
// behaviour is exactly what it was.
const devApiPort = process.env.AVG_DEV_API_PORT ?? "8756";
const devApiOrigin = process.env.AVG_DEV_API_URL ?? `http://127.0.0.1:${devApiPort}`;
const devApiHost = new URL(devApiOrigin);
const wsOrigin = `${devApiHost.protocol === "https:" ? "wss:" : "ws:"}//${devApiHost.host}`;

// AVG_DEV_TOKEN: when set, the proxy itself presents the bridge's session
// token, so a browser that was never opened with `?token=` (an embedded
// preview, a phone on the LAN) still reaches the API. `avg/shell.py` hands
// the token to the window in the URL and the client reads it from there;
// there is no equivalent hand-off when this dev server is the entry point.
// Unset (the normal case), nothing is injected and the client's own
// X-AVG-Token / ?token= keeps being the only credential the bridge sees.
const devToken = process.env.AVG_DEV_TOKEN;

const proxy = {
  "/api": {
    target: devApiOrigin,
    changeOrigin: false,
    ...(devToken ? { headers: { "X-AVG-Token": devToken } } : {}),
  },
  "/ws": {
    target: wsOrigin,
    ws: true,
    // Browsers cannot set an X-AVG-Token header on a WebSocket handshake,
    // and avg/api/app.py's ws_events route reads the token from the query
    // string only - so it has to be rewritten here, not headed.
    ...(devToken
      ? { rewrite: (path: string) => path.replace(/([?&]token=)[^&]*/, `$1${encodeURIComponent(devToken)}`) }
      : {}),
  },
};

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    proxy,
  },
});
