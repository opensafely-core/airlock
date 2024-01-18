/* eslint-disable import/no-extraneous-dependencies */
import { viteStaticCopy } from "vite-plugin-static-copy";

var env = process.env;

/**
 * @type {import('vite').UserConfig}
 */
const config = {
  base: "/static/",
  build: {
    manifest: true,
    rollupOptions: {
      input: {
        base: `./src/scripts/base.js`,
        components: `./src/scripts/components.js`,
        modal: "./templates/_components/modal/modal.js",
        multiselect: "./templates/_components/multiselect/multiselect.js",
      },
    },
    outDir: env.ASSETS_DIST,
    emptyOutDir: true,
  },
  server: {
    origin: "http://localhost:5173",
  },
  clearScreen: false,
  plugins: [
    /*
    viteStaticCopy({
      targets: [
        {
          src: "./node_modules/htmx.org/dist/htmx.min.js",
          dest: "vendor",
        },
      ],
    }),
    */
  ],
  test: {}
};

export default config;
