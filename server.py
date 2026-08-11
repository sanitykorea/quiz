#!/usr/bin/env python3
"""지식의 탑 — Personal study portal backend (stdlib only + PyMuPDF for PDF images).

- 서버 저장 보안: 봉인(PIN)은 pbkdf2+salt 해시로 sqlite에 저장, 세션은 httpOnly 쿠키.
- 문제 출제: black_bank.db 기출 → 개별 문항(발문·선지·지문) 분해 + 정답표 파싱 채점.
- 이미지 문항: 수학/그림 문항은 원본 PDF에서 문항 단위로 잘라 이미지로 제공.
- 오답노트: 응시 기록 수집, 최신 응시 기준 오답 집계.
- 관리자 모드: 기출/학습지문/카드/고정일정 CRUD.
- AI: Google AI Studio(Gemini) 키가 있으면 프록시, 없으면 클라이언트가 우아하게 폴백.

실행:  python3 server.py          → http://127.0.0.1:8787
자체검사: python3 server.py --selftest   / 기출 재동기화: python3 server.py --sync-questions
"""
import http.server, socketserver, sqlite3, json, hashlib, secrets, os, time, urllib.request, urllib.error, urllib.parse, re, base64, datetime
from http import cookies

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("DATA_DIR", HERE)   # 배포 시 영속 디스크 경로 지정(예: /data)
os.makedirs(DATA_DIR, exist_ok=True)
DB = os.path.join(DATA_DIR, "app.db")
PDF_CACHE = os.path.join(DATA_DIR, "pdf_cache")
SEED_DB = os.path.join(HERE, "black_bank.db")
HTML = os.path.join(HERE, "index.html")
PDF_DIR = os.path.join(HERE, "pdf")
SESSION_TTL = 60 * 60 * 24 * 30  # 30일

CONTENT_VERSION = "2"   # 지문/카드 콘텐츠 버전 — 올리면 배포 시 새 콘텐츠가 자동 추가됨
SEED_UNITS = [
    # 과학 (취약)
    ("과학", "광합성", "식물은 잎의 **엽록체**에서 **빛에너지**를 흡수하여 물과 이산화탄소를 원료로 **포도당**과 **산소**를 만든다. 이 과정을 광합성이라 하며, 주로 낮에 활발하게 일어난다."),
    ("과학", "세포와 유전", "생물의 유전 정보는 세포핵 속 **염색체**에 담겨 있고, 그 본체는 **DNA**이다. DNA에서 형질을 결정하는 특정 부분이 **유전자**이다. 사람의 체세포에는 **23쌍(46개)**의 염색체가 있다."),
    ("과학", "뉴턴의 운동 법칙", "**관성 법칙**(제1): 힘이 작용하지 않으면 물체는 운동 상태를 유지한다. **가속도 법칙**(제2): 힘 F = 질량 m × 가속도 a. **작용·반작용 법칙**(제3): 힘을 주면 크기가 같고 방향이 반대인 힘을 되받는다."),
    ("과학", "에너지 전환과 보존", "에너지는 형태가 바뀌어도 **총량은 일정하게 보존**된다(**에너지 보존 법칙**). 높은 곳의 **위치 에너지**는 떨어지며 **운동 에너지**로 바뀌고, 전기 에너지는 열·빛·운동 등 여러 형태로 전환된다."),
    ("과학", "산과 염기", "**산**은 물에 녹아 **수소 이온(H⁺)**을, **염기**는 **수산화 이온(OH⁻)**을 내놓는다. 산·염기의 세기는 **pH**로 나타내며 pH 7이 중성, 작으면 산성, 크면 염기성이다. 산과 염기가 만나 물과 염을 만드는 반응이 **중화 반응**이다."),
    ("과학", "지구 시스템", "지구는 **지권·수권·기권·생물권**이 서로 영향을 주고받는 하나의 시스템이다. 태양 **복사 에너지**가 물과 대기를 순환시키고, 지구 내부 에너지는 **판**을 움직여 지진·화산을 일으킨다."),
    # 도덕 (취약)
    ("도덕", "의무론과 결과론", "**칸트의 의무론**은 결과보다 **동기와 의무**를 중시하며 누구에게나 타당한 **보편적 도덕 법칙**을 따르라 한다. **공리주의(결과론)**는 행위 결과의 유용성, 곧 **'최대 다수의 최대 행복'**을 옳음의 기준으로 삼는다."),
    ("도덕", "인권과 정의", "**인권**은 인간이면 누구나 태어나면서 갖는 **천부적·보편적·불가침**의 권리이다. **정의**는 각자에게 정당한 몫을 주는 것으로, 절차의 공정성을 강조하는 **절차적 정의**와 몫의 분배를 다루는 **분배적 정의**가 있다."),
    ("도덕", "환경·생명 윤리", "**인간 중심주의**는 자연을 인간의 수단으로 보지만, **생태 중심주의**는 생태계 전체의 가치를 존중한다. 미래 세대와 자연에 대한 책임을 강조하며 **지속 가능한 발전**을 추구한다."),
    # 수학 (취약)
    ("수학", "이차방정식", "ax²+bx+c=0의 해는 **근의 공식** x = (−b ± √(b²−4ac)) / 2a 로 구한다. **판별식** D = b²−4ac 의 부호로 근을 판별한다: D>0이면 서로 다른 두 실근, D=0이면 **중근**, D<0이면 실근이 없다."),
    ("수학", "함수와 그래프", "**일차함수** y=ax+b의 그래프는 직선이며 a는 **기울기**, b는 **y절편**이다. **이차함수** y=ax²+bx+c의 그래프는 **포물선**으로 a>0이면 아래로 볼록하고, **꼭짓점**에서 최솟값(또는 최댓값)을 가진다."),
    ("수학", "확률과 통계", "**확률** = (사건이 일어나는 경우의 수) ÷ (모든 경우의 수)이며 항상 0 이상 1 이하이다. 자료의 중심을 나타내는 **대푯값**에는 **평균·중앙값·최빈값**이 있고, 흩어진 정도는 **분산·표준편차**로 나타낸다."),
    # 사회
    ("사회", "삼권분립", "국가 권력을 **입법부**(법 제정), **행정부**(법 집행), **사법부**(법 적용·심판)로 나누어 서로 **견제와 균형**을 이루게 함으로써 권력의 집중과 남용을 막는 원리이다."),
    ("사회", "시장과 수요·공급", "시장 가격은 **수요**와 **공급**으로 결정된다. 가격이 오르면 수요량은 줄고 공급량은 는다. 수요량과 공급량이 일치하는 지점의 가격이 **균형 가격**이다."),
    # 국어
    ("국어", "비유법", "표현하려는 **원관념**을 다른 대상인 **보조관념**에 빗대는 방법이다. '~같이/처럼'을 쓰면 **직유법**, 'A는 B이다' 형태로 직접 연결하면 **은유법**이다."),
    ("국어", "문학의 갈래", "문학은 크게 **시(운문)**, **소설(서사)**, **수필(교술)**, **희곡(극)**으로 나뉜다. 시는 함축과 **운율**, 소설은 **인물·사건·배경**과 서술자, 희곡은 무대를 전제로 한 **대사와 지시문**이 특징이다."),
    # 한국사
    ("한국사", "조선의 통치 체제", "조선은 **성리학(유교)**을 통치 이념으로 삼았다. 중앙에 최고 기구 **의정부**와 **6조**를 두고, 왕권을 견제하는 **삼사**(사헌부·사간원·홍문관)를 두었다. 지방은 **8도**로 나누어 관찰사를 파견했다."),
]
SEED_CARDS = [
    ("엽록체의 기능은?", "빛에너지를 흡수해 광합성이 일어나는 세포 소기관."),
    ("DNA·유전자·염색체의 관계는?", "염색체 속 물질이 DNA이고, DNA에서 형질을 결정하는 부분이 유전자."),
    ("사람 체세포의 염색체 수는?", "23쌍, 총 46개."),
    ("뉴턴 제2법칙(가속도 법칙) 식은?", "F = m × a (힘 = 질량 × 가속도)."),
    ("작용·반작용 법칙이란?", "힘을 주면 크기가 같고 방향이 반대인 힘을 동시에 되받는다."),
    ("에너지 보존 법칙이란?", "에너지는 형태가 바뀌어도 총량은 일정하게 유지된다."),
    ("pH 7은 무엇을 뜻하나?", "중성. 7보다 작으면 산성, 크면 염기성."),
    ("중화 반응이란?", "산과 염기가 만나 물과 염을 만드는 반응."),
    ("옴의 법칙(식)은?", "전류 I = 전압 V ÷ 저항 R."),
    ("칸트 의무론의 핵심은?", "결과가 아니라 동기·의무, 보편적 도덕 법칙을 따르는 것."),
    ("공리주의의 옳음의 기준은?", "최대 다수의 최대 행복(결과의 유용성)."),
    ("인권의 특징 세 가지는?", "천부성·보편성·불가침성."),
    ("인간 중심주의와 생태 중심주의의 차이는?", "전자는 자연을 인간의 수단으로, 후자는 생태계 전체의 가치를 존중."),
    ("이차방정식 근의 공식은?", "x = (−b ± √(b²−4ac)) / 2a."),
    ("판별식 D=b²−4ac의 의미는?", "D>0 두 실근, D=0 중근, D<0 실근 없음."),
    ("일차함수 y=ax+b에서 a·b는?", "a는 기울기, b는 y절편."),
    ("이차함수 그래프의 모양과 볼록 방향은?", "포물선. a>0이면 아래로 볼록, a<0이면 위로 볼록."),
    ("확률의 정의는?", "(사건이 일어나는 경우의 수)÷(모든 경우의 수), 0~1 사이."),
    ("대푯값 세 가지는?", "평균·중앙값·최빈값."),
    ("삼권분립의 목적은?", "권력의 집중·남용을 막고 국민의 자유와 권리를 보호 (견제와 균형)."),
    ("삼권분립의 세 기관은?", "입법부(국회)·행정부(정부)·사법부(법원)."),
    ("국민주권의 원리란?", "국가 권력의 정당성이 국민에게서 나온다는 민주주의의 기본 원리."),
    ("시장의 균형 가격이란?", "수요량과 공급량이 일치하는 지점의 가격."),
    ("은유법이란?", "'A는 B이다'처럼 원관념을 보조관념에 직접 빗대어 표현하는 방법."),
    ("직유법과 은유법의 차이는?", "직유는 '~같이/처럼'으로 빗대고, 은유는 'A는 B이다'로 직접 빗댄다."),
    ("문학의 4대 갈래는?", "시·소설·수필·희곡."),
    ("소설의 3요소는?", "인물·사건·배경."),
    ("조선의 통치 이념은?", "성리학(유교)."),
    ("조선의 삼사는?", "사헌부·사간원·홍문관 (언론·감찰로 왕권 견제)."),
]
SEED_SCHEDULE = [
    ("월", "09:00", "18:00", "정찰 임무 (외부 고정)", "recon"),
    ("수", "09:00", "11:00", "당무위 결계 (회의)", "seal"),
]


# ---- 저장소: 로컬 sqlite (기본) 또는 Turso(libSQL embedded replica, 배포 시 데이터 영속) ----
TURSO_URL = os.environ.get("TURSO_DATABASE_URL")
TURSO_TOKEN = os.environ.get("TURSO_AUTH_TOKEN", "")
FORCE_LIBSQL = os.environ.get("FORCE_LIBSQL_LOCAL")   # 테스트용: sync 없이 libsql 로컬 파일
REPLICA = os.path.join(DATA_DIR, "replica.db")

def _coerce(v):
    # 원격 Hrana가 TEXT를 bytes로 줄 때가 있어 str로 정규화(json 직렬화 가능하게)
    if isinstance(v, memoryview):
        v = v.tobytes()
    if isinstance(v, (bytes, bytearray)):
        return bytes(v).decode("utf-8", "replace")
    return v

class _Row(dict):
    """이름/정수 인덱스 모두 지원하는 dict-row (sqlite3.Row 호환)."""
    __slots__ = ("_v",)
    def __init__(self, cols, vals):
        vals = tuple(_coerce(v) for v in vals)
        super().__init__(zip(cols, vals)); self._v = vals
    def __getitem__(self, k):
        return self._v[k] if isinstance(k, int) else dict.__getitem__(self, k)

class _Result:
    def __init__(self, cur):
        self.lastrowid = getattr(cur, "lastrowid", None)
        cols = [d[0] for d in (cur.description or [])]
        try:
            raw = cur.fetchall()
        except Exception:
            raw = None
        self._rows = [_Row(cols, tuple(v)) for v in (raw or [])]
        self._i = 0
    def __iter__(self):
        return iter(self._rows)
    def fetchall(self):
        return self._rows
    def fetchone(self):
        if self._i < len(self._rows):
            r = self._rows[self._i]; self._i += 1; return r
        return None

class _Conn:
    """libsql 연결을 sqlite3 스타일로 감싸는 얇은 어댑터."""
    def __init__(self, raw, sync=False):
        self._raw = raw; self._sync = sync
    def execute(self, sql, params=()):
        return _Result(self._raw.execute(sql, tuple(params)))   # libsql 원격은 리스트 파라미터 거부 → 튜플 강제
    def executemany(self, sql, seq):
        self._raw.executemany(sql, seq); return self
    def executescript(self, script):
        for stmt in script.split(";"):        # 원격(Hrana)은 다중문장 미지원 → 개별 실행
            if stmt.strip():
                self._raw.execute(stmt)
        return self
    def commit(self):
        self._raw.commit()
        if self._sync:
            try:
                self._raw.sync()      # Turso로 변경분 푸시
            except Exception:
                pass
    def pull(self):
        if self._sync:
            try:
                self._raw.sync()      # Turso에서 최신 당겨오기(콜드스타트 복원)
            except Exception:
                pass
    def close(self):
        try:
            self._raw.close()
        except Exception:
            pass

def db():
    if TURSO_URL:
        import libsql_experimental as libsql
        raw = libsql.connect(TURSO_URL, auth_token=TURSO_TOKEN)   # 원격 직결(동기화 없음 → 'Failed sync' 없음)
        return _Conn(raw, sync=False)
    if FORCE_LIBSQL:
        import libsql_experimental as libsql
        return _Conn(libsql.connect(os.path.join(DATA_DIR, "libsql_local.db"), check_same_thread=False), sync=False)
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    # 커밋마다 fsync를 여러 번 하던 기본값(FULL/rollback journal) → 채점이 문항당 수백 ms 걸림
    try:
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=NORMAL")
    except Exception:
        pass
    return c


def _load_questions(c):
    """원본 black_bank.db의 기출을 현재 저장소로 적재하고 정답키 재생성(SQL 기반: 로컬/Turso 공통)."""
    src = sqlite3.connect(SEED_DB)
    has_ans = "answer_text" in [r[1] for r in src.execute("PRAGMA table_info(questions)")]
    cols = "id,year,exam_round,level,subject,raw_text" + (",answer_text" if has_ans else "")
    rows = [tuple(r) for r in src.execute(f"SELECT {cols} FROM questions")]
    src.close()
    c.execute("DELETE FROM questions")
    if rows:
        ph = ",".join("?" * len(rows[0]))
        c.executemany(f"INSERT INTO questions({cols}) VALUES({ph})", rows)
    rebuild_qkey(c)
    return len(rows)


def init_db():
    c = db()
    c.pull() if hasattr(c, "pull") else None      # Turso: 최신 데이터 당겨오기
    c.executescript("""
    CREATE TABLE IF NOT EXISTS questions(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        year TEXT, exam_round TEXT, level TEXT, subject TEXT, raw_text TEXT, answer_text TEXT);
    CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
    CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY, created INTEGER);
    CREATE TABLE IF NOT EXISTS app_state(id INTEGER PRIMARY KEY CHECK(id=1), json TEXT);
    CREATE TABLE IF NOT EXISTS units(id INTEGER PRIMARY KEY AUTOINCREMENT, subject TEXT, title TEXT, body TEXT, ord INTEGER);
    CREATE TABLE IF NOT EXISTS cards(id INTEGER PRIMARY KEY AUTOINCREMENT, q TEXT, a TEXT, ord INTEGER);
    CREATE TABLE IF NOT EXISTS schedule(id INTEGER PRIMARY KEY AUTOINCREMENT, day TEXT, t_start TEXT, t_end TEXT, name TEXT, kind TEXT, ord INTEGER);
    CREATE TABLE IF NOT EXISTS qkey(question_id INTEGER, qnum INTEGER, answer INTEGER, PRIMARY KEY(question_id,qnum));
    CREATE TABLE IF NOT EXISTS attempts(id INTEGER PRIMARY KEY AUTOINCREMENT, question_id INTEGER, subject TEXT, year TEXT, exam_round TEXT, qnum INTEGER, chosen INTEGER, answer INTEGER, correct INTEGER, ts INTEGER);
    CREATE TABLE IF NOT EXISTS study_log(date TEXT PRIMARY KEY, math REAL DEFAULT 0, sci REAL DEFAULT 0, kor REAL DEFAULT 0, nc2 REAL DEFAULT 0);
    CREATE TABLE IF NOT EXISTS pdf_files(name TEXT PRIMARY KEY, data TEXT);
    CREATE TABLE IF NOT EXISTS exam_results(id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT, year TEXT, exam_round TEXT, level TEXT, json TEXT, avg REAL, ts INTEGER);
    CREATE TABLE IF NOT EXISTS focus_log(id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT, start_ts INTEGER, end_ts INTEGER,
        focused_sec INTEGER, distract_cnt INTEGER, checks INTEGER, transcript TEXT);
    """)
    if "answer_text" not in [r["name"] for r in c.execute("PRAGMA table_info(questions)")]:
        c.execute("ALTER TABLE questions ADD COLUMN answer_text TEXT")
    if "source" not in [r["name"] for r in c.execute("PRAGMA table_info(attempts)")]:
        c.execute("ALTER TABLE attempts ADD COLUMN source TEXT DEFAULT 'quiz'")   # quiz(문제은행·복습) / pdf(오답노트 스캔 채점)
    if "tag" not in [r["name"] for r in c.execute("PRAGMA table_info(attempts)")]:
        c.execute("ALTER TABLE attempts ADD COLUMN tag TEXT")                     # 오답 원인: concept/mistake/confuse/time/guess
    if not c.execute("SELECT 1 FROM questions LIMIT 1").fetchone():
        _load_questions(c)                        # 첫 실행: 기출 42문항 + 정답키 적재
    # 지문/카드 콘텐츠: 버전이 바뀌면 새 항목만 추가(중복 제목/질문은 건너뜀 → 사용자 편집 보존)
    cur_ver = c.execute("SELECT v FROM meta WHERE k='content_version'").fetchone()
    if (cur_ver["v"] if cur_ver else "0") != CONTENT_VERSION:
        have_u = {r["title"] for r in c.execute("SELECT title FROM units")}
        ordu = (c.execute("SELECT COALESCE(MAX(ord),-1) o FROM units").fetchone()["o"])
        for s, t, b in SEED_UNITS:
            if t not in have_u:
                ordu += 1; c.execute("INSERT INTO units(subject,title,body,ord) VALUES(?,?,?,?)", (s, t, b, ordu))
        have_c = {r["q"] for r in c.execute("SELECT q FROM cards")}
        ordc = (c.execute("SELECT COALESCE(MAX(ord),-1) o FROM cards").fetchone()["o"])
        for q, a in SEED_CARDS:
            if q not in have_c:
                ordc += 1; c.execute("INSERT INTO cards(q,a,ord) VALUES(?,?,?)", (q, a, ordc))
        c.execute("INSERT OR REPLACE INTO meta(k,v) VALUES('content_version',?)", (CONTENT_VERSION,))
    if not c.execute("SELECT 1 FROM schedule LIMIT 1").fetchone():
        for i, row in enumerate(SEED_SCHEDULE):
            c.execute("INSERT INTO schedule(day,t_start,t_end,name,kind,ord) VALUES(?,?,?,?,?,?)", (*row, i))
    c.commit()
    materialize_pdfs(c)      # 업로드된 PDF 디스크 복원
    c.close()


# ---- 정답표 파서: '○○ 정답표' 제목 앞뒤 '런'을 그 과목 정답으로 배정(과목/문항수 무관) ----
_CDIGIT = {c: i + 1 for i, c in enumerate("①②③④⑤")}
KEY_SUBJECTS = ("국어", "수학", "영어", "사회", "과학", "한국사", "도덕")

