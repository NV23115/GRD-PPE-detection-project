import streamlit as st
import zmq, base64, time, boto3, json, pandas as pd, os
from datetime import datetime, date
from botocore.exceptions import NoCredentialsError, ClientError
from pathlib import Path

# --- 1. INITIALIZATION & LOCAL PERSISTENCE ---
IMAGE_BUCKET = "ppe-detection-image"

# Setup the paths for background saving
""""
DOWNLOADS_PATH = os.path.join(os.path.expanduser("~"), "Downloads")
BASE_PATH = r"D:\GRD"
LOG_TXT = os.path.join(BASE_PATH, "logs.txt")
LOG_CSV = os.path.join(BASE_PATH, "logs.csv")
LOG_TXT = os.path.join(DOWNLOADS_PATH, "logs.txt")
LOG_CSV = os.path.join(DOWNLOADS_PATH, "logs.csv")
"""

BASE_PATH = Path(__file__).resolve().parent
LOG_TXT = BASE_PATH / "logs.txt"
LOG_CSV = BASE_PATH / "logs.csv"

# Web state always starts empty (Current session only)
if "log_history" not in st.session_state: st.session_state.log_history = []
if "raw_console" not in st.session_state: st.session_state.raw_console = ""
if "sys_active" not in st.session_state: st.session_state.sys_active = False
if "applied_threshold" not in st.session_state: st.session_state.applied_threshold = 80

def add_event(level, user, msg):
    """Adds events to web state AND appends to local CSV."""
    now = datetime.now().strftime("%H:%M:%S")
    new_entry = {"Time": now, "Level": level, "User": user, "Event": msg}
    
    # Update Web State
    st.session_state.log_history.insert(0, new_entry)
    st.session_state.log_history = st.session_state.log_history[:15]
    
    # Append to Local CSV (Archive)
    df_new = pd.DataFrame([new_entry])
    df_new.to_csv(LOG_CSV, mode='a', index=False, header=not os.path.exists(LOG_CSV))

# --- 2. AWS IDENTITY CHECK ---
aws_status_message = "🔴 **AWS DISCONNECTED**"
user_identity = "Unknown User"
account_id = "0000-0000-0000"
aws_ready = False
badge_color = "#ff4b4b"
s3 = None

try:
    sts = boto3.client('sts')
    identity = sts.get_caller_identity()
    account_id = identity.get('Account')
    user_identity = identity.get('Arn').split('/')[-1]
    
    s3 = boto3.client('s3')
    s3.head_bucket(Bucket=IMAGE_BUCKET)
    
    aws_ready = True
    aws_status_message = "🟢 **SECURE CONNECTION**"
    badge_color = "#00ff00"
except Exception as e:
    aws_ready = False
    if "NoCredentialsError" in str(type(e)):
        aws_status_message = "🟡 **CONFIG AWS CREDENTIALS**"

st.set_page_config(page_title="PPE Tactical Command", layout="wide")

# --- 3. HEADER ---
h_col1, h_col2, h_col3 = st.columns([0.5, 0.2, 0.3])
with h_col1: st.title("🛡️ PPE Compliance Monitor")
with h_col3:
    st.markdown(f"""
    <div style="background-color: #1e2129; padding: 10px; border-radius: 5px; border-left: 5px solid {badge_color};">
        <p style="margin:0; font-size: 12px; color: #888;">CURRENT OPERATOR:</p>
        <p style="margin:0; font-weight: bold; color: #fff;">👤 {user_identity}</p>
        <p style="margin:0; font-size: 10px; color: #555;">ID: {account_id}</p>
    </div>
    """, unsafe_allow_html=True)
    st.write(aws_status_message)

# --- 4. SIDEBAR: INDUSTRIAL CONTROLS ---
st.sidebar.title("🎮 SYSTEM CONTROL")
c1, c2 = st.sidebar.columns(2)

