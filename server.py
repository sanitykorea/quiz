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
import http.server, socketserver, sqlite3, json, hashlib, secrets, os, time, urllib.request, urllib.error, urllib.parse, re
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

SEED_UNITS = [
    ("과학", "광합성", "식물은 잎의 **엽록체**에서 **빛에너지**를 흡수하여 물과 이산화탄소를 원료로 **포도당**과 **산소**를 만든다. 이 과정을 광합성이라 하며, 주로 낮에 활발하게 일어난다."),
    ("사회", "삼권분립", "국가 권력을 **입법부**(법 제정), **행정부**(법 집행), **사법부**(법 적용·심판)로 나누어 서로 **견제와 균형**을 이루게 함으로써 권력의 집중과 남용을 막는 원리이다."),
    ("국어", "비유법", "표현하려는 **원관념**을 다른 대상인 **보조관념**에 빗대는 방법이다. '~같이/처럼'을 쓰면 **직유법**, 'A는 B이다' 형태로 직접 연결하면 **은유법**이다."),
]
SEED_CARDS = [
    ("엽록체의 기능은?", "빛에너지를 흡수해 광합성이 일어나는 세포 소기관."),
    ("삼권분립의 목적은?", "권력의 집중·남용을 막고 국민의 자유와 권리를 보호 (견제와 균형)."),
    ("은유법이란?", "'A는 B이다'처럼 원관념을 보조관념에 직접 빗대어 표현하는 방법."),
    ("국민주권의 원리란?", "국가 권력의 정당성이 국민에게서 나온다는 민주주의의 기본 원리."),
    ("옴의 법칙(식)은?", "전류 I = 전압 V ÷ 저항 R."),
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
    """)
    if "answer_text" not in [r["name"] for r in c.execute("PRAGMA table_info(questions)")]:
        c.execute("ALTER TABLE questions ADD COLUMN answer_text TEXT")
    if not c.execute("SELECT 1 FROM questions LIMIT 1").fetchone():
        _load_questions(c)                        # 첫 실행: 기출 42문항 + 정답키 적재
    if not c.execute("SELECT 1 FROM units LIMIT 1").fetchone():
        for i, (s, t, b) in enumerate(SEED_UNITS):
            c.execute("INSERT INTO units(subject,title,body,ord) VALUES(?,?,?,?)", (s, t, b, i))
    if not c.execute("SELECT 1 FROM cards LIMIT 1").fetchone():
        for i, (q, a) in enumerate(SEED_CARDS):
            c.execute("INSERT INTO cards(q,a,ord) VALUES(?,?,?)", (q, a, i))
    if not c.execute("SELECT 1 FROM schedule LIMIT 1").fetchone():
        for i, row in enumerate(SEED_SCHEDULE):
            c.execute("INSERT INTO schedule(day,t_start,t_end,name,kind,ord) VALUES(?,?,?,?,?,?)", (*row, i))
    c.commit()
    c.close()


# ---- 정답표 파서: '○○ 정답표' 제목 앞뒤 '런'을 그 과목 정답으로 배정(과목/문항수 무관) ----
_CDIGIT = {c: i + 1 for i, c in enumerate("①②③④⑤")}

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
            flush(); cur = {'qnum': expected, 's': [mq.group(2)] if mq.group(2) else [], 'c': []}
            expected += 1; i += 1; continue
        if cur is not None:
            (cur['c'] if (any(x in ln for x in _CIRC) or cur['c']) else cur['s']).append(ln)
        i += 1
    flush()
    def split_ch(t):
        p = re.split(r'([①②③④⑤])', t); out = []
        for k in range(1, len(p), 2):
            out.append({"n": _CIRC.index(p[k]) + 1, "text": (p[k + 1] if k + 1 < len(p) else '').strip()})
        return out
    out = []
    for it in items:
        stem = '\n'.join(it['s']).strip()
        choices = split_ch(' '.join(it['c']))
        if not choices:
            choices = [{"n": k, "text": ""} for k in range(1, 5)]
        passage = next((t for a, b, t in passages if a <= it['qnum'] <= b), '')
        blob = stem + ' '.join(x["text"] for x in choices)
        out.append({"qnum": it['qnum'], "stem": stem, "choices": choices,
                    "passage": passage, "image": _pua_ratio(blob) > 0.15})
    return out


def rebuild_qkey(c):
    c.execute("DELETE FROM qkey")
    rows = c.execute("SELECT id,subject,answer_text FROM questions").fetchall()
    keys = [(r["id"], n, a) for r in rows for n, a in parse_key(r["answer_text"], r["subject"]).items()]
    if keys:
        c.executemany("INSERT OR REPLACE INTO qkey(question_id,qnum,answer) VALUES(?,?,?)", keys)   # 배치 삽입(원격 왕복 최소화)


# ---- 이미지 문항: 원본 PDF에서 문항 단위로 잘라 이미지 제공 ----
_qstarts_cache = {}
_pbreaks_cache = {}
_qfigs_cache = {}

def pdf_path(row):
    return os.path.join(PDF_DIR, f"{row['year']}_{row['exam_round']}_{row['level']}_{row['subject']}_문제.pdf")

def _qstarts(path):
    if path in _qstarts_cache:
        return _qstarts_cache[path]
    import fitz
    doc = fitz.open(path)
    qh = re.compile(r'^\s*(\d{1,2})\.\s')
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

def qfigs(path):
    """그림(래스터 이미지/큰 벡터 드로잉)이 포함된 문항 번호 집합."""
    if path in _qfigs_cache:
        return _qfigs_cache[path]
    import fitz
    starts = _qstarts(path); pbreaks = _pbreaks_cache.get(path)
    doc = fitz.open(path)
    figs = set()
    for num in starts:
        pg = doc[starts[num][0]]; rect = _qbox(pg, starts, num, pbreaks)
        foot = pg.rect.height - 62      # 하단 푸터(평가원 로고·교시 표기) 제외
        head = 55                       # 상단 헤더 제외
        def ok(r):
            return r.intersects(rect) and r.y0 > head and r.y1 < foot
        found = False
        for b in pg.get_text("dict")["blocks"]:
            if b.get("type") == 1:
                r = fitz.Rect(b["bbox"])
                if r.width > 28 and r.height > 22 and ok(r):
                    found = True; break
        if not found:
            for d in pg.get_drawings():
                r = d["rect"]
                if r.width > 60 and r.height > 40 and ok(r):
                    found = True; break
        if found:
            figs.add(num)
    doc.close()
    _qfigs_cache[path] = figs
    return figs

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
        return None
    import fitz
    doc = fitz.open(path)
    pg = doc[starts[qnum][0]]
    rect = _qbox(pg, starts, qnum, _pbreaks_cache.get(path))
    rect = fitz.Rect(rect.x0, rect.y0 - 6, rect.x1, rect.y1 - 6)
    pg.get_pixmap(matrix=fitz.Matrix(2.6, 2.6), clip=rect).save(out)
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
    model = os.environ.get("GEMINI_MODEL", "gemini-flash-latest")
    contents = [{"role": ("model" if m["role"] == "assistant" else "user"),
                 "parts": [{"text": m["content"]}]} for m in messages]
    body = json.dumps({
        "system_instruction": {"parts": [{"text": system}]},
        "contents": contents,
        # gemini-flash-latest는 thinking 모델 → 사고에 토큰 소모. 사고 억제(budget) + 답변 여유(+3500)로 긴 답변도 안 잘리게.
        "generationConfig": {"maxOutputTokens": int(max_tokens) + 3500, "thinkingConfig": {"thinkingBudget": 512}},
    }).encode()
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={urllib.parse.quote(key)}"
    req = urllib.request.Request(url, data=body, headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        out = json.load(resp)
    cands = out.get("candidates", [])
    if not cands:
        return ""
    return "".join(p.get("text", "") for p in cands[0].get("content", {}).get("parts", []))

def ai_complete(system, messages, max_tokens=700):
    gk = gemini_key()
    return _gemini(system, messages, max_tokens, gk) if gk else None


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
        if p == "/api/study":
            if self._guard():
                return
            return self._study_get(q.get("today", ""), q.get("weekStart", ""), q.get("periodStart", ""))
        return self._json({"error": "not_found"}, 404)

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
        sql = "SELECT id,year,exam_round,level,subject,raw_text,answer_text FROM questions"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY RANDOM() LIMIT 1" if q.get("random") else " ORDER BY id LIMIT 200"
        c = db(); rows = [dict(x) for x in c.execute(sql, args)]; c.close()
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
        for it in items:
            it["answer"] = key.get(it["qnum"])
            if not has_pdf:
                it["img"] = False
            elif subj == "수학":
                it["img"] = True
            elif figs is not None:
                it["img"] = it["qnum"] in figs
            else:
                it["img"] = it["image"]
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
        rows = c.execute("""
            SELECT a.* FROM attempts a
            JOIN (SELECT question_id,qnum,MAX(ts) mt FROM attempts GROUP BY question_id,qnum) l
              ON a.question_id=l.question_id AND a.qnum=l.qnum AND a.ts=l.mt
            WHERE a.correct=0 ORDER BY a.ts DESC""").fetchall()
        wrong = [dict(r) for r in rows]
        by_subject = {}
        for w in wrong:
            by_subject[w["subject"]] = by_subject.get(w["subject"], 0) + 1
        total = c.execute("SELECT COUNT(DISTINCT question_id||'-'||qnum) n FROM attempts").fetchone()["n"]
        c.close()
        return self._json({"wrong": wrong, "by_subject": by_subject, "answered": total})

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
        if p == "/api/attempt":
            if self._guard():
                return
            return self._attempt(b)
        if p == "/api/wrong/clear":
            if self._guard():
                return
            c = db(); c.execute("DELETE FROM attempts"); c.commit(); c.close()
            return self._json({"ok": True})
        if p in ("/api/questions", "/api/units", "/api/cards", "/api/schedule"):
            return self._admin_write(p.rsplit("/", 1)[1], b, create=True)
        return self._json({"error": "not_found"}, 404)

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
        c.execute("INSERT INTO attempts(question_id,subject,year,exam_round,qnum,chosen,answer,correct,ts) VALUES(?,?,?,?,?,?,?,?,?)",
                  (qid, meta["subject"], meta["year"], meta["exam_round"], qnum, chosen, ans, correct, int(time.time())))
        c.commit(); c.close()
        return self._json({"correct": bool(correct), "answer": ans})

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