def parse_key(answer_text, subject):
    if not answer_text:
        return {}
    ev = []
    for m in re.finditer(r'([가-힣]{2,4})\s*정답표', answer_text):
        ev.append((m.start(), 'T', m.group(1)))
    for m in re.finditer(r'문항\s*번호', answer_text):
        ev.append((m.start(), 'H', None))
    for m in re.finditer(r'(\d+)\s*([①②③④⑤])', answer_text):
        n = int(m.group(1))
        if 1 <= n <= 25:
            ev.append((m.start(), 'P', (n, _CDIGIT[m.group(2)])))
    ev.sort()
    seq, run = [], None       # 쌍 연속=런, 헤더/제목이 런 경계
    for off, t, d in ev:
        if t == 'P':
            if run is None:
                run = []; seq.append(('R', run))
            run.append(d)
        else:
            run = None
            if t == 'T':
                seq.append(('T', d))
    key = {}
    for i, (t, d) in enumerate(seq):
        if t == 'T' and d == subject:
            for j in (i - 1, i + 1):   # 제목 바로 앞(before-group)·뒤(after-group) 런
                if 0 <= j < len(seq) and seq[j][0] == 'R':
                    for n, a in seq[j][1]:
                        key.setdefault(n, a)
    return dict(sorted(key.items()))


# ---- 기출 페이지(raw_text)를 개별 문항으로 분해: 발문·선지·지문 ----
_NOISE = re.compile(r'^\s*(제\s*\d+\s*교시|고졸|중졸|\d{4}년도\s*제\d+회.*검정고시.*|(고졸|중졸)?\s*\([^)]+\)\s*\d+\s*[-－]\s*\d+)\s*$')
_QH = re.compile(r'^\s*(\d{1,2})\.\s?(.*)')
_PH = re.compile(r'^\s*\[(\d{1,2})\s*[~～]\s*(\d{1,2})\]\s*(.*)')
_CIRC = "①②③④⑤"

def _pua_ratio(s):
    return sum(1 for c in s if '' <= c <= '') / max(1, len(s))

# 독자가 볼 수 없는 대상을 가리키는 지시어로 시작 → 그림을 못 보면 아무 정보가 없는 문장
_DEIXIS = re.compile(r'^\s*(해당|위의|위\s|본\s|이\s|그\s|이것|그것|여기서)')
_STRIP = re.compile(r'[\s·:=＝()\[\]"\'“”‘’.,]')


def _vacuous(text, concept):
    """'해당 화석이 번성한 시기는 중생대임' 같은 동어반복(정보 0) 문장 걸러내기."""
    t = (text or "").strip()
    if len(t) < 4:
        return True
    # ① 지시어로 시작하면 무엇을 가리키는지 알 수 없음 → 카드 단독으로는 쓸모 없음
    if _DEIXIS.match(t):
        return True
    body = _STRIP.sub('', t)
    cc = _STRIP.sub('', concept or "")
    # ② 개념 이름을 거의 그대로 되풀이
    if cc and len(cc) >= 3 and (body == cc or (len(body) <= len(cc) + 6 and cc in body)):
        return True
    # ③ 'A = B' 인데 B가 A나 개념명에 이미 들어있음
    m = re.match(r'^(.{2,24})\s*[=＝]\s*(.{1,24})$', t)
    if m:
        left, right = _STRIP.sub('', m.group(1)), _STRIP.sub('', m.group(2))
        if right and (right in left or (cc and right in cc) or (cc and left in cc)):
            return True
    return False


def _split_circ(ln):
    """한 줄을 '첫 선지기호 앞'(발문)과 '기호부터'(선지)로 분리. 기호가 없으면 (원문, '')."""
    pos = [ln.find(ch) for ch in _CIRC if ch in ln]
    if not pos:
        return ln, ''
    cut = min(pos)
    return ln[:cut].strip(), ln[cut:]


def parse_items(raw):
    lines = [ln.rstrip() for ln in (raw or "").split('\n') if not _NOISE.match(ln.strip())]
    items, passages, i, expected, cur, n = [], [], 0, 1, None, 0
    n = len(lines)
    def flush():
        nonlocal cur
        if cur:
            items.append(cur); cur = None
    while i < n:
        ln = lines[i]; mp = _PH.match(ln); mq = _QH.match(ln)
        if mp:
            flush(); a, b = int(mp.group(1)), int(mp.group(2))
            buf = [mp.group(3)] if mp.group(3) else []; i += 1
            # 지문 안의 개요 항목('1. 2. 3.')에서 끊기지 않게, 다음 '실제 문항'(expected번)이나 다음 지문에서만 종료
            while i < n:
                m2 = _QH.match(lines[i])
                if (m2 and int(m2.group(1)) == expected) or _PH.match(lines[i]):
                    break
                buf.append(lines[i]); i += 1
            passages.append((a, b, '\n'.join(buf).strip())); continue
        if mq and int(mq.group(1)) == expected and expected <= 30:
            flush()
            # 2단 조판 PDF에선 '17. 발문 ①선지'가 한 줄로 추출되기도 함 → ① 앞뒤를 갈라 담는다
            pre, ch = _split_circ(mq.group(2) or '')
            cur = {'qnum': expected, 's': [pre] if pre else [], 'c': [ch] if ch else []}
            expected += 1; i += 1; continue
        if cur is not None:
            if cur['c']:
                cur['c'].append(ln)                      # 이미 선지 구간 → 여러 줄 선지 이어붙이기
            else:
                pre, ch = _split_circ(ln)
                if ch:
                    if pre:
                        cur['s'].append(pre)             # ① 앞의 발문 꼬리가 버려지지 않도록
                    cur['c'].append(ch)
                else:
                    cur['s'].append(ln)
        i += 1
    flush()
    def split_ch(t):
        """선지 분리. 지문 속 조항 기호('헌법 제37조 ②')가 선지로 잡히는 걸 걸러내고,
        걸러낸 텍스트는 버리지 않고 발문으로 돌려준다."""
        p = re.split(r'([①②③④⑤])', t)
        out = []
        for k in range(1, len(p), 2):
            out.append({"n": _CIRC.index(p[k]) + 1, "text": (p[k + 1] if k + 1 < len(p) else '').strip(),
                        "mark": p[k]})
        # 1부터 오름차순으로 4개 이상 이어지는 '마지막' 구간만 진짜 선지로 본다
        best = None
        for i, ch in enumerate(out):
            if ch["n"] != 1:
                continue
            run = [ch]
            for nxt in out[i + 1:]:
                if nxt["n"] == run[-1]["n"] + 1:
                    run.append(nxt)
                else:
                    break
            if len(run) >= 4:
                best = (i, len(run))
        if best is None:
            for c in out:
                c.pop("mark", None)
            return out, ''
        i, ln_ = best
        dropped = ' '.join((c["mark"] + c["text"]).strip() for c in out[:i]).strip()
        keep = out[i:i + ln_]
        for c in keep:
            c.pop("mark", None)
        return keep, dropped
    out = []
    for it in items:
        stem = '\n'.join(it['s']).strip()
        choices, dropped = split_ch(' '.join(it['c']))
        if dropped:
            stem = (stem + '\n' + dropped).strip()      # 조항 본문 등이 유실되지 않도록
        if not choices:
            choices = [{"n": k, "text": ""} for k in range(1, 5)]
        passage = next((t for a, b, t in passages if a <= it['qnum'] <= b), '')
        blob = stem + ' '.join(x["text"] for x in choices)
        # 선지 텍스트가 거의 비어 있으면 표/그림에 의존하는 문항 → 원본 이미지로 보여줘야 풀 수 있음
        layout_only = sum(1 for x in choices if x["text"]) <= 1
        out.append({"qnum": it['qnum'], "stem": stem, "choices": choices,
                    "passage": passage, "image": _pua_ratio(blob) > 0.15 or layout_only})
    return out


def rebuild_qkey(c):
    c.execute("DELETE FROM qkey")
    rows = c.execute("SELECT id,subject,answer_text FROM questions").fetchall()
    keys = [(r["id"], n, a) for r in rows for n, a in parse_key(r["answer_text"], r["subject"]).items()]
    if keys:
        c.executemany("INSERT OR REPLACE INTO qkey(question_id,qnum,answer) VALUES(?,?,?)", keys)   # 배치 삽입(원격 왕복 최소화)


# ---- 이미지 문항: 원본 PDF에서 문항 단위로 잘라 이미지 제공 ----
_CR_CACHE = {}          # 개념 총정리 결과 캐시 (오답 상태가 그대로면 재호출 안 함)
_YT_CACHE = {}
_YTD_CACHE = {}         # 영상 설명란 자료 링크 캐시 {videoId: (ts, files)}          # 유튜브 강의 영상 조회 캐시 {과목|연도|회차: (ts, video)}
_qstarts_cache = {}
_pbreaks_cache = {}
_qfigs_cache = {}

def _load_pdfmeta():
    """미리 계산한 PDF 문항좌표·그림 정보를 캐시에 로드 → 런타임 PDF 스캔 제거(로딩 대폭 단축)."""
    path = os.path.join(HERE, "pdfmeta.json")
    if not os.path.exists(path):
        return
    try:
        meta = json.load(open(path, encoding="utf-8"))
    except Exception:
        return
    for fn, d in meta.items():
        p = os.path.join(PDF_DIR, fn)
        _qstarts_cache[p] = {int(k): tuple(v) for k, v in d.get("starts", {}).items()}
        _pbreaks_cache[p] = [tuple(x) for x in d.get("pbreaks", [])]
        _qfigs_cache[p] = set(d.get("figs", []))
_load_pdfmeta()

def pdf_path(row):
    return os.path.join(PDF_DIR, f"{row['year']}_{row['exam_round']}_{row['level']}_{row['subject']}_문제.pdf")

def _qstarts(path):
    if path in _qstarts_cache:
        return _qstarts_cache[path]
    import fitz
    doc = fitz.open(path)
    # '16.' 처럼 번호만 있고 발문이 다음 줄로 넘어간 경우도 문항 시작으로 인정
    qh = re.compile(r'^\s*(\d{1,2})\.(\s|$)')
    ph = re.compile(r'^\s*\[\d{1,2}\s*[~～]')      # 지문 헤더 [a~b]
    starts = {}; pbreaks = []
    for pno in range(len(doc)):
        pg = doc[pno]; W = pg.rect.width
        for b in pg.get_text("dict")["blocks"]:
            for l in b.get("lines", []):
                txt = "".join(s["text"] for s in l["spans"]).strip()
                col = 0 if l["bbox"][0] < W / 2 else 1
                m = qh.match(txt)
                if m:
                    num = int(m.group(1))
                    if num not in starts:
                        starts[num] = (pno, col, l["bbox"][1])
                elif ph.match(txt):
                    pbreaks.append((pno, col, l["bbox"][1]))
    doc.close()
    _qstarts_cache[path] = starts; _pbreaks_cache[path] = pbreaks
    return starts

def _qbox(pg, starts, qnum, pbreaks=None):
    import fitz
    pno, col, y0 = starts[qnum]
    W, H = pg.rect.width, pg.rect.height
    x0, x1 = (20, W / 2 + 4) if col == 0 else (W / 2 - 4, W - 18)
    cand = [starts[m][2] for m in starts if starts[m][0] == pno and starts[m][1] == col and starts[m][2] > y0]
    for p, c, y in (pbreaks or []):     # 다음 지문 헤더에서도 크롭 종료(지문이 딸려오지 않게)
        if p == pno and c == col and y > y0:
            cand.append(y)
    return fitz.Rect(x0, y0, x1, min(cand) if cand else H - 36)

def _spill_bound(doc, pno, col, W):
    """다음 페이지 같은 칼럼에서 첫 경계(문항/지문헤더) y좌표. 없으면 None(페이지 끝까지 이어짐)."""
    if pno + 1 >= len(doc):
        return None
    pg2 = doc[pno + 1]
    best = None
    for b in pg2.get_text("dict")["blocks"]:
        for l in b.get("lines", []):
            txt = "".join(s["text"] for s in l["spans"]).strip()
            if not txt:
                continue
            c2 = 0 if l["bbox"][0] < W / 2 else 1
            if c2 != col:
                continue
            if _QH.match(txt) or _PH.match(txt):
                y = l["bbox"][1]
                if best is None or y < best:
                    best = y
    return best

def _qbox2(doc, starts, pbreaks, qnum):
    """문항 크롭 영역. 칼럼 끝(다음 경계 없음)이면 다음 페이지 상단 이어지는 부분도 함께 반환
    (2단 레이아웃에서 문항 내용이 페이지 경계를 넘어가면 그림/표가 잘려나가는 문제 방지)."""
    import fitz
    pno, col, y0 = starts[qnum]
    pg = doc[pno]; W, H = pg.rect.width, pg.rect.height
    x0, x1 = (20, W / 2 + 4) if col == 0 else (W / 2 - 4, W - 18)
    cand = [starts[m][2] for m in starts if starts[m][0] == pno and starts[m][1] == col and starts[m][2] > y0]
    for p, c, y in (pbreaks or []):
        if p == pno and c == col and y > y0:
            cand.append(y)
    if cand:
        return fitz.Rect(x0, y0, x1, min(cand)), None, None
    rect1 = fitz.Rect(x0, y0, x1, H - 36)
    has_choice = False   # 현재 페이지에 이미 선지(①~⑤)가 나왔으면 문항이 완결된 것 → 스필 볼 필요 없음
    for b in pg.get_text("dict")["blocks"]:
        for l in b.get("lines", []):
            txt = "".join(s["text"] for s in l["spans"])
            c2 = 0 if l["bbox"][0] < W / 2 else 1
            if c2 == col and y0 < l["bbox"][1] < H - 36 and re.search(r'[①②③④⑤]', txt):
                has_choice = True; break
        if has_choice:
            break
    if has_choice or pno + 1 >= len(doc):
        return rect1, None, None
    pg2 = doc[pno + 1]
    foot2 = pg2.rect.height - 62               # 다음 페이지도 푸터(로고) 영역은 제외
    y_end2 = _spill_bound(doc, pno, col, W)
    if y_end2 is None or y_end2 > foot2:
        y_end2 = foot2
    if y_end2 <= 60:
        return rect1, None, None
    has_text = False                            # 실제 이어지는 텍스트가 있을 때만 스필로 취급(오탐 방지)
    for b in pg2.get_text("dict")["blocks"]:
        for l in b.get("lines", []):
            txt = "".join(s["text"] for s in l["spans"]).strip()
            if not txt:
                continue
            c2 = 0 if l["bbox"][0] < W / 2 else 1
            if c2 == col and 55 < l["bbox"][1] < y_end2:
                has_text = True; break
        if has_text:
            break
    if not has_text:
        return rect1, None, None
    return rect1, fitz.Rect(x0, 55, x1, y_end2), pno + 1

def _scan_fig(pg, rect):
    foot = pg.rect.height - 62      # 하단 푸터(평가원 로고·교시 표기) 제외
    head = 55                       # 상단 헤더 제외
    def ok(r):
        return r.intersects(rect) and r.y0 > head and r.y1 < foot
    for b in pg.get_text("dict")["blocks"]:
        if b.get("type") == 1:
            r = fitz.Rect(b["bbox"])
            if r.width > 28 and r.height > 22 and ok(r):
                return True
    for d in pg.get_drawings():
        r = d["rect"]
        if r.width > 60 and r.height > 40 and ok(r):
            return True
    return False

def qfigs(path):
    """그림(래스터 이미지/큰 벡터 드로잉)이 포함된 문항 번호 집합. 페이지 경계를 넘어가는 내용도 검사."""
    if path in _qfigs_cache:
        return _qfigs_cache[path]
    import fitz
    starts = _qstarts(path); pbreaks = _pbreaks_cache.get(path)
    doc = fitz.open(path)
    figs = set()
    for num in starts:
        rect1, rect2, pno2 = _qbox2(doc, starts, pbreaks, num)
        found = _scan_fig(doc[starts[num][0]], rect1)
        if not found and rect2 is not None:
            found = _scan_fig(doc[pno2], rect2)
        if found:
            figs.add(num)
    doc.close()
    _qfigs_cache[path] = figs
    return figs

def extract_pdf_text(path):
    """PDF를 블록 기반 2단 분리로 텍스트 추출(기존 크롤러와 동일 방식 → 파서 호환)."""
    import fitz
    doc = fitz.open(path)
    out = []
    for pg in doc:
        W = pg.rect.width
        left, right = [], []
        for b in pg.get_text("blocks"):
            if len(b) >= 7 and b[6] == 0:      # 텍스트 블록만
                (left if (b[0] + b[2]) / 2 < W / 2 else right).append(b)
        left.sort(key=lambda b: b[1]); right.sort(key=lambda b: b[1])
        for b in left + right:
            out.append(b[4].rstrip())
    doc.close()
    return "\n".join(out)

def materialize_pdfs(c):
    """Turso에 저장된 업로드 PDF를 디스크(pdf/)로 복원(콜드스타트/재배포 후에도 이미지 렌더 가능)."""
    try:
        rows = c.execute("SELECT name,data FROM pdf_files").fetchall()
    except Exception:
        return
    os.makedirs(PDF_DIR, exist_ok=True)
    for r in rows:
        dst = os.path.join(PDF_DIR, r["name"])
        if not os.path.exists(dst) and r["data"]:
            try:
                with open(dst, "wb") as f:
                    f.write(base64.b64decode(r["data"]))
            except Exception:
                pass


def _stitch_vert(pix1, pix2, gap=10):
    """두 픽스맵을 세로로 이어붙인 하나의 픽스맵으로 합성(페이지 경계 넘어가는 문항용)."""
    import fitz
    W = max(pix1.width, pix2.width); H = pix1.height + gap + pix2.height
    d = fitz.open(); page = d.new_page(width=W, height=H)
    page.insert_image(fitz.Rect(0, 0, pix1.width, pix1.height), pixmap=pix1)
    page.insert_image(fitz.Rect(0, pix1.height + gap, pix2.width, pix1.height + gap + pix2.height), pixmap=pix2)
    out = page.get_pixmap()
    d.close()
    return out

def render_qimage(row, qnum):
    path = pdf_path(row)
    if not os.path.exists(path):
        return None
    os.makedirs(PDF_CACHE, exist_ok=True)
    out = os.path.join(PDF_CACHE, f"{row['id']}_{qnum}.png")
    if os.path.exists(out):
        return out
    starts = _qstarts(path)
    if qnum not in starts:
        # 미리 계산해둔 pdfmeta 캐시가 오래돼 이 문항 좌표가 빠져 있을 수 있음 → 한 번만 다시 스캔
        _qstarts_cache.pop(path, None)
        _pbreaks_cache.pop(path, None)
        starts = _qstarts(path)
        if qnum not in starts:
            return None
    import fitz
    doc = fitz.open(path)
    pbreaks = _pbreaks_cache.get(path)
    rect1, rect2, pno2 = _qbox2(doc, starts, pbreaks, qnum)
    rect1 = fitz.Rect(rect1.x0, rect1.y0 - 6, rect1.x1, rect1.y1 - 6)
    M = fitz.Matrix(2.2, 2.2)   # 2.6→2.2: 렌더·다운로드 속도↑, 모바일에도 충분
    pix1 = doc[starts[qnum][0]].get_pixmap(matrix=M, clip=rect1)
    if rect2 is not None:       # 문항 내용이 다음 페이지로 이어짐 → 이어붙여서 그림·표가 잘리지 않게
        pix2 = doc[pno2].get_pixmap(matrix=M, clip=rect2)
        _stitch_vert(pix1, pix2).save(out)
    else:
        pix1.save(out)
    doc.close()
    return out


# ---- 보안: 봉인 해시(서버 저장) ----
def set_seal(pin):
    salt = secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", pin.encode(), salt.encode(), 120_000).hex()
    c = db()
    c.execute("INSERT OR REPLACE INTO meta(k,v) VALUES('seal_salt',?)", (salt,))
    c.execute("INSERT OR REPLACE INTO meta(k,v) VALUES('seal_hash',?)", (h,))
    c.commit(); c.close()

def verify_seal(pin):
    c = db()
    r = dict((row["k"], row["v"]) for row in c.execute("SELECT k,v FROM meta WHERE k IN('seal_salt','seal_hash')"))
    c.close()
    if not r.get("seal_hash"):
        return False
    h = hashlib.pbkdf2_hmac("sha256", pin.encode(), r["seal_salt"].encode(), 120_000).hex()
    return secrets.compare_digest(h, r["seal_hash"])

def has_seal():
    c = db(); r = c.execute("SELECT 1 FROM meta WHERE k='seal_hash'").fetchone(); c.close()
    return bool(r)

