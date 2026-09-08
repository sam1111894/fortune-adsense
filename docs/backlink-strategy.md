# lucksajueun.com 백링크 전략

> 자동 백링크 + SEO 색인 가속화 문서

## 📊 현재 자동 백링크 현황

### ✅ 자동 완료 (내가 진행)
- **Google Search Console** 등록 + sitemap 제출 (10개 URL)
- **Cloudflare Pages 백링크 블로그** `blog.lucksajueun.com` (6개 페이지, 모두 lucksajueun.com 백링크)
- **IndexNow** API 색인 알림 (16개 URL) → Bing/Naver/DuckDuckGo/Yandex 자동 색인
- **GitHub Actions** 매일 자정 자동 빌드 + 배포

### 🟡 너 손 작업 (10분)
- **Naver Search Advisor** 등록 (5분)
- **Bing Webmaster Tools** 등록 (5분)

### 🟠 계정 필요 (너 손 30분~1시간)
- **Medium** 발행 (5개 글, 자동 작성됨 — `/tmp/medium-articles/`)
- **Reddit r/korea** 포스트
- **HackerNews** Show HN
- **네이버 카페 가입** (5개)
- **네이버 지식iN** 답변 (5개)

## 📈 예상 트래픽 증가 곡선

| 기간 | 일 방문자 | 출처 |
|---|---|---|
| 1주일 | 10~50 | Google 검색 (한국어) |
| 2주일 | 50~200 | Google + Naver + Bing |
| 1달 | 200~1000 | + Reddit/Medium 백링크 |
| 3달 | 1000~5000 | 안정적 SEO 순위 |
| 6달+ | 5000+ | 도메인 권위 상승 |

## 🎯 키워드 우선순위 (월 검색량)

| 1순위 | 2순위 | 3순위 |
|---|---|---|
| 사주 (49,500) | 사주팔자 (22,200) | 운세 (90,500) |
| 로또 번호 (49,500) | 오늘 운세 (27,100) | 띠 운세 (14,800) |
| 음력 변환 (8,100) | 연봉 계산 (33,100) | 12띠 (6,600) |

## 🛠️ 추가 자동화 가능 (계정만 있으면)

- **Twitter/X 봇** — 매일 운세 자동 트윗 (가이드: `docs/twitter-bot.md`)
- **YouTube Shorts** — AI 영상 자동 생성 (가이드: `docs/youtube-shorts.md`)
- **네이버 카페 자동 답변** — Python + Selenium (너 손 인증 필요)

## 🔄 주기적 자동 작업 (cron)

- **매 자정**: 사이트 자동 빌드 + Cloudflare Pages 자동 배포 (한국어 운세 갱신)
- **매주 일요일**: IndexNow 자동 색인 알림 (GitHub Actions weekly cron)
