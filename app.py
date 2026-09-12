import streamlit as st
import pandas as pd
import random
import plotly.graph_objects as go

st.set_page_layout = "centered"

# 1. Fetch Data from Google Sheets
@st.cache_data(ttl=60)
def load_data():
    sheet_url = "https://docs.google.com/spreadsheets/d/1hFoQMwnPugw8A6El69rkU0kNT4NDYJt4msOgnNv-f8Q/export?format=csv"
    df = pd.read_csv(sheet_url)
    df['Date'] = pd.to_datetime(df['Date']).dt.date
    return df

df = load_data()

# 2. State Initialization Helpers
def generate_question_sample(source_df, q_mode, num_q):
    """Filters data ensuring at least 1 question per date is chosen."""
    if q_mode == "All":
        return source_df.sample(frac=1).reset_index(drop=True)
        
    unique_dates = source_df['Date'].unique()
    
    # Ensure minimum questions = number of unique dates to satisfy the rule
    sample_size = max(int(num_q), len(unique_dates))
    sample_size = min(sample_size, len(source_df)) # Cap at total available
    
    # Step A: Grab exactly 1 random question from every available date
    guaranteed_df = source_df.groupby('Date').sample(n=1)
    
    # Step B: Fill the remaining slots (if any) with other random questions
    remaining_needed = sample_size - len(guaranteed_df)
    if remaining_needed > 0:
        remaining_pool = source_df.drop(guaranteed_df.index)
        extra_df = remaining_pool.sample(n=remaining_needed)
        final_sample = pd.concat([guaranteed_df, extra_df])
    else:
        final_sample = guaranteed_df
        
    # Step C: Shuffle the combined list so the date order is unpredictable
    return final_sample.sample(frac=1).reset_index(drop=True)


def initialize_quiz(source_df, q_mode, num_q):
    st.session_state['source_df'] = source_df
    st.session_state['q_mode'] = q_mode
    st.session_state['num_q'] = num_q
    
    # Pull fresh randomized sample based on constraints
    shuffled_df = generate_question_sample(source_df, q_mode, num_q)
    questions = []
    
    for _, row in shuffled_df.iterrows():
        opts = [str(row['Option_1']), str(row['Option_2']), str(row['Option_3']), str(row['Option_4'])]
        random.shuffle(opts)
        questions.append({
            'date': row['Date'],
            'question': str(row['Question']),
            'options': opts,
            'correct': str(row['Correct_Option']).strip(),
            'explanation': str(row['Explanation']),
            'user_answer': None,
            'locked': False
        })
        
    st.session_state['quiz_questions'] = questions
    st.session_state['current_index'] = 0
    st.session_state['quiz_completed'] = False

def reset_all():
    for key in list(st.session_state.keys()):
        del st.session_state[key]

# 3. Sidebar Controls
st.sidebar.title("Current Affairs Tracker")
mode = st.sidebar.radio("Select Mode", ["Daily Quiz", "Custom Test"], on_change=reset_all)

if st.sidebar.button("🔄 Sync Sheet / Reset"):
    st.cache_data.clear()
    reset_all()
    st.rerun()

# 4. Mode Selection & Quiz Generation
if 'quiz_questions' not in st.session_state:
    if mode == "Daily Quiz":
        st.title("📅 Daily Quiz")
        available_dates = sorted(df['Date'].dropna().unique())
        
        default_date = available_dates[-1] if len(available_dates) > 0 else None
        selected_date = st.date_input("Select Date", value=default_date)
        
        filtered_df = df[df['Date'] == selected_date].copy()
        
        if filtered_df.empty:
            st.warning("⚠️ No questions found for this date.")
            if len(available_dates) > 0:
                nearest_date = min(available_dates, key=lambda d: abs((d - selected_date).days))
                formatted_nearest = f"{nearest_date.day} {nearest_date.strftime('%B %Y')}"
                st.info(f"📌 **Nearest available quiz date:** {formatted_nearest}")
        else:
            formatted_date = f"{selected_date.day} {selected_date.strftime('%B %Y')}"
            st.write(f"Available questions for **{formatted_date}**: {len(filtered_df)}")
            if st.button("Start Daily Quiz", type="primary"):
                initialize_quiz(filtered_df, "All", len(filtered_df))
                st.rerun()

    elif mode == "Custom Test":
        st.title("🎯 Custom Test")
        col1, col2 = st.columns(2)
        with col1:
            start_date = st.date_input("Start Date", value=df['Date'].min())
        with col2:
            end_date = st.date_input("End Date", value=df['Date'].max())
            
        q_mode = st.radio("Question Quantity", ["Custom Number", "All"], horizontal=True)
        
        if q_mode == "Custom Number":
            num_q = st.number_input("Enter Number of Questions", min_value=1, max_value=len(df), value=min(10, len(df)))
        else:
            num_q = "All"
        
        if st.button("Start Custom Test", type="primary"):
            custom_df = df[(df['Date'] >= start_date) & (df['Date'] <= end_date)].copy()
            if custom_df.empty:
                st.warning("⚠️ No questions available within this date range.")
            else:
                unique_days_count = len(custom_df['Date'].unique())
                if q_mode == "Custom Number" and int(num_q) < unique_days_count:
                    st.toast(f"Increased test to {unique_days_count} questions to ensure 1 from every date.")
                
                initialize_quiz(custom_df, q_mode, num_q)
                st.rerun()