def new_session():
    tok = secrets.token_urlsafe(24)
    c = db(); c.execute("INSERT INTO sessions(token,created) VALUES(?,?)", (tok, int(time.time()))); c.commit(); c.close()
    return tok

def session_valid(tok):
    if not tok:
        return False
    c = db(); r = c.execute("SELECT created FROM sessions WHERE token=?", (tok,)).fetchone(); c.close()
    return bool(r) and (time.time() - r["created"]) < SESSION_TTL

def drop_session(tok):
    if tok:
        c = db(); c.execute("DELETE FROM sessions WHERE token=?", (tok,)); c.commit(); c.close()


# ---- AI: Google AI Studio (Gemini) ----
def _read_key(env_names, fname):
    for nm in env_names:
        v = os.environ.get(nm)
        if v:
            return v.strip()
    path = os.path.join(HERE, fname)
    if os.path.exists(path):
        with open(path) as f:
            return f.read().strip()
    return None

def gemini_key():
    return _read_key(["GEMINI_API_KEY", "GOOGLE_API_KEY"], "gemini_key.txt")

def ai_provider():
    return "gemini" if gemini_key() else None

def _gemini(system, messages, max_tokens, key):
    model = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
    contents = [{"role": ("model" if m["role"] == "assistant" else "user"),
                 "parts": [{"text": m["content"]}]} for m in messages]
    body = json.dumps({
        "system_instruction": {"parts": [{"text": system}]},
        "contents": contents,
        # gemini-flash-latest는 thinking 모델 → 사고에 토큰 소모. 사고 억제(budget) + 답변 여유(+3500)로 긴 답변도 안 잘리게.
        "generationConfig": {"maxOutputTokens": int(max_tokens) + 3500, "thinkingConfig": {"thinkingBudget": 512}},
    }).encode()
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={urllib.parse.quote(key)}"
    # 분당 요청 제한(429)에 걸리면 잠시 쉬었다 재시도 — 시험 당일 한 번의 실패로 못 쓰는 일이 없도록
    last = None
    for wait in (0, 6, 14):
        if wait:
            time.sleep(wait)
        try:
            req = urllib.request.Request(url, data=body, headers={"content-type": "application/json"})
            with urllib.request.urlopen(req, timeout=60) as resp:
                out = json.load(resp)
            cands = out.get("candidates", [])
            if not cands:
                return ""
            return "".join(p.get("text", "") for p in cands[0].get("content", {}).get("parts", []))
        except urllib.error.HTTPError as e:
            last = e
            if e.code not in (429, 500, 502, 503, 504):
                raise
        except Exception as e:
            last = e
    raise last

def ai_complete(system, messages, max_tokens=700):
    gk = gemini_key()
    return _gemini(system, messages, max_tokens, gk) if gk else None

LIVE_MODEL = os.environ.get("GEMINI_LIVE_MODEL", "gemini-3.1-flash-live-preview")
# Live API 프리빌트 음성 29종(2026-08 실측으로 전부 연결 확인). 앞 8개가 대표 음색.
LIVE_VOICES = ["Puck", "Charon", "Kore", "Fenrir", "Aoede", "Leda", "Orus", "Zephyr",
               "Autonoe", "Enceladus", "Iapetus", "Umbriel", "Algieba", "Despina", "Erinome",
               "Algenib", "Rasalgethi", "Laomedeia", "Achernar", "Alnilam", "Schedar", "Gacrux",
               "Pulcherrima", "Achird", "Zubenelgenubi", "Vindemiatrix", "Sadachbia", "Sadaltager", "Sulafat"]

