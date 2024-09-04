import { defineConfig } from "vite";

export default defineConfig({
  base: "/static/",
  build: {
    manifest: "manifest.json",
    outDir: "./assets/out",
    rollupOptions: {
      input: {
        main: "assets/src/scripts/main.js",
        resizer: "assets/src/scripts/resizer.js",
      },
    },
  },
});
