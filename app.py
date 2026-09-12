import streamlit as st
import pandas as pd
import random
import requests
import time
import plotly.graph_objects as go
from streamlit_autorefresh import st_autorefresh

st.set_page_layout = "centered"

FIREBASE_URL = "https://current-affairs-quiz-f86fd-default-rtdb.asia-southeast1.firebasedatabase.app"

# 1. Fetch Data from Google Sheets
@st.cache_data(ttl=60)
def load_data():
    sheet_url = "https://docs.google.com/spreadsheets/d/1hFoQMwnPugw8A6El69rkU0kNT4NDYJt4msOgnNv-f8Q/export?format=csv"
    df = pd.read_csv(sheet_url)
    df['Date'] = pd.to_datetime(df['Date']).dt.date
    return df

df = load_data()

# 2. Firebase Helpers
def fb_get(path):
    res = requests.get(f"{FIREBASE_URL}/{path}.json")
    return res.json() if res.status_code == 200 else None

def fb_put(path, data):
    requests.put(f"{FIREBASE_URL}/{path}.json", json=data)

def fb_patch(path, data):
    requests.patch(f"{FIREBASE_URL}/{path}.json", json=data)

# 3. Core Initialization
def generate_question_sample(source_df, q_mode, num_q):
    if q_mode == "All":
        return source_df.sample(frac=1).reset_index(drop=True)
        
    unique_dates = source_df['Date'].unique()
    sample_size = max(int(num_q), len(unique_dates))
    sample_size = min(sample_size, len(source_df))
    
    guaranteed_df = source_df.groupby('Date').sample(n=1)
    remaining_needed = sample_size - len(guaranteed_df)
    
    if remaining_needed > 0:
        remaining_pool = source_df.drop(guaranteed_df.index)
        extra_df = remaining_pool.sample(n=remaining_needed)
        final_sample = pd.concat([guaranteed_df, extra_df])
    else:
        final_sample = guaranteed_df
        
    return final_sample.sample(frac=1).reset_index(drop=True)

def initialize_quiz(source_df=None, q_mode="All", num_q=10, provided_questions=None):
    if provided_questions is None:
        st.session_state['source_df'] = source_df
        st.session_state['q_mode'] = q_mode
        st.session_state['num_q'] = num_q
        shuffled_df = generate_question_sample(source_df, q_mode, num_q)
        questions = []
        
        for _, row in shuffled_df.iterrows():
            opts = [str(row['Option_1']), str(row['Option_2']), str(row['Option_3']), str(row['Option_4'])]
            random.shuffle(opts)
            questions.append({
                'date': str(row['Date']),
                'question': str(row['Question']),
                'options': opts,
                'correct': str(row['Correct_Option']).strip(),
                'explanation': str(row['Explanation']),
                'user_answer': None,
                'locked': False
            })
    else:
        # Load questions from Firebase for joining players
        questions = []
        for q in provided_questions:
            questions.append({**q, 'user_answer': None, 'locked': False})

    st.session_state['quiz_questions'] = questions
    st.session_state['current_index'] = 0
    st.session_state['quiz_completed'] = False

def reset_all():
    for key in list(st.session_state.keys()):
        del st.session_state[key]

# 4. Sidebar Controls
st.sidebar.title("Current Affairs Tracker")
mode = st.sidebar.radio("Select Mode", ["Daily Quiz", "Custom Test", "Host Quiz", "Join Quiz"], on_change=reset_all)

if st.sidebar.button("🔄 Sync Sheet / Reset"):
    st.cache_data.clear()
    reset_all()
    st.rerun()

