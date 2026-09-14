import random
import calendar
import re
import html
import requests
from datetime import date, timedelta, datetime

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from supabase import create_client

APP_BUILD = "SUPABASE-FAST-UI-ADAPTIVE-CUSTOM-TEST-2026-09-14"

# =========================================================
# PAGE + APP CONFIG
# =========================================================
st.set_page_config(
    page_title="Current Affairs • Study Dashboard",
    page_icon="🌿",
    layout="wide",
    initial_sidebar_state="expanded",
)

SHEET_URL = "https://docs.google.com/spreadsheets/d/1hFoQMwnPugw8A6El69rkU0kNT4NDYJt4msOgnNv-f8Q/export?format=csv"

# =========================================================
# AUTHENTICATION + ROLE CONFIG
# =========================================================
# These are the owner/admin accounts requested for this app.
# For deployment, you can move them into st.secrets later.
MAIN_ADMIN_EMAIL = "arjunrose2005@gmail.com"
ADMIN_EMAILS = {
    "jsujarose10675@gmail.com",
    "abisreerose2007@gmail.com",
    "aiswaryarose2009@gmail.com",
}
ALL_ADMIN_EMAILS = {MAIN_ADMIN_EMAIL, *ADMIN_EMAILS}
SUPABASE_URL = ""
SUPABASE_SECRET_KEY = ""
try:
    _secrets = st.secrets.to_dict()
    _supabase_cfg = _secrets.get("supabase", {}) if isinstance(_secrets, dict) else {}
    SUPABASE_URL = str(_supabase_cfg.get("url", "")).strip()
    SUPABASE_SECRET_KEY = str(_supabase_cfg.get("secret_key", "")).strip()
except Exception:
    pass

SUPABASE_CONFIGURED = bool(SUPABASE_URL and SUPABASE_SECRET_KEY)
supabase = create_client(SUPABASE_URL, SUPABASE_SECRET_KEY) if SUPABASE_CONFIGURED else None



# =========================================================
# AUTH HELPERS
# =========================================================
def auth_is_configured():
    """Check whether Streamlit's default OIDC provider is configured.

    Streamlit exposes secrets sections through a mapping-like object, so do
    not require the [auth] section to be a literal built-in dict.
    """
    try:
        auth = st.secrets.get("auth")
        if auth is None:
            return False

        required = ("client_id", "client_secret", "server_metadata_url")
        return all(str(auth.get(key, "")).strip() for key in required)
    except Exception:
        return False


def get_auth_user():
    """
    Return the authenticated Google user.

    IMPORTANT:
    There is deliberately NO Demo User fallback. A real Google identity
    is required before any study data or Supabase profile is accessed.
    """
    if not auth_is_configured():
        return None

    try:
        user_data = st.user.to_dict()
    except Exception:
        return None

    # st.user only has is_logged_in when OIDC is configured.
    if not bool(user_data.get("is_logged_in", False)):
        return None

    email = str(user_data.get("email", "") or "").strip().lower()
    name = str(
        user_data.get("name", "")
        or (email.split("@")[0] if email else "User")
    ).strip()
    picture = str(user_data.get("picture", "") or "").strip()

    if not email:
        return None

    return {
        "id": str(user_data.get("sub", "") or email),
        "email": email,
        "name": name,
        "picture": picture,
    }


def user_role(email):
    email = str(email or "").strip().lower()
    if email == MAIN_ADMIN_EMAIL:
        return "main_admin"
    if email in ADMIN_EMAILS:
        return "admin"
    return "user"


def is_admin(email):
    return str(email or "").strip().lower() in ALL_ADMIN_EMAILS

def _sb_data(response):
    return getattr(response, "data", None) or []


def _sb_error(exc):
    return str(getattr(exc, "message", None) or getattr(exc, "details", None) or exc)


def _sb_schema():
    """Best-effort discovery of the PostgREST table schema."""
    if not supabase or not SUPABASE_URL:
        return {}
    cached = st.session_state.get("_sb_schema_cache")
    if isinstance(cached, dict):
        return cached
    try:
        headers = {"apikey": SUPABASE_SECRET_KEY, "Authorization": f"Bearer {SUPABASE_SECRET_KEY}"}
        r = requests.get(SUPABASE_URL.rstrip("/") + "/rest/v1/", headers=headers, timeout=8)
        r.raise_for_status()
        spec = r.json()
        result = {}
        defs = spec.get("definitions", {}) if isinstance(spec, dict) else {}
        for name, definition in defs.items():
            props = definition.get("properties", {}) if isinstance(definition, dict) else {}
            if props:
                result[name] = set(props.keys())
        # Some PostgREST versions expose tables through paths instead.
        for path, item in (spec.get("paths", {}) or {}).items():
            if not isinstance(path, str) or not path.startswith("/"):
                continue
            table = path.strip("/")
            if not table or table in result:
                continue
            getinfo = item.get("get", {}) if isinstance(item, dict) else {}
            schema = getinfo.get("responses", {}).get("200", {}).get("schema", {})
            props = schema.get("items", {}).get("properties", {}) if isinstance(schema, dict) else {}
            if props:
                result[table] = set(props.keys())
        st.session_state["_sb_schema_cache"] = result
        return result
    except Exception:
        st.session_state["_sb_schema_cache"] = {}
        return {}


def _sb_columns(table):
    cols = _sb_schema().get(table)
    return set(cols) if cols else None


def _sb_identity_column(table):
    """Return the best email-like identity column for legacy tables."""
    cols = _sb_columns(table)
    if not cols:
        return "email"
    for c in ("email", "user_email", "owner_email"):
        if c in cols:
            return c
    return None


def _sb_profile_id(email):
    """Resolve and cache the real profiles.id UUID for this signed-in user."""
    email = str(email or "").strip().lower()
    if not supabase or not email:
        return None

    cached_email = str(st.session_state.get("_supabase_profile_email", "")).strip().lower()
    cached_id = st.session_state.get("_supabase_user_id")
    if cached_email == email and cached_id:
        return str(cached_id)

    try:
        rows = _sb_data(
            supabase.table("profiles")
            .select("id,email")
            .eq("email", email)
            .limit(1)
            .execute()
        )
        if not rows:
            auth_user = st.session_state.get("auth_user") or {"email": email, "name": email.split("@")[0]}
            db_upsert_user(auth_user)
            rows = _sb_data(
                supabase.table("profiles")
                .select("id,email")
                .eq("email", email)
                .limit(1)
                .execute()
            )
        if not rows or not rows[0].get("id"):
            st.session_state["supabase_profile_error"] = f"No profile UUID found for {email}"
            return None

        profile_id = str(rows[0]["id"])
        st.session_state["_supabase_user_id"] = profile_id
        st.session_state["_supabase_profile_email"] = email
        return profile_id
    except Exception as exc:
        st.session_state["supabase_profile_error"] = _sb_error(exc)
        return None


def _sb_user_rows(table, email=None, order=None):
    """Read rows belonging to a user.

    Prefer the real Supabase profile UUID whenever possible.  The OpenAPI
    schema endpoint is not reliable across all Supabase/PostgREST versions,
    so user_id detection must not depend on schema discovery succeeding.
    """
    q = supabase.table(table).select("*")
    if email is not None:
        email = str(email or "").strip().lower()

        # First try the current schema used by the app: user_id -> profiles.id.
        user_id = _sb_profile_id(email)
        if user_id:
            try:
                q_user = supabase.table(table).select("*").eq("user_id", user_id)
                if order:
                    try:
                        q_user = q_user.order(order[0], desc=order[1])
                    except Exception:
                        pass
                return _sb_data(q_user.execute())
            except Exception:
                # Table may be a legacy email-owned table; fall through below.
                pass

        # Legacy compatibility: email/user_email/owner_email.
        identity = _sb_identity_column(table)
        if identity is None:
            # Last-resort direct email query for known legacy deployments.
            identity = "email"
        q = q.eq(identity, email)

    if order:
        col = order[0]
        try:
            q = q.order(col, desc=order[1])
        except Exception:
            pass
    return _sb_data(q.execute())


def _sb_rows(table, email=None, order=None):
    """Compatibility wrapper used throughout the app."""
    if email is not None:
        return _sb_user_rows(table, email, order=order)
    q = supabase.table(table).select("*")
    if order:
        col = order[0]
        cols = _sb_columns(table)
        if cols is None or col in cols:
            try:
                q = q.order(col, desc=order[1])
            except Exception:
                pass
    return _sb_data(q.execute())


@st.cache_data(ttl=60, show_spinner=False)
def _cached_sb_all_rows(table):
    return _sb_rows(table)


@st.cache_data(ttl=30, show_spinner=False)
def _cached_sb_user_rows(table, email):
    if not supabase:
        return []
    return _sb_user_rows(table, email)


def _clear_supabase_read_cache():
    try:
        _cached_sb_user_rows.clear()
    except Exception:
        pass
    try:
        _cached_sb_all_rows.clear()
    except Exception:
        pass


def _invalidate_progress_cache():
    st.session_state.pop("_progress_cache", None)
    st.session_state.pop("_progress_cache_at", None)

def _cached_user_attempts(user_id):
    """Session-local cache for the user's attempts; avoids a Supabase request on every widget rerun."""
    now = datetime.now().timestamp()
    cache_at = st.session_state.get("_attempt_rows_cache_at", 0.0)
    cache = st.session_state.get("_attempt_rows_cache")
    if cache is not None and now - cache_at < 8:
        return cache
    try:
        rows = _sb_data(
            supabase.table("quiz_attempts")
            .select("id,user_id,quiz_type,started_at,completed_at,total_questions,correct,incorrect,skipped,accuracy")
            .eq("user_id", user_id)
            .order("completed_at", desc=False)
            .execute()
        )
        st.session_state["_attempt_rows_cache"] = rows
        st.session_state["_attempt_rows_cache_at"] = now
        return rows
    except Exception:
        return cache or []

def _cached_user_mistake_rows(user_id):
    """Session-local cache for compact mistakes."""
    now = datetime.now().timestamp()
    cache_at = st.session_state.get("_mistake_rows_cache_at", 0.0)
    cache = st.session_state.get("_mistake_rows_cache")
    if cache is not None and now - cache_at < 8:
        return cache
    try:
        rows = _sb_data(
            supabase.table("mistakes")
            .select("id,user_id,question_id,mistake_count,last_answer")
            .eq("user_id", user_id)
            .execute()
        )
        st.session_state["_mistake_rows_cache"] = rows
        st.session_state["_mistake_rows_cache_at"] = now
        return rows
    except Exception:
        return cache or []


def _filter_payload_to_schema(table, payload):
    cols = _sb_columns(table)
    if not cols:
        return dict(payload)
    return {k: v for k, v in payload.items() if k in cols}


def _payload_for_user(table, email, payload):
    """Attach user ownership even when PostgREST schema discovery is unavailable."""
    clean = dict(payload)
    email = str(email or "").strip().lower()

    # Always resolve the real profile UUID first.  Current tables such as
    # mistakes use user_id as their ownership field.
    user_id = _sb_profile_id(email)
    if user_id:
        clean["user_id"] = user_id
    else:
        cols = _sb_columns(table)
        if not cols or "email" in cols:
            clean["email"] = email
        elif "user_email" in cols:
            clean["user_email"] = email
        elif "owner_email" in cols:
            clean["owner_email"] = email

    return clean


def _strip_missing_columns(payload, error_text):
    text = str(error_text or "")
    missing = []
    for pat in [
        r"column [\w]+\.([\w]+) does not exist",
        r"Could not find the '([^']+)' column",
        r"column ['\"]?([\w]+)['\"]? of",
    ]:
        missing += re.findall(pat, text, flags=re.I)
    cleaned = dict(payload)
    changed = False
    for col in missing:
        if col in cleaned:
            cleaned.pop(col, None)
            changed = True
    return cleaned, changed


def _sb_insert_adaptive(table, payload):
    current = _filter_payload_to_schema(table, payload)
    last = None
    for _ in range(15):
        try:
            return _sb_data(supabase.table(table).insert(current).execute())
        except Exception as exc:
            last = exc
            current, changed = _strip_missing_columns(current, _sb_error(exc))
            if not changed or not current:
                raise last
    raise last


def db_upsert_user(user):
    if not user or not supabase:
        return
    now = datetime.now().isoformat(timespec="seconds")
    email = user["email"].strip().lower()
    payload = {
        "email": email,
        "google_id": str(user.get("id", email)),
        "created_at": now,
        "name": user.get("name", "User"),
        "role": user_role(email),
        "last_seen": now,
    }
    try:
        cols = _sb_columns("profiles")
        clean = _filter_payload_to_schema("profiles", payload)
        identity = "email" if not cols or "email" in cols else _sb_identity_column("profiles")
        if identity:
            existing = _sb_data(supabase.table("profiles").select("*").eq(identity, email).limit(1).execute())
            if existing:
                supabase.table("profiles").update(clean).eq(identity, email).execute()
            else:
                _sb_insert_adaptive("profiles", clean)
        else:
            _sb_insert_adaptive("profiles", clean)
    except Exception as exc:
        st.session_state["supabase_profile_error"] = _sb_error(exc)


