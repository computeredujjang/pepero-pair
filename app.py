import os
import re
import json
import random
import sqlite3
from math import sqrt
from datetime import datetime
from urllib.parse import quote
from functools import wraps

from flask import Flask, request, redirect, session, render_template_string, g, url_for
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = "pepero-pair-1111-secret"   # 로그인 유지용 (데모용 값)

# ★ 개발자 페이지 비밀번호: 발표 전에 꼭 다른 값으로 바꾸세요! (주소: /admin)
ADMIN_PW = os.environ.get("PEPERO_ADMIN_PW", "choco1111")

# DB 파일은 이 파일과 같은 폴더에 만든다 (서버에서도 경로가 헷갈리지 않게 절대경로)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "pepero.db")

# 주소에 쓰는 성별 코드  (/join/w = 여성,  /join/m = 남성)
GCODE = {"w": "여성", "m": "남성"}
INSTA_RE = re.compile(r"^[a-z0-9._]{1,30}$")

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
    conn.execute("""
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
            created_at TEXT
        )
    """)
    # 1차 때 만든 DB에는 banned 칸이 없으므로 자동으로 추가한다
    cols = [r[1] for r in conn.execute("PRAGMA table_info(participants)").fetchall()]
    if "banned" not in cols:
        conn.execute("ALTER TABLE participants ADD COLUMN banned INTEGER DEFAULT 0")
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
    return round(sqrt(s1 * s2), 1), r1