# 5. Routing Logic (Isolating Host vs Player)
if st.session_state.get('is_host'):
    # --- MULTIPLAYER: HOST DASHBOARD ---
    st_autorefresh(interval=2000, key="host_refresh") 
    code = st.session_state['room_code']
    room_data = fb_get(f"rooms/{code}")
    
    if not room_data:
        st.error("Room data lost.")
        if st.button("Back to Menu"): reset_all(); st.rerun()
        st.stop()

    st.header(f"Room Code: **{code}**")
    status = room_data['status']
    players = room_data.get('players', {})
    
    if status == "waiting":
        st.subheader(f"👥 Players Joined: {len(players)}")
        for p in players.keys():
            st.markdown(f"👤 {p}")
            
        st.markdown("---")
        if st.button("🚀 Start Quiz", type="primary") and len(players) > 0:
            # Set start time 4 seconds in the future to allow for a 3-2-1 countdown on all devices
            fb_patch(f"rooms/{code}", {"status": "playing", "start_time": time.time() + 4})
            st.rerun()
        elif len(players) == 0:
            st.warning("Waiting for players to join before starting...")
            
    elif status == "playing":
        st.success("Quiz is live!")
        start_time = room_data.get('start_time', 0)
        duration = room_data.get('duration', -1)
        now = time.time()
        
        if now < start_time:
            st.header(f"Starting in {int(start_time - now)}...")
        else:
            if duration > 0:
                time_left = (start_time + duration) - now
                if time_left <= 0:
                    fb_patch(f"rooms/{code}", {"status": "finished"})
                    st.rerun()
                mins, secs = divmod(int(time_left), 60)
                st.subheader(f"⏳ Time Left: {mins:02d}:{secs:02d}")
            else:
                st.subheader("⏳ Time Left: Unlimited (Until Stop)")

            st.markdown("---")
            if not st.session_state.get('confirm_stop', False):
                if st.button("🛑 Stop Exam", type="primary"):
                    st.session_state['confirm_stop'] = True
                    st.rerun()
            else:
                st.warning("⚠️ Do you need to end the exam?")
                c1, c2 = st.columns(2)
                if c1.button("Yes, End Now", type="primary"):
                    fb_patch(f"rooms/{code}", {"status": "finished"})
                    st.session_state['confirm_stop'] = False
                    st.rerun()
                if c2.button("Cancel"):
                    st.session_state['confirm_stop'] = False
                    st.rerun()
                    
    elif status == "finished":
        st.header("🏆 Final Results Dashboard")
        if players:
            results = [{"Name": n, "Score": p.get('score', 0)} for n, p in players.items()]
            res_df = pd.DataFrame(results).sort_values(by="Score", ascending=False).reset_index(drop=True)
            
            st.metric("Average Score", round(res_df['Score'].mean(), 2))
            st.dataframe(res_df, use_container_width=True)
        else:
            st.warning("No players completed the quiz.")
        
        if st.button("Close Room"):
            fb_put(f"rooms/{code}", None) 
            reset_all()
            st.rerun()

