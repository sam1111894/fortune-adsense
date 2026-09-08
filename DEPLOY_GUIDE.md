# 🍀 fortune-adsense — 무료 만세력 운세 사이트

> Astro 7 + Cloudflare Pages + Google AdSense로 만드는 무료 한국어 운세·사주 웹앱.
> 서버 비용 0원. 정적 페이지로 빌드되어 Cloudflare CDN에서 무료 호스팅.

## ✅ 이미 구현된 기능

- **오늘의 운세** (`/`) — 매일 KST 기준 빌드 타임에 계산, SSG로 정적 제공
- **사주팔자 계산기** (`/saju`) — 생년월일시 입력 → 4주 한자 즉시 계산 (클라이언트 사이드)
- **음력 ↔ 양력 변환기** (`/lunar-calendar`) — 양방향 + 윤달 지원
- **12띠 가이드** (`/zodiac`) — 12개 띠의 성향·오행·잘 맞는 활동
- **소개 페이지** (`/about`) — 데이터 출처·면책·기술 스택
- **robots.txt / sitemap.xml** 자동 생성

## 🚀 로컬 실행

```bash
cd ~/HermesPractice/reviewer/fortune-adsense
npm install        # 이미 설치됨
npm run dev        # http://localhost:4321 (개발 서버)
npm run build      # → dist/ 폴더에 정적 파일 생성
npm run preview    # dist/ 정적 파일 로컬 서빙
```

빌드 결과: **5개 페이지 + 정적 자산 총 332KB** (Cloudflare Pages 무료 한도 20,000 파일 대비 무시할 수준)

## ☁️ Cloudflare Pages 배포 (무료)

### 방법 1: GitHub 연동 자동 배포 (1분 설정)

1. **GitHub에 push**
   ```bash
   cd ~/HermesPractice/reviewer/fortune-adsense
   git init && git add -A && git commit -m "feat: fortune site initial"
   # GitHub에서 새 repo 만들고 (e.g. fortune-adsense)
   git remote add origin git@github.com:YOUR_USERNAME/fortune-adsense.git
   git branch -M main && git push -u origin main
   ```

2. **Cloudflare 대시보드 접속**: https://dash.cloudflare.com → Workers & Pages → Create application → Pages → Connect to Git

3. **빌드 설정**:
   | 항목 | 값 |
   |---|---|
   | Project name | `fortune-adsense` (URL: `fortune-adsense.pages.dev`) |
   | Production branch | `main` |
   | Build command | `npm run build` |
   | Build output directory | `dist` |
   | Root directory | _(비워두기)_ |
   | Environment variables | (없음) |

4. **Save and Deploy** → 1~2분 후 `https://fortune-adsense.pages.dev`에서 접속 가능.

### 방법 2: 직접 업로드 (Wrangler CLI)

```bash
npm install -g wrangler  # 처음 한 번만
cd ~/HermesPractice/reviewer/fortune-adsense
npm run build
wrangler pages deploy dist --project-name=fortune-adsense
```

Cloudflare 계정 인증은 한 번만. 이후 `git push`만 하면 자동 빌드+배포.

### 무료 한도 (Cloudflare Pages Free Plan)

| 항목 | 한도 | 우리 사용량 |
|---|---|---|
| 빌드 | 500/월 | 일 1회 푸시 시 ~30/월 |
| 파일 수 | 20,000개 | ~10개 |
| 대역폭 | **무제한** | ✅ |
| 커스텀 도메인 | 100개 | 1~2개 |
| 동시 빌드 | 1 | 충분 |

## 💰 Google 애드센스 연동

### 1단계: 애드센스 신청

1. https://adsense.google.com 접속 → Google 계정으로 로그인
2. **사이트 추가**: `https://fortune-adsense.pages.dev` 입력
3. **결제 정보 입력**: 한국 KYC (개인정보 + 세금 정보) — 보통 1~3일 승인
4. **코드 발급**: `ca-pub-XXXXXXXXXXXXXX` 형식의 publisher ID 받음

### 2단계: 사이트에 코드 삽입

`src/layouts/Layout.astro` 28번째 줄 부근 (현재 주석 처리됨)을 해제:

```html
<!-- 이 주석을 풀고 ca-pub-XXXXXXXXXXXXXX를 본인 코드로 교체 -->
<script
  async
  src="https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client=ca-pub-여기에_당신의_코드"
  crossorigin="anonymous"
></script>
```

광고 단위 배치 (각 페이지 본문 끝):

```astro
<!-- 예: src/pages/index.astro 마지막에 추가 -->
<ins class="adsbygoogle"
     style="display:block"
     data-ad-client="ca-pub-XXXXXXXXXXXXXX"
     data-ad-slot="YYYYYYYYYY"
     data-ad-format="auto"
     data-full-width-responsive="true"></ins>
<script>(adsbygoogle = window.adsbygoogle || []).push({});</script>
```

