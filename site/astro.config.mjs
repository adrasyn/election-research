// @ts-check
import { defineConfig } from 'astro/config';

// Single-page site: map + side panel; no SSR, all static.
export default defineConfig({
  site: 'https://aec-elections.pages.dev',
  output: 'static',
  build: {
    assets: '_assets',
  },
  vite: {
    server: {
      // Pages public/seats/*.json live in public/ and are served as-is.
      fs: { strict: false },
    },
  },
});