elif 'quiz_questions' not in st.session_state:
    
    # --- OFFLINE MODES ---
    if mode == "Daily Quiz":
        st.title("📅 Daily Quiz")
        available_dates = sorted(df['Date'].dropna().unique())
        selected_date = st.date_input("Select Date", value=available_dates[-1] if len(available_dates) > 0 else None)
        filtered_df = df[df['Date'] == selected_date].copy()
        
        if filtered_df.empty:
            st.warning("⚠️ No questions found for this date.")
        else:
            if st.button("Start Daily Quiz", type="primary"):
                initialize_quiz(source_df=filtered_df, q_mode="All", num_q=len(filtered_df))
                st.rerun()

    elif mode == "Custom Test":
        st.title("🎯 Custom Test")
        col1, col2 = st.columns(2)
        with col1: start_date = st.date_input("Start Date", value=df['Date'].min())
        with col2: end_date = st.date_input("End Date", value=df['Date'].max())
            
        q_mode = st.radio("Question Quantity", ["Custom Number", "All"], horizontal=True)
        num_q = st.number_input("Questions", min_value=1, value=10) if q_mode == "Custom Number" else "All"
        
        if st.button("Start Custom Test", type="primary"):
            custom_df = df[(df['Date'] >= start_date) & (df['Date'] <= end_date)].copy()
            initialize_quiz(source_df=custom_df, q_mode=q_mode, num_q=num_q)
            st.rerun()

    # --- MULTIPLAYER: HOST SETUP ---
    elif mode == "Host Quiz":
        st.title("👑 Host a Live Quiz")
        host_name = st.text_input("Host Name", placeholder="Enter your name")
        
        c1, c2 = st.columns(2)
        with c1: start_date = st.date_input("Start Date", value=df['Date'].min())
        with c2: end_date = st.date_input("End Date", value=df['Date'].max())
        
        q_mode = st.radio("Question Quantity", ["Custom Number", "All"], horizontal=True)
        num_q = st.number_input("Questions", min_value=1, value=10) if q_mode == "Custom Number" else "All"
        
        t_mode = st.radio("Exam Duration", ["Minutes", "Until press stop"], horizontal=True)
        duration_mins = st.number_input("Minutes", min_value=1, value=10) if t_mode == "Minutes" else None
        
        if st.button("Generate Room", type="primary") and host_name:
            custom_df = df[(df['Date'] >= start_date) & (df['Date'] <= end_date)].copy()
            if not custom_df.empty:
                # Generate questions but DO NOT load them into session_state to prevent host from seeing them
                shuffled_df = generate_question_sample(custom_df, q_mode, num_q)
                qs_for_db = []
                for _, row in shuffled_df.iterrows():
                    opts = [str(row['Option_1']), str(row['Option_2']), str(row['Option_3']), str(row['Option_4'])]
                    random.shuffle(opts)
                    qs_for_db.append({
                        'date': str(row['Date']),
                        'question': str(row['Question']),
                        'options': opts,
                        'correct': str(row['Correct_Option']).strip(),
                        'explanation': str(row['Explanation'])
                    })
                
                room_code = str(random.randint(1000, 9999))
                
                fb_put(f"rooms/{room_code}", {
                    "status": "waiting",
                    "host": host_name,
                    "questions": qs_for_db,
                    "players": {},
                    "duration": duration_mins * 60 if duration_mins else -1
                })
                
                st.session_state['is_host'] = True
                st.session_state['room_code'] = room_code
                st.rerun()
            else:
                st.warning("No questions found in this date range.")

    # --- MULTIPLAYER: JOIN SETUP ---
    elif mode == "Join Quiz":
        st.title("🎮 Join Quiz")
        code_input = st.text_input("Enter 4-Digit Room Code", max_chars=4)
        player_name = st.text_input("Your Name")
        
        if st.button("Join Room", type="primary") and code_input and player_name:
            room_data = fb_get(f"rooms/{code_input}")
            if room_data and room_data.get('status') == "waiting":
                st.session_state['joined_room'] = code_input
                st.session_state['player_name'] = player_name
                fb_patch(f"rooms/{code_input}/players/{player_name}", {"score": 0, "finished": False})
                
                # Fetch locked questions into player's state
                initialize_quiz(provided_questions=room_data['questions'])
                st.session_state['is_multiplayer'] = True
                st.rerun()
            else:
                st.error("Invalid room code or quiz already started.")

