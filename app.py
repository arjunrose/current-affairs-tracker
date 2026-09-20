"""
Current Affairs Study Studio  ·  app.py
=======================================
Single-file Streamlit app: Google sign-in, Supabase persistence, daily/custom/review
quizzes, QP + leagues + daily missions, rankings, learner analytics and a full admin
control room.

Setup checklist (details in the accompanying files):
  1. requirements.txt            -> pip dependencies
  2. .streamlit/config.toml      -> forces the light base theme (fixes dark dropdowns/date pickers)
  3. supabase_setup.sql          -> run once in the Supabase SQL editor (safe to re-run)
  4. secrets.toml                -> [auth], [supabase] and optional [app] sections
"""
from __future__ import annotations

import calendar
import hashlib
import html
import inspect
import io
import random
import re
import time
import uuid
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st
from supabase import create_client

st.set_page_config(
    page_title="Current Affairs • Study Studio",
    page_icon="🌿",
    layout="wide",
    initial_sidebar_state="expanded",
)

# =========================================================
# 1. CONFIG
# =========================================================
APP_BUILD = "Stage 22 · Snapshot Engine + Admin Control Room"
DEFAULT_SHEET_URL = "https://docs.google.com/spreadsheets/d/1hFoQMwnPugw8A6El69rkU0kNT4NDYJt4msOgnNv-f8Q/export?format=csv"

PUBLIC_ID_COOLDOWN_DAYS = 3
MIN_SESSION_Q = 10          # questions needed for "study session" style tasks
TOP_N = 10                  # rows shown on public leaderboards
SNAPSHOT_TTL = 120          # seconds a learner's data snapshot stays fresh
RESERVED_ID_WORDS = ("ADMIN", "ROOT", "SUPPORT", "SYSTEM", "OWNER", "STAFF", "OFFICIAL", "MODERATOR")

# Page identifiers (also the sidebar labels)
P_DASH = "🏠 Dashboard"
P_DAILY = "📅 Daily Quiz"
P_CUSTOM = "🎯 Custom Test"
P_REVIEW = "🧠 Review Mistakes"
P_MARKS = "🔖 Bookmarks"
P_LEAGUE = "🏆 League"
P_RANK = "📊 Rankings"
P_PROGRESS = "📈 My Progress"
P_ADMIN = "👑 Admin Dashboard"


def cfg(section, key, default=None):
    """Read st.secrets[section][key] safely."""
    try:
        sec = st.secrets.get(section)
        if sec is None:
            return default
        val = sec.get(key, default)
        return default if val in (None, "") else val
    except Exception:
        return default


SHEET_URL = str(cfg("app", "sheet_url", DEFAULT_SHEET_URL))
MAIN_ADMIN_EMAIL = str(cfg("app", "main_admin", "arjunrose2005@gmail.com")).strip().lower()
_admins_cfg = cfg("app", "admins", None)
ADMIN_EMAILS = {str(e).strip().lower() for e in (list(_admins_cfg) if _admins_cfg else [
    "jsujarose10675@gmail.com", "abisreerose2007@gmail.com", "aiswaryarose2009@gmail.com"])} - {MAIN_ADMIN_EMAIL}
ALL_ADMIN_EMAILS = {MAIN_ADMIN_EMAIL, *ADMIN_EMAILS}
SUPABASE_URL = str(cfg("supabase", "url", "")).strip()
SUPABASE_KEY = str(cfg("supabase", "secret_key", "")).strip()


def _load_tz():
    name = str(cfg("app", "timezone", "Asia/Kolkata"))
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception:
        return timezone(timedelta(hours=5, minutes=30)) if name == "Asia/Kolkata" else timezone.utc


APP_TZ = _load_tz()


def now_local():
    return datetime.now(APP_TZ)


def today_local():
    return now_local().date()


def iso_now():
    return now_local().isoformat(timespec="seconds")


def parse_ts(raw):
    """Parse any Supabase timestamp into a timezone-aware local datetime (or None)."""
    if raw in (None, ""):
        return None
    try:
        ts = pd.Timestamp(raw)
        if pd.isna(ts):
            return None
        dt = ts.to_pydatetime()
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(APP_TZ)
    except Exception:
        return None


def _stretch(fn):
    """`width="stretch"` on new Streamlit, `use_container_width=True` on older versions."""
    try:
        if "width" in inspect.signature(fn).parameters:
            return {"width": "stretch"}
    except (TypeError, ValueError):
        pass
    return {"use_container_width": True}


BTN_W = _stretch(st.button)
FORM_W = _stretch(st.form_submit_button)
PLOT_W = _stretch(st.plotly_chart)
DL_W = _stretch(st.download_button)
try:
    _DLG_KW = {"dismissible": False} if "dismissible" in inspect.signature(st.dialog).parameters else {}
except (TypeError, ValueError):
    _DLG_KW = {}

LEAGUE_LADDER = [
    ("Bronze III", 80), ("Bronze II", 120), ("Bronze I", 180),
    ("Silver III", 240), ("Silver II", 300), ("Silver I", 360),
    ("Gold III", 420), ("Gold II", 480), ("Gold I", 550),
    ("Platinum III", 620), ("Platinum II", 700), ("Platinum I", 800),
    ("Diamond III", 900), ("Diamond II", 1000), ("Diamond I", 1100),
    ("Master III", 1200), ("Master II", 1300), ("Master I", 1450),
    ("Champion III", 1600), ("Champion II", 1800), ("Champion I", None),
]
LEAGUE_NAMES = [n for n, _ in LEAGUE_LADDER]
LEAGUE_ICONS = {"Bronze": "🥉", "Silver": "🥈", "Gold": "🥇", "Platinum": "💠",
                "Diamond": "💎", "Master": "👑", "Champion": "🏆"}

GENERAL_TASKS = [
    {"id": "daily_quiz", "title": "Complete the latest Daily Quiz", "reward": 10, "go": P_DAILY},
    {"id": "perfect_quiz", "title": f"Score 100% in a {MIN_SESSION_Q}+ question quiz", "reward": 15, "go": P_CUSTOM},
    {"id": "review_mistakes", "title": "Finish a Review / Reinforcement quiz", "reward": 15, "go": P_REVIEW},
    {"id": "study_session", "title": f"Complete a {MIN_SESSION_Q}+ question study session", "reward": 10, "go": P_CUSTOM},
]
SPECIAL_TASK_ROTATION = [
    ("previous_day", "Complete a Daily Quiz from an earlier study day", 15, P_DAILY),
    ("custom_15", "Complete a custom quiz with 15+ questions", 15, P_CUSTOM),
    ("accuracy_80", f"Score 80%+ in a {MIN_SESSION_Q}+ question quiz", 15, P_CUSTOM),
    ("accuracy_90", f"Score 90%+ in a {MIN_SESSION_Q}+ question quiz", 15, P_CUSTOM),
    ("two_sessions", f"Complete two separate {MIN_SESSION_Q}+ question quizzes", 15, P_CUSTOM),
    ("perfect_two", f"Score 100% in two {MIN_SESSION_Q}+ question quizzes", 15, P_CUSTOM),
]


# =========================================================
# 2. SMALL HELPERS
# =========================================================
class Raw(str):
    """Marks a string as trusted HTML (skips escaping in html_table)."""


def esc(x, br=True):
    """Escape any text (sheet content, names, ids) before it goes into HTML."""
    s = html.escape("" if x is None else str(x), quote=True).replace("$", "&#36;")
    return s.replace("\r", "").replace("\n", "<br>") if br else s


def H(s):
    """Strip indentation/blank lines so Markdown never turns HTML into a code block."""
    return "\n".join(line.strip() for line in str(s).strip().splitlines() if line.strip())


def md(s):
    st.markdown(H(s), unsafe_allow_html=True)


def chunked(seq, n):
    seq = list(seq)
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def plural(n, word):
    return f"{n} {word}{'' if n == 1 else 's'}"


# =========================================================
# 3. DATABASE LAYER  (one cached client, paginated reads, bulk writes)
# =========================================================
@st.cache_resource(show_spinner=False)
def get_supabase(url, key):
    return create_client(url, key) if url and key else None


supabase = get_supabase(SUPABASE_URL, SUPABASE_KEY)


def err_text(exc):
    return str(getattr(exc, "message", None) or getattr(exc, "details", None) or exc)


def db_note(scope, exc):
    st.session_state.setdefault("_db_errors", {})[scope] = f"{now_local():%H:%M:%S} · {err_text(exc)}"


def rows_of(resp):
    return getattr(resp, "data", None) or []


def is_missing_table(exc):
    t = err_text(exc)
    return "PGRST205" in t or "Could not find the table" in t or ("relation" in t and "does not exist" in t)


@st.cache_resource(show_spinner=False)
def _stripped_columns():
    """Columns we learned a table does not have (remembered for the whole server process)."""
    return {}


_MISSING_COL_PATTERNS = [
    r"Could not find the '([^']+)' column",
    r'column "([^"]+)" of relation "[^"]+" does not exist',
    r"column \w+\.(\w+) does not exist",
]


def missing_columns(text):
    found = []
    for pat in _MISSING_COL_PATTERNS:
        found += re.findall(pat, str(text or ""), flags=re.I)
    return found


def insert_rows(table, rows):
    """Bulk insert. Unknown columns are dropped automatically (and remembered)."""
    if not rows:
        return []
    gone = _stripped_columns().setdefault(table, set())
    payload = [{k: v for k, v in r.items() if k not in gone} for r in rows]
    for _ in range(12):
        try:
            return rows_of(supabase.table(table).insert(payload).execute())
        except Exception as exc:
            new = [c for c in missing_columns(err_text(exc)) if payload and c in payload[0]]
            if not new:
                raise
            gone.update(new)
            payload = [{k: v for k, v in r.items() if k not in gone} for r in payload]
    raise RuntimeError(f"Could not insert into {table}")


def update_rows(table, values, strip=True, **eq):
    """UPDATE table SET values WHERE eq. strip=False raises on unknown columns (admin features)."""
    gone = _stripped_columns().setdefault(table, set()) if strip else set()
    payload = {k: v for k, v in values.items() if k not in gone}
    for _ in range(8):
        if not payload:
            return []
        try:
            q = supabase.table(table).update(payload)
            for k, v in eq.items():
                q = q.eq(k, v)
            return rows_of(q.execute())
        except Exception as exc:
            new = [c for c in missing_columns(err_text(exc)) if c in payload]
            if not new or not strip:
                raise
            gone.update(new)
            payload = {k: v for k, v in payload.items() if k not in gone}
    raise RuntimeError(f"Could not update {table}")


def fetch_all(table, columns="*", eq=None, gte=None, order=None, page=1000, cap=200000):
    """Read every matching row. PostgREST silently truncates at 1000 rows, so page it."""
    def _go(with_order):
        out, start = [], 0
        while start < cap:
            q = supabase.table(table).select(columns)
            for k, v in (eq or {}).items():
                q = q.eq(k, v)
            for k, v in (gte or {}).items():
                q = q.gte(k, v)
            if with_order and order:
                q = q.order(order[0], desc=order[1])
            data = rows_of(q.range(start, start + page - 1).execute())
            out.extend(data)
            if len(data) < page:
                break
            start += page
        return out
    try:
        return _go(True)
    except Exception:
        if not order:
            raise
        return _go(False)


def run_parallel(jobs, workers=6):
    """Run {name: callable} concurrently -> {name: (data, exception)}."""
    def _one(item):
        name, fn = item
        try:
            return name, fn(), None
        except Exception as exc:
            return name, None, exc
    with ThreadPoolExecutor(max_workers=workers) as ex:
        return {n: (d, e) for n, d, e in ex.map(_one, jobs.items())}


# =========================================================
# 4. THEMES + CSS
# =========================================================
THEMES = {
    "Sage": {"bg": "#F7FAF4", "surface": "#FFFFFF", "surface_2": "#EEF4E8", "text": "#26352B", "muted": "#66766A",
             "accent": "#789052", "accent_dark": "#526A37", "accent_soft": "#E3EAD9", "success": "#4E8A63",
             "danger": "#D97A68", "warning": "#C8954D", "border": "#DDE6D7", "shadow": "rgba(52,74,45,.10)"},
    "Lavender": {"bg": "#F8F6FC", "surface": "#FFFFFF", "surface_2": "#F0ECF8", "text": "#302B3D", "muted": "#6D677D",
                 "accent": "#8A78B8", "accent_dark": "#665594", "accent_soft": "#E7E0F3", "success": "#5C9874",
                 "danger": "#CE786D", "warning": "#C58A45", "border": "#E1DBED", "shadow": "rgba(66,52,92,.10)"},
    "Sky": {"bg": "#F3F9FC", "surface": "#FFFFFF", "surface_2": "#E8F2F7", "text": "#263740", "muted": "#64777F",
            "accent": "#4E91AE", "accent_dark": "#356F87", "accent_soft": "#DDEDF4", "success": "#4F936B",
            "danger": "#CE786D", "warning": "#BF8A48", "border": "#D7E6ED", "shadow": "rgba(44,85,103,.10)"},
}
THEME_LABELS = {"🌿 Sage": "Sage", "💜 Lavender": "Lavender", "🩵 Sky": "Sky"}
CSS_VARS = {"bg": "--bg", "surface": "--surface", "surface_2": "--surface2", "text": "--text", "muted": "--muted",
            "accent": "--accent", "accent_dark": "--accent-dark", "accent_soft": "--accent-soft",
            "success": "--success", "danger": "--danger", "warning": "--warning", "border": "--border",
            "shadow": "--shadow"}

