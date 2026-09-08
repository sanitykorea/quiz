#!/usr/bin/env python3
"""성공회대 2027 수시 경쟁률·마감일 텔레그램 알림.

- 경쟁률: 진학어플라이 실시간 페이지에서 관심 전형 3개만 뽑아 변동이 있을 때만 보낸다.
- 마감일: 남은 날짜가 정해진 지점(D-3/D-2/D-1/당일)에 닿으면 한 번씩만 보낸다.
표준 라이브러리만 사용. 상태는 같은 폴더의 ipsi_state.json에 남긴다.
"""
import json, os, re, html, sys, urllib.request, urllib.parse, datetime, ssl

RATIO_URL = "https://addon.jinhakapply.com/RatioV1/RatioH/Ratio10910511.html"
STATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ipsi_state.json")
KST = datetime.timezone(datetime.timedelta(hours=9))

# (표 제목에 들어가는 전형명, 모집단위, 화면에 쓸 이름)
WATCH = [
    ("학생부종합 열린인재", "사회융합학부", "열린인재 · 사회융합"),
    ("교과성적",           "자유전공학부", "교과성적 · 자유전공"),
    ("교과성적",           "사회융합학부", "교과성적 · 사회융합"),
]
DEADLINES = [
    ("원서접수 마감", datetime.datetime(2026, 9, 11, 18, 0, tzinfo=KST)),
    ("서류제출 마감", datetime.datetime(2026, 9, 16, 17, 0, tzinfo=KST)),
]
MARKS = [3, 2, 1, 0]     # D-3 / D-2 / D-1 / 당일


def fetch(url):
    ctx = ssl.create_default_context()
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=25, context=ctx) as r:
        return r.read().decode("utf-8", "ignore")


def cells(row):
    out = [re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", c))).strip()
           for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.S)]
    return [c for c in out if c]


def parse(page):
    """{(전형, 모집단위): (모집, 지원, 경쟁률)} 과 기준시각을 돌려준다."""
    asof = ""
    m = re.search(r"(\d{4}-\d{2}-\d{2}[^<]{0,20}현황)", page)
    if m:
        asof = m.group(1).strip()
    data = {}
    for tbl in re.findall(r"<table[^>]*>(.*?)</table>", page, re.S):
        idx = page.find(tbl)
        before = re.sub(r"<[^>]+>", " ", page[max(0, idx - 400):idx])
        title = re.sub(r"\s+", " ", html.unescape(before)).strip()
        m = re.search(r"([가-힣()·\s]+?)\s*경쟁률 현황\s*$", title)
        if not m:
            continue
        jeonhyeong = m.group(1).strip()
        for row in re.findall(r"<tr[^>]*>(.*?)</tr>", tbl, re.S):
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


def send(text):
    token, chat = os.environ.get("TG_BOT_TOKEN"), os.environ.get("TG_CHAT_ID")
    if not token or not chat:
        print("[dry-run] 토큰/채팅ID 없음 — 전송 생략\n" + text)
        return False
    body = urllib.parse.urlencode({
        "chat_id": chat, "text": text,
        "parse_mode": "HTML", "disable_web_page_preview": "true"}).encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=body)
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode()).get("ok", False)


def main():
    now = datetime.datetime.now(KST)
    st = load()
    lines, changed = [], False

    # ---- 경쟁률 ----
    try:
        data, asof = parse(fetch(RATIO_URL))
    except Exception as e:
        print("경쟁률 조회 실패:", e)
        data, asof = {}, ""
    prev = st.get("ratio", {})
    cur = {}
    for jh, unit, label in WATCH:
        v = data.get((jh, unit))
        if not v:
            continue
        quota, applied, rate = v
        key = f"{jh}|{unit}"
        cur[key] = [quota, applied, rate]
        old = prev.get(key)
        delta = ""
        if old and old[1] != applied:
            d = applied - old[1]
            delta = f"  <b>{'+' if d > 0 else ''}{d}</b>"
        if not old or old[1] != applied:
            changed = True
        lines.append(f"· {label}\n   {applied}/{quota}명 · <b>{rate}</b>{delta}")

    msgs = []
    if lines and changed:
        head = f"📊 <b>성공회대 수시 경쟁률</b>\n<i>{html.escape(asof)}</i>\n\n"
        left = DEADLINES[0][1] - now
        tail = f"\n\n⏳ 원서접수 마감까지 <b>{left.days}일 {left.seconds // 3600}시간</b>" if left.total_seconds() > 0 else ""
        msgs.append(head + "\n".join(lines) + tail)

    # ---- 마감일 ----
    sent = set(st.get("deadline_sent", []))
    for name, when in DEADLINES:
        days = (when.date() - now.date()).days
        if when < now or days not in MARKS:
            continue
        tag = f"{name}|D-{days}"
        if tag in sent:
            continue
        sent.add(tag)
        left = when - now
        when_s = when.strftime("%m월 %d일 %H시")
        urgency = "🚨" if days == 0 else ("⚠️" if days <= 1 else "🔔")
        msgs.append(f"{urgency} <b>{name} D-{days}</b>\n\n"
                    f"{when_s}까지\n남은 시간 <b>{left.days}일 {left.seconds // 3600}시간</b>")

    for m in msgs:
        send(m)
    st["ratio"] = cur
    st["deadline_sent"] = sorted(sent)
    st["updated"] = now.isoformat(timespec="seconds")
    save(st)
    print(f"보낸 메시지 {len(msgs)}건 · 감시 {len(cur)}개 · {asof}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