def db_record_attempt(user_email, questions, mode):
    if not supabase:
        return
    correct, incorrect, skipped, attended = answer_counts(questions)
    completed_at = datetime.now().isoformat(timespec="seconds")
    started_at = st.session_state.get("quiz_started_at")
    if not started_at:
        started_at = completed_at
    # Older sessions may have stored only HH:MM. Convert those to a real
    # timestamptz-compatible ISO timestamp before writing to Supabase.
    if isinstance(started_at, str) and re.fullmatch(r"\d{1,2}:\d{2}", started_at.strip()):
        now_dt = datetime.now()
        hh, mm = [int(x) for x in started_at.strip().split(":")]
        started_at = now_dt.replace(hour=hh, minute=mm, second=0, microsecond=0).isoformat(timespec="seconds")
    attempt = _payload_for_user("quiz_attempts", user_email, {
        # Current Supabase schema uses quiz_type + started_at + completed_at.
        # Keep the legacy/stat fields too; schema filtering removes them when
        # they are not present in older deployments.
        "email": user_email,
        "quiz_type": str(mode),
        "started_at": started_at,
        "completed_at": completed_at,
        "mode": str(mode),
        "quiz_mode": str(mode),
        "total": len(questions),
        "total_questions": len(questions),
        "correct": correct,
        "correct_answers": correct,
        "incorrect": incorrect,
        "incorrect_answers": incorrect,
        "skipped": skipped,
        "attended": attended,
        "accuracy": (correct / attended * 100 if attended else 0.0),
    })
    try:
        rows = _sb_insert_adaptive("quiz_attempts", attempt)
        attempt_id = rows[0].get("id") if rows else None
        for q in questions:
            ans = q.get("user_answer")
            row = _payload_for_user("answers", user_email, {
                "attempt_id": attempt_id,
                "email": user_email,
                "question": q["question"],
                "question_text": q["question"],
                "question_id": question_key(q),
                "selected_answer": ans,
                "correct_answer": q["correct"],
                "is_correct": bool(ans is not None and ans == q["correct"]),
                "skipped": ans is None,
                "explanation": q.get("explanation", ""),
            })
            try:
                _sb_insert_adaptive("answers", row)
            except Exception:
                pass
        _clear_supabase_read_cache()
        _invalidate_progress_cache()
        st.session_state["_attempt_rows_cache"] = []
        st.session_state["_attempt_rows_cache_at"] = datetime.now().timestamp()
        # Refresh local cache from the just-written row set only on next stats read.
    except Exception as exc:
        st.session_state["supabase_attempt_error"] = _sb_error(exc)


def _mistake_row_key(row):
    for field in ("question_key", "question_id", "question_text", "question", "title"):
        value = row.get(field)
        if value not in (None, ""):
            return str(value)
    return ""


def db_upsert_mistake(user_email, q):
    """Persist a mistake using the current compact Supabase mistakes schema.

    The deployed schema is user_id(UUID), question_id(text), mistake_count(integer),
    last_answer(text). We intentionally write only those guaranteed columns so
    schema discovery cannot prevent the insert.
    """
    if not supabase:
        return
    key = question_key(q)
    try:
        user_id = _sb_profile_id(user_email)
        if not user_id:
            raise RuntimeError("Could not resolve the logged-in user's Supabase profile UUID.")

        existing_rows = _sb_data(
            supabase.table("mistakes")
            .select("id,question_id,mistake_count,last_answer")
            .eq("user_id", user_id)
            .eq("question_id", key)
            .limit(1)
            .execute()
        )
        existing = existing_rows[0] if existing_rows else None
        times = int((existing or {}).get("mistake_count", 0) or 0) + 1
        payload = {
            "user_id": user_id,
            "question_id": key,
            "mistake_count": times,
            "last_answer": q.get("user_answer"),
        }
        if existing and existing.get("id") is not None:
            supabase.table("mistakes").update(payload).eq("id", existing["id"]).execute()
        else:
            supabase.table("mistakes").insert(payload).execute()
        _clear_supabase_read_cache()
        st.session_state.pop("_mistake_rows_cache", None)
        st.session_state.pop("_mistake_rows_cache_at", None)
    except Exception as exc:
        st.session_state["supabase_mistake_error"] = _sb_error(exc)


def db_remove_mistake(user_email, q):
    if not supabase:
        return
    try:
        for r in _sb_user_rows("mistakes", user_email):
            if _mistake_row_key(r) in {question_key(q), str(q.get("question", ""))}:
                if r.get("id") is not None:
                    supabase.table("mistakes").delete().eq("id", r["id"]).execute()
                _clear_supabase_read_cache()
                return
    except Exception as exc:
        st.session_state["supabase_mistake_error"] = _sb_error(exc)


def db_user_stats(email):
    if not supabase:
        return {"quizzes": 0, "correct": 0, "attended": 0, "total": 0, "accuracy": 0, "mistakes": 0, "last_seen": "—"}
    try:
        email = str(email or "").strip().lower()
        def owner(row):
            return str(row.get("email", row.get("user_email", row.get("owner_email", "")))).strip().lower()
        attempts = [r for r in _cached_sb_all_rows("quiz_attempts") if owner(r) == email]
        mistakes = [r for r in _cached_sb_all_rows("mistakes") if owner(r) == email]
        profiles = [r for r in _cached_sb_all_rows("profiles") if owner(r) == email]
        correct = sum(int(x.get("correct", 0) or 0) for x in attempts)
        attended = sum(int(x.get("correct", 0) or 0) + int(x.get("incorrect", 0) or 0) for x in attempts)
        total = attended + sum(int(x.get("skipped", 0) or 0) for x in attempts)
        return {"quizzes": len(attempts), "correct": correct, "attended": attended, "total": total,
                "accuracy": correct / attended * 100 if attended else 0,
                "mistakes": len(mistakes), "last_seen": profiles[0].get("last_seen", "—") if profiles else "—"}
    except Exception:
        return {"quizzes": 0, "correct": 0, "attended": 0, "total": 0, "accuracy": 0, "mistakes": 0, "last_seen": "—"}

def db_all_users():
    if not supabase:
        return []
    try:
        rows = _cached_sb_all_rows("profiles")
        return [(r.get("email", r.get("user_email", r.get("owner_email", ""))), r.get("name", ""), r.get("role", "user"), True,
                 r.get("created_at", "—"), r.get("last_seen", "—")) for r in rows]
    except Exception as exc:
        st.session_state["supabase_users_error"] = _sb_error(exc)
        return []

def _recover_question_from_bank(question_id, fallback_text=""):
    """Rehydrate full question data from the Google Sheet.

    The compact Supabase mistakes table intentionally stores only:
    user_id, question_id, mistake_count, last_answer.
    question_id is our stable date|question key, so use it to recover the
    original options/correct answer/explanation from the Google Sheet.
    """
    text = str(fallback_text or "")
    qdate = None
    key = str(question_id or "")

    if "|" in key:
        date_part, possible_text = key.split("|", 1)
        try:
            qdate = datetime.fromisoformat(date_part).date()
        except Exception:
            qdate = None
        if not text:
            text = possible_text

    if not text:
        return None

    try:
        for _, row in df.iterrows():
            row_date = row.get("Date")

            if isinstance(row_date, pd.Timestamp):
                row_date = row_date.date()
            elif not isinstance(row_date, date):
                try:
                    row_date = pd.to_datetime(row_date).date()
                except Exception:
                    row_date = None

            if qdate is not None and row_date != qdate:
                continue

            row_question = str(
                row.get("Question")
                or row.get("question")
                or row.get("question_text")
                or ""
            )

            if row_question != text:
                continue

            # These are the real Google Sheet column names used by this app.
            options = [
                str(row.get("Option_1"))
                for _ in [0]
                if row.get("Option_1") not in (None, "")
            ] + [
                str(row.get("Option_2"))
                for _ in [0]
                if row.get("Option_2") not in (None, "")
            ] + [
                str(row.get("Option_3"))
                for _ in [0]
                if row.get("Option_3") not in (None, "")
            ] + [
                str(row.get("Option_4"))
                for _ in [0]
                if row.get("Option_4") not in (None, "")
            ]

            correct = str(
                row.get("Correct_Option")
                or row.get("Answer")
                or row.get("Correct Answer")
                or row.get("correct")
                or ""
            ).strip()

            explanation = str(
                row.get("Explanation")
                or row.get("explanation")
                or ""
            )

            # If the sheet's Correct_Option is "1"/"2"/"3"/"4",
            # convert it to the corresponding option text.
            if correct in {"1", "2", "3", "4"} and len(options) >= int(correct):
                correct = options[int(correct) - 1]

            return {
                "date": row_date or qdate or date.today(),
                "question": text,
                "options": options,
                "correct": correct,
                "explanation": explanation,
            }

    except Exception as exc:
        st.session_state["supabase_mistake_error"] = (
            f"Could not rehydrate question {question_id}: {_sb_error(exc)}"
        )

    return None


def load_persistent_mistakes():
    ensure_progress_state()
    email = st.session_state.get("auth_user", {}).get("email", "")
    if not supabase:
        return
    try:
        user_id = _sb_profile_id(email)
        if not user_id:
            return
        rows = _cached_user_mistake_rows(user_id)
        bank = {}
        for row in rows:
            key = str(row.get("question_id") or "")
            recovered = _recover_question_from_bank(key)
            if not recovered or not recovered.get("options"):
                continue
            item = {
                **recovered,
                "last_answer": row.get("last_answer"),
                "times_missed": int(row.get("mistake_count", 1) or 1),
            }
            bank[question_key(item)] = item
        st.session_state.mistake_bank = bank
    except Exception as exc:
        st.session_state["supabase_mistake_error"] = _sb_error(exc)


def load_persistent_history():
    ensure_progress_state()
    email = st.session_state.get("auth_user", {}).get("email", "")
    if not supabase:
        return
    try:
        history = []
        user_id = _sb_profile_id(email)
        rows = _cached_user_attempts(user_id) if user_id else []
        for row in rows:
            d = pd.to_datetime(row.get("completed_at") or row.get("created_at") or row.get("date"), errors="coerce")
            history.append({"completed_on": d.date() if not pd.isna(d) else date.today(),
                            "correct": int(row.get("correct", 0) or 0),
                            "incorrect": int(row.get("incorrect", 0) or 0),
                            "skipped": int(row.get("skipped", 0) or 0),
                            "mode": row.get("quiz_mode") or row.get("test_mode") or row.get("mode") or "Quiz"})
        st.session_state.quiz_history = history
    except Exception as exc:
        st.session_state["supabase_history_error"] = _sb_error(exc)


def _delete_user_rows(table, email):
    if not supabase:
        return
    cols = _sb_columns(table)
    if cols and "user_id" in cols:
        user_id = _sb_profile_id(email)
        if user_id:
            supabase.table(table).delete().eq("user_id", user_id).execute()
    else:
        identity = _sb_identity_column(table)
        if identity:
            supabase.table(table).delete().eq(identity, email).execute()


def db_reset_user_progress(email):
    if not supabase:
        return
    try:
        for table in ("answers", "quiz_attempts", "mistakes", "daily_activity", "achievements"):
            _delete_user_rows(table, email)
        _clear_supabase_read_cache()
    except Exception as exc:
        raise RuntimeError(_sb_error(exc)) from exc


def db_reset_user_progress(email):
    if not supabase:
        return
    try:
        for table in ("answers", "quiz_attempts", "mistakes", "daily_activity", "achievements"):
            identity = _sb_identity_column(table)
            if identity:
                supabase.table(table).delete().eq(identity, email).execute()
    except Exception as exc:
        raise RuntimeError(_sb_error(exc)) from exc

def db_delete_user(email):
    """Delete a user's study data and profile from the app database.
    This does not delete the person's Google identity; they can sign in again and
    a new profile will be created. Admin accounts are protected.
    """
    if not supabase:
        raise RuntimeError("Supabase is not configured.")
    email = str(email or "").strip().lower()
    if not email:
        raise RuntimeError("No user email was supplied.")
    if email in ALL_ADMIN_EMAILS:
        raise RuntimeError("Admin accounts cannot be deleted from the app.")

    # Delete dependent records first, then the profile.
    for table in ("answers", "quiz_attempts", "mistakes", "daily_activity", "achievements"):
        _delete_user_rows(table, email)

    profile_identity = _sb_identity_column("profiles")
    if profile_identity:
        supabase.table("profiles").delete().eq(profile_identity, email).execute()


THEMES = {
    "Sage": {
        "bg": "#F7FAF4",
        "surface": "#FFFFFF",
        "surface_2": "#EEF4E8",
        "text": "#26352B",
        "muted": "#708074",
        "accent": "#789052",
        "accent_dark": "#526A37",
        "accent_soft": "#E3EAD9",
        "success": "#4E8A63",
        "danger": "#D97A68",
        "warning": "#C8954D",
        "border": "#DDE6D7",
        "shadow": "rgba(52, 74, 45, 0.10)",
    },
    "Lavender": {
        "bg": "#F8F6FC",
        "surface": "#FFFFFF",
        "surface_2": "#F0ECF8",
        "text": "#302B3D",
        "muted": "#756F85",
        "accent": "#8A78B8",
        "accent_dark": "#665594",
        "accent_soft": "#E7E0F3",
        "success": "#5C9874",
        "danger": "#CE786D",
        "warning": "#C58A45",
        "border": "#E1DBED",
        "shadow": "rgba(66, 52, 92, 0.10)",
    },
    "Sky": {
        "bg": "#F3F9FC",
        "surface": "#FFFFFF",
        "surface_2": "#E8F2F7",
        "text": "#263740",
        "muted": "#6E7F88",
        "accent": "#4E91AE",
        "accent_dark": "#356F87",
        "accent_soft": "#DDEDF4",
        "success": "#4F936B",
        "danger": "#CE786D",
        "warning": "#BF8A48",
        "border": "#D7E6ED",
        "shadow": "rgba(44, 85, 103, 0.10)",
    },
}


if "theme" not in st.session_state or st.session_state.theme not in THEMES:
    st.session_state.theme = "Sage"

T = THEMES[st.session_state.theme]