BASE_CSS = r"""
@import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700;800&family=Playfair+Display:wght@600;700&display=swap');
.stApp{background:radial-gradient(circle at 92% 3%,var(--accent-soft),transparent 28%),var(--bg);color:var(--text);color-scheme:light}
html,body,.stApp p,.stApp label,.stApp li,.stApp input,.stApp textarea,.stApp button{font-family:'DM Sans',system-ui,sans-serif}
[data-testid="stHeader"]{background:transparent}
footer{visibility:hidden}
[data-testid="stSidebar"]{background:var(--surface2)!important;border-right:1px solid var(--border)}
[data-testid="stSidebar"] .stMarkdown,[data-testid="stSidebar"] label p{color:var(--text)}
h1,h2,h3,h4{color:var(--text)!important;letter-spacing:-.02em}
h1{font-family:'Playfair Display',serif!important;font-size:clamp(2rem,3.4vw,3.1rem)!important;line-height:1.1!important;margin-bottom:.4rem!important}
h2{font-size:1.5rem!important}h3{font-size:1.2rem!important}
p,label,.stMarkdown{color:var(--text)}
.stCaption,[data-testid="stCaptionContainer"]{color:var(--muted)!important}
.block-container{max-width:1440px;padding:2rem 2.4rem 4rem}

/* ---------- surfaces ---------- */
.hero{position:relative;overflow:hidden;background:linear-gradient(135deg,var(--surface),var(--surface2));border:1px solid var(--border);border-radius:26px;padding:2rem 2.3rem;box-shadow:0 16px 44px var(--shadow);margin-bottom:1.3rem}
.hero:after{content:"";position:absolute;width:240px;height:240px;right:-80px;top:-100px;border-radius:50%;background:var(--accent-soft);opacity:.7}
.hero h1{margin:.2rem 0 .5rem!important;position:relative;z-index:1}
.eyebrow{color:var(--accent-dark);font-weight:700;font-size:.82rem;margin-bottom:.35rem;position:relative;z-index:1}
.hero-sub{color:var(--muted);font-size:1.02rem;max-width:720px;line-height:1.6;position:relative;z-index:1}
.hero-badge{display:inline-block;margin-top:.9rem;padding:.4rem .75rem;border-radius:999px;background:var(--accent-soft);color:var(--accent-dark);font-size:.8rem;font-weight:700;position:relative;z-index:1}
.card{background:var(--surface);border:1px solid var(--border);border-radius:20px;padding:1.05rem 1.2rem;box-shadow:0 8px 26px var(--shadow);margin-bottom:.6rem}
.card.soft{background:var(--surface2)}
.metric-label{color:var(--muted);font-size:.75rem;font-weight:700}
.metric-value{font-size:1.75rem;font-weight:800;line-height:1.15;margin-top:.2rem;color:var(--text)}
.metric-foot{color:var(--muted);font-size:.8rem;margin-top:.2rem}
.section-head{display:flex;align-items:flex-end;justify-content:space-between;gap:1rem;margin:1.6rem 0 .8rem}
.section-head .title{font-weight:800;font-size:1.15rem}
.section-head .hint{color:var(--muted);font-size:.84rem}
.pill{display:inline-block;padding:.32rem .65rem;border-radius:999px;background:var(--accent-soft);color:var(--accent-dark);font-size:.74rem;font-weight:700;margin-right:.35rem}
.pill.good{background:rgba(78,138,99,.15);color:var(--success)}
.pill.bad{background:rgba(217,122,104,.16);color:var(--danger)}
.pill.warn{background:rgba(200,149,77,.18);color:#96682a}
.pill.off{background:var(--surface2);color:var(--muted);border:1px solid var(--border)}
.tip{border-left:4px solid var(--accent);padding:.75rem 1rem;border-radius:0 14px 14px 0;background:var(--surface2);color:var(--text);margin:.6rem 0}
.notice{border:1px solid var(--border);border-radius:16px;padding:.8rem 1rem;background:var(--accent-soft);color:var(--text);margin-bottom:.8rem}
.notice.warn{background:rgba(200,149,77,.14)}
.notice.bad{background:rgba(217,122,104,.13)}
.muted{color:var(--muted)}

/* ---------- buttons ---------- */
.stButton>button,[data-testid="stFormSubmitButton"]>button,[data-testid="stDownloadButton"]>button{border-radius:13px!important;border:1px solid var(--border)!important;background:var(--surface)!important;color:var(--text)!important;font-weight:700!important;min-height:42px;box-shadow:none!important;transition:border-color .15s,background .15s}
.stButton>button p,[data-testid="stFormSubmitButton"]>button p,[data-testid="stDownloadButton"]>button p{color:inherit!important}
.stButton>button:hover,[data-testid="stFormSubmitButton"]>button:hover,[data-testid="stDownloadButton"]>button:hover{border-color:var(--accent)!important;background:var(--accent-soft)!important;color:var(--accent-dark)!important}
.stButton>button:disabled{opacity:.45}
.stButton>button[kind="primary"],.stButton>button[data-testid="stBaseButton-primary"],[data-testid="stFormSubmitButton"]>button[kind="primaryFormSubmit"],[data-testid="stFormSubmitButton"]>button[data-testid="stBaseButton-primaryFormSubmit"]{background:var(--accent)!important;border-color:var(--accent)!important;color:#fff!important}
.stButton>button[kind="primary"]:hover,.stButton>button[data-testid="stBaseButton-primary"]:hover,[data-testid="stFormSubmitButton"]>button[kind="primaryFormSubmit"]:hover,[data-testid="stFormSubmitButton"]>button[data-testid="stBaseButton-primaryFormSubmit"]:hover{background:var(--accent-dark)!important;border-color:var(--accent-dark)!important;color:#fff!important}
.stButton>button[kind="primary"] p,.stButton>button[data-testid="stBaseButton-primary"] p,[data-testid="stFormSubmitButton"]>button[kind="primaryFormSubmit"] p{color:#fff!important}

/* ---------- inputs / dropdowns / dialogs (force the light look) ---------- */
[data-baseweb="select"],[data-baseweb="input"],[data-baseweb="textarea"],[data-testid="stDateInput"],[data-baseweb="popover"],[data-baseweb="calendar"],[role="dialog"]{color-scheme:light!important}
[data-baseweb="select"]>div,[data-baseweb="input"],[data-baseweb="input"]>div,[data-baseweb="textarea"],[data-baseweb="textarea"]>div,[data-testid="stNumberInput"] input{background:var(--surface)!important;color:var(--text)!important;border-color:var(--border)!important;border-radius:12px!important}
input,textarea,[data-baseweb="select"] input{color:var(--text)!important;-webkit-text-fill-color:var(--text)!important;caret-color:var(--accent-dark)!important;background:transparent!important}
input::placeholder,textarea::placeholder{color:var(--muted)!important;-webkit-text-fill-color:var(--muted)!important;opacity:1!important}
[data-baseweb="select"] svg,[data-testid="stDateInput"] svg{fill:var(--accent-dark)!important;color:var(--accent-dark)!important}
[data-baseweb="popover"],[data-baseweb="popover"]>div,[data-baseweb="menu"],[role="listbox"],[role="option"],[data-baseweb="calendar"],[data-baseweb="calendar"] *{background-color:var(--surface)!important;color:var(--text)!important}
[role="option"]:hover,[role="option"][aria-selected="true"],[data-baseweb="calendar"] button:hover{background-color:var(--accent-soft)!important;color:var(--accent-dark)!important}
[data-baseweb="calendar"] [aria-selected="true"]{background-color:var(--accent)!important;color:#fff!important}
div[role="dialog"]{background:var(--surface)!important;color:var(--text)!important;border-radius:22px!important}
[data-baseweb="tab-list"]{gap:.3rem}
button[data-baseweb="tab"]{border-radius:12px 12px 0 0;color:var(--muted)!important}
button[data-baseweb="tab"][aria-selected="true"]{color:var(--accent-dark)!important;font-weight:800}
[data-baseweb="tab-highlight"]{background-color:var(--accent)!important}
[data-testid="stExpander"]{border:1px solid var(--border)!important;border-radius:16px!important;background:var(--surface)}
div[data-testid="stAlert"]{border-radius:15px!important}
.stProgress>div>div>div>div{background:var(--accent)!important}
[data-testid="stForm"]{border:1px solid var(--border)!important;border-radius:18px!important;background:var(--surface)}

/* ---------- sidebar navigation ---------- */
[data-testid="stSidebar"] [data-testid="stRadio"] label{padding:.5rem .7rem;border-radius:12px;border:1px solid transparent;transition:background .15s}
[data-testid="stSidebar"] [data-testid="stRadio"] label:hover{background:var(--accent-soft)}
[data-testid="stSidebar"] [data-testid="stRadio"] label:has(input:checked){background:var(--accent-soft);border-color:var(--border);font-weight:800}
[data-testid="stSidebar"] h3{font-size:.8rem!important;color:var(--muted)!important;margin:1.1rem 0 .3rem!important}
.side-brand{font-family:'Playfair Display',serif;font-size:1.45rem;font-weight:700}
.side-mini{display:flex;align-items:center;gap:.65rem;padding:.4rem 0}
.side-name{font-weight:800;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.side-sub{color:var(--muted);font-size:.76rem}
.side-stats{display:grid;grid-template-columns:1fr 1fr;gap:.6rem;margin:.5rem 0}
.side-stat b{display:block;font-size:1.5rem;font-weight:800}
.side-stat span{color:var(--muted);font-size:.74rem}

/* ---------- quiz ---------- */
.quiz-shell{background:var(--surface);border:1px solid var(--border);border-radius:26px;padding:1.6rem 1.8rem;box-shadow:0 16px 48px var(--shadow)}
.question-text{font-size:clamp(1.25rem,2.2vw,1.85rem);font-weight:700;line-height:1.42;margin:.5rem 0 1.2rem}
.st-key-quizopts [data-testid="stRadio"] label{background:var(--surface2);border:1px solid var(--border);border-radius:15px;padding:.7rem .85rem;margin-bottom:.45rem;transition:border-color .15s}
.st-key-quizopts [data-testid="stRadio"] label:hover{border-color:var(--accent)}
.st-key-quizopts [data-testid="stRadio"] label:has(input:checked){border-color:var(--accent);background:var(--accent-soft)}
.opt{display:flex;gap:.6rem;align-items:center;padding:.72rem .95rem;border:1px solid var(--border);border-radius:14px;background:var(--surface2);margin:.45rem 0;font-weight:600}
.opt-correct{background:rgba(78,138,99,.14);border-color:var(--success);color:var(--success)}
.opt-wrong{background:rgba(217,122,104,.14);border-color:var(--danger);color:var(--danger)}
.explain{margin-top:1rem;padding:1rem 1.15rem;border-left:4px solid var(--accent);border-radius:0 16px 16px 0;background:var(--surface2)}
.palette{display:grid;grid-template-columns:repeat(auto-fill,minmax(32px,1fr));gap:.4rem}
.palette-item{display:grid;place-items:center;height:32px;border-radius:9px;font-size:.72rem;font-weight:700;border:1px solid var(--border);background:var(--surface2)}
.palette-current{border:2px solid var(--accent)}
.palette-good{background:rgba(78,138,99,.16);color:var(--success)}
.palette-bad{background:rgba(217,122,104,.17);color:var(--danger)}
.palette-skip{background:var(--accent-soft);color:var(--accent-dark)}
.review-item{border:1px solid var(--border);border-radius:16px;padding:.9rem 1.05rem;margin-bottom:.7rem;background:var(--surface)}
.review-item .q{font-weight:700;line-height:1.45;margin:.45rem 0}
.ans-line{margin-top:.4rem;padding:.5rem .7rem;background:var(--surface2);border-radius:10px;font-size:.85rem}

/* ---------- progress ---------- */
.stat-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:.8rem;margin:.4rem 0 1.1rem}
.stat{background:var(--surface);border:1px solid var(--border);border-radius:18px;padding:1rem 1.05rem;box-shadow:0 8px 22px var(--shadow)}
.stat .l{color:var(--muted);font-size:.76rem;font-weight:700}
.stat .v{font-size:1.7rem;font-weight:800;margin-top:.15rem}
.stat .s{color:var(--muted);font-size:.78rem;margin-top:.15rem}
.week-dots{display:flex;gap:.45rem;margin-top:.7rem}
.week-dot{width:2rem;height:2rem;border-radius:10px;display:grid;place-items:center;background:var(--surface2);border:1px solid var(--border);color:var(--muted);font-size:.72rem;font-weight:800}
.week-dot.done{background:var(--accent);border-color:var(--accent);color:#fff}
.week-dot.today{outline:2px solid var(--accent);outline-offset:2px}
.bar{height:8px;border-radius:999px;background:var(--border);overflow:hidden}
.bar>div{height:100%;border-radius:999px;background:linear-gradient(90deg,var(--accent),var(--accent-dark))}

/* ---------- league / tasks ---------- */
.league-hero{position:relative;overflow:hidden;border:1px solid var(--border);border-radius:26px;padding:1.6rem 1.8rem;margin:.4rem 0 1.1rem;background:linear-gradient(135deg,var(--surface),var(--surface2));box-shadow:0 16px 40px var(--shadow)}
.league-row{display:flex;gap:1.1rem;align-items:center}
.league-badge{width:96px;height:96px;border-radius:28px;display:grid;place-items:center;background:var(--surface);border:1px solid var(--border);box-shadow:0 10px 24px var(--shadow);flex:0 0 auto}
.league-emblem{width:72px;height:72px;border-radius:22px;display:grid;place-items:center;position:relative;border:3px solid currentColor;box-shadow:inset 0 0 0 5px rgba(255,255,255,.18)}
.league-emblem .tier{font-size:1.5rem;line-height:1;position:relative;z-index:1}
.league-emblem .division{font-size:.6rem;font-weight:900;position:absolute;bottom:6px;z-index:2;letter-spacing:.08em}
.league-emblem.bronze{color:#9b633f;background:rgba(155,99,63,.14)}.league-emblem.silver{color:#7c8793;background:rgba(124,135,147,.14)}
.league-emblem.gold{color:#b48716;background:rgba(180,135,22,.14)}.league-emblem.platinum{color:#438f91;background:rgba(67,143,145,.14)}
.league-emblem.diamond{color:#4f75c9;background:rgba(79,117,201,.14)}.league-emblem.master{color:#7656b5;background:rgba(118,86,181,.14)}
.league-emblem.champion{color:#b24b58;background:rgba(178,75,88,.14)}
.mini-emblem{display:inline-flex;align-items:center;justify-content:center;width:30px;height:30px;border-radius:9px;background:var(--surface);border:1px solid var(--border);font-size:.9rem;flex:0 0 auto}
.league-kicker{color:var(--muted);font-size:.78rem;font-weight:700}
.league-name{font-size:1.9rem;font-weight:900;letter-spacing:-.03em}
.league-meta{color:var(--muted);font-size:.82rem;margin-top:.35rem}
.league-qpline{display:flex;justify-content:space-between;margin:1rem 0 .4rem;font-weight:800}
.task{border:1px solid var(--border);border-radius:16px;padding:.85rem 1rem;background:var(--surface);box-shadow:0 6px 18px var(--shadow);margin-bottom:.6rem}
.task.special{background:linear-gradient(135deg,var(--surface),var(--accent-soft))}
.task.done{opacity:.72;background:rgba(78,138,99,.08);border-color:rgba(78,138,99,.3)}
.task-top{display:flex;justify-content:space-between;gap:1rem;align-items:flex-start}
.task-title{font-weight:800;line-height:1.35}
.task-sub{color:var(--muted);font-size:.78rem;margin-top:.2rem}
.task-reward{white-space:nowrap;font-weight:900;color:var(--accent-dark);background:var(--accent-soft);border-radius:999px;padding:.3rem .6rem;font-size:.78rem}
.task .bar{margin-top:.6rem;height:6px}
.group-label{display:flex;justify-content:space-between;align-items:center;margin:1.1rem 0 .5rem;font-weight:800;color:var(--muted);font-size:.85rem}
.ladder-row{display:flex;justify-content:space-between;align-items:center;padding:.42rem .65rem;border-radius:10px;font-weight:600}
.ladder-row.cur{background:var(--accent-soft);font-weight:800}
.badge-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:.7rem}
.badge{border:1px solid var(--border);border-radius:16px;padding:.8rem;background:var(--surface);text-align:center}
.badge.locked{opacity:.45;filter:grayscale(1)}
.badge .i{font-size:1.8rem}.badge .n{font-weight:800;font-size:.86rem;margin-top:.2rem}.badge .d{color:var(--muted);font-size:.72rem;margin-top:.15rem}
.promo{text-align:center;padding:1.8rem 1rem;border-radius:24px;border:1px solid var(--accent);background:linear-gradient(145deg,var(--accent-soft),var(--surface));box-shadow:0 20px 55px var(--shadow);margin:.4rem 0 1.1rem}
.promo .t{font-size:1.9rem;font-weight:900}
.promo .row{display:flex;justify-content:center;align-items:center;gap:1rem;margin:.7rem 0}

/* ---------- rankings ---------- */
.rank-me{display:flex;justify-content:space-between;align-items:center;gap:1rem;padding:1rem 1.15rem;margin:.4rem 0 1rem;border:1px solid var(--border);border-radius:18px;background:linear-gradient(135deg,var(--accent-soft),var(--surface))}
.rank-me .r{font-size:1.7rem;font-weight:900;line-height:1.1}
.rank-card{overflow:hidden;border:1px solid var(--border);border-radius:18px;background:var(--surface);box-shadow:0 8px 22px var(--shadow)}
.rank-row{display:grid;grid-template-columns:56px minmax(0,1fr) auto;align-items:center;gap:.8rem;padding:.8rem 1rem;border-bottom:1px solid var(--border)}
.rank-row:last-child{border-bottom:0}
.rank-row.me{background:var(--accent-soft)}
.rank-place{font-weight:900;text-align:center}
.rank-name{font-weight:800;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.rank-sub{display:flex;align-items:center;gap:.4rem;color:var(--muted);font-size:.76rem;margin-top:.15rem}
.rank-val{text-align:right;font-size:.9rem}
.rank-val small{display:block;color:var(--muted);font-size:.72rem}

/* ---------- tables + admin ---------- */
.tbl-wrap{overflow-x:auto;border:1px solid var(--border);border-radius:16px;background:var(--surface)}
.tbl{width:100%;border-collapse:separate;border-spacing:0;font-size:.85rem}
.tbl th{background:var(--accent-soft);color:var(--text);text-align:left;padding:.7rem .7rem;font-size:.76rem;border-bottom:1px solid var(--border);white-space:nowrap}
.tbl td{padding:.65rem .7rem;border-bottom:1px solid var(--border);vertical-align:middle;color:var(--text)}
.tbl tr:last-child td{border-bottom:0}.tbl tr:hover td{background:var(--surface2)}
.tbl .sub{color:var(--muted);font-size:.76rem}
.admin-hero{background:linear-gradient(135deg,var(--surface),var(--surface2));border:1px solid var(--border);border-radius:26px;padding:1.4rem 1.6rem;box-shadow:0 14px 40px var(--shadow);margin-bottom:1rem}
.admin-hero h1{margin:.1rem 0 .3rem!important}
.user-head{display:flex;gap:1rem;align-items:center;justify-content:space-between;flex-wrap:wrap}
.user-head .n{font-size:1.25rem;font-weight:900}
.st-key-admin_nav [data-testid="stRadio"] label{background:var(--surface);border:1px solid var(--border);border-radius:999px;padding:.35rem .9rem}
.st-key-admin_nav [data-testid="stRadio"] label:has(input:checked){background:var(--accent);border-color:var(--accent)}
.st-key-admin_nav [data-testid="stRadio"] label:has(input:checked) p{color:#fff!important;font-weight:800}
.st-key-admin_nav [data-testid="stRadio"] label>div:first-child{display:none}

/* ---------- calendar ---------- */
[class*="st-key-cal-"] [data-testid="stHorizontalBlock"]{flex-wrap:nowrap!important;gap:.3rem!important}
[class*="st-key-cal-"] [data-testid="stColumn"],[class*="st-key-cal-"] [data-testid="column"]{min-width:0!important;flex:1 1 0!important;width:auto!important}
[class*="st-key-cal-"] button{min-height:2.3rem!important;padding:.2rem 0!important;font-size:.82rem!important}
.cal-title{text-align:center;font-weight:800;padding:.5rem 0}
.cal-wd{text-align:center;color:var(--muted);font-size:.72rem;font-weight:800;padding:.2rem 0}
.cal-legend{color:var(--muted);font-size:.78rem;margin-top:.5rem}
/* day states are encoded in the button key: -perf- perfect, -done- completed, -sel- selected (+sel = selected too) */
[class*="st-key-cal-"][class*="-done-"] button,[class*="st-key-cal-"][class*="-donesel-"] button{background:var(--accent-soft)!important;border-color:var(--accent)!important;color:var(--accent-dark)!important}
[class*="st-key-cal-"][class*="-perf-"] button,[class*="st-key-cal-"][class*="-perfsel-"] button{background:var(--accent)!important;border-color:var(--accent)!important;color:#fff!important}
[class*="st-key-cal-"][class*="-perf-"] button p,[class*="st-key-cal-"][class*="-perfsel-"] button p{color:#fff!important;font-weight:900!important}
[class*="st-key-cal-"][class*="-perf-"] button:hover,[class*="st-key-cal-"][class*="-perfsel-"] button:hover{background:var(--accent-dark)!important;border-color:var(--accent-dark)!important;color:#fff!important}
[class*="st-key-cal-"][class*="-sel-"] button{background:var(--surface)!important;border:2px solid var(--accent-dark)!important;color:var(--accent-dark)!important}
[class*="st-key-cal-"][class*="-sel-"] button p{font-weight:900!important}
[class*="st-key-cal-"][class*="-donesel-"] button,[class*="st-key-cal-"][class*="-perfsel-"] button{box-shadow:0 0 0 2px var(--surface),0 0 0 4px var(--accent-dark)!important}
.cal-key{display:inline-block;width:.85rem;height:.85rem;border-radius:4px;vertical-align:-2px;margin-right:.25rem;border:1px solid var(--accent)}

@media (max-width:900px){
 .block-container{padding:1.2rem 1rem 3rem}.hero{padding:1.4rem;border-radius:20px}.quiz-shell{padding:1.1rem;border-radius:20px}
 .stat-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.league-badge{width:76px;height:76px}.league-emblem{width:56px;height:56px}
 .rank-row{grid-template-columns:44px minmax(0,1fr)}.rank-val{grid-column:2;text-align:left}
}
"""


def inject_theme(T):
    root = ":root{" + ";".join(f"{CSS_VARS[k]}:{T[k]}" for k in CSS_VARS) + "}"
    st.markdown(f"<style>{root}</style>\n<style>\n{BASE_CSS}\n</style>", unsafe_allow_html=True)


# =========================================================
# 5. QUESTION BANK  (Google Sheet -> validated records)
# =========================================================
def resolve_correct(options, raw):
    """Map the sheet's Correct_Option (text, 1-4, A-D, 'Option_2') to one of the option strings."""
    raw = str(raw or "").strip()
    norm = lambda s: re.sub(r"\s+", " ", str(s)).strip().casefold()
    for o in options:
        if norm(o) == norm(raw):
            return o
    m = re.fullmatch(r"(?:option[\s_-]*)?([1-4]|[a-d])[\).:]?", raw, flags=re.I)
    if m:
        t = m.group(1).lower()
        idx = int(t) - 1 if t.isdigit() else ord(t) - 97
        if 0 <= idx < len(options):
            return options[idx]
    return None


@st.cache_resource(ttl=300, show_spinner=False)
def load_question_bank(url):
    resp = requests.get(url, timeout=20)
    resp.raise_for_status()
    raw = pd.read_csv(io.StringIO(resp.content.decode("utf-8-sig", errors="replace")), dtype=str, keep_default_na=False)
    raw.columns = [c.strip() for c in raw.columns]
    need = ["Date", "Question", "Option_1", "Option_2", "Option_3", "Option_4", "Correct_Option", "Explanation"]
    missing = [c for c in need if c not in raw.columns]
    if missing:
        raise ValueError(f"Missing columns in Google Sheet: {', '.join(missing)}")

    dates = raw["Date"].str.strip()
    try:
        parsed = pd.to_datetime(dates, errors="coerce", format="mixed", dayfirst=True)
        if parsed.notna().sum() == 0:
            parsed = pd.to_datetime(dates, errors="coerce", format="mixed", dayfirst=False)
    except (TypeError, ValueError):
        parsed = pd.to_datetime(dates, errors="coerce", dayfirst=True)

    records, issues, seen = [], [], set()
    for i in range(len(raw)):
        row, line, d = raw.iloc[i], i + 2, parsed.iloc[i]
        q_raw = str(row["Question"])
        if pd.isna(d) or not q_raw.strip():
            if any(str(v).strip() for v in row.values):
                issues.append(f"Row {line}: missing or unreadable date/question")
            continue
        opts = [str(row[f"Option_{n}"]).strip() for n in range(1, 5)]
        opts = [o for o in opts if o]
        if len(opts) < 2:
            issues.append(f"Row {line}: fewer than 2 options - skipped")
            continue
        correct = resolve_correct(opts, row["Correct_Option"])
        if correct is None:
            issues.append(f"Row {line}: Correct_Option '{row['Correct_Option']}' does not match any option - skipped")
            continue
        if len({re.sub(r'\s+', ' ', o).casefold() for o in opts}) != len(opts):
            issues.append(f"Row {line}: duplicate option text")
        qdate = d.date()
        key = f"{qdate.isoformat()}|{q_raw}"       # identical to the legacy key -> old mistakes keep working
        if key in seen:
            issues.append(f"Row {line}: duplicate question on {qdate:%d %b %Y} - skipped")
            continue
        seen.add(key)
        records.append({"key": key, "date": qdate, "question": q_raw.strip(), "options": opts,
                        "correct": correct, "explanation": str(row["Explanation"]).strip()})
    return {"records": records, "issues": issues, "loaded_at": iso_now()}


@st.cache_resource(show_spinner=False)
def _bank_store():
    return {}


def get_bank():
    """Fresh bank when possible; last good copy when the sheet is temporarily unreachable."""
    try:
        bank = load_question_bank(SHEET_URL)
        _bank_store()["bank"] = bank
        return bank, None
    except Exception as exc:
        last = _bank_store().get("bank")
        if last:
            return last, err_text(exc)
        raise


def lookup_question(key):
    q = Q_BY_KEY.get(key)
    if q:
        return q
    d, _, text = str(key).partition("|")
    return Q_BY_STRIPPED.get(f"{d}|{text.strip()}")


# =========================================================
# 6. AUTH
# =========================================================
def auth_is_configured():
    try:
        auth = st.secrets.get("auth")
        if auth is None:
            return False
        return all(str(auth.get(k, "")).strip() for k in ("client_id", "client_secret", "server_metadata_url"))
    except Exception:
        return False


