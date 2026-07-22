# 지식의 탑 · Tower of Knowledge

수영의 검정고시/성공회대 입시용 RPG 학습 포털. **의존성 없음** (Python 표준 라이브러리 + vanilla JS).

## 실행 (로컬)
```bash
cd tower-of-knowledge
python3 server.py          # → http://127.0.0.1:8787
```

## 홈페이지로 배포 (Render 무료 + Turso 무료 DB) — 데이터 유지
데이터를 외부 무료 DB(**Turso** = 호스팅 sqlite)에 저장하므로, Render가 재배포·재시작해도 봉인·진행·오답이 유지됩니다. 로컬에선 `TURSO_*` 환경변수가 없으면 그냥 로컬 sqlite를 씁니다(자동 분기).

### 1) Turso DB 만들기 (무료, 카드 X)
```bash
# Turso CLI 설치
curl -sSfL https://get.tur.so/install.sh | bash
turso auth signup                 # 브라우저로 가입/로그인
turso db create jisik-tower       # DB 생성
turso db show jisik-tower --url   # → libsql://... (TURSO_DATABASE_URL 값)
turso db tokens create jisik-tower  # → 긴 토큰 (TURSO_AUTH_TOKEN 값)
```

### 2) 코드를 GitHub에 올리기
```bash
cd tower-of-knowledge
git init && git add -A && git commit -m "지식의 탑"
# GitHub에서 빈 저장소 만든 뒤:
git remote add origin https://github.com/<본인>/jisik-tower.git
git branch -M main && git push -u origin main
```
> `.gitignore`가 키파일·app.db·캐시를 제외하므로 비밀은 안 올라갑니다.

### 3) Render에 연결
1. https://render.com 가입(무료, 카드 X) → **New → Blueprint** → 방금 만든 GitHub 저장소 선택.
2. `render.yaml`이 자동 인식됨. 배포 전 **Environment**에 값 3개 입력:
   - `GEMINI_API_KEY` = 본인 AI Studio 키
   - `TURSO_DATABASE_URL` = 1)의 `libsql://...`
   - `TURSO_AUTH_TOKEN` = 1)의 토큰
3. **Apply/Deploy** → 몇 분 뒤 `https://jisik-tower.onrender.com` 주소로 접속.

- 키·토큰은 Render 환경변수로만 존재 → 코드/브라우저에 노출 안 됨.
- 무료 인스턴스는 15분 미사용 시 잠들고, 다음 접속 때 30초쯤 깨어납니다(데이터는 Turso에 있어 안전).
- 기출 문제를 갱신했을 땐 로컬에서 `TURSO_DATABASE_URL=... TURSO_AUTH_TOKEN=... python3 server.py --sync-questions` 로 Turso에 반영.
- 첫 실행 시 `black_bank.db`를 `app.db`로 복사하고 필요한 테이블을 추가합니다. (원본 `black_bank.db`는 건드리지 않음)
- 첫 접속 = 4자리 봉인 설정, 이후 = 봉인 해제. **봉인·세션·학습상태는 전부 서버(`app.db`)에 저장** (기기 저장 아님).

## AI 수호령 연결 — Google AI Studio (Gemini) 권장
채팅/백지검증/출제분석/기출 출제분석에 쓰입니다. 키가 없으면 크래시 없이 안내만 나옵니다.

### Google AI Studio 키 넣기 (가장 쉬움)
1. https://aistudio.google.com/apikey 에서 **Create API key** → 키 복사.
2. 프로젝트 폴더(`tower-of-knowledge/`)에 **`gemini_key.txt`** 파일을 만들고 키만 붙여넣어 저장.
3. `python3 server.py` 재시작. 시작 로그에 `🧚 수호령: Google AI Studio(Gemini) 연결됨`이 뜨고, 앱의 **🧚 수호령 소환** 탭 상단이 `● 연결됨`으로 바뀝니다.

환경변수로 줘도 됩니다: `GEMINI_API_KEY=... python3 server.py` (모델 변경: `GEMINI_MODEL=gemini-2.5-flash`).

### (대안) Anthropic Claude
`anthropic_key.txt` 파일 또는 `ANTHROPIC_API_KEY=sk-ant-...`. Gemini 키가 있으면 Gemini가 우선됩니다.

> 🔒 키는 **서버에서만** 사용되고 브라우저로 절대 전송되지 않습니다. `gemini_key.txt`는 공유·업로드하지 마세요.

## 관리자 모드 (🛠 관리소)
봉인 소유자가 로그인하면 관리소 탭이 열립니다. 모든 쓰기는 세션 인증 필요.
- **기출 문제**: `black_bank.db` 문항 추가/수정/삭제 (연도·회차·과정·과목·원문)
- **학습 지문**: LV.1 정찰용 지문. 본문에서 `**단어**` = 금색 강조
- **플래시카드**: LV.2 전투용 개념/정의

## 문제은행 (📚) — 퀴즐렛식 풀이 + 즉시 채점
과목·연도·회차·과정 필터 → **📖 열람**/**🎲 랜덤 소환** → 한 문제씩 카드로 풀이.
- 선지를 고르면 **즉시 정답/오답** 표시(정답 공개). 서버의 정답키(`black_bank.db`의 `answer_text`를 파싱)로 채점.
- **국어 지문**: `[3~4]`처럼 한 지문이 여러 문항에 걸리면 해당 문항에서 지문을 함께 보여주고, 같은 지문 문항은 연속으로 묶여 나옴.
- **이미지 문항 (원본 PDF 크롭)**: `pdf/` 폴더의 `{연도}_{회차}_{과정}_{과목}_문제.pdf`에서 **문항 단위로 잘라 이미지로** 보여줍니다. 잘라낸 PNG는 `pdf_cache/`에 캐시.
  - **수학**: 수식이 특수폰트라 **전 문항**을 이미지로.
  - **과학·사회·한국사**: **그림 있는 문항만** 자동 감지(PDF의 래스터 이미지/큰 벡터 드로잉)해서 이미지로, 나머지는 텍스트. 그림+선지가 함께 크롭되고 아래 ①②③④ 버튼으로 채점.
  - **국어**: 텍스트+지문으로 렌더(그림 거의 없음).
  - 필요 패키지: `pip install PyMuPDF` (없으면 텍스트 과목은 정상, 이미지 문항만 표시 안 됨).

## 오답노트 (📕)
틀린 문제를 자동 수집 → 과목별 오답 수 + 문항 목록(내 선택/정답). **↺ 복습**으로 해당 회차로 점프.
같은 문항을 다시 풀어 맞히면 목록에서 자동으로 빠짐(최신 응시 기준). 응시 기록은 서버(`attempts` 테이블)에 저장.

## 파일
- `server.py` — 백엔드(정적 서빙 + 인증 + 상태 + 기출 + 관리자 + AI 프록시)
- `index.html` — SPA 전체 (디자인 재현)
- `black_bank.db` — 원본 기출 (seed). `app.db` — 실제 앱 DB(생성물, 삭제하면 봉인부터 재설정)
- `pdf/` — 수학 문제 PDF (이미지 렌더 소스). `pdf_cache/` — 문항별로 잘라낸 PNG 캐시(생성물, 지워도 자동 재생성)

자체검사: `python3 server.py --selftest`
