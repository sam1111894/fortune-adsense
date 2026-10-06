import type { APIRoute } from "astro";
import { ZODIACS } from "../data/zodiacs";
import { getCollection } from "astro:content";

export const GET: APIRoute = async ({ site }) => {
  const baseUrl = (site?.href || "https://lucksajueun.com").replace(/\/$/, "");
  const today = new Date().toISOString().split("T")[0];

  // 트레일링 슬래시 통일: Cloudflare Pages 정책(/saju → 308 → /saju/)과 일치
  // → sitemap에 /saju/ 로 등록하여 308 리디렉션 제거 (Google "리디렉션 오류" 해결)
  const trailing = (loc: string): string => {
    if (loc === "" || loc === "/") return "/";
    return loc.endsWith("/") ? loc : `${loc}/`;
  };

  const staticPages = [
    { loc: "/", changefreq: "daily", priority: "1.0" },
    { loc: "/saju/", changefreq: "monthly", priority: "0.9" },
    { loc: "/lunar-calendar/", changefreq: "monthly", priority: "0.8" },
    { loc: "/zodiac/", changefreq: "monthly", priority: "0.9" },
    { loc: "/lotto/", changefreq: "monthly", priority: "0.7" },
    { loc: "/salary/", changefreq: "monthly", priority: "0.7" },
    { loc: "/methodology/", changefreq: "yearly", priority: "0.7" },
    { loc: "/sources/", changefreq: "yearly", priority: "0.7" },
    { loc: "/editorial-policy/", changefreq: "yearly", priority: "0.7" },
    { loc: "/about/", changefreq: "yearly", priority: "0.5" },
    { loc: "/privacy/", changefreq: "yearly", priority: "0.4" },
    { loc: "/terms/", changefreq: "yearly", priority: "0.4" },
    { loc: "/contact/", changefreq: "yearly", priority: "0.4" },
  ];

  const zodiacUrls = ZODIACS.map((z) => ({
    loc: trailing(`/zodiac/${z.slug}`),
    changefreq: "monthly",
    priority: "0.8",
  }));

  // Blog 칼럼 (/blog/) — content collection에서 동적 생성
  const posts = await getCollection("blog");
  const blogUrls = [
    { loc: "/blog/", changefreq: "weekly", priority: "0.9" },
    ...posts.map((p) => ({
      loc: trailing(`/blog/${p.id}`),
      changefreq: "monthly" as const,
      priority: "0.7",
    })),
  ];

  const allUrls = [...staticPages, ...zodiacUrls, ...blogUrls];

  const urls = allUrls
    .map(
      ({ loc, changefreq, priority }) => `  <url>
    <loc>${baseUrl}${loc}</loc>
    <lastmod>${today}</lastmod>
    <changefreq>${changefreq}</changefreq>
    <priority>${priority}</priority>
  </url>`
    )
    .join("\n");

  const xml = `<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
${urls}
</urlset>`;

  return new Response(xml, {
    headers: { "Content-Type": "application/xml; charset=utf-8" },
  });
};