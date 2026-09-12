import { defineConfig } from "vite";

// Only the test service uses this port; the normal development proxy is unchanged.
export default defineConfig({
  server: { host: "127.0.0.1", proxy: { "/api": "http://127.0.0.1:18080" } },
});