# =========================================================
# GLOBAL CSS
# =========================================================
st.markdown(
    f"""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=Playfair+Display:wght@600;700&display=swap');

    :root {{
        --bg: {T['bg']};
        --surface: {T['surface']};
        --surface2: {T['surface_2']};
        --text: {T['text']};
        --muted: {T['muted']};
        --accent: {T['accent']};
        --accent-dark: {T['accent_dark']};
        --accent-soft: {T['accent_soft']};
        --success: {T['success']};
        --danger: {T['danger']};
        --warning: {T['warning']};
        --border: {T['border']};
        --shadow: {T['shadow']};
    }}

    html, body, [class*="css"] {{
        font-family: 'DM Sans', sans-serif;
    }}

    .stApp {{
        background:
            radial-gradient(circle at 90% 5%, rgba(134, 158, 99, 0.08), transparent 24%),
            radial-gradient(circle at 10% 30%, rgba(134, 158, 99, 0.05), transparent 20%),
            var(--bg);
        color: var(--text);
    }}

    [data-testid="stHeader"] {{
        background: transparent;
    }}

    [data-testid="stSidebar"] {{
        background: var(--surface2) !important;
        border-right: 1px solid var(--border);
    }}

    [data-testid="stSidebar"] * {{
        color: var(--text) !important;
    }}

    h1, h2, h3, h4 {{
        color: var(--text) !important;
        letter-spacing: -0.025em;
    }}

    h1 {{
        font-family: 'Playfair Display', serif !important;
        font-size: clamp(2.3rem, 4vw, 3.8rem) !important;
        line-height: 1.05 !important;
        margin-bottom: 0.45rem !important;
    }}

    h2 {{ font-size: 1.65rem !important; }}
    h3 {{ font-size: 1.25rem !important; }}
    p, label, .stMarkdown, .stCaption {{ color: var(--text); }}
    .stCaption {{ color: var(--muted) !important; }}

    .block-container {{
        max-width: 1500px;
        padding: 2.5rem 3rem 4rem;
    }}

    .hero {{
        position: relative;
        overflow: hidden;
        background: linear-gradient(135deg, var(--surface), var(--surface2));
        border: 1px solid var(--border);
        border-radius: 28px;
        padding: 2.3rem 2.5rem;
        box-shadow: 0 18px 50px var(--shadow);
        margin-bottom: 1.5rem;
    }}
    .hero::after {{
        content: "";
        position: absolute;
        width: 260px;
        height: 260px;
        right: -90px;
        top: -110px;
        border-radius: 50%;
        background: rgba(120, 144, 82, 0.13);
    }}
    .eyebrow {{
        color: var(--accent-dark);
        text-transform: uppercase;
        letter-spacing: 0.16em;
        font-weight: 700;
        font-size: 0.78rem;
        margin-bottom: 0.65rem;
    }}
    .hero-sub {{
        color: var(--muted);
        font-size: 1.05rem;
        max-width: 720px;
        line-height: 1.65;
    }}
    .hero-badge {{
        display: inline-block;
        margin-top: 1rem;
        padding: 0.42rem 0.72rem;
        border-radius: 999px;
        background: var(--accent-soft);
        color: var(--accent-dark);
        font-size: 0.8rem;
        font-weight: 700;
    }}

    .card {{
        background: var(--surface);
        border: 1px solid var(--border);
        border-radius: 22px;
        padding: 1.25rem 1.35rem;
        box-shadow: 0 10px 32px var(--shadow);
        height: 100%;
    }}
    .metric-label {{
        color: var(--muted);
        font-size: 0.78rem;
        font-weight: 700;
        text-transform: uppercase;
        letter-spacing: 0.08em;
    }}
    .metric-value {{
        color: var(--text);
        font-size: 2rem;
        font-weight: 700;
        margin-top: 0.25rem;
    }}
    .metric-foot {{
        color: var(--muted);
        font-size: 0.82rem;
        margin-top: 0.25rem;
    }}

    .section-head {{
        display: flex;
        align-items: end;
        justify-content: space-between;
        gap: 1rem;
        margin: 1.8rem 0 0.85rem;
    }}
    .section-head .title {{ font-weight: 700; font-size: 1.2rem; color: var(--text); }}
    .section-head .hint {{ color: var(--muted); font-size: 0.86rem; }}

    .quiz-shell {{
        background: var(--surface);
        border: 1px solid var(--border);
        border-radius: 28px;
        padding: 1.8rem 2rem;
        box-shadow: 0 18px 55px var(--shadow);
    }}
    .q-meta {{
        display: flex;
        flex-wrap: wrap;
        gap: 0.5rem;
        margin-bottom: 1.1rem;
    }}
    .pill {{
        padding: 0.38rem 0.7rem;
        border-radius: 999px;
        background: var(--accent-soft);
        color: var(--accent-dark);
        font-size: 0.76rem;
        font-weight: 700;
    }}
    .question-text {{
        font-size: clamp(1.35rem, 2.4vw, 2rem);
        font-weight: 700;
        line-height: 1.42;
        color: var(--text);
        margin: 0.4rem 0 1.4rem;
    }}

    /* Make radio options look like tappable answer cards. */
    div[data-testid="stRadio"] > div {{ gap: 0.65rem; }}
    div[data-testid="stRadio"] label {{
        background: var(--surface2);
        border: 1px solid var(--border);
        border-radius: 15px;
        padding: 0.6rem 0.75rem;
        transition: transform .15s ease, border-color .15s ease, background .15s ease;
    }}
    div[data-testid="stRadio"] label:hover {{
        border-color: var(--accent);
        transform: translateY(-1px);
    }}

    div.stButton > button {{
        border-radius: 13px !important;
        border: 1px solid var(--border) !important;
        background: var(--surface) !important;
        color: var(--text) !important;
        font-weight: 700 !important;
        min-height: 44px;
        transition: all .18s ease;
    }}
    div.stButton > button:hover {{
        border-color: var(--accent) !important;
        transform: translateY(-1px);
        box-shadow: 0 8px 20px var(--shadow);
    }}
    div.stButton > button[kind="primary"] {{
        background: var(--accent) !important;
        color: white !important;
        border-color: var(--accent) !important;
    }}

    .palette {{
        display: grid;
        grid-template-columns: repeat(10, minmax(28px, 1fr));
        gap: .45rem;
    }}
    .palette-item {{
        display: grid;
        place-items: center;
        height: 34px;
        border-radius: 10px;
        font-size: .72rem;
        font-weight: 700;
        border: 1px solid var(--border);
        background: var(--surface2);
        color: var(--text);
    }}
    .palette-current {{ border: 2px solid var(--accent); }}
    .palette-done {{ background: var(--accent-soft); color: var(--accent-dark); }}
    .palette-good {{ background: rgba(78,138,99,.15); color: var(--success); }}
    .palette-bad {{ background: rgba(217,122,104,.15); color: var(--danger); }}

    .stProgress > div > div > div > div {{ background: var(--accent) !important; }}
    [data-testid="stMetricValue"] {{ color: var(--text) !important; }}
    [data-testid="stMetricLabel"] {{ color: var(--muted) !important; }}
    div[data-testid="stAlert"] {{ border-radius: 15px !important; }}

    .tip {{
        border-left: 4px solid var(--accent);
        padding: .8rem 1rem;
        border-radius: 0 14px 14px 0;
        background: var(--surface2);
        color: var(--text);
    }}

    .review-panel {{
        background: linear-gradient(145deg, var(--surface2), var(--surface));
        border: 1px solid var(--border);
        border-radius: 20px;
        padding: 1rem 1.05rem;
        box-shadow: 0 10px 26px var(--shadow);
        min-height: 255px;
    }}
    .review-kicker {{
        color: var(--danger);
        font-size: .75rem;
        text-transform: uppercase;
        letter-spacing: .1em;
        font-weight: 800;
        margin-bottom: .5rem;
    }}
    .review-question {{
        font-weight: 700;
        font-size: .98rem;
        line-height: 1.45;
        margin-bottom: .8rem;
    }}
    .review-row {{
        display: flex;
        justify-content: space-between;
        gap: .75rem;
        padding: .55rem .7rem;
        border-radius: 11px;
        margin-top: .4rem;
        font-size: .84rem;
    }}
    .review-row.wrong {{ background: rgba(217,122,104,.10); color: var(--danger); }}
    .review-row.correct {{ background: rgba(78,138,99,.11); color: var(--success); }}
    .review-explanation {{
        margin-top: .75rem;
        padding-top: .75rem;
        border-top: 1px solid var(--border);
        color: var(--muted);
        line-height: 1.55;
        font-size: .84rem;
    }}
    .empty-review {{
        min-height: 255px;
        border: 1px dashed var(--border);
        border-radius: 20px;
        display: flex;
        flex-direction: column;
        justify-content: center;
        align-items: center;
        text-align: center;
        padding: 1.4rem;
        background: rgba(255,255,255,.20);
    }}
    .empty-review-icon {{ font-size: 2rem; margin-bottom: .4rem; }}
    .empty-review-title {{ font-weight: 800; color: var(--text); }}
    .empty-review-text {{ color: var(--muted); line-height: 1.55; font-size: .86rem; margin-top: .25rem; }}

    /* -----------------------------------------------------------------
       FORCE LIGHT THEMED STREAMLIT / BASEWEB CONTROLS
       Streamlit can inherit the browser/app dark color-scheme.  The rules
       below deliberately override that inheritance for the date picker,
       selectbox and their portal/popover elements.
       ----------------------------------------------------------------- */
    [data-baseweb="select"],
    [data-baseweb="input"],
    [data-testid="stDateInput"],
    [data-testid="stDateInput"] *,
    [data-testid="stSelectbox"] * {{
        color-scheme: light !important;
    }}

    [data-baseweb="select"] > div,
    [data-baseweb="select"] [role="combobox"],
    [data-baseweb="input"] > div,
    [data-baseweb="input"] > div > div,
    [data-testid="stDateInput"] [data-baseweb="input"],
    [data-testid="stDateInput"] [data-baseweb="input"] > div,
    [data-testid="stDateInput"] [data-baseweb="input"] > div > div {{
        background: var(--surface) !important;
        background-color: var(--surface) !important;
        color: var(--text) !important;
        border-color: var(--border) !important;
        border-radius: 12px !important;
        box-shadow: none !important;
    }}

    [data-testid="stDateInput"] input,
    [data-testid="stSelectbox"] input,
    [data-baseweb="select"] input,
    [data-baseweb="input"] input {{
        background: transparent !important;
        background-color: transparent !important;
        color: var(--text) !important;
        -webkit-text-fill-color: var(--text) !important;
        caret-color: var(--accent-dark) !important;
        color-scheme: light !important;
    }}

    [data-testid="stDateInput"] svg,
    [data-testid="stSelectbox"] svg,
    [data-baseweb="select"] svg {{
        fill: var(--accent-dark) !important;
        color: var(--accent-dark) !important;
    }}

    /* Date-picker popup is rendered in a portal outside the DateInput block. */
    [data-baseweb="popover"],
    [data-baseweb="popover"] > div,
    [data-baseweb="popover"] > div > div,
    [data-baseweb="calendar"],
    [data-baseweb="calendar"] > div,
    [data-baseweb="calendar"] > div > div,
    [role="dialog"],
    [role="dialog"] > div {{
        background: var(--surface) !important;
        background-color: var(--surface) !important;
        color: var(--text) !important;
        border-color: var(--border) !important;
        color-scheme: light !important;
    }}

    [data-baseweb="calendar"] {{
        border-radius: 16px !important;
        box-shadow: 0 18px 45px var(--shadow) !important;
        overflow: hidden !important;
    }}

    [data-baseweb="calendar"] *,
    [role="dialog"] * {{
        color: var(--text) !important;
        color-scheme: light !important;
    }}

    [data-baseweb="calendar"] button {{
        color: var(--text) !important;
        background: transparent !important;
        background-color: transparent !important;
        border-radius: 9px !important;
        border-color: transparent !important;
    }}

    [data-baseweb="calendar"] button:hover {{
        background: var(--accent-soft) !important;
        background-color: var(--accent-soft) !important;
        color: var(--accent-dark) !important;
    }}

    [data-baseweb="calendar"] [aria-selected="true"] {{
        background: var(--accent) !important;
        background-color: var(--accent) !important;
        color: #ffffff !important;
    }}

    [data-baseweb="calendar"] [aria-current="date"] {{
        color: var(--accent-dark) !important;
        font-weight: 800 !important;
    }}

    /* Month/year selector menus opened from the calendar. */
    [data-baseweb="menu"],
    [data-baseweb="menu"] > div,
    [role="listbox"],
    [role="listbox"] > div,
    [role="option"] {{
        background: var(--surface) !important;
        background-color: var(--surface) !important;
        color: var(--text) !important;
        color-scheme: light !important;
    }}

    [role="option"]:hover,
    [role="option"][aria-selected="true"] {{
        background: var(--accent-soft) !important;
        background-color: var(--accent-soft) !important;
        color: var(--accent-dark) !important;
    }}

    /* -----------------------------------------------------------------
       ADMIN DASHBOARD LIGHT CONTROLS
       Explicitly override Streamlit/BaseWeb dark surfaces. These rules
       cover text inputs, selectboxes, dropdown menus, and dataframe cells.
       ----------------------------------------------------------------- */
    [data-testid="stTextInput"],
    [data-testid="stTextInput"] > div,
    [data-testid="stTextInput"] [data-baseweb="input"],
    [data-testid="stTextInput"] [data-baseweb="input"] > div,
    [data-testid="stTextInput"] input,
    [data-testid="stSelectbox"],
    [data-testid="stSelectbox"] > div,
    [data-testid="stSelectbox"] [data-baseweb="select"],
    [data-testid="stSelectbox"] [data-baseweb="select"] > div,
    [data-testid="stSelectbox"] [role="combobox"] {{
        background: var(--surface) !important;
        background-color: var(--surface) !important;
        color: var(--text) !important;
        -webkit-text-fill-color: var(--text) !important;
        border-color: var(--border) !important;
        color-scheme: light !important;
    }}

    [data-testid="stTextInput"] input::placeholder {{
        color: var(--muted) !important;
        -webkit-text-fill-color: var(--muted) !important;
        opacity: 1 !important;
    }}

    /* Dropdown portal: BaseWeb renders this outside stSelectbox. */
    [data-baseweb="popover"],
    [data-baseweb="popover"] > div,
    [data-baseweb="menu"],
    [data-baseweb="menu"] > div,
    [role="listbox"],
    [role="listbox"] > div,
    [role="option"] {{
        background: var(--surface) !important;
        background-color: var(--surface) !important;
        color: var(--text) !important;
        color-scheme: light !important;
    }}

    [role="option"]:hover,
    [role="option"][aria-selected="true"] {{
        background: var(--accent-soft) !important;
        background-color: var(--accent-soft) !important;
        color: var(--accent-dark) !important;
    }}

    /* Arrow and select text. */
    [data-testid="stSelectbox"] svg,
    [data-testid="stSelectbox"] [data-baseweb="select"] svg {{
        color: var(--accent-dark) !important;
        fill: var(--accent-dark) !important;
    }}

    /* Dataframe/table: force the grid out of browser dark mode. */
    [data-testid="stDataFrame"],
    [data-testid="stDataFrame"] > div,
    [data-testid="stDataFrame"] iframe {{
        color-scheme: light !important;
        background: var(--surface) !important;
        background-color: var(--surface) !important;
    }}

    /* Streamlit's newer dataframe implementation uses these selectors. */
    [data-testid="stDataFrame"] [role="grid"],
    [data-testid="stDataFrame"] [role="row"],
    [data-testid="stDataFrame"] [role="gridcell"],
    [data-testid="stDataFrame"] [role="columnheader"] {{
        color-scheme: light !important;
    }}

    /* Sidebar Theme select gets an extra explicit light surface. */
    [data-testid="stSidebar"] [data-baseweb="select"] > div,
    [data-testid="stSidebar"] [data-baseweb="select"] [role="combobox"] {{
        background: var(--surface) !important;
        background-color: var(--surface) !important;
        color: var(--text) !important;
        color-scheme: light !important;
        border: 1px solid var(--border) !important;
    }}

    .calendar-label {{
        color: var(--muted);
        font-size: .84rem;
        font-weight: 700;
        margin: .25rem 0 .55rem;
    }}
    .calendar-title {{
        text-align: center;
        font-weight: 800;
        color: var(--text);
        padding: .55rem 0;
    }}
    .calendar-weekday {{
        text-align: center;
        color: var(--muted);
        font-size: .72rem;
        font-weight: 800;
        padding: .3rem 0 .45rem;
        text-transform: uppercase;
        letter-spacing: .04em;
    }}
    .calendar-empty {{
        height: 2.35rem;
    }}
    .calendar-selected {{
        margin-top: .65rem;
        padding: .65rem .8rem;
        border: 1px solid var(--border);
        border-radius: 12px;
        background: var(--accent-soft);
        color: var(--accent-dark);
        font-size: .82rem;
    }}
    /* Custom calendar buttons stay fully in the active light theme. */
    div[data-testid="stHorizontalBlock"] div[data-testid="stButton"] > button {{
        background: var(--surface) !important;
        color: var(--text) !important;
        border: 1px solid var(--border) !important;
        box-shadow: none !important;
        min-height: 2.35rem !important;
        padding: .25rem .1rem !important;
        font-size: .82rem !important;
    }}
    div[data-testid="stHorizontalBlock"] div[data-testid="stButton"] > button:hover {{
        background: var(--accent-soft) !important;
        color: var(--accent-dark) !important;
        border-color: var(--accent) !important;
    }}


    .progress-strip {{
        display:grid;
        grid-template-columns: repeat(4, minmax(0,1fr));
        gap: .75rem;
        margin: 1rem 0 1.3rem;
    }}
    .progress-stat {{
        background: var(--surface);
        border: 1px solid var(--border);
        border-radius: 18px;
        padding: 1rem 1.05rem;
        box-shadow: 0 8px 24px var(--shadow);
    }}
    .progress-stat-label {{ color: var(--muted); font-size:.76rem; font-weight:800; text-transform:uppercase; letter-spacing:.08em; }}
    .progress-stat-value {{ color:var(--text); font-size:1.75rem; font-weight:800; margin-top:.18rem; }}
    .progress-stat-sub {{ color:var(--muted); font-size:.79rem; margin-top:.2rem; }}
    .week-dots {{ display:flex; gap:.45rem; margin-top:.85rem; }}
    .week-dot {{ width:2rem; height:2rem; border-radius:10px; display:grid; place-items:center; background:var(--surface2); border:1px solid var(--border); color:var(--muted); font-size:.72rem; font-weight:800; }}
    .week-dot.done {{ background:var(--accent); border-color:var(--accent); color:#fff; }}
    .week-dot.today {{ outline:2px solid var(--accent); outline-offset:2px; }}
    .mistake-card {{ background:var(--surface); border:1px solid var(--border); border-radius:18px; padding:1rem 1.1rem; margin-bottom:.8rem; box-shadow:0 8px 24px var(--shadow); }}
    .mistake-tag {{ display:inline-block; padding:.3rem .55rem; border-radius:999px; background:rgba(217,122,104,.12); color:var(--danger); font-size:.72rem; font-weight:800; text-transform:uppercase; letter-spacing:.06em; }}
    .review-answer {{ margin-top:.45rem; padding:.55rem .7rem; background:var(--surface2); border-radius:11px; font-size:.84rem; }}
    @media (max-width: 900px) {{
        .progress-strip {{ grid-template-columns: repeat(2, minmax(0,1fr)); }}
    }}

    @media (max-width: 900px) {{
        .block-container {{ padding: 1.25rem 1rem 3rem; }}
        .hero {{ padding: 1.6rem; border-radius: 22px; }}
        .quiz-shell {{ padding: 1.2rem; border-radius: 20px; }}
    }}
    </style>
    """,
    unsafe_allow_html=True,
)

