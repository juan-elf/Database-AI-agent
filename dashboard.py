"""
dashboard.py — DataGen Dashboard (Redesigned)
"""
import io
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
        for key in ("OPENROUTER_API_KEY", "GUARDRAIL_API_KEY", "AGENT_MODEL", "GUARDRAIL_MODEL",
                    "TAVILY_API_KEY", "DATABASE_URL", "WRITE_DATABASE_URL"):
            if not os.environ.get(key) and key in secrets:
                os.environ[key] = secrets[key]
    except Exception:
        pass


_inject_secrets()

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="DataGen Dashboard",
    page_icon="🔍",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── CSS ───────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
#MainMenu, footer, header { visibility: hidden; }

/* ═══ Design tokens (light) ═══ */
:root {
    /* Brand */
    --primary:#7C5CFC;
    --primary-2:#5B8CFF;
    --grad:linear-gradient(135deg,#7C5CFC 0%,#5B8CFF 100%);
    --accent:#6D4DF2;            /* text accent on light surfaces */
    --primary-soft:#F1ECFF;      /* hover / selected fill */
    /* Background & surfaces */
    --bg-1:#F7F7FC;
    --bg-2:#F1EFFA;
    --surface:#FFFFFF;
    --surface-2:#F6F3FF;         /* info panels, subtle fills */
    --border:#ECEAF3;
    /* Text */
    --text:#1A1A2E;
    --text-muted:#5B6072;
    --text-faint:#9A9FB0;
    /* Status */
    --success:#16A34A; --success-soft:#E9F9EF;
    --warning:#D97706; --warning-soft:#FDF3E5;
    --danger:#EF4444;  --danger-soft:#FDECEC;
    /* Radius */
    --r-lg:20px; --r:16px; --r-sm:12px; --r-xs:10px;
    /* Elevation */
    --shadow:0 2px 12px rgba(20,20,50,.06);
    --shadow-sm:0 1px 6px rgba(20,20,50,.04);
    --shadow-lg:0 8px 26px rgba(124,92,252,.16);
}

.stApp {
    background: linear-gradient(180deg, var(--bg-1) 0%, var(--bg-2) 100%);
    font-family: 'Inter', 'Segoe UI', sans-serif;
}
.block-container {
    padding: 1.75rem 2rem 2rem !important;
    max-width: 100% !important;
}

/* ── Sidebar ─────────────────────────────────────────── */
[data-testid="stSidebar"] {
    background: var(--surface) !important;
    border-right: 1px solid var(--border);
    box-shadow: 2px 0 18px rgba(20,20,50,.04);
}
[data-testid="stSidebar"] > div:first-child { padding: 1.5rem 1rem; }

[data-testid="stSidebar"] .stButton > button {
    background: transparent !important;
    border: 1px solid transparent !important;
    border-radius: var(--r-xs) !important;
    color: var(--text-muted) !important;
    font-weight: 500 !important;
    font-size: 14px !important;
    text-align: left !important;
    padding: 10px 14px !important;
    margin: 3px 0 !important;
    box-shadow: none !important;
    width: 100% !important;
    transition: background .15s ease, color .15s ease, transform .15s ease !important;
}
[data-testid="stSidebar"] .stButton > button:hover {
    background: var(--primary-soft) !important;
    color: var(--accent) !important;
}
[data-testid="stSidebar"] .stButton > button[kind="primary"] {
    background: var(--grad) !important;
    color: #fff !important;
    font-weight: 600 !important;
    box-shadow: 0 4px 14px rgba(124,92,252,.30) !important;
}

/* ── Surfaces (charts / tables / expanders) ──────────── */
[data-testid="stPlotlyChart"] {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: var(--r);
    padding: 10px 14px;
    box-shadow: var(--shadow);
}
[data-testid="stDataFrame"] {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: var(--r-sm);
    box-shadow: var(--shadow-sm);
    overflow: hidden;
}
[data-testid="stExpander"] {
    background: var(--surface);
    border-radius: var(--r-sm) !important;
    border: 1px solid var(--border) !important;
    box-shadow: var(--shadow-sm);
}

/* ── KPI card ────────────────────────────────────────── */
.kpi {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: var(--r);
    padding: 20px 22px;
    box-shadow: var(--shadow);
    transition: transform .15s ease, box-shadow .15s ease;
}
.kpi:hover { transform: translateY(-2px); box-shadow: var(--shadow-lg); }
.kpi-icon {
    width: 44px; height: 44px;
    border-radius: 12px;
    display: flex; align-items: center; justify-content: center;
    font-size: 21px; margin-bottom: 14px;
}
.kpi-val { font-size: 32px; font-weight: 800; color: var(--text); line-height: 1; letter-spacing: -.5px; }
.kpi-lbl { font-size: 12.5px; color: var(--text-faint); font-weight: 600; margin-top: 6px; }
.kpi-delta { font-size: 11.5px; margin-top: 8px; font-weight: 600; color: var(--text-muted); }

/* ── Page header ─────────────────────────────────────── */
.pg-hdr {
    display:flex; justify-content:space-between; align-items:flex-end;
    margin-bottom:24px; padding-bottom:16px; border-bottom:1px solid var(--border);
}
.pg-title { font-size:25px; font-weight:800; color:var(--text); margin:0; letter-spacing:-.4px; }
.pg-sub { font-size:13px; color:var(--text-faint); margin:4px 0 0 0; }
.pg-badge {
    background:var(--surface); border:1px solid var(--border); border-radius:20px;
    padding:7px 14px; font-size:12px; color:var(--text-muted); box-shadow:var(--shadow-sm);
}

/* ── Section card ────────────────────────────────────── */
.s-card {
    background: var(--surface); border:1px solid var(--border); border-radius: var(--r);
    padding: 20px 22px; box-shadow: var(--shadow);
    margin-bottom: 6px;
}
.s-title { font-size:15.5px; font-weight:700; color:var(--text); margin-bottom:3px; }
.s-sub   { font-size:12.5px; color:var(--text-faint); margin-bottom:14px; }

/* ── Info panel (theme-aware) ────────────────────────── */
.info-panel {
    background:var(--surface-2); border:1px solid var(--border);
    border-left:3px solid var(--primary); border-radius:var(--r-sm);
    padding:16px 20px; margin-bottom:16px;
}
.info-title { font-size:14.5px; font-weight:700; color:var(--text); margin-bottom:6px; }
.info-body  { font-size:13px; color:var(--text-muted); line-height:1.7; }

/* ── Empty state ─────────────────────────────────────── */
.empty-state {
    background:var(--surface); border:1px solid var(--border); border-radius:var(--r);
    padding:52px 32px; text-align:center; box-shadow:var(--shadow);
}
.empty-icon {
    width:60px; height:60px; border-radius:16px; background:var(--primary-soft);
    display:flex; align-items:center; justify-content:center;
    font-size:30px; margin:0 auto 16px;
}
.empty-title { font-size:18px; font-weight:700; color:var(--text); margin-bottom:6px; }
.empty-sub   { font-size:13.5px; color:var(--text-muted); line-height:1.6; }

/* ── Success note (theme-aware) ──────────────────────── */
.note-success {
    background:var(--success-soft); border:1px solid var(--border);
    border-left:3px solid var(--success); border-radius:var(--r-sm);
    padding:16px 20px; color:var(--text-muted); font-size:13.5px; line-height:1.7;
}

/* ── Captions ────────────────────────────────────────── */
[data-testid="stCaptionContainer"], [data-testid="stCaptionContainer"] p {
    color:var(--text-faint) !important;
}

/* ── Inputs (theme-aware via tokens) ─────────────────── */
[data-baseweb="select"] > div {
    background:var(--surface) !important; border-color:var(--border) !important;
}
.stTextArea textarea, .stTextInput input {
    background:var(--surface) !important; color:var(--text) !important;
    border-color:var(--border) !important;
}
[data-testid="stChatInput"] {
    background:var(--surface) !important; border:1px solid var(--border) !important;
    border-radius:var(--r-sm) !important;
}
[data-testid="stChatInput"] textarea { color:var(--text) !important; }

/* ── Chat bubbles ────────────────────────────────────── */
[data-testid="stChatMessage"] {
    background:var(--surface); border:1px solid var(--border); border-radius:var(--r-sm);
}

/* ── Scrollbar ───────────────────────────────────────── */
::-webkit-scrollbar { width: 6px; height: 6px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: #D2CFE3; border-radius: 3px; }
::-webkit-scrollbar-thumb:hover { background: #B9B4D6; }
</style>
""", unsafe_allow_html=True)

# ── Dark mode CSS (injected on toggle) ────────────────────────────────────────
_DARK_CSS = """
<style>
/* ═══ Design tokens (dark) — only the values flip ═══ */
.stApp {
    --accent:#A98BFF;
    --primary-soft:#241F3A;
    --bg-1:#0E0E18;  --bg-2:#131223;
    --surface:#1A1A2A;
    --surface-2:#201F33;
    --border:#2A2A3E;
    --text:#E7E7F6;
    --text-muted:#A7AAC4;
    --text-faint:#717492;
    --success-soft:#14241C;
    --warning-soft:#2A2113;
    --danger-soft:#2A1A1A;
    --shadow:0 2px 14px rgba(0,0,0,.35);
    --shadow-sm:0 1px 6px rgba(0,0,0,.30);
    --shadow-lg:0 8px 26px rgba(0,0,0,.45);
}
/* Force Streamlit's own (light-themed) text legible on dark surfaces */
.stMarkdown p, .stMarkdown li,
.stMarkdown h1, .stMarkdown h2, .stMarkdown h3, .stMarkdown h4,
.stMarkdown strong { color: var(--text) !important; }
::-webkit-scrollbar-thumb { background: #3A3A55; }
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

def empty_state(icon: str, title: str, subtitle: str) -> str:
    """Consistent 'nothing to show yet' block used across pages (theme-aware)."""
    return f"""
    <div class="empty-state">
        <div class="empty-icon">{icon}</div>
        <div class="empty-title">{title}</div>
        <div class="empty-sub">{subtitle}</div>
    </div>"""

def info_panel(title: str, body: str, icon: str = "💡") -> str:
    """Consistent 'how it works' explainer panel (theme-aware)."""
    return f"""
    <div class="info-panel">
        <div class="info-title">{icon} {title}</div>
        <div class="info-body">{body}</div>
    </div>"""

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
# LANDING GATE — pre-initialization screen (only DB + domain pack selection)
# ══════════════════════════════════════════════════════════════════════════════

db_files = get_db_files()   # defined globally so pages can reference it too


def _launch_agent(db_path, domain) -> None:
    """Initialize the agent and stash per-session state."""
    from database import set_database
    from agent import Agent
    set_database(db_path)
    st.session_state.agent = Agent(domain=domain, verbose=False, enable_logging=True)
    st.session_state.chat_history = []
    st.session_state.db_path = db_path
    st.session_state.domain = domain


def _end_session() -> None:
    """Tear down the agent and all per-session state → returns to the landing screen."""
    for k in ("agent", "chat_history", "db_path", "domain",
              "catalog", "catalog_domain", "classify_result",
              "classify_input_cols", "last_report"):
        st.session_state.pop(k, None)
    st.session_state.page = "dashboard"


if "agent" not in st.session_state:
    # Hide the sidebar entirely — landing screen is self-contained
    st.markdown("""
    <style>
      [data-testid="stSidebar"],
      [data-testid="stSidebarCollapsedControl"],
      [data-testid="collapsedControl"] { display:none !important; }
      [data-testid="stVerticalBlockBorderWrapper"]:has(.landing-anchor) {
          background:var(--surface); border:1px solid var(--border) !important;
          border-radius:var(--r-lg) !important; box-shadow:var(--shadow-lg) !important;
      }
    </style>
    """, unsafe_allow_html=True)

    _, mid, _ = st.columns([1, 1.25, 1])
    with mid:
        st.markdown("""
        <div style="text-align:center; padding:7vh 0 18px;">
            <div style="width:74px;height:74px;margin:0 auto 18px;border-radius:20px;
                        background:var(--grad);display:flex;align-items:center;justify-content:center;
                        font-size:36px;box-shadow:var(--shadow-lg);">🔍</div>
            <div style="font-size:30px;font-weight:800;color:var(--text);letter-spacing:-.6px;">DataGen</div>
            <div style="font-size:14px;color:var(--text-faint);margin-top:7px;line-height:1.6;">
                Natural-language SQL agent<br>Pilih database &amp; domain pack untuk mulai
            </div>
        </div>""", unsafe_allow_html=True)

        with st.container(border=True):
            st.markdown('<span class="landing-anchor"></span>', unsafe_allow_html=True)

            st.markdown('<div style="font-size:11px;font-weight:700;color:var(--text-faint);letter-spacing:.6px;margin-bottom:6px;">DATABASE</div>', unsafe_allow_html=True)
            if db_files:
                land_db_name = st.selectbox("db", [f.name for f in db_files],
                                            label_visibility="collapsed", key="land_db")
                land_db = DATA_DIR / land_db_name
            else:
                st.warning("Tidak ada file .db di folder `data/`")
                land_db = None

            st.markdown('<div style="font-size:11px;font-weight:700;color:var(--text-faint);letter-spacing:.6px;margin:14px 0 6px;">DOMAIN PACK</div>', unsafe_allow_html=True)
            try:
                from agent import list_available_domains
                land_domains = list_available_domains()
            except Exception:
                land_domains = []
            land_dom_label = st.selectbox("dom", ["(none)"] + land_domains,
                                          label_visibility="collapsed", key="land_dom")
            land_dom = None if land_dom_label == "(none)" else land_dom_label

            st.markdown("<div style='height:10px'></div>", unsafe_allow_html=True)
            launched = False
            if st.button("🚀  Launch Agent", type="primary", use_container_width=True,
                         disabled=(land_db is None), key="launch_btn"):
                with st.spinner("Menyiapkan agent..."):
                    try:
                        _launch_agent(land_db, land_dom)
                        launched = True
                    except Exception as e:
                        st.error(str(e))
                if launched:
                    st.rerun()

        # Theme toggle (centered, below the card)
        st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)
        _is_dark = st.session_state.get("dark", False)
        _tlabel = "☀️  Light Mode" if _is_dark else "🌙  Dark Mode"
        if st.button(_tlabel, use_container_width=True, key="land_theme_btn"):
            st.session_state.dark = not _is_dark
            st.rerun()

    st.stop()


# ══════════════════════════════════════════════════════════════════════════════
# SIDEBAR
# ══════════════════════════════════════════════════════════════════════════════

with st.sidebar:
    st.markdown("""
    <div style="display:flex;align-items:center;gap:11px;padding:4px 4px 20px 4px;">
        <div style="background:var(--grad);width:40px;height:40px;
                    border-radius:12px;display:flex;align-items:center;justify-content:center;
                    font-size:20px;box-shadow:0 4px 12px rgba(124,92,252,.30);">🔍</div>
        <div>
            <div style="font-weight:800;font-size:15px;color:var(--text);line-height:1.3;">DataGen</div>
            <div style="font-size:11px;color:var(--text-faint);">SQL Agent Dashboard</div>
        </div>
    </div>""", unsafe_allow_html=True)

    if "page" not in st.session_state:
        st.session_state.page = "dashboard"

    NAV = [
        ("📊", "Dashboard",      "dashboard"),
        ("💬", "Chat",           "chat"),
        ("🧠", "Insight Report", "report"),
        ("🧩", "Klasifikasi Data", "classify"),
        ("🗄️", "DB Explorer",    "explorer"),
        ("📋", "Riwayat Sesi",   "history"),
        ("📈", "Analytics",      "analytics"),
    ]
    for icon, label, key in NAV:
        active = st.session_state.page == key
        if st.button(f"{icon}  {label}", key=f"nav_{key}",
                     type="primary" if active else "secondary",
                     use_container_width=True):
            st.session_state.page = key
            st.rerun()

    st.divider()

    # Status card — landing gate guarantees the agent exists here
    stats = st.session_state.agent.get_stats()
    try:
        from web_search import is_available as _web
        web_ico = "🌐" if _web() else "⚪"
    except Exception:
        web_ico = "⚪"
    st.markdown(f"""
    <div style="background:var(--grad);
                border-radius:var(--r);padding:18px;color:white;
                box-shadow:0 6px 18px rgba(124,92,252,.28);">
        <div style="font-size:13px;font-weight:700;margin-bottom:10px;">⚡ Agent Aktif</div>
        <div style="font-size:11.5px;opacity:.92;line-height:1.9;">
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
    if st.button("⏹  End Session", use_container_width=True, key="end_btn"):
        _end_session()
        st.rerun()

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
    page_header("Dashboard", "Overview DataGen")

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
        st.markdown(empty_state(
            "🤖", "Agent belum diinisialisasi",
            "Pilih database di sidebar lalu klik <strong>Inisialisasi Agent</strong>.",
        ), unsafe_allow_html=True)
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
        st.markdown(empty_state(
            "📭", "Belum ada log",
            "Jalankan agent untuk menghasilkan session log.",
        ), unsafe_allow_html=True)
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

# ─────────────────────────────────────────────────────────────────────────────
# INSIGHT REPORT
# ─────────────────────────────────────────────────────────────────────────────
elif page == "report":
    page_header("Insight Report", "Analisis otomatis — satu tombol, laporan lengkap")

    if "agent" not in st.session_state:
        st.markdown(empty_state(
            "🧠", "Agent belum aktif",
            "Inisialisasi agent di sidebar untuk menghasilkan laporan otomatis.",
        ), unsafe_allow_html=True)
    else:
        st.markdown(info_panel(
            "Cara kerja",
            "Agent membaca profil database → merumuskan 6 pertanyaan analitik sendiri → "
            "menjalankan SQL → mendeteksi anomali → mensintesis laporan naratif."
            "<br><strong>Tidak perlu ketik apa pun.</strong>",
            icon="🤖",
        ), unsafe_allow_html=True)

        col_btn, col_info = st.columns([1, 3])
        with col_btn:
            generate = st.button("🚀  Generate Report", type="primary",
                                 use_container_width=True, key="gen_report_btn")
        with col_info:
            if "last_report" in st.session_state:
                r = st.session_state.last_report
                st.caption(f"Laporan terakhir: {r.get('generated_at', '')} · "
                           f"{len(r.get('findings', []))} temuan")

        if generate:
            from insight_report import generate_report

            status_box = st.empty()
            progress_text = st.empty()

            def on_progress(step: str, detail: str) -> None:
                icons = {
                    "profiling":    "📊",
                    "planning":     "🧠",
                    "executing":    "⚡",
                    "synthesizing": "✍️",
                    "done":         "✅",
                }
                icon = icons.get(step, "⏳")
                progress_text.markdown(
                    f'<div style="font-size:13px;color:var(--accent);font-weight:500;">'
                    f'{icon} {detail}</div>',
                    unsafe_allow_html=True,
                )

            with st.spinner("Menghasilkan laporan..."):
                report = generate_report(on_progress=on_progress)

            progress_text.empty()
            st.session_state.last_report = report

            if report.get("errors"):
                for err in report["errors"]:
                    st.error(err)

        # ── Display report ────────────────────────────────────────────────────
        if "last_report" in st.session_state:
            r = st.session_state.last_report

            # Executive summary
            if r.get("executive_summary"):
                st.markdown(f"""
                <div style="background:var(--grad);color:white;
                            border-radius:var(--r);padding:24px 28px;margin:20px 0;
                            box-shadow:0 8px 24px rgba(124,92,252,.24);">
                    <div style="font-size:13px;font-weight:700;opacity:.8;
                                margin-bottom:8px;letter-spacing:.5px;">RINGKASAN EKSEKUTIF</div>
                    <div style="font-size:15px;line-height:1.7;">{r['executive_summary']}</div>
                    <div style="font-size:11px;opacity:.65;margin-top:10px;">
                        {r['generated_at']} · {r['db_label']}
                    </div>
                </div>""", unsafe_allow_html=True)

            # Findings
            findings = r.get("findings", [])
            if findings:
                st.markdown("### Temuan")
                for i, f in enumerate(findings, 1):
                    anomaly_badge = " 🔴 Anomali" if f.get("is_anomaly") else ""
                    with st.expander(f"**{i}. {f['question']}**{anomaly_badge}",
                                     expanded=(i == 1)):
                        if f.get("error"):
                            st.warning(f"SQL gagal: {f['error']}")
                        else:
                            rows = f.get("rows", [])
                            if rows:
                                df_f = pd.DataFrame(rows)
                                st.dataframe(df_f, use_container_width=True,
                                             hide_index=True)
                                fig = auto_chart(df_f)
                                if fig:
                                    fmt(fig, "", 260)
                                    st.plotly_chart(fig, use_container_width=True,
                                                    config=_PCFG)
                            else:
                                st.info("Query mengembalikan 0 baris.")
                        with st.expander("SQL", expanded=False):
                            st.code(f["sql"], language="sql")

            # Recommendations
            if r.get("recommendations"):
                st.markdown("### Rekomendasi")
                st.markdown(f'<div class="note-success">{r["recommendations"]}</div>',
                            unsafe_allow_html=True)

            # Full narrative download
            st.divider()
            st.download_button(
                "⬇️  Download laporan (.md)",
                data=r.get("narrative", ""),
                file_name=f"insight_report_{r['generated_at'].replace(' ', '_').replace(':', '')}.md",
                mime="text/markdown",
                key="download_report",
            )


# ─────────────────────────────────────────────────────────────────────────────
# KLASIFIKASI DATA (router.py — read-only schema matching)
# ─────────────────────────────────────────────────────────────────────────────
elif page == "classify":
    page_header("Klasifikasi Data", "Cocokkan data baru dengan tabel yang ada — read-only, tidak menulis")

    if "agent" not in st.session_state:
        st.markdown(empty_state(
            "🧩", "Agent belum aktif",
            "Inisialisasi agent di sidebar untuk membangun katalog tabel.",
        ), unsafe_allow_html=True)
    else:
        st.markdown(info_panel(
            "Cara kerja",
            "Upload atau paste data baru → dibandingkan dengan semua tabel yang ada "
            "(nama kolom, tipe, rentang nilai) → LLM menentukan tabel paling cocok, "
            "confidence, dan pemetaan kolom."
            "<br><strong>Tidak ada data yang ditulis ke database.</strong>",
            icon="🧭",
        ), unsafe_allow_html=True)

        # ── Build / cache catalog ───────────────────────────────────────────────
        cur_domain = st.session_state.get("domain")
        if (st.session_state.get("catalog") is None
                or st.session_state.get("catalog_domain") != cur_domain):
            with st.spinner("Membangun katalog tabel..."):
                from router import build_catalog
                st.session_state.catalog = build_catalog(domain=cur_domain)
                st.session_state.catalog_domain = cur_domain

        catalog = st.session_state.catalog

        cat_col, btn_col = st.columns([4, 1])
        with cat_col:
            st.caption(f"📚 Katalog: {len(catalog)} tabel — {', '.join(catalog.keys()) or '(kosong)'}")
        with btn_col:
            if st.button("🔄 Refresh", use_container_width=True, key="refresh_catalog"):
                from router import build_catalog
                st.session_state.catalog = build_catalog(domain=cur_domain)
                st.rerun()

        st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)

        # ── Data input ───────────────────────────────────────────────────────────
        input_method = st.radio("Sumber data", ["Upload CSV", "Paste teks"],
                                horizontal=True, label_visibility="collapsed")

        df_input = None
        if input_method == "Upload CSV":
            uploaded = st.file_uploader("Upload file CSV", type=["csv"], key="classify_upload")
            if uploaded is not None:
                try:
                    df_input = pd.read_csv(uploaded)
                except Exception as e:
                    st.error(f"Gagal membaca CSV: {e}")
        else:
            pasted = st.text_area("Paste data (format CSV, baris pertama = header)",
                                  height=140, key="classify_paste",
                                  placeholder="battery_id,cycle,temp_charge\nB9,1,24.1\nB9,2,24.6")
            if pasted.strip():
                try:
                    df_input = pd.read_csv(io.StringIO(pasted))
                except Exception as e:
                    st.error(f"Gagal parse teks: {e}")

        if df_input is not None and not df_input.empty:
            st.markdown('<div class="s-card"><div class="s-title">Preview Data</div>', unsafe_allow_html=True)
            st.dataframe(df_input.head(10), use_container_width=True)
            st.caption(f"{len(df_input)} baris · {len(df_input.columns)} kolom")
            st.markdown("</div>", unsafe_allow_html=True)

            st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)

            if st.button("🔍  Klasifikasi", type="primary", use_container_width=True, key="classify_btn"):
                with st.spinner("Mengklasifikasi data... (~5-10s, memanggil LLM)"):
                    from router import classify_data
                    st.session_state.classify_result = classify_data(df_input, catalog)
                    st.session_state.classify_input_cols = list(df_input.columns)
                    st.session_state.classify_input_df = df_input.copy()
                    st.session_state.pop("insert_result", None)

        # ── Display result ──────────────────────────────────────────────────────
        if "classify_result" in st.session_state:
            result = st.session_state.classify_result
            conf = result.get("confidence", 0)
            needs_new = result.get("is_new_table_needed", False)

            if needs_new or conf < 50:
                badge_bg = "#FEE2E2"; badge_fg = "#DC2626"
            elif conf < 80:
                badge_bg = "#FEF3C7"; badge_fg = "#D97706"
            else:
                badge_bg = "#DCFCE7"; badge_fg = "#16A34A"

            best = result.get("best_match") or "Tidak ada tabel cocok"

            st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)
            st.markdown(f"""
            <div class="s-card">
                <div style="display:flex; justify-content:space-between; align-items:center; gap:16px;">
                    <div>
                        <div class="s-sub" style="margin-bottom:2px;">TABEL REKOMENDASI</div>
                        <div style="font-size:20px;font-weight:800;color:var(--text);">🎯 {best}</div>
                    </div>
                    <div style="background:{badge_bg};color:{badge_fg};border-radius:20px;
                                padding:10px 20px;font-weight:800;font-size:20px;white-space:nowrap;">
                        {conf}%
                    </div>
                </div>
            </div>""", unsafe_allow_html=True)

            if needs_new:
                st.warning("⚠️ Tidak ada tabel yang cukup cocok — data ini mungkin perlu tabel baru "
                           "(belum didukung — lihat roadmap B4 di `plan.md`).")

            if result.get("reasoning"):
                st.markdown(f"""
                <div class="s-card">
                    <div class="s-title">Alasan</div>
                    <div style="color:var(--text-muted);font-size:14px;line-height:1.65;">{result['reasoning']}</div>
                </div>""", unsafe_allow_html=True)

            mapping = result.get("column_mapping", {})
            if mapping:
                st.markdown('<div class="s-card"><div class="s-title">Pemetaan Kolom</div>', unsafe_allow_html=True)
                map_df = pd.DataFrame([
                    {"Kolom Input": k, "→": "→", "Kolom Tabel": v}
                    for k, v in mapping.items()
                ])
                st.dataframe(map_df, use_container_width=True, hide_index=True)
                input_cols = set(st.session_state.get("classify_input_cols", []))
                unmapped = input_cols - set(mapping.keys())
                if unmapped:
                    st.caption(f"⚠️ Tidak terpetakan: {', '.join(sorted(unmapped))}")
                st.markdown("</div>", unsafe_allow_html=True)

            candidates = result.get("candidates", [])
            if candidates:
                with st.expander(f"📋 Semua kandidat ({len(candidates)} tabel, skor heuristik)"):
                    cand_df = pd.DataFrame(candidates)
                    st.dataframe(cand_df, use_container_width=True, hide_index=True)

            # ── B2: Write section ─────────────────────────────────────────────
            best_table = result.get("best_match")
            write_ready = (
                not needs_new
                and conf >= 80
                and best_table
                and "classify_input_df" in st.session_state
            )
            if write_ready:
                from writer import is_write_configured, preview_insert, execute_insert

                df_to_write = st.session_state.classify_input_df
                col_mapping  = result.get("column_mapping", {})
                prev = preview_insert(df_to_write, best_table, col_mapping)

                st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)
                if prev["valid"]:
                    st.markdown(f"""
                    <div class="s-card">
                        <div class="s-title">📥 Tambah ke Database</div>
                        <div style="color:var(--text-muted);font-size:14px;line-height:1.7;">
                            Siap menginsert <b>{prev['row_count']:,} baris</b> ke tabel
                            <code>{best_table}</code>&nbsp;
                            <span style="color:var(--success);font-weight:600;">(confidence {conf}%)</span>
                        </div>
                    </div>""", unsafe_allow_html=True)

                    with st.expander(f"👁️ Preview {len(prev['preview_df'])} baris pertama"):
                        st.dataframe(prev["preview_df"], use_container_width=True, hide_index=True)

                    for w in prev.get("warnings", []):
                        st.caption(f"⚠️ {w}")

                    if "insert_result" in st.session_state:
                        ir = st.session_state.insert_result
                        if ir["success"]:
                            st.success(
                                f"✅ {ir['rows_inserted']:,} baris berhasil diinsert ke `{ir['table']}`."
                            )
                            if ir.get("audit_file"):
                                st.caption(f"Audit log: `{ir['audit_file']}`")
                        else:
                            st.error(f"Insert gagal: {'; '.join(ir['errors'])}")

                    elif not is_write_configured():
                        st.info(
                            "💡 Write belum dikonfigurasi. Tambahkan `WRITE_DATABASE_URL` di `.env` "
                            "dan jalankan `GRANT INSERT ON <tabel> TO agent_user;` di Supabase."
                        )
                    else:
                        sid = "dashboard"
                        try:
                            sid = st.session_state.agent.logger.session_id
                        except Exception:
                            pass
                        if st.button(
                            f"✅ Konfirmasi & Simpan {prev['row_count']:,} baris ke `{best_table}`",
                            type="primary",
                            use_container_width=True,
                            key="confirm_insert_btn",
                        ):
                            with st.spinner("Menyimpan ke database..."):
                                st.session_state.insert_result = execute_insert(
                                    df_to_write, best_table, col_mapping, session_id=sid
                                )
                            st.rerun()
                else:
                    st.error(f"Preview gagal: {'; '.join(prev['errors'])}")
