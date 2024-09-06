import { defineConfig } from "vite";

export default defineConfig({
  base: "/static/",
  build: {
    manifest: "manifest.json",
    outDir: "./assets/out",
    rollupOptions: {
      input: {
        htmx: "assets/src/scripts/htmx.js",
        main: "assets/src/scripts/main.js",
        resizer: "assets/src/scripts/resizer.js",
      },
    },
  },
  server: {
    origin: "http://localhost:5173",
  },
});
