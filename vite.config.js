import { defineConfig } from "vite";
import { viteStaticCopy } from "vite-plugin-static-copy";

export default defineConfig({
  base: "/static/",
  build: {
    manifest: "manifest.json",
    outDir: "./assets/out",
    rollupOptions: {
      input: {
        main: "assets/src/scripts/main.js",
      },
    },
  },
  server: {
    origin: "http://localhost:5173",
  },
  plugins: [
    viteStaticCopy({
      targets: [
        {
          src: "./node_modules/htmx.org/dist/htmx.min.js",
          dest: "vendor",
        },
      ],
    }),
  ],
});
