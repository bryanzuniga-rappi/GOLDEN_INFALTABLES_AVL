from __future__ import annotations

import html
import io
import math
import os
import re
from datetime import datetime, timezone
from typing import Iterable
from urllib.parse import quote

import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st


st.set_page_config(
    page_title="Supply Command Center",
    page_icon="📦",
    layout="wide",
    initial_sidebar_state="collapsed",
)


INK = "#0b0b0b"
PAPER = "#f2efe7"
WHITE = "#fffdf7"
RED = "#ff3b30"
ACID = "#dfff00"
BLUE = "#246bfe"
GREEN = "#00a854"
MUTED = "#55534e"

REQUIRED_COLUMNS = {
    "PRODUCT_ID",
    "PRODUCT_NAME",
    "WAREHOUSE_NAME",
    "CITY",
    "IGA",
    "STOCK TIENDA",
    "INCOMING",
    "AVL",
    "STATUS ACTUAL",
}


def get_setting(name: str, default: str = "") -> str:
    """Read a setting from Streamlit secrets or an environment variable."""
    try:
        value = st.secrets.get(name, "")
        if value not in (None, ""):
            return str(value)
    except Exception:
        pass
    return str(os.getenv(name, default))


def get_int_setting(name: str, default: int) -> int:
    try:
        return int(get_setting(name, str(default)))
    except (TypeError, ValueError):
        return default


