import type { APIRoute } from "astro";
import { ZODIACS } from "../data/zodiacs";
import { getCollection } from "astro:content";

export const GET: APIRoute = async ({ site }) => {
  const baseUrl = (site?.href || "https://lucksajueun.com").replace(/\/$/, "");
  const now = new Date().toUTCString();

  // 핵심 정적 페이지 (daily 갱신 = 메인, monthly = 사주·음력·띠·로또·연봉)
  const items: Array<{ title: string; link: string; description: string; pubDate: string; category: string }> = [
    {
      title: "오늘의 운세 (매일 자정 자동 갱신)",
      link: "/",
      description: "오늘의 일진(日辰)에 따른 운세 점수·메시지·행운의 색과 숫자를 매일 제공합니다. 한국 음력 만세력 데이터 기반.",
      pubDate: now,
      category: "운세",
    },
    {
      title: "사주팔자 계산기",
      link: "/saju/",
      description: "생년월일·태어난 시간으로 4주(년·월·일·시)의 천간·지지·오행을 계산합니다. 음력 입력 자동 변환.",
      pubDate: now,
      category: "도구",
    },
    {
      title: "음력 ↔ 양력 변환기",
      link: "/lunar-calendar/",
      description: "1900~2050년 한국 음력 데이터를 즉시 변환합니다. 한국 천문연구원 표준 역서 기준.",
      pubDate: now,
      category: "도구",
    },
    {
      title: "12띠 운세",
      link: "/zodiac/",
      description: "쥐·소·호랑이·토끼·용·뱀·말·양·원숭이·닭·개·돼지 12개 띠별 성격·궁합·2026년 운세.",
      pubDate: now,
      category: "운세",
    },
    {
      title: "로또 6/45 번호 생성기",
      link: "/lotto/",
      description: "매주 새로 뽑는 로또 번호 5세트 재미·여가용 생성기.",
      pubDate: now,
      category: "도구",
    },
    {
      title: "연봉 실수령액 계산기",
      link: "/salary/",
      description: "4대보험·소득세를 자동 계산해 연봉 실수령액을 즉시 확인합니다.",
      pubDate: now,
      category: "도구",
    },
  ];

  // 12띠 페이지도 RSS에 포함
  ZODIACS.forEach((z) => {
    items.push({
      title: `${z.name}띠 운세`,
      link: `/zodiac/${z.slug}/`,
      description: `${z.name}띠의 성격·궁합·2026년 운세를 상세히 확인하세요.`,
      pubDate: now,
      category: "띠별운세",
    });
  });

  // Blog 칼럼 RSS 포함
  const posts = (await getCollection("blog")).sort(
    (a, b) => b.data.pubDate.getTime() - a.data.pubDate.getTime()
  );
  posts.forEach((p) => {
    items.push({
      title: p.data.title,
      link: `/blog/${p.id}/`,
      description: p.data.description,
      pubDate: p.data.pubDate.toUTCString(),
      category: p.data.tags[0] || "칼럼",
    });
  });

  const escapeXml = (s: string): string =>
    s
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&apos;");

  const rssItems = items
    .map(
      (it) => `    <item>
      <title>${escapeXml(it.title)}</title>
      <link>${baseUrl}${it.link}</link>
      <guid isPermaLink="true">${baseUrl}${it.link}</guid>
      <description>${escapeXml(it.description)}</description>
      <category>${escapeXml(it.category)}</category>
      <pubDate>${it.pubDate}</pubDate>
    </item>`,
    )
    .join("\n");

  const xml = `<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">
  <channel>
    <title>오늘의 운세 - lucksajueun.com</title>
    <link>${baseUrl}/</link>
    <description>매일 갱신되는 한국 음력 만세력 기반 무료 운세·사주팔자·음력변환·로또·연봉계산</description>
    <language>ko-KR</language>
    <lastBuildDate>${now}</lastBuildDate>
    <atom:link href="${baseUrl}/rss.xml" rel="self" type="application/rss+xml" />
    <generator>Astro v7.3.1</generator>
${rssItems}
  </channel>
</rss>`;

  return new Response(xml, {
    headers: {
      "Content-Type": "application/rss+xml; charset=utf-8",
      "Cache-Control": "public, max-age=3600",
    },
  });
};