# 5. Quiz Execution & Single-Question Navigation
else:
    questions = st.session_state['quiz_questions']
    total_q = len(questions)
    curr_idx = st.session_state['current_index']

    # --- RESULTS SCREEN ---
    if st.session_state['quiz_completed']:
        st.title("🏁 Quiz Summary")
        
        attended = sum(1 for q in questions if q['user_answer'] is not None)
        unattended = total_q - attended
        correct = sum(1 for q in questions if q['user_answer'] == q['correct'])
        incorrect = attended - correct
        accuracy = (correct / attended * 100) if attended > 0 else 0

        # Overall Score Metrics
        col1, col2, col3 = st.columns(3)
        col1.metric("Final Score", f"{correct} / {total_q}")
        col2.metric("Attended", attended)
        col3.metric("Unattended", unattended)

        col4, col5 = st.columns(2)
        col4.metric("Correct", correct)
        col5.metric("Accuracy", f"{accuracy:.1f}%")
        st.markdown("---")

        # Date-wise Visual Feedback (Only for Custom Tests)
        if mode == "Custom Test":
            st.subheader("📅 Date-wise Breakdown")
            
            date_stats = {}
            for q in questions:
                d = q['date']
                if d not in date_stats:
                    date_stats[d] = {'total': 0, 'correct': 0}
                date_stats[d]['total'] += 1
                if q['user_answer'] == q['correct']:
                    date_stats[d]['correct'] += 1
            
            chart_cols = st.columns(3)
            for idx, (d, stats) in enumerate(date_stats.items()):
                target_col = chart_cols[idx % 3]
                d_total = stats['total']
                d_correct = stats['correct']
                d_incorrect = d_total - d_correct
                formatted_d = f"{d.day} {d.strftime('%b')}"
                
                fig = go.Figure(go.Pie(
                    values=[d_correct, d_incorrect] if d_total > 0 else [1],
                    labels=['Correct', 'Incorrect/Skipped'],
                    hole=0.75,
                    textinfo='none',
                    marker_colors=['#28a745', '#dc3545']
                ))
                
                fig.update_layout(
                    annotations=[dict(text=f"{d_correct}/{d_total}", x=0.5, y=0.5, font_size=20, showarrow=False, font=dict(color="white"))],
                    showlegend=False,
                    margin=dict(t=30, b=0, l=0, r=0),
                    height=180,
                    title=dict(text=formatted_d, x=0.5, y=0.95, font_size=14, font=dict(color="white"))
                )
                
                with target_col:
                    st.plotly_chart(fig, use_container_width=True, config={'displayModeBar': False})

            st.markdown("---")
        
        # Navigation Actions
        col_retry, col_home = st.columns(2)
        with col_retry:
            if st.button("🔁 Reattempt Test", type="primary"):
                # Calls the new generator to enforce the 1-per-date rule again with fresh randoms
                initialize_quiz(st.session_state['source_df'], st.session_state['q_mode'], st.session_state['num_q'])
                st.rerun()
                
        with col_home:
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
            st.radio(
                "Options:",
                q_data['options'],
                index=q_data['options'].index(q_data['user_answer']) if q_data['user_answer'] in q_data['options'] else None,
                disabled=True,
                key=f"locked_view_{curr_idx}"
            )
            
            if q_data['user_answer'] is None:
                st.warning("⚠️ You skipped this question.")
            elif q_data['user_answer'] == q_data['correct']:
                st.success("✅ Correct Answer!")
            else:
                st.error(f"❌ Incorrect. Your choice: '{q_data['user_answer']}'.")
                
            st.info(f"**Correct Answer:** {q_data['correct']}\n\n**Explanation:** {q_data['explanation']}")

            c_prev, c_space, c_next = st.columns([1, 1, 1])
            with c_prev:
                if curr_idx > 0:
                    if st.button("⬅️ Previous"):
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
            selected_option = st.radio(
                "Select your answer:",
                q_data['options'],
                index=None,
                key=f"active_choice_{curr_idx}"
            )

            col_prev, col_skip, col_act = st.columns([1, 1, 1])
            
            with col_prev:
                if curr_idx > 0:
                    if st.button("⬅️ Previous"):
                        st.session_state['current_index'] -= 1
                        st.rerun()

            with col_skip:
                if st.button("Skip"):
                    q_data['user_answer'] = None
                    q_data['locked'] = True
                    st.rerun()

            with col_act:
                btn_label = "Submit Test" if curr_idx == total_q - 1 else "Next"
                if st.button(btn_label, type="primary"):
                    if selected_option is None:
                        st.warning("Please choose an option or click Skip.")
                    else:
                        q_data['user_answer'] = selected_option
                        q_data['locked'] = True
                        st.rerun()
