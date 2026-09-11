#!/usr/bin/env python3
"""성공회대 2027 수시 경쟁률·마감일 텔레그램 알림.

- 경쟁률: 진학어플라이 실시간 페이지에서 관심 전형 3개만 뽑아 변동이 있을 때만 보낸다.
- 마감일: 남은 날짜가 정해진 지점(D-3/D-2/D-1/당일)에 닿으면 한 번씩만 보낸다.
표준 라이브러리만 사용. 상태는 같은 폴더의 ipsi_state.json에 남긴다.
"""
import json, os, re, html, sys, urllib.request, urllib.error, urllib.parse, datetime, ssl

RATIO_URL = "https://addon.jinhakapply.com/RatioV1/RatioH/Ratio10910511.html"
HERE = os.path.dirname(os.path.abspath(__file__))
# Actions는 레포에 커밋된 상태를 쓰고, 맥은 자기 상태를 따로 둔다(git 충돌 방지)
STATE = os.environ.get("IPSI_STATE") or os.path.join(HERE, "ipsi_state.json")
# all | ratio | deadline — 둘이 같은 알림을 두 번 보내지 않게 역할을 나눈다
MODE = os.environ.get("ALERT_MODE", "all")


def _secret(env_name, filename):
    """환경변수 우선, 없으면 같은 폴더의 파일에서 읽는다. 값은 절대 출력하지 않는다."""
    v = os.environ.get(env_name, "").strip()
    if v:
        return v
    try:
        with open(os.path.join(HERE, filename), encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""
KST = datetime.timezone(datetime.timedelta(hours=9))

# (표 제목에 들어가는 전형명, 모집단위, 화면에 쓸 이름)
WATCH = [
    ("학생부종합 열린인재", "사회융합학부", "열린인재 · 사회융합"),
    ("교과성적",           "자유전공학부", "교과성적 · 자유전공"),
    ("교과성적",           "사회융합학부", "교과성적 · 사회융합"),
]
# (학교, 항목, 마감일시) — 각 대학 2027 수시 요강에서 확인한 값
DEADLINES = [
    ("성공회대·인천대·인하대", "원서접수 마감", datetime.datetime(2026, 9, 11, 18, 0, tzinfo=KST)),
    ("인하대",   "대체서식 온라인 입력 마감", datetime.datetime(2026, 9, 12, 0, 0, tzinfo=KST)),
    ("인천대",   "서류 PDF 업로드 마감",     datetime.datetime(2026, 9, 15, 18, 0, tzinfo=KST)),
    ("성공회대", "서류 등기우편 마감",       datetime.datetime(2026, 9, 16, 17, 0, tzinfo=KST)),
    ("인하대",   "서류 등기우편 마감",       datetime.datetime(2026, 9, 16, 17, 0, tzinfo=KST)),
    ("성공회대", "열린인재 면접",          datetime.datetime(2026, 10, 31, 9, 0, tzinfo=KST)),
    ("인천대",   "자기추천 1단계 발표",      datetime.datetime(2026, 11, 6, 10, 0, tzinfo=KST)),
    ("성공회대", "합격자 발표",              datetime.datetime(2026, 11, 13, 16, 0, tzinfo=KST)),
    ("인하대",   "1단계 발표 · 면접 시간 공지", datetime.datetime(2026, 11, 17, 10, 0, tzinfo=KST)),
    ("인천대",   "면접 시간 · 장소 발표",    datetime.datetime(2026, 11, 18, 10, 0, tzinfo=KST)),
    ("인천대",   "자기추천 면접",            datetime.datetime(2026, 11, 21, 9, 0, tzinfo=KST)),
    ("인하대",   "면접(사회과학대학)",       datetime.datetime(2026, 11, 21, 9, 0, tzinfo=KST)),
    ("인천대·인하대", "합격자 발표", datetime.datetime(2026, 12, 18, 10, 0, tzinfo=KST)),
    ("전 대학",  "문서등록 시작",            datetime.datetime(2026, 12, 21, 10, 0, tzinfo=KST)),
    ("전 대학",  "문서등록 마감(한 곳만!)",   datetime.datetime(2026, 12, 23, 14, 0, tzinfo=KST)),
    ("전 대학",  "충원 통보 마감",           datetime.datetime(2026, 12, 29, 18, 0, tzinfo=KST)),
]
# 경쟁률 화면에 띄울 기준 마감(원서접수)
MAIN_DL = DEADLINES[0][2]

# 마감까지 남은 '시간' 기준 알림 지점. 가까워질수록 촘촘해진다.
MARKS = [72, 48, 24, 12, 6, 3, 1]

# 진학어플라이 공지 기준:
#  · 평소   4시간마다 갱신(00·04·08·12·16·20시)
#  · 마감일 정오까지 1시간마다 → 이후 갱신 중단, 최종 현황은 집계 후 별도 공지
CLOSE_DAY = MAIN_DL.date()
FREEZE_AT = datetime.datetime.combine(CLOSE_DAY, datetime.time(12, 0), tzinfo=KST)


HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
    "Referer": "https://addon.jinhakapply.com/",
    "Connection": "close",
}


