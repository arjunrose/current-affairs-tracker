import streamlit as st
import pandas as pd
import random

# 1. Fetch Data
@st.cache_data(ttl=60) # Refreshes data from Google Sheets every 60 seconds
def load_data():
    # Direct CSV export link generated from your provided URL
    sheet_url = "https://docs.google.com/spreadsheets/d/1hFoQMwnPugw8A6El69rkU0kNT4NDYJt4msOgnNv-f8Q/export?format=csv"
    df = pd.read_csv(sheet_url)
    df['Date'] = pd.to_datetime(df['Date']).dt.date
    return df

df = load_data()

# 2. Sidebar Navigation
st.sidebar.title("Current Affairs Tracker")
mode = st.sidebar.radio("Select Mode", ["Daily Quiz", "Custom Test"])

def reset_quiz():
    for key in st.session_state.keys():
        del st.session_state[key]

if st.sidebar.button("Sync Latest Questions / Reset"):
    st.cache_data.clear()
    reset_quiz()
    st.rerun()

# 3. Daily Quiz Mode
if mode == "Daily Quiz":
    st.title("📅 Daily Quiz")
    
    available_dates = df['Date'].dropna().unique()
    selected_date = st.selectbox("Select Date", available_dates, on_change=reset_quiz)
    
    daily_df = df[df['Date'] == selected_date].reset_index(drop=True)
    
    if daily_df.empty:
        st.warning("No questions available for this date.")
    else:
        with st.form("daily_quiz_form"):
            user_answers = {}
            for index, row in daily_df.iterrows():
                st.markdown(f"**Q{index+1}: {row['Question']}**")
                
                # Shuffle options once per session per question
                options_key = f"options_{index}"
                if options_key not in st.session_state:
                    opts = [str(row['Option_1']), str(row['Option_2']), str(row['Option_3']), str(row['Option_4'])]
                    random.shuffle(opts)
                    st.session_state[options_key] = opts
                
                user_answers[index] = st.radio("Select answer:", st.session_state[options_key], key=f"q_{index}", index=None)
                st.markdown("---")
            
            if st.form_submit_button("Submit Quiz"):
                score = 0
                for index, row in daily_df.iterrows():
                    correct_ans = str(row['Correct_Option'])
                    user_ans = str(user_answers[index])
                    
                    if user_ans == correct_ans:
                        score += 1
                        st.success(f"Q{index+1}: Correct! ({correct_ans})")
                    else:
                        st.error(f"Q{index+1}: Incorrect. You chose '{user_ans}'. The correct answer is '{correct_ans}'.")
                        st.info(f"**Explanation:** {row['Explanation']}")
                
                st.header(f"🎯 Your Score: {score} / {len(daily_df)}")

# 4. Custom Test Mode
elif mode == "Custom Test":
    st.title("🎯 Custom Test")
    
    col1, col2 = st.columns(2)
    with col1:
        start_date = st.date_input("Start Date", value=df['Date'].min(), on_change=reset_quiz)
    with col2:
        end_date = st.date_input("End Date", value=df['Date'].max(), on_change=reset_quiz)
        
    num_questions = st.number_input("Number of Questions", min_value=1, max_value=100, value=10, on_change=reset_quiz)
    
    custom_df = df[(df['Date'] >= start_date) & (df['Date'] <= end_date)]
    
    if st.button("Generate Test"):
        if custom_df.empty:
            st.warning("No questions found in this date range.")
        else:
            sample_size = min(num_questions, len(custom_df))
            st.session_state['custom_test_df'] = custom_df.sample(n=sample_size).reset_index(drop=True)
            
    if 'custom_test_df' in st.session_state:
        test_df = st.session_state['custom_test_df']
        
        with st.form("custom_test_form"):
            user_answers = {}
            for index, row in test_df.iterrows():
                st.markdown(f"**Q{index+1}: {row['Question']}**")
                
                options_key = f"custom_options_{index}"
                if options_key not in st.session_state:
                    opts = [str(row['Option_1']), str(row['Option_2']), str(row['Option_3']), str(row['Option_4'])]
                    random.shuffle(opts)
                    st.session_state[options_key] = opts
                    
                user_answers[index] = st.radio("Select answer:", st.session_state[options_key], key=f"cq_{index}", index=None)
                st.markdown("---")
                
            if st.form_submit_button("Submit Test"):
                score = 0
                for index, row in test_df.iterrows():
                    correct_ans = str(row['Correct_Option'])
                    user_ans = str(user_answers[index])
                    
                    if user_ans == correct_ans:
                        score += 1
                    else:
                        st.error(f"Q{index+1}: Incorrect. Correct answer is '{correct_ans}'.")
                        st.info(f"**Explanation:** {row['Explanation']}")
                        
                st.header(f"🎯 Final Score: {score} / {len(test_df)}")