# =========================================================
# DATA
# =========================================================
@st.cache_data(ttl=300, show_spinner=False)
def load_data():
    df = pd.read_csv(SHEET_URL)
    required = ["Date", "Question", "Option_1", "Option_2", "Option_3", "Option_4", "Correct_Option", "Explanation"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"Missing columns in Google Sheet: {', '.join(missing)}")

    # Google Sheets can return dates in several text formats. Parse the column
    # defensively so a format change does not turn the entire column into NaN.
    raw_dates = df["Date"].astype("string").str.strip()
    parsed_dates = pd.to_datetime(raw_dates, errors="coerce", format="mixed", dayfirst=True)
    if parsed_dates.notna().sum() == 0:
        parsed_dates = pd.to_datetime(raw_dates, errors="coerce", format="mixed", dayfirst=False)

    df["Date"] = parsed_dates.dt.date
    df = df.dropna(subset=["Date", "Question"]).reset_index(drop=True)

    if df.empty:
        raise ValueError(
            "No valid quiz rows were found in the Google Sheet. "
            "Please check that the Date column contains real dates and that the sheet has questions."
        )
    return df

try:
    df = load_data()
except Exception as exc:
    st.error("Could not load your Google Sheet.")
    st.exception(exc)
    st.stop()

# =========================================================
# STATE HELPERS
# =========================================================
def reset_quiz_state():
    keys = [
        "quiz_questions", "current_index", "quiz_completed", "celebration_done",
        "source_df", "q_mode", "num_q", "quiz_started_at", "review_panel",
        "quiz_recorded", "review_mode"
    ]
    for key in keys:
        st.session_state.pop(key, None)


def ensure_progress_state():
    if "study_history" not in st.session_state:
        st.session_state.study_history = []
    if "mistake_bank" not in st.session_state:
        st.session_state.mistake_bank = {}


def question_key(q):
    return f"{q['date'].isoformat()}|{q['question']}"


def update_mistake_bank(q):
    ensure_progress_state()
    key = question_key(q)
    email = st.session_state.get("auth_user", {}).get("email", "")
    if q["user_answer"] is not None and q["user_answer"] == q["correct"]:
        st.session_state.mistake_bank.pop(key, None)
        db_remove_mistake(email, q)
        _clear_supabase_read_cache()
        return
    existing = st.session_state.mistake_bank.get(key, {})
    item = {
        "date": q["date"],
        "question": q["question"],
        "options": q["options"],
        "correct": q["correct"],
        "explanation": q["explanation"],
        "last_answer": q["user_answer"],
        "times_missed": int(existing.get("times_missed", 0)) + 1,
    }
    st.session_state.mistake_bank[key] = item
    db_upsert_mistake(email, q)
    _clear_supabase_read_cache()


def record_quiz_result(questions):
    ensure_progress_state()
    if st.session_state.get("quiz_recorded"):
        return
    correct, incorrect, skipped, attended = answer_counts(questions)
    mode = "Review" if st.session_state.get("review_mode") else st.session_state.get("q_mode", "Quiz")
    st.session_state.study_history.append({
        "completed_on": date.today(),
        "correct": correct,
        "incorrect": incorrect,
        "skipped": skipped,
        "attended": attended,
        "total": len(questions),
        "mode": mode,
    })
    user_email = st.session_state.get("auth_user", {}).get("email", "")
    db_record_attempt(user_email, questions, mode)
    _clear_supabase_read_cache()
    st.session_state.quiz_recorded = True


def progress_stats():
    """Return fast, persisted progress with a short session cache."""
    ensure_progress_state()
    now_ts = datetime.now().timestamp()
    cached = st.session_state.get("_progress_cache")
    cached_at = st.session_state.get("_progress_cache_at", 0.0)
    if cached is not None and now_ts - cached_at < 8:
        return cached

    email = str(st.session_state.get("auth_user", {}).get("email", "")).strip().lower()
    attempts = []
    if supabase and email:
        user_id = _sb_profile_id(email)
        if user_id:
            attempts = _cached_user_attempts(user_id)

    persisted = []
    for r in attempts:
        raw_date = r.get("completed_at") or r.get("started_at") or r.get("date")
        dt = pd.to_datetime(raw_date, errors="coerce")
        persisted.append({
            "completed_on": dt.date() if not pd.isna(dt) else date.today(),
            "correct": int(r.get("correct", 0) or 0),
            "incorrect": int(r.get("incorrect", 0) or 0),
            "skipped": int(r.get("skipped", 0) or 0),
            "mode": r.get("quiz_type") or "Quiz",
        })

    all_attempts = list(persisted)
    seen = {(x["completed_on"], x["correct"], x["incorrect"], x["skipped"], x["mode"]) for x in persisted}
    for x in st.session_state.get("study_history", []):
        key = (x.get("completed_on"), int(x.get("correct", 0)), int(x.get("incorrect", 0)), int(x.get("skipped", 0)), x.get("mode", "Quiz"))
        if key not in seen:
            all_attempts.append(x)
            seen.add(key)

    quizzes = len(all_attempts)
    correct = sum(int(x.get("correct", 0) or 0) for x in all_attempts)
    attended = sum(int(x.get("correct", 0) or 0) + int(x.get("incorrect", 0) or 0) for x in all_attempts)
    accuracy = correct / attended * 100 if attended else 0
    dates = sorted({x.get("completed_on") for x in all_attempts if x.get("completed_on")})

    best = cur = 0
    prev = None
    for d in dates:
        cur = cur + 1 if prev is not None and d == prev + timedelta(days=1) else 1
        best = max(best, cur)
        prev = d
    today = date.today()
    current = cur if dates and dates[-1] in {today, today - timedelta(days=1)} else 0

    result = {"quizzes": quizzes, "correct": correct, "attended": attended, "accuracy": accuracy, "current_streak": current, "best_streak": best, "dates": dates}
    st.session_state["_progress_cache"] = result
    st.session_state["_progress_cache_at"] = now_ts
    return result