def live_token():
    """Live API용 임시 토큰 발급. API 키는 서버에만 두고 브라우저엔 30분짜리 토큰만 내보낸다."""
    key = gemini_key()
    if not key:
        return None
    now = datetime.datetime.now(datetime.timezone.utc)
    body = json.dumps({
        "uses": 1,
        "expireTime": (now + datetime.timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "newSessionExpireTime": (now + datetime.timedelta(minutes=2)).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }).encode()
    req = urllib.request.Request("https://generativelanguage.googleapis.com/v1beta/auth_tokens",
                                 data=body, headers={"x-goog-api-key": key, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode())["name"]

def ai_vision(prompt, images_png, max_tokens=2000, as_json=True):
    """이미지(PNG 바이트 리스트) + 프롬프트 → Gemini 비전. JSON 문자열 반환."""
    key = gemini_key()
    if not key:
        return None
    model = os.environ.get("GEMINI_MODEL", "gemini-flash-latest")
    parts = [{"text": prompt}]
    for img in images_png:
        mime = "image/jpeg" if img[:2] == b"\xff\xd8" else "image/png"   # JPEG(웹캠 캡처)도 지원
        parts.append({"inline_data": {"mime_type": mime, "data": base64.b64encode(img).decode()}})
    body = json.dumps({
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": dict({"maxOutputTokens": int(max_tokens) + 3500, "thinkingConfig": {"thinkingBudget": 512}},
                                 **({"responseMimeType": "application/json"} if as_json else {})),
    }).encode()
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={urllib.parse.quote(key)}"
    req = urllib.request.Request(url, data=body, headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        out = json.load(resp)
    cands = out.get("candidates", [])
    if not cands:
        return ""
    return "".join(p.get("text", "") for p in cands[0].get("content", {}).get("parts", []))


class H(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _tok(self):
        ck = cookies.SimpleCookie(self.headers.get("Cookie", ""))
        return ck["sid"].value if "sid" in ck else None

    def _authed(self):
        return session_valid(self._tok())

    def _json(self, obj, code=200, cookie=None):
        data = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(data)

    def _body(self):
        n = int(self.headers.get("Content-Length", 0) or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            return {}

    def _guard(self):
        if not self._authed():
            self._json({"error": "unauthorized"}, 401)
            return True
        return False

    def _cookie(self, tok):
        return f"sid={tok}; Path=/; Max-Age={SESSION_TTL}; HttpOnly; SameSite=Strict"

    def _file(self, path, ctype):
        try:
            with open(path, "rb") as f:
                data = f.read()
        except FileNotFoundError:
            return self._json({"error": "missing_file"}, 404)
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        if ctype.startswith("text/html"):
            self.send_header("Cache-Control", "no-cache, must-revalidate")  # 배포 즉시 반영
        elif ctype.startswith("image/"):
            self.send_header("Cache-Control", "public, max-age=31536000, immutable")  # 문항 이미지는 불변 → 브라우저 캐시(재요청 X)
        self.end_headers()
        self.wfile.write(data)

    # -- GET --
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        p = parsed.path
        q = {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items()}

        if p in ("/", "/index.html"):
            return self._file(HTML, "text/html; charset=utf-8")
        if p == "/api/session":
            return self._json({"authed": self._authed(), "setup": not has_seal(), "isAdmin": self._authed(), "ai": ai_provider()})
        # 봉인(PIN) 밖으로 새면 안 되는 학습 콘텐츠 — 기출 본문·정답키·문항 이미지 등
        if p.startswith("/api/") and p != "/api/session" and self._guard():
            return
        if p == "/api/state":
            if self._guard():
                return
            c = db(); r = c.execute("SELECT json FROM app_state WHERE id=1").fetchone(); c.close()
            return self._json({"state": json.loads(r["json"]) if r else None})
        if p == "/api/units":
            c = db(); rows = [dict(x) for x in c.execute("SELECT * FROM units ORDER BY ord,id")]; c.close()
            return self._json({"units": rows})
        if p == "/api/cards":
            c = db(); rows = [dict(x) for x in c.execute("SELECT * FROM cards ORDER BY ord,id")]; c.close()
            return self._json({"cards": rows})
        if p == "/api/schedule":
            c = db(); rows = [dict(x) for x in c.execute("SELECT * FROM schedule ORDER BY ord,id")]; c.close()
            return self._json({"schedule": rows})
        if p == "/api/questions/facets":
            c = db()
            def dl(col):
                return [r[0] for r in c.execute(f"SELECT DISTINCT {col} FROM questions WHERE {col} IS NOT NULL AND {col}<>'' ORDER BY {col}")]
            out = {"subject": dl("subject"), "year": dl("year"), "exam_round": dl("exam_round"), "level": dl("level")}
            c.close()
            return self._json(out)
        if p == "/api/questions":
            return self._questions(q)
        if p == "/api/qkey":
            c = db(); rows = c.execute("SELECT qnum,answer FROM qkey WHERE question_id=? ORDER BY qnum", (q.get("question_id"),)).fetchall(); c.close()
            return self._json({"key": {str(r["qnum"]): r["answer"] for r in rows}})
        if p == "/api/quiz":
            return self._quiz(q.get("question_id"))
        if p == "/api/qimage":
            return self._qimage(q.get("question_id"), q.get("qnum"))
        if p == "/api/wrong":
            if self._guard():
                return
            return self._wrong()
        if p == "/api/wrong/analyze":
            if self._guard():
                return
            return self._wrong_analyze()
        if p == "/api/diagnose":
            if self._guard():
                return
            return self._diagnose(q)
        if p == "/api/exammeta":
            return self._json(self._subject_exam_meta(q.get("subject", "도덕")))
        if p == "/api/conceptquiz":
            if self._guard():
                return
            kw = [k for k in (q.get("kw", "") or "").split(",") if k]
            return self._concept_quiz(q.get("subject", ""), kw or None)
        if p == "/api/weakspots":
            if self._guard():
                return
            return self._weakspots()
        if p == "/api/conceptreview":
            if self._guard():
                return
            return self._concept_review(force=q.get("force") == "1", raw=q.get("raw") == "1")
        if p == "/api/yt":
            if self._guard():
                return
            return self._yt_lookup(q)
        if p == "/api/ytdesc":
            if self._guard():
                return
            return self._yt_desc(q)
        if p == "/api/sheet":
            if self._guard():
                return
            return self._final_sheet()
        if p == "/api/replay":
            if self._guard():
                return
            return self._replay()
        if p == "/api/recap":
            if self._guard():
                return
            return self._recap()
        if p == "/api/report":
            if self._guard():
                return
            return self._full_report()
        if p == "/api/today":
            if self._guard():
                return
            return self._today(q.get("date", ""))
        if p == "/api/avatar":
            c = db(); r = c.execute("SELECT v FROM meta WHERE k='avatar_img'").fetchone(); c.close()
            return self._json({"data": r["v"] if r else ""})
        if p == "/api/focus/sessions":
            if self._guard():
                return
            c = db(); rows = [dict(x) for x in c.execute(
                "SELECT id,date,start_ts,end_ts,focused_sec,distract_cnt,checks FROM focus_log ORDER BY end_ts DESC LIMIT 30")]
            tot = c.execute("SELECT SUM(focused_sec) s, SUM(distract_cnt) d FROM focus_log").fetchone(); c.close()
            return self._json({"sessions": rows, "total_sec": tot["s"] or 0, "total_distract": tot["d"] or 0})
        if p == "/api/recommend":
            if self._guard():
                return
            return self._recommend()
        if p == "/api/similar":
            if self._guard():
                return
            return self._similar(q)
        if p == "/api/study":
            if self._guard():
                return
            return self._study_get(q.get("today", ""), q.get("weekStart", ""), q.get("periodStart", ""))
        if p == "/dodeok.json":
            return self._file(os.path.join(HERE, "dodeok.json"), "application/json; charset=utf-8")
        if p == "/manifest.json":
            return self._file(os.path.join(HERE, "manifest.json"), "application/manifest+json; charset=utf-8")
        if p in ("/icon-192.png", "/icon-512.png"):
            return self._file(os.path.join(HERE, p.lstrip("/")), "image/png")
        if p == "/api/examkeys":
            return self._examkeys(q)
        if p == "/api/examresults":
            if self._guard():
                return
            c = db(); rows = [dict(x) for x in c.execute("SELECT id,date,year,exam_round,level,json,avg FROM exam_results ORDER BY ts DESC")]; c.close()
            for r in rows:
                r["detail"] = json.loads(r.pop("json") or "{}")
            return self._json({"results": rows})
        if p == "/api/history":
            if self._guard():
                return
            return self._history()
        if p == "/api/wrong/pdf":
            if self._guard():
                return
            return self._wrong_pdf()
        return self._json({"error": "not_found"}, 404)

    def _examkeys(self, q):
        """한 회차(연도·회차·과정)의 모든 과목 정답키 반환 → 실전 채점용."""
        year, rnd, level = q.get("year"), q.get("exam_round"), q.get("level")
        c = db()
        rows = c.execute("""SELECT qu.subject subj, k.qnum qnum, k.answer ans
                            FROM qkey k JOIN questions qu ON qu.id=k.question_id
                            WHERE qu.year=? AND qu.exam_round=? AND qu.level=?
                            ORDER BY qu.subject, k.qnum""", (year, rnd, level))
        keys = {}
        for r in rows:
            keys.setdefault(r["subj"], {})[str(r["qnum"])] = r["ans"]
        c.close()
        return self._json({"keys": keys})

    def _history(self):
        """attempts를 (날짜·연도·회차·과목) 세션으로 집계 → 기출 풀이 기록."""
        c = db()
        rows = c.execute("""SELECT date(ts,'unixepoch','localtime') d, year, exam_round, subject,
                                   COUNT(*) total, SUM(CASE WHEN correct=1 THEN 1 ELSE 0 END) correct,
                                   MAX(ts) ts
                            FROM attempts GROUP BY d, year, exam_round, subject
                            ORDER BY ts DESC LIMIT 200""").fetchall()
        out = [{"date": r["d"], "year": r["year"], "exam_round": r["exam_round"], "subject": r["subject"],
                "total": r["total"], "correct": r["correct"], "wrong": r["total"] - r["correct"]} for r in rows]
        c.close()
        return self._json({"history": out})

    def _wrong_pdf(self):
        """최근 오답 문항들을 이미지로 렌더해 하나의 PDF로 합쳐 반환."""
        try:
            import fitz
        except ImportError:
            return self._json({"error": "no_fitz"}, 501)
        c = db()
        rows = c.execute("""SELECT a.question_id qid, a.qnum qnum, a.subject subj, a.year year, a.exam_round rnd,
                                   qu.level level
                            FROM attempts a
                            JOIN (SELECT question_id,qnum,MAX(id) mi FROM attempts WHERE source='quiz' GROUP BY question_id,qnum) l
                              ON a.question_id=l.question_id AND a.qnum=l.qnum AND a.id=l.mi AND a.source='quiz'
                            JOIN questions qu ON qu.id=a.question_id
                            WHERE a.correct=0 ORDER BY a.subject, a.year, a.exam_round, a.qnum""").fetchall()
        c.close()
        if not rows:
            return self._json({"error": "no_wrong"}, 404)
        # 과목별로 묶기(입력이 이미 subject 정렬)
        from collections import OrderedDict
        groups = OrderedDict()
        for r in rows:
            groups.setdefault(r["subj"], []).append(r)
        # A4 세로 · 3열 그리드 (실제 시험지처럼 여러 문항이 한 페이지에)
        W, H, MARGIN, GAP, COLS = 595.0, 842.0, 28.0, 18.0, 2   # 실제 검정고시처럼 세로 2단
        cellW = (W - 2 * MARGIN - (COLS - 1) * GAP) / COLS
        LABELH = 14   # 문항마다 고유번호(#N) 라벨 자리 → 채점 시 정확 매칭
        out = fitz.open()
        gidx = 1      # 전체 문항 통짜 순번(오답노트 목록 순서와 동일) → 채점 정렬 기준
        for subj, items in groups.items():
            imgs = []
            for r in items:
                row = {"id": r["qid"], "year": r["year"], "exam_round": r["rnd"], "level": r["level"], "subject": r["subj"]}
                try:
                    path = render_qimage(row, r["qnum"])
                except Exception:
                    path = None
                if not path or not os.path.exists(path):
                    continue
                src = fitz.open("png", open(path, "rb").read())
                sp = src[0].get_pixmap(matrix=fitz.Matrix(0.62, 0.62))   # 셀이 작으니 다운샘플 → 용량 절감
                imgs.append((r["qnum"], sp.tobytes("png"), sp.width, sp.height))
                src.close()
            if not imgs:
                continue
            page = out.new_page(width=W, height=H)
            page.insert_text((MARGIN, MARGIN + 8), f"[{subj}] 오답 {len(imgs)}문항", fontsize=15, fontname="korea")   # 내장 한국어 폰트
            y = MARGIN + 24
            for i in range(0, len(imgs), COLS):
                rowitems = imgs[i:i + COLS]
                scaled = [(png, cellW, ph * (cellW / pw)) for (qn, png, pw, ph) in rowitems]   # (png, 폭, 이미지높이)
                imgH = max(h for _, _, h in scaled)
                avail = H - MARGIN - y
                if imgH + LABELH > avail:              # 이 행이 페이지에 안 들어가면
                    if y > MARGIN + 24:                # 첫 행이 아니면 새 페이지
                        page = out.new_page(width=W, height=H); y = MARGIN; avail = H - 2 * MARGIN
                    if imgH + LABELH > avail:          # 새 페이지에도 안 들어갈 만큼 크면 행 전체 축소
                        sh = (avail - LABELH) / imgH
                        scaled = [(png, w * sh, h * sh) for (png, w, h) in scaled]
                        imgH = avail - LABELH
                for j, (png, w, hgt) in enumerate(scaled):
                    cx = MARGIN + j * (cellW + GAP)
                    page.insert_text((cx, y + 10), f"#{gidx}", fontsize=11, color=(0.85, 0, 0))   # 고유번호(채점 매칭 기준)
                    page.insert_image(fitz.Rect(cx + (cellW - w) / 2, y + LABELH, cx + (cellW - w) / 2 + w, y + LABELH + hgt), stream=png)
                    gidx += 1
                y += imgH + LABELH + GAP
        if out.page_count == 0:
            out.close(); return self._json({"error": "no_image"}, 404)
        data = out.tobytes(deflate=True)
        out.close()
        self.send_response(200)
        self.send_header("Content-Type", "application/pdf")
        self.send_header("Content-Disposition", "attachment; filename=wrong_notes.pdf")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _study_get(self, today, week_start, period_start):
        c = db()
        def one(sql, args=()):
            r = c.execute(sql, args).fetchone()
            return {k: round(r[k] or 0) for k in ("math", "sci", "kor", "nc2")} if r else {"math": 0, "sci": 0, "kor": 0, "nc2": 0}
        sums = "SELECT SUM(math) math,SUM(sci) sci,SUM(kor) kor,SUM(nc2) nc2 FROM study_log"
        today_row = one("SELECT math,sci,kor,nc2 FROM study_log WHERE date=?", (today,))
        week = one(sums + " WHERE date>=?", (week_start,))
        period = one(sums + " WHERE date>=?", (period_start,))     # 최근 3주(21일)
        total = one(sums)
        days = [dict(x) for x in c.execute("SELECT date,math,sci,kor,nc2 FROM study_log WHERE date>=? ORDER BY date", (period_start,))]
        c.close()
        return self._json({"today": today_row, "week": week, "period": period, "total": total, "days": days})

    def _questions(self, q):
        where, args = [], []
        for col in ("subject", "year", "exam_round", "level"):
            if q.get(col):
                where.append(f"{col}=?"); args.append(q[col])
        base = "SELECT id,year,exam_round,level,subject,raw_text,answer_text FROM questions"
        clause = (" WHERE " + " AND ".join(where)) if where else ""
        c = db()
        if q.get("random"):
            # 안 풀어본 기출(응시 기록 없는 것) 우선, 다 풀었으면 전체에서 랜덤
            und = "id NOT IN (SELECT question_id FROM attempts)"
            uw = " WHERE " + " AND ".join(where + [und]) if where else " WHERE " + und
            rows = [dict(x) for x in c.execute(base + uw + " ORDER BY RANDOM() LIMIT 1", args)]
            if not rows:
                rows = [dict(x) for x in c.execute(base + clause + " ORDER BY RANDOM() LIMIT 1", args)]
        else:
            rows = [dict(x) for x in c.execute(base + clause + " ORDER BY id LIMIT 200", args)]
            # 현재 상태는 '문항별 최신 시도'만으로 집계.
            # (기존엔 SUM(correct)가 모든 시도를 합산해 2회 풀면 49/25·224% 같은 값이 나왔음)
            prog = {r["qid"]: (r["n"], r["ok"]) for r in c.execute("""
                SELECT a.question_id qid, COUNT(*) n, SUM(a.correct) ok FROM attempts a
                JOIN (SELECT question_id,qnum,MAX(id) mi FROM attempts WHERE source='quiz' GROUP BY question_id,qnum) l
                  ON a.id=l.mi
                GROUP BY a.question_id""")}
            # 몇 번 풀었는지(문항별 최대 시도 횟수)와 전체 평균 정답률
            hist = {r["qid"]: (r["passes"], r["tries"], r["allok"]) for r in c.execute("""
                SELECT question_id qid, MAX(c) passes, SUM(c) tries, SUM(sc) allok FROM
                  (SELECT question_id, qnum, COUNT(*) c, SUM(correct) sc
                     FROM attempts WHERE source='quiz' GROUP BY question_id, qnum)
                GROUP BY question_id""")}
            # 회차별 총 문항수(정답키 기준) → 진도율 계산용
            tot = {r["qid"]: r["n"] for r in c.execute(
                "SELECT question_id qid, COUNT(*) n FROM qkey GROUP BY question_id")}
            for r in rows:
                n, ok = prog.get(r["id"], (0, 0))
                passes, tries, allok = hist.get(r["id"], (0, 0, 0))
                r["solved"] = n
                r["correct"] = ok or 0
                r["total"] = tot.get(r["id"], 0)
                r["passes"] = passes or 0                                  # 몇 회독
                r["avg_pct"] = round((allok or 0) / tries * 100) if tries else 0   # 전체 시도 평균
        c.close()
        return self._json({"questions": rows})

    def _quiz(self, qid):
        c = db()
        row = c.execute("SELECT id,subject,year,exam_round,level,raw_text FROM questions WHERE id=?", (qid,)).fetchone()
        if not row:
            c.close(); return self._json({"error": "not_found"}, 404)
        key = {r["qnum"]: r["answer"] for r in c.execute("SELECT qnum,answer FROM qkey WHERE question_id=?", (qid,))}
        c.close()
        items = parse_items(row["raw_text"])
        path = pdf_path(row); has_pdf = os.path.exists(path); subj = row["subject"]
        figs = None
        if has_pdf and subj in ("과학", "사회", "한국사", "국어"):
            try:
                figs = qfigs(path)
            except Exception:
                figs = None
        def wants_img(it):   # 그림·표·그래프·지도·보기박스 언급 → 이미지가 필요한 문항
            b = (it.get("stem", "") + " " + " ".join(c.get("text", "") for c in it.get("choices", [])))
            return bool(re.search(r'그림|도표|그래프|지도|다음\s*표|아래\s*표|〈보기〉|<보기>|\[보기\]|【보기】|다음\s*자료', b))
        for it in items:
            it["answer"] = key.get(it["qnum"])
            if not has_pdf:
                it["img"] = False
            elif subj == "수학":
                it["img"] = True
            elif figs is not None:
                it["img"] = (it["qnum"] in figs) or it["image"] or wants_img(it)
            else:
                it["img"] = it["image"] or wants_img(it)
            it["hasPdf"] = has_pdf   # 클라이언트: PDF 있으면 '원본 사진 보기' 버튼 노출
        meta = {k: row[k] for k in ("id", "subject", "year", "exam_round", "level")}
        return self._json({"meta": meta, "items": items})

    def _qimage(self, qid, qnum):
        try:
            qid = int(qid); qnum = int(qnum)
        except (TypeError, ValueError):
            return self._json({"error": "bad_input"}, 400)
        c = db(); row = c.execute("SELECT id,year,exam_round,level,subject FROM questions WHERE id=?", (qid,)).fetchone(); c.close()
        if not row:
            return self._json({"error": "not_found"}, 404)
        try:
            f = render_qimage(row, qnum)
        except ImportError:
            return self._json({"error": "no_fitz"}, 501)
        except Exception as e:
            return self._json({"error": "render_failed", "detail": str(e)}, 500)
        if not f:
            return self._json({"error": "no_image"}, 404)
        return self._file(f, "image/png")

    def _wrong(self):
        c = db()
        # 멤버십: 최신 '문제은행(quiz)' 시도가 오답인 문항 (PDF 채점은 멤버십에 영향 X → 정답 맞혀도 유지)
        # wrong_count: quiz+pdf 통틀어 틀린 횟수 → 2회 이상이면 '반복 오답'(복습·PDF에서 또 틀림)
        rows = c.execute("""
            SELECT a.*, wc.wc wrong_count FROM attempts a
            JOIN (SELECT question_id,qnum,MAX(id) mi FROM attempts WHERE source='quiz' GROUP BY question_id,qnum) l
              ON a.question_id=l.question_id AND a.qnum=l.qnum AND a.id=l.mi AND a.source='quiz'
            JOIN (SELECT question_id,qnum,SUM(CASE WHEN correct=0 THEN 1 ELSE 0 END) wc
                    FROM attempts GROUP BY question_id,qnum) wc
              ON wc.question_id=a.question_id AND wc.qnum=a.qnum
            WHERE a.correct=0 ORDER BY wc.wc DESC, a.ts DESC""").fetchall()
        wrong = [dict(r) for r in rows]
        by_subject, repeat = {}, 0
        for w in wrong:
            w["repeat"] = w["wrong_count"] >= 2       # 반복 오답 라벨
            if w["repeat"]:
                repeat += 1
            by_subject[w["subject"]] = by_subject.get(w["subject"], 0) + 1
        total = c.execute("SELECT COUNT(DISTINCT question_id||'-'||qnum) n FROM attempts WHERE source='quiz'").fetchone()["n"]
        c.close()
        return self._json({"wrong": wrong, "by_subject": by_subject, "answered": total, "repeat": repeat})

    @staticmethod
    def _garbled(s):
        """PDF에서 글꼴이 깨져 추출된 발문인지 판정 (수학 수식 · 중세국어 옛한글 등)."""
        if not s:
            return True
        n = len(s)
        bad = sum(1 for ch in s if "À" <= ch <= "ɏ" or ch in "˘˙˚˛˜˝ˆˇ")
        if bad >= 4 and bad / max(1, n) > 0.06:
            return True
        # 중세국어: 옛한글 글리프가 가운뎃점·중복 음절로 뭉개져 나옴
        dots = s.count("·") + s.count("ㆍ") + s.count("‧") + s.count("・")
        if n >= 30 and dots / n > 0.08:
            return True
        jamo = sum(1 for ch in s if "ᄀ" <= ch <= "ᇿ" or "㄰" <= ch <= "㆏"
                   or "ꥠ" <= ch <= "꥿" or "ힰ" <= ch <= "퟿")
        if n >= 20 and jamo / max(1, n) > 0.12:
            return True
        return False

    # 표·그림·보기에 의존해 텍스트만으론 못 푸는 문항
    _NEEDS_IMG = re.compile(r'그림|도표|그래프|지도|삽화|사진|다음\s*표|아래\s*표|위의?\s*표|표에서|표의\s|<표>|\[표\]|'
                            r'〈보기〉|<보기>|\[보기\]|【보기】|다음\s*자료|자료를\s*보고|다음\s*대화|대화에서')

    @classmethod
    def _needs_image(cls, subject, stem, choices_text, answer_text):
        if subject == "수학":
            return True
        if cls._garbled(stem) or cls._garbled(answer_text):
            return True
        if cls._NEEDS_IMG.search(stem or "") or cls._NEEDS_IMG.search(choices_text or ""):
            return True
        # 정답이 A~E·갑을병정·학생 N 처럼 표/그림 안에서만 의미를 갖는 경우
        if re.fullmatch(r'[A-E]|[가-힣]?\s*[A-E]|갑|을|병|정|학생\s*\d|[㉠-㉺]', (answer_text or "").strip()):
            return True
        return False

    def _final_sheet(self):
        """최종 요약 시트: 남은 오답 전부를 발문·내 답·정답까지 붙여 과목별로 한 번에 반환(시험 전날 1장 훑기용)."""
        c = db()
        rows = c.execute("""
            SELECT a.*, wc.wc wrong_count FROM attempts a
            JOIN (SELECT question_id,qnum,MAX(id) mi FROM attempts WHERE source='quiz' GROUP BY question_id,qnum) l
              ON a.question_id=l.question_id AND a.qnum=l.qnum AND a.id=l.mi AND a.source='quiz'
            JOIN (SELECT question_id,qnum,SUM(CASE WHEN correct=0 THEN 1 ELSE 0 END) wc
                    FROM attempts GROUP BY question_id,qnum) wc
              ON wc.question_id=a.question_id AND wc.qnum=a.qnum
            WHERE a.correct=0 ORDER BY a.subject, wc.wc DESC, a.year, a.qnum""").fetchall()
        pages, by_subject = {}, {}
        for w in rows:
            qid = w["question_id"]
            if qid not in pages:
                pr = c.execute("SELECT raw_text FROM questions WHERE id=?", (qid,)).fetchone()
                pages[qid] = {it["qnum"]: it for it in parse_items(pr["raw_text"])} if pr else {}
            it = pages[qid].get(w["qnum"], {})
            chs = {ch.get("n"): (ch.get("text") or "") for ch in it.get("choices", [])}
            stem = (it.get("stem") or "").replace("\n", " ").strip()[:150]
            ans_t, cho_t = chs.get(w["answer"], "")[:60], chs.get(w["chosen"], "")[:60]
            # 수학은 수식이 텍스트로 안 나오므로 항상 원본 이미지, 그 외엔 깨졌을 때만
            ch_txt = " ".join(ch.get("text", "") for ch in it.get("choices", []))
            img = bool(it.get("image")) or self._needs_image(w["subject"], stem, ch_txt, ans_t)
            by_subject.setdefault(w["subject"], []).append({
                "question_id": qid, "qnum": w["qnum"], "year": w["year"], "exam_round": w["exam_round"],
                "stem": stem, "img": img,
                "answer": w["answer"], "chosen": w["chosen"], "tag": w["tag"],
                "repeat": (w["wrong_count"] or 0) >= 2,
                "answer_text": ans_t, "chosen_text": cho_t,
            })
        c.close()
        order = ["과학", "도덕", "수학", "국어", "한국사", "사회"]
        subs = sorted(by_subject.keys(), key=lambda s: (order.index(s) if s in order else 99, s))
        return self._json({"subjects": [{"subject": s, "items": by_subject[s]} for s in subs],
                           "total": sum(len(v) for v in by_subject.values())})

    def _wrong_grade(self, b):
        """푼 오답노트 PDF 업로드 → Gemini 비전으로 표시된 답 읽어 채점. (attempts 미기록 → 오답노트 유지)"""
        try:
            import fitz
        except ImportError:
            return self._json({"error": "no_fitz"}, 501)
        if not gemini_key():
            return self._json({"error": "no-ai"}, 503)
        data = b.get("data", "")
        if "," in data:
            data = data.split(",", 1)[1]
        try:
            raw = base64.b64decode(data)
        except Exception:
            return self._json({"error": "bad_base64"}, 400)
        if raw[:4] != b"%PDF":
            return self._json({"error": "not_pdf"}, 400)
        # 오답노트 문항 목록(생성 PDF와 동일 순서) + 정답
        c = db()
        rows = c.execute("""SELECT a.question_id qid, a.qnum qnum, a.subject subj, a.year year, a.exam_round rnd, k.answer ans
                            FROM attempts a
                            JOIN (SELECT question_id,qnum,MAX(id) mi FROM attempts WHERE source='quiz' GROUP BY question_id,qnum) l
                              ON a.question_id=l.question_id AND a.qnum=l.qnum AND a.id=l.mi AND a.source='quiz'
                            LEFT JOIN qkey k ON k.question_id=a.question_id AND k.qnum=a.qnum
                            WHERE a.correct=0 ORDER BY a.subject, a.year, a.exam_round, a.qnum""").fetchall()
        c.close()
        wn = [dict(r) for r in rows]
        if not wn:
            return self._json({"error": "no_wrong"}, 404)
        # PDF → 이미지
        try:
            doc = fitz.open("pdf", raw)
            images = [pg.get_pixmap(matrix=fitz.Matrix(1.7, 1.7)).tobytes("png") for pg in doc]
            doc.close()
        except Exception as e:
            return self._json({"error": "pdf_read_failed", "detail": str(e)}, 400)
        images = images[:8]   # 과도한 업로드 방지
        prompt = ("이 이미지는 학생이 손으로 푼 검정고시 '오답노트' 답안지입니다. "
                  "각 문항 왼쪽 위에는 빨간색 '#숫자'(예: #1, #2 …) 고유번호가 적혀 있고, 문항마다 ①②③④ 보기가 있습니다. "
                  "★중요: 당신은 문제를 풀지 마세요. 오직 학생이 펜으로 '동그라미·체크·색칠 등으로 표시한' 보기 번호(1~4)만 그대로 읽어 주세요. "
                  "표시가 없거나 불확실하면 marked를 0으로 하세요. "
                  "각 문항의 '#숫자'(index)와 학생이 표시한 번호(marked)를 JSON 배열로만 답하세요: "
                  '[{"index":1,"marked":3},{"index":2,"marked":0}, ...]. 다른 설명 없이 JSON만 출력하세요.')
        try:
            txt = ai_vision(prompt, images)
        except Exception as e:
            return self._json({"error": "ai_failed", "detail": str(e)}, 502)
        if not txt:
            return self._json({"error": "ai_empty"}, 502)
        try:
            marks = json.loads(txt)
            if not isinstance(marks, list):
                marks = marks.get("answers") or marks.get("items") or []
        except Exception:
            return self._json({"error": "ai_parse", "raw": txt[:400]}, 502)
        # #번호(index)로 정확히 매칭 (index 없으면 순서 폴백)
        from collections import deque
        by_index = {}
        for m in marks:
            if isinstance(m, dict) and m.get("index") is not None:
                try:
                    by_index[int(m["index"])] = int(m.get("marked") or 0)
                except (TypeError, ValueError):
                    pass
        pos = deque(int(m.get("marked") or 0) for m in marks if isinstance(m, dict))   # 폴백: 순수 순서
        results, by_subject = [], {}
        for gi, it in enumerate(wn, start=1):
            if gi in by_index:
                marked = by_index[gi]
            else:
                marked = pos.popleft() if pos else 0
            ans = it["ans"]
            correct = 1 if (ans is not None and marked == ans) else 0
            results.append({"index": gi, "subject": it["subj"], "year": it["year"], "exam_round": it["rnd"],
                            "qnum": it["qnum"], "marked": marked, "answer": ans, "correct": correct})
            s = by_subject.setdefault(it["subj"], {"total": 0, "correct": 0, "score": 0, "pts": 5 if it["subj"] == "수학" else 4})
            s["total"] += 1; s["correct"] += correct; s["score"] += correct * s["pts"]
        graded = sum(1 for r in results if r["marked"])
        # AI가 읽은 문항을 source='pdf'로 기록 → 반복 오답 카운트 반영(멤버십엔 영향 없음: 정답이어도 오답노트 유지)
        c2 = db(); now = int(time.time())
        for it, r in zip(wn, results):
            if r["marked"]:
                c2.execute("INSERT INTO attempts(question_id,subject,year,exam_round,qnum,chosen,answer,correct,ts,source) VALUES(?,?,?,?,?,?,?,?,?,'pdf')",
                           (it["qid"], it["subj"], it["year"], it["rnd"], it["qnum"], r["marked"], it["ans"], r["correct"], now))
        c2.commit(); c2.close()
        return self._json({"results": results, "by_subject": by_subject,
                           "total": len(results), "read": graded,
                           "correct": sum(r["correct"] for r in results)})

    def _wrong_analyze(self):
        c = db()
        rows = c.execute("""
            SELECT a.* FROM attempts a
            JOIN (SELECT question_id,qnum,MAX(id) mi FROM attempts WHERE source='quiz' GROUP BY question_id,qnum) l
              ON a.question_id=l.question_id AND a.qnum=l.qnum AND a.id=l.mi AND a.source='quiz'
            WHERE a.correct=0 ORDER BY a.ts DESC LIMIT 30""").fetchall()
        if not rows:
            c.close(); return self._json({"analysis": "", "empty": True})
        pages, lines = {}, []
        for w in rows:
            qid = w["question_id"]
            if qid not in pages:
                pr = c.execute("SELECT raw_text FROM questions WHERE id=?", (qid,)).fetchone()
                pages[qid] = {it["qnum"]: it for it in parse_items(pr["raw_text"])} if pr else {}
            it = pages[qid].get(w["qnum"], {})
            stem = (it.get("stem") or "").replace("\n", " ").strip()[:70]
            chosen = "모름" if w["chosen"] == 0 else f"{w['chosen']}번"
            lines.append(f"- {w['subject']} {w['qnum']}번: {stem} (내 답 {chosen}, 정답 {w['answer']}번)")
        c.close()
        prompt = ("아래는 학생이 검정고시 공부 중 최근 틀린 문제들입니다. "
                  "(1) 자주 틀리는 과목·유형, (2) 틀리는 이유·패턴, (3) 과목별 보완 학습법 순서로 "
                  "존댓말로 간결하고 구조적으로 정리해 주세요.\n\n" + "\n".join(lines))
        try:
            out = ai_complete("당신은 검정고시 학습 코치입니다. 학생의 오답을 분석해 도움이 되는 조언을 합니다.",
                              [{"role": "user", "content": prompt}], 900)
        except Exception as e:
            return self._json({"error": "ai_failed", "detail": str(e)}, 502)
        if out is None:
            return self._json({"error": "no-ai"}, 503)
        return self._json({"analysis": out.strip(), "count": len(rows)})

    # ---- 수호령의 눈: 화상 스터디 버디 ----
    STUDENT = ("학생 이름은 '수영'. 2026-08-11(화) 고졸 검정고시 재응시. 영어는 면제(100점 확정), "
               "응시 6과목은 국어·수학·사회·과학·한국사·도덕. 취약 과목은 과학·도덕·수학. "
               "목표는 평균 94점 이상(성공회대 열린인재 지원선), 만점이면 교과전형도 가능. 반말로 친근하게 대한다.")

    def _decode_img(self, s):
        if not s:
            return None
        if "," in s:
            s = s.split(",", 1)[1]
        try:
            return base64.b64decode(s)
        except Exception:
            return None

    def _focus_check(self, b):
        """웹캠·화면 프레임 → 공부 중인지 판정 + 잔소리 멘트."""
        if not gemini_key():
            return self._json({"error": "no-ai"}, 503)
        imgs = [x for x in (self._decode_img(b.get("cam")), self._decode_img(b.get("screen"))) if x]
        if not imgs:
            return self._json({"error": "bad_input"}, 400)
        what = "첫 번째 이미지는 웹캠(학생 모습)" + (", 두 번째는 학생의 화면" if len(imgs) > 1 else "") + "입니다."
        mins = int(b.get("focused_min", 0) or 0)
        prompt = (f"{self.STUDENT}\n{what}\n"
                  "학생이 지금 '공부 중'인지 판단하세요. 공부 중 = 책·문제집·노트·태블릿을 보거나 필기하거나 "
                  "학습 사이트/문서를 보고 있음. 딴짓 = 자리 비움, 엎드려 잠, 휴대폰으로 SNS·영상 시청, "
                  "게임·쇼핑·유튜브 등 학습과 무관한 화면.\n"
                  f"지금까지 집중한 시간은 {mins}분입니다.\n"
                  '반드시 JSON만 출력: {"studying":true/false,"activity":"보이는 상황 한국어 12자 이내",'
                  '"message":"학생에게 할 말 한 문장(반말, 25자 이내)"}\n'
                  "공부 중이면 message는 짧은 격려, 딴짓이면 따끔하지만 다정한 잔소리로 쓰세요.")
        try:
            txt = ai_vision(prompt, imgs, 300)
        except Exception as e:
            return self._json({"error": "ai_failed", "detail": str(e)}, 502)
        try:
            d = json.loads(txt or "{}")
        except Exception:
            return self._json({"error": "ai_parse", "raw": (txt or "")[:200]}, 502)
        return self._json({"studying": bool(d.get("studying")), "activity": str(d.get("activity", ""))[:40],
                           "message": str(d.get("message", ""))[:120]})

    def _focus_talk(self, b):
        """스터디 버디와 대화 — 학생 정보·현재 학습 상황 기반."""
        msg = (b.get("text") or "").strip()
        if not msg:
            return self._json({"error": "bad_input"}, 400)
        c = db()
        acc = {}
        for r in c.execute("""SELECT a.subject s, COUNT(*) n, SUM(a.correct) ok FROM attempts a
                JOIN (SELECT question_id,qnum,MAX(id) mi FROM attempts WHERE source='quiz' GROUP BY question_id,qnum) l
                  ON a.question_id=l.question_id AND a.qnum=l.qnum AND a.id=l.mi AND a.source='quiz' GROUP BY a.subject"""):
            acc[r["s"]] = f'{round((r["ok"] or 0)/r["n"]*100)}%({r["n"]}문항)'
        c.close()
        import datetime as _dt
        dday = (_dt.date(2026, 8, 11) - _dt.date.today()).days
        ctx = (f"{self.STUDENT}\n시험까지 D-{dday}. 과목별 정답률: "
               + (", ".join(f"{k} {v}" for k, v in acc.items()) if acc else "아직 기록 없음")
               + f"\n지금 이번 세션에서 {int(b.get('focused_min',0) or 0)}분째 공부 중이고, "
                 f"현재 화면 상태는 '{b.get('activity','')}'입니다.")
        sysmsg = ("너는 학생 '수영'과 함께 공부하는 스터디 메이트 '수호령'이야. 화상으로 같이 공부하는 친구처럼 "
                  "반말로 짧고 자연스럽게 말해. 음성으로 읽히니 3문장 이내, 마크다운·이모지 남발 금지. "
                  "공부 내용 질문엔 정확히 답하고, 잡담엔 가볍게 받아주되 다시 공부로 유도해.\n\n" + ctx)
        hist = b.get("history") or []
        msgs = [{"role": ("assistant" if m.get("role") == "assistant" else "user"),
                 "content": str(m.get("text", ""))[:500]} for m in hist[-8:]]
        msgs.append({"role": "user", "content": msg})
        try:
            out = ai_complete(sysmsg, msgs, 400)
        except Exception as e:
            return self._json({"error": "ai_failed", "detail": str(e)}, 502)
        if out is None:
            return self._json({"error": "no-ai"}, 503)
        return self._json({"text": out.strip()})

    def _recommend(self):
        """오늘의 추천 학습: 취약 과목 · 반복오답 · 미풀이 회차를 조합해 할 일 제시."""
        c = db()
        st = {}
        for r in c.execute("""SELECT a.subject subj, a.correct ok FROM attempts a
                JOIN (SELECT question_id,qnum,MAX(id) mi FROM attempts WHERE source='quiz' GROUP BY question_id,qnum) l
                  ON a.question_id=l.question_id AND a.qnum=l.qnum AND a.id=l.mi AND a.source='quiz'"""):
            s = st.setdefault(r["subj"], [0, 0]); s[0] += 1; s[1] += r["ok"] or 0
        rep = {}
        for r in c.execute("""SELECT a.subject subj, COUNT(*) n FROM attempts a
                JOIN (SELECT question_id,qnum,SUM(CASE WHEN correct=0 THEN 1 ELSE 0 END) wc FROM attempts GROUP BY question_id,qnum) w
                  ON w.question_id=a.question_id AND w.qnum=a.qnum
                JOIN (SELECT question_id,qnum,MAX(id) mi FROM attempts WHERE source='quiz' GROUP BY question_id,qnum) l
                  ON a.question_id=l.question_id AND a.qnum=l.qnum AND a.id=l.mi AND a.source='quiz'
                WHERE a.correct=0 AND w.wc>=2 GROUP BY a.subject"""):
            rep[r["subj"]] = r["n"]
        unsolved = [dict(x) for x in c.execute(
            """SELECT id,subject,year,exam_round,level FROM questions
               WHERE id NOT IN (SELECT DISTINCT question_id FROM attempts WHERE source='quiz')
               ORDER BY year DESC, exam_round DESC LIMIT 60""")]
        c.close()
        WEAK = ["과학", "도덕", "수학"]        # 전년도 실점 76%가 몰린 과목
        accs = {k: (round(v[1] / v[0] * 100) if v[0] else None) for k, v in st.items()}
        def prio(subj):
            a = accs.get(subj)
            base = (100 - a) if a is not None else 55      # 안 푼 과목도 우선순위 확보
            return base + (12 if subj in WEAK else 0) + rep.get(subj, 0) * 3
        subs = sorted({*accs.keys(), *WEAK, *[u["subject"] for u in unsolved]}, key=lambda s: -prio(s))
        tasks = []
        for s in subs[:3]:
            un = [u for u in unsolved if u["subject"] == s]
            tasks.append({"subject": s, "acc": accs.get(s), "repeat": rep.get(s, 0),
                          "unsolved": un[0] if un else None, "unsolved_count": len(un)})
        return self._json({"tasks": tasks, "acc": accs, "repeat": rep, "unsolved_total": len(unsolved)})

    def _similar(self, q):
        """같은 과목의 다른 회차에서 키워드가 겹치는 유사 문항 추천."""
        try:
            qid = int(q.get("question_id")); qnum = int(q.get("qnum"))
        except (TypeError, ValueError):
            return self._json({"error": "bad_input"}, 400)
        c = db()
        row = c.execute("SELECT id,subject,raw_text FROM questions WHERE id=?", (qid,)).fetchone()
        if not row:
            c.close(); return self._json({"error": "not_found"}, 404)
        src = next((i for i in parse_items(row["raw_text"]) if i["qnum"] == qnum), None)
        if not src:
            c.close(); return self._json({"items": []})
        def keys(it):
            blob = (it.get("stem") or "") + " " + " ".join(ch.get("text", "") for ch in it.get("choices", []))
            return {w for w in re.findall(r'[가-힣]{2,10}', blob) if len(w) >= 2 and w not in
                    ("다음", "설명", "것은", "적절", "옳은", "가장", "무엇", "고른", "보기", "그림", "대한", "중에")}
        ks = keys(src)
        if not ks:
            c.close(); return self._json({"items": []})
        out = []
        for r in c.execute("SELECT id,year,exam_round,level,raw_text FROM questions WHERE subject=? AND id<>?", (row["subject"], qid)):
            for it in parse_items(r["raw_text"]):
                ov = ks & keys(it)
                if len(ov) >= 3:
                    out.append({"question_id": r["id"], "qnum": it["qnum"], "year": r["year"],
                                "exam_round": r["exam_round"], "level": r["level"], "subject": row["subject"],
                                "score": len(ov), "shared": sorted(ov)[:5],
                                "stem": (it.get("stem") or "").replace("\n", " ")[:70]})
        c.close()
        out.sort(key=lambda x: -x["score"])
        return self._json({"items": out[:6], "subject": row["subject"]})

    def _concept_quiz(self, subject, keywords=None):
        """해당 과목 기출 발문+선지+정답을 모아 속사포 개념 퀴즈로 제공(그림 문항 제외).
        keywords가 있으면 해당 단어를 포함하는 문항만(취약 유형 통합 연습용)."""
        if not subject:
            return self._json({"error": "bad_input"}, 400)
        c = db()
        rows = c.execute("SELECT id,year,exam_round,level,subject,raw_text FROM questions WHERE subject=?", (subject,)).fetchall()
        keys = {}
        for r in c.execute("""SELECT k.question_id qid,k.qnum qnum,k.answer ans FROM qkey k
                              JOIN questions q ON q.id=k.question_id WHERE q.subject=?""", (subject,)):
            keys[(r["qid"], r["qnum"])] = r["ans"]
        c.close()
        need = max(2, len(keywords) - 1) if keywords and len(keywords) >= 3 else (len(keywords) if keywords else 0)
        # 키워드 2개 이하면 전부 일치, 3개 이상이면 과반 이상 일치 요구 → 흔한 단어 1개만 겹쳐서 오탐 묶이는 것 방지
        out = []
        for r in rows:
            path = pdf_path(r); has_pdf = os.path.exists(path)
            for it in parse_items(r["raw_text"]):
                ans = keys.get((r["id"], it["qnum"]))
                stem = (it.get("stem") or "").strip()
                chs = [ch for ch in it.get("choices", []) if ch.get("text")]
                # 텍스트만으로 풀 수 있는 문항(그림·수식 의존 제외)
                if not ans or len(chs) < 4 or len(stem) < 8 or it.get("image"):
                    continue
                if re.search(r'그림|그래프|지도|다음\s*표|아래\s*표|자료를 보고', stem):
                    continue
                if keywords:
                    blob = stem + " " + " ".join(ch.get("text", "") for ch in chs) + " " + (it.get("passage") or "")
                    if sum(1 for kw in keywords if kw in blob) < need:
                        continue
                out.append({"question_id": r["id"], "qnum": it["qnum"], "stem": stem, "choices": chs,
                            "answer": ans, "year": r["year"], "exam_round": r["exam_round"],
                            "hasPdf": has_pdf, "passage": (it.get("passage") or "")[:400]})
        return self._json({"items": out, "subject": subject, "count": len(out)})

    def _concept_review(self, force=False, raw=False):
        """시험 전날 개념 총정리 — '한 번이라도 틀린 적 있는' 모든 문항(지금은 맞히는 것 포함)을
        개념 단위로 묶고, 실제 발문·내가 고른 오답·정답을 근거로 개념 설명을 만든다."""
        c = db()
        # 최신 시도만 보는 오답노트와 달리, 전체 이력에서 틀린 기록을 모두 센다
        rows = c.execute("""
            SELECT question_id, qnum, subject, year, exam_round,
                   COUNT(*) wrong_n, MAX(ts) last_ts,
                   GROUP_CONCAT(DISTINCT chosen) chosens
              FROM attempts WHERE correct=0
             GROUP BY question_id, qnum
             ORDER BY wrong_n DESC, last_ts DESC""").fetchall()
        total_wrong_ever = len(rows)
        if not rows:
            c.close(); return self._json({"clusters": [], "empty": True})
        # 오답 구성이 그대로면 이미 만든 결과를 재사용 (AI 호출 절약 · 레이트 리밋 회피)
        sig = hashlib.sha1(repr([(r["question_id"], r["qnum"], r["wrong_n"]) for r in rows]).encode()).hexdigest()
        if not force and not raw and _CR_CACHE.get("sig") == sig and _CR_CACHE.get("data"):
            c.close(); return self._json({**_CR_CACHE["data"], "cached": True})
        # 지금도 틀리는지(최신 시도 기준) → 우선순위 가중치
        still = {(r["question_id"], r["qnum"]) for r in c.execute("""
            SELECT a.question_id, a.qnum FROM attempts a
            JOIN (SELECT question_id,qnum,MAX(id) mi FROM attempts WHERE source='quiz' GROUP BY question_id,qnum) l
              ON a.id=l.mi WHERE a.correct=0""")}
        # 맞혔지만 오래 고민한 문항 = 흔들리는 개념. 체류시간은 직전 문항과의 시각 차로 추정.
        # (즉시채점은 문항마다 ts가 따로 찍히지만, 한번에 채점은 전부 같은 ts라 추정 불가 → 자동 제외)
        slow = []
        try:
            drows = c.execute("""
                WITH d AS (
                  SELECT id, question_id, qnum, subject, year, exam_round, correct, ts,
                         ts - LAG(ts) OVER (PARTITION BY question_id ORDER BY id) dwell
                    FROM attempts WHERE source='quiz')
                SELECT d.* FROM d
                JOIN (SELECT question_id,qnum,MAX(id) mi FROM attempts WHERE source='quiz'
                       GROUP BY question_id,qnum) l ON d.id=l.mi
                WHERE d.dwell IS NOT NULL AND d.dwell > 0 AND d.dwell <= 300""").fetchall()
            vals = sorted(r["dwell"] for r in drows)
            if len(vals) >= 8:
                med = vals[len(vals) // 2]
                cut = max(40, med * 2)                     # 평소의 2배 이상 걸린 것만
                ever_wrong_keys = {(r["question_id"], r["qnum"]) for r in rows}
                slow = [r for r in drows
                        if r["correct"] == 1 and r["dwell"] >= cut
                        and (r["question_id"], r["qnum"]) not in ever_wrong_keys]
                slow.sort(key=lambda r: -r["dwell"])
                slow = slow[:25]
        except Exception:
            slow = []                                      # 윈도우 함수 미지원 등은 조용히 건너뜀
        # 지금도 틀리는 것 → 여러 번 틀린 것 → 최근 순. 잘리더라도 덜 중요한 것부터 잘리도록.
        rows = sorted(rows, key=lambda w: (
            0 if (w["question_id"], w["qnum"]) in still else 1,
            -(w["wrong_n"] or 0), -(w["last_ts"] or 0)))
        MAX_ITEMS = 220                                        # 전체를 넣되 과도한 프롬프트만 방지
        brief = len(rows) > 90                                 # 많으면 항목당 텍스트를 줄여 전부 담는다
        pages, items = {}, []
        for w in rows[:MAX_ITEMS]:
            qid = w["question_id"]
            if qid not in pages:
                pr = c.execute("SELECT raw_text FROM questions WHERE id=?", (qid,)).fetchone()
                pages[qid] = {it["qnum"]: it for it in parse_items(pr["raw_text"])} if pr else {}
            it = pages[qid].get(w["qnum"], {})
            stem = (it.get("stem") or "").replace("\n", " ").strip()[:80 if brief else 110]
            if not stem:
                continue
            ch = {x.get("n"): (x.get("text") or "") for x in it.get("choices", [])}
            key = c.execute("SELECT answer FROM qkey WHERE question_id=? AND qnum=?", (qid, w["qnum"])).fetchone()
            ans = key["answer"] if key else None
            picked = [int(x) for x in (w["chosens"] or "").split(",") if x.strip().isdigit()]
            ans_t = (ch.get(ans) or "")[:44]
            ch_txt = " ".join(ch.values())
            items.append({
                "subject": w["subject"], "qid": qid, "qnum": w["qnum"],
                "round": f'{str(w["year"])[:4]} {str(w["exam_round"])[:2]}',
                "stem": stem, "wrong_n": w["wrong_n"],
                "still": (qid, w["qnum"]) in still,
                "answer_text": ans_t,
                # 표·그림에 의존하면 발문 텍스트만으론 내용을 알 수 없음 → AI가 지어내지 않도록 표시
                "visual": bool(it.get("image")) or self._needs_image(w["subject"], stem, ch_txt, ans_t),
                "choices_text": ch_txt[:60] if brief else ch_txt[:120],
                "wrong_text": " / ".join((ch.get(p) or "").strip()[:30] for p in picked if p and p != ans)[:70],
            })
        for w in slow:
            qid = w["question_id"]
            if qid not in pages:
                pr = c.execute("SELECT raw_text FROM questions WHERE id=?", (qid,)).fetchone()
                pages[qid] = {it["qnum"]: it for it in parse_items(pr["raw_text"])} if pr else {}
            it = pages[qid].get(w["qnum"], {})
            stem = (it.get("stem") or "").replace("\n", " ").strip()[:80 if brief else 110]
            if not stem:
                continue
            ch = {x.get("n"): (x.get("text") or "") for x in it.get("choices", [])}
            key = c.execute("SELECT answer FROM qkey WHERE question_id=? AND qnum=?", (qid, w["qnum"])).fetchone()
            ans_t = (ch.get(key["answer"]) or "")[:44] if key else ""
            ch_txt = " ".join(ch.values())
            items.append({
                "subject": w["subject"], "qid": qid, "qnum": w["qnum"],
                "round": f'{str(w["year"])[:4]} {str(w["exam_round"])[:2]}',
                "stem": stem, "wrong_n": 0, "still": False, "slow_sec": w["dwell"],
                "answer_text": ans_t,
                "visual": bool(it.get("image")) or self._needs_image(w["subject"], stem, ch_txt, ans_t),
                "choices_text": ch_txt[:60] if brief else ch_txt[:120],
                "wrong_text": "",
            })
        c.close()
        if len(items) < 2:
            return self._json({"clusters": [], "empty": True})
        # 두 그룹을 나눠 보여줘야 AI가 '지금은 맞힘' 쪽도 빠뜨리지 않음
        def fmt(i, it):
            vis = " ※표·그림 문항이라 발문만으로는 내용을 알 수 없음" if it["visual"] else ""
            return (f'#{i+1} [{it["subject"]}·{it["round"]}] {it["stem"]}{vis}\n'
                    f'   선택지: {it["choices_text"] or "-"}\n'
                    f'   └ {it["wrong_n"]}회 틀림 · 내가 고른 오답: {it["wrong_text"] or "-"} · 정답: {it["answer_text"] or "-"}')
        grp_a = [fmt(i, it) for i, it in enumerate(items) if it["still"]]
        grp_b = [fmt(i, it) for i, it in enumerate(items) if not it["still"] and not it.get("slow_sec")]
        grp_c = [f'#{i+1} [{it["subject"]}·{it["round"]}] {it["stem"]}\n'
                 f'   선택지: {it["choices_text"] or "-"}\n'
                 f'   └ 맞혔지만 {it["slow_sec"]}초 걸림 · 정답: {it["answer_text"] or "-"}'
                 for i, it in enumerate(items) if it.get("slow_sec")]
        lines = []
        if grp_a:
            lines.append(f"[A. 지금도 틀리는 문항 — {len(grp_a)}개]")
            lines += grp_a
        if grp_b:
            lines.append(f"\n[B. 예전에 틀렸다가 지금은 맞히는 문항 — {len(grp_b)}개]")
            lines.append("(이미 고쳤지만 한 번 틀린 개념이라 시험에서 또 흔들릴 수 있음 — 반드시 함께 정리)")
            lines += grp_b
        if grp_c:
            lines.append(f"\n[C. 맞혔지만 유난히 오래 걸린 문항 — {len(grp_c)}개]")
            lines.append("(정답은 골랐지만 확신이 없어 오래 망설인 것 — 개념이 덜 잡혔다는 신호)")
            lines += grp_c
        if raw:
            return self._json({"raw": True, "total_wrong_ever": total_wrong_ever,
                               "count": len(items), "items": items})
        prompt = (
            "아래는 검정고시 수험생이 지금까지 '한 번이라도 틀린' 문항들입니다. 내일이 시험이라 개념 총정리가 필요합니다.\n"
            "발문·선택지·학생이 고른 오답·정답을 근거로, 같은 개념끼리 묶어 '개념 복습 카드'를 만들어 주세요.\n\n"
            "가장 중요한 규칙 — 내용 없는 문장 금지:\n"
            "- '해당 화석이 번성한 시기는 중생대임', '이 시기의 대표 화석은 중생대의 화석임' 처럼 "
            "질문을 그대로 되풀이하는 동어반복은 절대 쓰지 마세요. 정보가 0입니다.\n"
            "- 각 core 항목은 그 자체로 검증 가능한 사실이어야 합니다. "
            "(나쁜 예: '해당 화석은 중생대에 번성함' / 좋은 예: '암모나이트·공룡은 중생대 표준 화석')\n"
            "- '※표·그림 문항' 표시가 있으면 그림 내용을 알 수 없으므로, 그 문항에서 구체적 사실을 추측하지 마세요.\n"
            "- 그런 문항만으로 이루어진 개념은 아예 카드로 만들지 마세요.\n"
            "- 선택지에 실제로 등장한 용어(예: 삼엽충·암모나이트·매머드)를 근거로 쓸 수 있으면 그것을 쓰세요.\n"
            "- 확실하지 않은 수치·연도·인명은 지어내지 말고, 쓸 내용이 없으면 그 카드를 만들지 마세요.\n\n"
            "구성 규칙 — A와 B를 모두 다뤄야 함:\n"
            "- 목록은 [A] 지금도 틀리는 문항과 [B] 예전에 틀렸다가 지금은 맞히는 문항으로 나뉩니다.\n"
            "- 카드의 최소 3분의 1은 [B]·[C] 문항으로 만드세요. B나 C만으로 이루어진 카드도 포함하세요.\n"
            "  (한 번 틀린 개념은 시험에서 다시 흔들리기 쉬우므로 복습 대상입니다.)\n"
            "- 전체 12~16개 카드, 과목당 최대 5개. 한 문항짜리라도 중요하면 카드로 만들어도 됩니다.\n"
            "- A를 앞쪽에, B를 뒤쪽에 배치하세요.\n"
            "- 학생이 고른 오답을 보고 '무엇과 무엇을 혼동했는지'를 콕 집어 주세요.\n\n"
            "각 카드는 JSON 객체로:\n"
            '{"subject":"과목","concept":"개념 이름(15자 이내)","wrong_n":총 틀린 횟수,'
            '"confuse":"무엇과 무엇을 헷갈렸는지 한 문장","core":["구체적 사실 2~4개(각 40자 이내)"],'
            '"trap":"시험에서 파는 함정 한 문장","memorize":"시험장에서 이것만 기억(30자 이내)",'
            '"needs_original":true/false(그림·표를 직접 봐야 하면 true),"indices":[해당 #번호]}\n'
            "JSON 배열만 출력하세요. 다른 설명 금지.\n\n" + "\n".join(lines))
        try:
            txt = ai_complete("당신은 검정고시 학습 코치입니다. 주어진 근거 안에서만, 정확하고 간결하게 개념을 정리합니다.",
                              [{"role": "user", "content": prompt}], 3000)
        except Exception as e:
            return self._json({"error": "ai_failed", "detail": str(e)[:120]}, 502)
        if txt is None:
            return self._json({"error": "no-ai"}, 503)
        try:
            arr = json.loads(re.sub(r'^```(?:json)?\s*|\s*```$', '', txt.strip()))
        except Exception:
            return self._json({"error": "ai_parse", "raw": txt[:300]}, 502)
        out = []
        for cl in (arr if isinstance(arr, list) else []):
            idxs = [i for i in (cl.get("indices") or []) if isinstance(i, int) and 1 <= i <= len(items)]
            refs = [{"question_id": items[i - 1]["qid"], "qnum": items[i - 1]["qnum"],
                     "round": items[i - 1]["round"], "stem": items[i - 1]["stem"][:60],
                     "still": items[i - 1]["still"]} for i in idxs]
            concept = str(cl.get("concept", ""))[:24]
            core = [c for c in (str(x)[:70] for x in (cl.get("core") or [])) if not _vacuous(c, concept)][:4]
            if not core:
                continue                                   # 알맹이가 없으면 카드 자체를 버림
            mem = str(cl.get("memorize", ""))[:60]
            if _vacuous(mem, concept):
                mem = ""
            # 근거가 전부 표·그림 문항이면 원본을 봐야 한다고 표시
            vis_only = bool(idxs) and all(items[i - 1]["visual"] for i in idxs)
            out.append({
                "subject": str(cl.get("subject", ""))[:8],
                "concept": concept,
                "wrong_n": cl.get("wrong_n") or sum(items[i - 1]["wrong_n"] for i in idxs),
                "confuse": str(cl.get("confuse", ""))[:120],
                "core": core,
                "trap": str(cl.get("trap", ""))[:120],
                "memorize": mem,
                "needs_original": bool(cl.get("needs_original")) or vis_only,
                "still": any(r["still"] for r in refs),
                "refs": refs[:6],
            })
        out.sort(key=lambda x: (-(1 if x["still"] else 0), -(x["wrong_n"] or 0)))
        data = {"clusters": out, "scanned": len(items), "total_wrong_ever": total_wrong_ever}
        if out:
            _CR_CACHE["sig"], _CR_CACHE["data"] = sig, data
        return self._json(data)

    def _weakspots(self):
        """오답노트 기반 반복 취약 유형 분석(AI) — 과목별로 자주 틀리는 개념을 묶어 검색 키워드까지 제시."""
        c = db()
        rows = c.execute("""SELECT a.* FROM attempts a
                JOIN (SELECT question_id,qnum,MAX(id) mi FROM attempts WHERE source='quiz' GROUP BY question_id,qnum) l
                  ON a.question_id=l.question_id AND a.qnum=l.qnum AND a.id=l.mi AND a.source='quiz'
                WHERE a.correct=0 ORDER BY a.subject, a.ts DESC""").fetchall()
        pages, items = {}, []
        for w in rows:
            qid = w["question_id"]
            if qid not in pages:
                pr = c.execute("SELECT raw_text FROM questions WHERE id=?", (qid,)).fetchone()
                pages[qid] = {it["qnum"]: it for it in parse_items(pr["raw_text"])} if pr else {}
            it = pages[qid].get(w["qnum"], {})
            stem = (it.get("stem") or "").replace("\n", " ").strip()[:90]
            if stem:
                items.append({"subject": w["subject"], "qid": qid, "qnum": w["qnum"], "stem": stem})
        c.close()
        if len(items) < 2:
            return self._json({"clusters": [], "empty": True})
        lines = [f"#{i+1} [{it['subject']}] {it['stem']}" for i, it in enumerate(items)]
        prompt = ("아래는 검정고시 수험생이 최근 틀린 문제들의 발문입니다(앞에 #번호). "
                  "학생이 '반복해서' 틀리는 개념·유형을 과목별로 찾아 묶어주세요. "
                  "한 유형에 문항이 2개 이상 모일 때만 포함하고(1개뿐이면 제외), 과목당 최대 3개 유형만 뽑으세요.\n"
                  "각 유형마다: subject(과목명), topic(간결한 한국어 이름, 12자 이내, 예: '충격량과 운동량', '통일신라의 통치체제'), "
                  "keywords(그 유형의 다른 기출을 찾을 때 검색할 핵심 단어 3~5개, 실제 문제 발문에 등장할 만한 단어. "
                  "단, '설명', '것은', '문제', '다음', '가장', '적절', '경우', '이유', '방법', '역할', '특징' 같은 범용 단어는 "
                  "절대 넣지 말고, 그 유형에서만 나올 법한 구체적인 용어(전문 명사·고유명사)만 넣으세요), "
                  "indices(해당하는 문항의 #번호 목록)를 담아 JSON 배열로만 출력하세요. 다른 설명 없이 JSON만.\n"
                  '형식 예: [{"subject":"과학","topic":"충격량과 운동량","keywords":["충격량","운동량","F·t"],"indices":[1,4]}]\n\n'
                  + "\n".join(lines))
        try:
            txt = ai_complete("당신은 검정고시 학습 코치입니다. 데이터에 근거해 정확하게 분석합니다.",
                              [{"role": "user", "content": prompt}], 900)
        except Exception as e:
            return self._json({"error": "ai_failed", "detail": str(e)}, 502)
        if txt is None:
            return self._json({"error": "no-ai"}, 503)
        try:
            clusters = json.loads(re.sub(r'^```(?:json)?\s*|\s*```$', '', txt.strip()))
        except Exception:
            return self._json({"error": "ai_parse", "raw": txt[:300]}, 502)
        out = []
        for cl in (clusters if isinstance(clusters, list) else []):
            idxs = [i for i in cl.get("indices", []) if isinstance(i, int) and 1 <= i <= len(items)]
            if len(idxs) < 2:
                continue
            refs = [{"question_id": items[i - 1]["qid"], "qnum": items[i - 1]["qnum"]} for i in idxs]
            out.append({"subject": str(cl.get("subject", "")), "topic": str(cl.get("topic", ""))[:20],
                        "keywords": [str(k)[:12] for k in (cl.get("keywords") or [])][:5],
                        "count": len(refs), "refs": refs})
        return self._json({"clusters": out})

    def _recap(self):
        """지금까지의 기록 회고: 학습일수·누적시간·정답률·되찾은 오답."""
        c = db()
        days = c.execute("SELECT COUNT(DISTINCT date) n FROM study_log WHERE (math+sci+kor+nc2)>0").fetchone()["n"]
        total_min = c.execute("SELECT SUM(math+sci+kor+nc2) s FROM study_log").fetchone()["s"] or 0
        rows = c.execute("SELECT question_id,qnum,correct,ts FROM attempts WHERE source='quiz' ORDER BY ts").fetchall()
        c.close()
        groups = {}
        for r in rows:
            groups.setdefault((r["question_id"], r["qnum"]), []).append(r["correct"])
        ever_wrong = sum(1 for v in groups.values() if 0 in v)
        recovered = sum(1 for v in groups.values() if 0 in v and v[-1] == 1)
        return self._json({"study_days": days, "total_minutes": round(total_min),
                           "total_attempts": len(rows), "total_correct": sum(r["correct"] for r in rows),
                           "recovered": recovered, "ever_wrong": ever_wrong})

    def _replay(self):
        """지금까지의 기록 전체를 되돌아보는 리플레이용 통계 묶음."""
        c = db()
        g = lambda q, *a: c.execute(q, a).fetchone()
        days = g("SELECT COUNT(DISTINCT date) n FROM study_log WHERE (math+sci+kor+nc2)>0")["n"] or 0
        mins = g("SELECT SUM(math+sci+kor+nc2) s FROM study_log")["s"] or 0
        subj_min = g("SELECT SUM(math) m,SUM(sci) s,SUM(kor) k,SUM(nc2) n FROM study_log") or {}
        best_day = g("""SELECT date, (math+sci+kor+nc2) t FROM study_log
                        WHERE t>0 ORDER BY t DESC LIMIT 1""")
        first = g("SELECT MIN(date) d FROM study_log WHERE (math+sci+kor+nc2)>0")["d"]
        att = c.execute("SELECT question_id,qnum,subject,correct,ts FROM attempts WHERE source='quiz' ORDER BY ts").fetchall()
        rounds_done = g("SELECT COUNT(DISTINCT question_id) n FROM attempts WHERE source='quiz'")["n"] or 0
        # 과목별 정답률
        by_sub = {}
        for r in c.execute("""SELECT a.subject s, COUNT(*) n, SUM(a.correct) ok FROM attempts a
                              JOIN (SELECT question_id,qnum,MAX(id) mi FROM attempts WHERE source='quiz' GROUP BY question_id,qnum) l
                                ON a.id=l.mi GROUP BY a.subject"""):
            by_sub[r["s"]] = {"n": r["n"], "ok": r["ok"] or 0,
                              "pct": round((r["ok"] or 0) / r["n"] * 100) if r["n"] else 0}
        # 시간대 분포 (언제 공부했나)
        hours = {r["h"]: r["n"] for r in c.execute(
            "SELECT CAST(strftime('%H', ts,'unixepoch','localtime') AS INT) h, COUNT(*) n "
            "FROM attempts WHERE source='quiz' GROUP BY h")}
        # 가장 오래 붙잡은 문항
        boss = g("""SELECT question_id qid, qnum, subject, COUNT(*) tries,
                           SUM(CASE WHEN correct=0 THEN 1 ELSE 0 END) miss
                      FROM attempts GROUP BY question_id,qnum
                     ORDER BY miss DESC, tries DESC LIMIT 1""")
        focus = g("SELECT SUM(focused_sec) f, COUNT(*) n, SUM(distract_cnt) d FROM focus_log")
        exams = [dict(r) for r in c.execute("SELECT date,avg FROM exam_results ORDER BY ts")]
        study_days = [r["date"] for r in c.execute(
            "SELECT date FROM study_log WHERE (math+sci+kor+nc2)>0 ORDER BY date")]
        c.close()
        # 되찾은 오답 · 최장 연속일
        groups = {}
        for r in att:
            groups.setdefault((r["question_id"], r["qnum"]), []).append(r["correct"])
        ever_wrong = sum(1 for v in groups.values() if 0 in v)
        recovered = sum(1 for v in groups.values() if 0 in v and v[-1] == 1)
        import datetime as _dt
        streak = best_streak = 0
        prev = None
        for d in study_days:
            try:
                cur = _dt.date.fromisoformat(d)
            except ValueError:
                continue
            streak = streak + 1 if (prev and (cur - prev).days == 1) else 1
            best_streak = max(best_streak, streak)
            prev = cur
        night = sum(n for h, n in hours.items() if h is not None and (h >= 22 or h < 4))
        morning = sum(n for h, n in hours.items() if h is not None and 5 <= h < 11)
        total_att = len(att)
        peak_h = max(hours.items(), key=lambda x: x[1])[0] if hours else None
        subj_kr = {"m": "수학", "s": "과학", "k": "국어", "n": "사회·한국사·도덕"}
        top_subject = None
        if subj_min:
            pairs = [(subj_kr[k], round(subj_min[k] or 0)) for k in ("m", "s", "k", "n")]
            pairs.sort(key=lambda x: -x[1])
            if pairs and pairs[0][1] > 0:
                top_subject = {"name": pairs[0][0], "minutes": pairs[0][1], "all": pairs}
        return self._json({
            "days": days, "minutes": round(mins), "first_day": first,
            "since_days": (_dt.date.today() - _dt.date.fromisoformat(first)).days + 1 if first else 0,
            "best_day": dict(best_day) if best_day else None,
            "best_streak": best_streak,
            "attempts": total_att, "correct": sum(r["correct"] for r in att),
            "acc": round(sum(r["correct"] for r in att) / total_att * 100) if total_att else 0,
            "rounds": rounds_done, "by_subject": by_sub, "top_subject": top_subject,
            "ever_wrong": ever_wrong, "recovered": recovered,
            "boss": dict(boss) if boss and boss["tries"] else None,
            "night": night, "morning": morning, "peak_hour": peak_h,
            "focus_min": round((focus["f"] or 0) / 60) if focus else 0,
            "focus_sessions": (focus["n"] or 0) if focus else 0,
            "exams": exams,
        })

    def _result_letter(self, b):
        """실전 채점 결과를 받아 그 자리에서 수호령이 반응 편지를 씀(AI). 실패 시 프론트가 고정 편지로 대체."""
        avg = b.get("avg")
        if not isinstance(avg, (int, float)):
            return self._json({"error": "bad_input"}, 400)
        passed = bool(b.get("pass"))
        detail = b.get("detail") or {}
        wrong_lines = [f"{subj} {d.get('wrong')}문항 오답(총 {d.get('total')}문항 중)"
                       for subj, d in detail.items() if isinstance(d, dict) and d.get("wrong")]
        prompt = (
            "너는 RPG '동반 수호령'이야. 검정고시(고졸) 시험을 막 마치고 실전 채점을 끝낸 수험생 '수영'에게 "
            "결과를 보자마자 바로 편지를 써주는 상황이야. 반말로, 따뜻하지만 솔직하게. 판타지 톤은 아주 가볍게만 섞어.\n"
            f"오늘 결과: 평균 {avg}점(영어 100점 포함 7과목 평균), 합격선(60점) {'통과' if passed else '미달'}.\n"
            + ("과목별 오답: " + ", ".join(wrong_lines) if wrong_lines else "전 과목 만점이야!") + "\n"
            "수영의 배경: 재응시생. 목표는 평균 98점 이상(성공회대 사회융합학부 교과 지원 가능선), "
            "마지노선은 94점(열린인재 학종 지원 가능선). 원래 과학·도덕·수학이 취약 과목이었어.\n"
            "이 결과를 있는 그대로 받아들이게 하되, 오늘 시험장에서 끝까지 버텨낸 것 자체를 먼저 인정해주고, "
            "이 점수가 앞으로(교과/열린인재 지원)에 어떤 의미인지 솔직하지만 다정하게 말해줘. "
            "6~10문장 정도, 편지 형식으로('수영에게'로 시작해서 줄바꿈, '— 수호령'으로 끝)."
        )
        try:
            txt = ai_complete("당신은 검정고시 학생의 동반자 '수호령'입니다. 반말로 진심을 담아 편지를 씁니다.",
                              [{"role": "user", "content": prompt}], 900)
        except Exception:
            txt = None
        if txt:
            return self._json({"letter": txt.strip(), "ai": True})
        return self._json({"letter": None, "ai": False})

    def _full_report(self):
        """모든 로그(정답률·반복오답·오답원인·실전성적 추이·학습시간·화상스터디 집중기록)를 종합한 AI 리포트."""
        c = db()
        latest = """SELECT a.* FROM attempts a
            JOIN (SELECT question_id,qnum,MAX(id) mi FROM attempts WHERE source='quiz' GROUP BY question_id,qnum) l
              ON a.question_id=l.question_id AND a.qnum=l.qnum AND a.id=l.mi AND a.source='quiz'"""
        rows = [dict(r) for r in c.execute(latest)]
        if len(rows) < 3:
            c.close(); return self._json({"empty": True})
        wc = {}
        for r in c.execute("SELECT question_id,qnum,SUM(CASE WHEN correct=0 THEN 1 ELSE 0 END) wc FROM attempts GROUP BY question_id,qnum"):
            wc[(r["question_id"], r["qnum"])] = r["wc"]
        stats = {}
        for r in rows:
            s = stats.setdefault(r["subject"], {"total": 0, "correct": 0, "wrong": 0, "repeat": 0, "tags": {}})
            s["total"] += 1
            if r["correct"]:
                s["correct"] += 1
            else:
                s["wrong"] += 1
                if wc.get((r["question_id"], r["qnum"]), 0) >= 2:
                    s["repeat"] += 1
                if r["tag"]:
                    s["tags"][r["tag"]] = s["tags"].get(r["tag"], 0) + 1
        exams = [dict(r) for r in c.execute("SELECT date,avg FROM exam_results ORDER BY ts")]
        study = c.execute("SELECT SUM(math) m,SUM(sci) s,SUM(kor) k,SUM(nc2) n FROM study_log").fetchone()
        study_days = c.execute("SELECT COUNT(DISTINCT date) n FROM study_log WHERE (math+sci+kor+nc2)>0").fetchone()["n"]
        focus = c.execute("SELECT SUM(focused_sec) f, SUM(distract_cnt) d, COUNT(*) n FROM focus_log").fetchone()
        c.close()
        import datetime as _dt
        dday = (_dt.date(2026, 8, 11) - _dt.date.today()).days
        lines = [f"시험까지 D-{dday}."]
        for subj, s in stats.items():
            acc = round(s["correct"] / s["total"] * 100) if s["total"] else 0
            tagtxt = ", ".join(f"{k} {v}건" for k, v in s["tags"].items()) or "태그 없음"
            lines.append(f"[{subj}] 정답률 {acc}%({s['correct']}/{s['total']}) · 반복오답(2회 이상 틀림) {s['repeat']}개 · 오답 원인: {tagtxt}")
        if exams:
            lines.append("실전 채점 성적 추이: " + " → ".join(f"{e['date']} {e['avg']}점" for e in exams))
        smin = round((study["m"] or 0) + (study["s"] or 0) + (study["k"] or 0) + (study["n"] or 0))
        lines.append(f"누적 학습시간(타이머 기록) {smin}분 · 학습일수 {study_days}일")
        if focus and focus["n"]:
            lines.append(f"화상 스터디(수호령의 눈) {focus['n']}회 · 총 집중시간 {round((focus['f'] or 0)/60)}분 · 딴짓 감지 {focus['d']}회")
        prompt = ("당신은 검정고시 학습 코치입니다. 아래는 학생의 전체 학습 기록(정답률·반복오답·오답 원인 태그·"
                  "실전 채점 성적 추이·누적 학습시간·화상 스터디 집중 기록)입니다. 이 데이터를 모두 종합해 "
                  "학생에게 도움이 되는 총체적 분석 리포트를 작성하세요.\n\n"
                  "구성:\n[총평] 전체 현재 상태·강점·가장 시급한 개선점·남은 기간을 고려한 종합 조언을 4~6문장으로.\n"
                  "그다음 데이터가 있는 과목마다 [과목명] 형태로: 현재 수준(정답률 인용), 구체적 취약 패턴(반복오답·오답원인 데이터 근거로), "
                  "지금 당장 해야 할 것 2~3가지.\n"
                  "실제 수치를 인용해 구체적으로 쓰고, 데이터에 없는 내용은 지어내지 마세요. 존댓말로.\n\n"
                  + "\n".join(lines))
        try:
            txt = ai_complete("당신은 검정고시 학습 코치입니다. 데이터에 근거해 상세하고 실행 가능한 분석을 제공합니다.",
                              [{"role": "user", "content": prompt}], 2200)
        except Exception as e:
            return self._json({"error": "ai_failed", "detail": str(e)}, 502)
        if txt is None:
            return self._json({"error": "no-ai"}, 503)
        return self._json({"report": txt.strip(), "generated_at": int(__import__("time").time())})

    def _today(self, date):
        """하루 마감 리포트: 오늘 푼 문항·정답률·과목별·해결한 반복오답."""
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date or ""):
            return self._json({"error": "bad_date"}, 400)
        c = db()
        rows = [dict(r) for r in c.execute(
            "SELECT * FROM attempts WHERE date(ts,'unixepoch','localtime')=? AND source='quiz'", (date,))]
        study = c.execute("SELECT math,sci,kor,nc2 FROM study_log WHERE date=?", (date,)).fetchone()
        # 오늘 이전에 틀렸다가 오늘 맞힌 문항(=회복)
        fixed = c.execute("""SELECT COUNT(*) n FROM (
              SELECT a.question_id qid,a.qnum qn FROM attempts a
              WHERE a.correct=1 AND date(a.ts,'unixepoch','localtime')=? AND source='quiz'
              AND EXISTS (SELECT 1 FROM attempts b WHERE b.question_id=a.question_id AND b.qnum=a.qnum
                          AND b.correct=0 AND b.ts<a.ts)
              GROUP BY a.question_id,a.qnum)""", (date,)).fetchone()["n"]
        # 연속 학습일: 오늘(또는 어제)부터 거꾸로 끊기지 않은 날 수
        days = {r["d"] for r in c.execute(
            """SELECT DISTINCT date(ts,'unixepoch','localtime') d FROM attempts WHERE source='quiz'
               UNION SELECT date FROM study_log WHERE (math+sci+kor+nc2)>0""")}
        c.close()
        import datetime as _dt
        base = _dt.date.fromisoformat(date)
        streak, cur = 0, base
        if base.isoformat() not in days:
            cur = base - _dt.timedelta(days=1)     # 오늘 아직 안 했으면 어제부터 세되 유지로 인정
        while cur.isoformat() in days:
            streak += 1; cur -= _dt.timedelta(days=1)
        by = {}
        for r in rows:
            s = by.setdefault(r["subject"], {"total": 0, "correct": 0})
            s["total"] += 1; s["correct"] += r["correct"] or 0
        total = len(rows); correct = sum(r["correct"] or 0 for r in rows)
        mins = sum(round(study[k] or 0) for k in ("math", "sci", "kor", "nc2")) if study else 0
        return self._json({"date": date, "total": total, "correct": correct,
                           "acc": round(correct / total * 100) if total else 0,
                           "by_subject": by, "fixed": fixed, "minutes": mins,
                           "streak": streak, "today_done": date in days})

    def _subject_exam_meta(self, subject):
        """해당 과목 기출에서 (1)실제 선지 묶음 (2)빈출 용어를 추출 → 기출 수준의 퀴즈 재료."""
        c = db()
        rows = c.execute("SELECT raw_text FROM questions WHERE subject=? AND raw_text IS NOT NULL", (subject,)).fetchall()
        c.close()
        groups, freq = [], {}
        seen = set()
        for r in rows:
            for it in parse_items(r["raw_text"]):
                texts = [ch.get("text", "").strip() for ch in it.get("choices", [])]
                texts = [t for t in texts if 1 <= len(t) <= 24]
                if len(texts) == 4:                     # 4지선다 원본 보기 묶음
                    k = tuple(sorted(texts))
                    if k not in seen:
                        seen.add(k); groups.append(texts)
                for t in texts:                          # 선지 = 실제 출제 개념
                    freq[t] = freq.get(t, 0) + 1
        STOP = ("것은", "것을", "적절", "옳은", "않은", "다음", "설명", "고른", "보기", "가장", "무엇",
                "내용", "용어", "들어갈", "대한", "이유", "한다", "이다", "모두")
        freq = {k: v for k, v in freq.items() if 2 <= len(k) <= 20 and not any(s in k for s in STOP)}
        top = sorted(freq.items(), key=lambda x: -x[1])[:400]
        return {"groups": groups, "freq": dict(top)}

    def _diagnose(self, q):
        """오답노트 기반 과목별 취약점 상세 진단(통계 + AI 코칭)."""
        c = db()
        latest = """SELECT a.* FROM attempts a
            JOIN (SELECT question_id,qnum,MAX(id) mi FROM attempts WHERE source='quiz' GROUP BY question_id,qnum) l
              ON a.question_id=l.question_id AND a.qnum=l.qnum AND a.id=l.mi AND a.source='quiz'"""
        rows = [dict(r) for r in c.execute(latest)]
        if not rows:
            c.close(); return self._json({"empty": True, "stats": []})
        rep = {(r["question_id"], r["qnum"]): r["wc"] for r in c.execute(
            "SELECT question_id,qnum,SUM(CASE WHEN correct=0 THEN 1 ELSE 0 END) wc FROM attempts GROUP BY question_id,qnum")}
        stats, wrongs = {}, {}
        for r in rows:
            s = stats.setdefault(r["subject"], {"subject": r["subject"], "total": 0, "correct": 0, "wrong": 0,
                                                "repeat": 0, "unknown": 0, "rounds": {}, "tags": {}})
            if not r["correct"] and r.get("tag"):
                s["tags"][r["tag"]] = s["tags"].get(r["tag"], 0) + 1
            s["total"] += 1
            rk = f'{r["year"]} {r["exam_round"]}'
            rr = s["rounds"].setdefault(rk, {"total": 0, "wrong": 0})
            rr["total"] += 1
            if r["correct"]:
                s["correct"] += 1
            else:
                s["wrong"] += 1; rr["wrong"] += 1
                if r["chosen"] == 0:
                    s["unknown"] += 1
                if rep.get((r["question_id"], r["qnum"]), 0) >= 2:
                    s["repeat"] += 1
                wrongs.setdefault(r["subject"], []).append(r)
        for s in stats.values():
            s["acc"] = round(s["correct"] / s["total"] * 100) if s["total"] else 0
            s["rounds"] = [dict(k=k, **v) for k, v in sorted(s["rounds"].items())]
        out = sorted(stats.values(), key=lambda x: (x["acc"], -x["wrong"]))
        if not q.get("ai"):
            c.close(); return self._json({"stats": out})
        # AI 상세 진단: 과목별 오답 발문 모아 전달
        pages, lines = {}, []
        for subj, ws in wrongs.items():
            lines.append(f"[{subj}] 정답률 {stats[subj]['acc']}% · 오답 {stats[subj]['wrong']}/{stats[subj]['total']} · 반복오답 {stats[subj]['repeat']}")
            for w in ws[:12]:
                qid = w["question_id"]
                if qid not in pages:
                    pr = c.execute("SELECT raw_text FROM questions WHERE id=?", (qid,)).fetchone()
                    pages[qid] = {it["qnum"]: it for it in parse_items(pr["raw_text"])} if pr else {}
                it = pages[qid].get(w["qnum"], {})
                stem = (it.get("stem") or "").replace("\n", " ").strip()[:80]
                lines.append(f"  - {w['qnum']}번: {stem} (내 답 {'모름' if w['chosen']==0 else str(w['chosen'])+'번'}, 정답 {w['answer']}번)")
        c.close()
        prompt = ("아래는 검정고시(고졸) 수험생의 과목별 오답 데이터입니다. 시험이 얼마 남지 않았습니다.\n"
                  "과목마다 다음을 구체적으로 진단해 주세요:\n"
                  "1) 취약한 단원·개념 (틀린 문제들의 공통 주제를 묶어서)\n"
                  "2) 틀리는 원인 유형 (개념 미숙지 / 자료·그래프 해석 / 계산 실수 / 헷갈리는 개념 혼동 / 찍음)\n"
                  "3) 지금 당장 해야 할 학습 처방 (우선순위 순으로 2~3개, 구체적으로)\n"
                  "과목 제목은 [과목명] 형태로 쓰고, 마지막에 [총평]으로 남은 기간 공부 우선순위를 정리해 주세요.\n\n"
                  + "\n".join(lines))
        try:
            txt = ai_complete("당신은 검정고시 학습 코치입니다. 데이터에 근거해 구체적이고 실행 가능한 진단을 존댓말로 제공합니다.",
                              [{"role": "user", "content": prompt}], 1600)
        except Exception as e:
            return self._json({"stats": out, "error": "ai_failed", "detail": str(e)}, 502)
        if txt is None:
            return self._json({"stats": out, "error": "no-ai"}, 503)
        return self._json({"stats": out, "diagnosis": txt.strip()})

    # -- POST --
    def do_POST(self):
        p = urllib.parse.urlparse(self.path).path
        b = self._body()
        if p == "/api/setup":
            if has_seal():
                return self._json({"error": "already_setup"}, 400)
            pin = str(b.get("pin", ""))
            if not re.fullmatch(r"\d{4}", pin):
                return self._json({"error": "bad_pin"}, 400)
            set_seal(pin)
            return self._json({"ok": True}, cookie=self._cookie(new_session()))
        if p == "/api/unlock":
            if verify_seal(str(b.get("pin", ""))):
                return self._json({"ok": True}, cookie=self._cookie(new_session()))
            return self._json({"error": "bad_pin"}, 401)
        if p == "/api/relock":
            drop_session(self._tok())
            return self._json({"ok": True}, cookie="sid=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict")
        if p == "/api/state":
            if self._guard():
                return
            c = db(); c.execute("INSERT OR REPLACE INTO app_state(id,json) VALUES(1,?)", (json.dumps(b.get("state", {}), ensure_ascii=False),)); c.commit(); c.close()
            return self._json({"ok": True})
        if p == "/api/resultletter":
            if self._guard():
                return
            return self._result_letter(b)
        if p == "/api/live/token":
            if self._guard():
                return
            return self._live_token()
        if p == "/api/ai":
            if self._guard():
                return
            try:
                txt = ai_complete(b.get("system", ""), b.get("messages", []), b.get("max_tokens", 700))
            except Exception as e:
                return self._json({"error": "ai_failed", "detail": str(e)}, 502)
            if txt is None:
                return self._json({"error": "no-ai"}, 503)
            return self._json({"text": txt})
        if p == "/api/study":
            if self._guard():
                return
            date = str(b.get("date", ""))
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
                return self._json({"error": "bad_date"}, 400)
            m = b.get("mins", {})
            vals = [float(m.get(k, 0) or 0) for k in ("math", "sci", "kor", "nc2")]
            c = db()
            c.execute("""INSERT INTO study_log(date,math,sci,kor,nc2) VALUES(?,?,?,?,?)
                         ON CONFLICT(date) DO UPDATE SET math=excluded.math,sci=excluded.sci,kor=excluded.kor,nc2=excluded.nc2""",
                      (date, *vals))
            c.commit(); c.close()
            return self._json({"ok": True})
        if p == "/api/examresult":
            if self._guard():
                return
            import time as _t
            date = str(b.get("date", ""))
            c = db()
            c.execute("INSERT INTO exam_results(date,year,exam_round,level,json,avg,ts) VALUES(?,?,?,?,?,?,?)",
                      (date, b.get("year", ""), b.get("exam_round", ""), b.get("level", ""),
                       json.dumps(b.get("detail", {}), ensure_ascii=False), float(b.get("avg", 0) or 0), int(_t.time())))
            c.commit(); c.close()
            return self._json({"ok": True})
        if p == "/api/upload":
            if self._guard():
                return
            return self._upload(b)
        if p == "/api/attempt":
            if self._guard():
                return
            return self._attempt(b)
        if p == "/api/attempts":
            if self._guard():
                return
            return self._attempt_batch(b)
        if p == "/api/wrong/clear":
            if self._guard():
                return
            c = db(); c.execute("DELETE FROM attempts"); c.commit(); c.close()
            return self._json({"ok": True})
        if p == "/api/wrong/grade":
            if self._guard():
                return
            return self._wrong_grade(b)
        if p == "/api/avatar":
            if self._guard():
                return
            d = b.get("data", "")
            if d and len(d) > 3_500_000:
                return self._json({"error": "too_large"}, 400)
            c = db(); c.execute("INSERT OR REPLACE INTO meta(k,v) VALUES('avatar_img',?)", (d,)); c.commit(); c.close()
            return self._json({"ok": True})
        if p == "/api/focus/check":
            if self._guard():
                return
            return self._focus_check(b)
        if p == "/api/focus/talk":
            if self._guard():
                return
            return self._focus_talk(b)
        if p == "/api/focus/session":
            if self._guard():
                return
            import time as _t
            c = db()
            c.execute("""INSERT INTO focus_log(date,start_ts,end_ts,focused_sec,distract_cnt,checks,transcript)
                         VALUES(?,?,?,?,?,?,?)""",
                      (b.get("date", ""), int(b.get("start", 0) or 0), int(_t.time()),
                       int(b.get("focused", 0) or 0), int(b.get("distract", 0) or 0),
                       int(b.get("checks", 0) or 0), json.dumps(b.get("transcript", []), ensure_ascii=False)))
            c.commit(); c.close()
            return self._json({"ok": True})
        if p == "/api/askimage":
            if self._guard():
                return
            data = b.get("data", "")
            if "," in data:
                data = data.split(",", 1)[1]
            try:
                raw = base64.b64decode(data)
            except Exception:
                return self._json({"error": "bad_base64"}, 400)
            if not raw:
                return self._json({"error": "bad_input"}, 400)
            if raw[:4] == b"%PDF":                      # PDF면 첫 페이지를 이미지로
                try:
                    import fitz
                    d = fitz.open("pdf", raw)
                    raw = d[0].get_pixmap(matrix=fitz.Matrix(1.8, 1.8)).tobytes("png")
                    d.close()
                except Exception as e:
                    return self._json({"error": "pdf_read_failed", "detail": str(e)}, 400)
            ask = (b.get("q") or "").strip()
            prompt = ("당신은 검정고시(고졸) 학습 튜터입니다. 사진 속 문제를 읽고 한국어 존댓말로 답하세요.\n"
                      "1) 무엇을 묻는 문제인지 한 줄 요약\n2) 풀이 과정을 단계별로 쉽게\n3) 정답\n4) 관련 개념 한 줄 정리\n"
                      "사진이 흐리면 읽을 수 있는 부분까지만 설명하세요."
                      + (f"\n학생의 추가 질문: {ask}" if ask else ""))
            try:
                txt = ai_vision(prompt, [raw], 1400, as_json=False)
            except Exception as e:
                return self._json({"error": "ai_failed", "detail": str(e)}, 502)
            if txt is None:
                return self._json({"error": "no-ai"}, 503)
            return self._json({"text": (txt or "").strip()})
        if p == "/api/wrong/tag":
            if self._guard():
                return
            try:
                qid = int(b["question_id"]); qnum = int(b["qnum"])
            except (KeyError, ValueError, TypeError):
                return self._json({"error": "bad_input"}, 400)
            tag = b.get("tag") or None
            if tag not in (None, "concept", "mistake", "confuse", "time", "guess"):
                return self._json({"error": "bad_tag"}, 400)
            c = db()
            c.execute("""UPDATE attempts SET tag=? WHERE id=(SELECT id FROM attempts
                         WHERE question_id=? AND qnum=? AND correct=0 ORDER BY ts DESC LIMIT 1)""",
                      (tag, qid, qnum))
            c.commit(); c.close()
            return self._json({"ok": True, "tag": tag})
        if p == "/api/wrong/adjust":
            if self._guard():
                return
            try:
                qid = int(b["question_id"]); qnum = int(b["qnum"])
            except (KeyError, ValueError, TypeError):
                return self._json({"error": "bad_input"}, 400)
            action = b.get("action", "dec")
            c = db()
            if action == "remove":          # 완전히 제거(오답노트에서 내림)
                c.execute("DELETE FROM attempts WHERE question_id=? AND qnum=?", (qid, qnum))
            else:                           # dec: 오답 1회 차감(가장 최근 오답 기록 삭제)
                r = c.execute("SELECT id FROM attempts WHERE question_id=? AND qnum=? AND correct=0 ORDER BY ts DESC LIMIT 1",
                              (qid, qnum)).fetchone()
                if r:
                    c.execute("DELETE FROM attempts WHERE id=?", (r["id"],))
            c.commit()
            left = c.execute("SELECT SUM(CASE WHEN correct=0 THEN 1 ELSE 0 END) n FROM attempts WHERE question_id=? AND qnum=?",
                             (qid, qnum)).fetchone()["n"] or 0
            c.close()
            return self._json({"ok": True, "wrong_count": left})
        if p in ("/api/questions", "/api/units", "/api/cards", "/api/schedule"):
            return self._admin_write(p.rsplit("/", 1)[1], b, create=True)
        return self._json({"error": "not_found"}, 404)

    def _upload(self, b):
        """기출/정답 PDF 업로드 → 파일명 규격화·저장·텍스트 추출·문항 등록(관리자 전용)."""
        kind = b.get("type")                 # '문제' 또는 '정답'
        year, rnd, level, subj = b.get("year"), b.get("exam_round"), b.get("level"), (b.get("subject") or "")
        data = b.get("data", "")
        # 문제는 과목 필수, 정답(회차 전체 정답표)은 과목 없어도 됨
        if kind not in ("문제", "정답") or not all([year, rnd, level]) or not data or (kind == "문제" and not subj):
            return self._json({"error": "bad_input"}, 400)
        if "," in data:                      # data:...;base64, 접두 제거
            data = data.split(",", 1)[1]
        try:
            raw = base64.b64decode(data)
        except Exception:
            return self._json({"error": "bad_base64"}, 400)
        if raw[:4] != b"%PDF":
            return self._json({"error": "not_pdf"}, 400)
        # 문제 PDF는 pdf/에 규격 파일명으로 저장(이미지 렌더용) + Turso 영속
        pname = f"{year}_{rnd}_{level}_{subj}_문제.pdf"
        tmp = os.path.join(PDF_DIR, "_upload_tmp.pdf")
        os.makedirs(PDF_DIR, exist_ok=True)
        with open(tmp, "wb") as f:
            f.write(raw)
        try:
            text = extract_pdf_text(tmp)
        except ImportError:
            os.remove(tmp); return self._json({"error": "no_fitz"}, 501)
        except Exception as e:
            os.remove(tmp); return self._json({"error": "extract_failed", "detail": str(e)}, 500)
        c = db()
        exist = c.execute("SELECT id FROM questions WHERE year=? AND exam_round=? AND level=? AND subject=?",
                          (year, rnd, level, subj)).fetchone()
        if kind == "문제":
            dst = os.path.join(PDF_DIR, pname)
            os.replace(tmp, dst)
            c.execute("INSERT OR REPLACE INTO pdf_files(name,data) VALUES(?,?)", (pname, base64.b64encode(raw).decode()))
            for k in (os.path.join(PDF_DIR, pname),):   # 새 PDF는 프리컴퓨트 캐시에 없으니 무효화 → 다음 접근 시 재계산
                _qstarts_cache.pop(k, None); _pbreaks_cache.pop(k, None); _qfigs_cache.pop(k, None)
            if exist:
                c.execute("UPDATE questions SET raw_text=? WHERE id=?", (text, exist["id"]))
                qid = exist["id"]
            else:
                cur = c.execute("INSERT INTO questions(year,exam_round,level,subject,raw_text) VALUES(?,?,?,?,?)",
                                (year, rnd, level, subj, text))
                qid = cur.lastrowid
            self._reparse_row(c, qid)       # 정답표가 이미 있으면 qkey 갱신
        else:  # 정답
            os.remove(tmp)
            if subj:
                targets = [exist["id"]] if exist else [c.execute(
                    "INSERT INTO questions(year,exam_round,level,subject,answer_text) VALUES(?,?,?,?,?)",
                    (year, rnd, level, subj, text)).lastrowid]
            else:   # 과목 미지정 = 회차 전체 정답표 → 해당 회차 모든 과목 행에 반영
                targets = [r["id"] for r in c.execute(
                    "SELECT id FROM questions WHERE year=? AND exam_round=? AND level=?", (year, rnd, level))]
                if not targets:
                    # 문제 PDF를 아직 안 올린 회차(예: 방금 치른 시험) → 정답표에 등장하는 과목으로 행을 새로 만든다.
                    # 예전엔 여기서 조용히 0건 처리되고 ok:true가 나가 "정답키가 없어요"만 뜨는 원인이었음
                    seen = []
                    for m in re.finditer(r'([가-힣]{2,4})\s*정답표', text):
                        s = m.group(1)
                        if s in KEY_SUBJECTS and s not in seen:
                            seen.append(s)
                    targets = [c.execute(
                        "INSERT INTO questions(year,exam_round,level,subject,answer_text) VALUES(?,?,?,?,?)",
                        (year, rnd, level, s, text)).lastrowid for s in seen]
            for tid in targets:
                c.execute("UPDATE questions SET answer_text=? WHERE id=?", (text, tid))
                self._reparse_row(c, tid)   # 정답키 재생성
            qid = targets[0] if targets else None
            nkeys = c.execute("SELECT COUNT(*) n FROM qkey WHERE question_id IN (%s)" %
                              (",".join("?" * len(targets)) or "NULL"), targets).fetchone()["n"] if targets else 0
            c.commit(); c.close()
            if not nkeys:   # 성공했다고 표시해놓고 실제론 아무것도 안 들어가던 문제 → 원인을 알려준다
                return self._json({"error": "no_key_parsed", "kind": kind, "chars": len(text),
                                   "detail": ("PDF에서 '○○ 정답표' 형식을 못 찾았어요. 스캔 이미지 PDF면 텍스트가 없어 파싱이 안 돼요."
                                              if not targets else "과목 행은 만들었지만 문항-정답 쌍을 못 읽었어요.")}, 422)
            return self._json({"ok": True, "kind": kind, "updated": len(targets), "chars": len(text), "keys": nkeys})
        nkeys = c.execute("SELECT COUNT(*) n FROM qkey WHERE question_id=?", (qid,)).fetchone()["n"]
        c.commit(); c.close()
        return self._json({"ok": True, "id": qid, "kind": kind, "file": pname if kind == "문제" else None,
                           "chars": len(text), "keys": nkeys})

    def _attempt(self, b):
        try:
            qid = int(b["question_id"]); qnum = int(b["qnum"]); chosen = int(b["chosen"])
        except (KeyError, ValueError, TypeError):
            return self._json({"error": "bad_input"}, 400)
        c = db()
        row = c.execute("SELECT answer FROM qkey WHERE question_id=? AND qnum=?", (qid, qnum)).fetchone()
        meta = c.execute("SELECT subject,year,exam_round FROM questions WHERE id=?", (qid,)).fetchone()
        if not row or not meta:
            c.close(); return self._json({"error": "no_key"}, 404)
        ans = row["answer"]; correct = 1 if chosen == ans else 0
        c.execute("INSERT INTO attempts(question_id,subject,year,exam_round,qnum,chosen,answer,correct,ts,source) VALUES(?,?,?,?,?,?,?,?,?,'quiz')",
                  (qid, meta["subject"], meta["year"], meta["exam_round"], qnum, chosen, ans, correct, int(time.time())))
        c.commit(); c.close()
        return self._json({"correct": bool(correct), "answer": ans})

    @staticmethod
    def _yt_fetch(url):
        """유튜브 페이지 가져오기. 동의 인터스티셜·인증서 문제·일시적 실패에 대비해 재시도."""
        last = None
        for attempt in range(2):
            try:
                req = urllib.request.Request(url, headers={
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                                  "(KHTML, like Gecko) Chrome/122.0 Safari/537.36",
                    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
                    # 데이터센터 IP에 뜨는 쿠키 동의 화면을 건너뛰기 위한 표준 우회
                    "Cookie": "CONSENT=YES+cb.20240101-00-p0.ko+FX+000; SOCS=CAI"})
                ctx = None
                try:
                    import ssl, certifi                      # 인증서 번들이 없는 환경 대비
                    ctx = ssl.create_default_context(cafile=certifi.where())
                except Exception:
                    ctx = None
                return urllib.request.urlopen(req, timeout=12, context=ctx).read().decode("utf-8", "replace")
            except Exception as e:
                last = e
                time.sleep(0.6)
        raise last

    def _yt_lookup(self, q):
        """검정고시마스터 채널에서 '과목+연도+회차'에 맞는 강의 영상 1개를 찾아 videoId 반환.
        (API 키 없이 채널 검색 결과 페이지를 읽어 제목으로 매칭 · 결과는 캐시)"""
        subject = (q.get("subject") or "").strip()
        year = re.sub(r"\D", "", q.get("year") or "")[:4]
        rnd = re.sub(r"\D", "", q.get("round") or "")[:1]
        if subject not in ("국어", "수학", "영어", "사회", "과학", "한국사", "도덕") or not year or not rnd:
            return self._json({"error": "bad_input"}, 400)
        ck = f"{subject}|{year}|{rnd}"
        hit = _YT_CACHE.get(ck)
        # 성공은 오래, 실패는 짧게 캐시 (일시적 실패가 몇 시간 동안 굳지 않도록)
        if hit and time.time() - hit[0] < (21600 if hit[1] else 180):
            return self._json({"video": hit[1], "cached": True})
        query = f"{year} {rnd}회 {subject}"
        url = "https://www.youtube.com/@blackgosimaster/search?query=" + urllib.parse.quote(query)
        try:
            html = self._yt_fetch(url + "&hl=ko&gl=KR")
        except Exception as e:
            return self._json({"error": "fetch_failed", "detail": str(e)[:80]}, 502)
        m = re.search(r"ytInitialData\s*=\s*({.*?})\s*;\s*</script>", html, re.S)
        if not m:
            return self._json({"error": "parse_failed"}, 502)
        try:
            data = json.loads(m.group(1))
        except Exception:
            return self._json({"error": "parse_failed"}, 502)

        def walk(o):
            if isinstance(o, dict):
                v = o.get("videoRenderer")
                if isinstance(v, dict) and v.get("videoId"):
                    runs = (v.get("title") or {}).get("runs") or [{}]
                    yield v["videoId"], (runs[0].get("text") or "")
                for x in o.values():
                    yield from walk(x)
            elif isinstance(o, list):
                for x in o:
                    yield from walk(x)

        best = None
        for i, (vid, title) in enumerate(walk(data)):
            if subject not in title:
                continue                                   # 과목이 안 맞으면 후보에서 제외
            sc = 0
            if year in title or (year[2:] + "년") in title: sc += 4   # '2026년' / '26년' 둘 다 인정
            if f"{rnd}회" in title: sc += 3
            if "고졸" in title: sc += 2
            if "중졸" in title or "초졸" in title: sc -= 6
            sc -= i * 0.01                                  # 동점이면 검색 상위 우선
            if best is None or sc > best[0]:
                best = (sc, vid, title)
        if not best or best[0] < 7:                         # 연도·회차가 둘 다 맞아야 확정
            _YT_CACHE[ck] = (time.time(), None)
            return self._json({"video": None, "query": query})
        out = {"id": best[1], "title": best[2], "score": round(best[0], 2)}
        _YT_CACHE[ck] = (time.time(), out)
        return self._json({"video": out, "query": query})

    # 설명란에서 '자료 파일' 링크만 골라내기 위한 기준
    _FILE_HOSTS = ("drive.google.com", "docs.google.com", "dropbox.com", "1drv.ms",
                   "mega.nz", "naver.me", "blog.naver.com", "cafe.naver.com", "notion.site")
    _SKIP_HOSTS = ("kyobobook", "aladin", "yes24", "mebook.io", "/join", "instagram", "facebook",
                   "youtube.com/channel", "youtu.be", "kakao", "smartstore")
    _FILE_WORDS = ("다운로드", "자료", "파일", "pdf", "PDF", "교안", "필기", "요약", "정리")

    @staticmethod
    def _yt_description(html):
        """설명란 원문 추출. 유튜브가 페이지 구조를 바꿔도 버티도록 패턴을 여러 개 시도."""
        # 1) 플레이어 응답의 shortDescription (가장 정확)
        m = re.search(r'"shortDescription":"((?:[^"\\]|\\.)*)"', html)
        if m:
            try:
                return json.loads('"' + m.group(1) + '"')
            except Exception:
                pass
        # 2) 새 UI의 attributedDescription
        m = re.search(r'"attributedDescription":\{"content":"((?:[^"\\]|\\.)*)"', html)
        if m:
            try:
                return json.loads('"' + m.group(1) + '"')
            except Exception:
                pass
        # 3) 최후: meta description (앞부분만 나오지만 없는 것보단 나음)
        m = re.search(r'<meta name="description" content="([^"]*)"', html)
        if m:
            import html as _h
            return _h.unescape(m.group(1))
        return None

    def _yt_desc(self, q):
        """영상 설명란(더보기)에서 개념 자료 링크만 추출. 파일을 가져오지 않고 원본 링크만 전달."""
        vid = (q.get("id") or "").strip()
        if not re.fullmatch(r"[\w-]{11}", vid):
            return self._json({"error": "bad_input"}, 400)
        hit = _YTD_CACHE.get(vid)
        if hit and time.time() - hit[0] < (21600 if hit[1] else 180):
            return self._json({**(hit[1] or {"files": []}), "cached": True})
        try:
            html = self._yt_fetch(f"https://www.youtube.com/watch?v={vid}&hl=ko&gl=KR")
        except Exception as e:
            return self._json({"error": "fetch_failed", "detail": str(e)[:80]}, 502)
        desc = self._yt_description(html)
        if desc is None:
            _YTD_CACHE[vid] = (time.time(), None)
            return self._json({"files": [], "error": "no_description"})
        files, label = [], ""
        for line in desc.split("\n"):
            t = line.strip()
            if not t:
                continue
            urls = re.findall(r'https?://[^\s<>"\')]+', t)
            if not urls:                                  # URL 없는 줄은 바로 위 제목으로 기억
                label = t[:40]
                continue
            for u in urls:
                low = u.lower()
                if any(sk in low for sk in self._SKIP_HOSTS):
                    continue                              # 교재 구매·채널 가입 링크 제외
                host_ok = any(hh in low for hh in self._FILE_HOSTS)
                label_ok = any(w in label for w in self._FILE_WORDS)
                if host_ok or label_ok:
                    nm = re.sub(r'[\[\]:·\-—]+', ' ', label).strip() or "자료 파일"
                    if not any(f["url"] == u for f in files):
                        files.append({"label": nm[:30], "url": u})
        files = files[:4]
        out = {"files": files, "desc": desc[:4000]}     # 추출 실패 대비: 설명란 원문도 함께
        _YTD_CACHE[vid] = (time.time(), out)
        return self._json(out)

    def _attempt_batch(self, b):
        """여러 문항을 한 요청·한 커밋으로 채점. (기존: 문항마다 요청 → 25문항이면 왕복 25회)"""
        try:
            qid = int(b["question_id"])
        except (KeyError, ValueError, TypeError):
            return self._json({"error": "bad_input"}, 400)
        picks = b.get("picks")
        if not isinstance(picks, dict) or not picks:
            return self._json({"error": "bad_input"}, 400)
        c = db()
        meta = c.execute("SELECT subject,year,exam_round FROM questions WHERE id=?", (qid,)).fetchone()
        if not meta:
            c.close(); return self._json({"error": "not_found"}, 404)
        keys = {r["qnum"]: r["answer"] for r in c.execute("SELECT qnum,answer FROM qkey WHERE question_id=?", (qid,))}
        ts, rows, out = int(time.time()), [], {}
        for k, v in picks.items():
            try:
                qnum, chosen = int(k), int(v)
            except (ValueError, TypeError):
                continue
            ans = keys.get(qnum)
            if ans is None:
                continue
            correct = 1 if chosen == ans else 0
            rows.append((qid, meta["subject"], meta["year"], meta["exam_round"], qnum, chosen, ans, correct, ts))
            out[str(qnum)] = {"correct": bool(correct), "answer": ans}
        if rows:
            c.executemany("""INSERT INTO attempts(question_id,subject,year,exam_round,qnum,chosen,answer,correct,ts,source)
                             VALUES(?,?,?,?,?,?,?,?,?,'quiz')""", rows)
            c.commit()
        c.close()
        return self._json({"results": out, "count": len(rows)})

    def do_PUT(self):
        m = re.match(r"^/api/(questions|units|cards|schedule)/(\d+)$", self.path)
        if not m:
            return self._json({"error": "not_found"}, 404)
        return self._admin_write(m.group(1), self._body(), rid=int(m.group(2)))

    def do_DELETE(self):
        m = re.match(r"^/api/(questions|units|cards|schedule)/(\d+)$", self.path)
        if not m:
            return self._json({"error": "not_found"}, 404)
        if self._guard():
            return
        c = db(); c.execute(f"DELETE FROM {m.group(1)} WHERE id=?", (int(m.group(2)),)); c.commit(); c.close()
        return self._json({"ok": True})

    COLS = {
        "questions": ["year", "exam_round", "level", "subject", "raw_text", "answer_text"],
        "units": ["subject", "title", "body", "ord"],
        "cards": ["q", "a", "ord"],
        "schedule": ["day", "t_start", "t_end", "name", "kind", "ord"],
    }

    def _admin_write(self, table, b, create=False, rid=None):
        if self._guard():
            return
        cols = self.COLS[table]
        c = db()
        if create:
            cur = c.execute(f"INSERT INTO {table}({','.join(cols)}) VALUES({','.join('?' * len(cols))})", [b.get(k) for k in cols])
            rid = cur.lastrowid
            if table == "questions":
                self._reparse_row(c, rid)
            c.commit(); c.close()
            return self._json({"ok": True, "id": rid})
        sets, args = [], []
        for k in cols:
            if k in b:
                sets.append(f"{k}=?"); args.append(b[k])
        if not sets:
            c.close(); return self._json({"error": "no_fields"}, 400)
        args.append(rid)
        c.execute(f"UPDATE {table} SET {','.join(sets)} WHERE id=?", args)
        if table == "questions":
            self._reparse_row(c, rid)
        c.commit(); c.close()
        return self._json({"ok": True})

    def _live_context(self):
        """Live 세션에 넣을 학습 맥락 — 실제 DB에서 뽑아 매 연결마다 최신으로 만든다."""
        c = db()
        lines = []
        try:
            r = c.execute("SELECT date, avg, json FROM exam_results ORDER BY id DESC LIMIT 1").fetchone()
            if r:
                per = ""
                try:
                    d = json.loads(r["json"] or "{}")
                    def one(k, v):
                        s = f'{k} {v.get("score")}점({v.get("correct")}/{v.get("total")})'
                        wl = v.get("wrongList") or []
                        return s + (f'[틀린 문항 {",".join(map(str, wl))}]' if wl else '')
                    per = ", ".join(one(k, v) for k, v in d.items() if isinstance(v, dict))
                except Exception:
                    pass
                lines.append(f'[실전 채점] {r["date"]} 평균 {r["avg"]}점' + (f' — {per}' if per else ''))
            acc = []
            for r in c.execute("""SELECT a.subject s, COUNT(*) n, SUM(a.correct) ok FROM attempts a
                    JOIN (SELECT question_id,qnum,MAX(id) mi FROM attempts GROUP BY question_id,qnum) l
                      ON a.id=l.mi GROUP BY a.subject ORDER BY 1.0*SUM(a.correct)/COUNT(*)"""):
                if r["n"]:
                    acc.append(f'{r["s"]} {round((r["ok"] or 0)/r["n"]*100)}%({r["n"]}문항)')
            if acc:
                lines.append("[과목별 정답률·낮은 순] " + ", ".join(acc))
            wr = []
            for r in c.execute("""SELECT a.subject s, COUNT(*) n FROM attempts a
                    JOIN (SELECT question_id,qnum,MAX(id) mi FROM attempts GROUP BY question_id,qnum) l
                      ON a.id=l.mi WHERE a.correct=0 GROUP BY a.subject ORDER BY n DESC LIMIT 4"""):
                wr.append(f'{r["s"]} {r["n"]}개')
            if wr:
                lines.append("[지금 오답노트에 남은 문항] " + ", ".join(wr))
            f = c.execute("""SELECT COUNT(*) n, SUM(focused_sec) s FROM focus_log
                             WHERE date >= date('now','-7 day')""").fetchone()
            if f and f["n"]:
                lines.append(f'[최근 7일 같이 공부한 시간] {round((f["s"] or 0)/3600,1)}시간 · {f["n"]}세션')
        except Exception:
            pass
        c.close()
        return "\n".join(lines) or "아직 쌓인 학습 기록이 없어."

    def _live_token(self):
        try:
            tok = live_token()
        except Exception as e:
            return self._json({"error": "token_failed", "detail": str(e)[:200]}, 502)
        if not tok:
            return self._json({"error": "no-ai"}, 503)
        system = (
            "너는 '루하', 수영이의 스터디 메이트야. 실시간 음성으로 대화한다.\n"
            "말투: 반말, 친구처럼 따뜻하게. 한 번에 1~2문장만. 음성이니 마크다운·이모지 쓰지 마. "
            "설교하지 말고 잔소리도 짧게. 모르는 건 아는 척하지 말고 모른다고 해.\n\n"
            "[수영이에 대해]\n"
            "2026년 8월 11일 고졸 검정고시를 봤고 이미 끝났다. 영어는 면제(100점 확정). "
            "작년 평균은 85.00이었다.\n"
            "이번 점수는 아래 [지금까지 쌓인 기록]의 실전 채점 값을 그대로 써라. "
            "기록에 없는 점수를 지어내지 말고, 어느 과목이 몇 점이었는지 헷갈리면 모른다고 해라.\n"
            "지금은 성공회대학교 2027학년도 수시를 준비하는 단계다. "
            "검정고시 환산 4등급이라 교과성적전형은 커트(3등급)에 못 미치고, 열린인재전형이 유일한 경로다. "
            "열린인재는 서류 60%(학업수행 35·자기주도성 25·공동체역량 25·성실성 15) + 면접 40%이고, "
            "면접은 2026년 10월 31일이다. 다음 할 일은 학교생활기록부 대체서식 작성. "
            "수영이는 청소년인권 활동 이력이 있고 그게 서류·면접의 핵심 재료다.\n"
            "이미 끝난 시험이니 '공부해라'가 아니라 수시 준비를 같이 하는 쪽으로 대화해라.\n\n"
            "[지금까지 쌓인 기록]\n" + self._live_context())
        return self._json({"token": tok, "model": LIVE_MODEL, "system": system, "voices": LIVE_VOICES})

    def _reparse_row(self, c, qid):
        r = c.execute("SELECT subject,answer_text FROM questions WHERE id=?", (qid,)).fetchone()
        c.execute("DELETE FROM qkey WHERE question_id=?", (qid,))
        if r:
            for n, a in parse_key(r["answer_text"], r["subject"]).items():
                c.execute("INSERT OR REPLACE INTO qkey(question_id,qnum,answer) VALUES(?,?,?)", (qid, n, a))


def sync_questions():
    """black_bank.db의 questions를 저장소로 다시 불러오고 정답키 재생성(봉인/상태/지문/카드/일정 보존)."""
    init_db()
    c = db()
    c.pull() if hasattr(c, "pull") else None
    nq = _load_questions(c)
    n_keys = c.execute("SELECT COUNT(*) FROM qkey").fetchone()[0]
    c.commit(); c.close()
    dst = "Turso" if TURSO_URL else "app.db"
    print(f"questions 동기화 완료 — {nq}문항 / 정답키 {n_keys}개 (black_bank.db → {dst})")


def selftest():
    global DB
    DB = os.path.join(HERE, "_selftest.db")
    if os.path.exists(DB):
        os.remove(DB)
    import shutil; shutil.copyfile(SEED_DB, DB)
    init_db()
    c = db(); rebuild_qkey(c); c.commit()
    assert not has_seal()
    set_seal("1234"); assert has_seal()
    assert verify_seal("1234") and not verify_seal("0000"), "seal"
    tok = new_session(); assert session_valid(tok) and not session_valid("nope"), "session"
    drop_session(tok); assert not session_valid(tok), "drop"
    nq = c.execute("SELECT COUNT(*) FROM questions").fetchone()[0]
    nk = c.execute("SELECT COUNT(*) FROM qkey").fetchone()[0]
    ns = c.execute("SELECT COUNT(*) FROM schedule").fetchone()[0]
    assert nq > 0 and nk > 500 and ns == len(SEED_SCHEDULE), (nq, nk, ns)
    # 파서 정합성: 국어 지문/과학 채점키
    row = c.execute("SELECT raw_text FROM questions WHERE subject='국어' LIMIT 1").fetchone()
    items = parse_items(row["raw_text"])
    assert len(items) >= 20 and any(it["passage"] for it in items), "parse_items"
    c.close()
    os.remove(DB)
    print(f"selftest OK — 기출 {nq} · 정답키 {nk} · 일정 {ns} · 파서/보안/세션 검증")


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        selftest(); raise SystemExit
    if "--sync-questions" in sys.argv:
        sync_questions(); raise SystemExit
    port = int(os.environ.get("PORT", 8787))
    # 배포(PORT 지정됨)면 0.0.0.0, 로컬이면 127.0.0.1
    host = os.environ.get("HOST") or ("0.0.0.0" if os.environ.get("PORT") else "127.0.0.1")
    socketserver.ThreadingTCPServer.allow_reuse_address = True
    # 포트를 먼저 열고(=Render가 즉시 감지) 그다음 DB 초기화 → 초기화가 느려도 포트는 열림
    srv = socketserver.ThreadingTCPServer((host, port), H)
    srv.daemon_threads = True
    print(f"[boot] listening on {host}:{port} (turso={'yes' if TURSO_URL else 'no'})", flush=True)
    try:
        init_db()
        c = db(); nq = c.execute("SELECT COUNT(*) FROM questions").fetchone()[0]; c.close()
        print(f"[boot] init_db 완료 — 기출 {nq}문항", flush=True)
    except Exception as e:
        import traceback
        print(f"[boot] init_db 실패: {e}", flush=True); traceback.print_exc()
    if ai_provider() == "gemini":
        print(f"  🧚 수호령: Google AI Studio(Gemini · {os.environ.get('GEMINI_MODEL','gemini-2.0-flash')}) 연결됨", flush=True)
    else:
        print("  (AI 미연결: GEMINI_API_KEY 설정 필요)", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
            pass
