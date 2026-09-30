// @ts-check
import { defineConfig } from 'astro/config';

// https://astro.build/config
export default defineConfig({
  site: 'https://lucksajueun.com',
  output: 'static',
  build: {
    format: 'directory',
  },
  // 트레일링 슬래시 강제: Googlebot 308 리디렉션 오류 제거
  // - 내부 링크 /about, /saju 등 → /about/, /saju/ 형태로 자동 생성
  // - sitemap과 사이트 동작 일치 → 0 hop으로 200 도달
  trailingSlash: 'always',
  compressHTML: true,
  vite: {
    build: {
      cssCodeSplit: true,
    },
  },
});