import { defineConfig } from "vite";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  base: "/static/",
  build: {
    manifest: "manifest.json",
    outDir: "./assets/out",
    rollupOptions: {
      input: {
        datatable: "assets/src/scripts/datatable.js",
        htmx: "assets/src/scripts/htmx.js",
        main: "assets/src/scripts/main.js",
        resizer: "assets/src/scripts/resizer.js",
      },
    },
  },
  plugins: [
    tailwindcss(),
  ],
  server: {
    origin: "http://localhost:5173",
  },
});
