// 12띠 데이터 — [slug].astro에서 import
export const ZODIACS = [
  {
    slug: "rat", name: "쥐", hanja: "子", element: "수(水)", elementColor: "#3b82f6",
    direction: "북쪽", month: "11월", directionTime: "23:00~01:00",
    personality: "쥐띠는 영리하고 민첩합니다. 새로운 환경에 빠르게 적응하고, 위기를 기회로 바꾸는 능력이 뛰어납니다. 사교성이 좋아 다양한 사람들과 원만한 관계를 유지하며, 재물에 대한 감각이 좋습니다.",
    strengths: ["뛰어난 적응력", "재물 감각", "사교성", "위기 대응 능력"],
    weaknesses: ["때로 의심이 많음", "과도한 경쟁심", "감정 표현이 서투름"],
    bestMatch: [
      { name: "용", reason: "용의 야심과 쥐의 영리함이 서로 보완" },
      { name: "소", reason: "소의 성실함이 쥐의 빠른 결정을 뒷받침" }
    ],
    worstMatch: [
      { name: "말", reason: "둘 다 자유를 추구해 갈등 발생 가능" },
      { name: "양", reason: "성향 차이로 마찰이 잦음" }
    ],
    lucky: { color: "파랑", number: 4, day: "수요일" },
    advice: "올해는 새로운 도전을 시작하기에 좋은 해입니다. 재물운이 상승하므로 작은 투자도 긍정적 결과를 가져올 수 있습니다. 다만 건강 관리에 신경 써야 합니다."
  },
  {
    slug: "ox", name: "소", hanja: "丑", element: "토(土)", elementColor: "#a16207",
    direction: "북북동", month: "12월", directionTime: "01:00~03:00",
    personality: "소띠는 성실하고 끈기 있습니다. 한 번 시작한 일은 끝까지 가는 책임감이 강하며, 인내심이 좋아 큰 성과를 이루어냅니다. 신뢰를 중시해 주변 사람들의 믿음을 받습니다.",
    strengths: ["강한 책임감", "인내심", "성실함", "신뢰성"],
    weaknesses: ["변화에 대한 적응이 느림", "고집이 셀 수 있음", "자기표현 부족"],
    bestMatch: [
      { name: "쥐", reason: "쥐의 영리함이 소의 성실함과 조화" },
      { name: "뱀", reason: "뱀의 지혜와 소의 인내가 균형을 이룸" }
    ],
    worstMatch: [
      { name: "양", reason: "성격 차이로 갈등 잦음" },
      { name: "호랑이", reason: "강한 자존심 대립 가능" }
    ],
    lucky: { color: "노랑", number: 5, day: "금요일" },
    advice: "올해는 묵묵히 해오던 일의 결실을 거두는 해입니다. 인내한 만큼 보상이 따르며, 재물운이 안정적입니다. 새로운 시도보다 기존 일의 완성도에 집중하세요."
  },
  {
    slug: "tiger", name: "호랑이", hanja: "寅", element: "목(木)", elementColor: "#16a34a",
    direction: "북동", month: "1월", directionTime: "03:00~05:00",
    personality: "호랑이띠는 용감하고 카리스마가 강합니다. 도전을 두려워하지 않고, 리더십이 뛰어납니다. 정의를 중시하며 약자를 보호하려는 의지가 강합니다.",
    strengths: ["강한 리더십", "추진력", "용기", "정의감"],
    weaknesses: ["성급한 판단", "독선적일 수 있음", "끈기 부족"],
    bestMatch: [
      { name: "말", reason: "둘 다 활기차고 자유를 사랑해 통함" },
      { name: "개", reason: "개가 충직하게 호랑이를 보조" }
    ],
    worstMatch: [
      { name: "뱀", reason: "지혜 vs. 직감, 성향 충돌" },
      { name: "원숭이", reason: "서로 궤변을 의심" }
    ],
    lucky: { color: "초록", number: 8, day: "화요일" },
    advice: "올해는 변화와 도전의 해입니다. 새로운 프로젝트나 이직을 고려한다면 좋은 시기입니다. 다만 신중함도 함께 가져야 큰 그림을 그릴 수 있습니다."
  },
  {
    slug: "rabbit", name: "토끼", hanja: "卯", element: "목(木)", elementColor: "#65a30d",
    direction: "동", month: "2월", directionTime: "05:00~07:00",
    personality: "토끼띠는 온화하고 부드럽습니다. 예술적 감각이 뛰어나고, 타인의 감정을 잘 읽어냅니다. 평화를 추구하며 분쟁을 피하는 성격입니다.",
    strengths: ["부드러운 카리스마", "예술적 감각", "공감 능력", "평화 추구"],
    weaknesses: ["결단력 부족", "우유부단", "갈등 회피"],
    bestMatch: [
      { name: "양", reason: "둘 다 부드럽고 예술적 감성 공유" },
      { name: "개", reason: "개가 토끼를 보호하고 안정감 제공" }
    ],
    worstMatch: [
      { name: "닭", reason: "꼼꼼함 vs 부드러움, 가치관 차이" },
      { name: "용", reason: "용의 야심이 토끼에겐 부담" }
    ],
    lucky: { color: "연두", number: 6, day: "월요일" },
    advice: "올해는 인간관계와 감정 안정에 집중하세요. 예술·창작 활동에서 좋은 결과를 기대할 수 있습니다. 큰 변화보다는 내면의 성장에 집중하는 것이 유리합니다."
  },
  {
    slug: "dragon", name: "용", hanja: "辰", element: "토(土)", elementColor: "#9333ea",
    direction: "동남", month: "3월", directionTime: "07:00~09:00",
    personality: "용띠는 카리스마가 강하고 야심이 큽니다. 큰일을 성취하며, 지도력이 있습니다. 자신감이 넘치고, 주변에 영향력을 행사합니다.",
    strengths: ["강한 야심", "추진력", "카리스마", "비전"],
    weaknesses: ["독선적일 수 있음", "완고함", "타협 부족"],
    bestMatch: [
      { name: "원숭이", reason: "원숭이의 재치가 용의 야심과 시너지" },
      { name: "쥐", reason: "쥐의 영리함이 용을 보조" }
    ],
    worstMatch: [
      { name: "개", reason: "성향 차이 큼, 자주 마찰" },
      { name: "양", reason: "서로 양보 없이 대립" }
    ],
    lucky: { color: "보라", number: 9, day: "목요일" },
    advice: "올해는 큰 꿈을 이룰 수 있는 해입니다. 사업·취업·학업 모두 좋은 결과를 기대할 수 있습니다. 다만 건강과 가족 관계에 신경 써야 합니다."
  },
  {
    slug: "snake", name: "뱀", hanja: "巳", element: "화(火)", elementColor: "#dc2626",
    direction: "남남동", month: "4월", directionTime: "09:00~11:00",
    personality: "뱀띠는 지혜롭고 직관이 뛰어납니다. 신중하게 판단하며, 깊이 있는 사고를 합니다. 비밀을 잘 지키고, 전략적 사고가 강합니다.",
    strengths: ["탁월한 직관", "지혜", "신중함", "전략적 사고"],
    weaknesses: ["의심이 많음", "표현 부족", "완벽주의"],
    bestMatch: [
      { name: "소", reason: "소의 성실함과 뱀의 지혜가 균형" },
      { name: "닭", reason: "둘 다 신중하고 분석적" }
    ],
    worstMatch: [
      { name: "돼지", reason: "서로 다른 가치관으로 충돌" },
      { name: "호랑이", reason: "직감 vs 충동, 갈등 가능" }
    ],
    lucky: { color: "빨강", number: 2, day: "화요일" },
    advice: "올해는 내면의 지혜가 빛나는 해입니다. 연구·학습·자기계발에서 좋은 결과가 따릅니다. 인간관계에서는 한 발 물러서는 지혜가 필요합니다."
  },
  {
    slug: "horse", name: "말", hanja: "午", element: "화(火)", elementColor: "#ea580c",
    direction: "남", month: "5월", directionTime: "11:00~13:00",
    personality: "말띠는 활기차고 자유를 사랑합니다. 사람들과 어울리기를 좋아하며, 활동적입니다. 따뜻한 마음을 가지고 있습니다.",
    strengths: ["활기", "사교성", "자유로움", "따뜻한 마음"],
    weaknesses: ["끈기 부족", "성급함", "한 곳에 머무르기 어려움"],
    bestMatch: [
      { name: "호랑이", reason: "둘 다 활기차고 자유를 사랑" },
      { name: "양", reason: "양의 다정함이 말의 자유로움과 조화" }
    ],
    worstMatch: [
      { name: "쥐", reason: "서로 다른 가치관" },
      { name: "뱀", reason: "직감 vs 신중함, 마찰 잦음" }
    ],
    lucky: { color: "주황", number: 3, day: "일요일" },
    advice: "올해는 활동과 모험의 해입니다. 여행·운동·새로운 만남에서 좋은 기회가 옵니다. 한 가지에 집중하기보다 다양한 시도를 해보세요."
  },
  {
    slug: "goat", name: "양", hanja: "未", element: "토(土)", elementColor: "#ca8a04",
    direction: "남남서", month: "6월", directionTime: "13:00~15:00",
    personality: "양띠는 다정하고 예술적입니다. 공감 능력이 뛰어나고, 타인의 아픔을 잘 이해합니다. 평화롭고 차분한 성격입니다.",
    strengths: ["공감 능력", "예술성", "다정함", "인내심"],
    weaknesses: ["우유부단", "자기주장 부족", "감정에 쉽게 영향"],
    bestMatch: [
      { name: "토끼", reason: "둘 다 부드럽고 예술적" },
      { name: "말", reason: "말의 활기가 양에겐 좋은 자극" }
    ],
    worstMatch: [
      { name: "쥐", reason: "서로 다른 생활 방식" },
      { name: "소", reason: "성격 차이로 갈등 잦음" }
    ],
    lucky: { color: "베이지", number: 7, day: "토요일" },
    advice: "올해는 인간관계와 감정 안정에 집중하세요. 가족·연인과의 시간을 소중히 하면 좋은 결과가 따릅니다. 재물은 안정적이지만 큰 돈은 기대하지 마세요."
  },
  {
    slug: "monkey", name: "원숭이", hanja: "申", element: "금(金)", elementColor: "#94a3b8",
    direction: "서남", month: "7월", directionTime: "15:00~17:00",
    personality: "원숭이띠는 영리하고 재치 있습니다. 문제 해결 능력이 뛰어나고, 위기를 기회로 바꾸는 능력이 있습니다. 사교성이 좋습니다.",
    strengths: ["재치", "문제 해결", "적응력", "사교성"],
    weaknesses: ["가벼움", "끈기 부족", "장난기 과다"],
    bestMatch: [
      { name: "용", reason: "용의 야심과 원숭이의 재치가 시너지" },
      { name: "쥐", reason: "둘 다 영리하고 사교적" }
    ],
    worstMatch: [
      { name: "호랑이", reason: "성격 차이로 갈등 잦음" },
      { name: "돼지", reason: "가벼움 vs 진지함 차이" }
    ],
    lucky: { color: "은색", number: 8, day: "수요일" },
    advice: "올해는 새로운 기술과 지식을 배우기 좋은 해입니다. 자기계발·창의적 활동에서 좋은 결과가 따릅니다. 다만 한 가지에 집중하는 인내가 필요합니다."
  },
  {
    slug: "rooster", name: "닭", hanja: "酉", element: "금(金)", elementColor: "#eab308",
    direction: "서", month: "8월", directionTime: "17:00~19:00",
    personality: "닭띠는 정확하고 시간 관념이 강합니다. 꼼꼼하며, 책임감이 강합니다. 주변 사람들에게 신뢰를 받는 성격입니다.",
    strengths: ["정확성", "책임감", "꼼꼼함", "시간 관리"],
    weaknesses: ["까다로움", "융통성 부족", "비판적"],
    bestMatch: [
      { name: "뱀", reason: "둘 다 신중하고 분석적" },
      { name: "소", reason: "소의 성실함과 닭의 꼼꼼함이 시너지" }
    ],
    worstMatch: [
      { name: "토끼", reason: "꼼꼼함 vs 부드러움, 가치관 차이" },
      { name: "말", reason: "꼼꼼함 vs 자유로움, 충돌" }
    ],
    lucky: { color: "금색", number: 1, day: "금요일" },
    advice: "올해는 안정적인 성과를 거두는 해입니다. 꾸준히 해온 일의 결실이 따르며, 재물운도 안정적입니다. 새로운 시도보다 기존 일의 마감에 집중하세요."
  },
  {
    slug: "dog", name: "개", hanja: "戌", element: "토(土)", elementColor: "#854d0e",
    direction: "서북", month: "9월", directionTime: "19:00~21:00",
    personality: "개띠는 충직하고 정직합니다. 의리가 강하며, 가족과 친구를 매우 중시합니다. 책임감이 강하고 신뢰를 받습니다.",
    strengths: ["충직함", "의리", "책임감", "신뢰성"],
    weaknesses: ["걱정이 많음", "완고함", "변화에 대한 두려움"],
    bestMatch: [
      { name: "토끼", reason: "토끼의 부드러움이 개에겐 편안함" },
      { name: "호랑이", reason: "개가 호랑이를 충직하게 보조" }
    ],
    worstMatch: [
      { name: "용", reason: "성향 차이, 갈등 잦음" },
      { name: "양", reason: "걱정 vs 다정함, 마찰" }
    ],
    lucky: { color: "갈색", number: 4, day: "토요일" },
    advice: "올해는 건강과 가족에 집중해야 합니다. 자기 자신보다 주변 사람을 챙기느라 지칠 수 있으니, 자기 시간도 확보하세요. 재물은 점진적으로 안정됩니다."
  },
  {
    slug: "pig", name: "돼지", hanja: "亥", element: "수(水)", elementColor: "#0891b2",
    direction: "북북서", month: "10월", directionTime: "21:00~23:00",
    personality: "돼지띠는 순수하고 솔직합니다. 낙천적이며, 다른 사람을 잘 믿어줍니다. 학문과 예술에 관심이 많고, 여유를 즐깁니다.",
    strengths: ["순수함", "낙천성", "관용", "학문적 흥미"],
    weaknesses: ["순진함", "현실감 부족", "거절을 어려워"],
    bestMatch: [
      { name: "토끼", reason: "둘 다 부드럽고 공감 능력 뛰어남" },
      { name: "호랑이", reason: "호랑이가 돼지를 보호" }
    ],
    worstMatch: [
      { name: "뱀", reason: "직감 vs 신중함, 가치관 차이" },
      { name: "원숭이", reason: "가벼움 vs 진지함, 갈등" }
    ],
    lucky: { color: "하늘색", number: 9, day: "일요일" },
    advice: "올해는 학문·예술·여행에서 좋은 기회가 옵니다. 자기계발과 내면의 성장에 집중하면 큰 만족을 얻습니다. 재물은 안정적이지만 큰 돈은 기대하지 마세요."
  }
];