def render_progress_strip():
    stats = progress_stats()
    st.markdown(
        f"""
        <div class='progress-strip'>
            <div class='progress-stat'><div class='progress-stat-label'>Current streak</div><div class='progress-stat-value'>{stats['current_streak']} day{'s' if stats['current_streak'] != 1 else ''} 🔥</div><div class='progress-stat-sub'>Keep the chain alive.</div></div>
            <div class='progress-stat'><div class='progress-stat-label'>Best streak</div><div class='progress-stat-value'>{stats['best_streak']} day{'s' if stats['best_streak'] != 1 else ''}</div><div class='progress-stat-sub'>Your personal record.</div></div>
            <div class='progress-stat'><div class='progress-stat-label'>Quizzes taken</div><div class='progress-stat-value'>{stats['quizzes']}</div><div class='progress-stat-sub'>Completed sessions.</div></div>
            <div class='progress-stat'><div class='progress-stat-label'>Overall accuracy</div><div class='progress-stat-value'>{stats['accuracy']:.0f}%</div><div class='progress-stat-sub'>{stats['correct']} correct / {stats['attended']} attempted.</div></div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    week_start = date.today() - timedelta(days=date.today().weekday())
    completed = set(stats["dates"])
    labels = ["M","T","W","T","F","S","S"]
    dots = []
    for i, label in enumerate(labels):
        d = week_start + timedelta(days=i)
        cls = "week-dot"
        if d in completed:
            cls += " done"
        if d == date.today():
            cls += " today"
        dots.append(f"<div class='{cls}'>{label}</div>")
    st.markdown(
        f"<div class='card'><div class='metric-label'>This week</div><div class='week-dots'>{''.join(dots)}</div></div>",
        unsafe_allow_html=True,
    )


def initialize_quiz_from_records(records):
    questions = []
    for item in records:
        opts = list(item["options"])
        random.shuffle(opts)
        questions.append({
            "date": item["date"],
            "question": item["question"],
            "options": opts,
            "correct": item["correct"],
            "explanation": item["explanation"],
            "user_answer": None,
            "locked": False,
        })
    st.session_state.update({
        "source_df": pd.DataFrame(),
        "q_mode": "Review",
        "num_q": len(questions),
        "quiz_questions": questions,
        "current_index": 0,
        "quiz_completed": False,
        "celebration_done": False,
        "quiz_started_at": datetime.now().isoformat(timespec="seconds"),
        "review_panel": None,
        "review_mode": True,
        "quiz_recorded": False,
    })


def generate_question_sample(source_df, q_mode, num_q):
    """Build a custom test with sensible date coverage.

    Rules for a custom number:
      * When requested <= number of available study days: pick that many
        random dates, then pick exactly one random question from each date.
      * When requested > number of available study days: guarantee one
        question from every available day, then fill the remaining slots with
        random questions from the rest of the selected date range.
      * Never silently increase a user's requested count.

    This means asking for 5 questions across 133 days produces exactly 5
    questions from 5 random dates, while asking for 150 produces 150
    questions with at least one question from each of the 133 days.
    """
    if source_df.empty:
        return source_df.copy()

    # "All" keeps the existing behaviour: every question in the range.
    if q_mode == "All":
        return source_df.sample(frac=1).reset_index(drop=True)

    requested = max(1, int(num_q))
    available_days = source_df["Date"].dropna().unique()
    day_count = len(available_days)
    if day_count == 0:
        return source_df.sample(n=min(requested, len(source_df))).reset_index(drop=True)

    # Never create more questions than actually exist.
    target_count = min(requested, len(source_df))

    # Case 1: the test is smaller than the number of study days.
    # Pick random dates first so the requested questions are spread across
    # randomly chosen days instead of being forced to cover every day.
    if requested <= day_count:
        chosen_dates = list(random.sample(list(available_days), requested))
        pieces = []
        for d in chosen_dates:
            day_questions = source_df[source_df["Date"] == d]
            pieces.append(day_questions.sample(n=1))
        return pd.concat(pieces).sample(frac=1).reset_index(drop=True)

    # Case 2: the test is larger than the number of available days.
    # First guarantee one question from every day. Then fill the remaining
    # slots randomly from all questions that were not already selected.
    guaranteed_df = source_df.groupby("Date", group_keys=False).sample(n=1)
    remaining_needed = target_count - len(guaranteed_df)

    if remaining_needed <= 0:
        final_sample = guaranteed_df
    else:
        remaining_pool = source_df.drop(index=guaranteed_df.index)
        extra_df = remaining_pool.sample(
            n=min(remaining_needed, len(remaining_pool)),
            replace=False,
        )
        final_sample = pd.concat([guaranteed_df, extra_df])

    return final_sample.sample(frac=1).reset_index(drop=True)


def initialize_quiz(source_df, q_mode, num_q):
    shuffled_df = generate_question_sample(source_df, q_mode, num_q)
    questions = []

    for _, row in shuffled_df.iterrows():
        opts = [
            str(row["Option_1"]), str(row["Option_2"]),
            str(row["Option_3"]), str(row["Option_4"])
        ]
        random.shuffle(opts)
        correct = str(row["Correct_Option"]).strip()
        questions.append({
            "date": row["Date"],
            "question": str(row["Question"]),
            "options": opts,
            "correct": correct,
            "explanation": str(row["Explanation"]),
            "user_answer": None,
            "locked": False,
        })

    st.session_state.update({
        "source_df": source_df,
        "q_mode": q_mode,
        "num_q": num_q,
        "quiz_questions": questions,
        "current_index": 0,
        "quiz_completed": False,
        "celebration_done": False,
        "quiz_started_at": datetime.now().isoformat(timespec="seconds"),
        "review_panel": None,
        "review_mode": False,
        "quiz_recorded": False,
    })


def answer_counts(questions):
    attended = sum(q["user_answer"] is not None for q in questions)
    correct = sum(q["user_answer"] == q["correct"] for q in questions)
    skipped = len(questions) - attended
    incorrect = attended - correct
    return correct, incorrect, skipped, attended


def render_metric(label, value, foot=""):
    st.markdown(
        f"""
        <div class="card">
            <div class="metric-label">{label}</div>
            <div class="metric-value">{value}</div>
            <div class="metric-foot">{foot}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_palette(questions, current):
    items = []
    for i, q in enumerate(questions):
        cls = "palette-item"
        if i == current:
            cls += " palette-current"
        if q["locked"]:
            if q["user_answer"] == q["correct"]:
                cls += " palette-good"
            elif q["user_answer"] is None:
                cls += " palette-done"
            else:
                cls += " palette-bad"
        items.append(f'<div class="{cls}">{i+1}</div>')
    st.markdown('<div class="palette">' + ''.join(items) + '</div>', unsafe_allow_html=True)



def _shift_month(d, delta):
    y, m = d.year, d.month + delta
    while m < 1:
        y -= 1
        m += 12
    while m > 12:
        y += 1
        m -= 12
    return date(y, m, 1)


def render_calendar_picker(label, state_key, default_value):
    """Lightweight themed calendar made from Streamlit buttons.
    This deliberately avoids Streamlit's BaseWeb date-picker popup, which can
    inherit a dark browser/Cloud theme even when the rest of the app is light.
    """
    selected_key = f"{state_key}_selected"
    month_key = f"{state_key}_month"
    if selected_key not in st.session_state:
        st.session_state[selected_key] = default_value
    if month_key not in st.session_state:
        st.session_state[month_key] = date(default_value.year, default_value.month, 1)

    selected = st.session_state[selected_key]
    month_start = st.session_state[month_key]

    st.markdown(f'<div class="calendar-label">{label}</div>', unsafe_allow_html=True)
    nav_l, nav_title, nav_r = st.columns([0.7, 3.1, 0.7])
    with nav_l:
        if st.button("‹", key=f"{state_key}_prev", use_container_width=True):
            st.session_state[month_key] = _shift_month(month_start, -1)
            st.rerun()
    with nav_title:
        st.markdown(
            f'<div class="calendar-title">{month_start.strftime("%B %Y")}</div>',
            unsafe_allow_html=True,
        )
    with nav_r:
        if st.button("›", key=f"{state_key}_next", use_container_width=True):
            st.session_state[month_key] = _shift_month(month_start, 1)
            st.rerun()

    weekdays = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    cols = st.columns(7, gap="small")
    for col, wd in zip(cols, weekdays):
        with col:
            st.markdown(f'<div class="calendar-weekday">{wd}</div>', unsafe_allow_html=True)

    first_weekday, days_in_month = calendar.monthrange(month_start.year, month_start.month)
    # Monday-based calendar; blank cells first.
    cells = [None] * first_weekday + list(range(1, days_in_month + 1))
    while len(cells) % 7:
        cells.append(None)

    for row_start in range(0, len(cells), 7):
        cols = st.columns(7, gap="small")
        for col, day_num in zip(cols, cells[row_start:row_start + 7]):
            with col:
                if day_num is None:
                    st.markdown('<div class="calendar-empty"></div>', unsafe_allow_html=True)
                    continue
                d = date(month_start.year, month_start.month, day_num)
                label_text = f"✓ {day_num}" if d == selected else str(day_num)
                if st.button(label_text, key=f"{state_key}_day_{d.isoformat()}", use_container_width=True):
                    st.session_state[selected_key] = d
                    st.rerun()

    st.markdown(
        f'<div class="calendar-selected">Selected: <b>{selected.strftime("%d %B %Y")}</b></div>',
        unsafe_allow_html=True,
    )
    return selected


# Initialize persistent session-state containers only after helper functions exist.
ensure_progress_state()

# =========================================================
# AUTH GATE + USER RECORD
# =========================================================
# Authentication is mandatory in production. Never create a demo account.
if not auth_is_configured():
    st.error("Google authentication is not configured for this deployment.")
    st.info(
        "In Streamlit Cloud → Manage app → Settings → Secrets, "
        "configure [auth] with redirect_uri, cookie_secret, client_id, "
        "client_secret and server_metadata_url."
    )
    st.stop()

auth_user = get_auth_user()

if auth_user is None:
    st.markdown("""
    <div class='hero'>
        <div class='eyebrow'>Current Affairs Study Studio</div>
        <h1>Welcome back.</h1>
        <div class='hero-sub'>Sign in with Google to keep your streak, accuracy, mistakes and quiz history tied to your own profile.</div>
    </div>
    """, unsafe_allow_html=True)

    # Streamlit's default OIDC provider is Google in our configuration.
    if st.button("Continue with Google", type="primary", use_container_width=True):
        st.login()

    st.stop()
    
st.session_state["auth_user"] = auth_user
# Resolve the Supabase profile UUID once per login session.
_current_email = str(auth_user.get("email", "")).strip().lower()
if st.session_state.get("_profile_synced_email") != _current_email:
    db_upsert_user(auth_user)
    st.session_state["_profile_synced_email"] = _current_email
if st.session_state.get("_persistent_loaded_email") != _current_email:
    load_persistent_history()
    load_persistent_mistakes()
    st.session_state["_persistent_loaded_email"] = _current_email

# Small build marker makes it unambiguous which source file is running.
st.caption(f"App build: {APP_BUILD} · ⚡ instant UI mode")
_db_errors = {
    "Profile": st.session_state.get("supabase_profile_error"),
    "History": st.session_state.get("supabase_history_error"),
    "Mistakes": st.session_state.get("supabase_mistake_error"),
    "Progress": st.session_state.get("supabase_progress_error"),
    "Attempt": st.session_state.get("supabase_attempt_error"),
}
_db_errors = {k: v for k, v in _db_errors.items() if v}
if _db_errors:
    with st.expander("Supabase diagnostics", expanded=False):
        st.write(_db_errors)


# =========================================================
# SIDEBAR
# =========================================================
st.sidebar.markdown("# 🌿 Current Affairs")
st.sidebar.caption("A calm little place to turn daily news into long-term memory.")

with st.sidebar:
    if is_admin(auth_user["email"]):
        st.markdown("### 👑 Administration")
        if st.button("👑 Open Admin Dashboard", use_container_width=True, type="primary", key="open_admin_dashboard"):
            reset_quiz_state()
            st.session_state.app_page = "👑 Admin Dashboard"
            st.rerun()
        st.caption("Users · analytics · mistakes · controls")

    st.markdown("### Workspace")
    pages = ["🏠 Dashboard", "📅 Daily Quiz", "🎯 Custom Test", "🧠 Review Mistakes"]
    if is_admin(auth_user["email"]):
        pages.append("👑 Admin Dashboard")
    current_page = st.session_state.get("app_page", pages[0])
    if current_page not in pages:
        current_page = pages[0]
    app_page = st.radio("Go to", pages, index=pages.index(current_page), label_visibility="collapsed")
    if app_page != current_page:
        reset_quiz_state()
        st.session_state.app_page = app_page
        st.rerun()

    st.markdown("### Appearance")
    theme_labels = {"🌿 Sage": "Sage", "💜 Lavender": "Lavender", "🩵 Sky": "Sky"}
    current_label = next(k for k, v in theme_labels.items() if v == st.session_state.theme)
    selected_label = st.radio(
        "Theme",
        list(theme_labels.keys()),
        index=list(theme_labels.keys()).index(current_label),
        label_visibility="collapsed",
        key="theme_radio",
    )
    selected_theme = theme_labels[selected_label]
    if selected_theme != st.session_state.theme:
        st.session_state.theme = selected_theme
        st.rerun()

    stats = progress_stats()
    st.markdown("### Your progress")
    st.markdown(
        f"<div style='display:grid;grid-template-columns:1fr 1fr;gap:.7rem'><div><div style='color:var(--muted);font-size:.75rem'>Streak</div><div style='font-size:1.9rem;font-weight:800'>{stats['current_streak']} 🔥</div></div><div><div style='color:var(--muted);font-size:.75rem'>Accuracy</div><div style='font-size:1.9rem;font-weight:800'>{stats['accuracy']:.0f}%</div></div></div>",
        unsafe_allow_html=True,
    )
    if st.button(f"🧠 Review Mistakes ({len(st.session_state.mistake_bank)})", use_container_width=True):
        reset_quiz_state()
        st.session_state.app_page = "🧠 Review Mistakes"
        st.rerun()

    st.markdown("### Your library")
    st.metric("Questions", f"{len(df):,}")
    st.metric("Study days", f"{df['Date'].nunique():,}")
    latest = df["Date"].max()
    latest_label = latest.strftime("%d %b %Y") if isinstance(latest, date) else "—"
    st.caption(f"Latest sheet date · {latest_label}")

    st.markdown("### Profile")
    role_label = {"main_admin": "👑 Main Admin", "admin": "🛠️ Admin", "user": "👤 Student"}[user_role(auth_user["email"])]
    st.caption(f"{auth_user['name']} · {role_label}")
    if auth_is_configured() and st.button("Sign out", use_container_width=True):
        st.logout()

    if st.button("🔄 Sync Google Sheet", use_container_width=True):
        st.cache_data.clear()
        _invalidate_progress_cache()
        st.session_state.pop("_attempt_rows_cache", None)
        st.session_state.pop("_mistake_rows_cache", None)
        reset_quiz_state()
        st.rerun()

# =========================================================
_fragment = getattr(st, "fragment", lambda fn: fn)

@_fragment
def render_main_content():
    # SPECIAL ADMIN DASHBOARD
    if app_page == "👑 Admin Dashboard":
        if not is_admin(auth_user["email"]):
            st.error("You do not have permission to access the admin dashboard.")
            st.stop()

        # Admin-only visual system: deliberately avoid st.dataframe for the main
        # user table because its internal grid can inherit dark browser styles.
        st.markdown(r"""
        <style>
        .admin-hero {
            background: linear-gradient(135deg, rgba(255,255,255,.94), rgba(245,250,241,.96));
            border: 1px solid var(--border);
            border-radius: 30px;
            padding: 1.55rem 1.7rem;
            box-shadow: 0 18px 50px var(--shadow);
            margin-bottom: 1.25rem;
        }
        .admin-eyebrow { color: var(--accent-dark); font-size:.72rem; font-weight:800; letter-spacing:.16em; text-transform:uppercase; }
        .admin-title { color:var(--text); font-family:'Playfair Display',serif; font-size:clamp(2rem,4vw,3.3rem); font-weight:700; line-height:1.05; margin:.25rem 0 .45rem; }
        .admin-sub { color:var(--muted); font-size:.95rem; line-height:1.55; }
        .admin-toolbar {
            display:flex; align-items:center; justify-content:space-between; gap:1rem;
            background:rgba(255,255,255,.82); border:1px solid var(--border);
            border-radius:24px; padding:1rem 1.15rem; margin:1.25rem 0;
            box-shadow:0 10px 30px var(--shadow);
        }
        .admin-toolbar-title { color:var(--text); font-weight:800; font-size:1rem; }
        .admin-toolbar-sub { color:var(--muted); font-size:.82rem; margin-top:.15rem; }
        .admin-icon {
            width:44px; height:44px; border-radius:14px; display:flex; align-items:center; justify-content:center;
            background:var(--accent-soft); color:var(--accent-dark); font-size:1.25rem; flex:0 0 auto;
        }
        .admin-table-card {
            background:rgba(255,255,255,.88); border:1px solid var(--border); border-radius:26px;
            padding:1.15rem; box-shadow:0 16px 45px var(--shadow); overflow:hidden;
        }
        .admin-table-head { display:flex; justify-content:space-between; align-items:center; gap:1rem; margin:.1rem .2rem 1rem; }
        .admin-table-title { color:var(--text); font-weight:800; font-size:1.2rem; }
        .admin-table-sub { color:var(--muted); font-size:.82rem; }
        .admin-count { background:var(--accent-soft); color:var(--accent-dark); border:1px solid var(--border); border-radius:999px; padding:.42rem .75rem; font-size:.78rem; font-weight:800; }
        .admin-table-wrap { overflow-x:auto; border:1px solid var(--border); border-radius:18px; background:var(--surface); }
        .admin-table { width:100%; border-collapse:separate; border-spacing:0; color:var(--text); font-size:.86rem; min-width:980px; }
        .admin-table th { background:var(--accent-soft); color:var(--text); text-align:left; padding:.78rem .72rem; font-size:.72rem; text-transform:uppercase; letter-spacing:.05em; border-bottom:1px solid var(--border); white-space:nowrap; }
        .admin-table td { background:var(--surface); color:var(--text); padding:.78rem .72rem; border-bottom:1px solid var(--border); vertical-align:middle; }
        .admin-table tr:last-child td { border-bottom:0; }
        .admin-table tr:hover td { background:var(--surface2); }
        .admin-name { font-weight:700; }
        .admin-email { color:var(--muted); font-size:.8rem; }
        .admin-pill { display:inline-flex; align-items:center; border-radius:999px; padding:.3rem .58rem; font-size:.72rem; font-weight:800; white-space:nowrap; }
        .admin-pill-user { background:#eaf3ff; color:#1557a6; border:1px solid #bfd9ff; }
        .admin-pill-admin { background:#fff5df; color:#9a5a00; border:1px solid #f2d28a; }
        .admin-pill-active { background:#e8f8ef; color:#167342; border:1px solid #b9e8ca; }
        .admin-pill-disabled { background:#f3f4f6; color:#667085; border:1px solid #d7dbe0; }
        .admin-section {
            background:rgba(255,255,255,.86); border:1px solid var(--border); border-radius:26px;
            padding:1.2rem; margin-top:1.25rem; box-shadow:0 14px 40px var(--shadow);
        }
        .admin-section-title { display:flex; align-items:center; gap:.75rem; color:var(--text); font-size:1.18rem; font-weight:800; }
        .admin-section-sub { color:var(--muted); font-size:.82rem; margin:.2rem 0 1rem 3.15rem; }
        .admin-control-card { border:1px solid var(--border); border-radius:20px; padding:1rem; background:var(--surface); height:100%; }
        .admin-control-card.blue { background:linear-gradient(135deg,#eef5ff,#f8fbff); border-color:#c8dcff; }
        .admin-control-card.gold { background:linear-gradient(135deg,#fff8e7,#fffdf6); border-color:#f1d798; }
        .admin-control-card.red { background:linear-gradient(135deg,#fff0f2,#fffafb); border-color:#f2c5cc; }
        .admin-control-label { font-weight:800; color:var(--text); }
        .admin-control-copy { color:var(--muted); font-size:.8rem; margin-top:.2rem; }
        .admin-tip { margin-top:1rem; padding:.8rem 1rem; border-radius:16px; background:#edf6ff; border:1px solid #c7e0ff; color:#174f8c; font-size:.82rem; }

        /* =====================================================
           CUSTOM USER SELECTOR STYLES
           These styles create the light selector/popover so the
           Admin Dashboard never falls back to a black BaseWeb box.
           ===================================================== */
        .admin-custom-select-label {
            margin:.85rem 0 .4rem;
            color:var(--muted);
            font-size:.72rem;
            font-weight:900;
            letter-spacing:.09em;
            text-transform:uppercase;
        }
        .admin-selected-role {
            margin-left:auto;
            padding:.3rem .65rem;
            border-radius:999px;
            background:#edf7ee;
            color:#2f7540;
            border:1px solid #c8e6ce;
            font-size:.7rem;
            font-weight:800;
            white-space:nowrap;
        }
        .admin-popover-title {
            font-size:1rem;
            font-weight:850;
            color:var(--text);
            margin-bottom:.15rem;
        }
        .admin-popover-sub {
            color:var(--muted);
            font-size:.78rem;
            margin-bottom:.7rem;
        }
        /* Make the popover trigger itself look like our custom field. */
        div[data-testid="stPopover"] > button {
            width:100% !important;
            min-height:54px !important;
            justify-content:flex-start !important;
            text-align:left !important;
            background:linear-gradient(135deg,#ffffff,#f7faf4) !important;
            color:#24352a !important;
            border:1px solid #d5e1d0 !important;
            border-radius:16px !important;
            box-shadow:0 6px 18px rgba(45,66,50,.06) !important;
            font-weight:750 !important;
        }
        div[data-testid="stPopover"] > button:hover {
            background:#f1f7eb !important;
            border-color:#9ebd7a !important;
        }
        /* Popover surface and its buttons stay light as well. */
        div[data-testid="stPopoverBody"],
        div[data-baseweb="popover"] {
            background:#ffffff !important;
        }
        div[data-testid="stPopoverBody"] div[data-testid="stButton"] > button {
            min-height:48px !important;
            justify-content:flex-start !important;
            text-align:left !important;
            background:#ffffff !important;
            color:#24352a !important;
            border:1px solid #e0e8df !important;
            border-radius:13px !important;
            margin:.2rem 0 !important;
            box-shadow:none !important;
        }
        div[data-testid="stPopoverBody"] div[data-testid="stButton"] > button:hover {
            background:#f1f7eb !important;
            border-color:#a7c28a !important;
        }

        .admin-picker-card {
            margin-top:1.1rem; padding:1.15rem; border:1px solid var(--border); border-radius:24px;
            background:rgba(255,255,255,.88); box-shadow:0 12px 34px var(--shadow);
        }
        .admin-picker-label { font-size:.78rem; font-weight:900; letter-spacing:.08em; color:var(--text); }
        .admin-picker-sub { color:var(--muted); font-size:.86rem; margin:.25rem 0 1rem; }
        .admin-picker-grid { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:.9rem; }
        .admin-user-chip { min-height:112px; padding:1rem; border:1px solid #dfe7df; border-radius:20px; background:linear-gradient(145deg,#ffffff,#f7faf6); display:flex; align-items:center; gap:.75rem; box-shadow:0 7px 22px rgba(45,66,50,.07); }
        .admin-user-chip.selected { border:2px solid #8aa65a; background:linear-gradient(145deg,#f4f8ed,#ffffff); box-shadow:0 9px 26px rgba(111,142,70,.14); }
        .admin-user-chip-avatar { width:42px; height:42px; border-radius:14px; display:flex; align-items:center; justify-content:center; background:#eaf1df; font-size:1.35rem; flex:0 0 auto; }
        .admin-user-chip-name { color:var(--text); font-weight:850; font-size:.94rem; line-height:1.25; }
        .admin-user-chip-email { color:var(--muted); font-size:.74rem; margin-top:.22rem; overflow-wrap:anywhere; }
        .admin-user-chip-role { margin-left:auto; align-self:flex-start; padding:.28rem .5rem; border-radius:999px; background:#edf5ff; color:#2563a6; border:1px solid #c9ddf7; font-size:.68rem; font-weight:800; white-space:nowrap; }
        .admin-user-chip.selected .admin-user-chip-role { background:#fff4dc; color:#a26000; border-color:#f0d28f; }
        .admin-selected-user { display:flex; align-items:center; gap:.55rem; margin:1rem 0 .75rem; padding:.72rem .9rem; border-radius:16px; background:#f3f8ed; border:1px solid #d9e6cb; color:var(--muted); }
        .admin-selected-user strong { color:var(--text); }
        /* Selection/action buttons are intentionally light and rounded. */
        div[data-testid="stButton"] > button { background:#ffffff !important; color:#24352a !important; border:1px solid #d8e3d8 !important; border-radius:14px !important; box-shadow:0 4px 12px rgba(45,66,50,.05) !important; font-weight:750 !important; }
        div[data-testid="stButton"] > button:hover { background:#f3f8ed !important; color:#31552e !important; border-color:#9fba82 !important; }
        div[data-testid="stButton"] > button:focus:not(:active) { box-shadow:0 0 0 3px rgba(137,166,91,.18) !important; }
        @media (max-width: 900px) { .admin-picker-grid { grid-template-columns:1fr; } }

        /* Force Streamlit's remaining admin inputs to stay light. */
        div[data-testid="stTextInput"] input,
        div[data-testid="stSelectbox"] div[data-baseweb="select"] > div,
        div[data-testid="stSelectbox"] div[data-baseweb="select"] input {
            background:#ffffff !important; color:#18251d !important; border-color:#cbd8cc !important;
        }
        div[data-testid="stTextInput"] input::placeholder { color:#81908a !important; opacity:1 !important; }
        div[data-testid="stSelectbox"] svg { fill:#56705f !important; }
        [data-baseweb="popover"] [role="listbox"], [data-baseweb="menu"] { background:#ffffff !important; color:#18251d !important; }
        [data-baseweb="popover"] [role="option"] { background:#ffffff !important; color:#18251d !important; }
        [data-baseweb="popover"] [role="option"]:hover { background:#eef5eb !important; }
        </style>
        """, unsafe_allow_html=True)

        st.markdown(
            f"""<div class='admin-hero'>
                <div class='admin-eyebrow'>👑 ADMIN CONTROL ROOM</div>
                <div class='admin-title'>Your special dashboard.</div>
                <div class='admin-sub'>A clean command center for your study community — manage accounts, inspect learning progress, find difficult questions and control user study data.</div>
            </div>""",
            unsafe_allow_html=True,
        )

        users = db_all_users()
        total_users = len(users)
        active_users = sum(int(bool(r[3])) for r in users)
        main_admins = sum(1 for r in users if r[2] == "main_admin")
        admins = sum(1 for r in users if r[2] == "admin")

        try:
            all_attempt_rows = _cached_sb_all_rows("quiz_attempts") if supabase else []
        except Exception:
            all_attempt_rows = []
        try:
            all_answer_rows = _cached_sb_all_rows("answers") if supabase else []
        except Exception:
            all_answer_rows = []

        total_attempts = len(all_attempt_rows)
        total_answers = len(all_answer_rows)
        correct_answers = sum(1 for r in all_answer_rows if bool(r.get("is_correct")))
        attempted_answers = sum(1 for r in all_answer_rows if not bool(r.get("skipped")))
        global_accuracy = (correct_answers / attempted_answers * 100) if attempted_answers else 0.0

        k1, k2, k3, k4 = st.columns(4, gap="medium")
        with k1: render_metric("Total accounts", total_users, "Google accounts / profiles")
        with k2: render_metric("Registered users", max(0, total_users - main_admins - admins), "Non-admin users")
        with k3: render_metric("Total quizzes", total_attempts, "Across all users")
        with k4: render_metric("Overall accuracy", f"{global_accuracy:.0f}%", f"{correct_answers:,} correct / {attempted_answers:,} attempted")

        # Search / filter toolbar
        st.markdown("<div class='admin-toolbar'><div style='display:flex;align-items:center;gap:.8rem'><div class='admin-icon'>🔎</div><div><div class='admin-toolbar-title'>Search users</div><div class='admin-toolbar-sub'>Search by name or email to quickly find a user</div></div></div></div>", unsafe_allow_html=True)
        search = st.text_input("Search users", placeholder="Type a name or email…", key="admin_user_search", label_visibility="collapsed")
        rows = users
        if search.strip():
            q = search.strip().lower()
            rows = [r for r in rows if q in str(r[0]).lower() or q in str(r[1]).lower()]

        # Beautiful HTML table — no dark Streamlit dataframe.
        table_rows = []
        for idx, (email, name, role, active, created, last_seen) in enumerate(rows):
            stt = db_user_stats(email)
            role_label = role.replace("_", " ").title()
            role_cls = "admin-pill-admin" if role in {"admin", "main_admin"} else "admin-pill-user"
            status_cls = "admin-pill-active" if active else "admin-pill-disabled"
            safe_name = html.escape(str(name or "—"))
            safe_email = html.escape(str(email or "—"))
            safe_role = html.escape(role_label)
            safe_last = html.escape(str(last_seen or "—"))
            table_rows.append(f"""
            <tr>
              <td>{idx}</td>
              <td><div class='admin-name'>{safe_name}</div></td>
              <td><div class='admin-email'>{safe_email}</div></td>
              <td><span class='admin-pill {role_cls}'>{safe_role}</span></td>
              <td><span class='admin-pill {status_cls}'>{'Active' if active else 'Disabled'}</span></td>
              <td>{stt['quizzes']}</td><td>{stt['accuracy']:.0f}%</td><td>{stt['mistakes']}</td><td>{stt['total']}</td>
              <td>{safe_last}</td>
            </tr>""")

        if table_rows:
            st.markdown(f"""
            <div class='admin-table-card'>
              <div class='admin-table-head'>
                <div><div class='admin-table-title'>👥 Registered Users</div><div class='admin-table-sub'>All users who have signed in to the study dashboard</div></div>
                <div class='admin-count'>👥 {len(rows)} user{'s' if len(rows)!=1 else ''}</div>
              </div>
              <div class='admin-table-wrap'>
                <table class='admin-table'>
                  <thead><tr><th>#</th><th>Name</th><th>Email</th><th>Role</th><th>Status</th><th>Quizzes</th><th>Accuracy</th><th>Mistakes</th><th>Questions</th><th>Last seen</th></tr></thead>
                  <tbody>{''.join(table_rows)}</tbody>
                </table>
              </div>
            </div>
            """, unsafe_allow_html=True)
        else:
            st.info("No users found.")

        # =========================================================
        # ADMIN USER CONTROL: SIMPLE OPEN/CLOSE USER SELECTOR
        # This uses the same selection approach as the reference code:
        # click the box -> choose a user -> the box closes.
        # The existing CSS keeps the selector light and matches the app theme.
        # =========================================================

        # Build the list of available user emails from the Supabase profiles.
        # The email is the internal value we use when loading that user's data.
        email_choices = [
            str(r[0]).strip().lower()
            for r in users
            if r and r[0]
        ] if users else [auth_user["email"]]

        # Build a display map so the dropdown shows:
        # "Name — email" instead of only the email address.
        user_display = {
            str(r[0]).strip().lower():
                f"{str(r[1] or 'User')} — {str(r[0]).strip().lower()}"
            for r in users
            if r and r[0]
        }

        # Safety fallback in case the database returns no users.
        if not email_choices:
            email_choices = [auth_user["email"]]
            user_display = {
                auth_user["email"]:
                    f"{auth_user.get('name', 'User')} — {auth_user['email']}"
            }

        # IMPORTANT: keep the current selection valid after a user is
        # deleted or when the user list changes.
        if st.session_state.get("admin_manage_user") not in email_choices:
            st.session_state.admin_manage_user = email_choices[0]

        # ---------------------------------------------------------
        # THE USER SELECTOR
        # This is intentionally a normal Streamlit selectbox, because
        # this gives the exact familiar "open -> select -> close" UX
        # requested, while our CSS controls its light appearance.
        # ---------------------------------------------------------
        email_to_manage = st.selectbox(
            "Choose a user",
            email_choices,
            key="admin_manage_user",
            format_func=lambda e: user_display.get(
                str(e).strip().lower(),
                str(e).strip().lower()
            ),
        )

        selected_role = user_role(email_to_manage)
        user_stat = db_user_stats(email_to_manage)

        u1, u2, u3, u4 = st.columns(4, gap="medium")
        with u1: render_metric("Quizzes", user_stat["quizzes"], "Completed sessions")
        with u2: render_metric("Accuracy", f"{user_stat['accuracy']:.0f}%", "Attempted answers")
        with u3: render_metric("Mistakes", user_stat["mistakes"], "Current review bank")
        with u4: render_metric("Questions", user_stat["total"], "Across attempts")

        c1, c2, c3 = st.columns(3, gap="medium")
        with c1:
            st.markdown("<div class='admin-control-card blue'><div class='admin-icon'>📊</div><div class='admin-control-label'>View detailed progress</div><div class='admin-control-copy'>See quizzes, accuracy and mistakes.</div></div>", unsafe_allow_html=True)
            if st.button("View progress", use_container_width=True, key="admin_view_history"):
                try:
                    hist_rows = _sb_user_rows("quiz_attempts", email_to_manage, order=("completed_at", True))[:100]
                    st.session_state.admin_history = pd.DataFrame(hist_rows)
                except Exception as exc:
                    st.error(f"Could not load detailed history: {_sb_error(exc)}")
        with c2:
            st.markdown("<div class='admin-control-card gold'><div class='admin-icon'>↻</div><div class='admin-control-label'>Reset user progress</div><div class='admin-control-copy'>Clear all study data for this user.</div></div>", unsafe_allow_html=True)
            cannot_reset = email_to_manage in ALL_ADMIN_EMAILS
            if st.button("Reset progress", use_container_width=True, disabled=cannot_reset, key="admin_reset_button"):
                st.session_state.admin_reset_confirm = email_to_manage
            if cannot_reset: st.caption("Admin accounts cannot be reset.")
        with c3:
            st.markdown("<div class='admin-control-card red'><div class='admin-icon'>🗑️</div><div class='admin-control-label'>Delete user</div><div class='admin-control-copy'>Remove this user's profile and all study data from the app.</div></div>", unsafe_allow_html=True)
            cannot_delete = email_to_manage in ALL_ADMIN_EMAILS or email_to_manage == "demo@local"
            if st.button("🗑️ Delete user", use_container_width=True, disabled=cannot_delete, key="admin_delete_button"):
                st.session_state.admin_delete_confirm = email_to_manage
            if email_to_manage in ALL_ADMIN_EMAILS:
                st.caption("Admin accounts cannot be deleted.")
            elif email_to_manage == "demo@local":
                st.caption("The local demo account cannot be deleted.")

        st.markdown("<div class='admin-tip'><b>💡 Tip:</b> Select a user above to view detailed statistics, reset progress, or delete the user's app data. Admin accounts are protected.</div>", unsafe_allow_html=True)

        if st.session_state.get("admin_delete_confirm") == email_to_manage:
            st.error(f"Delete **{email_to_manage}** permanently? This removes the profile, quiz attempts, answers, mistakes, activity and achievements stored by this app. The Google account itself is not deleted.")
            yes_del, no_del = st.columns(2)
            with yes_del:
                if st.button("Yes, delete user permanently", type="primary", use_container_width=True, key="admin_delete_yes"):
                    try:
                        db_delete_user(email_to_manage)
                        _clear_supabase_read_cache()
                        st.session_state.admin_delete_confirm = None
                        st.session_state.admin_history = None
                        st.success("User deleted from the study dashboard.")
                        st.rerun(scope="fragment")
                    except Exception as exc:
                        st.error(f"Delete failed: {_sb_error(exc)}")
            with no_del:
                if st.button("Cancel", use_container_width=True, key="admin_delete_no"):
                    st.session_state.admin_delete_confirm = None
                    st.rerun(scope="fragment")

        if st.session_state.get("admin_reset_confirm") == email_to_manage:
            st.warning("This permanently clears this user's quiz attempts, answers and mistake bank. It does not delete their Google account.")
            yes, no = st.columns(2)
            with yes:
                if st.button("Yes, reset permanently", type="primary", use_container_width=True, key="admin_reset_yes"):
                    try:
                        db_reset_user_progress(email_to_manage)
                        _clear_supabase_read_cache()
                        st.session_state.admin_reset_confirm = None
                        st.session_state.admin_history = None
                        st.success("Progress reset successfully.")
                        st.rerun(scope="fragment")
                    except Exception as exc:
                        st.error(f"Reset failed: {_sb_error(exc)}")
            with no:
                if st.button("Cancel", use_container_width=True, key="admin_reset_no"):
                    st.session_state.admin_reset_confirm = None
                    st.rerun(scope="fragment")

        if isinstance(st.session_state.get("admin_history"), pd.DataFrame):
            st.markdown("<div class='admin-section'><div class='admin-section-title'>📋 Recent quiz history</div></div>", unsafe_allow_html=True)
            hist = st.session_state.admin_history.copy()
            preferred = [c for c in ["completed_at", "mode", "correct", "incorrect", "skipped", "accuracy"] if c in hist.columns]
            # Use HTML table for history too, preventing dark dataframe styling.
            if preferred and not hist.empty:
                hrows=[]
                for _, rr in hist[preferred].head(50).iterrows():
                    hrows.append("<tr>" + "".join(f"<td>{html.escape(str(v))}</td>" for v in rr.tolist()) + "</tr>")
                heads="".join(f"<th>{html.escape(str(c).replace('_',' ').title())}</th>" for c in preferred)
                st.markdown(f"<div class='admin-table-card'><div class='admin-table-wrap'><table class='admin-table'><thead><tr>{heads}</tr></thead><tbody>{''.join(hrows)}</tbody></table></div></div>", unsafe_allow_html=True)

        st.markdown("<div class='admin-section'><div class='admin-section-title'>🔥 Difficult questions</div><div class='admin-section-sub'>Questions with the highest number of incorrect answers</div></div>", unsafe_allow_html=True)
        if all_answer_rows:
            raw = pd.DataFrame(all_answer_rows)
            qcol = next((c for c in ["question", "question_text", "question_key", "question_id"] if c in raw.columns), None)
            if qcol and "is_correct" in raw.columns:
                raw["is_correct"] = raw["is_correct"].fillna(False).astype(bool)
                if "skipped" in raw.columns: raw = raw[~raw["skipped"].fillna(False).astype(bool)]
                hard = raw.groupby(qcol, as_index=False).agg(attempts=("is_correct", "size"), misses=("is_correct", lambda x: int((~x.astype(bool)).sum()))).sort_values(["misses", "attempts"], ascending=[False, False]).head(15)
                if not hard.empty:
                    hard["Miss rate"]=(hard["misses"]/hard["attempts"]*100).round(0).astype(int).astype(str)+"%"
                    hard=hard.rename(columns={qcol:"Question"})
                    hrows=[]
                    for _,rr in hard[["Question","attempts","misses","Miss rate"]].iterrows(): hrows.append("<tr>"+"".join(f"<td>{html.escape(str(v))}</td>" for v in rr.tolist())+"</tr>")
                    st.markdown(f"<div class='admin-table-card'><div class='admin-table-wrap'><table class='admin-table'><thead><tr><th>Question</th><th>Attempts</th><th>Misses</th><th>Miss rate</th></tr></thead><tbody>{''.join(hrows)}</tbody></table></div></div>", unsafe_allow_html=True)
                else: st.info("No answer history yet.")
            else: st.info("The answers table does not contain a recognizable question field yet.")
        else: st.info("No answer history yet. Complete a quiz to populate this section.")

        st.markdown("<div class='admin-section'><div class='admin-section-title'>🛠️ Administration status</div><div class='admin-section-sub'>Current role configuration</div></div>", unsafe_allow_html=True)
        s1,s2,s3=st.columns(3,gap="medium")
        with s1: render_metric("Main admin", 1, MAIN_ADMIN_EMAIL)
        with s2: render_metric("Other admins", len(ADMIN_EMAILS), "Configured admin accounts")
        with s3: render_metric("Active users", active_users, f"{admins + main_admins} admin accounts")

    # DASHBOARD HOME
    # =========================================================
    if app_page == "🏠 Dashboard" and "quiz_questions" not in st.session_state:
        latest = df["Date"].max()
        oldest = df["Date"].min()
        total_days = df["Date"].nunique()
        avg_per_day = len(df) / total_days if total_days else 0

        st.markdown(
            f"""
            <div class="hero">
                <div class="eyebrow">Current Affairs Study Studio</div>
                <h1>Know more. Remember longer.</h1>
                <div class="hero-sub">
                    A beautiful, distraction-light dashboard for turning your current-affairs sheet into quick daily quizzes,
                    focused tests, and measurable progress.
                </div>
                <div class="hero-badge">🌱 {len(df):,} questions · {total_days:,} study days · Latest update {latest.strftime('%d %b %Y')}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        render_progress_strip()

        c1, c2, c3, c4 = st.columns(4)
        with c1:
            render_metric("Question bank", f"{len(df):,}", "Questions ready to practise")
        with c2:
            render_metric("Study days", f"{total_days:,}", "Days represented in the sheet")
        with c3:
            render_metric("Avg / day", f"{avg_per_day:.1f}", "Questions per available day")
        with c4:
            span = (latest - oldest).days + 1 if latest and oldest else 0
            render_metric("Coverage", f"{span:,}d", "Calendar span in your data")

        st.markdown('<div class="section-head"><div class="title">Choose your study rhythm</div><div class="hint">Start in one click</div></div>', unsafe_allow_html=True)
        a, b, c = st.columns(3)
        with a:
            st.markdown('<div class="card"><div style="font-size:2rem">☀️</div><h3>Daily Quiz</h3><p style="color:var(--muted)">Focus on one date. Perfect for a 10–15 minute routine.</p></div>', unsafe_allow_html=True)
            if st.button("Open Daily Quiz →", key="go_daily", use_container_width=True, type="primary"):
                st.session_state.app_page = "📅 Daily Quiz"
                st.rerun()
        with b:
            st.markdown('<div class="card"><div style="font-size:2rem">🎯</div><h3>Custom Test</h3><p style="color:var(--muted)">Mix multiple dates and guarantee date coverage.</p></div>', unsafe_allow_html=True)
            if st.button("Build a Test →", key="go_custom", use_container_width=True):
                st.session_state.app_page = "🎯 Custom Test"
                st.rerun()
        with c:
            recent_dates = sorted(df["Date"].unique())[-7:]
            recent_count = len(df[df["Date"].isin(recent_dates)])
            st.markdown(f'<div class="card"><div style="font-size:2rem">✨</div><h3>7-day pulse</h3><p style="color:var(--muted)">{recent_count:,} questions across the latest {len(recent_dates)} available study days.</p></div>', unsafe_allow_html=True)
            st.caption("A small snapshot, not a performance score.")

        if st.session_state.mistake_bank:
            st.markdown(f'<div class="tip">🧠 <b>{len(st.session_state.mistake_bank)} question{"s" if len(st.session_state.mistake_bank) != 1 else ""} waiting for review.</b> Practice mistakes regularly and they will disappear automatically when you get them right.</div>', unsafe_allow_html=True)
            if st.button("Open Review Mistakes →", use_container_width=True):
                st.session_state.app_page = "🧠 Review Mistakes"
                st.rerun()

        st.markdown('<div class="section-head"><div class="title">Your question bank at a glance</div><div class="hint">Distribution by date</div></div>', unsafe_allow_html=True)
        daily_counts = df.groupby("Date").size().reset_index(name="Questions")
        fig = go.Figure(go.Bar(x=daily_counts["Date"], y=daily_counts["Questions"], marker_color=T["accent"]))
        fig.update_layout(
            height=320,
            margin=dict(l=10, r=10, t=10, b=10),
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            font=dict(color=T["muted"]),
            xaxis=dict(showgrid=False, title=""),
            yaxis=dict(showgrid=True, gridcolor=T["border"], title="Questions"),
        )
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

        st.markdown('<div class="tip">💡 <b>Study tip:</b> a small daily quiz is usually easier to sustain than occasional marathon sessions. Aim for consistency first.</div>', unsafe_allow_html=True)

    # =========================================================
    # DAILY QUIZ SETUP
    # =========================================================
    elif app_page == "📅 Daily Quiz" and "quiz_questions" not in st.session_state:
        available_dates = sorted(df["Date"].dropna().unique())
        default_date = available_dates[-1] if available_dates else date.today()

        st.markdown('<div class="eyebrow">Daily practice</div>', unsafe_allow_html=True)
        st.title("Your daily knowledge reset")
        st.write("Pick a date and turn that day's current affairs into a focused quiz.")

        left, right = st.columns([1.15, 1])
        with left:
            selected_date = render_calendar_picker("Study date", "daily_calendar", default_date)
            filtered_df = df[df["Date"] == selected_date].copy()
            if filtered_df.empty:
                st.warning("No questions are available for this date.")
                if available_dates:
                    nearest = min(available_dates, key=lambda d: abs((d - selected_date).days))
                    st.info(f"Nearest available date: **{nearest.strftime('%d %B %Y')}**")
            else:
                st.success(f"{len(filtered_df)} questions are ready for {selected_date.strftime('%d %B %Y')}.")
                if st.button("🌿 Start Daily Quiz", type="primary", use_container_width=True):
                    initialize_quiz(filtered_df, "All", len(filtered_df))
                    st.rerun(scope="fragment")
        with right:
            count = len(filtered_df) if not filtered_df.empty else 0
            st.markdown(
                f"""
                <div class="card">
                    <div class="metric-label">Today’s study card</div>
                    <div class="metric-value">{count} <span style="font-size:1rem;color:var(--muted)">questions</span></div>
                    <p style="color:var(--muted);margin-top:.4rem">All available questions for the selected date, shuffled into a fresh session.</p>
                    <div class="tip">🧠 <b>Memory rule:</b> answer first, then read the explanation before moving on.</div>
                </div>
                """,
                unsafe_allow_html=True,
            )

    # =========================================================
    # CUSTOM TEST SETUP
    # =========================================================
    elif app_page == "🎯 Custom Test" and "quiz_questions" not in st.session_state:
        st.markdown('<div class="eyebrow">Build your session</div>', unsafe_allow_html=True)
        st.title("Design a test that fits your time")
        st.write("Choose a date range, question count, and let the app keep every available day represented.")

        min_date, max_date = df["Date"].min(), df["Date"].max()
        c1, c2 = st.columns(2)
        with c1:
            start_date = render_calendar_picker("Start date", "custom_start_calendar", min_date)
        with c2:
            end_date = render_calendar_picker("End date", "custom_end_calendar", max_date)

        if start_date > end_date:
            st.error("Start date must be on or before end date.")
            st.stop()

        custom_df = df[(df["Date"] >= start_date) & (df["Date"] <= end_date)].copy()
        available_days = custom_df["Date"].nunique()
        available_questions = len(custom_df)

        st.markdown('<div class="section-head"><div class="title">Test shape</div><div class="hint">Balanced across selected days</div></div>', unsafe_allow_html=True)
        c1, c2, c3 = st.columns(3)
        with c1:
            render_metric("Available", f"{available_questions:,}", "Questions in range")
        with c2:
            render_metric("Days", f"{available_days:,}", "Dates in range")
        with c3:
            render_metric("Coverage rule", "Adaptive", "1 per day only when requested > days")

        q_mode = st.radio("Question quantity", ["Custom Number", "All"], horizontal=True)
        if q_mode == "Custom Number":
            requested = st.number_input(
                "Number of questions",
                min_value=1,
                max_value=max(1, available_questions),
                value=min(15, max(1, available_questions)),
                step=1,
            )
            actual_num = int(requested)
            if actual_num > available_days:
                st.info(
                    f"Your test will include at least **1 question from each of the {available_days} study days**, "
                    f"then fill the remaining **{actual_num - available_days}** questions randomly."
                )
            else:
                st.info(
                    f"Your test will contain exactly **{actual_num} questions** from **{actual_num} randomly selected study days** "
                    f"(1 question per selected day)."
                )
        else:
            actual_num = "All"
            st.info(f"You’ll practise all **{available_questions}** questions in the selected range.")

        if st.button("🎯 Generate My Test", type="primary", use_container_width=True):
            if custom_df.empty:
                st.error("No questions are available in this date range.")
            else:
                initialize_quiz(custom_df, q_mode, actual_num)
                st.rerun(scope="fragment")

    # =========================================================
    # REVIEW MISTAKES
    # =========================================================
    elif app_page == "🧠 Review Mistakes" and "quiz_questions" not in st.session_state:
        mistakes = list(st.session_state.mistake_bank.values())
        st.markdown('<div class="eyebrow">Memory repair</div>', unsafe_allow_html=True)
        st.title("Review your mistakes")
        st.write("Questions you missed or skipped stay here until you answer them correctly.")
        render_progress_strip()

        if not mistakes:
            st.info("Your mistakes bank is empty. Take a quiz first, and anything you miss will land here for practice.")
            if st.button("📅 Start a Daily Quiz", type="primary", use_container_width=True):
                st.session_state.app_page = "📅 Daily Quiz"
                st.rerun()
        else:
            c1, c2, c3 = st.columns(3)
            with c1: render_metric("Needs review", f"{len(mistakes)}", "Questions waiting in your bank")
            with c2: render_metric("Most missed", f"{max(int(x.get('times_missed', 1)) for x in mistakes)}×", "Highest miss count")
            with c3: render_metric("Goal", "100%", "Clear the bank by mastering them")

            if st.button("🧠 Practice all mistakes", type="primary", use_container_width=True):
                initialize_quiz_from_records(mistakes)
                st.rerun(scope="fragment")

            st.markdown('<div class="section-head"><div class="title">Your mistake bank</div><div class="hint">Correct answers remove items from this list.</div></div>', unsafe_allow_html=True)
            for item in sorted(mistakes, key=lambda x: (-int(x.get("times_missed", 1)), x["date"])):
                last = "Skipped" if item.get("last_answer") is None else f"Your answer: {item['last_answer']}"
                st.markdown(
                    f"""
                    <div class="mistake-card">
                        <div style="display:flex;justify-content:space-between;gap:.6rem;align-items:center;flex-wrap:wrap">
                            <span class="mistake-tag">Needs review · {item.get('times_missed',1)}×</span>
                            <span style="color:var(--muted);font-size:.78rem">{item['date'].strftime('%d %b %Y')}</span>
                        </div>
                        <div style="font-weight:800;font-size:1rem;line-height:1.45;margin-top:.65rem">{item['question']}</div>
                        <div class="review-answer"><b>Correct:</b> {item['correct']}</div>
                        <div class="review-answer"><b>Last attempt:</b> {last}</div>
                        <div style="color:var(--muted);font-size:.84rem;line-height:1.55;margin-top:.55rem"><b>Explanation:</b> {item['explanation']}</div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

    # =========================================================
    # QUIZ ENGINE
    # =========================================================
    elif "quiz_questions" in st.session_state:
        questions = st.session_state.quiz_questions
        total_q = len(questions)
        curr_idx = st.session_state.current_index

        if st.session_state.quiz_completed:
            record_quiz_result(questions)
            if not st.session_state.get("celebration_done", False):
                st.balloons()
                st.session_state.celebration_done = True

            correct, incorrect, skipped, attended = answer_counts(questions)
            accuracy = correct / attended * 100 if attended else 0

            st.markdown('<div class="eyebrow">Session complete</div>', unsafe_allow_html=True)
            st.title("You made it to the finish line. 🏁")
            st.write("Here’s the part worth reviewing: not just your score, but where your memory is strongest and weakest.")

            c1, c2, c3, c4, c5 = st.columns(5)
            with c1: render_metric("Score", f"{correct}/{total_q}", "Correct answers")
            with c2: render_metric("Accuracy", f"{accuracy:.1f}%", "Among attempted questions")
            with c3: render_metric("Attempted", f"{attended}", "Questions answered")
            with c4: render_metric("Skipped", f"{skipped}", "Questions left blank")
            with c5: render_metric("Wrong", f"{incorrect}", "Needs another look")

            fig = go.Figure(go.Pie(
                values=[correct, incorrect, skipped] if total_q else [1],
                labels=["Correct", "Incorrect", "Skipped"],
                hole=0.72,
                textinfo="none",
                marker_colors=[T["accent"], T["danger"], T["border"]],
            ))
            fig.update_layout(
                height=300,
                margin=dict(l=10, r=10, t=15, b=10),
                paper_bgcolor="rgba(0,0,0,0)",
                showlegend=True,
                legend=dict(orientation="h", x=0.5, xanchor="center", y=-0.05),
                annotations=[dict(text=f"{accuracy:.0f}%", x=0.5, y=0.5, showarrow=False, font_size=30, font_color=T["text"])],
            )
            st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

            # Review queue: wrong/skipped first.
            weak = [q for q in questions if q["user_answer"] != q["correct"]]
            st.markdown('<div class="section-head"><div class="title">Review queue</div><div class="hint">Your most useful next 3 questions</div></div>', unsafe_allow_html=True)
            if weak:
                for q in weak[:3]:
                    result = "Skipped" if q["user_answer"] is None else "Incorrect"
                    st.markdown(
                        f"""
                        <div class="card" style="margin-bottom:.8rem">
                            <div class="q-meta"><span class="pill">{q['date'].strftime('%d %b %Y')}</span><span class="pill">{result}</span></div>
                            <div style="font-weight:700;font-size:1rem">{q['question']}</div>
                            <div style="color:var(--muted);margin-top:.5rem"><b>Correct:</b> {q['correct']}</div>
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )
            else:
                st.success("Perfect session. No mistakes or skips to review. 🌱")

            st.markdown('<div class="section-head"><div class="title">What next?</div><div class="hint">Keep your momentum</div></div>', unsafe_allow_html=True)
            c1, c2, c3 = st.columns(3)
            with c1:
                if st.button("🔁 Reattempt", type="primary", use_container_width=True):
                    if st.session_state.get("review_mode"):
                        initialize_quiz_from_records(questions)
                    else:
                        initialize_quiz(st.session_state.source_df, st.session_state.q_mode, st.session_state.num_q)
                    st.rerun(scope="fragment")
            with c2:
                if st.button("🏠 Back to Dashboard", use_container_width=True):
                    reset_quiz_state()
                    st.session_state.app_page = "🏠 Dashboard"
                    st.rerun()
            with c3:
                if st.button("🎯 New Custom Test", use_container_width=True):
                    reset_quiz_state()
                    st.session_state.app_page = "🎯 Custom Test"
                    st.rerun()

        else:
            q_data = questions[curr_idx]
            done_count = sum(q["locked"] for q in questions)
            pct = (curr_idx + 1) / total_q

            top_left, top_right = st.columns([3, 1])
            with top_left:
                st.markdown(f"**Question {curr_idx + 1} of {total_q}**")
                st.progress(pct, text=f"{done_count}/{total_q} answered")
            with top_right:
                if st.button("✕ Exit quiz", use_container_width=True):
                    reset_quiz_state()
                    st.session_state.app_page = "🏠 Dashboard"
                    st.rerun()

            # Main quiz + navigator rail.
            q_col, nav_col = st.columns([3.5, 1])

            with q_col:
                st.markdown('<div class="quiz-shell">', unsafe_allow_html=True)
                st.markdown(
                    f"<div class='q-meta'><span class='pill'>📅 {q_data['date'].strftime('%d %b %Y')}</span><span class='pill'>🌿 {st.session_state.get('q_mode', 'Quiz')}</span></div>",
                    unsafe_allow_html=True,
                )
                st.markdown(f"<div class='question-text'>{q_data['question']}</div>", unsafe_allow_html=True)

                if q_data["locked"]:
                    selected_index = q_data["options"].index(q_data["user_answer"]) if q_data["user_answer"] in q_data["options"] else None
                    st.radio("Answer", q_data["options"], index=selected_index, disabled=True, key=f"locked_{curr_idx}", label_visibility="collapsed")

                    if q_data["user_answer"] is None:
                        st.warning("You skipped this question. Your answer is now locked.")
                    elif q_data["user_answer"] == q_data["correct"]:
                        st.success("✅ Correct! Your answer is locked. Read the explanation before moving on.")
                    else:
                        st.error(f"❌ Incorrect. Your answer is locked: **{q_data['user_answer']}**")

                    # The explanation belongs to THIS question. It stays visible while
                    # the answer controls are disabled, and the learner must press Next
                    # to advance.
                    st.markdown(
                        f"""
                        <div class="card" style="margin-top:1rem;background:var(--surface2);border-left:4px solid var(--accent)">
                            <div class="metric-label">📖 Explanation for this question</div>
                            <div style="font-weight:700;margin:.35rem 0 .55rem">✅ Correct answer: {q_data['correct']}</div>
                            <div style="color:var(--muted);line-height:1.65">{q_data['explanation']}</div>
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )

                    st.markdown("<div style='height:.9rem'></div>", unsafe_allow_html=True)
                    c1, c2, c3 = st.columns(3)
                    with c1:
                        if curr_idx > 0 and st.button("← Previous", use_container_width=True):
                            st.session_state.current_index -= 1
                            st.rerun(scope="fragment")
                    with c2:
                        st.caption("Answer locked • Explanation shown")
                    with c3:
                        if curr_idx < total_q - 1:
                            if st.button("Next →", type="primary", use_container_width=True):
                                st.session_state.current_index += 1
                                st.rerun(scope="fragment")
                        else:
                            if st.button("View Results 🏁", type="primary", use_container_width=True):
                                st.session_state.quiz_completed = True
                                st.rerun(scope="fragment")
                else:
                    # Answering a question is a two-step interaction:
                    # 1) choose an option and press Next/Check Answer
                    # 2) the same question becomes locked and its explanation appears
                    #    before the learner can move to the next question.
                    options_col, explanation_col = st.columns([1.45, 1], gap="large")
                    with options_col:
                        selected_option = st.radio(
                            "Select your answer",
                            q_data["options"],
                            index=None,
                            key=f"active_{curr_idx}",
                            label_visibility="collapsed",
                        )
                        st.caption("Choose an answer, then press **Check Answer**. Your choice will lock and the explanation will appear before you continue.")

                    with explanation_col:
                        st.markdown(
                            """
                            <div class="empty-review">
                                <div class="empty-review-icon">💡</div>
                                <div class="empty-review-title">Explanation will appear here</div>
                                <div class="empty-review-text">After you check your answer, this space will show the correct answer and explanation for <b>this question</b>.</div>
                            </div>
                            """,
                            unsafe_allow_html=True,
                        )

                    st.markdown("<div style='height:.5rem'></div>", unsafe_allow_html=True)
                    c1, c2, c3 = st.columns(3)
                    with c1:
                        if curr_idx > 0 and st.button("← Previous", use_container_width=True):
                            st.session_state.current_index -= 1
                            st.rerun(scope="fragment")
                    with c2:
                        if st.button("Skip", use_container_width=True):
                            q_data["user_answer"] = None
                            q_data["locked"] = True
                            update_mistake_bank(q_data)
                            # Stay on the same question so the skipped state and explanation
                            # are visible before the learner moves on.
                            st.rerun(scope="fragment")
                    with c3:
                        if st.button("Check Answer", type="primary", use_container_width=True):
                            if selected_option is None:
                                st.warning("Pick an option or use Skip.")
                            else:
                                q_data["user_answer"] = selected_option
                                q_data["locked"] = True
                                update_mistake_bank(q_data)
                                # Do NOT advance yet. The learner must see the explanation
                                # for this exact question and explicitly press Next.
                                st.rerun(scope="fragment")

                st.markdown('</div>', unsafe_allow_html=True)

            with nav_col:
                st.markdown("### Question map")
                st.caption("Green = correct · red = incorrect · muted = skipped")
                render_palette(questions, curr_idx)

                st.markdown("<div style='height:.7rem'></div>", unsafe_allow_html=True)
                correct, incorrect, skipped, attended = answer_counts(questions)
                render_metric("Current score", f"{correct}/{done_count or 1}", "Updates as you answer")

                st.markdown("<div style='height:.7rem'></div>", unsafe_allow_html=True)
                st.markdown(
                    '<div class="tip"><b>Focus cue</b><br>Read the question twice before looking at the options. It reduces impulse guessing.</div>',
                    unsafe_allow_html=True,
                )


render_main_content()
