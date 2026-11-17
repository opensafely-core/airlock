import type { StorybookConfig } from '@storybook/web-components-vite';

const config: StorybookConfig = {
  "stories": [
    "../stories/**/*.stories.@(js|jsx|mjs|ts|tsx)"
  ],
  staticDirs: [{ from: '../airlock/static', to: '/static' }],
  "addons": [],
  "framework": "@storybook/web-components-vite"
};
export default config;