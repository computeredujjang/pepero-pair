import os
import json
import random
import sqlite3
from math import sqrt
from datetime import datetime

from flask import Flask, request, redirect, session, render_template_string, g
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = "pepero-pair-1111-secret"   # 로그인 유지용 (데모용 값)
SEED_KEY = "1111"                            # 가상 참가자 생성용 비밀 키

# DB 파일은 이 파일과 같은 폴더에 만든다 (서버에서도 경로가 헷갈리지 않게 절대경로)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "pepero.db")

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
            created_at TEXT
        )
    """)
    conn.commit()
    conn.close()


init_db()


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
        "SELECT * FROM participants WHERE gender=? AND answers IS NOT NULL", (other_gender,)
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
    return row_to_person(r) if r else None


# ------------------------------------------------------------
# 화면 (HTML) - 한 파일에 같이 둔다
# ------------------------------------------------------------
LAYOUT = """
<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PEPERO PAIR 11:11</title>
<style>
 :root { --choco:#4a2c1a; --biscuit:#e9c58a; --cream:#fff6e5; --berry:#e8607c; --red:#d7263d; }
 * { box-sizing: border-box; }
 body { margin:0; background:var(--cream); color:var(--choco);
        font-family:'Malgun Gothic','Apple SD Gothic Neo',sans-serif; }
 header { background:var(--choco); color:var(--cream); padding:14px 18px; display:flex;
          justify-content:space-between; align-items:center; }
 header a { color:var(--biscuit); text-decoration:none; font-size:14px; }
 .logo { font-weight:bold; font-size:20px; letter-spacing:1px; }
 main { max-width:640px; margin:0 auto; padding:18px; }
 .card { background:#fff; border-radius:16px; padding:18px; margin-bottom:16px;
         box-shadow:0 2px 8px rgba(74,44,26,.12); }
 h1,h2,h3 { margin-top:0; }
 label { display:block; margin:12px 0 4px; font-weight:bold; }
 input[type=text], input[type=password], input[type=number], select {
    width:100%; padding:10px; border:2px solid var(--biscuit); border-radius:10px; font-size:16px; }
 .opts label { font-weight:normal; display:inline-block; margin:4px 10px 4px 0; }
 button, .btn { background:var(--red); color:#fff; border:0; padding:12px 20px; border-radius:24px;
        font-size:16px; cursor:pointer; text-decoration:none; display:inline-block; margin-top:14px; }
 .btn.sub { background:var(--choco); }
 .msg { background:#ffe3e8; border-left:5px solid var(--red); padding:10px 14px; border-radius:8px; margin-bottom:14px; }
 .small { font-size:13px; opacity:.75; }
 .row2 { display:grid; grid-template-columns:1fr 1fr; gap:10px; }
 .axis { display:grid; grid-template-columns:1fr 1fr; gap:8px; }
 .rank { font-size:22px; font-weight:bold; color:var(--red); }
 .score { font-size:34px; font-weight:bold; }
 .gauge { display:flex; align-items:center; margin:12px 0 4px; }
 .stick { flex:1; height:20px; border-radius:10px; min-width:40px; }
 .stick.l { background:linear-gradient(to right,var(--biscuit) 0 28%,var(--choco) 28% 100%); }
 .stick.r { background:linear-gradient(to left,var(--biscuit) 0 28%,var(--choco) 28% 100%); }
 .gap { height:2px; border-bottom:3px dotted var(--berry); transition:width .5s; }
 ul { padding-left:20px; margin:8px 0; }
</style>
</head>
<body>
<header>
  <span class="logo">🍫 PEPERO PAIR 11:11</span>
  <span>
   {% if user %}{{ user.nickname }}님 · <a href="/logout">나가기</a>{% else %}<a href="/">처음으로</a>{% endif %}
  </span>
</header>
<main>
{% if flash %}<div class="msg">{{ flash }}</div>{% endif %}
{{ content|safe }}
</main>
</body>
</html>
"""


def page(body, **ctx):
    inner = render_template_string(body, **ctx)
    flash = ctx.pop("flash", None)
    return render_template_string(LAYOUT, content=inner, user=current_user(), flash=flash)


HOME = """
<div class="card">
  <h2>빼빼로 두 개가 만나는 거리를 계산합니다</h2>
  <p>설문에 답하면 나와 가장 가까운 빼빼로를 찾아드려요.<br>
     현재 참가자 <b>{{ total }}명</b> (설문 완료 {{ done }}명)</p>
</div>

<div class="card">
  <h3>🍫 처음이에요 (가입)</h3>
  <form method="post" action="/register">
    <label>별명</label>
    <input type="text" name="nickname" maxlength="12" required>
    <label>PIN 4자리 숫자 (다시 들어올 때 필요해요)</label>
    <input type="password" name="pin" maxlength="4" pattern="[0-9]{4}" inputmode="numeric" required>
    <div class="row2">
      <div>
        <label>성별</label>
        <select name="gender" required>
          <option value="여성">여성</option>
          <option value="남성">남성</option>
        </select>
      </div>
      <div>
        <label>나이</label>
        <input type="number" name="age" min="15" max="80" required>
      </div>
    </div>
    <label>인스타그램 ID (선택, 서로 수락해야 공개)</label>
    <input type="text" name="insta" placeholder="@없이 입력">
    <label style="font-weight:normal"><input type="checkbox" name="agree" required>
      개인정보(별명·나이·성별·인스타)를 행사 매칭에만 쓰고 종료 후 삭제하는 데 동의합니다.</label>
    <button type="submit">포장지 뜯기 →</button>
  </form>
</div>

<div class="card">
  <h3>다시 왔어요 (로그인)</h3>
  <form method="post" action="/login">
    <label>별명</label>
    <input type="text" name="nickname" required>
    <label>PIN 4자리</label>
    <input type="password" name="pin" maxlength="4" inputmode="numeric" required>
    <button class="sub" type="submit">들어가기</button>
  </form>
</div>
"""

SURVEY = """
<div class="card">
  <h2>🍫 시식 테스트 (10문항)</h2>
  <p class="small">{{ user.nickname }}님, 편하게 골라 주세요. 다시 제출하면 답이 덮어써져요.</p>
</div>
<form method="post">
 <div class="card">
  <label>1. 가장 좋아하는 빼빼로는?</label>
  <div class="opts">{% for f in flavors %}
    <label><input type="radio" name="fav" value="{{ f }}" {{ 'checked' if a.get('fav')==f }} required> {{ f }}</label>{% endfor %}</div>

  <label>2. 빼빼로 먹는 나만의 방식은?</label>
  <div class="opts">{% for f in eats %}
    <label><input type="radio" name="eat" value="{{ f }}" {{ 'checked' if a.get('eat')==f }} required> {{ f }}</label>{% endfor %}</div>

  <label>3. 초코 코팅은 어느 정도가 좋아요?</label>
  <div class="opts">{% for l in coat %}
    <label><input type="radio" name="coat" value="{{ loop.index }}" {{ 'checked' if a.get('coat')|string==loop.index|string }} required> {{ l }}</label>{% endfor %}</div>

  <label>4. 가장 받고 싶은 빼빼로 선물은?</label>
  <div class="opts">{% for f in gifts %}
    <label><input type="radio" name="gift" value="{{ f }}" {{ 'checked' if a.get('gift')==f }} required> {{ f }}</label>{% endfor %}</div>

  <label>5. 좋아하는 사람과 함께하고 싶은 활동은?</label>
  <div class="opts">{% for f in dates %}
    <label><input type="radio" name="date" value="{{ f }}" {{ 'checked' if a.get('date')==f }} required> {{ f }}</label>{% endfor %}</div>

  <label>6. 좋아하면 말과 행동으로 표현하는 편인가요?</label>
  <div class="opts">{% for l in expr %}
    <label><input type="radio" name="expr" value="{{ loop.index }}" {{ 'checked' if a.get('expr')|string==loop.index|string }} required> {{ l }}</label>{% endfor %}</div>

  <label>7. 데이트는 계획파? 즉흥파?</label>
  <div class="opts">{% for l in plan %}
    <label><input type="radio" name="plan" value="{{ loop.index }}" {{ 'checked' if a.get('plan')|string==loop.index|string }} required> {{ l }}</label>{% endfor %}</div>
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
  <div class="small">인스타 공개·빼빼로 건네기는 2차에서 추가돼요.</div>
</div>
{% endfor %}
"""


# ------------------------------------------------------------
# 라우트
# ------------------------------------------------------------
@app.route("/")
def home():
    db = get_db()
    total = db.execute("SELECT COUNT(*) FROM participants").fetchone()[0]
    done = db.execute("SELECT COUNT(*) FROM participants WHERE answers IS NOT NULL").fetchone()[0]
    return page(HOME, total=total, done=done, flash=request.args.get("m"))


@app.route("/register", methods=["POST"])
def register():
    nickname = request.form.get("nickname", "").strip()
    pin = request.form.get("pin", "").strip()
    gender = request.form.get("gender", "")
    insta = request.form.get("insta", "").strip().lstrip("@")

    try:
        age = int(request.form.get("age", ""))
    except ValueError:
        return redirect("/?m=나이를 숫자로 입력해 주세요.")

    if not nickname or len(nickname) > 12:
        return redirect("/?m=별명은 1~12자로 입력해 주세요.")
    if not (pin.isdigit() and len(pin) == 4):
        return redirect("/?m=PIN은 숫자 4자리예요.")
    if gender not in ("여성", "남성"):
        return redirect("/?m=성별을 선택해 주세요.")
    if not (15 <= age <= 80):
        return redirect("/?m=나이 범위를 확인해 주세요.")

    db = get_db()
    if db.execute("SELECT 1 FROM participants WHERE nickname=?", (nickname,)).fetchone():
        return redirect("/?m=이미 있는 별명이에요. 다른 별명을 쓰거나 로그인해 주세요.")

    cur = db.execute(
        "INSERT INTO participants (nickname, pin_hash, gender, age, insta, created_at) VALUES (?,?,?,?,?,?)",
        (nickname, generate_password_hash(pin), gender, age, insta, datetime.now().isoformat()),
    )
    db.commit()
    session["uid"] = cur.lastrowid
    return redirect("/survey")


@app.route("/login", methods=["POST"])
def login():
    nickname = request.form.get("nickname", "").strip()
    pin = request.form.get("pin", "").strip()
    r = get_db().execute("SELECT * FROM participants WHERE nickname=?", (nickname,)).fetchone()
    if not r or not check_password_hash(r["pin_hash"], pin):
        return redirect("/?m=별명 또는 PIN이 맞지 않아요.")
    session["uid"] = r["id"]
    return redirect("/result" if r["answers"] else "/survey")


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")


@app.route("/survey", methods=["GET", "POST"])
def survey():
    user = current_user()
    if not user:
        return redirect("/?m=먼저 가입하거나 로그인해 주세요.")

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
        return redirect("/?m=먼저 가입하거나 로그인해 주세요.")
    if not user["ans"]:
        return redirect("/survey")
    top, n_other = get_top(user, 3)
    return page(RESULT, user=user, top=top, n_other=n_other)


@app.route("/seed")
def seed():
    """테스트용 가상 참가자 생성: /seed?key=1111  (이전 가상 참가자는 지우고 다시 만든다)"""
    if request.args.get("key") != SEED_KEY:
        return "key가 맞지 않아요.", 403
    n = int(request.args.get("n", 20))
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
             "pepe_user%02d" % (i + 1), json.dumps(ans, ensure_ascii=False),
             datetime.now().isoformat()),
        )
    db.commit()
    return "가상 참가자 %d명 생성 완료. <a href='/'>처음으로</a>" % n


if __name__ == "__main__":
    app.run(debug=True)