# 예년 최종 확정치 — 성공회대 입시결과(enter.skhu.ac.kr) 3개년.
# 접수 중에는 경쟁률 절대값보다 "작년 최종의 몇 %가 들어왔나"가 판단에 쓸모 있다.
# {키: [(학년도, 모집, 최종지원), ...]}  자유전공은 2026학년도 신설이라 1개년뿐.
HISTORY = {
    "학생부종합 열린인재|사회융합학부": [(2026, 34, 270), (2025, 34, 265), (2024, 34, 238)],
    "교과성적|사회융합학부":            [(2026, 15, 74),  (2025, 37, 213), (2024, 37, 171)],
    "교과성적|자유전공학부":            [(2026, 109, 624)],
}


def history_line(key, applied):
    """작년 최종 대비 진척도 + 3개년 평균 경쟁률."""
    h = HISTORY.get(key)
    if not h:
        return ""
    ly_year, ly_quota, ly_applied = h[0]
    pct = round(applied / ly_applied * 100) if ly_applied else 0
    avg = sum(a / q for _, q, a in h) / len(h)
    extra = f" · {len(h)}년평균 {avg:.2f}:1" if len(h) > 1 else ""
    return f"\n   <i>{ly_year}학년도 최종 {ly_applied}명({ly_applied/ly_quota:.2f}:1) 대비 <b>{pct}%</b>{extra}</i>"


def fetch_plain(url):
    """가벼운 방법. 국내 일반 IP에서는 이걸로 충분하다."""
    ctx = ssl.create_default_context()
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=25, context=ctx) as r:
        return r.read().decode("utf-8", "ignore")


def fetch_browser(url):
    """진짜 크롬으로 연다.

    이 사이트는 Cloudflare 뒤에 있고, 데이터센터 IP(GitHub 러너 등)에서는
    검사 수위가 올라가 TLS 지문까지 본다. urllib은 파이썬이라는 게 지문에서
    드러나 막히므로, 실제 브라우저 엔진으로 받아야 통과한다.
    """
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(args=[
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox", "--disable-dev-shm-usage",
        ])
        ctx = browser.new_context(
            locale="ko-KR", timezone_id="Asia/Seoul",
            user_agent=HEADERS["User-Agent"],
            viewport={"width": 1440, "height": 900},
            extra_http_headers={"Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8"},
        )
        # navigator.webdriver 흔적 제거 — 자동화 탐지의 1순위 지표
        ctx.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});")
        page = ctx.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            # Cloudflare 자바스크립트 챌린지가 걸리면 표가 늦게 나타난다
            try:
                page.wait_for_selector("table", timeout=20000)
            except Exception:
                page.wait_for_timeout(6000)
            return page.content()
        finally:
            browser.close()


