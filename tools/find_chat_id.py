#!/usr/bin/env python3
"""텔레그램 채널 ID 찾기. 토큰은 파일/환경변수에서만 읽고 화면에 절대 찍지 않는다.

토큰 넣는 곳 (둘 중 하나)
  · tools/ipsi_bot_token.txt   (.gitignore에 등록돼 있음)
  · 환경변수 TG_BOT_TOKEN
"""
import json, os, sys, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
TOKEN_FILE = os.path.join(HERE, "ipsi_bot_token.txt")


def token():
    t = os.environ.get("TG_BOT_TOKEN", "").strip()
    if t:
        return t
    try:
        with open(TOKEN_FILE, encoding="utf-8") as f:
            return f.read().strip()
    except FileNotFoundError:
        return ""


def main():
    tk = token()
    if not tk:
        print("토큰이 없어요. 아래 중 하나로 넣어주세요.\n")
        print(f"  1) 파일에 저장   {TOKEN_FILE}")
        print("     (이 파일은 .gitignore에 있어 커밋되지 않습니다)")
        print("  2) 환경변수      export TG_BOT_TOKEN='...'")
        return 1
    try:
        with urllib.request.urlopen(
                f"https://api.telegram.org/bot{tk}/getUpdates", timeout=20) as r:
            d = json.loads(r.read().decode())
    except Exception as e:
        # 예외 메시지에 토큰이 섞인 URL이 들어갈 수 있어 그대로 찍지 않는다
        print("요청 실패:", type(e).__name__, "— 토큰이 맞는지 확인해주세요.")
        return 1

    if not d.get("ok"):
        print("텔레그램이 거절했어요:", d.get("description", "(사유 불명)"))
        return 1

    found = {}
    for u in d.get("result", []):
        for key in ("channel_post", "message", "edited_channel_post", "my_chat_member"):
            chat = (u.get(key) or {}).get("chat")
            if chat:
                found[chat["id"]] = (chat.get("type", "?"),
                                     chat.get("title") or chat.get("username") or "(제목없음)")
    if not found:
        print("아직 받은 업데이트가 없어요.")
        print("  · 봇을 채널 '관리자'로 넣었는지 확인")
        print("  · 봇을 넣은 '뒤에' 채널에 메시지를 새로 하나 올리고 다시 실행")
        return 1

    print("찾은 채팅:\n")
    for cid, (typ, title) in found.items():
        star = "  ← 이걸 IPSI_TG_CHAT_ID 에 넣으세요" if typ == "channel" else ""
        print(f"  {cid}    [{typ}] {title}{star}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