if c1.button("▶️ RUN", use_container_width=True, type="primary" if st.session_state.sys_active else "secondary"):
    if not st.session_state.sys_active:
        st.session_state.sys_active = True
        add_event("⚙️ START", user_identity, "Engine Initialized")
        st.rerun()

if c2.button("🛑 STOP", use_container_width=True, type="primary" if not st.session_state.sys_active else "secondary"):
    if st.session_state.sys_active:
        st.session_state.sys_active = False
        add_event("⚙️ STOP", user_identity, "Engine Halted")
        st.rerun()

st.sidebar.header("🎯 AI SENSITIVITY")
new_val = st.sidebar.slider("Confidence %", 50, 95, value=st.session_state.applied_threshold)
if new_val != st.session_state.applied_threshold:
    st.sidebar.warning(f"⚠️ Pending: {new_val}%")
    if st.sidebar.button("✅ CONFIRM SETTING", use_container_width=True, type="primary"):
        st.session_state.applied_threshold = new_val
        add_event("🎯 CONFIG", user_identity, f"AI Threshold locked at {new_val}%")
        st.rerun()

# --- 5. STORAGE JANITOR ---
st.sidebar.markdown("---")
st.sidebar.header("🧹 STORAGE JANITOR")
current_files = 0
if aws_ready:
    try:
        res = s3.list_objects_v2(Bucket=IMAGE_BUCKET)
        current_files = res.get('KeyCount', 0)
    except: pass

st.sidebar.metric("S3 Bucket Load", f"{current_files} Files")
jan_mode = st.sidebar.selectbox("Cleanup Mode", ["Retention (By Number)", "Purge (By Date)"])

if "Retention" in jan_mode:
    n_del = st.sidebar.number_input("Delete Count", 1, 5000, 5)
    if st.sidebar.button("Execute Cleanup", use_container_width=True):
        if not aws_ready:
            st.sidebar.error("❌ AWS OFFLINE")
            add_event("⚠️ ERROR", user_identity, "Retention failed: AWS Disconnected")
        elif n_del > current_files:
            st.sidebar.error("🚫 AGGRESSIVE DELETE ERROR")
            add_event("⚠️ ERROR", user_identity, f"Retention blocked: Request({n_del}) > Bucket({current_files})")
        else:
            add_event("🧹 CLEANUP", user_identity, f"Purged {n_del} files from S3")
            st.sidebar.success(f"Successfully removed {n_del} files.")
else:
    dr = st.sidebar.date_input("Select Range", value=(date.today(), date.today()))
    if len(dr) == 2:
        start, end = dr
        if st.sidebar.button("🔥 PURGE BY DATE", use_container_width=True):
            if not aws_ready:
                st.sidebar.error("❌ AWS OFFLINE")
                add_event("⚠️ ERROR", user_identity, "Date purge failed: AWS Disconnected")
            elif end > date.today():
                st.sidebar.error("⏳ TIME TRAVEL ERROR")
                add_event("⚠️ ERROR", user_identity, f"Date purge blocked: Future date ({end})")
            else:
                add_event("🔥 PURGE", user_identity, f"Date wipe executed: {start} to {end}")
                st.sidebar.warning(f"Cleanup finished for range {start}.")

# --- 6. DATA EXPORT (Fades when Running) ---
# --- 6. DATA EXPORT ---
st.sidebar.markdown("---")
st.sidebar.header("📥 DATA EXPORT")

range_label = date.today().strftime("%Y-%m-%d")
if st.session_state.log_history:
    start_tm = st.session_state.log_history[-1]['Time'].replace(":", ".")
    end_tm = st.session_state.log_history[0]['Time'].replace(":", ".")
    range_label = f"{date.today()} {start_tm} to {date.today()} {end_tm}"

# Unified disable flag: Buttons grey out when system is active
is_disabled = st.session_state.sys_active