# 6. Active Quiz Execution (Players Only)
else:
    questions = st.session_state['quiz_questions']
    total_q = len(questions)
    curr_idx = st.session_state['current_index']
    is_multi = st.session_state.get('is_multiplayer', False)

    # Check live Multiplayer Status & Timers
    if is_multi and not st.session_state.get('quiz_completed', False):
        st_autorefresh(interval=1000, key="player_refresh")
        code = st.session_state['joined_room']
        room_data = fb_get(f"rooms/{code}")
        
        if not room_data:
            st.error("Room closed by host.")
            st.stop()
            
        status = room_data['status']
        if status == "waiting":
            st.info(f"Connected! Waiting for host ({room_data['host']}) to start...")
            st.stop()
            
        elif status == "playing":
            start_time = room_data.get('start_time', 0)
            duration = room_data.get('duration', -1)
            now = time.time()
            
            if now < start_time:
                st.header(f"Starting in {int(start_time - now)}...")
                st.stop()
                
            if duration > 0:
                time_left = (start_time + duration) - now
                if time_left <= 0:
                    st.session_state['quiz_completed'] = True
                    st.rerun()
                else:
                    mins, secs = divmod(int(time_left), 60)
                    col_spacer, col_timer = st.columns([3, 1])
                    col_timer.subheader(f"⏳ {mins:02d}:{secs:02d}")
                    
        elif status == "finished":
            st.session_state['quiz_completed'] = True
            st.rerun()

    # --- RESULTS SCREEN ---
    if st.session_state.get('quiz_completed', False):
        st.title("🏁 Quiz Summary")
        attended = sum(1 for q in questions if q['user_answer'] is not None)
        correct = sum(1 for q in questions if q['user_answer'] == q['correct'])
        
        col1, col2, col3 = st.columns(3)
        col1.metric("Final Score", f"{correct} / {total_q}")
        col2.metric("Attended", attended)
        col3.metric("Accuracy", f"{(correct / attended * 100) if attended > 0 else 0:.1f}%")
        st.markdown("---")

        if is_multi:
            code = st.session_state['joined_room']
            p_name = st.session_state['player_name']
            
            fb_patch(f"rooms/{code}/players/{p_name}", {"score": correct, "finished": True})
            
            st.subheader("🏆 Top 3 Leaderboard")
            room_data = fb_get(f"rooms/{code}")
            
            if room_data and 'players' in room_data:
                ranked = sorted([{"name": k, "score": v.get("score", 0)} for k, v in room_data['players'].items()], key=lambda x: x["score"], reverse=True)
                for i, p in enumerate(ranked[:3]):
                    medal = "🥇" if i == 0 else "🥈" if i == 1 else "🥉"
                    st.markdown(f"**{medal} {p['name']}** - {p['score']} pts")
                
                my_rank = next((i for i, p in enumerate(ranked) if p['name'] == p_name), -1) + 1
                if my_rank > 3:
                    st.markdown(f"*You placed #{my_rank} with {correct} pts.*")

        if st.button("🏠 Exit to Menu"):
            reset_all()
            st.rerun()

    # --- SINGLE QUESTION SCREEN ---
    else:
        q_data = questions[curr_idx]
        st.caption(f"Question {curr_idx + 1} of {total_q}")
        st.progress((curr_idx + 1) / total_q)
        st.markdown(f"### Q{curr_idx + 1}: {q_data['question']}")

        if q_data['locked']:
            st.radio("Options:", q_data['options'], index=q_data['options'].index(q_data['user_answer']) if q_data['user_answer'] in q_data['options'] else None, disabled=True)
            if q_data['user_answer'] == q_data['correct']: st.success("✅ Correct Answer!")
            else: st.error(f"❌ Incorrect. Correct Answer: {q_data['correct']}")
            st.info(f"**Explanation:** {q_data['explanation']}")

            c_prev, _, c_next = st.columns([1, 1, 1])
            with c_prev:
                if curr_idx > 0 and st.button("⬅️ Previous"):
                    st.session_state['current_index'] -= 1
                    st.rerun()
            with c_next:
                if curr_idx < total_q - 1:
                    if st.button("Next Question ➡️"):
                        st.session_state['current_index'] += 1
                        st.rerun()
                else:
                    if st.button("View Final Score 🏁", type="primary"):
                        st.session_state['quiz_completed'] = True
                        st.rerun()
        else:
            selected = st.radio("Select your answer:", q_data['options'], index=None, key=f"ans_{curr_idx}")
            c_prev, c_skip, c_act = st.columns([1, 1, 1])
            
            with c_prev:
                if curr_idx > 0 and st.button("⬅️ Previous"):
                    st.session_state['current_index'] -= 1
                    st.rerun()
            with c_skip:
                if st.button("Skip"):
                    q_data['user_answer'] = None; q_data['locked'] = True; st.rerun()
            with c_act:
                btn_label = "Submit Test" if curr_idx == total_q - 1 else "Next"
                if st.button(btn_label, type="primary"):
                    if selected:
                        q_data['user_answer'] = selected; q_data['locked'] = True
                        if is_multi:
                            current_score = sum(1 for q in questions if q['user_answer'] == q['correct'])
                            fb_patch(f"rooms/{st.session_state['joined_room']}/players/{st.session_state['player_name']}", {"score": current_score})
                        st.rerun()
                    else: st.warning("Choose an option or Skip.")
