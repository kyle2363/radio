import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  base: "./",
  server: {
    port: 3000,
    proxy: {
      // The playback control socket lives at /api/playback/sessions/events.
      // Without ws: true the dev server never answers its upgrade, leaving the
      // page permanently deaf to pause/seek/quiesce and playback_state frames.
      "/api": { target: "http://localhost:8765", changeOrigin: true, ws: true },
      "/ws": { target: "ws://localhost:8765", ws: true },
    },
  },
  build: {
    outDir: "dist",
  },
});