### 3단계: 자동광고 (가장 쉬움)

Layout.astro의 `<head>` 안의 `<script>` 태그만 활성화하면 Google이 자동으로 광고 위치를 결정해서 배치. 별도 `<ins>` 태그 없이도 동작.

### ⚠️ 애드센스 승인 팁

| 항목 | 우리 사이트 상태 |
|---|---|
| **원본 콘텐츠** | ✅ 100% 자체 작성 (만세력 라이브러리 기반 계산) |
| **풍부한 페이지** | ✅ 5개 페이지, 각 페이지 본문 500자+ |
| **개인정보 페이지** | ✅ `/about` 페이지에 면책·정책 명시 |
| **정책 준수** | ✅ 의료·법률 조언 아님 명시 |
| **도메인 권위** | ⚠️ `.pages.dev` 도메인 처음엔 신뢰도 낮음 → 1~3개월 후 커스텀 도메인 권장 |

### 커스텀 도메인 (선택, 권장)

Cloudflare Pages → Custom domains → `fortune.example.com` 연결. 가비아/후이즈에서 도메인 ₩15,000/년 정도. 도메인만 있으면 애드센스 승인이 더 쉬워짐.

## 📈 트래픽 유도 (SEO)

### 네이버·구글 등록

- **Naver 서치어드바이저**: https://searchadvisor.naver.com → 사이트 등록
- **Google Search Console**: https://search.google.com/search-console → sitemap.xml 제출

검색량 큰 키워드 (한국어):
- "오늘 운세" (월 ~50만)
- "만세력" (월 ~10만)
- "사주팔자" (월 ~10만)
- "음력 양력 변환" (월 ~3만)
- "띠별 운세" (월 ~5만)

### 매일 자동 빌드 (선택)

Cloudflare Pages는 GitHub push 시 자동 빌드. 매일 운세 갱신을 원한다면 GitHub Actions + cron으로 자정 push:

```yaml
# .github/workflows/daily.yml
name: Daily Fortune Refresh
on:
  schedule:
    - cron: '0 15 * * *'  # UTC 15:00 = KST 24:00
jobs:
  refresh:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: echo "noop - actual build happens on next push"
```

또는 간단히 GitHub Actions에서 `git commit --allow-empty -m "refresh" && git push` 한 줄로 매일 푸시 → Cloudflare Pages 자동 빌드.

## 🔧 기술 스택

| 항목 | 선택 | 이유 |
|---|---|---|
| 프레임워크 | Astro 7 | 정적 사이트 + 부분 hydration (계산기만 JS) |
| 음력 라이브러리 | @fullstackfamily/manseryeok | 1900~2050 한국 음력 DB |
| 언어 | TypeScript strict | 타입 안전성 |
| 호스팅 | Cloudflare Pages | 무료 + 무제한 bandwidth + 한국 CDN |
| 광고 | Google AdSense | 도구형 사이트와 정책 매칭 |

## 📂 프로젝트 구조

```
fortune-adsense/
├── astro.config.mjs          # Astro 설정 (site URL, SSG)
├── package.json              # @fullstackfamily/manseryeok 의존성
├── public/
│   └── favicon.svg           # 🍀 아이콘
└── src/
    ├── lib/
    │   └── fortune.ts        # 만세력·사주 계산 (서버·클라이언트 공용)
    ├── layouts/
    │   └── Layout.astro      # 공통 레이아웃 + AdSense 코드 위치
    └── pages/
        ├── index.astro       # 오늘의 운세 (메인)
        ├── saju.astro        # 사주팔자 계산기
        ├── lunar-calendar.astro  # 음력↔양력 변환기
        ├── zodiac.astro      # 12띠 가이드
        ├── about.astro       # 소개·면책
        ├── sitemap.xml.ts    # sitemap 동적 생성
        └── robots.txt.ts     # robots.txt 동적 생성
```

## ⚠️ 면책·정책

이 사이트는 **흥미·오락 목적**입니다. 의료·법률·재정·인간관계 결정을 의존하지 마세요. 자세한 면책은 `/about` 페이지 참조.

## 📊 예상 수익 (참고)

| 월 방문자 | 예상 CTR | 예상 수익 (USD) |
|---|---|---|
| 1,000 | 1.5% | $1~3 |
| 10,000 | 1.5% | $15~30 |
| 50,000 | 1.5% | $75~150 |
| 100,000 | 1.5% | $150~300 |

한국 키워드 CPC가 높아서 (운세·사주 카테고리 $0.30~0.80/클릭) 영어권 대비 동일 트래픽의 2~3배 수익 가능.
SEO가 잘 되는 운세 사이트는 6~12개월 내 월 5~20만 방문자 도달 가능.

---

**즉시 시작하려면:**
1. `git push` 후 Cloudflare Pages 연결
2. 애드센스 신청
3. Naver/Google Search Console 등록
4. 1주일 후 색인 확인 → 광고 활성화