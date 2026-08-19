import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// Dev : Vite tourne sur 5173 et proxie /api + /ws vers le backend FastAPI (8000).
// Prod : `npm run build` sort vers ../server/static, servi par FastAPI à /.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:8000",
      "/ws": { target: "ws://127.0.0.1:8000", ws: true },
      "/data": "http://127.0.0.1:8000",
    },
  },
  build: {
    outDir: "../server/static",
    emptyOutDir: true,
    sourcemap: true,
  },
});
