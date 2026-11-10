import os
import re
import json
import hmac as _hmac


class hmac:
    """비밀번호 비교 도우미: 한글이 섞여 있어도 오류 없이 비교한다 (한/영 키를 잘못 눌렀을 때 대비)"""
    @staticmethod
    def compare_digest(a, b):
        return _hmac.compare_digest(str(a).encode("utf-8"), str(b).encode("utf-8"))

import random
import sqlite3
from math import sqrt
from datetime import datetime, timedelta, timezone
from urllib.parse import quote
from functools import wraps

from flask import (Flask, request, redirect, session, render_template_string,
                   g, url_for, Response)
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = "pepero-pair-1111-secret"   # 로그인 유지용 (데모용 값)

# ★ 개발자 페이지 비밀번호: PythonAnywhere WSGI 파일에서 환경변수로 바꾸세요! (주소: /admin)
ADMIN_PW = os.environ.get("PEPERO_ADMIN_PW", "choco1111")

# DB 파일은 이 파일과 같은 폴더에 만든다 (절대경로)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "pepero.db")

GCODE = {"w": "여성", "m": "남성"}              # /join/w , /join/m
INSTA_RE = re.compile(r"^[a-z0-9._]{1,30}$")

# ---- 빼빼로 건네기 / 모의 결제 설정 ----
DEFAULT_MSG = "저희 같이 얘기해봐요!"           # 쪽지를 안 고치면 이 문구가 간다
FREE_GIFTS = 1                                 # 기본 무료 건네기 횟수
PRICE_PER = 1100                               # 1회 가격(원)
PACKAGES = [(1, 1100, ""), (3, 3000, "인기"), (11, 9900, "11:11 특가")]   # (횟수, 가격, 꼬리표)
REPORT_CATS = ["오류가 났어요", "접속이 안 돼요", "민원·건의", "신고하기", "기타"]

# ------------------------------------------------------------
# 설문 보기 (여기만 고치면 문항이 바뀐다)
# ------------------------------------------------------------
FLAVORS = ["오리지널", "아몬드", "누드", "크런키", "화이트쿠키", "딸기"]
EATS = ["초코부터", "과자부터", "한 번에 와구와구", "조금씩 아껴서", "반으로 쪼개 나눠서"]
GIFTS = ["내가 좋아하는 맛", "여러 종류 모음", "직접 만든 빼빼로", "편지와 함께"]
DATES = ["함께 빼빼로 만들기", "영화 보며 나눠 먹기", "빼빼로 게임", "맛 비교 시식회"]
AXES = [("E", "I"), ("N", "S"), ("F", "T"), ("P", "J")]
COAT_LABELS = ["1 담백", "2", "3", "4", "5 진한 달콤"]
EXPR_LABELS = ["1 말수 적음", "2", "3", "4", "5 표현 자주"]
PLAN_LABELS = ["1 계획파", "2", "3", "4", "5 즉흥파"]


# ------------------------------------------------------------
# 빼빼로 아이콘 (이모지에 빼빼로가 없어서 SVG로 직접 그림)
#  - 헤더 로고와 브라우저 탭 아이콘(파비콘)에 쓴다
# ------------------------------------------------------------
def icon_svg(size=26, bg=False):
    back = '<rect width="32" height="32" rx="7" fill="#fff6e5"/>' if bg else ""
    return ('<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 32 32">' % (size, size)
            + back
            + '<rect x="7" y="17" width="4" height="13" rx="2" fill="#e9c58a"/>'
            + '<rect x="6" y="2" width="6" height="19" rx="3" fill="#4a2c1a"/>'
            + '<rect x="8" y="4" width="1.4" height="9" rx="0.7" fill="#7b4b30"/>'
            + '<rect x="21" y="17" width="4" height="13" rx="2" fill="#e9c58a"/>'
            + '<rect x="20" y="2" width="6" height="19" rx="3" fill="#4a2c1a"/>'
            + '<rect x="22" y="4" width="1.4" height="9" rx="0.7" fill="#7b4b30"/>'
            + '</svg>')


ICON_SM = icon_svg(26)
FAVICON = "data:image/svg+xml," + quote(icon_svg(32, bg=True))


def now_kst():
    """서버(UTC)가 아니라 한국 시간으로 기록한다"""
    return (datetime.now(timezone.utc) + timedelta(hours=9)).strftime("%Y-%m-%d %H:%M:%S")


