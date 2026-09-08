/**
 * 만세력 / 사주 / 운세 계산 헬퍼
 * @fullstackfamily/manseryeok 사용 (1900~2050)
 *
 * 실제 API 시그니처 (검증됨):
 *   solarToLunar(year, month, day) -> { solar, lunar, gapja, julianDay }
 *   getGapja(year, month, day) -> { yearPillar, yearPillarHanja, monthPillar, monthPillarHanja, dayPillar, dayPillarHanja }
 *   calculateSaju(year, month, day, hour?) -> { yearPillar, yearPillarHanja, monthPillar, monthPillarHanja, dayPillar, dayPillarHanja, hourPillar, hourPillarHanja, gapja, isTimeCorrected, correctedTime }
 *   getSolarTermForDate(year, month, day) -> { name, hangul, hanja, ... } | null
 */
import {
  solarToLunar,
  lunarToSolar,
  calculateSaju,
  getSolarTermForDate,
  isSupportedYear,
} from "@fullstackfamily/manseryeok";

export interface DailyFortune {
  date: string; // YYYY-MM-DD
  solarYear: number;
  solarMonth: number;
  solarDay: number;
  lunarYear: number;
  lunarMonth: number;
  lunarDay: number;
  isLeapMonth: boolean;
  yearPillar: string; // 갑자 (음력 년기준, 입춘 전이면 전년도 사용)
  yearPillarHanja: string; // 甲子
  monthPillar: string;
  monthPillarHanja: string;
  dayPillar: string;
  dayPillarHanja: string;
  hourPillar: string;
  hourPillarHanja: string;
  zodiac: string; // 쥐/소/호랑이...
  solarTerm: string; // 절기
  elementOfDay: string; // 일간 오행 (나무/불/흙/쇠/물)
  totalScore: number;
  moneyScore: number;
  loveScore: number;
  healthScore: number;
  workScore: number;
  luckyColor: string;
  luckyNumber: number;
  message: string;
}

const ZODIAC_FROM_JIJI: Record<string, string> = {
  子: "쥐",
  丑: "소",
  寅: "호랑이",
  卯: "토끼",
  辰: "용",
  巳: "뱀",
  午: "말",
  未: "양",
  申: "원숭이",
  酉: "닭",
  戌: "개",
  亥: "돼지",
};

const ELEMENT_OF_CHEONGAN: Record<string, string> = {
  甲: "나무",
  乙: "나무",
  丙: "불",
  丁: "불",
  戊: "흙",
  己: "흙",
  庚: "쇠",
  辛: "쇠",
  壬: "물",
  癸: "물",
};

const LUCKY_COLORS: Record<string, string[]> = {
  나무: ["초록", "연두", "카키"],
  불: ["빨강", "주황", "산호"],
  흙: ["노랑", "갈색", "베이지"],
  쇠: ["흰색", "은색", "회색"],
  물: ["검정", "파랑", "남색"],
};

function hashScore(seed: string, max: number): number {
  let h = 0;
  for (let i = 0; i < seed.length; i++) {
    h = ((h << 5) - h + seed.charCodeAt(i)) | 0;
  }
  return Math.abs(h) % max;
}

/** 오늘의 운세 생성 (년-월-일 입력, 시간은 정오로 고정) */
export function generateDailyFortune(
  year: number,
  month: number,
  day: number,
): DailyFortune {
  if (!isSupportedYear(year)) {
    throw new Error(`지원하지 않는 연도: ${year} (1900~2050)`);
  }

  const lunar = solarToLunar(year, month, day);
  const saju = calculateSaju(year, month, day, 12); // 정오 기준
  const solarTermInfo = getSolarTermForDate(year, month, day);
  const dayCheongan = saju.dayPillarHanja.charAt(0);
  const yearJijiHanja = saju.yearPillarHanja.charAt(1);
  const element = ELEMENT_OF_CHEONGAN[dayCheongan] || "흙";
  const colors = LUCKY_COLORS[element];
  const dateKey = `${year}-${String(month).padStart(2, "0")}-${String(day).padStart(2, "0")}`;

  return {
    date: dateKey,
    solarYear: year,
    solarMonth: month,
    solarDay: day,
    lunarYear: lunar.lunar.year,
    lunarMonth: lunar.lunar.month,
    lunarDay: lunar.lunar.day,
    isLeapMonth: lunar.lunar.isLeapMonth,
    yearPillar: saju.yearPillar,
    yearPillarHanja: saju.yearPillarHanja,
    monthPillar: saju.monthPillar,
    monthPillarHanja: saju.monthPillarHanja,
    dayPillar: saju.dayPillar,
    dayPillarHanja: saju.dayPillarHanja,
    hourPillar: saju.hourPillar,
    hourPillarHanja: saju.hourPillarHanja,
    zodiac: ZODIAC_FROM_JIJI[yearJijiHanja] || "쥐",
    solarTerm: solarTermInfo?.name || "—",
    elementOfDay: element,
    totalScore: 60 + hashScore(dateKey + "total", 41),
    moneyScore: 50 + hashScore(dateKey + "money", 51),
    loveScore: 50 + hashScore(dateKey + "love", 51),
    healthScore: 50 + hashScore(dateKey + "health", 51),
    workScore: 50 + hashScore(dateKey + "work", 51),
    luckyColor: colors[hashScore(dateKey + "color", colors.length)],
    luckyNumber: hashScore(dateKey + "num", 99) + 1,
    message: pickDailyMessage(dateKey, element),
  };
}

