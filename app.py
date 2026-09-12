import random
from datetime import date

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

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
    "Midnight": {
        "bg": "#0E1411",
        "surface": "#151D18",
        "surface_2": "#1D2821",
        "text": "#EAF3EC",
        "muted": "#A8B7AC",
        "accent": "#A7C77A",
        "accent_dark": "#7FA258",
        "accent_soft": "#2A382C",
        "success": "#6FBE8A",
        "danger": "#E28A7A",
        "warning": "#D7A75A",
        "border": "#2C392F",
        "shadow": "rgba(0, 0, 0, 0.28)",
    },
    "Warm": {
        "bg": "#FBF8F1",
        "surface": "#FFFDF9",
        "surface_2": "#F5EFE3",
        "text": "#3B3328",
        "muted": "#86796B",
        "accent": "#8A8F57",
        "accent_dark": "#666B3E",
        "accent_soft": "#EBE9D8",
        "success": "#5E8A69",
        "danger": "#C97567",
        "warning": "#BC8B48",
        "border": "#E7DDCC",
        "shadow": "rgba(74, 58, 39, 0.10)",
    },
}

if "theme" not in st.session_state:
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
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce").dt.date
    df = df.dropna(subset=["Date", "Question"]).reset_index(drop=True)
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
        "source_df", "q_mode", "num_q", "quiz_started_at"
    ]
    for key in keys:
        st.session_state.pop(key, None)


def generate_question_sample(source_df, q_mode, num_q):
    if source_df.empty:
        return source_df.copy()

    if q_mode == "All":
        return source_df.sample(frac=1).reset_index(drop=True)

    unique_dates = source_df["Date"].dropna().unique()
    sample_size = min(max(int(num_q), len(unique_dates)), len(source_df))
    guaranteed_df = source_df.groupby("Date", group_keys=False).sample(n=1)
    remaining_needed = sample_size - len(guaranteed_df)

    if remaining_needed > 0:
        remaining_pool = source_df.drop(guaranteed_df.index)
        extra_df = remaining_pool.sample(n=min(remaining_needed, len(remaining_pool)))
        final_sample = pd.concat([guaranteed_df, extra_df])
    else:
        final_sample = guaranteed_df

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
        "quiz_started_at": pd.Timestamp.now().strftime("%H:%M"),
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


# =========================================================
# SIDEBAR
# =========================================================
st.sidebar.markdown("# 🌿 Current Affairs")
st.sidebar.caption("A calm little place to turn daily news into long-term memory.")

with st.sidebar:
    st.markdown("### Workspace")
    app_page = st.radio(
        "Go to",
        ["🏠 Dashboard", "📅 Daily Quiz", "🎯 Custom Test"],
        label_visibility="collapsed",
        key="app_page",
    )

    st.markdown("### Appearance")
    selected_theme = st.selectbox("Theme", list(THEMES.keys()), index=list(THEMES.keys()).index(st.session_state.theme))
    if selected_theme != st.session_state.theme:
        st.session_state.theme = selected_theme
        st.rerun()

    st.markdown("### Your library")
    st.metric("Questions", f"{len(df):,}")
    st.metric("Study days", f"{df['Date'].nunique():,}")
    latest = df["Date"].max()
    st.caption(f"Latest sheet date · {latest.strftime('%d %b %Y') if latest else '—'}")

    if st.button("🔄 Sync Google Sheet", use_container_width=True):
        st.cache_data.clear()
        reset_quiz_state()
        st.rerun()

# =========================================================
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
        selected_date = st.date_input("Study date", value=default_date)
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
                st.rerun()
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
        start_date = st.date_input("Start date", value=min_date)
    with c2:
        end_date = st.date_input("End date", value=max_date)

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
        render_metric("Minimum test", f"{available_days:,}", "One question per date")

    q_mode = st.radio("Question quantity", ["Custom Number", "All"], horizontal=True)
    if q_mode == "Custom Number":
        requested = st.number_input(
            "Number of questions",
            min_value=1,
            max_value=max(1, available_questions),
            value=min(15, max(1, available_questions)),
            step=1,
        )
        actual_num = max(int(requested), available_days) if available_questions else 0
        if actual_num > int(requested):
            st.info(f"To cover all {available_days} study days, your test will contain **{actual_num}** questions.")
    else:
        actual_num = "All"
        st.info(f"You’ll practise all **{available_questions}** questions in the selected range.")

    if st.button("🎯 Generate My Test", type="primary", use_container_width=True):
        if custom_df.empty:
            st.error("No questions are available in this date range.")
        else:
            initialize_quiz(custom_df, q_mode, actual_num)
            st.rerun()

# =========================================================
# QUIZ ENGINE
# =========================================================
elif "quiz_questions" in st.session_state:
    questions = st.session_state.quiz_questions
    total_q = len(questions)
    curr_idx = st.session_state.current_index

    if st.session_state.quiz_completed:
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
                initialize_quiz(st.session_state.source_df, st.session_state.q_mode, st.session_state.num_q)
                st.rerun()
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
                    st.warning("You skipped this one. That’s okay—make the explanation count.")
                elif q_data["user_answer"] == q_data["correct"]:
                    st.success("Correct! Nice work.")
                else:
                    st.error(f"Not quite. You chose: {q_data['user_answer']}")

                st.markdown(
                    f"""
                    <div class="card" style="margin-top:1rem;background:var(--surface2)">
                        <div class="metric-label">Answer & explanation</div>
                        <div style="font-weight:700;margin:.35rem 0 .55rem">✅ {q_data['correct']}</div>
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
                        st.rerun()
                with c2:
                    if st.button("🔁 Retake", use_container_width=True):
                        q_data["user_answer"] = None
                        q_data["locked"] = False
                        st.rerun()
                with c3:
                    if curr_idx < total_q - 1:
                        if st.button("Next →", type="primary", use_container_width=True):
                            st.session_state.current_index += 1
                            st.rerun()
                    else:
                        if st.button("View Results 🏁", type="primary", use_container_width=True):
                            st.session_state.quiz_completed = True
                            st.rerun()
            else:
                selected_option = st.radio(
                    "Select your answer",
                    q_data["options"],
                    index=None,
                    key=f"active_{curr_idx}",
                    label_visibility="collapsed",
                )
                st.caption("Choose the best answer. You can review locked questions before finishing.")

                c1, c2, c3 = st.columns(3)
                with c1:
                    if curr_idx > 0 and st.button("← Previous", use_container_width=True):
                        st.session_state.current_index -= 1
                        st.rerun()
                with c2:
                    if st.button("Skip", use_container_width=True):
                        q_data["user_answer"] = None
                        q_data["locked"] = True
                        st.rerun()
                with c3:
                    label = "Submit & Finish" if curr_idx == total_q - 1 else "Submit & Next"
                    if st.button(label, type="primary", use_container_width=True):
                        if selected_option is None:
                            st.warning("Pick an option or use Skip.")
                        else:
                            q_data["user_answer"] = selected_option
                            q_data["locked"] = True
                            if curr_idx == total_q - 1:
                                st.session_state.quiz_completed = True
                            else:
                                st.session_state.current_index += 1
                            st.rerun()

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