def get_auth_user():
    """Real Google identity only - there is deliberately no demo fallback."""
    if not auth_is_configured():
        return None
    try:
        data = st.user.to_dict()
    except Exception:
        return None
    if not bool(data.get("is_logged_in", False)):
        return None
    email = str(data.get("email", "") or "").strip().lower()
    if not email:
        return None
    return {"id": str(data.get("sub", "") or email), "email": email,
            "name": str(data.get("name", "") or email.split("@")[0]).strip(),
            "picture": str(data.get("picture", "") or "")}


def user_role(email):
    email = str(email or "").strip().lower()
    return "main_admin" if email == MAIN_ADMIN_EMAIL else ("admin" if email in ADMIN_EMAILS else "user")


def is_admin(email):
    return str(email or "").strip().lower() in ALL_ADMIN_EMAILS


# =========================================================
# 7. PROFILE + PUBLIC USER ID
# =========================================================
PUBLIC_ID_RE = re.compile(r"[A-Z][A-Z0-9_-]{3,19}")


def fetch_profile(email):
    try:
        r = rows_of(supabase.table("profiles").select("*").eq("email", email).limit(1).execute())
        return r[0] if r else None
    except Exception as exc:
        db_note("Profile", exc)
        return None


def sync_profile(user):
    """Create/refresh the profile once per login session (created_at is never overwritten)."""
    email, now = user["email"], iso_now()
    prof = fetch_profile(email)
    try:
        if prof:
            upd = {"name": user["name"], "role": user_role(email), "last_seen": now, "google_id": user["id"]}
            update_rows("profiles", upd, id=prof["id"])
            prof.update(upd)
        else:
            new = {"email": email, "google_id": user["id"], "name": user["name"], "role": user_role(email),
                   "created_at": now, "last_seen": now}
            ins = insert_rows("profiles", [new])
            prof = ins[0] if ins else fetch_profile(email)
    except Exception as exc:
        db_note("Profile", exc)
    return prof


def ping_last_seen():
    if time.time() - st.session_state.get("_ping_at", 0) < 600:
        return
    st.session_state["_ping_at"] = time.time()
    try:
        update_rows("profiles", {"last_seen": iso_now()}, id=st.session_state["_uid"])
    except Exception as exc:
        db_note("Profile", exc)


def validate_public_id(value, privileged=False):
    v = str(value or "").strip().upper()
    if not PUBLIC_ID_RE.fullmatch(v):
        return None, "Use 4–20 characters: letters, numbers, _ or -, starting with a letter."
    if not privileged and any(w in v for w in RESERVED_ID_WORDS):
        return None, "That User ID contains a reserved word. Please choose another one."
    return v, ""


def cooldown_remaining(profile):
    changed = parse_ts(profile.get("public_user_id_changed_at"))
    if not changed:
        return timedelta(0)
    return max(timedelta(days=PUBLIC_ID_COOLDOWN_DAYS) - (now_local() - changed), timedelta(0))


