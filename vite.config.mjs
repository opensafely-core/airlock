import { viteStaticCopy } from "vite-plugin-static-copy";

/**
 * @type {import('vite').UserConfig}
 */
const config = {
  base: "/static/",
  build: {
    manifest: true,
    rollupOptions: {
      input: {
        base: "./assets/src/scripts/base.js",
        multiselect: "./assets/templates/_components/multiselect/multiselect.js",
      },
    },
    outDir: "assets/dist",
    emptyOutDir: true,
  },
  server: {
    origin: "http://localhost:5173",
  },
  clearScreen: false,
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
};

export default config;