def fetch_and_parse(url):
    """가벼운 방법으로 받아 파싱해보고, 원하는 표가 안 나오면 브라우저로 다시 받는다.

    Cloudflare 안내 페이지에도 <table>이 들어있을 수 있어, 태그 존재가 아니라
    '실제로 파싱되는가'로 판정해야 한다.
    """
    notes = []
    for name, getter in (("plain", fetch_plain), ("browser", fetch_browser)):
        try:
            page = getter(url)
        except Exception as e:
            notes.append(f"{name}={type(e).__name__}:{str(e)[:60]}")
            continue
        data, asof = parse(page)
        if data:
            if name == "browser":
                print("브라우저로 통과")
            return data, asof, ""
        head = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", page)).strip()[:90]
        notes.append(f"{name}={len(page)}자/표0 «{head}»")
    return {}, "", " | ".join(notes)


def cells(row):
    out = [re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", c))).strip()
           for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.S)]
    return [c for c in out if c]


def parse(page):
    """{(전형, 모집단위): (모집, 지원, 경쟁률)} 과 기준시각을 돌려준다.

    표 바로 앞 N자를 보는 방식은 브라우저가 DOM을 재직렬화하면 어긋난다.
    문서 전체에서 '○○ 경쟁률 현황'의 위치와 <table>의 위치를 각각 모은 뒤,
    각 표에 가장 가까운 앞쪽 제목을 붙인다.
    """
    asof = ""
    m = re.search(r"(\d{4}-\d{2}-\d{2}[^<]{0,20}현황)", page)
    if m:
        asof = m.group(1).strip()

    # 태그를 '같은 길이의 공백'으로 바꿔야 원본과 문자 위치가 그대로 유지된다
    flat = re.sub(r"<[^>]+>", lambda mm: " " * len(mm.group(0)), page)
    titles = [(mm.start(), re.sub(r"\s+", " ", mm.group(1)).strip())
              for mm in re.finditer(r"([가-힣()·][가-힣()·\s]{0,29})\s*경쟁률\s*현황", flat)]

    data = {}
    for tm in re.finditer(r"<table[^>]*>(.*?)</table>", page, re.S):
        # 이 표 앞에 있는 제목 중 가장 가까운 것
        before = [t for pos, t in titles if pos < tm.start()]
        jeonhyeong = before[-1] if before else ""
        for row in re.findall(r"<tr[^>]*>(.*?)</tr>", tm.group(1), re.S):
            c = cells(row)
            if len(c) >= 4 and c[0].endswith("학부"):
                try:
                    data[(jeonhyeong, c[0])] = (int(c[1]), int(c[2]), c[3])
                except ValueError:
                    pass
    return data, asof


def load():
    try:
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save(st):
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=1, sort_keys=True)


def _api(method, **payload):
    token = _secret("TG_BOT_TOKEN", "ipsi_bot_token.txt")
    if not token:
        return None
    body = urllib.parse.urlencode(
        {k: (json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v)
         for k, v in payload.items()}).encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/{method}", data=body)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            print(f"텔레그램 {method} 실패: HTTP {e.code} "
                  f"{json.loads(e.read().decode()).get('description','')}")
        except Exception:
            print(f"텔레그램 {method} 실패: HTTP {e.code}")
    except Exception as e:
        print(f"텔레그램 {method} 실패:", type(e).__name__, str(e)[:120])
    return None


def send(text, done_tag=None):
    """done_tag가 있으면 '완료' 버튼을 붙인다. 누르면 그 항목 알림이 멎는다."""
    token = _secret("TG_BOT_TOKEN", "ipsi_bot_token.txt")
    chat = _secret("TG_CHAT_ID", "ipsi_chat_id.txt")
    if not token or not chat:
        print("[dry-run] 토큰/채팅ID 없음 — 전송 생략\n" + text)
        return False
    payload = {"chat_id": chat, "text": text,
               "parse_mode": "HTML", "disable_web_page_preview": "true"}
    if done_tag:
        payload["reply_markup"] = {"inline_keyboard": [[
            {"text": "✅ 완료했어요 (알림 끄기)", "callback_data": "done|" + done_tag[:55]}]]}
    res = _api("sendMessage", **payload)
    if res and not res.get("ok"):
        print("텔레그램 거절:", res.get("description"))
    return bool(res and res.get("ok"))