# ------------------------------------------------------------
# DB
# ------------------------------------------------------------
def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS participants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nickname TEXT UNIQUE NOT NULL,
            pin_hash TEXT NOT NULL,
            gender TEXT NOT NULL,
            age INTEGER NOT NULL,
            insta TEXT,
            answers TEXT,
            is_fake INTEGER DEFAULT 0,
            banned INTEGER DEFAULT 0,
            bonus INTEGER DEFAULT 0,
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS gifts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            from_id INTEGER NOT NULL,
            to_id INTEGER NOT NULL,
            message TEXT,
            created_at TEXT,
            UNIQUE(from_id, to_id)
        );
        CREATE TABLE IF NOT EXISTS reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            nickname TEXT,
            category TEXT,
            content TEXT,
            contact TEXT,
            status TEXT DEFAULT 'new',
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS admin_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            action TEXT,
            detail TEXT,
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            nickname TEXT,
            qty INTEGER,
            amount INTEGER,
            created_at TEXT
        );
    """)
    # 이전 버전 DB에는 없는 칸을 자동으로 추가한다
    cols = [r[1] for r in conn.execute("PRAGMA table_info(participants)").fetchall()]
    if "banned" not in cols:
        conn.execute("ALTER TABLE participants ADD COLUMN banned INTEGER DEFAULT 0")
    if "bonus" not in cols:
        conn.execute("ALTER TABLE participants ADD COLUMN bonus INTEGER DEFAULT 0")
    conn.commit()
    conn.close()


init_db()


def go(path, msg=None):
    """메시지를 붙여 이동한다 (한글이 깨지지 않게 인코딩)"""
    if msg:
        path += ("&" if "?" in path else "?") + "m=" + quote(msg)
    return redirect(path)


def clean_insta(raw):
    return (raw or "").strip().lstrip("@").lower()


def is_admin():
    return bool(session.get("admin"))


def log(action, detail=""):
    """개발자 활동 기록 (개발자 페이지의 '로그' 탭에서 본다)"""
    db = get_db()
    db.execute("INSERT INTO admin_logs (action, detail, created_at) VALUES (?,?,?)",
               (action, detail, now_kst()))
    db.commit()


# ------------------------------------------------------------
# 매칭 점수 (한쪽 기준 100점 만점)
# ------------------------------------------------------------
def scale_sim(x, y):
    """1~5점 척도 유사도 (같으면 1.0, 4칸 차이면 0.0)"""
    return 1 - abs(int(x) - int(y)) / 4


def one_way(a, b):
    """a의 기준으로 본 b의 점수와 일치 이유"""
    A, B = a["ans"], b["ans"]
    s = 0.0
    reasons = []

    if A["fav"] == B["fav"]:
        s += 15
        reasons.append("최애 빼빼로가 같아요: " + A["fav"])
    if A["eat"] == B["eat"]:
        s += 10
        reasons.append("먹는 방식이 같아요: " + A["eat"])
    s += 10 * scale_sim(A["coat"], B["coat"])
    if A["gift"] == B["gift"]:
        s += 10
        reasons.append("받고 싶은 선물이 같아요: " + A["gift"])
    if A["date"] == B["date"]:
        s += 10
        reasons.append("하고 싶은 활동이 같아요: " + A["date"])
    s += 10 * scale_sim(A["expr"], B["expr"])
    s += 10 * scale_sim(A["plan"], B["plan"])

    # 이상형 MBTI: a가 원하는 글자를 b가 가졌는가 (any = 상관없음)
    ok = 0
    for i in range(4):
        pref = A["pref"][i]
        if pref == "any" or pref == B["mbti"][i]:
            ok += 1
    s += 15 * ok / 4
    if ok >= 3:
        reasons.append("이상형 MBTI 조건에 %d/4 맞아요" % ok)

    # 나이: b가 a 기준 허용 범위 안인가
    diff = b["age"] - a["age"]
    if diff > 0:
        excess = max(0, diff - int(A["older"]))
    else:
        excess = max(0, -diff - int(A["younger"]))
    s += max(0, 10 - 2 * excess)

    return s, reasons


def pair_index(a, b):
    """빼빼로 지수 = 양쪽 점수의 기하평균 (한쪽만 좋아하면 크게 떨어진다)"""
    s1, r1 = one_way(a, b)
    s2, _ = one_way(b, a)
    return round(sqrt(s1 * s2), 1), r1, round(s1, 1), round(s2, 1)


def row_to_person(r):
    return {
        "id": r["id"],
        "nickname": r["nickname"],
        "gender": r["gender"],
        "age": r["age"],
        "insta": r["insta"],
        "bonus": r["bonus"] or 0,
        "is_fake": r["is_fake"],
        "ans": json.loads(r["answers"]) if r["answers"] else None,
    }


def get_top(me, n=3):
    other_gender = "남성" if me["gender"] == "여성" else "여성"
    rows = get_db().execute(
        "SELECT * FROM participants WHERE gender=? AND answers IS NOT NULL AND banned=0",
        (other_gender,),
    ).fetchall()
    result = []
    for r in rows:
        p = row_to_person(r)
        idx, reasons, s1, s2 = pair_index(me, p)
        result.append({"p": p, "idx": idx, "reasons": reasons, "s1": s1, "s2": s2})
    result.sort(key=lambda x: x["idx"], reverse=True)
    return result[:n], len(rows)


def current_user():
    uid = session.get("uid")
    if not uid:
        return None
    r = get_db().execute("SELECT * FROM participants WHERE id=?", (uid,)).fetchone()
    if not r or r["banned"]:      # 삭제됐거나 차단된 사람은 로그인 상태가 풀린다
        return None
    return row_to_person(r)


def static_url(name):
    """static 폴더에 내가 넣은 그림이 있으면 주소를 돌려주고, 없으면 None
    예) static_url('hero') -> static/hero.png, hero.jpg, hero.jpeg, hero.webp 중 있는 것"""
    for ext in ("png", "jpg", "jpeg", "webp", "ico"):
        fn = "%s.%s" % (name, ext)
        if os.path.exists(os.path.join(BASE_DIR, "static", fn)):
            return url_for("static", filename=fn)
    return None


# ------------------------------------------------------------
# 건네기(빼빼로) 도우미
# ------------------------------------------------------------
def gifts_left(user):
    """남은 건네기 횟수. 개발자 모드면 None(= 무한대)"""
    if is_admin():
        return None
    used = get_db().execute("SELECT COUNT(*) FROM gifts WHERE from_id=?", (user["id"],)).fetchone()[0]
    return max(0, FREE_GIFTS + user["bonus"] - used)


def annotate(me, top):
    """TOP 카드마다 '내가 건넸나 / 상대가 건넸나 / 서로 건넸나'를 붙이고, 받은 빼빼로 목록을 만든다"""
    db = get_db()
    sent = {r["to_id"]: dict(r) for r in db.execute("SELECT * FROM gifts WHERE from_id=?", (me["id"],))}
    recv = {r["from_id"]: dict(r) for r in db.execute("SELECT * FROM gifts WHERE to_id=?", (me["id"],))}
    for t in top:
        pid = t["p"]["id"]
        t["sent"] = sent.get(pid)
        t["recv"] = recv.get(pid)
        t["mutual"] = bool(sent.get(pid) and recv.get(pid))
    top_ids = [t["p"]["id"] for t in top]
    rows = db.execute(
        "SELECT g.from_id, g.message, g.created_at, p.nickname, p.age, p.insta "
        "FROM gifts g JOIN participants p ON p.id=g.from_id "
        "WHERE g.to_id=? AND p.banned=0 ORDER BY g.id DESC", (me["id"],)).fetchall()
    got = []
    for r in rows:
        d = dict(r)
        d["mutual"] = d["from_id"] in sent
        d["in_top"] = d["from_id"] in top_ids
        got.append(d)
    return got


# ------------------------------------------------------------
# 화면 (HTML) - 한 파일에 같이 둔다
#  LAYOUT = 모든 페이지의 겉틀(머리글/색/글꼴/공통 스크립트)
#  SPLASH, ENTER, JOIN, SURVEY, RESULT, SHOP, CHECKOUT, REPORT = 사용자 페이지
#  ADMIN_* = 개발자 페이지
# ------------------------------------------------------------
LAYOUT = """
<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{ title or '빼빼로 PAIR' }}</title>
<link rel="icon" href="{{ favicon }}">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Jua&family=Noto+Sans+KR:wght@400;700&display=swap" rel="stylesheet">
<style>
 /* ===== 색깔은 여기 변수만 바꾸면 전체가 바뀐다 ===== */
 :root { --choco:#4a2c1a; --biscuit:#e9c58a; --cream:#fff6e5; --berry:#e8607c; --red:#d7263d;
         --accent:#d7263d; --bg:#fff6e5; }
 body.t-w { --accent:#e8607c; --bg:#fff0f3; }       /* 여성: 딸기 핑크 */
 body.t-m { --accent:#2f6fa8; --bg:#eef5fb; }       /* 남성: 블루 */
 body.t-admin { --accent:#37474f; --bg:#eceff1; }   /* 개발자: 회색 */

 * { box-sizing: border-box; }
 body { margin:0; background:var(--bg); color:var(--choco); line-height:1.6; font-size:17px;
        font-family:'Noto Sans KR','Malgun Gothic','Apple SD Gothic Neo',sans-serif; }
 h1,h2,h3,.logo { font-family:'Jua','Noto Sans KR',sans-serif; font-weight:400; }
 h1,h2,h3 { margin-top:0; }
 a { color:var(--accent); }

 /* 개발자 모드일 때만 나타나는 검은 바와 주황 테두리 */
 .devbar { position:sticky; top:0; z-index:60; background:#111; color:#ffb74d; padding:8px 14px;
           display:flex; flex-wrap:wrap; gap:10px; align-items:center; font-size:14px; }
 .devbar b { background:#ff9800; color:#111; padding:2px 8px; border-radius:6px; letter-spacing:1px; }
 .devbar a { color:#ffe0b2; text-decoration:none; }
 .devbar .sp { flex:1; }
 body.dev::after { content:''; position:fixed; inset:0; border:5px solid #ff9800; pointer-events:none; z-index:55; }

 header { background:var(--choco); color:var(--cream); padding:12px 18px; display:flex;
          justify-content:space-between; align-items:center; border-bottom:4px solid var(--biscuit); }
 header a { color:var(--biscuit); text-decoration:none; font-size:15px; }
 header .logo { font-size:22px; color:var(--cream); display:flex; align-items:center; gap:6px; }
 header .logo small { color:var(--biscuit); font-size:14px; }
 header img { height:28px; }
 header .right { display:flex; gap:12px; align-items:center; flex-wrap:wrap; justify-content:flex-end; }
 header .home { border:1px solid var(--biscuit); padding:3px 12px; border-radius:16px; font-weight:bold; }
 main { max-width:620px; margin:0 auto; padding:18px 16px 90px; }
 main.wide { max-width:1000px; }
 footer { text-align:center; font-size:13px; opacity:.7; padding:0 0 30px; }

 .card { background:#fff; border-radius:18px; padding:20px; margin-bottom:16px;
         box-shadow:0 3px 10px rgba(74,44,26,.12); }
 .hero { text-align:center; border:3px solid var(--choco); background:linear-gradient(180deg,#fff 0%,#fff3dc 100%); }
 .hero h1 { font-size:31px; line-height:1.3; margin:8px 0 6px; }
 .hero-img { max-width:100%; max-height:260px; border-radius:14px; margin-bottom:6px; }
 .lead { font-size:18px; margin:6px 0 16px; }
 .eyebrow { letter-spacing:3px; font-size:12px; color:var(--accent); font-weight:bold; margin:0; }
 .notice { border:2px dashed var(--accent); }
 .small { font-size:13px; opacity:.75; }
 label { display:block; margin:12px 0 4px; font-weight:bold; }
 input[type=text], input[type=password], input[type=number], select, textarea {
    width:100%; padding:10px; border:2px solid var(--biscuit); border-radius:10px; font-size:16px;
    font-family:inherit; background:#fff; color:var(--choco); }
 textarea { min-height:110px; }
 button, .btn { background:var(--accent); color:#fff; border:0; padding:12px 22px; border-radius:24px;
        font-size:16px; cursor:pointer; text-decoration:none; display:inline-block; margin-top:14px;
        font-family:inherit; }
 .btn.sub, button.sub { background:var(--choco); }
 .btn.insta { background:linear-gradient(45deg,#f9a03f,#e1306c,#833ab4); }
 .btn.dng, button.dng { background:#b71c1c; }
 .btn.sm, button.sm { padding:5px 12px; font-size:13px; margin-top:0; }
 .row2 { display:grid; grid-template-columns:1fr 1fr; gap:10px; }
 .axis { display:grid; grid-template-columns:1fr 1fr; gap:8px; }
 .pwrow { display:flex; gap:8px; align-items:center; }
 .eye { margin-top:0; padding:10px 14px; border-radius:10px; background:var(--biscuit); color:var(--choco);
        user-select:none; -webkit-user-select:none; white-space:nowrap; font-size:14px; }

 /* 메시지 (몇 초 뒤 사라짐) */
 .msg { background:#ffe3e8; border-left:5px solid var(--red); padding:10px 14px; border-radius:8px;
        margin-bottom:14px; transition:opacity .4s; }

 /* 선택지를 둥근 버튼처럼 */
 .opts { display:flex; flex-wrap:wrap; gap:8px; margin-top:4px; }
 .chip { position:relative; margin:0; font-weight:normal; }
 .chip input { position:absolute; opacity:0; width:1px; height:1px; }
 .chip span { display:inline-block; padding:8px 14px; border:2px solid var(--biscuit); border-radius:20px; cursor:pointer; background:#fff; }
 .chip input:checked + span { background:var(--accent); border-color:var(--accent); color:#fff; }
 .chip input:focus-visible + span { outline:3px solid var(--biscuit); }

 /* 11:11 빼빼로 막대 장식 (CSS로만 그림) */
 .sticks { display:flex; justify-content:center; align-items:flex-end; gap:10px; margin:4px 0 12px; }
 .sticks i { display:block; width:16px; height:100px; border-radius:9px 9px 4px 4px;
             background:linear-gradient(to top,var(--biscuit) 0 30%,var(--choco) 30% 100%);
             animation:bob 2.4s ease-in-out infinite; }
 .sticks i:nth-child(2) { animation-delay:.2s; } .sticks i:nth-child(4) { animation-delay:.4s; }
 .sticks i:nth-child(5) { animation-delay:.6s; }
 .sticks b { font-family:'Jua',sans-serif; font-size:46px; line-height:1; padding-bottom:34px; }
 @keyframes bob { 50% { transform:translateY(-8px); } }

 /* 성별 입구 */
 .gate { display:grid; grid-template-columns:1fr 1fr; gap:12px; }
 .gate a { text-decoration:none; text-align:center; padding:22px 8px; border-radius:18px; color:#fff;
           display:flex; flex-direction:column; gap:4px; }
 .gate .gw { background:#e8607c; } .gate .gm { background:#2f6fa8; }
 .gate b { font-family:'Jua',sans-serif; font-size:26px; font-weight:400; }
 .gate .ic { font-size:34px; }

 /* 결과 */
 .rank { font-size:22px; font-weight:bold; color:var(--accent); }
 .score { font-size:34px; font-weight:bold; }
 .gauge { display:flex; align-items:center; margin:12px 0 4px; }
 .stick { flex:1; height:20px; border-radius:10px; min-width:40px; }
 .stick.l { background:linear-gradient(to right,var(--biscuit) 0 28%,var(--choco) 28% 100%); }
 .stick.r { background:linear-gradient(to left,var(--biscuit) 0 28%,var(--choco) 28% 100%); }
 .gap { height:2px; border-bottom:3px dotted var(--berry); transition:width .5s; }
 ul { padding-left:20px; margin:8px 0; }
 .mutual { background:#fff3dc; border:2px solid var(--accent); border-radius:12px; padding:10px 14px; margin-top:10px; }
 .sentbox { background:#f3f3f3; border-radius:12px; padding:10px 14px; margin-top:10px; }
 .quote { background:#fff9ee; border-left:4px solid var(--biscuit); padding:6px 10px; border-radius:6px; margin:6px 0; }
 .recv { border-top:1px solid #eee; padding:8px 0; }
 .ad { display:block; text-decoration:none; color:var(--choco); border:2px solid var(--biscuit);
       background:linear-gradient(90deg,#fff3dc,#ffe3e8); border-radius:14px; padding:14px; margin-bottom:16px; }
 .ad .tag { background:#999; color:#fff; font-size:11px; padding:1px 6px; border-radius:4px; margin-right:6px; }
 .tag { background:var(--accent); color:#fff; font-size:12px; padding:1px 8px; border-radius:10px; }
 .price { font-size:24px; font-weight:bold; }
 .pack { display:flex; justify-content:space-between; align-items:center; gap:10px; }
 .pack form { display:contents; }
 .sim { background:#fff8e1; border:2px dashed #f9a825; border-radius:12px; padding:10px 14px; margin-bottom:14px; font-size:15px; }
 .fab { position:fixed; right:16px; bottom:16px; z-index:40; background:var(--choco); color:var(--cream);
        padding:10px 16px; border-radius:24px; text-decoration:none; font-size:14px; box-shadow:0 3px 10px rgba(0,0,0,.3); }

 /* 팝업 */
 .modal { position:fixed; inset:0; background:rgba(0,0,0,.55); z-index:100; display:flex; align-items:center; justify-content:center; padding:16px; }
 .modal[hidden] { display:none; }
 .modal-box { background:#fff; border-radius:18px; padding:22px; max-width:420px; width:100%; text-align:center; }
 .modal-box p { white-space:pre-line; }
 .modal-btns { display:flex; gap:10px; justify-content:center; }
 .ad-in { background:linear-gradient(90deg,#fff3dc,#ffe3e8); border-radius:12px; padding:12px; margin:10px 0; }

 /* 개발자 화면 */
 .tabs { display:flex; flex-wrap:wrap; gap:6px; margin-bottom:14px; }
 .tabs a { padding:8px 14px; background:#fff; border-radius:20px; text-decoration:none; color:#37474f; border:2px solid #cfd8dc; }
 .tabs a.on { background:#37474f; color:#fff; border-color:#37474f; }
 .badge { background:#d32f2f; color:#fff; border-radius:10px; padding:0 7px; margin-left:6px; font-size:12px; }
 .stats { display:grid; grid-template-columns:repeat(auto-fill,minmax(120px,1fr)); gap:10px; }
 .stat { background:#f5f7f8; border-radius:12px; padding:12px; text-align:center; }
 .stat b { display:block; font-size:26px; }
 .menu-grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(200px,1fr)); gap:12px; margin-bottom:16px; }
 .menu-grid a { background:#fff; border-radius:14px; padding:16px; text-decoration:none; color:#37474f; box-shadow:0 2px 6px rgba(0,0,0,.1); }
 .menu-grid b { display:block; font-size:18px; }
 .scroll { overflow-x:auto; }
 .tbl { width:100%; border-collapse:collapse; font-size:14px; background:#fff; }
 .tbl th, .tbl td { padding:8px; border-bottom:1px solid #eceff1; text-align:left; vertical-align:top; }
 .tbl th { background:#f5f7f8; }
 .tbl tr.banned td { background:#ffebee; }
 .inl { display:inline; }
 details summary { cursor:pointer; color:#37474f; }
 .danger { border:2px solid #b71c1c; }
 .warn { background:#fff3e0; border-left:5px solid #ff9800; padding:10px 14px; border-radius:8px; margin-bottom:14px; }
 .devnote { background:#111; color:#ffb74d; font-size:12px; padding:4px 10px; border-radius:6px; margin-top:8px; display:inline-block; }
</style>
</head>
<body class="{{ body_class }}">
{% if is_admin %}
<div class="devbar">
  <b>DEV MODE</b>
  <span>개발자 모드 켜짐 · 건네기 무제한</span>
  <a href="/admin">메뉴</a>
  <a href="/admin/people">참가자</a>
  <a href="/admin/reports">문의함{% if new_reports %}<span class="badge">{{ new_reports }}</span>{% endif %}</a>
  <a href="/admin/logs">로그</a>
  <span class="sp"></span>
  <a href="/admin/logout">개발자 모드 끄기</a>
</div>
{% endif %}
<header>
  <a class="logo" href="/">{% if logo %}<img src="{{ logo }}" alt="">{% else %}{{ icon|safe }}{% endif %} 빼빼로 PAIR <small>11:11</small></a>
  <span class="right">
    {% if user %}<span>{{ user.nickname }}님</span><a href="/logout">로그아웃</a>{% endif %}
    <a class="home" href="/">HOME</a>
  </span>
</header>
<main class="{{ 'wide' if wide }}">
{% if flash %}<div class="msg" data-auto="{{ auto }}">{{ flash }}</div>{% endif %}
{{ content|safe }}
</main>
<footer><a href="/report">문의 · 오류 신고</a> · 빼빼로 PAIR 11:11</footer>
{% if not wide %}<a class="fab" href="/report">문의 · 신고</a>{% endif %}

<div id="cfm" class="modal" hidden>
  <div class="modal-box">
    <p id="cfm-text"></p>
    <div class="modal-btns">
      <button type="button" id="cfm-no" class="sub">취소</button>
      <button type="button" id="cfm-yes">확인</button>
    </div>
  </div>
</div>

<script>
(function () {
  /* 1) 알림 문구는 몇 초 뒤 사라진다 (개발자 3초, 일반 6초) */
  var m = document.querySelector('.msg[data-auto]');
  if (m) {
    setTimeout(function () {
      m.style.opacity = '0';
      setTimeout(function () { m.style.display = 'none'; }, 400);
    }, parseInt(m.getAttribute('data-auto')) * 1000);
  }
  var u = new URL(location.href);
  if (u.searchParams.has('m') && history.replaceState) {
    u.searchParams.delete('m');
    history.replaceState(null, '', u.pathname + u.search);
  }

  /* 2) '보기' 버튼을 누르고 있는 동안만 비밀번호가 보인다 */
  document.querySelectorAll('.eye').forEach(function (b) {
    var t = document.getElementById(b.getAttribute('data-target'));
    function show(e) { e.preventDefault(); t.type = 'text'; }
    function hide() { t.type = 'password'; }
    b.addEventListener('mousedown', show);
    b.addEventListener('touchstart', show, { passive: false });
    ['mouseup', 'mouseleave', 'touchend', 'touchcancel'].forEach(function (ev) { b.addEventListener(ev, hide); });
    b.addEventListener('contextmenu', function (e) { e.preventDefault(); });
  });

  /* 3) data-confirm 이 붙은 폼은 '정말요?' 창을 먼저 띄운다 */
  var cfm = document.getElementById('cfm');
  var pending = null;
  document.addEventListener('submit', function (e) {
    var f = e.target;
    var msg = f.getAttribute('data-confirm');
    if (msg) {
      e.preventDefault();
      pending = f;
      document.getElementById('cfm-text').textContent = msg;
      cfm.hidden = false;
    }
  });
  document.getElementById('cfm-no').addEventListener('click', function () { cfm.hidden = true; pending = null; });
  document.getElementById('cfm-yes').addEventListener('click', function () {
    cfm.hidden = true;
    if (pending) { pending.submit(); }
  });
})();
</script>
</body>
</html>
"""


def page(body, **ctx):
    admin = is_admin()
    theme = ctx.pop("theme", None)          # 'w' / 'm' / 'admin'
    flash = ctx.pop("flash", None)
    title = ctx.pop("title", None)
    user = ctx["user"] if "user" in ctx else current_user()
    ctx["user"] = user
    new_reports = 0
    if admin:
        new_reports = get_db().execute("SELECT COUNT(*) FROM reports WHERE status='new'").fetchone()[0]

    if theme == "admin":
        cls = "t-admin"
    elif theme in ("w", "m"):
        cls = "t-" + theme
    elif user:
        cls = "t-w" if user["gender"] == "여성" else "t-m"
    else:
        cls = ""
    if admin:
        cls += " dev"

    inner = render_template_string(body, is_admin=admin, new_reports=new_reports, **ctx)
    return render_template_string(
        LAYOUT, content=inner, user=user, flash=flash, title=title, body_class=cls,
        is_admin=admin, new_reports=new_reports, favicon=static_url("favicon") or FAVICON,
        icon=ICON_SM, logo=static_url("logo"), wide=(theme == "admin"),
        auto=3 if theme == "admin" else 6)


# ---------------- 사용자 페이지 ----------------
SPLASH = """
<div class="card hero">
  {% if hero %}<img class="hero-img" src="{{ hero }}" alt="">
  {% else %}<div class="sticks"><i></i><i></i><b>:</b><i></i><i></i></div>{% endif %}
  <p class="eyebrow">PEPERO DAY · 11.11</p>
  <h1>빼빼로 PAIR에<br>오신 걸 환영합니다!</h1>
  <p class="lead">빼빼로로 마음을 전달해서 사랑을 키워봐요!</p>
  <p class="small">지금 참가자 <b>{{ total }}명</b> · 설문 완료 {{ done }}명</p>
  {% if user %}<a class="btn" href="/result">내 결과 보기 →</a>
  {% else %}<a class="btn" href="/enter">입장하기 →</a>{% endif %}
</div>

<div class="card notice">
  <h3>시작하기 전에 확인해 주세요</h3>
  <p>빼빼로 PAIR에 참여하려면 <b>'인스타그램 아이디'</b>가 필요해요!</p>
  <p class="small">서로 빼빼로를 건네면 두 사람의 인스타그램 아이디가 공개돼요. 아이디가 없다면 먼저 만들어 주세요.</p>
  <a class="btn insta" href="https://www.instagram.com/" target="_blank" rel="noopener">인스타그램 사이트로 이동하기 ↗</a>
</div>
"""

ENTER = """
<div class="card">
  <h2>어느 쪽 빼빼로인가요?</h2>
  <p class="small">성별에 따라 입구가 달라요. 이성 참가자와 매칭돼요.</p>
  <div class="gate">
    <a class="gw" href="/join/w"><span class="ic">♀</span><b>여성</b><span>가입하기</span></a>
    <a class="gm" href="/join/m"><span class="ic">♂</span><b>남성</b><span>가입하기</span></a>
  </div>
</div>

<div class="card">
  <h3>다시 왔어요 (로그인)</h3>
  <form method="post" action="/login">
    <label>별명</label>
    <input type="text" name="nickname" required>
    <label>PIN 4자리</label>
    <div class="pwrow">
      <input type="password" id="pin_l" name="pin" maxlength="4" inputmode="numeric" required>
      <button type="button" class="eye" data-target="pin_l">보기</button>
    </div>
    <button class="sub" type="submit">들어가기</button>
  </form>
</div>
"""

JOIN = """
<div class="card">
  <h2>{{ gender }} 입구 · 포장지 뜯기</h2>
  <p class="small">이미 가입했다면 <a href="/enter">로그인</a>으로 들어가세요.</p>
  <form method="post" action="/join/{{ gc }}">
    <label>별명 (1~12자)</label>
    <input type="text" name="nickname" maxlength="12" required>
    <label>PIN 4자리 숫자 (다시 들어올 때 필요해요)</label>
    <div class="pwrow">
      <input type="password" id="pin_j" name="pin" maxlength="4" pattern="[0-9]{4}" inputmode="numeric" required>
      <button type="button" class="eye" data-target="pin_j">보기</button>
    </div>
    <label>나이</label>
    <input type="number" name="age" min="15" max="80" required>
    <label>인스타그램 아이디 (@ 없이)</label>
    <input type="text" name="insta" placeholder="예: pepero_1111" required>
    <p class="small">한 아이디로 한 번만 가입할 수 있어요. 서로 빼빼로를 건넬 때만 상대에게 공개돼요.</p>
    <label style="font-weight:normal"><input type="checkbox" name="agree" required>
      개인정보(별명·나이·성별·인스타)를 행사 매칭에만 쓰고 종료 후 삭제하는 데 동의합니다.</label>
    <button type="submit">시식 테스트 하러 가기 →</button>
  </form>
</div>
"""

SURVEY = """
{% macro chips(name, items, cur, numeric=false) %}
<div class="opts">{% for it in items %}
  <label class="chip"><input type="radio" name="{{ name }}" value="{{ loop.index if numeric else it }}"
    {{ 'checked' if (cur|string) == ((loop.index if numeric else it)|string) }} required><span>{{ it }}</span></label>{% endfor %}
</div>
{% endmacro %}

<div class="card">
  <h2>시식 테스트 (10문항)</h2>
  <p class="small">{{ user.nickname }}님, 편하게 골라 주세요. 다시 제출하면 답이 덮어써져요.</p>
</div>
<form method="post">
 <div class="card">
  <label>1. 가장 좋아하는 빼빼로는?</label>{{ chips('fav', flavors, a.get('fav')) }}
  <label>2. 빼빼로 먹는 나만의 방식은?</label>{{ chips('eat', eats, a.get('eat')) }}
  <label>3. 초코 코팅은 어느 정도가 좋아요?</label>{{ chips('coat', coat, a.get('coat'), true) }}
  <label>4. 가장 받고 싶은 빼빼로 선물은?</label>{{ chips('gift', gifts, a.get('gift')) }}
  <label>5. 좋아하는 사람과 함께하고 싶은 활동은?</label>{{ chips('date', dates, a.get('date')) }}
  <label>6. 좋아하면 말과 행동으로 표현하는 편인가요?</label>{{ chips('expr', expr, a.get('expr'), true) }}
  <label>7. 데이트는 계획파? 즉흥파?</label>{{ chips('plan', plan, a.get('plan'), true) }}
 </div>

 <div class="card">
  <label>8. 나의 MBTI (재미용 간이 입력이에요)</label>
  <div class="axis">
   {% for x in axes %}{% set i = loop.index0 %}
   <select name="m{{ i }}" required>
     <option value="{{ x[0] }}" {{ 'selected' if a.get('mbti') and a['mbti'][i]==x[0] }}>{{ x[0] }}</option>
     <option value="{{ x[1] }}" {{ 'selected' if a.get('mbti') and a['mbti'][i]==x[1] }}>{{ x[1] }}</option>
   </select>{% endfor %}
  </div>

  <label>9. 이상형의 MBTI는? (상관없으면 그대로 두세요)</label>
  <div class="axis">
   {% for x in axes %}{% set i = loop.index0 %}
   <select name="p{{ i }}">
     <option value="any">상관없음</option>
     <option value="{{ x[0] }}" {{ 'selected' if a.get('pref') and a['pref'][i]==x[0] }}>{{ x[0] }}</option>
     <option value="{{ x[1] }}" {{ 'selected' if a.get('pref') and a['pref'][i]==x[1] }}>{{ x[1] }}</option>
   </select>{% endfor %}
  </div>

  <label>10. 이상형 나이: 나보다 연상 / 연하 몇 살까지 괜찮아요?</label>
  <div class="row2">
    <select name="older">{% for n in range(0,11) %}
      <option value="{{ n }}" {{ 'selected' if (a.get('older',3))|int==n }}>연상 {{ n }}살까지</option>{% endfor %}</select>
    <select name="younger">{% for n in range(0,11) %}
      <option value="{{ n }}" {{ 'selected' if (a.get('younger',3))|int==n }}>연하 {{ n }}살까지</option>{% endfor %}</select>
  </div>
  <button type="submit">초코 코팅하러 가기 →</button>
 </div>
</form>
"""

RESULT = """
<div class="card">
  <h2>{{ user.nickname }}님의 TOP3</h2>
  <p class="small">이성 참가자 {{ n_other }}명 중에서 계산했어요. 새 참가자가 들어오면 결과가 바뀔 수 있어요.</p>
  <p>남은 빼빼로 건네기:
    <b>{% if left is none %}∞ (개발자 모드){% else %}{{ left }}회{% endif %}</b>
    · <a href="/shop">건네기권 보기</a></p>
  <a class="btn sub" href="/result">결과 새로고침</a>
  <a class="btn sub" href="/survey">답 수정하기</a>
</div>

{% if not top %}
  <div class="card">아직 매칭할 이성 참가자가 없어요. 친구를 초대하고 새로고침해 보세요!</div>
{% endif %}

{% for t in top %}
<div class="card">
  <span class="rank">{{ loop.index }}위</span>
  <b>{{ t.p.nickname }}</b> ({{ t.p.age }}세 · {{ t.p.ans.mbti }})
  <div class="score">{{ t.idx }}점</div>
  <div class="gauge">
    <div class="stick l"></div>
    <div class="gap" style="width:{{ ((100 - t.idx) * 0.6)|round(0) }}%"></div>
    <div class="stick r"></div>
  </div>
  <div class="small">남은 거리 {{ ((100 - t.idx) / 10)|round(1) }} cm</div>
  <ul>
   {% for r in t.reasons %}<li>{{ r }}</li>{% endfor %}
   {% if not t.reasons %}<li>겹치는 항목은 적지만 다른 성향이 서로를 채워줄 수 있어요</li>{% endif %}
  </ul>

  {% if t.mutual %}
    <div class="mutual"><b>서로 빼빼로를 건넸어요!</b><br>
      인스타그램: <a href="https://www.instagram.com/{{ t.p.insta }}/" target="_blank" rel="noopener">@{{ t.p.insta }}</a>
      <div class="quote">상대의 쪽지: “{{ t.recv.message }}”</div>
    </div>
  {% elif t.sent %}
    <div class="sentbox">빼빼로를 건넸어요. 내 쪽지: “{{ t.sent.message }}”<br>
      <span class="small">상대도 나에게 건네면 인스타그램 아이디가 서로 공개돼요.</span></div>
  {% else %}
    {% if t.recv %}<div class="mutual"><b>이 분이 먼저 빼빼로를 건넸어요!</b>
      <div class="quote">상대의 쪽지: “{{ t.recv.message }}”</div></div>{% endif %}
    {% if can_give %}
    <form method="post" action="/give/{{ t.p.id }}"
          data-confirm="{{ t.p.nickname }}님에게 빼빼로를 건넬까요?{% if not is_admin %} 건네기 횟수가 1회 줄어들고 되돌릴 수 없어요.{% endif %}">
      <label>쪽지 한마디 (그대로 보내도 돼요)</label>
      <input type="text" name="message" maxlength="40" value="{{ default_msg }}">
      <button type="submit">빼빼로 건네기</button>
    </form>
    {% else %}
    <div class="sentbox">건네기 횟수를 모두 썼어요. <a href="/shop">건네기권 보러가기</a></div>
    {% endif %}
  {% endif %}

  {% if is_admin %}<div class="devnote">DEV · id {{ t.p.id }} · 나→상대 {{ t.s1 }}점 · 상대→나 {{ t.s2 }}점</div>{% endif %}
</div>
{% endfor %}

{% if got %}
<div class="card">
  <h3>나에게 온 빼빼로 {{ got|length }}개</h3>
  {% for r in got %}
  <div class="recv">
    <b>{{ r.nickname }}</b> ({{ r.age }}세)
    <div class="quote">“{{ r.message }}”</div>
    <div class="small">
      {% if r.mutual %}서로 건넸어요 · 인스타 @{{ r.insta }}
      {% elif r.in_top %}위 TOP3 카드에서 빼빼로를 건네 답장할 수 있어요
      {% else %}이 분은 내 TOP3 밖이라 답장은 어려워요{% endif %}
    </div>
  </div>
  {% endfor %}
</div>
{% endif %}

<a class="ad" href="/shop"><span class="tag">AD</span><b>11:11 특가! 빼빼로 11번 건네기 9,900원</b>
  <div class="small">모의 광고예요 (학술제 시연용)</div></a>

{% if popup %}
<div class="modal">
  <div class="modal-box">
    <h2>빼빼로를 건넸어요!</h2>
    <p>쪽지와 함께 마음이 전달됐어요.</p>
    <div class="ad-in"><b>빼빼로를 여러 번 건넬 수 있어요!</b>
      <p>무료 건네기는 1회뿐이에요.<br>1회 1,100원부터 더 건네 보세요.</p></div>
    <a class="btn" href="/shop">건네기권 보러가기</a>
    <button type="button" class="sub" onclick="this.closest('.modal').hidden=true">다음에 할게요</button>
  </div>
</div>
{% endif %}
"""

SHOP = """
<div class="card">
  <h2>빼빼로 건네기권</h2>
  <div class="sim">이 페이지는 <b>학술제 시연용 모의 결제</b>예요. 실제 돈이 빠져나가지 않고, 카드·계좌 정보도 받지 않아요.</div>
  <p>남은 건네기: <b>{% if left is none %}∞ (개발자 모드){% else %}{{ left }}회{% endif %}</b><br>
     <span class="small">기본 {{ free }}회는 무료, 추가는 1회 {{ '{:,}'.format(price) }}원이에요.</span></p>
</div>
{% for q, p, tag in packages %}
<div class="card pack">
  <form method="post" action="/checkout">
    <input type="hidden" name="qty" value="{{ q }}">
    <div><b style="font-size:20px">{{ q }}회</b> {% if tag %}<span class="tag">{{ tag }}</span>{% endif %}<br>
      <span class="price">{{ '{:,}'.format(p) }}원</span></div>
    <button type="submit">선택</button>
  </form>
</div>
{% endfor %}
<a class="btn sub" href="/result">← 결과로 돌아가기</a>
"""

CHECKOUT = """
<div class="card">
  <h2>모의 결제</h2>
  <div class="sim">시연용 화면이에요. 실제 결제는 이뤄지지 않아요.</div>
  <table class="tbl">
    <tr><th>상품</th><td>빼빼로 건네기권 {{ qty }}회</td></tr>
    <tr><th>결제 금액</th><td><b>{{ '{:,}'.format(amount) }}원</b></td></tr>
    <tr><th>구매자</th><td>{{ user.nickname }}</td></tr>
  </table>
  <form method="post" action="/pay" data-confirm="모의 결제를 진행할까요? (실제 결제는 되지 않아요)">
    <input type="hidden" name="qty" value="{{ qty }}">
    <a class="btn sub" href="/shop">취소</a>
    <button type="submit">모의 결제하기</button>
  </form>
</div>
"""

REPORT = """
<div class="card">
  <h2>문의 · 오류 신고</h2>
  <p class="small">오류, 접속 문제, 건의사항을 남겨 주세요. 운영진이 확인해요.</p>
  <form method="post">
    <label>종류</label>
    <select name="category">{% for c in cats %}<option>{{ c }}</option>{% endfor %}</select>
    <label>내용 (5~500자)</label>
    <textarea name="content" maxlength="500" required></textarea>
    <label>연락 방법 (선택, 인스타 ID나 이메일)</label>
    <input type="text" name="contact" maxlength="60" value="{{ user.insta if user else '' }}">
    <button type="submit">보내기</button>
  </form>
</div>
"""

# ---------------- 개발자 페이지 ----------------
ADMIN_NAV = """
{% set tabs = [('menu','/admin','메뉴'),('people','/admin/people','참가자'),('gifts','/admin/gifts','건네기·결제'),('reports','/admin/reports','문의함'),('logs','/admin/logs','로그'),('tools','/admin/tools','도구·초기화')] %}
<div class="tabs">{% for k, u, n in tabs %}<a href="{{ u }}" class="{{ 'on' if tab == k }}">{{ n }}{% if k == 'reports' and new_reports %}<span class="badge">{{ new_reports }}</span>{% endif %}</a>{% endfor %}</div>
"""

ADMIN_LOGIN = """
<div class="card">
  <h2>개발자 로그인</h2>
  <p class="small">'보기' 버튼을 누르고 있는 동안만 입력한 비밀번호가 보여요.</p>
  <form method="post" action="/admin/login">
    <label>비밀번호</label>
    <div class="pwrow">
      <input type="password" id="pw1" name="pw" autocomplete="off" required>
      <button type="button" class="eye" data-target="pw1">보기</button>
    </div>
    <button type="submit">들어가기</button>
  </form>
</div>
"""

ADMIN_MENU = ADMIN_NAV + """
{% if default_pw %}<div class="warn"><b>기본 비밀번호를 쓰고 있어요!</b> GitHub에 공개된 값이라 누구나 알 수 있어요. PythonAnywhere WSGI 파일에서 PEPERO_ADMIN_PW를 바꿔 주세요.</div>{% endif %}
<div class="card">
  <h2>현황</h2>
  <div class="stats">
  {% for label, val in [('전체 참가자', st.total), ('여성', st.w), ('남성', st.m), ('설문 완료', st.done), ('가상 참가자', st.fake), ('차단', st.banned), ('건넨 빼빼로', st.gifts), ('서로 건넨 쌍', st.mutual), ('모의 결제', st.orders), ('모의 매출(원)', st.sales), ('새 문의', st.reports)] %}
    <div class="stat"><b>{{ val }}</b>{{ label }}</div>
  {% endfor %}
  </div>
</div>
<div class="menu-grid">
  <a href="/admin/people"><b>참가자 관리</b>차단 · 삭제 · 정보 수정 · 건네기권 지급</a>
  <a href="/admin/gifts"><b>건네기 · 결제</b>건네기 기록, 모의 결제, 가상 참가자 답장 테스트</a>
  <a href="/admin/reports"><b>문의함</b>사용자가 남긴 오류 신고와 건의</a>
  <a href="/admin/logs"><b>활동 로그</b>누가 언제 무엇을 했는지 기록</a>
  <a href="/admin/tools"><b>도구 · 초기화</b>가상 참가자 만들기, 데이터 초기화</a>
  <a href="/"><b>사용자 화면 보기</b>개발자 모드를 켠 채 사이트를 둘러보기</a>
</div>
"""

ADMIN_PEOPLE = ADMIN_NAV + """
<div class="card">
  <h2>참가자 {{ people|length }}명</h2>
  <p class="small">차단하면 로그인과 매칭에서 빠지지만 인스타 ID는 계속 사용 중으로 남아요. 삭제하면 완전히 지워져요.</p>
  <div class="scroll">
  <table class="tbl">
    <tr><th>번호</th><th>별명</th><th>성별</th><th>나이</th><th>인스타</th><th>설문</th><th>건넨 수</th><th>추가권</th><th>상태</th><th>관리</th></tr>
    {% for p in people %}
    <tr class="{{ 'banned' if p.banned }}">
      <td>{{ p.id }}</td>
      <td>{{ p.nickname }}{% if p.is_fake %} <span class="tag">가상</span>{% endif %}</td>
      <td>{{ p.gender }}</td><td>{{ p.age }}</td><td>{{ p.insta }}</td>
      <td>{{ '완료' if p.answers else '-' }}</td><td>{{ p.sent }}</td><td>{{ p.bonus or 0 }}</td>
      <td>{{ '차단' if p.banned else '정상' }}</td>
      <td>
        <form class="inl" method="post" action="/admin/ban/{{ p.id }}"
              data-confirm="{{ p.nickname }}님을 {{ '차단 해제' if p.banned else '차단' }}할까요?">
          <button class="sm sub" type="submit">{{ '해제' if p.banned else '차단' }}</button></form>
        <form class="inl" method="post" action="/admin/delete/{{ p.id }}"
              data-confirm="{{ p.nickname }}님을 정말로 삭제할까요? 이 사람의 건네기 기록도 함께 지워지고 되돌릴 수 없어요.">
          <button class="sm dng" type="submit">삭제</button></form>
        <details><summary>수정</summary>
          <form method="post" action="/admin/edit/{{ p.id }}" data-confirm="{{ p.nickname }}님의 정보를 수정할까요?">
            <input type="text" name="nickname" value="{{ p.nickname }}" maxlength="12">
            <input type="number" name="age" value="{{ p.age }}" min="15" max="80">
            <input type="text" name="insta" value="{{ p.insta }}">
            <input type="number" name="bonus" value="{{ p.bonus or 0 }}" min="0" max="999" title="추가 건네기권">
            <button class="sm" type="submit">저장</button>
          </form>
        </details>
      </td>
    </tr>
    {% endfor %}
  </table>
  </div>
</div>
"""

ADMIN_GIFTS = ADMIN_NAV + """
<div class="card">
  <h3>테스트: 가상 참가자가 빼빼로 건네기</h3>
  <p class="small">서로 건넸을 때 인스타 ID가 공개되는 화면을 시험할 수 있어요.<br>
    ① '나에게 건네기' = 이 사용자의 점수 상위 가상 참가자가 먼저 건네요. ② '내 건네기에 답장' = 이 사용자가 건넨 가상 참가자들이 답장해요.</p>
  <form method="post" action="/admin/fake_gift" data-confirm="가상 참가자의 건네기 기록을 만들까요?">
    <div class="row2">
      <input type="text" name="nickname" placeholder="사용자 별명" required>
      <select name="mode"><option value="to_me">① 나에게 건네기</option><option value="reply">② 내 건네기에 답장</option></select>
    </div>
    <label>몇 명? (①에서만 쓰여요)</label>
    <input type="number" name="n" value="2" min="1" max="10">
    <button type="submit">실행</button>
  </form>
</div>

<div class="card">
  <h3>건네기 기록 {{ gifts|length }}건 (최근 200건)</h3>
  <div class="scroll"><table class="tbl">
    <tr><th>번호</th><th>보낸 사람</th><th>받은 사람</th><th>쪽지</th><th>서로?</th><th>시간</th><th></th></tr>
    {% for g in gifts %}
    <tr><td>{{ g.id }}</td><td>{{ g.from_n }}</td><td>{{ g.to_n }}</td><td>{{ g.message }}</td>
        <td>{{ 'O' if g.mutual else '' }}</td><td>{{ g.created_at }}</td>
        <td><form method="post" action="/admin/gift_delete/{{ g.id }}" data-confirm="이 건네기 기록을 삭제할까요?"><button class="sm dng" type="submit">삭제</button></form></td></tr>
    {% endfor %}
  </table></div>
</div>

<div class="card">
  <h3>모의 결제 내역 {{ orders|length }}건</h3>
  <div class="scroll"><table class="tbl">
    <tr><th>번호</th><th>구매자</th><th>횟수</th><th>금액(원)</th><th>시간</th></tr>
    {% for o in orders %}<tr><td>{{ o.id }}</td><td>{{ o.nickname }}</td><td>{{ o.qty }}</td><td>{{ o.amount }}</td><td>{{ o.created_at }}</td></tr>{% endfor %}
  </table></div>
</div>
"""

ADMIN_REPORTS = ADMIN_NAV + """
<div class="card">
  <h2>문의함 ({{ reports|length }}건)</h2>
  {% if not reports %}<p>아직 접수된 문의가 없어요.</p>{% endif %}
  {% for r in reports %}
  <div class="recv">
    <span class="tag" style="background:{{ '#d32f2f' if r.status == 'new' else '#78909c' }}">{{ '새 문의' if r.status == 'new' else '처리완료' }}</span>
    <b>{{ r.category }}</b> · {{ r.nickname or '비회원' }} · <span class="small">{{ r.created_at }}</span>
    <div class="quote" style="white-space:pre-wrap">{{ r.content }}</div>
    {% if r.contact %}<div class="small">연락처: {{ r.contact }}</div>{% endif %}
    {% if r.status == 'new' %}
    <form class="inl" method="post" action="/admin/report_done/{{ r.id }}"><button class="sm" type="submit">처리완료로 표시</button></form>
    {% endif %}
    <form class="inl" method="post" action="/admin/report_delete/{{ r.id }}" data-confirm="이 문의를 삭제할까요?"><button class="sm dng" type="submit">삭제</button></form>
  </div>
  {% endfor %}
</div>
"""

ADMIN_LOGS = ADMIN_NAV + """
<div class="card">
  <h2>개발자 활동 로그 (최근 200건)</h2>
  <div class="scroll"><table class="tbl">
    <tr><th>시간</th><th>동작</th><th>내용</th></tr>
    {% for l in logs %}<tr><td>{{ l.created_at }}</td><td>{{ l.action }}</td><td>{{ l.detail }}</td></tr>{% endfor %}
  </table></div>
</div>
"""

ADMIN_TOOLS = ADMIN_NAV + """
<div class="card">
  <h3>가상 참가자 만들기</h3>
  <p class="small">기존 가상 참가자는 지우고 다시 만들어요. 매번 같은 데이터가 나와요.</p>
  <form method="post" action="/admin/seed" data-confirm="기존 가상 참가자를 지우고 새로 만들까요?">
    <label>인원 (2~100명)</label>
    <input type="number" name="n" value="20" min="2" max="100">
    <button type="submit">만들기</button>
  </form>
</div>
<div class="card danger">
  <h3>초기화 (되돌릴 수 없어요)</h3>
  <p class="small">버튼을 누르면 한 번 더 확인하는 화면으로 넘어가요.</p>
  {% for key, name, desc in resets %}
  <div class="recv"><b>{{ name }}</b><div class="small">{{ desc }}</div>
    <a class="btn sm {{ 'dng' if key == 'all' else 'sub' }}" href="/admin/confirm/{{ key }}" style="margin-top:6px">{{ name }} →</a></div>
  {% endfor %}
</div>
"""

ADMIN_CONFIRM = ADMIN_NAV + """
<div class="card danger">
  <h2>정말로 '{{ name }}'을(를) 실행할까요?</h2>
  <p>{{ desc }}</p>
  <p>대상: <b>{{ cnt }}건</b> · 이 작업은 되돌릴 수 없어요.</p>
  <form method="post" action="/admin/reset" data-confirm="마지막 확인이에요. 정말로 실행할까요?">
    <input type="hidden" name="scope" value="{{ scope }}">
    {% if scope == 'all' %}
    <label>확인을 위해 아래 칸에 <b>초기화</b> 라고 입력하세요</label>
    <input type="text" name="confirm" autocomplete="off">
    {% endif %}
    <a class="btn sub" href="/admin/tools">취소</a>
    <button class="dng" type="submit">네, 실행합니다</button>
  </form>
</div>
"""


# ------------------------------------------------------------
# 라우트 - 사용자
# ------------------------------------------------------------
@app.route("/")
def home():
    db = get_db()
    total = db.execute("SELECT COUNT(*) FROM participants").fetchone()[0]
    done = db.execute("SELECT COUNT(*) FROM participants WHERE answers IS NOT NULL").fetchone()[0]
    return page(SPLASH, total=total, done=done, hero=static_url("hero"), flash=request.args.get("m"))


@app.route("/favicon.ico")
def favicon():
    return Response(icon_svg(32, bg=True), mimetype="image/svg+xml")


@app.route("/enter")
def enter():
    return page(ENTER, flash=request.args.get("m"))


@app.route("/join/<gc>", methods=["GET", "POST"])
def join(gc):
    if gc not in GCODE:
        return redirect("/enter")
    gender = GCODE[gc]
    back = "/join/" + gc
    if request.method == "GET":
        return page(JOIN, theme=gc, gc=gc, gender=gender, flash=request.args.get("m"))

    nickname = request.form.get("nickname", "").strip()
    pin = request.form.get("pin", "").strip()
    insta = clean_insta(request.form.get("insta"))
    try:
        age = int(request.form.get("age", ""))
    except ValueError:
        return go(back, "나이를 숫자로 입력해 주세요.")
    if not nickname or len(nickname) > 12:
        return go(back, "별명은 1~12자로 입력해 주세요.")
    if not (pin.isdigit() and len(pin) == 4):
        return go(back, "PIN은 숫자 4자리예요.")
    if not (15 <= age <= 80):
        return go(back, "나이 범위를 확인해 주세요.")
    if not INSTA_RE.match(insta):
        return go(back, "인스타그램 아이디는 영문·숫자·밑줄(_)·점(.)만 쓸 수 있어요.")

    db = get_db()
    # 동명이인 방지: 인스타그램 아이디가 이미 있으면 가입 불가
    if db.execute("SELECT 1 FROM participants WHERE lower(insta)=?", (insta,)).fetchone():
        return go(back, "이미 가입된 인스타그램 아이디예요. 본인이라면 로그인해 주세요.")
    if db.execute("SELECT 1 FROM participants WHERE nickname=?", (nickname,)).fetchone():
        return go(back, "이미 있는 별명이에요. 다른 별명을 써 주세요.")
    try:
        cur = db.execute(
            "INSERT INTO participants (nickname, pin_hash, gender, age, insta, created_at) VALUES (?,?,?,?,?,?)",
            (nickname, generate_password_hash(pin), gender, age, insta, now_kst()),
        )
        db.commit()
    except sqlite3.IntegrityError:
        return go(back, "이미 있는 별명이에요. 다른 별명을 써 주세요.")
    session["uid"] = cur.lastrowid
    return redirect("/survey")


@app.route("/login", methods=["POST"])
def login():
    nickname = request.form.get("nickname", "").strip()
    pin = request.form.get("pin", "").strip()
    r = get_db().execute("SELECT * FROM participants WHERE nickname=?", (nickname,)).fetchone()
    if not r or not check_password_hash(r["pin_hash"], pin):
        return go("/enter", "별명 또는 PIN이 맞지 않아요.")
    if r["banned"]:
        return go("/enter", "이용이 제한된 계정이에요. 운영진에게 문의해 주세요.")
    session["uid"] = r["id"]
    return redirect("/result" if r["answers"] else "/survey")


@app.route("/logout")
def logout():
    session.pop("uid", None)       # 개발자 로그인은 유지한다
    return redirect("/")


@app.route("/survey", methods=["GET", "POST"])
def survey():
    user = current_user()
    if not user:
        return go("/enter", "먼저 가입하거나 로그인해 주세요.")

    if request.method == "POST":
        f = request.form
        try:
            ans = {
                "fav": f["fav"], "eat": f["eat"], "coat": int(f["coat"]),
                "gift": f["gift"], "date": f["date"],
                "expr": int(f["expr"]), "plan": int(f["plan"]),
                "mbti": "".join(f["m%d" % i] for i in range(4)),
                "pref": [f.get("p%d" % i, "any") for i in range(4)],
                "older": int(f["older"]), "younger": int(f["younger"]),
            }
        except (KeyError, ValueError):
            return redirect("/survey")
        db = get_db()
        db.execute("UPDATE participants SET answers=? WHERE id=?",
                   (json.dumps(ans, ensure_ascii=False), user["id"]))
        db.commit()
        return redirect("/result")

    return page(SURVEY, user=user, a=user["ans"] or {}, flavors=FLAVORS, eats=EATS, gifts=GIFTS,
                dates=DATES, axes=AXES, coat=COAT_LABELS, expr=EXPR_LABELS, plan=PLAN_LABELS)


@app.route("/result")
def result():
    user = current_user()
    if not user:
        return go("/enter", "먼저 가입하거나 로그인해 주세요.")
    if not user["ans"]:
        return redirect("/survey")
    top, n_other = get_top(user, 3)
    got = annotate(user, top)
    left = gifts_left(user)
    popup = session.pop("popup", None)
    return page(RESULT, user=user, top=top, n_other=n_other, got=got, left=left,
                can_give=(left is None or left > 0), default_msg=DEFAULT_MSG,
                popup=popup, flash=request.args.get("m"))


@app.route("/give/<int:to_id>", methods=["POST"])
def give(to_id):
    """빼빼로 건네기: TOP3 안의 이성에게만, 횟수 제한 있음 (개발자 모드는 무제한)"""
    user = current_user()
    if not user:
        return go("/enter", "먼저 가입하거나 로그인해 주세요.")
    if not user["ans"]:
        return redirect("/survey")
    top, _ = get_top(user, 3)
    if to_id not in [t["p"]["id"] for t in top]:
        return go("/result", "TOP3 안에 있는 분께만 빼빼로를 건넬 수 있어요.")
    db = get_db()
    if db.execute("SELECT 1 FROM gifts WHERE from_id=? AND to_id=?", (user["id"], to_id)).fetchone():
        return go("/result", "이미 빼빼로를 건넨 분이에요.")
    left = gifts_left(user)
    if left is not None and left <= 0:
        return go("/result", "건네기 횟수를 모두 썼어요. 건네기권을 구매하면 더 건넬 수 있어요.")
    msg = request.form.get("message", "").strip()[:40] or DEFAULT_MSG
    db.execute("INSERT INTO gifts (from_id, to_id, message, created_at) VALUES (?,?,?,?)",
               (user["id"], to_id, msg, now_kst()))
    db.commit()
    session["popup"] = 1            # 건네기 직후 '여러 번 건넬 수 있어요' 안내 팝업
    return redirect("/result")


@app.route("/shop")
def shop():
    user = current_user()
    if not user:
        return go("/enter", "먼저 가입하거나 로그인해 주세요.")
    return page(SHOP, user=user, left=gifts_left(user), packages=PACKAGES, price=PRICE_PER, free=FREE_GIFTS)


@app.route("/checkout", methods=["POST"])
def checkout():
    user = current_user()
    if not user:
        return go("/enter", "먼저 가입하거나 로그인해 주세요.")
    try:
        qty = int(request.form.get("qty", "0"))
    except ValueError:
        qty = 0
    pk = [p for p in PACKAGES if p[0] == qty]
    if not pk:
        return go("/shop", "상품을 다시 선택해 주세요.")
    return page(CHECKOUT, user=user, qty=qty, amount=pk[0][1])


@app.route("/pay", methods=["POST"])
def pay():
    """모의 결제: 돈은 오가지 않고 건네기 횟수만 늘어난다 (시연용)"""
    user = current_user()
    if not user:
        return go("/enter", "먼저 가입하거나 로그인해 주세요.")
    try:
        qty = int(request.form.get("qty", "0"))
    except ValueError:
        qty = 0
    pk = [p for p in PACKAGES if p[0] == qty]
    if not pk:
        return go("/shop", "상품을 다시 선택해 주세요.")
    db = get_db()
    db.execute("INSERT INTO orders (user_id, nickname, qty, amount, created_at) VALUES (?,?,?,?,?)",
               (user["id"], user["nickname"], qty, pk[0][1], now_kst()))
    db.execute("UPDATE participants SET bonus = COALESCE(bonus,0) + ? WHERE id=?", (qty, user["id"]))
    db.commit()
    return go("/result", "모의 결제 완료! 건네기권 %d회가 충전됐어요." % qty)


@app.route("/report", methods=["GET", "POST"])
def report():
    user = current_user()
    if request.method == "POST":
        cat = request.form.get("category", "")
        content = request.form.get("content", "").strip()
        contact = request.form.get("contact", "").strip()[:60]
        if cat not in REPORT_CATS:
            cat = "기타"
        if len(content) < 5 or len(content) > 500:
            return go("/report", "내용을 5자 이상 500자 이하로 적어 주세요.")
        db = get_db()
        db.execute("INSERT INTO reports (user_id, nickname, category, content, contact, created_at) VALUES (?,?,?,?,?,?)",
                   (user["id"] if user else None, user["nickname"] if user else None, cat, content, contact, now_kst()))
        db.commit()
        return go("/", "문의가 접수됐어요. 확인 후 연락드릴게요.")
    return page(REPORT, user=user, cats=REPORT_CATS, flash=request.args.get("m"))


# ------------------------------------------------------------
# 라우트 - 개발자 전용 (/admin)
# ------------------------------------------------------------
RESET_INFO = [
    ("fake", "가상 참가자 삭제", "가상 참가자와 그들이 주고받은 건네기 기록을 지워요."),
    ("answers", "설문 답 초기화", "실제 참가자의 설문 답만 지워요. 가입 정보는 남아요."),
    ("gifts", "건네기 기록 초기화", "빼빼로 건네기 기록을 모두 지워요."),
    ("orders", "모의 결제 초기화", "모의 결제 내역을 지우고 모든 참가자의 추가 건네기권을 0으로 되돌려요."),
    ("reports", "문의함 비우기", "사용자 문의를 모두 지워요."),
    ("logs", "활동 로그 지우기", "개발자 활동 로그를 모두 지워요."),
    ("all", "전체 초기화", "모든 참가자·건네기·결제 기록을 지우고 번호도 1번부터 다시 시작해요."),
]
RESET_DICT = {k: (n, d) for k, n, d in RESET_INFO}


def reset_count(scope):
    q = {
        "fake": "SELECT COUNT(*) FROM participants WHERE is_fake=1",
        "answers": "SELECT COUNT(*) FROM participants WHERE is_fake=0 AND answers IS NOT NULL",
        "gifts": "SELECT COUNT(*) FROM gifts",
        "orders": "SELECT COUNT(*) FROM orders",
        "reports": "SELECT COUNT(*) FROM reports",
        "logs": "SELECT COUNT(*) FROM admin_logs",
        "all": "SELECT COUNT(*) FROM participants",
    }[scope]
    return get_db().execute(q).fetchone()[0]


def admin_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not is_admin():
            return redirect("/admin")
        return f(*args, **kwargs)
    return wrapper


@app.route("/admin")
def admin():
    msg = request.args.get("m")
    if not is_admin():
        return page(ADMIN_LOGIN, theme="admin", flash=msg)
    db = get_db()
    one = lambda q: db.execute(q).fetchone()[0]
    st = {
        "total": one("SELECT COUNT(*) FROM participants"),
        "w": one("SELECT COUNT(*) FROM participants WHERE gender='여성'"),
        "m": one("SELECT COUNT(*) FROM participants WHERE gender='남성'"),
        "done": one("SELECT COUNT(*) FROM participants WHERE answers IS NOT NULL"),
        "fake": one("SELECT COUNT(*) FROM participants WHERE is_fake=1"),
        "banned": one("SELECT COUNT(*) FROM participants WHERE banned=1"),
        "gifts": one("SELECT COUNT(*) FROM gifts"),
        "mutual": one("SELECT COUNT(*) FROM gifts a JOIN gifts b ON a.from_id=b.to_id AND a.to_id=b.from_id "
                      "WHERE a.from_id < a.to_id"),
        "orders": one("SELECT COUNT(*) FROM orders"),
        "sales": "{:,}".format(one("SELECT COALESCE(SUM(amount),0) FROM orders")),
        "reports": one("SELECT COUNT(*) FROM reports WHERE status='new'"),
    }
    return page(ADMIN_MENU, theme="admin", tab="menu", st=st, default_pw=(ADMIN_PW == "choco1111"), flash=msg)


@app.route("/admin/login", methods=["POST"])
def admin_login():
    if hmac.compare_digest(request.form.get("pw", ""), ADMIN_PW):
        session["admin"] = True
        log("로그인", "개발자 모드 켜짐")
        return redirect("/admin")
    log("로그인 실패", "비밀번호가 틀렸어요")
    return go("/admin", "비밀번호가 맞지 않아요.")


@app.route("/admin/logout")
def admin_logout():
    if is_admin():
        log("로그아웃", "개발자 모드 꺼짐")
    session.pop("admin", None)
    return redirect("/")


@app.route("/admin/people")
@admin_required
def admin_people():
    rows = get_db().execute(
        "SELECT p.*, (SELECT COUNT(*) FROM gifts WHERE from_id=p.id) AS sent "
        "FROM participants p ORDER BY p.id DESC").fetchall()
    return page(ADMIN_PEOPLE, theme="admin", tab="people", people=[dict(r) for r in rows],
                flash=request.args.get("m"))


@app.route("/admin/ban/<int:pid>", methods=["POST"])
@admin_required
def admin_ban(pid):
    db = get_db()
    r = db.execute("SELECT nickname, banned FROM participants WHERE id=?", (pid,)).fetchone()
    if r:
        new = 0 if r["banned"] else 1
        db.execute("UPDATE participants SET banned=? WHERE id=?", (new, pid))
        db.commit()
        log("차단 해제" if r["banned"] else "차단", "#%d %s" % (pid, r["nickname"]))
    return go("/admin/people", "차단 상태를 바꿨어요.")


@app.route("/admin/delete/<int:pid>", methods=["POST"])
@admin_required
def admin_delete(pid):
    db = get_db()
    r = db.execute("SELECT nickname FROM participants WHERE id=?", (pid,)).fetchone()
    db.execute("DELETE FROM gifts WHERE from_id=? OR to_id=?", (pid, pid))
    db.execute("DELETE FROM participants WHERE id=?", (pid,))
    db.commit()
    log("삭제", "#%d %s" % (pid, r["nickname"] if r else "?"))
    return go("/admin/people", "삭제했어요.")


@app.route("/admin/edit/<int:pid>", methods=["POST"])
@admin_required
def admin_edit(pid):
    nickname = request.form.get("nickname", "").strip()
    insta = clean_insta(request.form.get("insta"))
    try:
        age = int(request.form.get("age", ""))
        bonus = int(request.form.get("bonus", "0"))
    except ValueError:
        return go("/admin/people", "나이와 추가권은 숫자로 입력해 주세요.")
    bonus = max(0, min(999, bonus))
    if not nickname or len(nickname) > 12 or not (15 <= age <= 80) or not INSTA_RE.match(insta):
        return go("/admin/people", "입력값을 확인해 주세요. (별명 1~12자, 나이 15~80, 인스타 형식)")
    db = get_db()
    if db.execute("SELECT 1 FROM participants WHERE lower(insta)=? AND id!=?", (insta, pid)).fetchone():
        return go("/admin/people", "다른 사람이 쓰는 인스타그램 아이디예요.")
    try:
        db.execute("UPDATE participants SET nickname=?, age=?, insta=?, bonus=? WHERE id=?",
                   (nickname, age, insta, bonus, pid))
        db.commit()
    except sqlite3.IntegrityError:
        return go("/admin/people", "이미 있는 별명이에요.")
    log("수정", "#%d → %s / %d세 / @%s / 추가권 %d" % (pid, nickname, age, insta, bonus))
    return go("/admin/people", "수정했어요.")


@app.route("/admin/gifts")
@admin_required
def admin_gifts():
    db = get_db()
    gifts = db.execute(
        "SELECT g.id, g.message, g.created_at, a.nickname AS from_n, b.nickname AS to_n, "
        "EXISTS(SELECT 1 FROM gifts r WHERE r.from_id=g.to_id AND r.to_id=g.from_id) AS mutual "
        "FROM gifts g JOIN participants a ON a.id=g.from_id JOIN participants b ON b.id=g.to_id "
        "ORDER BY g.id DESC LIMIT 200").fetchall()
    orders = db.execute("SELECT * FROM orders ORDER BY id DESC LIMIT 100").fetchall()
    return page(ADMIN_GIFTS, theme="admin", tab="gifts", gifts=[dict(r) for r in gifts],
                orders=[dict(r) for r in orders], flash=request.args.get("m"))


@app.route("/admin/gift_delete/<int:gid>", methods=["POST"])
@admin_required
def admin_gift_delete(gid):
    db = get_db()
    db.execute("DELETE FROM gifts WHERE id=?", (gid,))
    db.commit()
    log("건네기 기록 삭제", "#%d" % gid)
    return go("/admin/gifts", "건네기 기록을 삭제했어요.")


@app.route("/admin/fake_gift", methods=["POST"])
@admin_required
def admin_fake_gift():
    """테스트: 가상 참가자가 대신 빼빼로를 건넨다 (서로 건넴 화면 시험용)"""
    nick = request.form.get("nickname", "").strip()
    mode = request.form.get("mode", "to_me")
    try:
        n = max(1, min(10, int(request.form.get("n", 2))))
    except ValueError:
        n = 2
    db = get_db()
    r = db.execute("SELECT * FROM participants WHERE nickname=?", (nick,)).fetchone()
    if not r:
        return go("/admin/gifts", "그 별명의 참가자가 없어요.")
    me = row_to_person(r)
    if not me["ans"]:
        return go("/admin/gifts", "그 참가자가 아직 설문을 안 했어요.")
    cnt = 0
    if mode == "reply":
        rows = db.execute(
            "SELECT to_id FROM gifts WHERE from_id=? AND to_id IN (SELECT id FROM participants WHERE is_fake=1)",
            (me["id"],)).fetchall()
        for x in rows:
            if not db.execute("SELECT 1 FROM gifts WHERE from_id=? AND to_id=?", (x["to_id"], me["id"])).fetchone():
                db.execute("INSERT INTO gifts (from_id, to_id, message, created_at) VALUES (?,?,?,?)",
                           (x["to_id"], me["id"], "(가상) 저도 같이 얘기해보고 싶어요!", now_kst()))
                cnt += 1
    else:
        top, _ = get_top(me, 50)
        for t in top:
            if cnt >= n:
                break
            p = t["p"]
            if not p["is_fake"]:
                continue
            if db.execute("SELECT 1 FROM gifts WHERE from_id=? AND to_id=?", (p["id"], me["id"])).fetchone():
                continue
            db.execute("INSERT INTO gifts (from_id, to_id, message, created_at) VALUES (?,?,?,?)",
                       (p["id"], me["id"], "(가상) 반가워요! 같이 이야기해요", now_kst()))
            cnt += 1
    db.commit()
    log("가상 건네기", "%s 대상 %s %d건" % (nick, mode, cnt))
    return go("/admin/gifts", "가상 건네기 %d건을 만들었어요." % cnt)


@app.route("/admin/reports")
@admin_required
def admin_reports():
    rows = get_db().execute(
        "SELECT * FROM reports ORDER BY (status='new') DESC, id DESC").fetchall()
    return page(ADMIN_REPORTS, theme="admin", tab="reports", reports=[dict(r) for r in rows],
                flash=request.args.get("m"))


@app.route("/admin/report_done/<int:rid>", methods=["POST"])
@admin_required
def admin_report_done(rid):
    db = get_db()
    db.execute("UPDATE reports SET status='done' WHERE id=?", (rid,))
    db.commit()
    log("문의 처리완료", "#%d" % rid)
    return go("/admin/reports", "처리완료로 표시했어요.")


@app.route("/admin/report_delete/<int:rid>", methods=["POST"])
@admin_required
def admin_report_delete(rid):
    db = get_db()
    db.execute("DELETE FROM reports WHERE id=?", (rid,))
    db.commit()
    log("문의 삭제", "#%d" % rid)
    return go("/admin/reports", "문의를 삭제했어요.")


@app.route("/admin/logs")
@admin_required
def admin_logs():
    rows = get_db().execute("SELECT * FROM admin_logs ORDER BY id DESC LIMIT 200").fetchall()
    return page(ADMIN_LOGS, theme="admin", tab="logs", logs=[dict(r) for r in rows],
                flash=request.args.get("m"))


@app.route("/admin/tools")
@admin_required
def admin_tools():
    return page(ADMIN_TOOLS, theme="admin", tab="tools", resets=RESET_INFO, flash=request.args.get("m"))


@app.route("/admin/confirm/<scope>")
@admin_required
def admin_confirm(scope):
    if scope not in RESET_DICT:
        return go("/admin/tools", "알 수 없는 요청이에요.")
    name, desc = RESET_DICT[scope]
    return page(ADMIN_CONFIRM, theme="admin", tab="tools", scope=scope, name=name, desc=desc,
                cnt=reset_count(scope))


@app.route("/admin/reset", methods=["POST"])
@admin_required
def admin_reset():
    scope = request.form.get("scope")
    if scope not in RESET_DICT:
        return go("/admin/tools", "알 수 없는 요청이에요.")
    db = get_db()
    cnt = reset_count(scope)
    if scope == "fake":
        db.execute("DELETE FROM gifts WHERE from_id IN (SELECT id FROM participants WHERE is_fake=1) "
                   "OR to_id IN (SELECT id FROM participants WHERE is_fake=1)")
        db.execute("DELETE FROM participants WHERE is_fake=1")
        msg = "가상 참가자를 삭제했어요."
    elif scope == "answers":
        db.execute("UPDATE participants SET answers=NULL WHERE is_fake=0")
        msg = "실제 참가자의 설문 답을 초기화했어요."
    elif scope == "gifts":
        db.execute("DELETE FROM gifts")
        msg = "건네기 기록을 초기화했어요."
    elif scope == "orders":
        db.execute("DELETE FROM orders")
        db.execute("UPDATE participants SET bonus=0")
        msg = "모의 결제를 초기화했어요."
    elif scope == "reports":
        db.execute("DELETE FROM reports")
        msg = "문의함을 비웠어요."
    elif scope == "logs":
        db.execute("DELETE FROM admin_logs")
        msg = "활동 로그를 지웠어요."
    else:   # all
        if request.form.get("confirm", "").strip() != "초기화":
            return go("/admin/confirm/all", "확인 칸에 '초기화'를 정확히 입력해야 해요.")
        db.execute("DELETE FROM gifts")
        db.execute("DELETE FROM orders")
        db.execute("DELETE FROM participants")
        db.execute("DELETE FROM sqlite_sequence WHERE name IN ('participants','gifts','orders')")
        msg = "모든 데이터를 초기화했어요."
    db.commit()
    log("초기화", "%s (%d건)" % (RESET_DICT[scope][0], cnt))
    return go("/admin/tools", msg)


@app.route("/admin/seed", methods=["POST"])
@admin_required
def admin_seed():
    """테스트용 가상 참가자 생성 (이전 가상 참가자는 지우고 다시 만든다)"""
    try:
        n = max(2, min(100, int(request.form.get("n", 20))))
    except ValueError:
        n = 20
    rnd = random.Random(1111)  # 같은 값으로 고정 (발표 때도 같은 데이터)
    db = get_db()
    db.execute("DELETE FROM gifts WHERE from_id IN (SELECT id FROM participants WHERE is_fake=1) "
               "OR to_id IN (SELECT id FROM participants WHERE is_fake=1)")
    db.execute("DELETE FROM participants WHERE is_fake=1")
    for i in range(n):
        gender = "여성" if i % 2 == 0 else "남성"
        ans = {
            "fav": rnd.choices(FLAVORS, weights=[22, 50, 16, 6, 5, 1])[0],
            "eat": rnd.choice(EATS), "coat": rnd.randint(1, 5),
            "gift": rnd.choice(GIFTS), "date": rnd.choice(DATES),
            "expr": rnd.randint(1, 5), "plan": rnd.randint(1, 5),
            "mbti": "".join(rnd.choice(ax) for ax in AXES),
            "pref": [rnd.choice([ax[0], ax[1], "any"]) for ax in AXES],
            "older": rnd.randint(0, 5), "younger": rnd.randint(0, 5),
        }
        db.execute(
            "INSERT INTO participants (nickname, pin_hash, gender, age, insta, answers, is_fake, created_at) "
            "VALUES (?,?,?,?,?,?,1,?)",
            ("가상_%s%02d" % ("여" if gender == "여성" else "남", i + 1),
             generate_password_hash("0000"), gender, rnd.randint(19, 25),
             "pepe_fake%02d" % (i + 1), json.dumps(ans, ensure_ascii=False), now_kst()),
        )
    db.commit()
    log("가상 참가자 생성", "%d명" % n)
    return go("/admin/tools", "가상 참가자 %d명을 만들었어요." % n)

# ------------------------------------------------------------
# [4차 추가] 은밀한 개발자 입구 + 쪽지함 + 빼빼로 답장
# ------------------------------------------------------------

# (1) 주소를 짧게 쳐도 개발자 페이지로 가게 한다  (/dev, /d)
@app.route("/dev")
@app.route("/d")
def dev_entry():
    return redirect("/admin")


# (2) 모든 화면에 '숨은 버튼'과 '쪽지함 버튼'을 끼워 넣는다
#  - 숨은 버튼: 화면 맨 아래 가운데를 1.5초 안에 5번 톡톡톡톡톡 누르면 개발자 페이지로 이동
#  - 쪽지함 버튼: 로그인한 사용자 화면 왼쪽 아래에 표시, 아직 답장 안 한 쪽지 수가 빨간 숫자로 나온다
SECRET_HTML = (
    '<div id="pp-sec" style="position:fixed;left:50%;bottom:0;width:70px;height:36px;'
    'margin-left:-35px;z-index:9998;-webkit-tap-highlight-color:transparent;"></div>'
    '<script>(function(){var e=document.getElementById("pp-sec"),c=0,t=0;'
    'e.addEventListener("click",function(){var n=Date.now();if(n-t>1500){c=0;}t=n;c++;'
    'if(c>=5){location.href="/admin";}});})();</script>'
)


# 핸드폰 화면에서 위쪽 로고가 2줄로 깨지지 않게 글자를 줄이는 스타일 (화면 폭 520px 이하일 때만 적용)
MOBILE_FIX = (
    '<style>@media (max-width:520px){'
    'header{padding:10px 12px !important;gap:8px !important;flex-wrap:nowrap !important;}'
    '.logo,header .logo,header .logo *{font-size:clamp(13px,4.3vw,17px) !important;'
    'white-space:nowrap !important;letter-spacing:0 !important;}'
    'header a,header span{font-size:13px !important;white-space:nowrap !important;}'
    'header svg{width:20px !important;height:20px !important;}'
    '}</style>'
)


def inbox_pill(n):
    badge = ('<span style="background:#d7263d;color:#fff;border-radius:10px;padding:1px 7px;'
             'margin-left:6px;font-size:13px;">%d</span>' % n) if n else ""
    return ('<a href="/inbox" style="position:fixed;left:14px;bottom:14px;z-index:9997;'
            'background:#4a2c1a;color:#fff6e5;padding:12px 18px;border-radius:26px;'
            'text-decoration:none;font-size:15px;box-shadow:0 3px 10px rgba(0,0,0,.3);">'
            '쪽지함%s</a>' % badge)


@app.after_request
def inject_extras(resp):
    try:
        if resp.status_code != 200 or resp.mimetype != "text/html":
            return resp
        if request.path.startswith("/admin") or request.path.startswith("/static"):
            return resp
        html = resp.get_data(as_text=True)
        pos = html.rfind("</body>")
        if pos < 0:
            return resp
        extra = MOBILE_FIX + SECRET_HTML
        uid = session.get("uid")
        if uid and request.path != "/inbox":
            n = get_db().execute(
                "SELECT COUNT(*) FROM gifts g JOIN participants p ON p.id=g.from_id "
                "WHERE g.to_id=? AND p.banned=0 AND NOT EXISTS "
                "(SELECT 1 FROM gifts r WHERE r.from_id=? AND r.to_id=g.from_id)", (uid, uid)).fetchone()[0]
            extra += inbox_pill(n)
        resp.set_data(html[:pos] + extra + html[pos:])
    except Exception:
        pass     # 끼워 넣기에 실패해도 원래 화면은 그대로 보여준다
    return resp


# (3) 쪽지함 화면
INBOX = """
<div class="card">
  <h2>쪽지함</h2>
  <p class="small">나에게 빼빼로를 건넨 사람들이에요. 내 TOP3 밖의 사람이 보냈어도 여기서 확인하고 답장할 수 있어요.</p>
  <a class="btn sub" href="/result">내 결과 보기</a>
  <a class="btn sub" href="/inbox">새로고침</a>
</div>

<div class="card">
  <h3>받은 빼빼로 ({{ received|length }})</h3>
  {% if not received %}<p class="small">아직 받은 빼빼로가 없어요. 조금만 기다려 보세요!</p>{% endif %}
  {% for r in received %}
  <div style="border-top:1px solid #eadfcf;padding:14px 0;">
    <b>{{ r.nickname }}</b> ({{ r.age }}세{% if r.mbti %} · {{ r.mbti }}{% endif %})
    {% if r.idx is not none %}
      <div class="score" style="font-size:26px">{{ r.idx }}점</div>
      <div class="small">{{ r.rank_text }} · 내 기준 {{ r.s1 }}점 / 상대 기준 {{ r.s2 }}점</div>
    {% endif %}
    <div style="background:#f6f1e8;border-radius:12px;padding:10px 14px;margin:10px 0;">
      “{{ r.msg }}”<div class="small">{{ r.at }}</div>
    </div>
    {% if r.replied %}
      <div class="msg" style="background:#e5f6e8;border-left-color:#2e9e4f">
        서로 건넸어요!
        {% if r.insta %}인스타그램:
          <a href="{{ r.insta_url }}" target="_blank" rel="noopener">@{{ r.insta }}</a>{% endif %}
      </div>
    {% else %}
      <form method="post" action="/reply/{{ r.id }}"
            onsubmit="return confirm('{{ r.nickname }}님에게 빼빼로 답장을 보낼까요?');">
        <label>답장 쪽지 (고치지 않으면 기본 문구가 가요)</label>
        <input type="text" name="message" maxlength="60" value="{{ default_msg }}">
        <button type="submit">빼빼로 답장 보내기</button>
      </form>
    {% endif %}
  </div>
  {% endfor %}
</div>

<div class="card">
  <h3>내가 보낸 빼빼로 ({{ sent|length }})</h3>
  {% if not sent %}<p class="small">아직 보낸 빼빼로가 없어요. 결과 화면에서 건네 보세요!</p>{% endif %}
  {% for s in sent %}
  <div style="border-top:1px solid #eadfcf;padding:12px 0;">
    <b>{{ s.nickname }}</b> ({{ s.age }}세) · <span class="small">{{ s.at }}</span>
    <div>내 쪽지: “{{ s.msg }}”</div>
    {% if s.reply_msg is not none %}
      <div class="msg" style="background:#e5f6e8;border-left-color:#2e9e4f">
        답장이 왔어요: “{{ s.reply_msg }}”
        {% if s.insta %}<br>인스타그램: <a href="{{ s.insta_url }}" target="_blank" rel="noopener">@{{ s.insta }}</a>{% endif %}
      </div>
    {% else %}
      <div class="small">아직 답장을 기다리고 있어요.</div>
    {% endif %}
  </div>
  {% endfor %}
</div>
"""


def insta_link(insta):
    return "https://www.instagram.com/" + quote(insta) if insta else ""


@app.route("/inbox")
def inbox():
    me = current_user()
    if not me:
        return go("/", "먼저 가입하거나 로그인해 주세요.")
    db = get_db()

    # 내 기준 전체 순위표 (점수 + 순위를 쪽지에 붙이려고)
    ranks = {}
    if me["ans"]:
        allr, _ = get_top(me, 9999)
        for i, t in enumerate(allr):
            ranks[t["p"]["id"]] = (i + 1, t)

    rows = db.execute(
        "SELECT p.*, g.message AS gmsg, g.created_at AS gat, "
        "EXISTS(SELECT 1 FROM gifts r WHERE r.from_id=? AND r.to_id=g.from_id) AS replied "
        "FROM gifts g JOIN participants p ON p.id=g.from_id "
        "WHERE g.to_id=? AND p.banned=0 ORDER BY replied ASC, g.id DESC", (me["id"], me["id"])).fetchall()
    received = []
    for r in rows:
        p = row_to_person(r)
        item = {"id": p["id"], "nickname": p["nickname"], "age": p["age"],
                "mbti": p["ans"]["mbti"] if p["ans"] else "",
                "msg": r["gmsg"] or DEFAULT_MSG, "at": (r["gat"] or "")[:16],
                "replied": bool(r["replied"]), "insta": p["insta"], "insta_url": insta_link(p["insta"]),
                "idx": None, "s1": None, "s2": None, "rank_text": ""}
        if me["ans"] and p["ans"]:
            idx, _, s1, s2 = pair_index(me, p)
            rk = ranks.get(p["id"], (0, None))[0]
            item.update(idx=idx, s1=s1, s2=s2,
                        rank_text=("내 TOP3 중 %d위" % rk) if 0 < rk <= 3 else ("내 순위 %d위 (TOP3 밖)" % rk))
        received.append(item)

    srows = db.execute(
        "SELECT p.nickname, p.age, p.insta, g.message, g.created_at, "
        "(SELECT r.message FROM gifts r WHERE r.from_id=g.to_id AND r.to_id=g.from_id) AS reply_msg "
        "FROM gifts g JOIN participants p ON p.id=g.to_id "
        "WHERE g.from_id=? AND p.banned=0 ORDER BY g.id DESC", (me["id"],)).fetchall()
    sent = [{"nickname": s["nickname"], "age": s["age"], "msg": s["message"] or DEFAULT_MSG,
             "at": (s["created_at"] or "")[:16], "reply_msg": s["reply_msg"],
             "insta": s["insta"] if s["reply_msg"] is not None else "",
             "insta_url": insta_link(s["insta"])} for s in srows]

    return page(INBOX, user=me, received=received, sent=sent, default_msg=DEFAULT_MSG,
                flash=request.args.get("m"))


@app.route("/reply/<int:from_id>", methods=["POST"])
def reply(from_id):
    me = current_user()
    if not me:
        return go("/", "먼저 가입하거나 로그인해 주세요.")
    db = get_db()
    got = db.execute("SELECT 1 FROM gifts g JOIN participants p ON p.id=g.from_id "
                     "WHERE g.from_id=? AND g.to_id=? AND p.banned=0", (from_id, me["id"])).fetchone()
    if not got:
        return go("/inbox", "그 사람이 보낸 빼빼로가 없어요.")
    msg = (request.form.get("message") or "").strip()[:60] or DEFAULT_MSG
    try:
        db.execute("INSERT INTO gifts (from_id, to_id, message, created_at) VALUES (?,?,?,?)",
                   (me["id"], from_id, msg, now_kst()))
        db.commit()
    except sqlite3.IntegrityError:
        return go("/inbox", "이미 답장을 보냈어요.")
    return go("/inbox", "답장을 보냈어요! 서로 건넸으니 인스타그램 아이디가 공개돼요.")



if __name__ == "__main__":
    app.run(debug=True)