def row_to_person(r):
    return {
        "id": r["id"],
        "nickname": r["nickname"],
        "gender": r["gender"],
        "age": r["age"],
        "insta": r["insta"],
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
        idx, reasons = pair_index(me, p)
        result.append({"p": p, "idx": idx, "reasons": reasons})
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
    for ext in ("png", "jpg", "jpeg", "webp"):
        fn = "%s.%s" % (name, ext)
        if os.path.exists(os.path.join(BASE_DIR, "static", fn)):
            return url_for("static", filename=fn)
    return None


# ------------------------------------------------------------
# 화면 (HTML) - 한 파일에 같이 둔다
#  LAYOUT = 모든 페이지의 겉틀(머리글/색/글꼴)
#  SPLASH, ENTER, JOIN, SURVEY, RESULT, ADMIN = 각 페이지의 속 내용
# ------------------------------------------------------------
LAYOUT = """
<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>빼빼로 PAIR 11:11</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Jua&family=Noto+Sans+KR:wght@400;700&display=swap" rel="stylesheet">
<style>
 /* ===== 색깔은 여기 변수만 바꾸면 전체가 바뀐다 ===== */
 :root { --choco:#4a2c1a; --biscuit:#e9c58a; --cream:#fff6e5; --berry:#e8607c; --red:#d7263d;
         --accent:#d7263d; --bg:#fff6e5; }
 body.t-w { --accent:#e8607c; --bg:#fff0f3; }      /* 여성 페이지: 딸기 핑크 */
 body.t-m { --accent:#2f6fa8; --bg:#eef5fb; }      /* 남성 페이지: 블루 */
 body.t-admin { --accent:#37474f; --bg:#eceff1; }  /* 개발자 페이지: 회색 */

 * { box-sizing: border-box; }
 body { margin:0; background:var(--bg); color:var(--choco); line-height:1.55;
        font-family:'Noto Sans KR','Malgun Gothic','Apple SD Gothic Neo',sans-serif; }
 h1,h2,h3,.logo { font-family:'Jua','Noto Sans KR',sans-serif; font-weight:400; }
 h1,h2,h3 { margin-top:0; }
 header { background:var(--choco); color:var(--cream); padding:12px 18px; display:flex;
          justify-content:space-between; align-items:center; border-bottom:4px solid var(--biscuit); }
 header a { color:var(--biscuit); text-decoration:none; font-size:14px; }
 header .logo { font-size:22px; color:var(--cream); }
 header img { height:28px; vertical-align:middle; margin-right:6px; }
 main { max-width:620px; margin:0 auto; padding:18px 16px 40px; }

 .card { background:#fff; border-radius:18px; padding:20px; margin-bottom:16px;
         box-shadow:0 3px 10px rgba(74,44,26,.12); }
 .hero { text-align:center; border:3px solid var(--choco);
         background:linear-gradient(180deg,#fff 0%,#fff3dc 100%); }
 .hero h1 { font-size:31px; line-height:1.3; margin:8px 0 6px; }
 .hero-img { max-width:100%; max-height:260px; border-radius:14px; margin-bottom:6px; }
 .lead { font-size:17px; margin:6px 0 16px; }
 .eyebrow { letter-spacing:3px; font-size:12px; color:var(--accent); font-weight:bold; margin:0; }
 .notice { border:2px dashed var(--accent); }

 /* 11:11 빼빼로 막대 장식 (그림 없이 CSS로만 그림) */
 .sticks { display:flex; justify-content:center; align-items:flex-end; gap:10px; margin:4px 0 12px; }
 .sticks i { display:block; width:16px; height:100px; border-radius:9px 9px 4px 4px;
             background:linear-gradient(to bottom,var(--choco) 0 68%,var(--biscuit) 68% 100%);
             animation:bob 2.4s ease-in-out infinite; }
 .sticks i:nth-child(even) { animation-delay:.3s; }
 .sticks b { font-family:'Jua',sans-serif; font-size:44px; align-self:center; color:var(--accent); }
 @keyframes bob { 50% { transform:translateY(-8px); } }

 label { display:block; margin:14px 0 5px; font-weight:bold; }
 input[type=text], input[type=password], input[type=number], select {
    width:100%; padding:11px; border:2px solid var(--biscuit); border-radius:12px; font-size:16px;
    font-family:inherit; background:#fffdf8; }
 input:focus, select:focus { outline:none; border-color:var(--accent); }

 button, .btn { background:var(--accent); color:#fff; border:0; padding:12px 22px; border-radius:26px;
        font-size:16px; font-family:inherit; cursor:pointer; text-decoration:none; display:inline-block; margin-top:14px; }
 .btn.big { display:block; text-align:center; font-size:19px; padding:14px; }
 .btn.sub, button.sub { background:var(--choco); }
 .btn.ig { background:linear-gradient(45deg,#f9a825,#e1306c,#833ab4); }
 .btn.danger, button.danger { background:#b71c1c; }
 .btn.mini, button.mini { padding:6px 12px; font-size:13px; margin-top:6px; }

 .gbtn { display:flex; flex-direction:column; align-items:center; gap:4px; padding:24px 8px;
         border-radius:20px; text-decoration:none; color:#fff; font-size:38px; text-align:center; }
 .gbtn b { font-size:18px; font-family:'Jua',sans-serif; font-weight:400; }
 .gbtn span { font-size:12px; opacity:.92; }
 .gbtn.w { background:#e8607c; }  .gbtn.m { background:#2f6fa8; }

 .msg { background:#ffe3e8; border-left:5px solid var(--red); padding:10px 14px; border-radius:8px; margin-bottom:14px; }
 .small { font-size:13px; opacity:.75; }
 .row2 { display:grid; grid-template-columns:1fr 1fr; gap:10px; }
 .axis { display:grid; grid-template-columns:1fr 1fr; gap:8px; }

 /* 설문 보기: 동그란 알약 버튼 */
 .opts { display:flex; flex-wrap:wrap; gap:8px; }
 .chip { position:relative; margin:0; font-weight:normal; }
 .chip input { position:absolute; opacity:0; pointer-events:none; }
 .chip span { display:inline-block; padding:8px 14px; border:2px solid var(--biscuit); border-radius:22px; background:#fffdf8; }
 .chip input:checked + span { background:var(--accent); border-color:var(--accent); color:#fff; font-weight:bold; }
 .chip input:focus + span { outline:2px solid var(--choco); }

 .rank { font-size:24px; font-family:'Jua',sans-serif; color:var(--accent); }
 .score { font-size:38px; font-family:'Jua',sans-serif; }
 .gauge { display:flex; align-items:center; margin:12px 0 4px; }
 .stick { flex:1; height:20px; border-radius:10px; min-width:40px; }
 .stick.l { background:linear-gradient(to right,var(--biscuit) 0 28%,var(--choco) 28% 100%); }
 .stick.r { background:linear-gradient(to left,var(--biscuit) 0 28%,var(--choco) 28% 100%); }
 .gap { height:2px; border-bottom:3px dotted var(--berry); transition:width .5s; }
 ul { padding-left:20px; margin:8px 0; }

 /* 개발자 페이지 */
 .stats { display:grid; grid-template-columns:repeat(3,1fr); gap:8px; text-align:center; }
 .stats div { background:#f5f5f5; border-radius:12px; padding:10px 4px; }
 .stats b { display:block; font-size:24px; }
 .prow { border-bottom:1px solid #e0e0e0; padding:12px 0; }
 .prow.banned { background:#fff0f0; }
 .tag { display:inline-block; font-size:11px; padding:1px 8px; border-radius:10px; background:#eee; margin-left:4px; }
 .inline { display:inline; }
 details summary { cursor:pointer; font-size:13px; margin-top:6px; }
</style>
</head>
<body class="t-{{ theme }}">
<header>
  <a class="logo" href="/">{% if logo %}<img src="{{ logo }}" alt="">{% else %}🍫 {% endif %}빼빼로 PAIR 11:11</a>
  <span>
   {% if admin %}개발자 모드 · <a href="/admin/logout">나가기</a>
   {% elif user %}{{ user.nickname }}님 · <a href="/logout">나가기</a>
   {% else %}<a href="/enter">입장</a>{% endif %}
  </span>
</header>
<main>
{% if flash %}<div class="msg">{{ flash }}</div>{% endif %}
{{ content|safe }}
</main>
</body>
</html>
"""


def page(body, theme="", **ctx):
    flash = ctx.pop("flash", None)
    user = ctx.get("user") or current_user()
    ctx["user"] = user
    inner = render_template_string(body, **ctx)
    if not theme and user:
        theme = "w" if user["gender"] == "여성" else "m"
    return render_template_string(
        LAYOUT, content=inner, user=user, flash=flash, theme=theme,
        admin=bool(session.get("admin")), logo=static_url("logo"),
    )


# ---------- 1) 인트로(제목) 페이지 ----------
SPLASH = """
<div class="card hero">
  {% if hero %}<img class="hero-img" src="{{ hero }}" alt="">
  {% else %}<div class="sticks"><i></i><i></i><b>:</b><i></i><i></i></div>{% endif %}
  <p class="eyebrow">11 · 11 &nbsp; PEPERO DAY</p>
  <h1>빼빼로 PAIR에<br>오신 걸 환영합니다!</h1>
  <p class="lead">빼빼로로 마음을 전달해서 사랑을 키워봐요!</p>
  <a class="btn big" href="/enter">시작하기 →</a>
  <p class="small" style="margin-bottom:0">현재 참가자 <b>{{ total }}</b>명</p>
</div>

<div class="card notice">
  <h3>📷 잠깐, 인스타그램 아이디가 필요해요</h3>
  <p>빼빼로 PAIR에 참여하려면 <b>'인스타그램 아이디'</b>가 필요해요!<br>
     아이디는 다른 참가자에게 바로 공개되지 않아요. 계정이 없다면 만들고 돌아와 주세요.</p>
  <a class="btn ig" href="https://www.instagram.com/" target="_blank" rel="noopener">인스타그램 사이트로 이동하기 ↗</a>
</div>
"""

# ---------- 2) 입구: 여성/남성 선택 + 로그인 ----------
ENTER = """
<div class="card">
  <h2>처음이세요? 입구를 골라 주세요</h2>
  <div class="row2">
    <a class="gbtn w" href="/join/w">🍓<b>여성 참가자</b><span>가입하러 가기</span></a>
    <a class="gbtn m" href="/join/m">🍫<b>남성 참가자</b><span>가입하러 가기</span></a>
  </div>
</div>

<div class="card">
  <h3>다시 오셨나요? (로그인)</h3>
  <form method="post" action="/login">
    <label>별명</label>
    <input type="text" name="nickname" required>
    <label>PIN 4자리</label>
    <input type="password" name="pin" maxlength="4" inputmode="numeric" required>
    <button class="sub" type="submit">들어가기</button>
  </form>
</div>
"""

# ---------- 3) 성별별 가입 페이지 (색이 다르다) ----------
JOIN = """
<div class="card">
  <h2>{{ '🍓' if code == 'w' else '🍫' }} {{ gender }} 참가자 가입</h2>
  <p class="small">가입 후 바로 10문항 설문이 이어져요. 약 2분 걸려요.</p>
  <form method="post" action="/register">
    <input type="hidden" name="g" value="{{ code }}">
    <label>별명 (최대 12자)</label>
    <input type="text" name="nickname" maxlength="12" required>
    <label>PIN 4자리 숫자 (다시 들어올 때 필요해요)</label>
    <input type="password" name="pin" maxlength="4" pattern="[0-9]{4}" inputmode="numeric" required>
    <label>나이</label>
    <input type="number" name="age" min="15" max="80" required>
    <label>인스타그램 아이디 (필수)</label>
    <input type="text" name="insta" placeholder="@ 없이 입력 (예: pepero_lover)" required>
    <p class="small">한 아이디로 한 번만 가입할 수 있어요.
       <a href="https://www.instagram.com/" target="_blank" rel="noopener">인스타그램 열기 ↗</a></p>
    <label style="font-weight:normal"><input type="checkbox" name="agree" required>
      개인정보(별명·나이·성별·인스타)를 행사 매칭에만 쓰고 종료 후 삭제하는 데 동의합니다.</label>
    <button type="submit">포장지 뜯기 →</button>
  </form>
  <p class="small"><a href="/enter">← 입구로 돌아가기</a></p>
</div>
"""

# ---------- 4) 설문 ----------
SURVEY = """
{% macro chips(name, items, cur, numeric=False) %}
<div class="opts">{% for it in items %}{% set val = loop.index if numeric else it %}
  <label class="chip"><input type="radio" name="{{ name }}" value="{{ val }}" {{ 'checked' if cur|string == val|string }} required><span>{{ it }}</span></label>{% endfor %}</div>
{% endmacro %}

<div class="card">
  <h2>🍫 시식 테스트 (10문항)</h2>
  <p class="small">{{ user.nickname }}님, 편하게 골라 주세요. 다시 제출하면 답이 덮어써져요.</p>
</div>
<form method="post">
 <div class="card">
  <label>1. 가장 좋아하는 빼빼로는?</label>
  {{ chips('fav', flavors, a.get('fav')) }}
  <label>2. 빼빼로 먹는 나만의 방식은?</label>
  {{ chips('eat', eats, a.get('eat')) }}
  <label>3. 초코 코팅은 어느 정도가 좋아요?</label>
  {{ chips('coat', coat, a.get('coat'), True) }}
  <label>4. 가장 받고 싶은 빼빼로 선물은?</label>
  {{ chips('gift', gifts, a.get('gift')) }}
  <label>5. 좋아하는 사람과 함께하고 싶은 활동은?</label>
  {{ chips('date', dates, a.get('date')) }}
  <label>6. 좋아하면 말과 행동으로 표현하는 편인가요?</label>
  {{ chips('expr', expr, a.get('expr'), True) }}
  <label>7. 데이트는 계획파? 즉흥파?</label>
  {{ chips('plan', plan, a.get('plan'), True) }}
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

# ---------- 5) 결과 ----------
RESULT = """
<div class="card">
  <h2>💗 {{ user.nickname }}님의 TOP3</h2>
  <p class="small">이성 참가자 {{ n_other }}명 중에서 계산했어요. 새 참가자가 들어오면 결과가 바뀔 수 있어요.</p>
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
  <div class="small">인스타 공개·빼빼로 건네기는 다음 버전에서 추가돼요.</div>
</div>
{% endfor %}
"""

# ---------- 6) 개발자 페이지 ----------
ADMIN_LOGIN = """
<div class="card">
  <h2>🛠 개발자 전용</h2>
  <form method="post" action="/admin/login">
    <label>개발자 비밀번호</label>
    <input type="password" name="pw" required>
    <button type="submit">들어가기</button>
  </form>
</div>
"""

ADMIN = """
<div class="card">
  <h2>🛠 개발자 전용 관리 화면</h2>
  <div class="stats">
    <div><b>{{ st.total }}</b>전체</div>
    <div><b>{{ st.w }}</b>여성</div>
    <div><b>{{ st.m }}</b>남성</div>
    <div><b>{{ st.done }}</b>설문 완료</div>
    <div><b>{{ st.fake }}</b>가상</div>
    <div><b>{{ st.banned }}</b>차단</div>
  </div>
</div>

<div class="card">
  <h3>데이터 관리</h3>

  <form method="post" action="/admin/seed" class="inline">
    가상 참가자 <input type="number" name="n" value="20" min="2" max="100" style="width:80px"> 명
    <button class="mini" type="submit">생성 (기존 가상 참가자는 교체)</button>
  </form>
  <hr>
  <form method="post" action="/admin/reset" class="inline">
    <input type="hidden" name="scope" value="fake">
    <button class="mini sub" type="submit" onclick="return confirm('가상 참가자를 모두 삭제할까요?')">가상 참가자만 삭제</button>
  </form>
  <form method="post" action="/admin/reset" class="inline">
    <input type="hidden" name="scope" value="answers">
    <button class="mini sub" type="submit" onclick="return confirm('실제 참가자의 설문 답을 모두 지울까요? (가입 정보는 남아요)')">설문 답만 초기화</button>
  </form>
  <hr>
  <form method="post" action="/admin/reset">
    <input type="hidden" name="scope" value="all">
    <label>⚠ 전체 초기화 (모든 참가자 삭제) - 확인을 위해 <b>초기화</b> 라고 입력</label>
    <input type="text" name="confirm" placeholder="초기화" style="width:160px">
    <button class="mini danger" type="submit">전부 삭제</button>
  </form>
</div>

<div class="card">
  <h3>참가자 목록 ({{ people|length }}명)</h3>
  <p class="small">차단: 로그인·매칭에서 빠지지만 인스타 아이디는 계속 사용 중으로 남아 재가입을 막아요.<br>
     삭제: 완전히 지워져서 같은 아이디로 다시 가입할 수 있어요.</p>
  {% for p in people %}
  <div class="prow {{ 'banned' if p.banned }}">
    <b>{{ p.nickname }}</b>
    <span class="tag">{{ p.gender }}</span><span class="tag">{{ p.age }}세</span>
    <span class="tag">@{{ p.insta }}</span>
    {% if p.answers %}<span class="tag">설문 완료</span>{% else %}<span class="tag">설문 전</span>{% endif %}
    {% if p.is_fake %}<span class="tag">가상</span>{% endif %}
    {% if p.banned %}<span class="tag" style="background:#ffcdd2">차단됨</span>{% endif %}
    <br>
    <form method="post" action="/admin/ban/{{ p.id }}" class="inline">
      <button class="mini sub" type="submit">{{ '차단 해제' if p.banned else '차단' }}</button></form>
    <form method="post" action="/admin/delete/{{ p.id }}" class="inline">
      <button class="mini danger" type="submit" onclick="return confirm('{{ p.nickname }} 님을 삭제할까요?')">삭제</button></form>
    <details>
      <summary>정보 수정</summary>
      <form method="post" action="/admin/edit/{{ p.id }}">
        <div class="row2">
          <input type="text" name="nickname" value="{{ p.nickname }}" maxlength="12">
          <input type="number" name="age" value="{{ p.age }}" min="15" max="80">
        </div>
        <input type="text" name="insta" value="{{ p.insta }}" style="margin-top:8px">
        <button class="mini" type="submit">저장</button>
      </form>
    </details>
  </div>
  {% endfor %}
  {% if not people %}<p>참가자가 없어요. 위에서 가상 참가자를 만들어 보세요.</p>{% endif %}
</div>
"""


# ------------------------------------------------------------
# 라우트 - 참가자용
# ------------------------------------------------------------
@app.route("/")
def splash():
    total = get_db().execute("SELECT COUNT(*) FROM participants WHERE banned=0").fetchone()[0]
    return page(SPLASH, total=total, hero=static_url("hero"))


@app.route("/enter")
def enter():
    return page(ENTER, flash=request.args.get("m"))


@app.route("/join/<code>")
def join(code):
    if code not in GCODE:
        return redirect("/enter")
    return page(JOIN, theme=code, code=code, gender=GCODE[code], flash=request.args.get("m"))


@app.route("/register", methods=["POST"])
def register():
    code = request.form.get("g", "")
    if code not in GCODE:
        return go("/enter", "성별 입구를 먼저 골라 주세요.")
    gender = GCODE[code]
    back = "/join/" + code

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
        return go(back, "이미 가입된 인스타그램 아이디예요. 본인이라면 입구에서 로그인해 주세요.")
    if db.execute("SELECT 1 FROM participants WHERE nickname=?", (nickname,)).fetchone():
        return go(back, "이미 있는 별명이에요. 다른 별명을 써 주세요.")

    try:
        cur = db.execute(
            "INSERT INTO participants (nickname, pin_hash, gender, age, insta, created_at) VALUES (?,?,?,?,?,?)",
            (nickname, generate_password_hash(pin), gender, age, insta, datetime.now().isoformat()),
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
    session.pop("uid", None)
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
    return page(RESULT, user=user, top=top, n_other=n_other)


# ------------------------------------------------------------
# 라우트 - 개발자 전용 (/admin)
# ------------------------------------------------------------
def admin_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("admin"):
            return redirect("/admin")
        return f(*args, **kwargs)
    return wrapper


@app.route("/admin")
def admin():
    msg = request.args.get("m")
    if not session.get("admin"):
        return page(ADMIN_LOGIN, theme="admin", flash=msg)
    rows = get_db().execute("SELECT * FROM participants ORDER BY id DESC").fetchall()
    people = [dict(r) for r in rows]
    st = {
        "total": len(people),
        "w": sum(1 for p in people if p["gender"] == "여성"),
        "m": sum(1 for p in people if p["gender"] == "남성"),
        "done": sum(1 for p in people if p["answers"]),
        "fake": sum(1 for p in people if p["is_fake"]),
        "banned": sum(1 for p in people if p["banned"]),
    }
    return page(ADMIN, theme="admin", people=people, st=st, flash=msg)


@app.route("/admin/login", methods=["POST"])
def admin_login():
    if request.form.get("pw", "") == ADMIN_PW:
        session["admin"] = True
        return redirect("/admin")
    return go("/admin", "비밀번호가 맞지 않아요.")


@app.route("/admin/logout")
def admin_logout():
    session.pop("admin", None)
    return redirect("/")


@app.route("/admin/ban/<int:pid>", methods=["POST"])
@admin_required
def admin_ban(pid):
    db = get_db()
    r = db.execute("SELECT banned FROM participants WHERE id=?", (pid,)).fetchone()
    if r:
        db.execute("UPDATE participants SET banned=? WHERE id=?", (0 if r["banned"] else 1, pid))
        db.commit()
    return go("/admin", "차단 상태를 바꿨어요.")


@app.route("/admin/delete/<int:pid>", methods=["POST"])
@admin_required
def admin_delete(pid):
    db = get_db()
    db.execute("DELETE FROM participants WHERE id=?", (pid,))
    db.commit()
    return go("/admin", "삭제했어요.")


@app.route("/admin/edit/<int:pid>", methods=["POST"])
@admin_required
def admin_edit(pid):
    nickname = request.form.get("nickname", "").strip()
    insta = clean_insta(request.form.get("insta"))
    try:
        age = int(request.form.get("age", ""))
    except ValueError:
        return go("/admin", "나이는 숫자로 입력해 주세요.")
    if not nickname or len(nickname) > 12 or not (15 <= age <= 80) or not INSTA_RE.match(insta):
        return go("/admin", "입력값을 확인해 주세요. (별명 1~12자, 나이 15~80, 인스타 형식)")
    db = get_db()
    if db.execute("SELECT 1 FROM participants WHERE lower(insta)=? AND id!=?", (insta, pid)).fetchone():
        return go("/admin", "다른 사람이 쓰는 인스타그램 아이디예요.")
    try:
        db.execute("UPDATE participants SET nickname=?, age=?, insta=? WHERE id=?",
                   (nickname, age, insta, pid))
        db.commit()
    except sqlite3.IntegrityError:
        return go("/admin", "이미 있는 별명이에요.")
    return go("/admin", "수정했어요.")


@app.route("/admin/reset", methods=["POST"])
@admin_required
def admin_reset():
    scope = request.form.get("scope")
    db = get_db()
    if scope == "fake":
        db.execute("DELETE FROM participants WHERE is_fake=1")
        msg = "가상 참가자를 삭제했어요."
    elif scope == "answers":
        db.execute("UPDATE participants SET answers=NULL WHERE is_fake=0")
        msg = "실제 참가자의 설문 답을 초기화했어요."
    elif scope == "all":
        if request.form.get("confirm", "").strip() != "초기화":
            return go("/admin", "확인 칸에 '초기화'를 정확히 입력해야 전체 삭제가 돼요.")
        db.execute("DELETE FROM participants")
        db.execute("DELETE FROM sqlite_sequence WHERE name='participants'")   # 번호도 1부터 다시
        msg = "모든 데이터를 초기화했어요."
    else:
        return go("/admin", "알 수 없는 요청이에요.")
    db.commit()
    return go("/admin", msg)


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
             "pepe_fake%02d" % (i + 1), json.dumps(ans, ensure_ascii=False),
             datetime.now().isoformat()),
        )
    db.commit()
    return go("/admin", "가상 참가자 %d명을 만들었어요." % n)


if __name__ == "__main__":
    app.run(debug=True)
