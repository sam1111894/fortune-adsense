import type { APIRoute } from "astro";

export const GET: APIRoute = () => {
  // IndexNow API verification key (TXT response)
  // 검증: api.indexnow.org/IndexNowKeyLocationValidator
  // 또는 직접 fetch: https://lucksajueun.com/lucksajueun-indexnow-2026.txt
  // 본문은 정확히 key 문자열 한 줄이어야 함
  const body = "lucksajueun-indexnow-2026\n";
  return new Response(body, {
    headers: { "Content-Type": "text/plain; charset=utf-8" },
  });
};