const MESSAGES_BY_ELEMENT: Record<string, string[]> = {
  나무: [
    "오늘은 새로운 시작에 좋은 날입니다. 용기를 내어 첫 발을 내딛으세요.",
    "성장과 발전의 기운이 흐릅니다. 배움에 마음을 열면 좋은 결과가 따릅니다.",
    "주변 사람들과의 협력이 빛을 발하는 날입니다. 혼자 끌어안지 마세요.",
  ],
  불: [
    "열정이 최고조인 날입니다. 하고 싶었던 일을 망설이지 말고 시작하세요.",
    "표현력과 매력이 빛나는 하루. 중요한 인연이 기다리고 있습니다.",
    "주변에 밝은 에너지를 퍼뜨리면 돌아오는 것이 큽니다.",
  ],
  흙: [
    "안정과 조화의 기운이 강한 날입니다. 무리하지 말고 꾸준히 가세요.",
    "신뢰가 쌓이는 하루. 약속을 지키면 큰 복이 옵니다.",
    "마음의 여유를 가지면 좋은 결정이 따라옵니다.",
  ],
  쇠: [
    "결단력이 빛나는 날. 결단이 필요했던 일이 있다면 지금 정리하세요.",
    "정리정돈의 기운. 집안일이나 일의 정리에 좋은 날입니다.",
    "옳고 그름이 분명해지는 하루. 원칙을 지키면 좋은 결과.",
  ],
  물: [
    "지혜와 통찰이 빛나는 날. 깊은 사고가 필요한 일에 집중하세요.",
    "유연한 사고가 해결책을 가져다줍니다. 흐름에 맡겨 보세요.",
    "분위기를 읽는 능력이 좋아지는 하루. 커뮤니케이션에 유리합니다.",
  ],
};

function pickDailyMessage(dateKey: string, element: string): string {
  const list = MESSAGES_BY_ELEMENT[element] || MESSAGES_BY_ELEMENT["흙"];
  return list[hashScore(dateKey + "msg", list.length)];
}

/** 사주 4주 계산 (입력 검증 포함) */
export function calculateBirthSaju(
  year: number,
  month: number,
  day: number,
  hour: number = 12,
) {
  if (!isSupportedYear(year)) {
    throw new Error(`지원하지 않는 연도: ${year} (1900~2050)`);
  }
  if (hour < 0 || hour > 23) {
    throw new Error(`시간은 0~23 사이여야 합니다: ${hour}`);
  }
  const lunar = solarToLunar(year, month, day);
  const saju = calculateSaju(year, month, day, hour);
  return { lunar, saju };
}

/** 양력 → 음력 변환 */
export function convertSolarToLunar(year: number, month: number, day: number) {
  if (!isSupportedYear(year)) {
    throw new Error(`지원하지 않는 연도: ${year} (1900~2050)`);
  }
  return solarToLunar(year, month, day);
}

/** 음력 → 양력 변환 (윤달 지원) */
export function convertLunarToSolar(
  year: number,
  month: number,
  day: number,
  isLeapMonth: boolean = false,
) {
  if (!isSupportedYear(year)) {
    throw new Error(`지원하지 않는 연도: ${year} (1900~2050)`);
  }
  return lunarToSolar(year, month, day, isLeapMonth);
}