def cooldown_text(rem):
    hours = max(1, int(rem.total_seconds() // 3600))
    days, h = divmod(hours, 24)
    if days:
        return f"You can change your User ID again in {plural(days, 'day')} and {plural(h, 'hour')}."
    return f"You can change your User ID again in {plural(h, 'hour')}."


def set_public_user_id(profile_id, value, bypass_cooldown=False):
    """Returns (ok, value_or_message). Learners get a cooldown, admins do not."""
    v, msg = validate_public_id(value, privileged=bypass_cooldown)
    if v is None:
        return False, msg
    if not supabase:
        return False, "Supabase is not configured."
    try:
        rows = rows_of(supabase.table("profiles").select("*").eq("id", profile_id).limit(1).execute())
        fresh = rows[0] if rows else {}
        existing = str(fresh.get("public_user_id") or "").strip().upper()
        if existing == v:
            return True, v
        if existing and not bypass_cooldown:
            rem = cooldown_remaining(fresh)
            if rem > timedelta(0):
                return False, cooldown_text(rem)
        clash = rows_of(supabase.table("profiles").select("id").eq("public_user_id", v).limit(1).execute())
        if clash and str(clash[0]["id"]) != str(profile_id):
            return False, "That User ID is already taken. Please choose another one."
        payload = {"public_user_id": v}
        if existing and not bypass_cooldown:
            payload["public_user_id_changed_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        update_rows("profiles", payload, strip=False, id=profile_id)
    except Exception as exc:
        t = err_text(exc)
        if "duplicate" in t.lower() or "23505" in t:
            return False, "That User ID is already taken. Please choose another one."
        return False, t
    if st.session_state.get("_snap"):
        st.session_state["_snap"]["at"] = 0
    ranking_data.clear()
    return True, v


@st.dialog("🔐 Create your User ID", width="small", **_DLG_KW)
def dialog_create_user_id():
    md("""<div style='text-align:center'><div style='font-size:2.1rem'>👤</div>
    <h3 style='margin:.2rem 0'>Pick your public User ID</h3>
    <p class='muted'>This is the only identity other learners see on Rankings. Your Google name and email stay private.</p></div>""")
    with st.form("create_uid_form", clear_on_submit=False):
        value = st.text_input("User ID", max_chars=20, placeholder="Example: ARJUN_25")
        st.caption("4–20 characters · starts with a letter · A–Z, 0–9, _ or -")
        go_ = st.form_submit_button("Create User ID and continue", type="primary", **FORM_W)
    if go_:
        ok, res = set_public_user_id(st.session_state["_uid"], value, bypass_cooldown=True)
        if ok:
            st.rerun()
        else:
            st.error(res)


@st.dialog("✏️ Edit your User ID", width="small")
def dialog_edit_user_id():
    snap = snapshot()
    prof = snap["profile"]
    admin = is_admin(st.session_state["auth_user"]["email"])
    current = str(prof.get("public_user_id") or "")
    rem = timedelta(0) if admin else cooldown_remaining(prof)
    if admin:
        md("<div class='notice'>👑 Admin accounts can change their User ID at any time.</div>")
    else:
        md(f"<div class='notice'>🔒 After a change you can edit your User ID again only after <b>{PUBLIC_ID_COOLDOWN_DAYS * 24} hours</b>.</div>")
    if rem > timedelta(0):
        st.info(cooldown_text(rem))
        return
    with st.form("edit_uid_form"):
        value = st.text_input("User ID", value=current, max_chars=20)
        st.caption("4–20 characters · starts with a letter · A–Z, 0–9, _ or -")
        go_ = st.form_submit_button("Save User ID", type="primary", **FORM_W)
    if go_:
        ok, res = set_public_user_id(st.session_state["_uid"], value, bypass_cooldown=admin)
        if ok:
            st.toast(f"User ID is now {res}", icon="✅")
            st.rerun()
        else:
            st.error(res)


# =========================================================
# 8. LEARNER SNAPSHOT  (all of a learner's data, loaded once, updated in place)
# =========================================================
def proc_attempt(r):
    dt = parse_ts(r.get("completed_at") or r.get("started_at"))
    c, i, s = int(r.get("correct") or 0), int(r.get("incorrect") or 0), int(r.get("skipped") or 0)
    total = int(r.get("total_questions") or 0) or (c + i + s)
    return {"id": r.get("id"), "dt": dt, "day": dt.date() if dt else None,
            "type": str(r.get("quiz_type") or "Quiz"), "total": total, "correct": c, "incorrect": i, "skipped": s}


def _load_snapshot(uid, prev=None):
    prev = prev or {}
    for k in ("Profile", "Attempts", "Mistakes", "League", "Completions"):
        st.session_state.get("_db_errors", {}).pop(k, None)
    since = (today_local() - timedelta(days=120)).isoformat()
    res = run_parallel({
        "profile": lambda: rows_of(supabase.table("profiles").select("*").eq("id", uid).limit(1).execute()),
        "attempts": lambda: fetch_all("quiz_attempts",
                                      "id,quiz_type,started_at,completed_at,total_questions,correct,incorrect,skipped,accuracy",
                                      eq={"user_id": uid}, order=("completed_at", False)),
        "mistakes": lambda: fetch_all("mistakes", "id,question_id,mistake_count,last_answer", eq={"user_id": uid}, order=("id", False)),
        "league": lambda: rows_of(supabase.table("league_progress").select("qp,lifetime_qp,league_index").eq("user_id", uid).limit(1).execute()),
        "completions": lambda: fetch_all("task_completions", "task_key,task_date,qp_awarded,completed_at",
                                         eq={"user_id": uid}, gte={"task_date": since}, order=("completed_at", True)),
        "bookmarks": lambda: fetch_all("bookmarks", "question_id", eq={"user_id": uid}),
        "daily": lambda: fetch_all("daily_quiz_results", "quiz_date,total_questions,best_correct,attempts,completed,perfect",
                                   eq={"user_id": uid}),
    })

    def got(name):
        data, exc = res[name]
        if exc is not None:
            if not (name in ("bookmarks", "daily") and is_missing_table(exc)):
                db_note(name.title(), exc)
            return None
        return data

    prof_rows = got("profile")
    profile = (prof_rows[0] if prof_rows else None) or prev.get("profile") or {}
    att_rows = got("attempts")
    attempts = [proc_attempt(r) for r in att_rows] if att_rows is not None else prev.get("attempts", [])
    mist_rows = got("mistakes")
    if mist_rows is not None:
        mistakes, orphans = {}, []
        for r in mist_rows:
            q = lookup_question(r.get("question_id"))
            if q:
                mistakes[q["key"]] = r
            else:
                orphans.append(r)
    else:
        mistakes, orphans = prev.get("mistakes", {}), prev.get("orphans", [])
    lg_rows = got("league")
    if lg_rows is not None:
        r = lg_rows[0] if lg_rows else {}
        league = {"qp": int(r.get("qp") or 0), "lifetime_qp": int(r.get("lifetime_qp") or 0),
                  "league_index": min(max(int(r.get("league_index") or 0), 0), len(LEAGUE_LADDER) - 1)}
    else:
        league = prev.get("league", {"qp": 0, "lifetime_qp": 0, "league_index": 0})
    comp = got("completions")
    completions = comp if comp is not None else prev.get("completions", [])
    bm_data, bm_exc = res["bookmarks"]
    bookmarks_ok = bm_exc is None or not is_missing_table(bm_exc)
    bookmarks = {r["question_id"] for r in bm_data} if bm_data is not None else prev.get("bookmarks", set())
    dq_data, dq_exc = res["daily"]
    daily_ok = dq_exc is None or not is_missing_table(dq_exc)
    if dq_data is not None:
        daily = {}
        for r in dq_data:
            try:
                daily[date.fromisoformat(str(r["quiz_date"])[:10])] = r
            except Exception:
                pass
    else:
        daily = prev.get("daily", {})
    return {"uid": uid, "at": time.time(), "profile": profile, "daily": daily, "daily_ok": daily_ok, "attempts": attempts, "mistakes": mistakes,
            "orphans": orphans, "league": league, "completions": completions, "bookmarks": bookmarks,
            "bookmarks_ok": bookmarks_ok, "_stats": None}


def snapshot(force=False):
    uid = st.session_state.get("_uid")
    s = st.session_state.get("_snap")
    if not force and s and s.get("uid") == uid and time.time() - s["at"] < SNAPSHOT_TTL:
        return s
    new = _load_snapshot(uid, s if s and s.get("uid") == uid else None)
    st.session_state["_snap"] = new
    return new


def streaks(days):
    if not days:
        return 0, 0
    ordered = sorted(days)
    best = run = 1
    for a, b in zip(ordered, ordered[1:]):
        run = run + 1 if b == a + timedelta(days=1) else 1
        best = max(best, run)
    today = today_local()
    d = today if today in days else (today - timedelta(days=1) if (today - timedelta(days=1)) in days else None)
    cur = 0
    while d is not None and d in days:
        cur += 1
        d -= timedelta(days=1)
    return cur, best


def stats_of(snap):
    if snap.get("_stats"):
        return snap["_stats"]
    atts = snap["attempts"]
    days = {a["day"] for a in atts if a["day"]}
    cur, best = streaks(days)
    override = None
    try:
        ov = snap["profile"].get("streak_override")
        override = None if ov in (None, "") else max(0, int(ov))
    except Exception:
        override = None
    if override is not None:
        cur, best = override, max(best, override)
    correct = sum(a["correct"] for a in atts)
    attended = sum(a["correct"] + a["incorrect"] for a in atts)
    out = {"quizzes": len(atts), "correct": correct, "attended": attended,
           "accuracy": correct / attended * 100 if attended else 0.0,
           "current_streak": cur, "best_streak": best, "days": days, "override": override,
           "perfect": any(a["total"] >= MIN_SESSION_Q and a["correct"] == a["total"] for a in atts)}
    snap["_stats"] = out
    return out


def league_of(snap):
    lg = snap["league"]
    idx = min(max(int(lg.get("league_index", 0)), 0), len(LEAGUE_LADDER) - 1)
    name, threshold = LEAGUE_LADDER[idx]
    qp = int(lg.get("qp", 0))
    return {"idx": idx, "name": name, "threshold": threshold, "qp": qp, "lifetime": int(lg.get("lifetime_qp", 0)),
            "pct": 1.0 if threshold is None else min(max(qp / threshold, 0), 1),
            "remaining": 0 if threshold is None else max(threshold - qp, 0),
            "next": LEAGUE_LADDER[idx + 1][0] if idx < len(LEAGUE_LADDER) - 1 else None}


def mistake_bank(snap):
    out = []
    for key, row in snap["mistakes"].items():
        q = Q_BY_KEY.get(key)
        if q:
            out.append({**q, "times_missed": int(row.get("mistake_count") or 1),
                        "last_answer": row.get("last_answer"), "row_id": row.get("id")})
    return out


def completed_keys(snap, day):
    iso = day.isoformat()
    return {c["task_key"] for c in snap["completions"] if str(c.get("task_date"))[:10] == iso}


# =========================================================
# 9. QP / LEAGUE / MISSIONS ENGINE
# =========================================================
def apply_qp(p, delta):
    """Add (or remove) QP with carry-over promotion. Never demotes."""
    qp = max(0, int(p["qp"]) + int(delta))
    life = max(0, int(p["lifetime_qp"]) + int(delta))
    idx = start = int(p["league_index"])
    while delta > 0 and idx < len(LEAGUE_LADDER) - 1:
        th = LEAGUE_LADDER[idx][1]
        if th is None or qp < th:
            break
        qp -= th
        idx += 1
    return {"qp": qp, "lifetime_qp": life, "league_index": idx}, start


def save_league(uid, p):
    payload = {"qp": int(p["qp"]), "lifetime_qp": int(p["lifetime_qp"]),
               "league_index": int(p["league_index"]), "updated_at": iso_now()}
    if not update_rows("league_progress", payload, user_id=uid):
        insert_rows("league_progress", [{"user_id": uid, **payload}])


def award_qp(items):
    """items: [(task_key, reward, title)] earned today. Writes completions first, then QP. Idempotent per day."""
    snap, uid, today = snapshot(), st.session_state["_uid"], today_local()
    done = completed_keys(snap, today)
    fresh = [(k, r, t) for k, r, t in items if k not in done]
    if not fresh:
        return None
    now = iso_now()
    rows = [{"user_id": uid, "task_key": k, "task_date": today.isoformat(), "qp_awarded": int(r),
             "metadata": {"title": t}, "completed_at": now} for k, r, t in fresh]
    insert_rows("task_completions", rows)
    total = sum(r for _, r, _ in fresh)
    new, start = apply_qp(snap["league"], total)
    try:
        save_league(uid, new)
    except Exception:
        time.sleep(0.4)
        save_league(uid, new)
    snap["league"] = new
    snap["completions"] = [{"task_key": k, "task_date": today.isoformat(), "qp_awarded": r, "completed_at": now}
                           for k, r, _ in fresh] + snap["completions"]
    ranking_data.clear()
    return {"total": total, "items": [(t, r) for _, r, t in fresh], "promoted": new["league_index"] > start,
            "from": LEAGUE_LADDER[start][0], "to": LEAGUE_LADDER[new["league_index"]][0]}


def special_tasks(day):
    n = day.toordinal() * 2
    out = []
    for off in (0, 1):
        code, title, reward, go = SPECIAL_TASK_ROTATION[(n + off) % len(SPECIAL_TASK_ROTATION)]
        out.append({"id": f"special_{code}", "title": title, "reward": reward, "go": go, "special": True})
    return out


def tasks_for(day):
    return GENERAL_TASKS + special_tasks(day)


def _sess(a):
    return {"mode": a["type"], "total": a["total"], "correct": a["correct"], "attempted": a["total"] - a["skipped"]}


def _valid(s):
    return s["total"] > 0 and s["attempted"] * 2 >= s.get("planned", s["total"])


def _big(s):
    return _valid(s) and s["total"] >= MIN_SESSION_Q


def _perfect(s):
    return _big(s) and s["correct"] == s["total"]


def newly_met_tasks(cur, priors):
    """Which task ids does this finished session satisfy? `cur` may carry `dates` and `planned`."""
    met, sessions = set(), priors + [cur]
    if _valid(cur) and cur["mode"] == "Daily" and cur.get("dates"):
        if cur["dates"] == {LATEST_DATE}:
            met.add("daily_quiz")
        elif LATEST_DATE not in cur["dates"]:
            met.add("special_previous_day")
    if _big(cur):
        met.add("study_session")
    if _perfect(cur):
        met.add("perfect_quiz")
    if _valid(cur) and cur["mode"] == "Review":
        met.add("review_mistakes")
    if _valid(cur) and cur["mode"] in {"Custom Number", "All"} and cur["total"] >= 15:
        met.add("special_custom_15")
    if _big(cur):
        ratio = cur["correct"] / cur["total"]
        if ratio >= 0.8:
            met.add("special_accuracy_80")
        if ratio >= 0.9:
            met.add("special_accuracy_90")
    if sum(_big(s) for s in sessions) >= 2:
        met.add("special_two_sessions")
    if sum(_perfect(s) for s in sessions) >= 2:
        met.add("special_perfect_two")
    return met


def task_progress(task, snap, day):
    """(percent, label) for a mission card."""
    if task["id"] in completed_keys(snap, day):
        return 100, "Completed"
    todays = [_sess(a) for a in snap["attempts"] if a["day"] == day]
    if task["id"] == "special_two_sessions":
        n = min(sum(_big(s) for s in todays), 2)
        return n * 50, f"{n}/2 quizzes"
    if task["id"] == "special_perfect_two":
        n = min(sum(_perfect(s) for s in todays), 2)
        return n * 50, f"{n}/2 perfect quizzes"
    if task["id"] in {"special_accuracy_80", "special_accuracy_90"}:
        target = 0.8 if task["id"].endswith("80") else 0.9
        best = max((s["correct"] / s["total"] for s in todays if _big(s)), default=0.0)
        return int(min(best / target, 1) * 100), f"Best today {best * 100:.0f}%"
    return 0, "Not started"


BADGE_DEFS = [
    ("🌱", "First steps", "Complete your first quiz", lambda s, l: s["quizzes"] >= 1),
    ("🔥", "On fire", "Reach a 3-day streak", lambda s, l: s["best_streak"] >= 3),
    ("⚡", "Weekly warrior", "Reach a 7-day streak", lambda s, l: s["best_streak"] >= 7),
    ("🌟", "Unstoppable", "Reach a 30-day streak", lambda s, l: s["best_streak"] >= 30),
    ("📚", "Centurion", "Answer 100 questions", lambda s, l: s["attended"] >= 100),
    ("🎓", "Scholar", "Answer 500 questions", lambda s, l: s["attended"] >= 500),
    ("💯", "Flawless", f"Perfect score in a {MIN_SESSION_Q}+ question quiz", lambda s, l: s["perfect"]),
    ("🎯", "Sharpshooter", "90%+ accuracy over 50+ answers", lambda s, l: s["attended"] >= 50 and s["accuracy"] >= 90),
    ("🥈", "Silver league", "Reach Silver", lambda s, l: l["idx"] >= 3),
    ("🥇", "Gold league", "Reach Gold", lambda s, l: l["idx"] >= 6),
    ("💎", "Diamond league", "Reach Diamond", lambda s, l: l["idx"] >= 12),
    ("🏆", "Champion", "Reach Champion", lambda s, l: l["idx"] >= 18),
]


def badges_of(snap):
    s, l = stats_of(snap), league_of(snap)
    return [(i, n, d, bool(fn(s, l))) for i, n, d, fn in BADGE_DEFS]


# =========================================================
# 10. SHARED CACHED DATA  (rankings, announcements, audit)
# =========================================================
def week_start_iso():
    d = today_local()
    monday = d - timedelta(days=d.weekday())
    return datetime(monday.year, monday.month, monday.day, tzinfo=APP_TZ).isoformat()


@st.cache_data(ttl=45, show_spinner=False)
def ranking_data(week_start):
    """Public leaderboard rows. Contains ONLY public User IDs - never names or emails."""
    def _profiles():
        try:
            return fetch_all("profiles", "id,role,public_user_id,disabled", order=("id", False))
        except Exception:
            return fetch_all("profiles", "id,role,public_user_id", order=("id", False))
    res = run_parallel({
        "profiles": _profiles,
        "league": lambda: fetch_all("league_progress", "user_id,qp,lifetime_qp,league_index", order=("user_id", False)),
        "weekly": lambda: fetch_all("task_completions", "user_id,qp_awarded", gte={"completed_at": week_start}, order=("id", False)),
    })
    if res["profiles"][1] is not None:
        return {"error": err_text(res["profiles"][1]), "league": [], "weekly": [], "lifetime": []}
    lg = {str(r["user_id"]): r for r in (res["league"][0] or [])}
    wk = Counter()
    for r in (res["weekly"][0] or []):
        wk[str(r["user_id"])] += int(r.get("qp_awarded") or 0)
    rows = []
    for p in res["profiles"][0] or []:
        if str(p.get("role", "user")).lower() in {"admin", "main_admin"} or p.get("disabled") or not p.get("public_user_id"):
            continue
        uid = str(p["id"])
        r = lg.get(uid, {})
        idx = min(max(int(r.get("league_index") or 0), 0), len(LEAGUE_LADDER) - 1)
        rows.append({"uid": uid, "public_id": str(p["public_user_id"]), "league_index": idx, "league": LEAGUE_LADDER[idx][0],
                     "qp": int(r.get("qp") or 0), "lifetime": int(r.get("lifetime_qp") or 0), "weekly": wk.get(uid, 0)})

    def board(key, keep=lambda r: True):
        out = [dict(r) for r in sorted(rows, key=key) if keep(r)]
        for i, r in enumerate(out, 1):
            r["rank"] = i
        return out
    return {"error": None,
            "league": board(lambda r: (-r["league_index"], -r["qp"], -r["lifetime"], r["public_id"])),
            "weekly": board(lambda r: (-r["weekly"], -r["lifetime"], r["public_id"]), keep=lambda r: r["weekly"] > 0),
            "lifetime": board(lambda r: (-r["lifetime"], r["public_id"]))}


@st.cache_data(ttl=60, show_spinner=False)
def active_announcements():
    try:
        return rows_of(supabase.table("announcements").select("id,message,level,created_at")
                       .eq("active", True).order("created_at", desc=True).limit(3).execute())
    except Exception:
        return []


def audit(action, target="", details=""):
    """Best-effort admin audit trail (needs the admin_audit table)."""
    try:
        insert_rows("admin_audit", [{"admin_email": st.session_state["auth_user"]["email"], "action": action,
                                     "target_email": target, "details": str(details)[:500], "created_at": iso_now()}])
    except Exception as exc:
        if not is_missing_table(exc):
            db_note("Audit", exc)


# =========================================================
# 11. UI HELPERS
# =========================================================
def theme():
    return THEMES[st.session_state.get("theme", "Sage")]


def rerun_fragment():
    """Rerun only the current fragment; fall back to a full rerun when not inside a fragment rerun."""
    try:
        st.rerun(scope="fragment")
    except st.errors.StreamlitAPIException:
        st.rerun()


def goto(page):
    """Navigation that works from on_click callbacks (no manual reruns needed)."""
    st.session_state["nav_page"] = page
    q = st.session_state.get("quiz")
    if q and q.get("done"):
        st.session_state.pop("quiz", None)


def emblem_html(name, mini=False):
    parts = str(name).split()
    tier, division = (parts[0] if parts else "Champion"), (parts[1] if len(parts) > 1 else "I")
    icon = LEAGUE_ICONS.get(tier, "🏆")
    if mini:
        return f"<span class='mini-emblem'>{icon}</span>"
    return (f"<div class='league-emblem {esc(tier.lower())}'><div class='tier'>{icon}</div>"
            f"<div class='division'>{esc(division)}</div></div>")


def metric(label, value, foot=""):
    md(f"<div class='card'><div class='metric-label'>{esc(label)}</div><div class='metric-value'>{esc(value)}</div>"
       f"<div class='metric-foot'>{esc(foot)}</div></div>")


def section(title, hint=""):
    md(f"<div class='section-head'><div class='title'>{esc(title)}</div><div class='hint'>{esc(hint)}</div></div>")


def pill(text, kind=""):
    return Raw(f"<span class='pill {kind}'>{esc(text)}</span>")


def hero(eyebrow, title, sub, badge=""):
    b = f"<div class='hero-badge'>{esc(badge)}</div>" if badge else ""
    md(f"<div class='hero'><div class='eyebrow'>{esc(eyebrow)}</div><h1>{esc(title)}</h1>"
       f"<div class='hero-sub'>{esc(sub)}</div>{b}</div>")


def html_table(headers, rows, min_width=0):
    head = "".join(f"<th>{esc(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{c if isinstance(c, Raw) else esc(c)}</td>" for c in r) + "</tr>" for r in rows)
    return (f"<div class='tbl-wrap'><table class='tbl' style='min-width:{min_width}px'><thead><tr>{head}</tr></thead>"
            f"<tbody>{body}</tbody></table></div>")


def fig_style(fig, height=280, legend=False):
    T = theme()
    fig.update_layout(height=height, margin=dict(l=8, r=8, t=10, b=8), paper_bgcolor="rgba(0,0,0,0)",
                      plot_bgcolor="rgba(0,0,0,0)", template="none", showlegend=legend,
                      font=dict(color=T["text"], family="DM Sans"),
                      legend=dict(orientation="h", x=0.5, xanchor="center", y=-0.12))
    fig.update_xaxes(showgrid=False, linecolor=T["border"], tickfont=dict(color=T["muted"], size=11))
    fig.update_yaxes(gridcolor=T["border"], zeroline=False, tickfont=dict(color=T["muted"], size=11))
    return fig


def plot(fig, key):
    st.plotly_chart(fig, key=key, config={"displayModeBar": False}, **PLOT_W)


def stat_grid(items):
    cells = "".join(f"<div class='stat'><div class='l'>{esc(l)}</div><div class='v'>{esc(v)}</div><div class='s'>{esc(s)}</div></div>"
                    for l, v, s in items)
    md(f"<div class='stat-grid'>{cells}</div>")


def week_dots(days):
    start = today_local() - timedelta(days=today_local().weekday())
    dots = []
    for i, lab in enumerate("MTWTFSS"):
        d = start + timedelta(days=i)
        dots.append(f"<div class='week-dot{' done' if d in days else ''}{' today' if d == today_local() else ''}'>{lab}</div>")
    md(f"<div class='card'><div class='metric-label'>This week</div><div class='week-dots'>{''.join(dots)}</div></div>")


def task_card(task, snap, day, with_go=False):
    pct, label = task_progress(task, snap, day)
    done = pct >= 100
    cls = "task" + (" special" if task.get("special") else "") + (" done" if done else "")
    md(f"<div class='{cls}'><div class='task-top'><div><div class='task-title'>{'✓ ' if done else ''}{esc(task['title'])}</div>"
       f"<div class='task-sub'>{esc(label)}</div></div><div class='task-reward'>+{task['reward']} QP</div></div>"
       f"<div class='bar'><div style='width:{pct}%'></div></div></div>")
    if with_go and not done:
        st.button("Start →", key=f"go_{task['id']}", on_click=goto, args=(task["go"],))


def shift_month(d, delta):
    idx = d.year * 12 + (d.month - 1) + delta
    return date(idx // 12, idx % 12 + 1, 1)


def _set_state(key, value):
    st.session_state[key] = value


def calendar_picker(key, default, available, completed=frozenset(), perfect=frozenset()):
    """Button calendar. Only enabled on days that have questions; ✓ = completed, ★ = perfect."""
    sel_k, mon_k = f"{key}_sel", f"{key}_month"
    if sel_k not in st.session_state:
        st.session_state[sel_k] = default
    if mon_k not in st.session_state:
        st.session_state[mon_k] = date(default.year, default.month, 1)
    sel, month = st.session_state[sel_k], st.session_state[mon_k]
    first, last = min(available), max(available)
    with st.container(key=f"cal-{key}"):
        c1, c2, c3 = st.columns([1, 4, 1])
        c1.button("‹", key=f"cal-{key}-prev", on_click=_set_state, args=(mon_k, shift_month(month, -1)),
                  disabled=month <= date(first.year, first.month, 1), **BTN_W)
        c2.markdown(f"<div class='cal-title'>{month:%B %Y}</div>", unsafe_allow_html=True)
        c3.button("›", key=f"cal-{key}-next", on_click=_set_state, args=(mon_k, shift_month(month, 1)),
                  disabled=month >= date(last.year, last.month, 1), **BTN_W)
        for c, wd in zip(st.columns(7), ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]):
            c.markdown(f"<div class='cal-wd'>{wd}</div>", unsafe_allow_html=True)
        for week in calendar.Calendar(0).monthdayscalendar(month.year, month.month):
            for c, n in zip(st.columns(7), week):
                if n == 0:
                    c.write("")
                    continue
                d = date(month.year, month.month, n)
                base = "perf" if d in perfect else ("done" if d in completed else "")
                state = (base + "sel") if d == sel else (base or "day")
                c.button(str(n), key=f"cal-{key}-{state}-{d.isoformat()}", on_click=_set_state, args=(sel_k, d),
                         disabled=d not in available, **BTN_W)
        md("<div class='cal-legend'><span class='cal-key' style='background:var(--accent)'></span>Perfect (no mistakes or skips) · "
           "<span class='cal-key' style='background:var(--accent-soft)'></span>Completed · "
           "<span class='cal-key' style='background:var(--surface);border:2px solid var(--accent-dark)'></span>Selected · greyed = no questions</div>")
    return sel


# =========================================================
# 12. QUIZ ENGINE
# =========================================================
def pick_questions(pool, count):
    """Spread a custom test over as many distinct study days as possible."""
    by_day = defaultdict(list)
    for q in pool:
        by_day[q["date"]].append(q)
    days, count = list(by_day), min(int(count), len(pool))
    if count <= len(days):
        picks = [random.choice(by_day[d]) for d in random.sample(days, count)]
    else:
        picks = [random.choice(by_day[d]) for d in days]
        taken = {p["key"] for p in picks}
        picks += random.sample([q for q in pool if q["key"] not in taken], count - len(picks))
    random.shuffle(picks)
    return picks


def records_from_source(src):
    if src["kind"] == "daily":
        recs = list(Q_BY_DATE.get(src["date"], []))
    elif src["kind"] == "custom":
        pool = [q for q in QUESTIONS if src["start"] <= q["date"] <= src["end"]]
        if src["qmode"] == "All":
            return random.sample(pool, len(pool))
        return pick_questions(pool, src["count"])
    else:
        recs = [Q_BY_KEY[k] for k in src["keys"] if k in Q_BY_KEY]
    random.shuffle(recs)
    return recs


def start_quiz(mode, records, page, source):
    qs = []
    for r in records:
        opts = list(r["options"])
        random.shuffle(opts)
        qs.append({"key": r["key"], "date": r["date"], "question": r["question"], "options": opts,
                   "correct": r["correct"], "explanation": r["explanation"], "user_answer": None, "locked": False})
    st.session_state["quiz"] = {"uid": uuid.uuid4().hex[:8], "mode": mode, "page": page, "questions": qs, "idx": 0,
                                "started_at": iso_now(), "source": source, "done": False, "result": None}


def toggle_bookmark(key):
    snap, uid = snapshot(), st.session_state["_uid"]
    try:
        if key in snap["bookmarks"]:
            supabase.table("bookmarks").delete().eq("user_id", uid).eq("question_id", key).execute()
            snap["bookmarks"].discard(key)
        else:
            insert_rows("bookmarks", [{"user_id": uid, "question_id": key}])
            snap["bookmarks"].add(key)
    except Exception as exc:
        db_note("Bookmarks", exc)
        st.toast("Could not update bookmark - see the setup notes.", icon="⚠️")


def _is_right(q):
    return q["user_answer"] is not None and q["user_answer"] == q["correct"]


def finalize_quiz():
    """Save once, at the end: 1 attempt + 1 bulk answers insert + bulk mistake sync + QP. No per-click DB calls."""
    quiz = st.session_state.get("quiz")
    if not quiz or quiz.get("result") is not None:
        return
    planned = len(quiz["questions"])
    qs = [q for q in quiz["questions"] if q["locked"]]
    quiz["questions"], quiz["done"] = qs, True
    completed_at = iso_now()
    if not qs:
        quiz["result"] = {"empty": True, "errors": [], "qp": None, "completed_at": completed_at}
        return
    errors, qp = [], None
    with st.spinner("Saving your results…"):
        snap, uid, today = snapshot(), st.session_state["_uid"], today_local()
        priors = [_sess(a) for a in snap["attempts"] if a["day"] == today]
        correct = sum(_is_right(q) for q in qs)
        skipped = sum(q["user_answer"] is None for q in qs)
        attended = len(qs) - skipped
        incorrect = attended - correct
        row = {"user_id": uid, "quiz_type": quiz["mode"], "started_at": quiz["started_at"], "completed_at": completed_at,
               "total_questions": len(qs), "correct": correct, "incorrect": incorrect, "skipped": skipped,
               "accuracy": correct / attended * 100 if attended else 0.0}
        attempt_id = None
        try:
            ins = insert_rows("quiz_attempts", [row])
            attempt_id = ins[0].get("id") if ins else None
            snap["attempts"].append(proc_attempt({**row, "id": attempt_id}))
            snap["_stats"] = None
        except Exception as exc:
            errors.append(f"Quiz attempt was not saved: {err_text(exc)}")
            db_note("Attempt", exc)

        if attempt_id is not None:
            try:
                insert_rows("answers", [{
                    "attempt_id": attempt_id, "user_id": uid, "question_id": q["key"], "question": q["question"],
                    "selected_answer": q["user_answer"], "correct_answer": q["correct"], "is_correct": _is_right(q),
                    "skipped": q["user_answer"] is None, "explanation": q["explanation"]} for q in qs])
            except Exception as exc:
                errors.append(f"Answer details were not saved: {err_text(exc)}")
                db_note("Answers", exc)

        try:  # mistake bank: bulk insert / bulk upsert / bulk delete
            existing, ins_rows, upd_rows, del_ids = snap["mistakes"], [], [], []
            for q in qs:
                ex = existing.get(q["key"])
                if _is_right(q):
                    if ex:
                        del_ids.append(ex["id"])
                elif ex:
                    upd_rows.append({"id": ex["id"], "user_id": uid, "question_id": ex["question_id"],
                                     "mistake_count": int(ex.get("mistake_count") or 0) + 1, "last_answer": q["user_answer"]})
                else:
                    ins_rows.append({"user_id": uid, "question_id": q["key"], "mistake_count": 1, "last_answer": q["user_answer"]})
            if ins_rows:
                for r in insert_rows("mistakes", ins_rows):
                    hit = lookup_question(r.get("question_id"))
                    if hit:
                        existing[hit["key"]] = r
            if upd_rows:
                supabase.table("mistakes").upsert(upd_rows).execute()
                for u in upd_rows:
                    hit = lookup_question(u["question_id"])
                    if hit:
                        existing[hit["key"]] = {**existing.get(hit["key"], {}), **u}
            for part in chunked(del_ids, 100):
                supabase.table("mistakes").delete().in_("id", part).execute()
            gone = set(del_ids)
            for k in [k for k, v in existing.items() if v.get("id") in gone]:
                existing.pop(k, None)
        except Exception as exc:
            errors.append(f"Mistake bank was not fully updated: {err_text(exc)}")
            db_note("Mistakes", exc)

        try:  # QP
            cur = {"mode": quiz["mode"], "total": len(qs), "correct": correct, "attempted": attended,
                   "planned": planned, "dates": {q["date"] for q in qs}}
            by_id = {t["id"]: t for t in tasks_for(today)}
            items = [(t, by_id[t]["reward"], by_id[t]["title"]) for t in newly_met_tasks(cur, priors) if t in by_id]
            if _perfect(cur):
                digest = hashlib.sha256("|".join(sorted(q["key"] for q in qs)).encode()).hexdigest()[:24]
                items.append((f"perfect_quiz:{digest}", 2, "Perfect score bonus"))
            qp = award_qp(items) if items else None
        except Exception as exc:
            errors.append(f"QP could not be awarded: {err_text(exc)}")
            db_note("QP", exc)

        daily_perfect = False
        if quiz["mode"] == "Daily" and len({q["date"] for q in qs}) == 1:
            try:
                daily_perfect = record_daily_result(snap, uid, qs[0]["date"], len(qs), correct)
            except Exception as exc:
                errors.append(f"Calendar mark was not saved: {err_text(exc)}")
                db_note("Calendar", exc)
    quiz["result"] = {"errors": errors, "qp": qp, "completed_at": completed_at, "celebrated": False,
                      "daily_perfect": daily_perfect}
    if qp and qp["promoted"]:
        st.toast(f"Promoted to {qp['to']}!", icon="🎉")


def record_daily_result(snap, uid, d, n_locked, correct):
    """Persist 'this study day was completed / perfect' in daily_quiz_results. Perfect is never removed."""
    expected = len(Q_BY_DATE.get(d, []))
    if not snap["daily_ok"] or expected == 0 or n_locked < max(1, (expected * 8 + 9) // 10):
        return False
    perfect_now = n_locked >= expected and correct == n_locked      # every question answered, none wrong, none skipped
    now, ex = iso_now(), snap["daily"].get(d)
    if ex is None:
        row = {"user_id": uid, "quiz_date": d.isoformat(), "total_questions": expected, "best_correct": correct,
               "attempts": 1, "completed": True, "perfect": perfect_now, "first_completed_at": now, "last_completed_at": now}
        try:
            insert_rows("daily_quiz_results", [row])
            snap["daily"][d] = row
            return perfect_now
        except Exception:                      # duplicate (another tab/device) -> merge instead
            ex = {}
    row = {"total_questions": max(int(ex.get("total_questions") or 0), expected),
           "best_correct": max(int(ex.get("best_correct") or 0), correct),
           "attempts": int(ex.get("attempts") or 0) + 1, "completed": True,
           "perfect": bool(ex.get("perfect")) or perfect_now, "last_completed_at": now}
    update_rows("daily_quiz_results", row, user_id=uid, quiz_date=d.isoformat())
    snap["daily"][d] = {**ex, **row}
    return perfect_now


def render_palette(qs, cur):
    items = []
    for i, q in enumerate(qs):
        cls = "palette-item" + (" palette-current" if i == cur else "")
        if q["locked"]:
            cls += " palette-good" if _is_right(q) else (" palette-skip" if q["user_answer"] is None else " palette-bad")
        items.append(f"<div class='{cls}'>{i + 1}</div>")
    md(f"<div class='palette'>{''.join(items)}</div>")


@st.fragment
def quiz_engine():
    quiz = st.session_state.get("quiz")
    if not quiz:
        return
    if quiz["done"]:
        render_results(quiz)
        return
    qs, uid = quiz["questions"], quiz["uid"]
    n = len(qs)
    if n == 0:
        st.warning("This quiz has no questions.")
        if st.button("Back"):
            st.session_state.pop("quiz", None)
            st.rerun()
        return
    i = quiz["idx"] = min(max(quiz["idx"], 0), n - 1)
    q, snap = qs[i], snapshot()
    answered = sum(x["locked"] for x in qs)

    head, act = st.columns([3, 1.7], vertical_alignment="center")
    with head:
        st.markdown(f"**Question {i + 1} of {n}** · {answered}/{n} answered")
        st.progress(answered / n)
    with act:
        b1, b2 = st.columns(2)
        if b1.button("🏁 Finish", key=f"fin_{uid}", help="End now and save the questions you have answered", **BTN_W):
            finalize_quiz()
            rerun_fragment()
        if b2.button("✕ Exit", key=f"exit_{uid}", help="Leave without saving", **BTN_W):
            st.session_state.pop("quiz", None)
            st.rerun()

    main, side = st.columns([3.4, 1], gap="large")
    with main:
        with st.container(border=True):
            marked = q["key"] in snap["bookmarks"]
            md(f"<div>{pill('📅 ' + q['date'].strftime('%d %b %Y'))}{pill('🌿 ' + quiz['mode'])}"
               f"{pill('🔖 Saved', 'good') if marked else ''}</div>"
               f"<div class='question-text'>{esc(q['question'])}</div>")
            if q["locked"]:
                rows = []
                for o in q["options"]:
                    if o == q["correct"]:
                        rows.append(f"<div class='opt opt-correct'>✅ {esc(o)}</div>")
                    elif o == q["user_answer"]:
                        rows.append(f"<div class='opt opt-wrong'>❌ {esc(o)}</div>")
                    else:
                        rows.append(f"<div class='opt'>{esc(o)}</div>")
                md("".join(rows))
                if q["user_answer"] is None:
                    st.warning("You skipped this question.")
                elif _is_right(q):
                    st.success("Correct! Read the explanation before moving on.")
                else:
                    st.error(f"Incorrect. You chose: {esc(q['user_answer'], br=False)}")
                md(f"<div class='explain'><div class='metric-label'>📖 Explanation</div>"
                   f"<div style='font-weight:700;margin:.3rem 0'>Correct answer: {esc(q['correct'])}</div>"
                   f"<div class='muted' style='line-height:1.6'>{esc(q['explanation'] or 'No explanation was provided for this question.')}</div></div>")
                cols = st.columns(3 if snap["bookmarks_ok"] else 2)
                if i > 0 and cols[0].button("← Previous", key=f"prev_{uid}_{i}", **BTN_W):
                    quiz["idx"] -= 1
                    rerun_fragment()
                if snap["bookmarks_ok"]:
                    cols[1].button("🔖 Unsave" if marked else "🔖 Save", key=f"bm_{uid}_{i}", on_click=toggle_bookmark,
                                   args=(q["key"],), **BTN_W)
                if i < n - 1:
                    if cols[-1].button("Next →", type="primary", key=f"next_{uid}_{i}", **BTN_W):
                        quiz["idx"] += 1
                        rerun_fragment()
                elif cols[-1].button("View results 🏁", type="primary", key=f"res_{uid}_{i}", **BTN_W):
                    finalize_quiz()
                    rerun_fragment()
            else:
                with st.container(key="quizopts"):
                    choice = st.radio("Select your answer", q["options"], index=None, key=f"opt_{uid}_{i}",
                                      label_visibility="collapsed")
                st.caption("Pick an answer, then press **Check answer**. It locks and the explanation appears.")
                cols = st.columns(4 if snap["bookmarks_ok"] else 3)
                if i > 0 and cols[0].button("← Previous", key=f"prev_{uid}_{i}", **BTN_W):
                    quiz["idx"] -= 1
                    rerun_fragment()
                if snap["bookmarks_ok"]:
                    cols[1].button("🔖 Unsave" if marked else "🔖 Save", key=f"bm_{uid}_{i}", on_click=toggle_bookmark,
                                   args=(q["key"],), **BTN_W)
                if cols[-2].button("Skip", key=f"skip_{uid}_{i}", **BTN_W):
                    q["user_answer"], q["locked"] = None, True
                    rerun_fragment()
                if cols[-1].button("Check answer", type="primary", key=f"chk_{uid}_{i}", **BTN_W):
                    if choice is None:
                        st.warning("Pick an option or use Skip.")
                    else:
                        q["user_answer"], q["locked"] = choice, True
                        rerun_fragment()
    with side:
        st.markdown("**Question map**")
        st.caption("Green correct · red wrong · blue skipped")
        render_palette(qs, i)
        c_, w_, s_ = sum(_is_right(x) for x in qs), sum(x["locked"] and x["user_answer"] is not None and not _is_right(x) for x in qs), sum(x["locked"] and x["user_answer"] is None for x in qs)
        metric("Score so far", f"{c_}/{answered}", f"{w_} wrong · {s_} skipped")
        md("<div class='tip'><b>Focus cue</b><br>Read the question twice before looking at the options.</div>")


def render_results(quiz):
    res, qs = quiz.get("result") or {}, quiz["questions"]
    n = len(qs)
    md("<div class='eyebrow'>Session complete</div>")
    st.title("Here's how you did 🏁")
    if n == 0:
        st.info("You finished before answering any question, so nothing was saved.")
        if st.button("Back", type="primary"):
            st.session_state.pop("quiz", None)
            st.rerun()
        return
    correct = sum(_is_right(q) for q in qs)
    skipped = sum(q["user_answer"] is None for q in qs)
    attended = n - skipped
    wrong = attended - correct
    acc = correct / attended * 100 if attended else 0
    t0, t1 = parse_ts(quiz["started_at"]), parse_ts(res.get("completed_at"))
    mins = max(1, round((t1 - t0).total_seconds() / 60)) if t0 and t1 else 0
    for e in res.get("errors", []):
        st.warning(e)
    if res.get("daily_perfect"):
        st.success("🌟 Perfect day! This date is now locked in green on your Daily Quiz calendar.")

    cols = st.columns(5)
    for c, (l, v, f) in zip(cols, [("Score", f"{correct}/{n}", "Correct answers"), ("Accuracy", f"{acc:.0f}%", "Of attempted"),
                                   ("Wrong", str(wrong), "Needs another look"), ("Skipped", str(skipped), "Left blank"),
                                   ("Time", f"{mins} min", "Start to finish")]):
        with c:
            metric(l, v, f)

    left, right = st.columns([1, 1.2], gap="large")
    with left:
        fig = go.Figure(go.Pie(values=[correct, wrong, skipped] if n else [1], labels=["Correct", "Wrong", "Skipped"], hole=0.72,
                               textinfo="none", marker_colors=[theme()["accent"], theme()["danger"], theme()["border"]]))
        fig_style(fig, 260, legend=True)
        fig.update_layout(annotations=[dict(text=f"{acc:.0f}%", x=0.5, y=0.5, showarrow=False, font_size=30, font_color=theme()["text"])])
        plot(fig, f"res_donut_{quiz['uid']}")
    with right:
        qp = res.get("qp")
        if qp and qp["promoted"]:
            if not res.get("celebrated"):
                st.balloons()
                res["celebrated"] = True
            md(f"<div class='promo'><div class='t'>🎉 Promoted!</div><div class='row'>{emblem_html(qp['from'], True)}<b>→</b>{emblem_html(qp['to'], True)}</div>"
               f"<div><b>{esc(qp['from'])}</b> → <b>{esc(qp['to'])}</b></div><div style='font-weight:900;margin-top:.4rem'>+{qp['total']} QP earned</div></div>")
        elif qp:
            lines = "".join(f"<li>{esc(t)} · <b>+{r} QP</b></li>" for t, r in qp["items"])
            md(f"<div class='notice'><b>+{qp['total']} QP earned</b><ul style='margin:.4rem 0 0 1rem'>{lines}</ul></div>")
        else:
            md("<div class='notice'>No mission completed by this session. Open <b>League</b> to see what today's missions need.</div>")
        st.button("🏆 See my missions", key="res_to_league", on_click=goto, args=(P_LEAGUE,), **BTN_W)

    weak = [q for q in qs if not _is_right(q)]
    section("Review queue", "Missed and skipped questions are now in your Review bank" if weak else "")
    if not weak:
        st.success("Perfect session. Nothing to review. 🌱")
    for q in weak:
        md(f"<div class='review-item'><div>{pill(q['date'].strftime('%d %b %Y'))}{pill('Skipped' if q['user_answer'] is None else 'Incorrect', 'bad')}</div>"
           f"<div class='q'>{esc(q['question'])}</div><div class='ans-line'><b>Correct:</b> {esc(q['correct'])}</div>"
           f"<div class='muted' style='font-size:.84rem;margin-top:.4rem'>{esc(q['explanation'])}</div></div>")
    with st.expander("Review every answer"):
        for q in qs:
            st_ = "Skipped" if q["user_answer"] is None else ("Correct" if _is_right(q) else "Incorrect")
            md(f"<div class='review-item'><div>{pill(st_, 'good' if st_ == 'Correct' else ('off' if st_ == 'Skipped' else 'bad'))}</div>"
               f"<div class='q'>{esc(q['question'])}</div><div class='ans-line'>Your answer: {esc(q['user_answer'] or '—')} · Correct: {esc(q['correct'])}</div></div>")

    section("What next?")
    c1, c2, c3 = st.columns(3)
    if c1.button("🔁 Reattempt", type="primary", key="res_again", **BTN_W):
        src = quiz["source"]
        start_quiz(quiz["mode"], records_from_source(src), quiz["page"], src)
        st.rerun()
    if c2.button("✅ Close results", key="res_close", **BTN_W):
        st.session_state.pop("quiz", None)
        st.rerun()
    c3.button("🏠 Dashboard", key="res_home", on_click=goto, args=(P_DASH,), **BTN_W)


# =========================================================
# 13. LEARNER PAGES
# =========================================================
TASK_TITLES = {t["id"]: t["title"] for t in GENERAL_TASKS}
TASK_TITLES.update({f"special_{c}": title for c, title, _, _ in SPECIAL_TASK_ROTATION})


def task_title(key):
    return "Perfect score bonus" if str(key).startswith("perfect_quiz:") else TASK_TITLES.get(key, str(key))


def announcement_banners():
    dismissed = st.session_state.setdefault("_ann_dismissed", set())
    for a in active_announcements():
        if a["id"] in dismissed:
            continue
        kind = {"warning": "warn", "alert": "bad"}.get(str(a.get("level")), "")
        c1, c2 = st.columns([14, 1], vertical_alignment="center")
        c1.markdown(H(f"<div class='notice {kind}'>📣 {esc(a['message'])}</div>"), unsafe_allow_html=True)
        c2.button("✕", key=f"ann_{a['id']}", on_click=dismissed.add, args=(a["id"],), help="Dismiss")


def resume_banner(page):
    quiz = st.session_state.get("quiz")
    if quiz and not quiz["done"] and quiz["page"] != page:
        done = sum(q["locked"] for q in quiz["questions"])
        c1, c2 = st.columns([5, 1.2], vertical_alignment="center")
        c1.markdown(H(f"<div class='notice'>▶ <b>Quiz in progress</b> · {esc(quiz['mode'])} · {done}/{len(quiz['questions'])} answered</div>"),
                    unsafe_allow_html=True)
        c2.button("Resume", key="resume_quiz", type="primary", on_click=goto, args=(quiz["page"],), **BTN_W)


def _cb_start_latest_daily():
    src = {"kind": "daily", "date": LATEST_DATE}
    start_quiz("Daily", records_from_source(src), P_DAILY, src)
    st.session_state["nav_page"] = P_DAILY


# ---------------------------------------------------------
# Dashboard
# ---------------------------------------------------------
def page_dashboard():
    snap, today = snapshot(), today_local()
    stats, lg = stats_of(snap), league_of(snap)
    first = (st.session_state["auth_user"]["name"] or "there").split()[0]
    hour = now_local().hour
    greet = "Good morning" if hour < 12 else ("Good afternoon" if hour < 17 else "Good evening")
    done_today = "daily_quiz" in completed_keys(snap, today)
    n_latest = len(Q_BY_DATE[LATEST_DATE])
    bank = mistake_bank(snap)

    announcement_banners()
    resume_banner(P_DASH)
    hero("Current Affairs Study Studio", f"{greet}, {first}",
         ("You've finished the latest Daily Quiz. Keep the momentum with a custom test or a review round."
          if done_today else f"The latest Daily Quiz ({n_latest} questions from {LATEST_DATE:%d %b %Y}) is ready when you are."),
         f"{len(QUESTIONS):,} questions · {len(DATES):,} study days · latest {LATEST_DATE:%d %b %Y}")
    b1, b2, b3 = st.columns(3)
    b1.button("🌿 Retake latest Daily Quiz" if done_today else "🌿 Start latest Daily Quiz", type="primary",
              key="dash_daily", on_click=_cb_start_latest_daily, **BTN_W)
    b2.button("🎯 Build a custom test", key="dash_custom", on_click=goto, args=(P_CUSTOM,), **BTN_W)
    b3.button(f"🧠 Review mistakes ({len(bank)})", key="dash_review", on_click=goto, args=(P_REVIEW,), **BTN_W)

    stat_grid([("Current streak", f"{stats['current_streak']} 🔥", "Best: " + plural(stats["best_streak"], "day")),
               ("Overall accuracy", f"{stats['accuracy']:.0f}%", f"{stats['correct']} of {stats['attended']} answers"),
               ("League", lg["name"], f"{lg['qp']:,} QP · {lg['lifetime']:,} lifetime"),
               ("Quizzes taken", str(stats["quizzes"]), f"{len(bank)} in review bank")])

    left, right = st.columns([1.5, 1], gap="large")
    with left:
        section("Today's missions", "Complete them to earn QP")
        tasks = tasks_for(today)
        done_keys = completed_keys(snap, today)
        active = [t for t in tasks if t["id"] not in done_keys]
        for t in active:
            task_card(t, snap, today)
        if not active:
            md("<div class='notice'>✓ Every mission is complete for today. Come back tomorrow for a fresh set.</div>")
        st.caption(f"{len(tasks) - len(active)}/{len(tasks)} completed today")
        st.button("Open League and missions →", key="dash_to_league", on_click=goto, args=(P_LEAGUE,))
    with right:
        section("Your league")
        nxt = f"{lg['remaining']:,} QP to {lg['next']}" if lg["next"] else "Top league reached"
        md(f"<div class='card'><div class='league-row'>{emblem_html(lg['name'], True)}<div><b>{esc(lg['name'])}</b>"
           f"<div class='task-sub'>{lg['qp']:,} / {lg['threshold'] or '—'} QP</div></div></div>"
           f"<div class='bar' style='margin:.7rem 0 .4rem'><div style='width:{lg['pct'] * 100:.0f}%'></div></div>"
           f"<div class='task-sub'>{esc(nxt)}</div></div>")
        week_dots(stats["days"])
        if bank:
            md(f"<div class='tip'>🧠 <b>{plural(len(bank), 'question')}</b> waiting in your review bank. Answer them correctly and they disappear.</div>")

    section("Recent activity", "Last 14 days")
    c1, c2 = st.columns(2, gap="large")
    by_day = Counter()
    for a in snap["attempts"]:
        if a["day"]:
            by_day[a["day"]] += a["correct"] + a["incorrect"]
    days = [today - timedelta(days=13 - i) for i in range(14)]
    with c1:
        fig = go.Figure(go.Bar(x=[f"{d:%d %b}" for d in days], y=[by_day.get(d, 0) for d in days], marker_color=theme()["accent"]))
        plot(fig_style(fig, 250), "dash_activity")
        st.caption("Questions answered per day")
    with c2:
        recent = [a for a in snap["attempts"] if a["dt"]][-15:]
        if recent:
            ys = [a["correct"] / max(a["correct"] + a["incorrect"], 1) * 100 for a in recent]
            fig = go.Figure(go.Scatter(x=list(range(1, len(ys) + 1)), y=ys, mode="lines+markers",
                                       line=dict(color=theme()["accent"], width=3), marker=dict(size=7)))
            fig_style(fig, 250).update_yaxes(range=[0, 100], ticksuffix="%")
            plot(fig, "dash_scores")
            st.caption("Accuracy of your latest quizzes")
        else:
            st.info("Finish your first quiz to see your accuracy trend here.")
    earned = [b for b in badges_of(snap) if b[3]]
    if earned:
        section("Badges earned", f"{len(earned)} of {len(BADGE_DEFS)}")
        md("<div class='badge-grid'>" + "".join(
            f"<div class='badge'><div class='i'>{i}</div><div class='n'>{esc(n)}</div><div class='d'>{esc(d)}</div></div>"
            for i, n, d, _ in earned[:6]) + "</div>")


# ---------------------------------------------------------
# Daily quiz
# ---------------------------------------------------------
def _answers_for_user(uid, attempts):
    try:
        return fetch_all("answers", "attempt_id,question_id,is_correct,skipped", eq={"user_id": uid}, order=("id", False))
    except Exception:
        out = []
        for part in chunked([a["id"] for a in attempts if a.get("id") is not None], 100):
            out += rows_of(supabase.table("answers").select("attempt_id,question_id,is_correct,skipped").in_("attempt_id", part).execute())
        return out


def legacy_daily_status():
    """Old method: rebuild completed/perfect days from the answers table. Used for a one-time migration and as a fallback."""
    cache = st.session_state.get("_daily_status")
    if cache and time.time() - cache["at"] < 300:
        return cache
    snap, uid = snapshot(), st.session_state["_uid"]
    completed, perfect = set(), set()
    try:
        by_attempt = defaultdict(list)
        for r in _answers_for_user(uid, snap["attempts"]):
            by_attempt[str(r.get("attempt_id"))].append(r)
        types = {str(a["id"]): a["type"].lower() for a in snap["attempts"]}
        expected = {d: len(v) for d, v in Q_BY_DATE.items()}
        for aid, rs in by_attempt.items():
            found = set()
            for r in rs:
                try:
                    found.add(date.fromisoformat(str(r["question_id"]).split("|", 1)[0]))
                except Exception:
                    pass
            if len(found) != 1:
                continue
            d = next(iter(found))
            exp, t = expected.get(d, 0), types.get(aid, "")
            if exp == 0 or not (t == "daily" or (t == "all" and len(rs) == exp)) or len(rs) < max(1, int(exp * 0.8)):
                continue
            completed.add(d)
            # Perfect = every question of that day answered correctly, none skipped. Once earned it is never removed.
            if len(rs) >= exp and all(r.get("is_correct") and not r.get("skipped") for r in rs):
                perfect.add(d)
    except Exception as exc:
        db_note("Calendar", exc)
    st.session_state["_daily_status"] = {"at": time.time(), "completed": completed, "perfect": perfect}
    return st.session_state["_daily_status"]


def _migrate_legacy_daily(snap):
    """One-time, per learner: copy days derived from old answer history into daily_quiz_results."""
    uid = st.session_state["_uid"]
    if st.session_state.get("_daily_migrated") == uid or snap["daily"]:
        return
    st.session_state["_daily_migrated"] = uid
    if not any(a["type"].lower() in ("daily", "all") for a in snap["attempts"]):
        return
    try:
        old = legacy_daily_status()
        rows = [{"user_id": uid, "quiz_date": d.isoformat(), "total_questions": len(Q_BY_DATE.get(d, [])),
                 "best_correct": len(Q_BY_DATE.get(d, [])) if d in old["perfect"] else 0, "attempts": 1,
                 "completed": True, "perfect": d in old["perfect"]} for d in old["completed"]]
        if rows:
            insert_rows("daily_quiz_results", rows)
            for r in rows:
                snap["daily"][date.fromisoformat(r["quiz_date"])] = r
    except Exception as exc:
        db_note("Calendar", exc)


def daily_status():
    """Completed / perfect study days. Persistent (from daily_quiz_results) whenever that table exists."""
    snap = snapshot()
    if snap["daily_ok"]:
        _migrate_legacy_daily(snap)
        rows = snap["daily"]
        return {"completed": {d for d, r in rows.items() if r.get("completed", True)},
                "perfect": {d for d, r in rows.items() if r.get("perfect")}, "persistent": True}
    return {**legacy_daily_status(), "persistent": False}


@st.fragment
def daily_setup():
    status = daily_status()
    if not status["persistent"]:
        md("<div class='notice warn'>Calendar marks are not being saved permanently yet. Run the latest "
           "<b>supabase_setup.sql</b> (it creates the <b>daily_quiz_results</b> table) and reload.</div>")
    left, right = st.columns([1.15, 1], gap="large")
    with left:
        sel = calendar_picker("daily", LATEST_DATE, set(DATES), status["completed"], status["perfect"])
    pool = Q_BY_DATE.get(sel, [])
    with right:
        tag = pill("Perfect · no mistakes or skips", "good") if sel in status["perfect"] else (pill("Completed ✓", "good") if sel in status["completed"] else pill("Not attempted", "off"))
        md(f"<div class='card'><div class='metric-label'>{sel:%A, %d %B %Y}</div>"
           f"<div class='metric-value'>{len(pool)} <span style='font-size:1rem' class='muted'>questions</span></div>"
           f"<div style='margin:.5rem 0'>{tag}{pill('Latest', 'warn') if sel == LATEST_DATE else ''}</div>"
           "<p class='muted'>Your questions are shuffled into a fresh session every time.</p>"
           "<div class='tip'>🧠 <b>Memory rule:</b> answer first, then read the explanation before moving on.</div></div>")
        if pool:
            if st.button("🌿 Start Daily Quiz", type="primary", key="start_daily", **BTN_W):
                src = {"kind": "daily", "date": sel}
                start_quiz("Daily", records_from_source(src), P_DAILY, src)
                st.rerun()
        else:
            st.warning("No questions for this date.")


def page_daily():
    resume_banner(P_DAILY)
    md("<div class='eyebrow'>Daily practice</div>")
    st.title("Your daily knowledge reset")
    st.write("Pick a study day and turn its current affairs into a focused quiz.")
    daily_setup()


# ---------------------------------------------------------
# Custom test
# ---------------------------------------------------------
@st.fragment
def custom_setup():
    preset = st.radio("Date range", ["Latest 7 study days", "Latest 30 study days", "This month", "All time", "Custom range"],
                      horizontal=True, key="custom_preset")
    if preset == "Latest 7 study days":
        start, end = DATES[-7:][0], LATEST_DATE
    elif preset == "Latest 30 study days":
        start, end = DATES[-30:][0], LATEST_DATE
    elif preset == "This month":
        start, end = LATEST_DATE.replace(day=1), LATEST_DATE
    elif preset == "All time":
        start, end = DATES[0], LATEST_DATE
    else:
        val = st.date_input("Choose a range", value=(DATES[-30:][0], LATEST_DATE), min_value=DATES[0], max_value=LATEST_DATE, key="custom_range")
        if not (isinstance(val, (tuple, list)) and len(val) == 2):
            st.info("Pick an end date to finish the range.")
            return
        start, end = val
    pool = [q for q in QUESTIONS if start <= q["date"] <= end]
    days = len({q["date"] for q in pool})
    st.caption(f"{start:%d %b %Y} → {end:%d %b %Y}")
    c1, c2 = st.columns(2)
    with c1:
        metric("Available", f"{len(pool):,}", "Questions in range")
    with c2:
        metric("Study days", f"{days:,}", "Days with questions")
    if not pool:
        st.warning("No questions in this range.")
        return
    qmode = st.radio("Question quantity", ["Custom Number", "All"], horizontal=True, key="custom_qmode")
    count = len(pool)
    if qmode == "Custom Number":
        count = int(st.number_input("Number of questions", min_value=1, max_value=len(pool), value=min(15, len(pool)), step=1))
        if count > days:
            st.info(f"At least **1 question from each of the {days} days**, then **{count - days}** more picked at random.")
        else:
            st.info(f"Exactly **{count} questions** from **{count} randomly chosen days** (1 per day).")
    else:
        st.info(f"You'll practise all **{len(pool)}** questions in this range.")
    if st.button("🎯 Generate my test", type="primary", key="gen_custom", **BTN_W):
        src = {"kind": "custom", "start": start, "end": end, "qmode": qmode, "count": count}
        start_quiz(qmode, records_from_source(src), P_CUSTOM, src)
        st.rerun()


def page_custom():
    resume_banner(P_CUSTOM)
    md("<div class='eyebrow'>Build your session</div>")
    st.title("Design a test that fits your time")
    st.write("Choose a date range and size. Small tests sample different days; large tests cover every day.")
    custom_setup()


# ---------------------------------------------------------
# Review mistakes / bookmarks
# ---------------------------------------------------------
def _clear_orphans():
    snap = snapshot()
    try:
        ids = [r["id"] for r in snap["orphans"] if r.get("id") is not None]
        for part in chunked(ids, 100):
            supabase.table("mistakes").delete().in_("id", part).execute()
        snap["orphans"] = []
        st.toast("Removed unavailable questions.", icon="🧹")
    except Exception as exc:
        db_note("Mistakes", exc)


def _mistake_card(item):
    last = "Skipped" if item.get("last_answer") is None else f"Your answer: {item['last_answer']}"
    tag = pill("Needs review · " + str(item.get("times_missed", 1)) + "×", "bad")
    md(f"<div class='review-item'><div>{tag}"
       f"<span class='muted' style='font-size:.78rem'>{item['date']:%d %b %Y}</span></div><div class='q'>{esc(item['question'])}</div>"
       f"<div class='ans-line'><b>Correct:</b> {esc(item['correct'])}</div><div class='ans-line'><b>Last attempt:</b> {esc(last)}</div>"
       f"<div class='muted' style='font-size:.84rem;margin-top:.5rem'><b>Explanation:</b> {esc(item['explanation'])}</div></div>")


def page_review():
    resume_banner(P_REVIEW)
    snap = snapshot()
    bank = sorted(mistake_bank(snap), key=lambda x: (-x["times_missed"], x["date"]))
    md("<div class='eyebrow'>Memory repair</div>")
    st.title("Review your mistakes")
    st.write("Questions you missed or skipped stay here until you answer them correctly.")
    if not bank:
        st.info("Your mistake bank is empty. Nothing to repair right now.")
        c1, c2 = st.columns(2)
        if c1.button("🔄 Start a reinforcement quiz (10 random questions)", type="primary", key="reinforce", **BTN_W):
            recs = random.sample(QUESTIONS, min(10, len(QUESTIONS)))
            src = {"kind": "keys", "keys": [r["key"] for r in recs]}
            start_quiz("Review", recs, P_REVIEW, src)
            st.rerun()
        c2.button("📅 Open Daily Quiz", key="review_to_daily", on_click=goto, args=(P_DAILY,), **BTN_W)
        st.caption("A reinforcement quiz counts for the daily Review mission even when your bank is empty.")
    else:
        c1, c2, c3 = st.columns(3)
        with c1:
            metric("Needs review", str(len(bank)), "Questions in your bank")
        with c2:
            metric("Most missed", f"{bank[0]['times_missed']}×", "Highest miss count")
        with c3:
            metric("Goal", "0 left", "Clear the bank by mastering them")
        count = int(st.number_input("How many to practise now?", 1, len(bank), min(15, len(bank)), 1))
        if st.button(f"🧠 Practise {count} mistake{'s' if count != 1 else ''} (most missed first)", type="primary", key="practise", **BTN_W):
            chosen = bank[:count]
            start_quiz("Review", chosen, P_REVIEW, {"kind": "keys", "keys": [c["key"] for c in chosen]})
            st.rerun()
        section("Your mistake bank", "Correct answers remove items from this list")
        show_all = st.toggle("Show all questions", value=False, key="review_show_all")
        for item in (bank if show_all else bank[:20]):
            _mistake_card(item)
        if not show_all and len(bank) > 20:
            st.caption(f"Showing 20 of {len(bank)}. Turn on 'Show all questions' to see the rest.")
    if snap["orphans"]:
        st.caption(f"{plural(len(snap['orphans']), 'saved mistake')} refer to questions that were removed or edited in the sheet.")
        st.button("🧹 Clear unavailable questions", key="clear_orphans", on_click=_clear_orphans)


def page_bookmarks():
    resume_banner(P_MARKS)
    snap = snapshot()
    md("<div class='eyebrow'>Saved for later</div>")
    st.title("Bookmarks")
    if not snap["bookmarks_ok"]:
        md("<div class='notice warn'>Bookmarks need one extra table. Run <b>supabase_setup.sql</b> in the Supabase SQL editor, then reload.</div>")
        return
    items = [Q_BY_KEY[k] for k in snap["bookmarks"] if k in Q_BY_KEY]
    items.sort(key=lambda q: q["date"], reverse=True)
    if not items:
        st.info("Tap **🔖 Save** on any question during a quiz and it will appear here.")
        return
    st.write(f"{plural(len(items), 'question')} saved.")
    if st.button("🔖 Practise my bookmarks", type="primary", key="practise_marks", **BTN_W):
        pick = items[:50]
        start_quiz("Bookmarks", pick, P_MARKS, {"kind": "keys", "keys": [q["key"] for q in pick]})
        st.rerun()
    for q in items[:50]:
        md(f"<div class='review-item'><div>{pill(q['date'].strftime('%d %b %Y'))}</div><div class='q'>{esc(q['question'])}</div>"
           f"<div class='ans-line'><b>Answer:</b> {esc(q['correct'])}</div>"
           f"<div class='muted' style='font-size:.84rem;margin-top:.5rem'>{esc(q['explanation'])}</div></div>")
        st.button("Remove bookmark", key=f"rm_{hashlib.md5(q['key'].encode()).hexdigest()[:10]}", on_click=toggle_bookmark, args=(q["key"],))


# ---------------------------------------------------------
# League
# ---------------------------------------------------------
def league_hero(lg):
    nxt = "Champion I reached — keep building lifetime QP." if lg["threshold"] is None else f"{lg['remaining']:,} QP to {lg['next']}"
    md(f"<div class='league-hero'><div class='league-row'><div class='league-badge'>{emblem_html(lg['name'])}</div><div>"
       f"<div class='league-kicker'>Current league</div><div class='league-name'>{esc(lg['name'])}</div>"
       f"<div class='league-meta'>{lg['lifetime']:,} lifetime QP · leagues never demote</div></div></div>"
       f"<div class='league-qpline'><span>{lg['qp']:,} QP</span><span>{lg['threshold'] if lg['threshold'] is not None else 'MAX'} QP</span></div>"
       f"<div class='bar' style='height:12px'><div style='width:{lg['pct'] * 100:.1f}%'></div></div><div class='league-meta'>{esc(nxt)}</div></div>")


def page_league():
    resume_banner(P_LEAGUE)
    snap, today = snapshot(), today_local()
    stats, lg = stats_of(snap), league_of(snap)
    md("<div class='eyebrow'>Your learning progression</div>")
    st.title("League")
    st.write("Turn consistent study into QP, climb the leagues and keep your progress permanently.")
    league_hero(lg)
    left, right = st.columns([1.55, 1], gap="large")
    with left:
        done = completed_keys(snap, today)
        for label, group in (("General missions", GENERAL_TASKS), ("Special missions", special_tasks(today))):
            active = [t for t in group if t["id"] not in done]
            md(f"<div class='group-label'><span>{label}</span><span>{len(active)} remaining</span></div>")
            for t in active:
                task_card(t, snap, today, with_go=True)
            if not active:
                md(f"<div class='notice'>✓ All {label.lower()} completed today.</div>")
        finished = [t for t in tasks_for(today) if t["id"] in done]
        if finished:
            with st.expander(f"Completed today ({len(finished)})"):
                for t in finished:
                    task_card(t, snap, today)
        st.caption("Missions reset at midnight " + str(cfg("app", "timezone", "Asia/Kolkata")) + ". A quiz only counts when you attempt at least half of its questions.")
    with right:
        section("Your stats")
        metric("🔥 Current streak", plural(stats["current_streak"], "day"), "Streak is independent of QP")
        metric("⭐ Lifetime QP", f"{lg['lifetime']:,}", "Everything you have earned")
        section("Recent QP")
        hist = sorted(snap["completions"], key=lambda c: str(c.get("completed_at")), reverse=True)[:8]
        if hist:
            md("<div class='card'>" + "".join(
                f"<div class='ladder-row'><span>{esc(task_title(c['task_key']))}</span><b>+{int(c.get('qp_awarded') or 0)}</b></div>" for c in hist) + "</div>")
        else:
            st.caption("No QP earned yet.")
        section("League ladder")
        rows = []
        for j, (name, _) in enumerate(LEAGUE_LADDER):
            mark = "✓" if j < lg["idx"] else ("●" if j == lg["idx"] else "○")
            rows.append(f"<div class='ladder-row{' cur' if j == lg['idx'] else ''}'><span>{mark} {LEAGUE_ICONS[name.split()[0]]} {name}</span>"
                        f"<span class='muted' style='font-size:.75rem'>{'Current' if j == lg['idx'] else ('Reached' if j < lg['idx'] else '')}</span></div>")
        md("<div class='card'>" + "".join(rows) + "</div>")
        st.button("Open Rankings →", key="league_to_rank", on_click=goto, args=(P_RANK,), **BTN_W)
    section("Badges", f"{sum(b[3] for b in badges_of(snap))} of {len(BADGE_DEFS)} earned")
    md("<div class='badge-grid'>" + "".join(
        f"<div class='badge{'' if e else ' locked'}'><div class='i'>{i}</div><div class='n'>{esc(n)}</div><div class='d'>{esc(d)}</div></div>"
        for i, n, d, e in badges_of(snap)) + "</div>")


# ---------------------------------------------------------
# Rankings
# ---------------------------------------------------------
def render_board(rows, me_uid, value_fn, empty):
    if not rows:
        st.info(empty)
        return
    mine = next((r for r in rows if r["uid"] == me_uid), None)
    if mine:
        big, small = value_fn(mine)
        md(f"<div class='rank-me'><div><div class='metric-label'>Your position</div><div class='r'>#{mine['rank']}</div></div>"
           f"<div style='text-align:right'><b>{esc(mine['public_id'])}</b><br><span class='muted'>{esc(big)} · {esc(small)}</span></div></div>")
    else:
        st.caption("You are not on this board yet — earn QP to appear.")
    out = []
    for r in rows[:TOP_N]:
        mark = {1: "🥇", 2: "🥈", 3: "🥉"}.get(r["rank"], f"#{r['rank']}")
        big, small = value_fn(r)
        out.append(f"<div class='rank-row{' me' if r['uid'] == me_uid else ''}'><div class='rank-place'>{mark}</div>"
                   f"<div><div class='rank-name'>{esc(r['public_id'])}{' (you)' if r['uid'] == me_uid else ''}</div>"
                   f"<div class='rank-sub'>{emblem_html(r['league'], True)} {esc(r['league'])}</div></div>"
                   f"<div class='rank-val'><b>{esc(big)}</b><small>{esc(small)}</small></div></div>")
    md("<div class='rank-card'>" + "".join(out) + "</div>")
    if mine and mine["rank"] > TOP_N:
        st.caption(f"Only the top {TOP_N} are listed. Your private position is #{mine['rank']}.")


def page_rankings():
    resume_banner(P_RANK)
    md("<div class='eyebrow'>Learner rankings</div>")
    st.title("Rankings")
    st.write("Only public User IDs are shown. Names and email addresses are never displayed to other learners.")
    data = ranking_data(week_start_iso())
    if data["error"]:
        st.warning(f"Rankings could not be loaded: {data['error']}")
        return
    me = str(st.session_state["_uid"])
    t1, t2, t3 = st.tabs(["🏆 Leagues", "📅 This week", "⭐ All-time"])
    with t1:
        render_board(data["league"], me, lambda r: (f"{r['qp']:,} QP", f"{r['lifetime']:,} lifetime"), "Rankings appear once learners join.")
        md("<div class='tip'>📌 League order: league → current QP → lifetime QP. Streaks never affect your league position.</div>")
    with t2:
        render_board(data["weekly"], me, lambda r: (f"{r['weekly']:,} QP", "earned this week"), "Nobody has earned QP this week yet. Be the first!")
        st.caption("The weekly board resets every Monday.")
    with t3:
        render_board(data["lifetime"], me, lambda r: (f"{r['lifetime']:,} QP", r["league"]), "Rankings appear once learners join.")


# ---------------------------------------------------------
# My progress
# ---------------------------------------------------------
def activity_heatmap(day_counts, weeks=16):
    T = theme()
    end = today_local()
    start = end - timedelta(days=end.weekday()) - timedelta(weeks=weeks - 1)
    z = [[None] * weeks for _ in range(7)]
    tip = [[""] * weeks for _ in range(7)]
    for w in range(weeks):
        for dow in range(7):
            d = start + timedelta(weeks=w, days=dow)
            if d <= end:
                z[dow][w] = day_counts.get(d, 0)
                tip[dow][w] = f"{d:%a %d %b}: {day_counts.get(d, 0)} answered"
    fig = go.Figure(go.Heatmap(z=z, x=[f"{start + timedelta(weeks=w):%d %b}" for w in range(weeks)],
                               y=["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"], text=tip, hoverinfo="text",
                               colorscale=[[0, T["surface_2"]], [0.001, T["accent_soft"]], [1, T["accent"]]],
                               showscale=False, xgap=3, ygap=3))
    fig_style(fig, 230).update_yaxes(autorange="reversed", showgrid=False)
    return fig


def page_progress():
    resume_banner(P_PROGRESS)
    snap, auth = snapshot(), st.session_state["auth_user"]
    stats, lg = stats_of(snap), league_of(snap)
    md("<div class='eyebrow'>Your analytics</div>")
    st.title("My progress")
    md(f"<div class='card'><div class='league-row'>{emblem_html(lg['name'], True)}<div><b>{esc(auth['name'])}</b>"
       f"<div class='task-sub'>Public User ID: <b>{esc(snap['profile'].get('public_user_id') or '—')}</b> · {esc(lg['name'])} · {lg['lifetime']:,} lifetime QP</div></div></div></div>")
    stat_grid([("Quizzes", str(stats["quizzes"]), "Completed sessions"), ("Answered", f"{stats['attended']:,}", "Questions attempted"),
               ("Accuracy", f"{stats['accuracy']:.0f}%", f"{stats['correct']} correct"), ("Best streak", plural(stats["best_streak"], "day"), f"Current: {stats['current_streak']}")])
    atts = [a for a in snap["attempts"] if a["day"]]
    if not atts:
        st.info("Complete a quiz and your analytics will appear here.")
        return
    by_day = Counter()
    for a in atts:
        by_day[a["day"]] += a["correct"] + a["incorrect"]
    section("Study activity", "Questions answered per day, last 16 weeks")
    plot(activity_heatmap(by_day), "prog_heat")
    c1, c2 = st.columns(2, gap="large")
    ordered = sorted(atts, key=lambda a: a["dt"])[-30:]
    ys = [a["correct"] / max(a["correct"] + a["incorrect"], 1) * 100 for a in ordered]
    roll = pd.Series(ys).rolling(5, min_periods=1).mean().tolist()
    with c1:
        section("Accuracy trend", "Latest 30 quizzes")
        fig = go.Figure()
        fig.add_bar(x=list(range(1, len(ys) + 1)), y=ys, marker_color=theme()["accent_soft"], name="Quiz")
        fig.add_scatter(x=list(range(1, len(ys) + 1)), y=roll, mode="lines", line=dict(color=theme()["accent_dark"], width=3), name="5-quiz average")
        fig_style(fig, 260, legend=True).update_yaxes(range=[0, 100], ticksuffix="%")
        plot(fig, "prog_trend")
    with c2:
        section("Quizzes by type")
        types = Counter(a["type"] for a in atts)
        fig = go.Figure(go.Bar(x=list(types.keys()), y=list(types.values()), marker_color=theme()["accent"]))
        plot(fig_style(fig, 260), "prog_types")
    section("Recent quizzes")
    rows = [[f"{a['dt']:%d %b %Y, %H:%M}", a["type"], a["total"], a["correct"], a["incorrect"], a["skipped"],
             f"{a['correct'] / max(a['correct'] + a['incorrect'], 1) * 100:.0f}%"] for a in sorted(atts, key=lambda a: a["dt"], reverse=True)[:15]]
    md(html_table(["When", "Type", "Questions", "Correct", "Wrong", "Skipped", "Accuracy"], rows, 640))
    df_ = pd.DataFrame([{"date": f"{a['dt']:%Y-%m-%d %H:%M}", "type": a["type"], "questions": a["total"], "correct": a["correct"],
                         "incorrect": a["incorrect"], "skipped": a["skipped"]} for a in sorted(atts, key=lambda a: a["dt"])])
    st.download_button("⬇️ Download my quiz history (CSV)", df_.to_csv(index=False).encode("utf-8"), "my_quiz_history.csv", "text/csv", key="dl_hist")


# =========================================================
# 14. ADMIN CONTROL ROOM
# =========================================================
def fmt_dt(raw, fmt="%d %b %Y, %H:%M"):
    dt = raw if isinstance(raw, datetime) else parse_ts(raw)
    return dt.strftime(fmt) if dt else "—"


@st.cache_data(ttl=60, show_spinner=False)
def admin_model():
    """Everything the admin screens need, fetched in 4 parallel paged queries and aggregated once."""
    res = run_parallel({
        "profiles": lambda: fetch_all("profiles", "*", order=("id", False)),
        "league": lambda: fetch_all("league_progress", "user_id,qp,lifetime_qp,league_index", order=("user_id", False)),
        "attempts": lambda: fetch_all("quiz_attempts", "user_id,quiz_type,completed_at,total_questions,correct,incorrect,skipped", order=("completed_at", True)),
        "mistakes": lambda: fetch_all("mistakes", "user_id", order=("id", False)),
    })
    errors = {k: err_text(e) for k, (_, e) in res.items() if e is not None}
    lg = {str(r["user_id"]): r for r in (res["league"][0] or [])}
    mist = Counter(str(r["user_id"]) for r in (res["mistakes"][0] or []))
    per_user = defaultdict(list)
    d_quiz, d_users, d_qs = Counter(), defaultdict(set), Counter()
    for a in res["attempts"][0] or []:
        dt = parse_ts(a.get("completed_at"))
        day, uid = (dt.date() if dt else None), str(a["user_id"])
        per_user[uid].append((day, a))
        if day:
            d_quiz[day] += 1
            d_users[day].add(uid)
            d_qs[day] += int(a.get("correct") or 0) + int(a.get("incorrect") or 0)
    today, users = today_local(), []
    for p in res["profiles"][0] or []:
        uid = str(p["id"])
        rows = per_user.get(uid, [])
        days = {d for d, _ in rows if d}
        cur, best = streaks(days)
        try:
            ov = p.get("streak_override")
            override = None if ov in (None, "") else max(0, int(ov))
        except Exception:
            override = None
        correct = sum(int(a.get("correct") or 0) for _, a in rows)
        attended = correct + sum(int(a.get("incorrect") or 0) for _, a in rows)
        r = lg.get(uid, {})
        idx = min(max(int(r.get("league_index") or 0), 0), len(LEAGUE_LADDER) - 1)
        created = parse_ts(p.get("created_at"))
        users.append({"id": uid, "email": str(p.get("email") or "").lower(), "name": str(p.get("name") or ""),
                      "role": str(p.get("role") or "user"), "public_id": str(p.get("public_user_id") or ""),
                      "disabled": bool(p.get("disabled")), "created": created, "last_seen": p.get("last_seen"),
                      "league": LEAGUE_LADDER[idx][0], "league_index": idx, "qp": int(r.get("qp") or 0),
                      "lifetime": int(r.get("lifetime_qp") or 0), "streak": override if override is not None else cur,
                      "override": override, "quizzes": len(rows), "correct": correct, "attended": attended,
                      "accuracy": correct / attended * 100 if attended else 0.0,
                      "questions": sum(int(a.get("total_questions") or 0) for _, a in rows),
                      "mistakes": mist.get(uid, 0), "last_active": max(days) if days else None})
    learners = [u for u in users if u["role"] == "user"]
    tot_c = sum(u["correct"] for u in users)
    tot_a = sum(u["attended"] for u in users)
    totals = {"accounts": len(users), "learners": len(learners), "admins": len(users) - len(learners),
              "active_today": sum(u["last_active"] == today for u in learners),
              "active_7d": sum(bool(u["last_active"]) and u["last_active"] >= today - timedelta(days=6) for u in learners),
              "new_week": sum(bool(u["created"]) and u["created"].date() >= today - timedelta(days=6) for u in learners),
              "quizzes": sum(u["quizzes"] for u in users), "accuracy": tot_c / tot_a * 100 if tot_a else 0.0}
    days30 = [today - timedelta(days=29 - i) for i in range(30)]
    activity = [{"day": d, "quizzes": d_quiz.get(d, 0), "active": len(d_users.get(d, ())), "questions": d_qs.get(d, 0)} for d in days30]
    return {"users": users, "totals": totals, "activity": activity, "errors": errors, "loaded_at": iso_now()}


@st.cache_data(ttl=300, show_spinner="Crunching answer history…")
def admin_answer_stats():
    try:  # fast path: the optional question_stats SQL view (see supabase_setup.sql)
        rows = fetch_all("question_stats", "question_id,attempts,misses", order=("question_id", False))
        return {"rows": [(r["question_id"], int(r["attempts"]), int(r["misses"])) for r in rows], "source": "view"}
    except Exception:
        pass
    agg = defaultdict(lambda: [0, 0])
    for r in fetch_all("answers", "question_id,is_correct,skipped", order=("id", False)):
        if r.get("skipped"):
            continue
        a = agg[r["question_id"]]
        a[0] += 1
        a[1] += 0 if r.get("is_correct") else 1
    return {"rows": [(k, v[0], v[1]) for k, v in agg.items()], "source": "scan"}


def _league_row(uid):
    r = rows_of(supabase.table("league_progress").select("qp,lifetime_qp,league_index").eq("user_id", uid).limit(1).execute())
    r = r[0] if r else {}
    return {"qp": int(r.get("qp") or 0), "lifetime_qp": int(r.get("lifetime_qp") or 0), "league_index": int(r.get("league_index") or 0)}


def wipe_user_data(uid, keep_league=False):
    """Delete a learner's study data. Answers are removed via attempt ids so it works with any answers schema."""
    errs = []
    try:
        ids = [r["id"] for r in fetch_all("quiz_attempts", "id", eq={"user_id": uid})]
    except Exception as exc:
        ids = []
        errs.append(f"quiz_attempts: {err_text(exc)}")
    for part in chunked(ids, 100):
        try:
            supabase.table("answers").delete().in_("attempt_id", part).execute()
        except Exception as exc:
            if not is_missing_table(exc):
                errs.append(f"answers: {err_text(exc)}")
    tables = ["answers", "quiz_attempts", "mistakes", "bookmarks", "daily_quiz_results", "daily_activity", "achievements"]
    if not keep_league:
        tables += ["task_completions", "league_progress"]
    for t in tables:
        try:
            supabase.table(t).delete().eq("user_id", uid).execute()
        except Exception as exc:
            if not is_missing_table(exc) and not missing_columns(err_text(exc)):
                errs.append(f"{t}: {err_text(exc)}")
    try:
        update_rows("profiles", {"streak_override": None}, id=uid)
    except Exception:
        pass
    return errs


def _flash(kind, msg):
    st.session_state["_admin_flash"] = (kind, msg)


def _admin_changed(uid=None):
    admin_model.clear()
    ranking_data.clear()
    if uid and uid == st.session_state.get("_uid"):
        snapshot(force=True)
    st.session_state.pop("adm_hist", None)


def _pill_role(u):
    if u["disabled"]:
        return pill("Disabled", "bad")
    if u["role"] in {"admin", "main_admin"}:
        return pill(u["role"].replace("_", " ").title(), "warn")
    return pill("Learner", "good")


# ---------------- Overview ----------------
def admin_overview(m):
    t = m["totals"]
    cols = st.columns(6)
    for c, (l, v, f) in zip(cols, [("Learners", t["learners"], f"{t['admins']} admin accounts"), ("Active today", t["active_today"], "Studied today"),
                                   ("Active 7 days", t["active_7d"], "Studied this week"), ("New this week", t["new_week"], "Joined in 7 days"),
                                   ("Quizzes", f"{t['quizzes']:,}", "All time"), ("Accuracy", f"{t['accuracy']:.0f}%", "Across everyone")]):
        with c:
            metric(l, v, f)
    c1, c2 = st.columns([1.7, 1], gap="large")
    with c1:
        section("Activity", "Last 30 days")
        act = m["activity"]
        fig = go.Figure()
        fig.add_bar(x=[f"{a['day']:%d %b}" for a in act], y=[a["quizzes"] for a in act], name="Quizzes", marker_color=theme()["accent_soft"])
        fig.add_scatter(x=[f"{a['day']:%d %b}" for a in act], y=[a["active"] for a in act], name="Active learners", mode="lines+markers",
                        line=dict(color=theme()["accent_dark"], width=3))
        plot(fig_style(fig, 290, legend=True), "adm_activity")
    with c2:
        section("League distribution")
        dist = Counter(u["league"].split()[0] for u in m["users"] if u["role"] == "user")
        tiers = list(LEAGUE_ICONS)
        fig = go.Figure(go.Bar(x=tiers, y=[dist.get(x, 0) for x in tiers], marker_color=theme()["accent"]))
        plot(fig_style(fig, 290), "adm_leagues")
    today = today_local()
    learners = [u for u in m["users"] if u["role"] == "user"]
    a1, a2 = st.columns(2, gap="large")
    with a1:
        section("Top learners", "By lifetime QP")
        top = sorted(learners, key=lambda u: -u["lifetime"])[:6]
        md(html_table(["Learner", "League", "Lifetime QP", "Streak"], [[Raw(f"<b>{esc(u['public_id'] or u['name'])}</b>"), u["league"], f"{u['lifetime']:,}", f"{u['streak']} 🔥"] for u in top]) if top else "<div class='notice'>No learners yet.</div>")
    with a2:
        section("Needs attention", "Inactive 7+ days or low accuracy")
        risk = [u for u in learners if not u["disabled"] and ((u["last_active"] and (today - u["last_active"]).days >= 7) or (u["attended"] >= 20 and u["accuracy"] < 50))]
        risk.sort(key=lambda u: (u["last_active"] or date.min))
        md(html_table(["Learner", "Last active", "Accuracy"], [[Raw(f"<b>{esc(u['public_id'] or u['name'])}</b><div class='sub'>{esc(u['email'])}</div>"),
                                                                 f"{u['last_active']:%d %b}" if u["last_active"] else "Never", f"{u['accuracy']:.0f}%"] for u in risk[:8]]) if risk else "<div class='notice'>Everyone is on track. 🎉</div>")


# ---------------- Users ----------------
def admin_users_tab(m):
    users = m["users"]
    c1, c2, c3, c4 = st.columns([2.2, 1, 1.2, 1.2])
    q = c1.text_input("Search", placeholder="Search name, email or User ID…", key="adm_search", label_visibility="collapsed").strip().lower()
    role_f = c2.selectbox("Role", ["All roles", "Learners", "Admins"], key="adm_role", label_visibility="collapsed")
    stat_f = c3.selectbox("Status", ["Any status", "Active (7d)", "Inactive 7d+", "Never studied", "Disabled"], key="adm_status", label_visibility="collapsed")
    sort_f = c4.selectbox("Sort", ["Last active", "Name", "Most quizzes", "Highest accuracy", "Highest QP", "Longest streak"], key="adm_sort", label_visibility="collapsed")
    today = today_local()
    rows = users
    if q:
        rows = [u for u in rows if q in u["email"] or q in u["name"].lower() or q in u["public_id"].lower()]
    if role_f == "Learners":
        rows = [u for u in rows if u["role"] == "user"]
    elif role_f == "Admins":
        rows = [u for u in rows if u["role"] != "user"]
    if stat_f == "Active (7d)":
        rows = [u for u in rows if u["last_active"] and (today - u["last_active"]).days <= 6]
    elif stat_f == "Inactive 7d+":
        rows = [u for u in rows if u["last_active"] and (today - u["last_active"]).days >= 7]
    elif stat_f == "Never studied":
        rows = [u for u in rows if not u["last_active"]]
    elif stat_f == "Disabled":
        rows = [u for u in rows if u["disabled"]]
    keyf = {"Last active": lambda u: -(u["last_active"] or date.min).toordinal(), "Name": lambda u: u["name"].lower(),
            "Most quizzes": lambda u: -u["quizzes"], "Highest accuracy": lambda u: -u["accuracy"],
            "Highest QP": lambda u: -u["lifetime"], "Longest streak": lambda u: -u["streak"]}[sort_f]
    rows = sorted(rows, key=keyf)
    st.caption(f"{plural(len(rows), 'account')} match · showing up to 200")
    body = [[i + 1, Raw(f"<b>{esc(u['name'] or '—')}</b><div class='sub'>{esc(u['email'])}</div>"), u["public_id"] or "—", u["league"],
             f"{u['streak']} 🔥" + (" ✎" if u["override"] is not None else ""), _pill_role(u), u["quizzes"], f"{u['accuracy']:.0f}%",
             u["mistakes"], f"{u['last_active']:%d %b %Y}" if u["last_active"] else "Never", fmt_dt(u["created"], "%d %b %Y")]
            for i, u in enumerate(rows[:200])]
    if body:
        md(html_table(["#", "Learner", "User ID", "League", "Streak", "Status", "Quizzes", "Accuracy", "Mistakes", "Last active", "Joined"], body, 980))
    else:
        st.info("No accounts match these filters.")
    df_ = pd.DataFrame([{"name": u["name"], "email": u["email"], "user_id": u["public_id"], "role": u["role"], "league": u["league"], "lifetime_qp": u["lifetime"],
                         "streak": u["streak"], "quizzes": u["quizzes"], "accuracy": round(u["accuracy"], 1), "mistakes": u["mistakes"],
                         "disabled": u["disabled"], "last_active": u["last_active"]} for u in rows])
    st.download_button("⬇️ Export these users (CSV)", df_.to_csv(index=False).encode("utf-8"), "users.csv", "text/csv", key="adm_export")
    st.caption("✎ = streak set manually by an admin.")


# ---------------- Manage one learner ----------------
def admin_manage_tab(m):
    users = m["users"]
    if not users:
        st.info("No accounts yet.")
        return
    by_email = {u["email"]: u for u in users}
    options = sorted(by_email, key=lambda e: (by_email[e]["name"] or e).lower())
    if st.session_state.get("adm_target") not in by_email:
        st.session_state["adm_target"] = options[0]
    target = st.selectbox("Choose an account", options, key="adm_target",
                          format_func=lambda e: f"{by_email[e]['name'] or 'User'} · {by_email[e]['public_id'] or 'no User ID'} · {e}")
    u, uid = by_email[target], by_email[target]["id"]
    protected = target in ALL_ADMIN_EMAILS
    qp_label = f"{u['qp']:,} QP"
    md(f"<div class='card'><div class='user-head'><div><div class='n'>{esc(u['name'] or 'User')}</div><div class='muted'>{esc(u['email'])} · User ID {esc(u['public_id'] or '—')}</div>"
       f"<div style='margin-top:.4rem'>{_pill_role(u)}{pill(u['league'])}{pill(qp_label)}</div></div>"
       f"<div class='muted' style='text-align:right'>Joined {fmt_dt(u['created'], '%d %b %Y')}<br>Last seen {fmt_dt(u['last_seen'])}</div></div></div>")
    c = st.columns(5)
    for col, (l, v, f) in zip(c, [("Quizzes", u["quizzes"], "Completed"), ("Accuracy", f"{u['accuracy']:.0f}%", f"{u['correct']}/{u['attended']}"),
                                  ("Streak", f"{u['streak']} 🔥", "Manual" if u["override"] is not None else "Automatic"),
                                  ("Mistakes", u["mistakes"], "In review bank"), ("Lifetime QP", f"{u['lifetime']:,}", u["league"])]):
        with col:
            metric(l, v, f)

    t1, t2, t3, t4, t5 = st.tabs(["📊 Recent quizzes", "⭐ QP & league", "🔥 Streak", "🪪 User ID", "🔐 Account"])
    with t1:
        if st.button("Load recent quizzes", key="adm_load_hist"):
            try:
                st.session_state["adm_hist"] = (uid, rows_of(supabase.table("quiz_attempts").select("quiz_type,completed_at,total_questions,correct,incorrect,skipped")
                                                             .eq("user_id", uid).order("completed_at", desc=True).limit(25).execute()))
            except Exception as exc:
                st.error(err_text(exc))
        hist = st.session_state.get("adm_hist")
        if hist and hist[0] == uid:
            md(html_table(["When", "Type", "Questions", "Correct", "Wrong", "Skipped"], [[fmt_dt(r["completed_at"]), r["quiz_type"], r["total_questions"], r["correct"], r["incorrect"], r["skipped"]] for r in hist[1]], 560) if hist[1] else "<div class='notice'>No quizzes yet.</div>")
    with t2:
        f1, f2 = st.columns(2, gap="large")
        with f1, st.form("adm_qp_form"):
            st.markdown("**Add or remove QP**")
            delta = st.number_input("QP change (negative removes)", -5000, 5000, 0, 5)
            go_ = st.form_submit_button("Apply QP change", type="primary", **FORM_W)
        if go_ and delta:
            try:
                new, start = apply_qp(_league_row(uid), int(delta))
                save_league(uid, new)
                audit("qp_adjust", target, f"{int(delta):+d} QP -> {LEAGUE_LADDER[new['league_index']][0]} {new['qp']} QP")
                _admin_changed(uid)
                _flash("success", f"Applied {int(delta):+d} QP. Now {LEAGUE_LADDER[new['league_index']][0]} with {new['qp']} QP.")
                rerun_fragment()
            except Exception as exc:
                st.error(err_text(exc))
        with f2, st.form("adm_league_form"):
            st.markdown("**Set league directly**")
            lname = st.selectbox("League", LEAGUE_NAMES, index=u["league_index"])
            lqp = st.number_input("QP inside that league", 0, 100000, int(u["qp"]), 5)
            go2 = st.form_submit_button("Set league", **FORM_W)
        if go2:
            try:
                cur = _league_row(uid)
                save_league(uid, {"qp": int(lqp), "lifetime_qp": max(cur["lifetime_qp"], int(lqp)), "league_index": LEAGUE_NAMES.index(lname)})
                audit("league_set", target, f"{lname}, {int(lqp)} QP")
                _admin_changed(uid)
                _flash("success", f"League set to {lname}.")
                rerun_fragment()
            except Exception as exc:
                st.error(err_text(exc))
    with t3:
        with st.form("adm_streak_form"):
            sval = st.number_input("Current streak (days)", 0, 9999, int(u["streak"]), 1)
            s1, s2 = st.columns(2)
            save_s = s1.form_submit_button("Save streak", type="primary", **FORM_W)
            auto_s = s2.form_submit_button("Follow quiz history (auto)", **FORM_W)
        if save_s or auto_s:
            try:
                update_rows("profiles", {"streak_override": int(sval) if save_s else None}, strip=False, id=uid)
                audit("streak_override" if save_s else "streak_auto", target, str(int(sval)) if save_s else "auto")
                _admin_changed(uid)
                _flash("success", f"Streak set to {int(sval)} days." if save_s else "Streak now follows quiz history.")
                rerun_fragment()
            except Exception as exc:
                st.error(f"{err_text(exc)} — run supabase_setup.sql to add the missing column.")
    with t4:
        st.caption("Admins can change any User ID without the 72-hour cooldown.")
        with st.form("adm_uid_form"):
            new_id = st.text_input("New User ID", value=u["public_id"], max_chars=20)
            b1, b2, b3 = st.columns(3)
            set_id = b1.form_submit_button("Save User ID", type="primary", **FORM_W)
            clear_id = b2.form_submit_button("Force re-create", help="Clears the ID; the learner must pick a new one at next login", **FORM_W)
            reset_cd = b3.form_submit_button("Reset cooldown", **FORM_W)
        try:
            if set_id:
                ok, res = set_public_user_id(uid, new_id, bypass_cooldown=True)
                if not ok:
                    st.error(res)
                else:
                    audit("user_id_set", target, res)
                    _admin_changed(uid)
                    _flash("success", f"User ID is now {res}.")
                    rerun_fragment()
            if clear_id:
                update_rows("profiles", {"public_user_id": None}, strip=False, id=uid)
                audit("user_id_cleared", target)
                _admin_changed(uid)
                _flash("success", "User ID cleared. The learner will be asked to create a new one.")
                rerun_fragment()
            if reset_cd:
                update_rows("profiles", {"public_user_id_changed_at": None}, strip=False, id=uid)
                audit("user_id_cooldown_reset", target)
                _admin_changed(uid)
                _flash("success", "Cooldown removed.")
                rerun_fragment()
        except Exception as exc:
            st.error(err_text(exc))
    with t5:
        if protected:
            st.info("Admin accounts are protected: they cannot be disabled, reset or deleted from the app.")
        else:
            if u["disabled"]:
                st.warning("This account is disabled and cannot open the app.")
            if st.button("✅ Enable account" if u["disabled"] else "⛔ Disable account", key="adm_toggle_disable"):
                try:
                    update_rows("profiles", {"disabled": not u["disabled"]}, strip=False, id=uid)
                    audit("account_enabled" if u["disabled"] else "account_disabled", target)
                    _admin_changed(uid)
                    _flash("success", "Account enabled." if u["disabled"] else "Account disabled.")
                    rerun_fragment()
                except Exception as exc:
                    st.error(f"{err_text(exc)} — run supabase_setup.sql to add the 'disabled' column.")
            with st.form("adm_reset_form"):
                st.markdown("**Reset study progress**")
                also_qp = st.checkbox("Also reset QP, league and mission history", value=True)
                sure = st.checkbox("I understand this cannot be undone")
                go_r = st.form_submit_button("Reset progress", **FORM_W)
            if go_r:
                if not sure:
                    st.warning("Tick the confirmation box first.")
                else:
                    errs = wipe_user_data(uid, keep_league=not also_qp)
                    audit("progress_reset", target, "with QP" if also_qp else "study data only")
                    _admin_changed(uid)
                    _flash("warning" if errs else "success", ("Reset finished with warnings: " + "; ".join(errs)) if errs else "Progress reset.")
                    rerun_fragment()
            with st.form("adm_delete_form"):
                st.markdown("**Delete user**")
                st.caption("Removes the profile and all study data from this app. The Google account itself is untouched; signing in again creates a fresh profile.")
                typed = st.text_input("Type the account email to confirm")
                go_d = st.form_submit_button("Delete permanently", **FORM_W)
            if go_d:
                if typed.strip().lower() != target:
                    st.warning("The email does not match.")
                else:
                    errs = wipe_user_data(uid)
                    try:
                        supabase.table("profiles").delete().eq("id", uid).execute()
                        audit("user_deleted", target)
                        _admin_changed(uid)
                        _flash("success", f"{target} was deleted.")
                        st.session_state.pop("adm_target", None)
                        rerun_fragment()
                    except Exception as exc:
                        st.error(f"Profile could not be deleted: {err_text(exc)} {' | '.join(errs)}")


# ---------------- Questions ----------------
def admin_questions_tab():
    per_day = Counter(q["date"] for q in QUESTIONS)
    span = (DATES[-1] - DATES[0]).days + 1
    gaps = [DATES[0] + timedelta(days=i) for i in range(span) if (DATES[0] + timedelta(days=i)) not in per_day]
    c = st.columns(4)
    for col, (l, v, f) in zip(c, [("Questions", f"{len(QUESTIONS):,}", "Valid rows"), ("Study days", len(DATES), f"{DATES[0]:%d %b %Y} → {DATES[-1]:%d %b %Y}"),
                                  ("Sheet problems", len(BANK["issues"]), "Rows skipped or suspicious"), ("Missing dates", len(gaps), "Days with no questions")]):
        with col:
            metric(l, v, f)
    b1, b2 = st.columns([1, 3])
    if b1.button("🔄 Reload sheet now", key="adm_reload_sheet"):
        load_question_bank.clear()
        st.rerun()
    b2.caption(f"Sheet last loaded {fmt_dt(BANK['loaded_at'])}. It refreshes itself every 5 minutes.")
    if BANK["issues"]:
        with st.expander(f"⚠️ {len(BANK['issues'])} problems found in the Google Sheet", expanded=True):
            for i in BANK["issues"][:60]:
                st.write("• " + i)
    fig = go.Figure(go.Bar(x=list(sorted(per_day)), y=[per_day[d] for d in sorted(per_day)], marker_color=theme()["accent"]))
    section("Questions per study day")
    plot(fig_style(fig, 240), "adm_coverage")
    if gaps:
        st.caption("Dates without questions: " + ", ".join(f"{d:%d %b}" for d in gaps[:30]) + (" …" if len(gaps) > 30 else ""))
    section("Difficult questions", "Highest miss rate (min. 3 attempts)")
    try:
        stats = admin_answer_stats()
        hard = sorted([r for r in stats["rows"] if r[1] >= 3], key=lambda r: (-(r[2] / r[1]), -r[1]))[:20]
        rows = []
        for qid, att, miss in hard:
            q = lookup_question(qid)
            rows.append([(q["question"] if q else str(qid).split("|", 1)[-1])[:140], att, miss, f"{miss / att * 100:.0f}%"])
        md(html_table(["Question", "Attempts", "Misses", "Miss rate"], rows, 640) if rows else "<div class='notice'>Not enough answers yet.</div>")
        if stats["source"] == "scan":
            st.caption("Tip: create the question_stats view from supabase_setup.sql to make this instant on large datasets.")
    except Exception as exc:
        st.warning(f"Could not load answer history: {err_text(exc)}")


# ---------------- Announcements / audit / system ----------------
def admin_announcements_tab():
    with st.form("adm_ann_form", clear_on_submit=True):
        msg = st.text_area("Message shown on every learner's dashboard", max_chars=300, height=90)
        level = st.selectbox("Style", ["info", "warning", "alert"])
        go_ = st.form_submit_button("Publish announcement", type="primary", **FORM_W)
    if go_ and msg.strip():
        try:
            insert_rows("announcements", [{"message": msg.strip(), "level": level, "active": True,
                                           "created_by": st.session_state["auth_user"]["email"], "created_at": iso_now()}])
            audit("announcement_published", "", msg.strip()[:120])
            active_announcements.clear()
            _flash("success", "Announcement published.")
            rerun_fragment()
        except Exception as exc:
            st.error(f"{err_text(exc)} — run supabase_setup.sql to create the announcements table." if is_missing_table(exc) else err_text(exc))
    try:
        rows = rows_of(supabase.table("announcements").select("*").order("created_at", desc=True).limit(20).execute())
    except Exception:
        rows = []
    for r in rows:
        c1, c2, c3 = st.columns([6, 1.2, 1.2], vertical_alignment="center")
        c1.markdown(H(f"<div class='notice {'' if r.get('active') else 'warn'}'>{pill('Live', 'good') if r.get('active') else pill('Hidden', 'off')} {esc(r.get('message'))}"
                      f"<div class='task-sub'>{fmt_dt(r.get('created_at'))}</div></div>"), unsafe_allow_html=True)
        if c2.button("Hide" if r.get("active") else "Show", key=f"ann_t_{r['id']}", **BTN_W):
            update_rows("announcements", {"active": not r.get("active")}, id=r["id"])
            active_announcements.clear()
            rerun_fragment()
        if c3.button("Delete", key=f"ann_d_{r['id']}", **BTN_W):
            supabase.table("announcements").delete().eq("id", r["id"]).execute()
            audit("announcement_deleted", "", str(r.get("message"))[:120])
            active_announcements.clear()
            rerun_fragment()


def admin_audit_tab():
    try:
        rows = rows_of(supabase.table("admin_audit").select("*").order("created_at", desc=True).limit(100).execute())
    except Exception as exc:
        st.info("The audit log needs the admin_audit table — run supabase_setup.sql." if is_missing_table(exc) else err_text(exc))
        return
    if not rows:
        st.info("No admin actions recorded yet.")
        return
    md(html_table(["When", "Admin", "Action", "Target", "Details"], [[fmt_dt(r.get("created_at")), r.get("admin_email"), r.get("action"), r.get("target_email") or "—", r.get("details") or ""] for r in rows], 800))


HEALTH_CHECKS = {
    "profiles": "id,email,name,role,public_user_id,public_user_id_changed_at,streak_override,disabled,last_seen,created_at",
    "quiz_attempts": "id,user_id,quiz_type,started_at,completed_at,total_questions,correct,incorrect,skipped,accuracy",
    "answers": "id,attempt_id,question_id,is_correct,skipped",
    "mistakes": "id,user_id,question_id,mistake_count,last_answer",
    "league_progress": "user_id,qp,lifetime_qp,league_index,updated_at",
    "task_completions": "id,user_id,task_key,task_date,qp_awarded,completed_at",
    "daily_quiz_results": "id,user_id,quiz_date,total_questions,best_correct,attempts,completed,perfect,first_completed_at,last_completed_at",
    "bookmarks (optional)": "user_id,question_id",
    "announcements (optional)": "id,message,level,active,created_at",
    "admin_audit (optional)": "id,admin_email,action,target_email,created_at",
}


def admin_system_tab():
    c = st.columns(4)
    snap = st.session_state.get("_snap")
    for col, (l, v, f) in zip(c, [("Build", APP_BUILD.split("·")[0].strip(), APP_BUILD), ("Streamlit", st.__version__, "Runtime"),
                                  ("Timezone", str(cfg("app", "timezone", "Asia/Kolkata")), "Days & missions reset here"),
                                  ("My snapshot age", f"{int(time.time() - snap['at'])}s" if snap else "—", f"Refreshes after {SNAPSHOT_TTL}s")]):
        with col:
            metric(l, v, f)
    section("Database health check", "Reads one row from every table the app uses")
    if st.button("🩺 Run health check", key="adm_health", type="primary"):
        rows = []
        for table, cols in HEALTH_CHECKS.items():
            name = table.split(" ")[0]
            try:
                supabase.table(name).select(cols).limit(1).execute()
                rows.append([name, Raw(pill("OK", "good")), ""])
            except Exception as exc:
                optional = "optional" in table
                rows.append([name, Raw(pill("Optional - not set up" if optional and is_missing_table(exc) else "Problem", "warn" if optional else "bad")), err_text(exc)[:160]])
        md(html_table(["Table", "Status", "Details"], rows, 640))
        st.caption("Any problem row: run supabase_setup.sql in the Supabase SQL editor (it is safe to re-run).")
    section("Maintenance")
    m1, m2, m3 = st.columns(3)
    if m1.button("🧹 Clear all caches", key="adm_clear_caches", **BTN_W):
        st.cache_data.clear()
        load_question_bank.clear()
        _flash("success", "Caches cleared.")
        st.rerun()
    if m2.button("🔄 Refresh my snapshot", key="adm_snap", **BTN_W):
        snapshot(force=True)
        _flash("success", "Snapshot refreshed.")
        rerun_fragment()
    if m3.button("🪪 Clear learned schema", key="adm_schema", help="Forget which optional columns were missing", **BTN_W):
        _stripped_columns().clear()
        _flash("success", "Schema memory cleared.")
        rerun_fragment()
    errs = st.session_state.get("_db_errors")
    if errs:
        with st.expander("Recent database errors (this session)"):
            for k, v in errs.items():
                st.write(f"**{k}** — {v}")


ADMIN_SECTIONS = ["📊 Overview", "👥 Users", "🛠 Manage account", "❓ Questions", "📣 Announcements", "🧾 Audit log", "⚙️ System"]


@st.fragment
def admin_panel():
    if not is_admin(st.session_state["auth_user"]["email"]):
        st.error("You do not have permission to open the admin dashboard.")
        return
    md("<div class='admin-hero'><div class='eyebrow'>👑 Admin control room</div><h1>Run your study community</h1>"
       "<div class='muted'>Understand how learners are doing, manage accounts, keep the question bank healthy and talk to everyone at once.</div></div>")
    flash = st.session_state.pop("_admin_flash", None)
    if flash:
        getattr(st, flash[0])(flash[1])
    m = admin_model()
    if m["errors"]:
        st.warning("Some admin data could not be loaded: " + "; ".join(f"{k}: {v}" for k, v in m["errors"].items()))
    c1, c2 = st.columns([6, 1], vertical_alignment="center")
    with c1:
        sec = st.radio("Admin section", ADMIN_SECTIONS, horizontal=True, key="admin_nav", label_visibility="collapsed")
    with c2:
        if st.button("↻ Refresh", key="adm_refresh", help=f"Data loaded {fmt_dt(m['loaded_at'])}", **BTN_W):
            admin_model.clear()
            rerun_fragment()
    if sec == ADMIN_SECTIONS[0]:
        admin_overview(m)
    elif sec == ADMIN_SECTIONS[1]:
        admin_users_tab(m)
    elif sec == ADMIN_SECTIONS[2]:
        admin_manage_tab(m)
    elif sec == ADMIN_SECTIONS[3]:
        admin_questions_tab()
    elif sec == ADMIN_SECTIONS[4]:
        admin_announcements_tab()
    elif sec == ADMIN_SECTIONS[5]:
        admin_audit_tab()
    else:
        admin_system_tab()


def page_admin():
    admin_panel()


# =========================================================
# 15. APP BOOTSTRAP  (theme -> config checks -> sign-in -> data -> sidebar -> page)
# =========================================================
def _on_theme():
    label = st.session_state.get("theme_choice")
    if label in THEME_LABELS:
        st.session_state["theme"] = THEME_LABELS[label]
        try:
            st.query_params["theme"] = THEME_LABELS[label]
        except Exception:
            pass


def on_nav_change():
    q = st.session_state.get("quiz")
    if q and q.get("done"):
        st.session_state.pop("quiz", None)


def _sync_everything():
    load_question_bank.clear()
    ranking_data.clear()
    active_announcements.clear()
    admin_model.clear()
    st.session_state.pop("_daily_status", None)
    snapshot(force=True)


if "theme" not in st.session_state:
    _qp_theme = st.query_params.get("theme")
    st.session_state["theme"] = _qp_theme if _qp_theme in THEMES else "Sage"
inject_theme(THEMES[st.session_state["theme"]])

if supabase is None:
    st.error("Supabase is not configured for this deployment.")
    st.info("Add a [supabase] section with url and secret_key to your Streamlit secrets.")
    st.stop()
if not auth_is_configured():
    st.error("Google authentication is not configured for this deployment.")
    st.info("In Streamlit Cloud → Manage app → Settings → Secrets, configure [auth] with redirect_uri, cookie_secret, "
            "client_id, client_secret and server_metadata_url.")
    st.stop()

auth_user = get_auth_user()
if auth_user is None:
    hero("Current Affairs Study Studio", "Welcome back.",
         "Sign in with Google to keep your streak, accuracy, mistakes, league and quiz history tied to your own profile.")
    if st.button("Continue with Google", type="primary", **BTN_W):
        st.login()
    st.stop()
st.session_state["auth_user"] = auth_user
_email = auth_user["email"]

# ---- profile (once per login) ----
if st.session_state.get("_profile_email") != _email:
    _prof = sync_profile(auth_user)
    if not _prof:
        st.error("Your profile could not be loaded from the database. Please refresh in a moment.")
        st.json(st.session_state.get("_db_errors", {}))
        st.stop()
    for _k in ("_snap", "quiz", "_daily_status", "_ann_dismissed"):
        st.session_state.pop(_k, None)
    st.session_state.update({"_uid": str(_prof["id"]), "_profile_email": _email, "_ping_at": time.time()})

# ---- question bank ----
try:
    BANK, BANK_WARN = get_bank()
except Exception as exc:
    st.error("Could not load your Google Sheet.")
    st.exception(exc)
    st.stop()
QUESTIONS = BANK["records"]
if not QUESTIONS:
    st.error("No valid quiz rows were found in the Google Sheet.")
    for _i in BANK["issues"][:15]:
        st.write("• " + _i)
    st.stop()
Q_BY_KEY = {q["key"]: q for q in QUESTIONS}
Q_BY_STRIPPED = {f"{q['date'].isoformat()}|{q['question']}": q for q in QUESTIONS}
Q_BY_DATE = defaultdict(list)
for _q in QUESTIONS:
    Q_BY_DATE[_q["date"]].append(_q)
DATES = sorted(Q_BY_DATE)
LATEST_DATE = DATES[-1]

# ---- learner data ----
SNAP = snapshot()
if SNAP["profile"].get("disabled") and not is_admin(_email):
    hero("Account disabled", "This account is not active.", "Please contact an administrator if you think this is a mistake.")
    st.button("Sign out", on_click=st.logout)
    st.stop()
ping_last_seen()
if not str(SNAP["profile"].get("public_user_id") or "").strip():
    dialog_create_user_id()
    st.stop()


# ---- sidebar ----
def render_sidebar(snap):
    admin = is_admin(_email)
    stats, lg = stats_of(snap), league_of(snap)
    n_bank = len(mistake_bank(snap))
    pages = [P_DASH, P_DAILY, P_CUSTOM, P_REVIEW] + ([P_MARKS] if snap["bookmarks_ok"] else []) + [P_LEAGUE, P_RANK, P_PROGRESS] + ([P_ADMIN] if admin else [])
    if st.session_state.get("nav_page") not in pages:
        st.session_state["nav_page"] = P_DASH
    with st.sidebar:
        md("<div class='side-brand'>🌿 Current Affairs</div>")
        st.caption("A calm place to turn daily news into long-term memory.")
        st.radio("Navigate", pages, key="nav_page", label_visibility="collapsed", on_change=on_nav_change,
                 format_func=lambda p: f"{p}  ({n_bank})" if p == P_REVIEW and n_bank else p)
        st.markdown("### You")
        md(f"<div class='side-mini'>{emblem_html(lg['name'], True)}<div style='min-width:0'>"
           f"<div class='side-name'>{esc(snap['profile'].get('public_user_id') or _email)}</div>"
           f"<div class='side-sub'>{esc(lg['name'])} · {lg['qp']:,} QP</div></div></div>"
           f"<div class='bar'><div style='width:{lg['pct'] * 100:.0f}%'></div></div>"
           f"<div class='side-stats'><div class='side-stat'><b>{stats['current_streak']} 🔥</b><span>Streak</span></div>"
           f"<div class='side-stat'><b>{stats['accuracy']:.0f}%</b><span>Accuracy</span></div></div>")
        if st.button("✏️ Edit User ID", key="edit_uid_btn", **BTN_W):
            dialog_edit_user_id()
        st.markdown("### Appearance")
        cur_label = next(k for k, v in THEME_LABELS.items() if v == st.session_state["theme"])
        st.radio("Theme", list(THEME_LABELS), index=list(THEME_LABELS).index(cur_label), key="theme_choice",
                 label_visibility="collapsed", on_change=_on_theme, horizontal=True)
        st.markdown("### Library")
        st.caption(f"{len(QUESTIONS):,} questions · {len(DATES):,} study days · latest {LATEST_DATE:%d %b %Y}")
        role_label = {"main_admin": "👑 Main admin", "admin": "🛠️ Admin", "user": "👤 Learner"}[user_role(_email)]
        st.caption(f"{auth_user['name']} · {role_label}")
        errs = st.session_state.get("_db_errors", {})
        if any(k in errs for k in ("Profile", "Attempts", "Mistakes", "League", "Completions")):
            st.warning("Some of your data could not be loaded. Try Sync data.")
        c1, c2 = st.columns(2)
        c1.button("🔄 Sync data", key="sync_all", on_click=_sync_everything, help="Reload the Google Sheet and your data", **BTN_W)
        c2.button("Sign out", key="sign_out", on_click=st.logout, **BTN_W)
        if admin:
            st.caption(f"Build: {APP_BUILD}")


render_sidebar(SNAP)

# ---- router ----
PAGE_FUNCS = {P_DASH: page_dashboard, P_DAILY: page_daily, P_CUSTOM: page_custom, P_REVIEW: page_review,
              P_MARKS: page_bookmarks, P_LEAGUE: page_league, P_RANK: page_rankings, P_PROGRESS: page_progress,
              P_ADMIN: page_admin}
_page = st.session_state["nav_page"]
if BANK_WARN and is_admin(_email):
    st.warning(f"Google Sheet is temporarily unreachable; showing the last loaded copy. ({BANK_WARN})")
_quiz = st.session_state.get("quiz")
if _quiz and _quiz["page"] == _page:
    quiz_engine()
else:
    PAGE_FUNCS[_page]()
