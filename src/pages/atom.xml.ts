import type { APIRoute } from "astro";
import { ZODIACS } from "../data/zodiacs";

export const GET: APIRoute = ({ site }) => {
  const baseUrl = (site?.href || "https://lucksajueun.com").replace(/\/$/, "");
  const now = new Date().toISOString();

  // RSS와 동일한 항목 (Atom 형식으로 변환)
  type AtomItem = {
    title: string;
    link: string;
    summary: string;
    updated: string;
    category: string;
  };

  const items: AtomItem[] = [
    {
      title: "오늘의 운세 (매일 자정 자동 갱신)",
      link: "/",
      summary: "오늘의 일진(日辰)에 따른 운세 점수·메시지·행운의 색과 숫자를 매일 제공합니다.",
      updated: now,
      category: "운세",
    },
    {
      title: "사주팔자 계산기",
      link: "/saju/",
      summary: "생년월일·태어난 시간으로 4주(년·월·일·시)의 천간·지지·오행을 계산합니다.",
      updated: now,
      category: "도구",
    },
    {
      title: "음력 ↔ 양력 변환기",
      link: "/lunar-calendar/",
      summary: "1900~2050년 한국 음력 데이터를 즉시 변환합니다.",
      updated: now,
      category: "도구",
    },
    {
      title: "12띠 운세",
      link: "/zodiac/",
      summary: "12개 띠별 성격·궁합·2026년 운세.",
      updated: now,
      category: "운세",
    },
    {
      title: "로또 6/45 번호 생성기",
      link: "/lotto/",
      summary: "매주 새로 뽑는 로또 번호 5세트.",
      updated: now,
      category: "도구",
    },
    {
      title: "연봉 실수령액 계산기",
      link: "/salary/",
      summary: "4대보험·소득세를 자동 계산합니다.",
      updated: now,
      category: "도구",
    },
  ];

  ZODIACS.forEach((z) => {
    items.push({
      title: `${z.name}띠 운세`,
      link: `/zodiac/${z.slug}/`,
      summary: `${z.name}띠의 성격·궁합·2026년 운세.`,
      updated: now,
      category: "띠별운세",
    });
  });

  const escapeXml = (s: string): string =>
    s
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&apos;");

  const entries = items
    .map(
      (it) => `  <entry>
    <title>${escapeXml(it.title)}</title>
    <link href="${baseUrl}${it.link}" />
    <id>${baseUrl}${it.link}</id>
    <updated>${it.updated}</updated>
    <summary>${escapeXml(it.summary)}</summary>
    <category term="${escapeXml(it.category)}" />
  </entry>`,
    )
    .join("\n");

  const xml = `<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>오늘의 운세 - lucksajueun.com</title>
  <link href="${baseUrl}/" />
  <link href="${baseUrl}/atom.xml" rel="self" />
  <id>${baseUrl}/</id>
  <updated>${now}</updated>
  <subtitle>매일 갱신되는 한국 음력 만세력 기반 무료 운세 서비스</subtitle>
  <generator>Astro v7.3.1</generator>
  <rights>© 2026 오늘의 운세</rights>
${entries}
</feed>`;

  return new Response(xml, {
    headers: {
      "Content-Type": "application/atom+xml; charset=utf-8",
      "Cache-Control": "public, max-age=3600",
    },
  });
};