def collect_done(st):
    """채널에서 누른 '완료' 버튼과 '완료 <항목>' 메시지를 읽어 done 목록에 넣는다."""
    done = set(st.get("done", []))
    res = _api("getUpdates", offset=st.get("upd_offset", 0), timeout=0)
    if not res or not res.get("ok"):
        return done
    for u in res.get("result", []):
        st["upd_offset"] = u["update_id"] + 1
        cq = u.get("callback_query")
        if cq:
            data = cq.get("data", "")
            if data.startswith("done|"):
                done.add(data[5:])
                _api("answerCallbackQuery", callback_query_id=cq["id"],
                     text="알림을 껐어요")
                print("완료 처리:", data[5:])
            continue
        txt = ((u.get("message") or u.get("channel_post") or {}).get("text") or "").strip()
        if txt.startswith("완료"):
            key = txt[2:].strip()
            if key:
                done.add(key)
                print("완료 처리(문자):", key)
    return done


APP_URL = os.environ.get("IPSI_APP_URL", "https://jisik-tower.onrender.com")


def app_sync(payload=None):
    """학습포털과 완료 현황·경쟁률을 주고받는다. 키가 없으면 조용히 건너뛴다."""
    key = _secret("IPSI_SYNC_KEY", "ipsi_sync_key.txt")
    if not key:
        return None
    data = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
    req = urllib.request.Request(APP_URL.rstrip("/") + "/api/ipsi", data=data,
                                 method="POST" if data else "GET",
                                 headers={"X-Ipsi-Key": key, "Content-Type": "application/json",
                                          "User-Agent": "ipsi-alert"})
    try:
        with urllib.request.urlopen(req, timeout=75) as r:   # Render 무료 플랜 콜드스타트 대비
            return json.loads(r.read().decode())
    except Exception as e:
        print("앱 동기화 실패:", type(e).__name__, str(e)[:100])
        return None


