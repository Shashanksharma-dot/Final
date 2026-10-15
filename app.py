
import streamlit as st
from datetime import datetime

st.set_page_config(
    page_title="Autonomous AI Operator",
    page_icon="🤖",
    layout="wide"
)

if "logs" not in st.session_state:
    st.session_state.logs = ["Operator initialized."]

if "running" not in st.session_state:
    st.session_state.running = False

if "approved" not in st.session_state:
    st.session_state.approved = False

if "break_spreadsheet" not in st.session_state:
    st.session_state.break_spreadsheet = False

if "steps" not in st.session_state:
    st.session_state.steps = [
        {"name": "Research opportunities", "status": "Pending"},
        {"name": "Compare eligibility", "status": "Pending"},
        {"name": "Update spreadsheet", "status": "Pending"},
        {"name": "Verify spreadsheet", "status": "Pending"},
        {"name": "Create email drafts", "status": "Pending"},
        {"name": "Verify email drafts", "status": "Pending"},
    ]


def log_event(message):
    timestamp = datetime.now().strftime("%H:%M:%S")
    st.session_state.logs.insert(0, f"{timestamp} — {message}")


st.title("🤖 Autonomous AI Operator")
st.caption("Plan • Execute • Recover • Verify")

goal = st.text_area(
    "What should the agent accomplish?",
    value=(
        "Find 3 internships suitable for a second-year CSE student "
        "in India, compare eligibility, save results to Excel, "
        "and prepare personalized email drafts."
    ),
    height=100
)

col1, col2 = st.columns([1, 1])

with col1:
    if st.button("Generate plan", use_container_width=True):
        log_event("Mock execution plan generated.")
        st.success("Demo plan generated. LLM integration comes next.")

with col2:
    if st.button("Reset demo", use_container_width=True):
        st.session_state.steps = [
            {"name": "Research opportunities", "status": "Pending"},
            {"name": "Compare eligibility", "status": "Pending"},
            {"name": "Update spreadsheet", "status": "Pending"},
            {"name": "Verify spreadsheet", "status": "Pending"},
            {"name": "Create email drafts", "status": "Pending"},
            {"name": "Verify email drafts", "status": "Pending"},
        ]
        st.session_state.approved = False
        st.session_state.running = False
        log_event("Demo reset.")
        st.rerun()

st.divider()

completed = sum(
    step["status"] == "Completed"
    for step in st.session_state.steps
)
total = len(st.session_state.steps)

c1, c2, c3 = st.columns(3)
c1.metric("Total steps", total)
c2.metric("Completed", completed)
c3.metric("Progress", f"{completed / total * 100:.0f}%")

st.progress(completed / total)

left, right = st.columns([3, 2])

with left:
    st.subheader("Execution plan")

    for index, step in enumerate(st.session_state.steps, start=1):
        st.write(
            f"**{index}. {step['name']}** — {step['status']}"
        )

    st.subheader("Human approval")

    st.write("Sending emails requires explicit approval.")

    a, b = st.columns(2)

    with a:
        if st.button("Approve email sending"):
            st.session_state.approved = True
            log_event("Email sending approved by user.")

    with b:
        if st.button("Reject email sending"):
            st.session_state.approved = False
            log_event("Email sending rejected by user.")

    if st.session_state.approved:
        st.success("Approval recorded for this demo session.")
    else:
        st.warning("Email sending is not approved.")

with right:
    st.subheader("Fault injection")

    st.session_state.break_spreadsheet = st.toggle(
        "Break spreadsheet once",
        value=st.session_state.break_spreadsheet
    )

    st.caption(
        "This toggle is a UI placeholder for now. "
        "Failure injection will be implemented in the backend."
    )

    st.subheader("Audit log")

    for event in st.session_state.logs[:15]:
        st.text(event)

st.divider()
st.caption(
    "Prototype mode: actions are simulated. "
    "No real research, spreadsheet writes, or emails occur yet."
)
