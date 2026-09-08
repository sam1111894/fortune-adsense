import type { APIRoute } from "astro";

export const GET: APIRoute = ({ site }) => {
  const pages = ["", "/saju", "/lunar-calendar", "/zodiac", "/lotto", "/salary", "/about", "/privacy", "/terms", "/contact"];
  const baseUrl = (site?.href || "https://fortune-adsense.pages.dev").replace(/\/$/, "");
  const today = new Date().toISOString().split("T")[0];

  const urls = pages
    .map(
      (p) => `  <url>
    <loc>${baseUrl}${p}</loc>
    <lastmod>${today}</lastmod>
    <changefreq>${p === "" ? "daily" : "monthly"}</changefreq>
    <priority>${p === "" ? "1.0" : "0.8"}</priority>
  </url>`,
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