// @ts-check
import { defineConfig } from 'astro/config';

// https://astro.build/config
export default defineConfig({
  site: 'https://lucksajueun.com',
  output: 'static',
  build: {
    format: 'directory',
  },
  compressHTML: true,
  vite: {
    build: {
      cssCodeSplit: true,
    },
  },
});