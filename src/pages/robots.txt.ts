import type { APIRoute } from "astro";

export const GET: APIRoute = ({ site }) => {
  const baseUrl = (site?.href || "https://lucksajueun.com").replace(/\/$/, "");
  const body = `User-agent: *
Allow: /

# RSS / Atom 자동 발견
Sitemap: ${baseUrl}/sitemap.xml
Sitemap: ${baseUrl}/rss.xml
Sitemap: ${baseUrl}/atom.xml

# 색인 알림
# IndexNow keyLocation: https://${baseUrl.replace(/^https?:\/\//, "")}/lucksajueun-indexnow-2026.txt
`;
  return new Response(body, {
    headers: { "Content-Type": "text/plain; charset=utf-8" },
  });
};