def normalize_header(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def sheet_to_csv_url(sheet_reference: str, sheet_name: str) -> str:
    """Accept a Google Sheets URL, file ID, or an already-built CSV URL."""
    reference = sheet_reference.strip()
    if not reference:
        raise ValueError("Falta configurar SHEET_URL.")

    lower = reference.lower()
    if "output=csv" in lower or "tqx=out:csv" in lower:
        return reference

    match = re.search(r"/spreadsheets/d/([a-zA-Z0-9_-]+)", reference)
    sheet_id = match.group(1) if match else reference

    if not re.fullmatch(r"[a-zA-Z0-9_-]{20,}", sheet_id):
        raise ValueError(
            "La URL no parece corresponder a un Google Sheet. "
            "Pega la URL completa, el ID del archivo o una URL CSV pública."
        )

    return (
        f"https://docs.google.com/spreadsheets/d/{sheet_id}/gviz/tq"
        f"?tqx=out:csv&sheet={quote(sheet_name)}"
    )


@st.cache_data(ttl=300, show_spinner=False)
def load_sheet(
    sheet_reference: str,
    sheet_name: str,
    header_row: int,
    max_rows: int,
) -> tuple[pd.DataFrame, dict[str, object]]:
    csv_url = sheet_to_csv_url(sheet_reference, sheet_name)
    response = requests.get(
        csv_url,
        timeout=35,
        headers={"User-Agent": "Mozilla/5.0 SupplyCommandCenter/1.0"},
    )
    response.raise_for_status()

    body = response.text.lstrip()
    if body.lower().startswith("<!doctype html") or body.lower().startswith("<html"):
        raise PermissionError(
            "Google devolvió una página web en lugar del CSV. "
            "Verifica que el Sheet sea público para lectura y que exista la pestaña indicada."
        )

    try:
        frame = pd.read_csv(
            io.StringIO(response.text),
            header=max(header_row - 1, 0),
            dtype=str,
            keep_default_na=False,
            nrows=max_rows + 1,
        )
    except pd.errors.EmptyDataError as exc:
        raise ValueError("La pestaña no contiene datos legibles.") from exc

    frame.columns = [normalize_header(column) for column in frame.columns]
    frame = frame.loc[:, ~frame.columns.duplicated()].copy()
    frame = frame.replace(r"^\s*$", pd.NA, regex=True).dropna(how="all").fillna("")

    missing = sorted(REQUIRED_COLUMNS.difference(frame.columns))
    if missing:
        raise ValueError(
            f"Faltan encabezados en la fila {header_row}: {', '.join(missing)}"
        )

    truncated = len(frame) > max_rows
    if truncated:
        frame = frame.iloc[:max_rows].copy()

    metadata = {
        "rows": len(frame),
        "truncated": truncated,
        "max_rows": max_rows,
        "loaded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sheet_name": sheet_name,
    }
    return frame, metadata


def series_as_text(frame: pd.DataFrame, column: str, fallback: str = "") -> pd.Series:
    if column not in frame.columns:
        return pd.Series(fallback, index=frame.index, dtype="object")
    return frame[column].fillna("").astype(str).str.strip()


def series_as_number(frame: pd.DataFrame, column: str) -> pd.Series:
    values = series_as_text(frame, column)
    values = values.str.replace(",", "", regex=False).str.replace("%", "", regex=False)
    return pd.to_numeric(values, errors="coerce").fillna(0.0)


def scalar_as_number(value: object) -> float:
    try:
        parsed = float(str(value).replace(",", "").replace("%", "").strip())
        return parsed if math.isfinite(parsed) else 0.0
    except (TypeError, ValueError):
        return 0.0


def format_number(value: object) -> str:
    number = scalar_as_number(value)
    if number.is_integer():
        return f"{int(number):,}"
    return f"{number:,.2f}".rstrip("0").rstrip(".")


def status_clean(values: pd.Series) -> pd.Series:
    cleaned = values.replace("", "SIN STATUS").str.replace(
        r"^[0-9]+\.\s*", "", regex=True
    ).str.strip()
    return cleaned.replace("", "SIN STATUS")


def is_healthy(frame: pd.DataFrame) -> pd.Series:
    broken = (series_as_number(frame, "STOCK TIENDA") <= 0) & (
        series_as_number(frame, "INCOMING") <= 0
    )
    return (series_as_number(frame, "AVL") > 0) | ~broken


def is_broken(frame: pd.DataFrame) -> pd.Series:
    return (series_as_number(frame, "STOCK TIENDA") <= 0) & (
        series_as_number(frame, "INCOMING") <= 0
    )


def status_color(status: str) -> str:
    upper = status.upper()
    if any(term in upper for term in ("LINKS", "0 STOCK", "INSUFICIENTE")):
        return RED
    if any(term in upper for term in ("OPORTUNIDAD", "ORIGEN")):
        return BLUE
    return ACID


def escaped(value: object, fallback: str = "S/N") -> str:
    text = str(value).strip() if value not in (None, "") else fallback
    return html.escape(text)


def section_kicker(index: str, label: str) -> None:
    st.markdown(
        f'<div class="section-kicker"><span>{index}</span>{html.escape(label)}</div>',
        unsafe_allow_html=True,
    )


def render_header() -> None:
    st.markdown(
        """
        <div class="masthead">
          <div class="brand-mark">L+</div>
          <div class="brand-copy">
            <h1>Supply Command Center</h1>
            <span>Live Operations</span>
          </div>
          <div class="health"><i></i>Sistema online</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_kpis(frame: pd.DataFrame) -> None:
    broken = is_broken(frame)
    healthy = is_healthy(frame)
    status = series_as_text(frame, "STATUS ACTUAL")
    comments = series_as_text(frame, "COMMENT")
    critical = comments.str.contains(r"CR[IÍ]TICO", case=False, regex=True) | status.str.contains(
        "LINKS", case=False, regex=False
    )

    total = len(frame)
    nominal = int(healthy.sum())
    availability = (nominal / total * 100) if total else 0.0
    stores_with_breaks = int(
        series_as_text(frame.loc[broken], "WAREHOUSE_NAME", "SIN TIENDA")
        .replace("", "SIN TIENDA")
        .nunique()
    )

    cards = [
        ("acid", "AVL", "AVL General<br>Disponibilidad", "↗", f"{availability:.1f}%", f"Sanos: {nominal:,} / Total: {total:,}"),
        ("red", "QBR", "Quiebre físico<br>0 stock + 0 inc", "!", f"{int(broken.sum()):,}", "SKUs totalmente agotados"),
        ("white", "WH", "Tiendas con<br>quiebres", "⌂", f"{stores_with_breaks:,}", "Almacenes requiriendo atención"),
        ("blue", "LCK", "Críticos<br>Chedraui Lock", "×", f"{int(critical.sum()):,}", "Requiere intervención de catálogo"),
    ]
    card_html = "".join(
        f"""
        <article class="kpi-card {color}" data-code="{code}">
          <div class="eyebrow"><span>{label}</span><b>{symbol}</b></div>
          <div class="metric">{value}</div>
          <div class="metric-sub">{subtitle}</div>
        </article>
        """
        for color, code, label, symbol, value, subtitle in cards
    )
    st.markdown(f'<div class="bento-grid">{card_html}</div>', unsafe_allow_html=True)


def unique_options(frame: pd.DataFrame, column: str) -> list[str]:
    values = [value for value in series_as_text(frame, column).unique().tolist() if value]
    return sorted(values, key=str.casefold)


def apply_filters(
    frame: pd.DataFrame,
    search: str,
    city: str,
    store: str,
    segment: str,
) -> pd.DataFrame:
    mask = pd.Series(True, index=frame.index)
    if city != "Todas":
        mask &= series_as_text(frame, "CITY").eq(city)
    if store != "Todas":
        mask &= series_as_text(frame, "WAREHOUSE_NAME").eq(store)
    if segment != "Todos":
        mask &= series_as_text(frame, "IGA").eq(segment)
    if search.strip():
        term = re.escape(search.strip())
        mask &= series_as_text(frame, "PRODUCT_NAME").str.contains(
            term, case=False, regex=True, na=False
        ) | series_as_text(frame, "PRODUCT_ID").str.contains(
            term, case=False, regex=True, na=False
        )
    return frame.loc[mask].copy()


def top_counts(frame: pd.DataFrame, column: str) -> list[tuple[str, int]]:
    broken_frame = frame.loc[is_broken(frame)].copy()
    if broken_frame.empty:
        return []
    names = series_as_text(broken_frame, column).replace("", "SIN DATO")
    counts = names.value_counts().head(5)
    return [(str(name), int(count)) for name, count in counts.items()]


def render_top_table(title: str, badge: str, column_label: str, rows: Iterable[tuple[str, int]]) -> None:
    rows = list(rows)
    if rows:
        body = "".join(
            f"""
            <tr>
              <td title="{escaped(name)}"><span class="rank">{rank:02d}</span>{escaped(name)}</td>
              <td class="num"><span class="danger-num">{count:,}</span></td>
            </tr>
            """
            for rank, (name, count) in enumerate(rows, start=1)
        )
    else:
        body = '<tr><td colspan="2" class="healthy-network">RED 100% SANA // 00</td></tr>'

    st.markdown(
        f"""
        <div class="analytics-card">
          <div class="card-title">{html.escape(title)}<span>{html.escape(badge)}</span></div>
          <table class="mini-table">
            <thead><tr><th>{html.escape(column_label)}</th><th class="num">Quiebres</th></tr></thead>
            <tbody>{body}</tbody>
          </table>
        </div>
        """,
        unsafe_allow_html=True,
    )


def donut_figure(frame: pd.DataFrame) -> go.Figure:
    statuses = status_clean(series_as_text(frame, "STATUS ACTUAL"))
    counts = statuses.value_counts()
    labels = counts.index.tolist()
    values = counts.values.tolist()
    colors = [status_color(label) for label in labels]

    figure = go.Figure(
        go.Pie(
            labels=labels,
            values=values,
            hole=0.68,
            marker={"colors": colors, "line": {"color": INK, "width": 3}},
            sort=False,
            textinfo="none",
            hovertemplate="%{label}<br>%{value:,} registros<extra></extra>",
        )
    )
    figure.update_layout(
        height=245,
        margin={"l": 8, "r": 8, "t": 8, "b": 8},
        paper_bgcolor=WHITE,
        plot_bgcolor=WHITE,
        showlegend=False,
        annotations=[
            {
                "text": f"<b>{len(frame):,}</b><br><span style='font-size:11px'>REGISTROS</span>",
                "x": 0.5,
                "y": 0.5,
                "showarrow": False,
                "font": {"size": 22, "color": INK, "family": "Arial Black"},
            }
        ],
    )
    return figure


def store_availability_figure(frame: pd.DataFrame, selected_city: str) -> go.Figure | None:
    if frame.empty:
        return None

    working = pd.DataFrame(
        {
            "store": series_as_text(frame, "WAREHOUSE_NAME").replace("", "SIN TIENDA"),
            "city": series_as_text(frame, "CITY"),
            "healthy": is_healthy(frame).astype(int),
        },
        index=frame.index,
    )
    grouped = (
        working.groupby("store", as_index=False)
        .agg(city=("city", "first"), nominal=("healthy", "sum"), target=("healthy", "size"))
    )
    grouped["avl"] = grouped["nominal"] / grouped["target"] * 100
    grouped["label"] = grouped.apply(
        lambda row: (
            f"{row['store']} [{row['city']}]"
            if selected_city == "Todas" and row["city"]
            else row["store"]
        ),
        axis=1,
    )
    grouped = grouped.sort_values(["avl", "label"], ascending=[True, True])
    colors = [GREEN if value >= 90 else BLUE if value >= 75 else RED for value in grouped["avl"]]

    figure = go.Figure(
        go.Bar(
            x=grouped["avl"],
            y=grouped["label"],
            orientation="h",
            marker={"color": colors, "line": {"color": INK, "width": 1.5}},
            customdata=grouped[["nominal", "target"]],
            hovertemplate=(
                "%{y}<br>AVL: %{x:.1f}%<br>Sanos: %{customdata[0]:,} / "
                "%{customdata[1]:,}<extra></extra>"
            ),
        )
    )
    figure.update_layout(
        height=max(360, min(1050, 30 * len(grouped) + 90)),
        margin={"l": 12, "r": 18, "t": 15, "b": 35},
        paper_bgcolor=WHITE,
        plot_bgcolor=WHITE,
        showlegend=False,
        bargap=0.28,
        font={"family": "Arial", "color": INK},
        xaxis={
            "title": "AVL %",
            "range": [0, 100],
            "ticksuffix": "%",
            "gridcolor": "rgba(11,11,11,.12)",
            "zeroline": False,
        },
        yaxis={"title": "", "automargin": True, "gridcolor": "rgba(0,0,0,0)"},
    )
    return figure


def cedis_pill(label: str, value: object) -> str:
    number = scalar_as_number(value)
    empty_class = " empty" if number <= 0 else ""
    return (
        f'<div class="cedis-pill{empty_class}">{label}<br>'
        f'<span>{format_number(number)}</span></div>'
    )


def render_detail_table(frame: pd.DataFrame) -> None:
    if frame.empty:
        body = '<tr><td colspan="5" class="empty-state">Sin datos para mostrar // Ajusta los filtros</td></tr>'
    else:
        rows: list[str] = []
        for _, row in frame.head(100).iterrows():
            status = re.sub(r"^[0-9]+\.\s*", "", str(row.get("STATUS ACTUAL", "SIN STATUS"))).strip() or "SIN STATUS"
            color = status_color(status)
            tag_class = "tag-red" if color == RED else "tag-blue" if color == BLUE else "tag-gray"
            physical = format_number(row.get("STOCK TIENDA", 0))
            incoming = format_number(row.get("INCOMING", 0))
            rows.append(
                f"""
                <tr>
                  <td>
                    <div class="product-name">{escaped(row.get('PRODUCT_NAME'))}</div>
                    <div class="micro-copy">{escaped(row.get('PRODUCT_ID'))} // {escaped(row.get('IGA'))}</div>
                  </td>
                  <td>
                    <div class="product-name">{escaped(row.get('WAREHOUSE_NAME'))}</div>
                    <div class="micro-copy">{escaped(row.get('CITY'))}</div>
                  </td>
                  <td>
                    <div class="stock-main">{physical} Físico</div>
                    <div class="micro-copy">+{incoming} Incoming</div>
                  </td>
                  <td><span class="status-tag {tag_class}">{escaped(status)}</span></td>
                  <td><div class="cedis-grid">
                    {cedis_pill('444', row.get('444', 0))}
                    {cedis_pill('831', row.get('831', 0))}
                    {cedis_pill('811', row.get('811', 0))}
                    {cedis_pill('834', row.get('834', 0))}
                  </div></td>
                </tr>
                """
            )
        body = "".join(rows)

    visible_note = (
        f"Mostrando 100 de {len(frame):,} registros"
        if len(frame) > 100
        else "Mostrando todos los registros"
    )
    st.markdown(
        f"""
        <section class="detail-card">
          <div class="detail-head">
            <h2>Visibilidad CEDIS</h2>
            <div class="result-counter">{len(frame):,} resultados</div>
          </div>
          <div class="table-scroll">
            <table class="data-table">
              <thead><tr>
                <th>SKU &amp; Producto</th>
                <th>Tienda</th>
                <th>Inventario / Físico + Inc</th>
                <th>Status actual</th>
                <th>Stock orígenes / 444 · 831 · 811 · 834</th>
              </tr></thead>
              <tbody>{body}</tbody>
            </table>
          </div>
          <div class="footer-note">
            <span>Supply Pro // Control operativo</span>
            <span>{visible_note}</span>
          </div>
        </section>
        """,
        unsafe_allow_html=True,
    )


def inject_css() -> None:
    st.markdown(
        f"""
        <style>
          :root {{ --ink:{INK}; --paper:{PAPER}; --white:{WHITE}; --red:{RED}; --acid:{ACID}; --blue:{BLUE}; --green:{GREEN}; --muted:{MUTED}; }}
          .stApp {{
            background: linear-gradient(rgba(11,11,11,.045) 1px, transparent 1px),
                        linear-gradient(90deg, rgba(11,11,11,.045) 1px, transparent 1px), var(--paper);
            background-size: 28px 28px; color: var(--ink);
          }}
          .block-container {{ max-width:1680px; padding:1rem 2.1rem 3rem; }}
          header[data-testid="stHeader"] {{ background:transparent; }}
          #MainMenu, footer {{ visibility:hidden; }}
          h1, h2, h3 {{ color:var(--ink); }}
          .masthead {{ display:grid; grid-template-columns:78px 1fr auto; min-height:78px; margin:0 0 30px; background:var(--ink); color:var(--white); border:3px solid var(--ink); box-shadow:7px 7px 0 var(--ink); }}
          .brand-mark {{ display:grid; place-items:center; background:var(--red); color:var(--ink); border-right:4px solid var(--white); font:900 31px/1 Impact, "Arial Black", sans-serif; }}
          .brand-copy {{ display:flex; align-items:center; gap:16px; padding:13px 22px; min-width:0; }}
          .brand-copy h1 {{ margin:0; color:var(--white); font:900 clamp(22px,2.4vw,38px)/.95 Impact,"Arial Black",sans-serif; letter-spacing:.02em; text-transform:uppercase; white-space:nowrap; }}
          .brand-copy span {{ padding:5px 8px; background:var(--acid); color:var(--ink); border:2px solid var(--white); font-size:11px; font-weight:800; text-transform:uppercase; white-space:nowrap; }}
          .health {{ display:flex; align-items:center; gap:10px; padding:0 25px; border-left:2px solid rgba(255,255,255,.35); font-size:12px; font-weight:800; text-transform:uppercase; white-space:nowrap; }}
          .health i {{ width:11px; height:11px; background:var(--acid); border:2px solid var(--white); border-radius:50%; box-shadow:0 0 0 2px var(--ink); }}
          .section-kicker {{ display:flex; align-items:center; gap:12px; margin:20px 0 14px; font-size:12px; font-weight:900; letter-spacing:.05em; text-transform:uppercase; }}
          .section-kicker span {{ display:grid; place-items:center; width:27px; height:27px; background:var(--ink); color:var(--white); }}
          .bento-grid {{ display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:18px; margin-bottom:30px; }}
          .kpi-card {{ position:relative; min-height:205px; padding:19px; display:flex; flex-direction:column; justify-content:space-between; overflow:hidden; border:3px solid var(--ink); box-shadow:7px 7px 0 var(--ink); }}
          .kpi-card::after {{ content:attr(data-code); position:absolute; right:-7px; bottom:-24px; color:rgba(11,11,11,.07); font:900 92px/1 Impact,"Arial Black",sans-serif; }}
          .kpi-card.acid {{ background:var(--acid); }} .kpi-card.red {{ background:var(--red); }} .kpi-card.white {{ background:var(--white); }} .kpi-card.blue {{ background:var(--blue); color:var(--white); }}
          .eyebrow {{ position:relative; z-index:1; display:flex; justify-content:space-between; gap:10px; padding-bottom:13px; border-bottom:2px solid currentColor; font-size:12px; font-weight:900; text-transform:uppercase; }}
          .eyebrow b {{ font:900 18px Impact,"Arial Black",sans-serif; }}
          .metric {{ position:relative; z-index:1; margin-top:18px; font:900 clamp(54px,5vw,86px)/.86 Impact,"Arial Black",sans-serif; letter-spacing:-.02em; }}
          .metric-sub {{ position:relative; z-index:1; margin-top:14px; font-size:12px; font-weight:800; text-transform:uppercase; }}
          .st-key-filter_panel {{ padding:16px; background:var(--ink); border:3px solid var(--ink)!important; border-radius:0!important; box-shadow:7px 7px 0 var(--ink); }}
          .st-key-filter_panel label {{ color:var(--acid)!important; font-size:11px!important; font-weight:900!important; letter-spacing:.05em; text-transform:uppercase; }}
          .st-key-filter_panel div[data-baseweb="input"] > div, .st-key-filter_panel div[data-baseweb="select"] > div {{ border-radius:0!important; border:2px solid var(--white)!important; background:var(--white)!important; }}
          .analytics-card {{ min-height:300px; overflow:hidden; background:var(--white); border:3px solid var(--ink); box-shadow:7px 7px 0 var(--ink); }}
          .card-title {{ min-height:48px; display:flex; align-items:center; justify-content:space-between; gap:10px; padding:13px 16px; background:var(--ink); color:var(--white); font-size:13px; font-weight:900; letter-spacing:.04em; text-transform:uppercase; }}
          .card-title span {{ padding:4px 6px; background:var(--acid); color:var(--ink); font-size:10px; }}
          .mini-table {{ width:100%; border-collapse:collapse; table-layout:fixed; font-size:13px; }}
          .mini-table th {{ padding:11px 15px; color:var(--muted); border-bottom:2px solid var(--ink); font-size:11px; font-weight:900; letter-spacing:.05em; text-align:left; text-transform:uppercase; }}
          .mini-table td {{ padding:10px 15px; border-bottom:1px solid var(--ink); font-weight:700; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }}
          .mini-table .num {{ width:92px; text-align:right; }}
          .rank {{ display:inline-grid; place-items:center; width:23px; height:23px; margin-right:7px; background:var(--ink); color:var(--white); font-size:11px; font-weight:900; }}
          .danger-num {{ display:inline-block; min-width:37px; padding:4px 6px; background:var(--red); border:2px solid var(--ink); text-align:center; }}
          .healthy-network {{ color:var(--green); padding:25px 15px!important; }}
          .st-key-status_chart {{ min-height:300px; background:var(--white); border:3px solid var(--ink); box-shadow:7px 7px 0 var(--ink); }}
          .st-key-network_chart {{ padding-bottom:10px; background:var(--white); border:3px solid var(--ink); box-shadow:7px 7px 0 var(--ink); margin-bottom:30px; }}
          .network-head, .detail-head {{ display:flex; align-items:center; justify-content:space-between; gap:18px; padding:18px 20px; background:var(--acid); border-bottom:3px solid var(--ink); }}
          .network-head h2, .detail-head h2 {{ margin:0; font:900 clamp(24px,3vw,43px)/.95 Impact,"Arial Black",sans-serif; text-transform:uppercase; }}
          .result-counter {{ padding:7px 10px; background:var(--ink); color:var(--white); border:2px solid var(--ink); font-size:11px; font-weight:900; text-transform:uppercase; white-space:nowrap; }}
          .detail-card {{ overflow:hidden; margin-bottom:30px; background:var(--white); border:3px solid var(--ink); box-shadow:7px 7px 0 var(--ink); }}
          .table-scroll {{ max-height:590px; overflow:auto; }}
          .data-table {{ width:100%; min-width:1000px; border-collapse:separate; border-spacing:0; font-size:14px; }}
          .data-table th {{ position:sticky; top:0; z-index:3; padding:12px 11px; background:var(--ink); color:var(--white); border-right:1px solid #4f4f4f; font-size:11px; font-weight:900; letter-spacing:.05em; text-align:left; text-transform:uppercase; }}
          .data-table td {{ padding:13px 11px; border-right:1px solid var(--ink); border-bottom:1px solid var(--ink); vertical-align:middle; }}
          .data-table tbody tr:nth-child(even) {{ background:#e6e2d8; }} .data-table tbody tr:hover {{ background:#fff4ac; }}
          .product-name {{ margin-bottom:4px; font-size:14px; font-weight:800; line-height:1.2; }}
          .micro-copy {{ color:var(--muted); font-size:12px; line-height:1.3; }} .stock-main {{ font-size:15px; font-weight:900; }}
          .status-tag {{ display:inline-block; max-width:250px; padding:6px 8px; border:2px solid var(--ink); box-shadow:3px 3px 0 var(--ink); font-size:10px; font-weight:900; letter-spacing:.05em; text-transform:uppercase; }}
          .tag-red {{ background:var(--red); }} .tag-blue {{ background:var(--blue); color:var(--white); }} .tag-gray {{ background:#cfcac0; }}
          .cedis-grid {{ display:grid; grid-template-columns:repeat(4,minmax(48px,1fr)); gap:5px; }}
          .cedis-pill {{ padding:5px 6px; background:var(--ink); color:var(--white); border:2px solid var(--ink); font-size:11px; font-weight:900; text-align:center; }}
          .cedis-pill span {{ color:var(--acid); }} .cedis-pill.empty {{ background:var(--white); color:var(--ink); }} .cedis-pill.empty span {{ color:var(--red); }}
          .empty-state {{ padding:44px!important; color:var(--muted); font-size:13px; font-weight:900; text-align:center; text-transform:uppercase; }}
          .footer-note {{ display:flex; justify-content:space-between; gap:16px; padding:12px 15px; background:var(--ink); color:var(--white); font-size:11px; font-weight:800; letter-spacing:.05em; text-transform:uppercase; }}
          ::-webkit-scrollbar {{ width:12px; height:12px; }} ::-webkit-scrollbar-track {{ background:var(--white); }} ::-webkit-scrollbar-thumb {{ background:var(--red); border:2px solid var(--ink); }}
          @media(max-width:1000px) {{ .bento-grid {{ grid-template-columns:repeat(2,minmax(0,1fr)); }} .masthead {{ grid-template-columns:65px 1fr; }} .health {{ display:none; }} }}
          @media(max-width:650px) {{ .block-container {{ padding:1rem .8rem 2rem; }} .bento-grid {{ grid-template-columns:1fr; }} .brand-copy span {{ display:none; }} .brand-copy h1 {{ white-space:normal; }} .footer-note {{ flex-direction:column; }} }}
        </style>
        """,
        unsafe_allow_html=True,
    )


def main() -> None:
    inject_css()
    render_header()

    configured_url = get_setting("SHEET_URL")
    sheet_name = get_setting("SHEET_NAME", "BASE") or "BASE"
    header_row = max(get_int_setting("HEADER_ROW", 2), 1)
    max_rows = max(get_int_setting("MAX_DATA_ROWS", 15000), 1)

    if not configured_url:
        st.warning("Falta conectar el origen de datos.")
        configured_url = st.text_input(
            "URL o ID del Google Sheet público",
            placeholder="https://docs.google.com/spreadsheets/d/...",
            help="Para el despliegue permanente, configura SHEET_URL en los Secrets de Streamlit.",
        )
        if not configured_url:
            st.stop()

    with st.sidebar:
        st.markdown("### Origen de datos")
        st.caption(f"Pestaña: {sheet_name} · Encabezados: fila {header_row}")
        if st.button("Actualizar datos", use_container_width=True):
            load_sheet.clear()
            st.rerun()

    try:
        with st.spinner("Inicializando motor analítico..."):
            raw_data, metadata = load_sheet(
                configured_url, sheet_name, header_row, max_rows
            )
    except Exception as exc:
        st.error(f"No fue posible cargar el Google Sheet: {exc}")
        st.info(
            "Confirma que el archivo esté compartido como “Cualquier persona con el enlace: Lector” "
            f"y que la pestaña se llame “{sheet_name}”."
        )
        st.stop()

    if metadata["truncated"]:
        st.warning(
            f"La fuente supera el límite de {metadata['max_rows']:,} filas; "
            "el dashboard cargó únicamente ese máximo."
        )

    section_kicker("01", "Visión macro / disponibilidad")
    kpi_slot = st.empty()

    section_kicker("02", "Funnel de filtrado")
    with st.container(border=True, key="filter_panel"):
        search_col, city_col, store_col, segment_col = st.columns([2, 1, 1, 1])
        with search_col:
            search = st.text_input(
                "Búsqueda quirúrgica / SKU o nombre",
                placeholder="Escribe un producto o SKU...",
            )
        with city_col:
            city = st.selectbox("Ciudad", ["Todas", *unique_options(raw_data, "CITY")])
        with store_col:
            store = st.selectbox(
                "Tienda / Warehouse",
                ["Todas", *unique_options(raw_data, "WAREHOUSE_NAME")],
            )
        with segment_col:
            segment = st.selectbox(
                "Segmento / IGA", ["Todos", *unique_options(raw_data, "IGA")]
            )

    filtered = apply_filters(raw_data, search, city, store, segment)
    with kpi_slot.container():
        render_kpis(filtered)

    section_kicker("03", "Inteligencia de negocio / ofensores")
    top_store_col, top_sku_col, donut_col = st.columns([1, 1, 0.82], gap="large")
    with top_store_col:
        render_top_table(
            "Top 5 / Tiendas con quiebres",
            "Warehouse",
            "Tienda",
            top_counts(filtered, "WAREHOUSE_NAME"),
        )
    with top_sku_col:
        render_top_table(
            "Top 5 / SKUs quebrados",
            "Global",
            "Producto",
            top_counts(filtered, "PRODUCT_NAME"),
        )
    with donut_col, st.container(key="status_chart"):
        st.markdown(
            '<div class="card-title">Composición del estatus<span>Mix</span></div>',
            unsafe_allow_html=True,
        )
        st.plotly_chart(
            donut_figure(filtered),
            use_container_width=True,
            config={"displayModeBar": False, "responsive": True},
        )

    section_kicker("04", "Desempeño / AVL por Tienda")
    store_figure = store_availability_figure(filtered, city)
    with st.container(key="network_chart"):
        st.markdown(
            """
            <div class="network-head"><h2>Rendimiento de Red</h2><div class="result-counter">AVL % por Tienda</div></div>
            """,
            unsafe_allow_html=True,
        )
        if store_figure is None:
            st.info("Sin tiendas para mostrar con los filtros actuales.")
        else:
            st.plotly_chart(
                store_figure,
                use_container_width=True,
                config={"displayModeBar": False, "responsive": True},
            )

    section_kicker("05", "Ejecución / detalle operativo")
    render_detail_table(filtered)

    loaded_at = str(metadata["loaded_at"]).replace("T", " ").replace("+00:00", " UTC")
    st.caption(
        f"Fuente: {metadata['sheet_name']} · {metadata['rows']:,} filas cargadas · "
        f"Última lectura: {loaded_at} · caché de 5 minutos"
    )


if __name__ == "__main__":
    main()