def main():
    now = datetime.datetime.now(KST)
    st = load()
    lines, changed = [], False

    # ---- 경쟁률 ----
    if MODE == "deadline" or now.date() > CLOSE_DAY:
        data, asof, err = {}, "", ""          # 마감 다음 날부터는 조회할 것이 없다
    else:
        data, asof, err = fetch_and_parse(RATIO_URL)
    if err:
        print("경쟁률 조회 실패:", err)
    prev = st.get("ratio", {})
    prev_asof = st.get("asof", "")
    # 이 페이지는 4시간마다(00·04·08·12·16·20시) 갱신된다. 숫자가 안 늘어도
    # 새 회차가 나오면 "그대로"인 것 자체가 정보라 보낸다.
    fresh = bool(asof) and asof != prev_asof
    cur = {}
    for jh, unit, label in WATCH:
        v = data.get((jh, unit))
        if not v:
            continue
        quota, applied, rate = v
        key = f"{jh}|{unit}"
        cur[key] = [quota, applied, rate]
        old = prev.get(key)
        if old is None:
            delta = ""
        elif old[1] == applied:
            delta = '  <i>변동 없음</i>'
        else:
            d = applied - old[1]
            delta = f"  <b>{'+' if d > 0 else ''}{d}</b>"
        if not old or old[1] != applied:
            changed = True
        lines.append(f"· {label}\n   {applied}/{quota}명 · <b>{rate}</b>{delta}"
                     + history_line(key, applied))

    msgs = []
    deadline_msgs = []
    if lines and (fresh or changed):
        head = f"📊 <b>성공회대 수시 경쟁률</b>\n<i>{html.escape(asof)}</i>\n\n"
        left = MAIN_DL - now
        tail = f"\n\n⏳ 원서접수 마감까지 <b>{left.days}일 {left.seconds // 3600}시간</b>" if left.total_seconds() > 0 else ""
        msgs.append(head + "\n".join(lines) + tail)

    # ---- 마감일 ----
    sent = set(st.get("deadline_sent", []))
    done = collect_done(st) if MODE != "ratio" else set(st.get("done", []))
    # 학습포털이 완료 현황의 기준. 텔레그램에서 이번에 새로 누른 것만 앱에 더하고,
    # 앱에서 체크/해제한 것은 그대로 따른다(앱에서 체크하면 알림도 멎는다).
    if MODE != "ratio":
        tg_new = done - set(st.get("done", []))
        remote = app_sync()
        if remote is not None and isinstance(remote.get("done"), list):
            done = set(remote["done"]) | tg_new
            if tg_new:
                app_sync({"done_add": sorted(tg_new)})
    st["done"] = sorted(done)
    for school, item, when in (DEADLINES if MODE != "ratio" else []):
        key = f"{school}|{item}"
        if when < now or key in done:          # 이미 지났거나 완료 처리된 항목
            continue
        hrs_left = (when - now).total_seconds() / 3600
        hit = next((t for t in MARKS if hrs_left <= t), None)
        if hit is None:
            continue
        tag = f"{key}|{hit}h"
        if tag in sent:
            continue
        sent.add(tag)
        left = when - now
        h, m = int(left.total_seconds() // 3600), int(left.total_seconds() % 3600 // 60)
        when_s = when.strftime("%m월 %d일 %H시").lstrip("0")
        urgency = "🚨" if hrs_left <= 6 else ("⚠️" if hrs_left <= 24 else "🔔")
        remain = f"<b>{h}시간 {m}분</b>" if h < 48 else f"{left.days}일 {left.seconds // 3600}시간"
        deadline_msgs.append((
            f"{urgency} <b>{school} · {item}</b>\n\n"
            f"{when_s}까지\n남은 시간 {remain}", key))

    # 갱신이 멈추는 구간을 미리 알려두지 않으면, 조용한 게 고장인지 정상인지 알 수 없다
    notices = st.get("notices", [])
    if now >= FREEZE_AT and "freeze" not in notices:
        notices.append("freeze")
        msgs.append("🔒 <b>경쟁률 갱신 중단</b>\n\n"
                    "마감일 정오까지만 1시간 단위로 갱신돼요. "
                    "지금부터 마감(18:00)까지는 숫자가 그대로 멈춰 있어요.\n"
                    "최종 현황은 대학이 집계 후 따로 공지합니다.")
    if now >= MAIN_DL and "closed" not in notices:
        notices.append("closed")
        snap = "\n".join(f"· {l}: {prev.get(f'{j}|{u}', ['?','?'])[1]}명"
                          for j, u, l in WATCH) if prev else ""
        msgs.append("🏁 <b>원서접수 마감</b>\n\n"
                    f"마지막으로 확인된 수치\n{snap}\n\n"
                    "실제 최종 경쟁률은 대학 집계 후 공지돼요.")
    st["notices"] = notices

    if cur and MODE != "deadline":
        app_sync({"ratio": cur, "asof": asof})   # 학습포털 경쟁률 패널 갱신
    for m in msgs:
        send(m)
    for text, key in deadline_msgs:
        send(text, done_tag=key)
    if cur:
        st["ratio"] = cur
        st["asof"] = asof          # 실패했을 때 직전값을 지우지 않는다
    st["deadline_sent"] = sorted(sent)
    st["updated"] = now.isoformat(timespec="seconds")
    save(st)
    print(f"보낸 메시지 {len(msgs)}건 · 감시 {len(cur)}개 · {asof}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        import traceback
        traceback.print_exc()
        # 알림 도구가 죽었다고 워크플로 전체를 실패로 만들지 않는다
        sys.exit(0)