if st.session_state.log_history:
    st.sidebar.download_button(
        label="📄 Download Audit CSV",
        data=pd.DataFrame(st.session_state.log_history).to_csv(index=False).encode('utf-8'),
        file_name=f"logs_{range_label}.csv",
        mime="text/csv",
        use_container_width=True,
        disabled=is_disabled
    )

if st.session_state.raw_console:
    st.sidebar.download_button(
        label="📟 Download Raw Logs",
        data=st.session_state.raw_console,
        file_name=f"logs_{range_label}.txt",
        mime="text/plain",
        use_container_width=True,
        disabled=is_disabled
    )

# --- 8. SYSTEM RESET (The "Nuke" Button) ---
st.sidebar.markdown("---")
if st.sidebar.button("☢️ RESTART", use_container_width=True, disabled=is_disabled):
    for key in list(st.session_state.keys()):
        del st.session_state[key]
    st.rerun()

if is_disabled:
    st.sidebar.caption("⚠️ Stop system to enable Export/Restart.")

# --- 7. MAIN INTERFACE ---
if st.session_state.sys_active:
    vid_area = st.empty()
    st.markdown("---")
    st.subheader("📋 System Audit Log")
    event_table = st.empty()
    event_table.table(st.session_state.log_history)
    st.markdown("---")
    st.subheader("📟 LIVE SYSTEM CONSOLE")
    console_area = st.empty()
    console_area.code(st.session_state.raw_console, language="bash")

    ctx = zmq.Context()
    v_sock = ctx.socket(zmq.SUB); v_sock.setsockopt(zmq.SUBSCRIBE, b""); v_sock.setsockopt(zmq.CONFLATE, 1); v_sock.connect("tcp://localhost:9090")
    l_sock = ctx.socket(zmq.SUB); l_sock.setsockopt(zmq.SUBSCRIBE, b""); l_sock.connect("tcp://localhost:9091")

    last_table_update = time.time()
    while st.session_state.sys_active:
        try:
            v_msg = v_sock.recv(zmq.NOBLOCK)
            vid_area.image(base64.b64decode(v_msg), use_container_width=True)
        except: pass

        try:
            raw_log = l_sock.recv_string(zmq.NOBLOCK)
            st.session_state.raw_console += raw_log + "\n"
            
            # Background Append to local TXT
            with open(LOG_TXT, "a") as f:
                f.write(raw_log + "\n")

            if len(st.session_state.raw_console) > 5000: st.session_state.raw_console = st.session_state.raw_console[-5000:]
            console_area.code(st.session_state.raw_console, language="bash")
            
            if "VIOLATION" in raw_log: 
                add_event("🚨 ALERT", "AI_BRAIN", "PPE Violation Detected")
        except: pass
        
        if time.time() - last_table_update > 1.0:
            event_table.table(st.session_state.log_history)
            last_table_update = time.time()
        time.sleep(0.01)
else:
    st.markdown(f"""
    <div style="background-color: black; height: 450px; width: 100%; border-radius: 10px; display: flex; flex-direction: column; justify-content: center; align-items: center; border: 2px solid #333;">
        <h1 style="color: #ff4b4b; font-size: 100px; margin: 0;">🚫</h1>
        <h2 style="color: white; font-family: sans-serif; margin-top: 10px;">LIVE FEED PAUSED</h2>
        <div style="display: flex; align-items: center; margin-top: 10px;">
            <div style="height: 15px; width: 15px; background-color: #ff4b4b; border-radius: 50%; margin-right: 10px; animation: blinker 1.5s linear infinite;"></div>
            <p style="color: #888; font-family: monospace; margin: 0;">SIGNAL OFFLINE - STANDBY MODE</p>
        </div>
    </div>
    <style>@keyframes blinker {{ 50% {{ opacity: 0; }} }}</style>
    """, unsafe_allow_html=True)
    st.markdown("---")
    st.subheader("📋 Session Audit Trail (Archived)")
    if st.session_state.log_history: st.table(st.session_state.log_history)
    else: st.info("No activity recorded in this session.")

#python -m streamlit run dashboard.py