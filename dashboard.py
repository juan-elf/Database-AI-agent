"""
dashboard.py — Universal SQL Agent Dashboard (Redesigned)
"""
import json
import os
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))

DATA_DIR  = PROJECT_ROOT / "data"
LOGS_DIR  = PROJECT_ROOT / "logs"


def _inject_secrets() -> None:
    """On Streamlit Cloud, read st.secrets and inject into os.environ.

    agent.py reads keys via os.getenv() after load_dotenv(). Injecting
    here (before any agent import) makes cloud secrets visible to agent.py
    without modifying agent.py at all. No-op when running locally with .env.
    """
    try:
        secrets = st.secrets
        for key in ("MINIMAX_API_KEY", "TAVILY_API_KEY"):
            if not os.environ.get(key) and key in secrets:
                os.environ[key] = secrets[key]
    except Exception:
        pass


_inject_secrets()

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="SQL Agent Dashboard",
    page_icon="🔍",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── CSS ───────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
#MainMenu, footer, header { visibility: hidden; }

.stApp {
    background: linear-gradient(135deg, #D8EFE0 0%, #F8FAFF 45%, #EDE7F6 100%);
    font-family: 'Segoe UI', 'Inter', sans-serif;
}
.block-container {
    padding: 1.5rem 2rem 1rem !important;
    max-width: 100% !important;
}

/* ── Sidebar ─────────────────────────────────────────── */
[data-testid="stSidebar"] {
    background: #FFFFFF !important;
    border-right: 1px solid #EFEFEF;
    box-shadow: 3px 0 20px rgba(0,0,0,0.04);
}
[data-testid="stSidebar"] > div:first-child { padding: 1.5rem 1rem; }

[data-testid="stSidebar"] .stButton > button {
    background: transparent !important;
    border: none !important;
    border-radius: 10px !important;
    color: #666 !important;
    font-weight: 500 !important;
    font-size: 14px !important;
    text-align: left !important;
    padding: 10px 14px !important;
    margin: 2px 0 !important;
    box-shadow: none !important;
    width: 100% !important;
    transition: all 0.15s ease !important;
}
[data-testid="stSidebar"] .stButton > button:hover {
    background: #F5F1FF !important;
    color: #7C5CFC !important;
}
[data-testid="stSidebar"] .stButton > button[kind="primary"] {
    background: linear-gradient(135deg,#7C5CFC,#9B7DFF) !important;
    color: white !important;
    font-weight: 600 !important;
}

/* ── Card base ───────────────────────────────────────── */
[data-testid="stPlotlyChart"] {
    background: white;
    border-radius: 18px;
    padding: 8px 12px;
    box-shadow: 0 2px 14px rgba(0,0,0,0.05);
}
[data-testid="stDataFrame"] {
    background: white;
    border-radius: 16px;
    box-shadow: 0 2px 14px rgba(0,0,0,0.05);
    overflow: hidden;
}
[data-testid="stExpander"] {
    background: white;
    border-radius: 12px !important;
    border: 1px solid #F0F0F5 !important;
    box-shadow: 0 1px 6px rgba(0,0,0,0.04);
}

/* ── KPI card ────────────────────────────────────────── */
.kpi {
    background: white;
    border-radius: 18px;
    padding: 22px;
    box-shadow: 0 2px 14px rgba(0,0,0,0.05);
}
.kpi-icon {
    width: 46px; height: 46px;
    border-radius: 13px;
    display: flex; align-items: center; justify-content: center;
    font-size: 22px; margin-bottom: 14px;
}
.kpi-val { font-size: 34px; font-weight: 800; color: #1A1A2E; line-height: 1; letter-spacing: -1px; }
.kpi-lbl { font-size: 13px; color: #A0A0B0; font-weight: 500; margin-top: 5px; }
.kpi-delta { font-size: 11.5px; margin-top: 8px; font-weight: 600; color: #94A3B8; }

/* ── Page header ─────────────────────────────────────── */
.pg-hdr { display:flex; justify-content:space-between; align-items:flex-end; margin-bottom:22px; }
.pg-title { font-size:26px; font-weight:800; color:#1A1A2E; margin:0; }
.pg-sub { font-size:13px; color:#B0B0C0; margin:2px 0 0 0; }
.pg-badge {
    background:white; border-radius:20px; padding:6px 14px;
    font-size:12px; color:#888; box-shadow:0 2px 8px rgba(0,0,0,0.06);
}

/* ── Section card ────────────────────────────────────── */
.s-card {
    background: white; border-radius: 18px;
    padding: 22px 24px; box-shadow: 0 2px 14px rgba(0,0,0,0.05);
    margin-bottom: 4px;
}
.s-title { font-size:16px; font-weight:700; color:#1A1A2E; margin-bottom:2px; }
.s-sub   { font-size:12px; color:#B0B0C0; margin-bottom:14px; }

/* ── Scrollbar ───────────────────────────────────────── */
::-webkit-scrollbar { width: 5px; }
::-webkit-scrollbar-track { background: #F5F5F5; }
::-webkit-scrollbar-thumb { background: #D0D0E0; border-radius: 3px; }
</style>
""", unsafe_allow_html=True)

# ── Dark mode CSS (injected on toggle) ────────────────────────────────────────
_DARK_CSS = """
<style>
.stApp {
    background: linear-gradient(135deg, #0D0D1A 0%, #141428 50%, #0F1A2E 100%) !important;
}
[data-testid="stSidebar"] {
    background: #13131F !important;
    border-right: 1px solid #252538 !important;
    box-shadow: 3px 0 20px rgba(0,0,0,0.3) !important;
}
[data-testid="stSidebar"] .stButton > button {
    color: #9090B0 !important;
}
[data-testid="stSidebar"] .stButton > button:hover {
    background: #2A2A4A !important;
    color: #AA99FF !important;
}
[data-testid="stPlotlyChart"] { background: #1C1C2E !important; }
[data-testid="stDataFrame"]   { background: #1C1C2E !important; }
[data-testid="stExpander"]    {
    background: #1C1C2E !important;
    border-color: #252538 !important;
}
.kpi    { background: #1C1C2E !important; }
.s-card { background: #1C1C2E !important; }
.kpi-val  { color: #E0E0FF !important; }
.kpi-lbl  { color: #5858A0 !important; }
.s-title  { color: #E0E0FF !important; }
.s-sub    { color: #5858A0 !important; }
.pg-title { color: #E0E0FF !important; }
.pg-sub   { color: #6060A0 !important; }
.pg-badge { background: #1C1C2E !important; color: #7070A0 !important; }
[data-testid="stChatMessage"] { background: #1C1C2E !important; }
.stMarkdown p, .stMarkdown li,
.stMarkdown h1, .stMarkdown h2, .stMarkdown h3 { color: #D0D0F0 !important; }
::-webkit-scrollbar-track { background: #1C1C2E; }
::-webkit-scrollbar-thumb { background: #3A3A5E; }
</style>
"""

if "dark" not in st.session_state:
    st.session_state.dark = False

if st.session_state.dark:
    st.markdown(_DARK_CSS, unsafe_allow_html=True)

# ── Helpers ───────────────────────────────────────────────────────────────────

def get_db_files():
    return sorted(DATA_DIR.glob("*.db")) if DATA_DIR.exists() else []

def get_log_files():
    return sorted(LOGS_DIR.glob("*.jsonl"), reverse=True) if LOGS_DIR.exists() else []

def parse_log(path: Path) -> list[dict]:
    out = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                if s := line.strip():
                    try: out.append(json.loads(s))
                    except: pass
    except: pass
    return out

def strip_think(text: str) -> str:
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()

def load_sessions() -> list[dict]:
    result = []
    for lp in get_log_files():
        evts = parse_log(lp)
        if not evts: continue
        sid = evts[0].get("session_id", "?")
        ts  = evts[0].get("timestamp", "")
        u   = [e for e in evts if e.get("event") == "user_message"]
        tc  = [e for e in evts if e.get("event") == "tool_call"]
        err = [e for e in evts if e.get("event") == "error"]
        am  = [e for e in evts if e.get("event") == "assistant_message"]
        total_tok = 0
        if am:
            usage = am[-1].get("token_usage") or {}
            total_tok = usage.get("total_input", 0) + usage.get("total_output", 0)
        result.append({
            "session_id": sid, "log_path": lp, "start_time": ts,
            "questions": len(u), "tool_calls": len(tc),
            "errors": len(err), "total_tokens": total_tok, "events": evts,
        })
    return result

def query_db(db_path: Path, sql: str) -> pd.DataFrame:
    try:
        conn = sqlite3.connect(db_path)
        df = pd.read_sql_query(sql, conn)
        conn.close()
        return df
    except Exception:
        return pd.DataFrame()


_TIME_KW = {"cycle", "date", "time", "year", "month", "day", "week", "hour", "period", "step"}

def auto_chart(df: pd.DataFrame) -> go.Figure | None:
    """Pick the best chart for a query result. Returns None if not worth charting."""
    if df.empty or len(df) < 2:
        return None

    num_cols = df.select_dtypes(include="number").columns.tolist()
    if not num_cols:
        return None

    all_cols  = df.columns.tolist()
    cat_cols  = [c for c in all_cols if c not in num_cols]
    time_cols = [c for c in all_cols if any(k in c.lower() for k in _TIME_KW)]

    # ── time/sequence axis → line chart ──────────────────────────────────────
    if time_cols:
        x      = time_cols[0]
        y_cols = [c for c in num_cols if c != x]
        if not y_cols:
            return None
        rem_cats = [c for c in cat_cols if c != x]
        color    = rem_cats[0] if rem_cats else None

        if len(y_cols) == 1:
            fig = px.line(df, x=x, y=y_cols[0], color=color,
                          color_discrete_sequence=COLORS)
        else:
            id_vars = [x] + ([color] if color else [])
            df_m = df[id_vars + y_cols[:4]].melt(
                id_vars=id_vars, value_vars=y_cols[:4],
                var_name="Metrik", value_name="Nilai",
            )
            fig = px.line(df_m, x=x, y="Nilai",
                          color="Metrik" if not color else color,
                          color_discrete_sequence=COLORS)
        return fmt(fig, h=280)

    # ── categorical + numeric → bar chart ────────────────────────────────────
    if cat_cols and num_cols:
        fig = px.bar(df.head(25), x=cat_cols[0], y=num_cols[0],
                     color=cat_cols[1] if len(cat_cols) > 1 else None,
                     color_discrete_sequence=COLORS)
        return fmt(fig, h=280)

    # ── two numeric columns → scatter ─────────────────────────────────────────
    if len(num_cols) >= 2:
        fig = px.scatter(df, x=num_cols[0], y=num_cols[1],
                         color_discrete_sequence=COLORS)
        return fmt(fig, h=280)

    return None


def _show_auto_chart(tool_calls: list[dict], db_path: Path | None) -> None:
    """Re-run the last execute_sql result and render an auto-chart if the data is chartable."""
    if not db_path:
        return
    sql_calls = [tc for tc in tool_calls if tc.get("tool") == "execute_sql"]
    if not sql_calls:
        return
    args = sql_calls[-1].get("args", {})
    if isinstance(args, str):
        try:    args = json.loads(args)
        except: args = {}
    sql = args.get("sql", "")
    if not sql:
        return
    df  = query_db(db_path, sql)
    fig = auto_chart(df)
    if fig is not None:
        with st.expander("📈 Visualisasi Otomatis", expanded=True):
            st.plotly_chart(fig, use_container_width=True, config=_PCFG)

def kpi(icon: str, value, label: str, bg: str) -> str:
    return f"""
    <div class="kpi">
        <div class="kpi-icon" style="background:{bg};">{icon}</div>
        <div class="kpi-val">{value}</div>
        <div class="kpi-lbl">{label}</div>
    </div>"""

def page_header(title: str, subtitle: str):
    today = datetime.now().strftime("%d %B %Y")
    st.markdown(f"""
    <div class="pg-hdr">
      <div><p class="pg-title">{title}</p><p class="pg-sub">{subtitle}</p></div>
      <div class="pg-badge">📅 {today}</div>
    </div>""", unsafe_allow_html=True)

# ── Chart helpers ─────────────────────────────────────────────────────────────

COLORS = ["#7C5CFC", "#5B8CFF", "#22C55E", "#FF7043", "#FF9800", "#EC4899"]

def _layout():
    dark = st.session_state.get("dark", False)
    return dict(
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        margin=dict(l=8, r=8, t=38, b=8),
        font=dict(family="Segoe UI, Inter, sans-serif", size=12,
                  color="#AAAACC" if dark else "#666"),
        xaxis=dict(gridcolor="#2A2A4E" if dark else "#F0F0F8",
                   linecolor="#2A2A4E" if dark else "#F0F0F8", zeroline=False),
        yaxis=dict(gridcolor="#2A2A4E" if dark else "#F0F0F8",
                   linecolor="#2A2A4E" if dark else "#F0F0F8", zeroline=False),
        legend=dict(orientation="h", yanchor="bottom", y=-0.28, xanchor="center", x=0.5),
    )

def fmt(fig, title="", h=280):
    dark = st.session_state.get("dark", False)
    fig.update_layout(
        title=dict(text=title,
                   font=dict(size=14, color="#E0E0FF" if dark else "#1A1A2E", weight=700),
                   x=0),
        height=h, **_layout(),
    )
    return fig

_PCFG = {"displayModeBar": False}

# ══════════════════════════════════════════════════════════════════════════════
# SIDEBAR
# ══════════════════════════════════════════════════════════════════════════════

with st.sidebar:
    st.markdown("""
    <div style="display:flex;align-items:center;gap:10px;padding:4px 4px 20px 4px;">
        <div style="background:linear-gradient(135deg,#7C5CFC,#5B8CFF);width:40px;height:40px;
                    border-radius:11px;display:flex;align-items:center;justify-content:center;
                    font-size:20px;">🔍</div>
        <div>
            <div style="font-weight:800;font-size:14px;color:#1A1A2E;line-height:1.3;">Universal SQL</div>
            <div style="font-size:11px;color:#B0B0C0;">Agent Dashboard</div>
        </div>
    </div>""", unsafe_allow_html=True)

    if "page" not in st.session_state:
        st.session_state.page = "dashboard"

    NAV = [
        ("📊", "Dashboard",    "dashboard"),
        ("💬", "Chat",         "chat"),
        ("🗄️", "DB Explorer",  "explorer"),
        ("📋", "Riwayat Sesi", "history"),
        ("📈", "Analytics",    "analytics"),
    ]
    for icon, label, key in NAV:
        active = st.session_state.page == key
        if st.button(f"{icon}  {label}", key=f"nav_{key}",
                     type="primary" if active else "secondary",
                     use_container_width=True):
            st.session_state.page = key
            st.rerun()

    st.divider()

    # DB selector
    st.markdown('<div style="font-size:11px;font-weight:700;color:#C0C0D0;letter-spacing:.6px;margin-bottom:6px;">DATABASE</div>', unsafe_allow_html=True)
    db_files = get_db_files()
    sel_db = None
    if db_files:
        sel_db_name = st.selectbox("db", [f.name for f in db_files], label_visibility="collapsed")
        sel_db = DATA_DIR / sel_db_name
    else:
        st.warning("Tidak ada .db di folder data/")

    st.markdown('<div style="font-size:11px;font-weight:700;color:#C0C0D0;letter-spacing:.6px;margin:10px 0 6px;">DOMAIN PACK</div>', unsafe_allow_html=True)
    try:
        from agent import list_available_domains
        domains = list_available_domains()
    except Exception:
        domains = []
    dom_label = st.selectbox("dom", ["(none)"] + domains, label_visibility="collapsed")
    sel_dom = None if dom_label == "(none)" else dom_label

    st.markdown("<div style='height:6px'></div>", unsafe_allow_html=True)
    if sel_db:
        if st.button("🚀  Inisialisasi Agent", type="primary", use_container_width=True, key="init_btn"):
            with st.spinner("Connecting..."):
                try:
                    from database import set_database
                    from agent import Agent
                    set_database(sel_db)
                    st.session_state.agent = Agent(domain=sel_dom, verbose=False, enable_logging=True)
                    st.session_state.chat_history = []
                    st.session_state.db_path = sel_db
                    st.success(f"✅ {sel_db.name}")
                except Exception as e:
                    st.error(str(e))

    # Status card
    if "agent" in st.session_state:
        stats = st.session_state.agent.get_stats()
        try:
            from web_search import is_available as _web
            web_ico = "🌐" if _web() else "⚪"
        except Exception:
            web_ico = "⚪"
        st.markdown(f"""
        <div style="background:linear-gradient(135deg,#7C5CFC,#5B8CFF);
                    border-radius:16px;padding:18px;margin-top:14px;color:white;">
            <div style="font-size:13px;font-weight:700;margin-bottom:10px;">⚡ Agent Aktif</div>
            <div style="font-size:11.5px;opacity:.9;line-height:1.9;">
                📂 {st.session_state.db_path.name}<br>
                🎯 {stats['domain']}<br>
                {web_ico} Web search<br>
                🔑 {stats['session_id']}<br>
                🪙 {stats['total_tokens']:,} tokens
            </div>
        </div>""", unsafe_allow_html=True)
        st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)
        if st.button("🔄  Reset Chat", use_container_width=True, key="reset_btn"):
            st.session_state.agent.reset()
            st.session_state.chat_history = []
            st.rerun()
    else:
        st.markdown("""
        <div style="background:#F8F5FF;border-radius:16px;padding:20px;margin-top:14px;
                    text-align:center;border:1.5px dashed #D0C0FF;">
            <div style="font-size:28px;margin-bottom:8px;">🚀</div>
            <div style="font-size:12px;color:#7C5CFC;font-weight:600;">Pilih database &<br>inisialisasi agent</div>
        </div>""", unsafe_allow_html=True)

    # ── Theme toggle ──────────────────────────────────────────────────────────
    st.divider()
    is_dark  = st.session_state.get("dark", False)
    tog_icon  = "☀️" if is_dark else "🌙"
    tog_label = f"{tog_icon}  Light Mode" if is_dark else f"{tog_icon}  Dark Mode"
    if st.button(tog_label, use_container_width=True, key="theme_btn"):
        st.session_state.dark = not is_dark
        st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
# PAGE ROUTER
# ══════════════════════════════════════════════════════════════════════════════

page = st.session_state.page
sessions = load_sessions()

# ─────────────────────────────────────────────────────────────────────────────
# DASHBOARD
# ─────────────────────────────────────────────────────────────────────────────
if page == "dashboard":
    page_header("Dashboard", "Overview Universal SQL Agent")

    # Aggregates
    total_q  = sum(s["questions"]   for s in sessions)
    total_tc = sum(s["tool_calls"]  for s in sessions)
    total_er = sum(s["errors"]      for s in sessions)
    total_tok = sum(s["total_tokens"] for s in sessions)
    tool_counts: dict[str, int] = {}
    for s in sessions:
        for e in s["events"]:
            if e.get("event") == "tool_call":
                t = e.get("tool", "?")
                tool_counts[t] = tool_counts.get(t, 0) + 1

    tok_str = f"{total_tok//1000}K" if total_tok >= 1000 else str(total_tok)

    c1, c2, c3, c4 = st.columns(4)
    c1.markdown(kpi("🗂️", len(sessions), "Total Sesi",       "#FFF0E8"), unsafe_allow_html=True)
    c2.markdown(kpi("💬", total_q,        "Total Pertanyaan", "#E8F5E9"), unsafe_allow_html=True)
    c3.markdown(kpi("🔧", total_tc,       "Tool Calls",       "#F3E5F5"), unsafe_allow_html=True)
    c4.markdown(kpi("🪙", tok_str,        "Total Tokens",     "#E3F2FD"), unsafe_allow_html=True)

    st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)

    # Row 1 charts
    db_path = st.session_state.get("db_path")
    col_a, col_b = st.columns([3, 2])

    with col_a:
        if db_path:
            df_b = query_db(db_path, "SELECT battery_id, cycle, soh FROM battery_cycles ORDER BY battery_id, cycle")
            if not df_b.empty:
                fig = px.line(df_b, x="cycle", y="soh", color="battery_id",
                              color_discrete_sequence=COLORS,
                              labels={"soh": "SOH (%)", "cycle": "Siklus", "battery_id": "Baterai"})
                fig.add_hline(y=80, line_dash="dash", line_color="#EF4444",
                              annotation_text="EOL 80%",
                              annotation_font=dict(color="#EF4444", size=11))
                fmt(fig, "State of Health (SOH) — Degradasi Baterai", h=300)
                st.plotly_chart(fig, use_container_width=True, config=_PCFG)
            else:
                st.info("Inisialisasi agent dengan battery.db untuk melihat grafik SOH.")
        else:
            st.info("Inisialisasi agent untuk melihat grafik.")

    with col_b:
        if tool_counts:
            fig = px.pie(names=list(tool_counts.keys()), values=list(tool_counts.values()),
                         color_discrete_sequence=COLORS, hole=0.48)
            fig.update_traces(textposition="outside", textinfo="percent+label", textfont_size=11)
            fmt(fig, "Tool Usage", h=300)
            fig.update_layout(legend=dict(orientation="v", yanchor="middle", y=0.5, xanchor="left", x=1.02))
            st.plotly_chart(fig, use_container_width=True, config=_PCFG)
        else:
            st.info("Belum ada data tool calls.")

    # Row 2 charts
    col_c, col_d = st.columns(2)

    with col_c:
        if db_path:
            df_cap = query_db(db_path, "SELECT battery_id, cycle, capacity FROM battery_cycles ORDER BY battery_id, cycle")
            if not df_cap.empty:
                fig = px.line(df_cap, x="cycle", y="capacity", color="battery_id",
                              color_discrete_sequence=COLORS,
                              labels={"capacity": "Kapasitas (Ah)", "cycle": "Siklus", "battery_id": "Baterai"})
                fmt(fig, "Degradasi Kapasitas", h=260)
                st.plotly_chart(fig, use_container_width=True, config=_PCFG)

    with col_d:
        all_in = all_out = 0
        for s in sessions:
            am = [e for e in s["events"] if e.get("event") == "assistant_message"]
            if am:
                u = am[-1].get("token_usage") or {}
                all_in  += u.get("total_input", 0)
                all_out += u.get("total_output", 0)
        if all_in + all_out > 0:
            fig = go.Figure([
                go.Bar(name="Input",  x=["Tokens"], y=[all_in],  marker_color="#7C5CFC"),
                go.Bar(name="Output", x=["Tokens"], y=[all_out], marker_color="#5B8CFF"),
            ])
            fig.update_layout(barmode="group")
            fmt(fig, "Input vs Output Tokens", h=260)
            st.plotly_chart(fig, use_container_width=True, config=_PCFG)

    # Session table
    if sessions:
        st.markdown('<div class="s-card"><div class="s-title">Riwayat Sesi</div><div class="s-sub">Log percakapan yang telah direkam</div>', unsafe_allow_html=True)
        df_s = pd.DataFrame([{
            "Session ID": s["session_id"],
            "Waktu": s["start_time"][:16].replace("T", " "),
            "Pertanyaan": s["questions"],
            "Tool Calls": s["tool_calls"],
            "Tokens": s["total_tokens"],
            "Errors": s["errors"],
        } for s in sessions])
        st.dataframe(df_s, use_container_width=True, hide_index=True)
        st.markdown("</div>", unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────────────────────────
# CHAT
# ─────────────────────────────────────────────────────────────────────────────
elif page == "chat":
    page_header("Chat", "Tanya agent dalam bahasa natural")

    if "agent" not in st.session_state:
        st.markdown("""
        <div style="background:white;border-radius:18px;padding:56px;text-align:center;
                    box-shadow:0 2px 16px rgba(0,0,0,0.05);">
            <div style="font-size:52px;margin-bottom:14px;">🤖</div>
            <div style="font-size:20px;font-weight:700;color:#1A1A2E;margin-bottom:8px;">Agent belum diinisialisasi</div>
            <div style="font-size:14px;color:#B0B0C0;">Pilih database di sidebar dan klik <strong>Inisialisasi Agent</strong></div>
        </div>""", unsafe_allow_html=True)
    else:
        agent_inst = st.session_state.agent
        if "chat_history" not in st.session_state:
            st.session_state.chat_history = []

        for msg in st.session_state.chat_history:
            with st.chat_message(msg["role"]):
                if msg["role"] == "assistant":
                    st.markdown(msg["content"])
                    if msg.get("tool_calls"):
                        with st.expander(f"🔧 {len(msg['tool_calls'])} tool call(s)"):
                            for tc in msg["tool_calls"]:
                                st.markdown(f"**`{tc['tool']}`**")
                                args = tc.get("args", {})
                                if isinstance(args, str):
                                    try: args = json.loads(args)
                                    except: pass
                                st.json(args)
                        _show_auto_chart(msg["tool_calls"], st.session_state.get("db_path"))
                else:
                    st.write(msg["content"])

        if prompt := st.chat_input("Tanya tentang database..."):
            st.session_state.chat_history.append({"role": "user", "content": prompt})
            with st.chat_message("user"):
                st.write(prompt)

            with st.chat_message("assistant"):
                with st.spinner("Berpikir..."):
                    log_path, events_before = None, 0
                    if agent_inst.logger:
                        log_path = Path(agent_inst.logger.get_log_path())
                        if log_path.exists():
                            events_before = sum(1 for _ in log_path.open(encoding="utf-8"))
                    response = agent_inst.chat(prompt)
                    tool_calls_used = []
                    if log_path and log_path.exists():
                        new_evts = parse_log(log_path)[events_before:]
                        tool_calls_used = [
                            {"tool": e["tool"], "args": e.get("arguments", {})}
                            for e in new_evts if e.get("event") == "tool_call"
                        ]
                clean = strip_think(response)
                st.markdown(clean)
                if tool_calls_used:
                    with st.expander(f"🔧 {len(tool_calls_used)} tool call(s)"):
                        for tc in tool_calls_used:
                            st.markdown(f"**`{tc['tool']}`**")
                            args = tc.get("args", {})
                            if isinstance(args, str):
                                try: args = json.loads(args)
                                except: pass
                            st.json(args)
                    _show_auto_chart(tool_calls_used, st.session_state.get("db_path"))

            st.session_state.chat_history.append({
                "role": "assistant", "content": clean, "tool_calls": tool_calls_used,
            })


# ─────────────────────────────────────────────────────────────────────────────
# DB EXPLORER
# ─────────────────────────────────────────────────────────────────────────────
elif page == "explorer":
    page_header("Database Explorer", "Jelajahi skema dan data")

    db_exp = st.session_state.get("db_path")
    if db_exp is None:
        if not db_files:
            st.info("Tidak ada database di folder data/")
            st.stop()
        db_exp = DATA_DIR / st.selectbox("Pilih database", [f.name for f in db_files])

    conn = sqlite3.connect(db_exp)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")
    tables = [r[0] for r in cur.fetchall()]

    left, right = st.columns([1, 2], gap="medium")

    with left:
        st.markdown(f'<div class="s-card"><div class="s-title">📂 {db_exp.name}</div><div class="s-sub">{len(tables)} table(s)</div>', unsafe_allow_html=True)
        for tbl in tables:
            cur.execute(f"SELECT COUNT(*) FROM [{tbl}]")
            rc = cur.fetchone()[0]
            cur.execute(f"PRAGMA table_info({tbl})")
            col_info = cur.fetchall()
            with st.expander(f"🗂️ **{tbl}** — {rc:,} rows"):
                rows_d = [{"Column": c[1], "Type": c[2] or "?",
                           "Flags": ("PK " if c[5] else "") + ("NN" if c[3] else "")}
                          for c in col_info]
                st.dataframe(pd.DataFrame(rows_d), hide_index=True, use_container_width=True)
        st.markdown("</div>", unsafe_allow_html=True)

    with right:
        st.markdown('<div class="s-card"><div class="s-title">Data Preview</div>', unsafe_allow_html=True)
        if tables:
            sel_tbl = st.selectbox("Tabel", tables)
            lim = st.slider("Rows", 10, 500, 100, step=10)
            df_prev = query_db(db_exp, f"SELECT * FROM [{sel_tbl}] LIMIT {lim}")
            if not df_prev.empty:
                st.dataframe(df_prev, use_container_width=True, height=340)
                st.caption(f"{len(df_prev)} rows")
            else:
                st.warning("Tabel kosong atau gagal diakses.")
        st.markdown("</div>", unsafe_allow_html=True)

    conn.close()

    # Quick charts
    st.markdown("### 📈 Quick Charts")

    if "battery_cycles" in tables:
        df_all = query_db(db_exp, "SELECT * FROM battery_cycles ORDER BY battery_id, cycle")
        if not df_all.empty:
            ch1, ch2 = st.columns(2)
            with ch1:
                fig = px.line(df_all, x="cycle", y="soh", color="battery_id",
                              color_discrete_sequence=COLORS,
                              labels={"soh": "SOH (%)", "cycle": "Siklus", "battery_id": "Baterai"})
                fig.add_hline(y=80, line_dash="dash", line_color="#EF4444", annotation_text="EOL 80%")
                fmt(fig, "State of Health (SOH)", 280)
                st.plotly_chart(fig, use_container_width=True, config=_PCFG)
            with ch2:
                fig = px.line(df_all, x="cycle", y="capacity", color="battery_id",
                              color_discrete_sequence=COLORS,
                              labels={"capacity": "Kapasitas (Ah)", "cycle": "Siklus", "battery_id": "Baterai"})
                fmt(fig, "Degradasi Kapasitas", 280)
                st.plotly_chart(fig, use_container_width=True, config=_PCFG)

            ch3, ch4 = st.columns(2)
            with ch3:
                fig = px.line(df_all, x="cycle", y="rul", color="battery_id",
                              color_discrete_sequence=COLORS,
                              labels={"rul": "RUL (siklus)", "cycle": "Siklus", "battery_id": "Baterai"})
                fmt(fig, "Remaining Useful Life (RUL)", 280)
                st.plotly_chart(fig, use_container_width=True, config=_PCFG)
            with ch4:
                first_bat = df_all["battery_id"].iloc[0]
                df_t = df_all[df_all["battery_id"] == first_bat].melt(
                    id_vars=["cycle"],
                    value_vars=["charge_temperature", "discharge_temperature"],
                    var_name="Phase", value_name="Suhu (°C)")
                df_t["Phase"] = df_t["Phase"].str.replace("_temperature", "")
                fig = px.line(df_t, x="cycle", y="Suhu (°C)", color="Phase",
                              color_discrete_sequence=["#EF4444", "#7C5CFC"],
                              labels={"cycle": "Siklus"})
                fmt(fig, f"Profil Suhu ({first_bat})", 280)
                st.plotly_chart(fig, use_container_width=True, config=_PCFG)
    else:
        df_g = query_db(db_exp, f"SELECT * FROM [{tables[0]}] LIMIT 1000") if tables else pd.DataFrame()
        if not df_g.empty:
            num_cols = df_g.select_dtypes(include="number").columns.tolist()
            all_cols = df_g.columns.tolist()
            if len(num_cols) >= 2:
                g1, g2, g3 = st.columns(3)
                x_col = g1.selectbox("X", all_cols)
                y_col = g2.selectbox("Y", num_cols, index=min(1, len(num_cols)-1))
                c_col = g3.selectbox("Color", ["(none)"] + all_cols)
                fig = px.scatter(df_g, x=x_col, y=y_col,
                                 color=None if c_col == "(none)" else c_col,
                                 color_discrete_sequence=COLORS)
                fmt(fig, f"{y_col} vs {x_col}", 300)
                st.plotly_chart(fig, use_container_width=True, config=_PCFG)


# ─────────────────────────────────────────────────────────────────────────────
# SESSION HISTORY
# ─────────────────────────────────────────────────────────────────────────────
elif page == "history":
    page_header("Riwayat Sesi", "Log percakapan dengan agent")

    if not sessions:
        st.markdown("""
        <div style="background:white;border-radius:18px;padding:56px;text-align:center;
                    box-shadow:0 2px 16px rgba(0,0,0,0.05);">
            <div style="font-size:52px;margin-bottom:14px;">📭</div>
            <div style="font-size:18px;font-weight:700;color:#1A1A2E;margin-bottom:8px;">Belum ada log</div>
            <div style="font-size:14px;color:#B0B0C0;">Jalankan agent untuk generate session log</div>
        </div>""", unsafe_allow_html=True)
    else:
        labels = [
            f"{s['start_time'][:16].replace('T',' ')}  ·  {s['session_id']}  ·  {s['questions']} pertanyaan"
            for s in sessions
        ]
        idx = st.selectbox("Pilih sesi", range(len(labels)), format_func=lambda i: labels[i])
        sel = sessions[idx]

        m1, m2, m3, m4 = st.columns(4)
        tok_d = f"{sel['total_tokens']//1000}K" if sel["total_tokens"] >= 1000 else str(sel["total_tokens"])
        m1.markdown(kpi("💬", sel["questions"],  "Pertanyaan", "#E8F5E9"), unsafe_allow_html=True)
        m2.markdown(kpi("🔧", sel["tool_calls"], "Tool Calls",  "#F3E5F5"), unsafe_allow_html=True)
        m3.markdown(kpi("⚠️", sel["errors"],     "Errors",      "#FFF3E8"), unsafe_allow_html=True)
        m4.markdown(kpi("🪙", tok_d,             "Tokens",      "#E3F2FD"), unsafe_allow_html=True)

        st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)
        st.markdown('<div class="s-card"><div class="s-title">Percakapan</div><div class="s-sub">Timeline Q&A sesi ini</div>', unsafe_allow_html=True)

        qa_evts = [e for e in sel["events"] if e.get("event") in ("user_message", "assistant_message", "tool_call")]
        i, qa_num = 0, 1
        while i < len(qa_evts):
            evt = qa_evts[i]
            if evt.get("event") == "user_message":
                ts = evt.get("timestamp", "")[:16].replace("T", " ")
                with st.chat_message("user"):
                    st.caption(f"Q{qa_num} · {ts}")
                    st.write(evt.get("content", ""))
                i += 1
                q_tools, q_resp = [], None
                while i < len(qa_evts) and qa_evts[i].get("event") != "user_message":
                    if qa_evts[i].get("event") == "tool_call":   q_tools.append(qa_evts[i])
                    elif qa_evts[i].get("event") == "assistant_message": q_resp = qa_evts[i]
                    i += 1
                if q_resp:
                    usage = q_resp.get("token_usage") or {}
                    with st.chat_message("assistant"):
                        st.caption(f"input: {usage.get('total_input',0):,} · output: {usage.get('total_output',0):,} tokens")
                        st.markdown(strip_think(q_resp.get("content", "")))
                        if q_tools:
                            with st.expander(f"🔧 {len(q_tools)} tool call(s)"):
                                for tc in q_tools:
                                    st.markdown(f"**`{tc['tool']}`**")
                                    args = tc.get("arguments", {})
                                    if isinstance(args, str):
                                        try: args = json.loads(args)
                                        except: pass
                                    st.json(args)
                qa_num += 1
            else:
                i += 1

        st.markdown("</div>", unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────────────────────────
# ANALYTICS
# ─────────────────────────────────────────────────────────────────────────────
elif page == "analytics":
    page_header("Analytics", "Statistik penggunaan agent")

    if not sessions:
        st.info("Belum ada data untuk dianalisis.")
    else:
        total_q  = sum(s["questions"]   for s in sessions)
        total_tc = sum(s["tool_calls"]  for s in sessions)
        total_er = sum(s["errors"]      for s in sessions)
        all_in = all_out = 0
        tool_counts: dict[str, int] = {}
        for s in sessions:
            am = [e for e in s["events"] if e.get("event") == "assistant_message"]
            if am:
                u = am[-1].get("token_usage") or {}
                all_in  += u.get("total_input", 0)
                all_out += u.get("total_output", 0)
            for e in s["events"]:
                if e.get("event") == "tool_call":
                    t = e.get("tool", "?")
                    tool_counts[t] = tool_counts.get(t, 0) + 1

        total_tok = all_in + all_out
        tok_str = f"{total_tok//1000}K" if total_tok >= 1000 else str(total_tok)

        c1, c2, c3, c4, c5 = st.columns(5)
        c1.markdown(kpi("🗂️", len(sessions), "Sesi",         "#FFF0E8"), unsafe_allow_html=True)
        c2.markdown(kpi("💬", total_q,        "Pertanyaan",   "#E8F5E9"), unsafe_allow_html=True)
        c3.markdown(kpi("🔧", total_tc,       "Tool Calls",   "#F3E5F5"), unsafe_allow_html=True)
        c4.markdown(kpi("⚠️", total_er,       "Errors",       "#FFF3E8"), unsafe_allow_html=True)
        c5.markdown(kpi("🪙", tok_str,        "Total Tokens", "#E3F2FD"), unsafe_allow_html=True)

        st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)

        ch1, ch2 = st.columns(2)
        with ch1:
            if tool_counts:
                fig = px.pie(names=list(tool_counts.keys()), values=list(tool_counts.values()),
                             color_discrete_sequence=COLORS, hole=0.45)
                fig.update_traces(textposition="outside", textinfo="percent+label", textfont_size=11)
                fmt(fig, "Distribusi Tool Usage", 300)
                fig.update_layout(legend=dict(orientation="v", yanchor="middle", y=0.5, xanchor="left", x=1.02))
                st.plotly_chart(fig, use_container_width=True, config=_PCFG)

        with ch2:
            if all_in + all_out > 0:
                fig = go.Figure([
                    go.Bar(name="Input",  x=["Tokens"], y=[all_in],  marker_color="#7C5CFC"),
                    go.Bar(name="Output", x=["Tokens"], y=[all_out], marker_color="#5B8CFF"),
                ])
                fig.update_layout(barmode="group")
                fmt(fig, "Input vs Output Tokens", 300)
                st.plotly_chart(fig, use_container_width=True, config=_PCFG)

        # Detailed table
        st.markdown('<div class="s-card"><div class="s-title">Detail Semua Sesi</div>', unsafe_allow_html=True)
        df_s = pd.DataFrame([{
            "Session ID": s["session_id"],
            "Waktu": s["start_time"][:16].replace("T", " "),
            "Pertanyaan": s["questions"],
            "Tool Calls": s["tool_calls"],
            "Tokens": s["total_tokens"],
            "Errors": s["errors"],
        } for s in sessions])
        st.dataframe(df_s, use_container_width=True, hide_index=True)
        st.markdown("</div>", unsafe_allow_html=True)

        # Multi-session tool breakdown
        if len(sessions) > 1:
            tool_rows = []
            for s in sessions:
                lbl = f"{s['start_time'][:10]} ({s['session_id']})"
                for e in s["events"]:
                    if e.get("event") == "tool_call":
                        tool_rows.append({"Sesi": lbl, "Tool": e.get("tool", "?")})
            if tool_rows:
                df_t = pd.DataFrame(tool_rows).groupby(["Sesi", "Tool"]).size().reset_index(name="Count")
                fig = px.bar(df_t, x="Sesi", y="Count", color="Tool",
                             color_discrete_sequence=COLORS)
                fmt(fig, "Tool Calls per Sesi", 280)
                st.plotly_chart(fig, use_container_width=True, config=_PCFG)
