from __future__ import annotations

import csv
import html
import io
import math
import re
from datetime import datetime, timezone
from itertools import islice

import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st


st.set_page_config(
    page_title="Golden & Infaltables | Command Center",
    page_icon="📦",
    layout="wide",
    initial_sidebar_state="collapsed",
)


# ---------------------------------------------------------------------------
# Fuente de datos: Google Sheet público
# ---------------------------------------------------------------------------
SPREADSHEET_ID = "1OwvE6mwc2G8yrFoBpP0oO_DePw88eiBrlsQoXCP-SS8"
SHEET_NAME = "BASE"
SHEET_GID = "2089074760"
HEADER_ROW = 2
CACHE_TTL_SECONDS = 300

GVIZ_URL = f"https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/gviz/tq"
EXPORT_URL = (
    f"https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/export"
    f"?format=csv&gid={SHEET_GID}"
)

# Solamente descarga las columnas que utiliza el dashboard.
GVIZ_PARAMS = {
    "tqx": "out:csv",
    "sheet": SHEET_NAME,
    "range": "A2:AJ",
    "headers": "1",
    "tq": "select B,D,E,G,H,L,R,S,T,U,V,W,X,Z,AA,AI",
}

CORE_COLUMNS = {
    "CITY",
    "WAREHOUSE_NAME",
    "PRODUCT_ID",
    "PRODUCT_NAME",
    "AVL",
    "STOCK TIENDA",
    "INCOMING",
    "STATUS ACTUAL",
    "IGA",
}

DISPLAY_COLUMNS = [
    "CITY",
    "WAREHOUSE_NAME",
    "PRODUCT_ID",
    "PRODUCT_NAME",
    "STORAGE_TYPE",
    "AVL",
    "STOCK TIENDA",
    "INCOMING",
    "444",
    "831",
    "811",
    "834",
    "STATUS ACTUAL",
    "COMMENT",
    "COMMENT 2",
    "IGA",
]


# ---------------------------------------------------------------------------
# Paleta: brutalismo moderno, sobrio y legible
# ---------------------------------------------------------------------------
INK = "#171717"
PAPER = "#F3F1EB"
WHITE = "#FFFFFF"
SOFT = "#E7E4DC"
MUTED = "#66645F"
ACID = "#DDF64C"
RED = "#FF5A4F"
BLUE = "#4B68E8"
GREEN = "#179A63"


def normalize_header(value: object) -> str:
    text = str(value or "").lstrip("\ufeff")
    return re.sub(r"\s+", " ", text).strip()


def detect_header_row(csv_text: str) -> int:
    """Detecta la fila de cabeceras aunque Google omita o conserve la fila 1."""
    reader = csv.reader(io.StringIO(csv_text))
    for index, row in enumerate(islice(reader, 10)):
        normalized = {normalize_header(cell) for cell in row}
        if CORE_COLUMNS.issubset(normalized):
            return index
    raise ValueError(
        "No se localizaron las cabeceras esperadas de BASE en las primeras 10 filas."
    )


def parse_sheet_csv(csv_text: str) -> pd.DataFrame:
    if csv_text.lstrip().lower().startswith(("<!doctype html", "<html")):
        raise PermissionError(
            "Google devolvió una página de acceso en lugar del archivo CSV."
        )

    header_index = detect_header_row(csv_text)
    frame = pd.read_csv(
        io.StringIO(csv_text),
        header=header_index,
        dtype=str,
        keep_default_na=False,
        low_memory=False,
    )
    frame.columns = [normalize_header(column) for column in frame.columns]
    frame = frame.loc[:, ~frame.columns.duplicated()].copy()

    missing = sorted(CORE_COLUMNS.difference(frame.columns))
    if missing:
        raise ValueError(f"Faltan columnas obligatorias: {', '.join(missing)}")

    for column in DISPLAY_COLUMNS:
        if column not in frame.columns:
            frame[column] = ""

    frame = frame[DISPLAY_COLUMNS].copy()
    frame = frame.replace(r"^\s*$", pd.NA, regex=True)
    frame = frame.dropna(
        how="all",
        subset=["PRODUCT_ID", "PRODUCT_NAME", "WAREHOUSE_NAME"],
    ).fillna("")
    return frame.reset_index(drop=True)


@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner=False)
def load_data() -> tuple[pd.DataFrame, dict[str, object]]:
    """Carga la hoja pública; usa un endpoint alterno si Google rechaza GViz."""
    attempts = [
        (GVIZ_URL, GVIZ_PARAMS),
        (EXPORT_URL, None),
    ]
    errors: list[str] = []

    for url, params in attempts:
        try:
            response = requests.get(
                url,
                params=params,
                timeout=120,
                headers={"User-Agent": "Mozilla/5.0 SupplyCommandCenter/2.0"},
            )
            response.raise_for_status()
            text = response.content.decode("utf-8-sig")
            frame = parse_sheet_csv(text)
            return frame, {
                "rows": len(frame),
                "loaded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "sheet": SHEET_NAME,
            }
        except Exception as exc:
            errors.append(str(exc))

    detail = errors[-1] if errors else "Error desconocido"
    raise RuntimeError(
        "No fue posible leer el Google Sheet público. "
        f"Último detalle recibido: {detail}"
    )


def text_series(frame: pd.DataFrame, column: str, fallback: str = "") -> pd.Series:
    if column not in frame.columns:
        return pd.Series(fallback, index=frame.index, dtype="object")
    return frame[column].fillna("").astype(str).str.strip()


def number_series(frame: pd.DataFrame, column: str) -> pd.Series:
    values = (
        text_series(frame, column)
        .str.replace(",", "", regex=False)
        .str.replace("%", "", regex=False)
    )
    return pd.to_numeric(values, errors="coerce").fillna(0.0)


def scalar_number(value: object) -> float:
    try:
        number = float(str(value).replace(",", "").replace("%", "").strip())
        return number if math.isfinite(number) else 0.0
    except (TypeError, ValueError):
        return 0.0


def format_number(value: object) -> str:
    number = scalar_number(value)
    if number.is_integer():
        return f"{int(number):,}"
    return f"{number:,.2f}".rstrip("0").rstrip(".")


def clean_status(values: pd.Series) -> pd.Series:
    cleaned = (
        values.replace("", "SIN STATUS")
        .str.replace(r"^[0-9]+\.\s*", "", regex=True)
        .str.strip()
    )
    return cleaned.replace("", "SIN STATUS")


def broken_mask(frame: pd.DataFrame) -> pd.Series:
    return (number_series(frame, "STOCK TIENDA") <= 0) & (
        number_series(frame, "INCOMING") <= 0
    )


def healthy_mask(frame: pd.DataFrame) -> pd.Series:
    return (number_series(frame, "AVL") > 0) | ~broken_mask(frame)


def critical_mask(frame: pd.DataFrame) -> pd.Series:
    comments = text_series(frame, "COMMENT")
    statuses = text_series(frame, "STATUS ACTUAL")
    return comments.str.contains(
        r"CR[IÍ]TICO", case=False, regex=True
    ) | statuses.str.contains("LINKS", case=False, regex=False)


def status_tone(status: str) -> str:
    upper = status.upper()
    if any(term in upper for term in ("LINKS", "0 STOCK", "INSUFICIENTE")):
        return "red"
    if any(term in upper for term in ("OPORTUNIDAD", "ORIGEN")):
        return "blue"
    if any(term in upper for term in ("CON STOCK", "SANO", "CUBIERTO")):
        return "green"
    return "gray"


def status_color(status: str) -> str:
    return {
        "red": RED,
        "blue": BLUE,
        "green": GREEN,
        "gray": "#A8A59E",
    }[status_tone(status)]


def escape(value: object, fallback: str = "S/N") -> str:
    value_text = str(value).strip() if value not in (None, "") else fallback
    return html.escape(value_text)


def unique_options(frame: pd.DataFrame, column: str) -> list[str]:
    values = [value for value in text_series(frame, column).unique() if value]
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
        mask &= text_series(frame, "CITY").eq(city)
    if store != "Todas":
        mask &= text_series(frame, "WAREHOUSE_NAME").eq(store)
    if segment != "Todos":
        mask &= text_series(frame, "IGA").eq(segment)
    if search.strip():
        term = search.strip()
        mask &= text_series(frame, "PRODUCT_NAME").str.contains(
            term, case=False, regex=False, na=False
        ) | text_series(frame, "PRODUCT_ID").str.contains(
            term, case=False, regex=False, na=False
        )
    return frame.loc[mask].copy()


def section_title(kicker: str, title: str, description: str = "") -> None:
    description_html = (
        f'<p class="section-description">{html.escape(description)}</p>'
        if description
        else ""
    )
    st.html(
        f"""
        <div class="section-heading">
          <span>{html.escape(kicker)}</span>
          <div><h2>{html.escape(title)}</h2>{description_html}</div>
        </div>
        """
    )


def render_header() -> None:
    st.html(
        """
        <header class="hero">
          <div>
            <div class="hero-kicker">Supply operations · Inventory control</div>
            <h1>Golden &amp; Infaltables</h1>
            <p>Disponibilidad, quiebres y visibilidad de inventario por tienda.</p>
          </div>
          <div class="live-badge"><i></i> LIVE · BASE</div>
        </header>
        """
    )


def render_kpis(frame: pd.DataFrame) -> None:
    broken = broken_mask(frame)
    healthy = healthy_mask(frame)
    total = len(frame)
    nominal = int(healthy.sum())
    availability = nominal / total * 100 if total else 0.0
    stores_with_breaks = (
        text_series(frame.loc[broken], "WAREHOUSE_NAME")
        .replace("", "SIN TIENDA")
        .nunique()
    )
    critical = int(critical_mask(frame).sum())

    cards = [
        (
            "green",
            "AVL general",
            f"{availability:.1f}%",
            f"{nominal:,} sanos de {total:,} registros",
        ),
        (
            "red",
            "Quiebre físico",
            f"{int(broken.sum()):,}",
            "Stock tienda 0 + incoming 0",
        ),
        (
            "blue",
            "Tiendas con quiebre",
            f"{int(stores_with_breaks):,}",
            "Almacenes que requieren atención",
        ),
        (
            "dark",
            "Críticos / Links",
            f"{critical:,}",
            "Intervención de catálogo o links",
        ),
    ]

    card_html = "".join(
        f"""
        <article class="kpi-card {tone}">
          <div class="kpi-label">{label}</div>
          <div class="kpi-value">{value}</div>
          <div class="kpi-detail">{detail}</div>
        </article>
        """
        for tone, label, value, detail in cards
    )
    st.html(f'<div class="kpi-grid">{card_html}</div>')


def top_counts(frame: pd.DataFrame, column: str) -> list[tuple[str, int]]:
    broken = frame.loc[broken_mask(frame)]
    if broken.empty:
        return []
    counts = (
        text_series(broken, column)
        .replace("", "SIN DATO")
        .value_counts()
        .head(5)
    )
    return [(str(label), int(value)) for label, value in counts.items()]


def render_ranking_card(
    title: str,
    badge: str,
    rows: list[tuple[str, int]],
) -> None:
    if rows:
        body = "".join(
            f"""
            <div class="rank-row">
              <span class="rank-number">{index:02d}</span>
              <span class="rank-name" title="{escape(name)}">{escape(name)}</span>
              <strong>{count:,}</strong>
            </div>
            """
            for index, (name, count) in enumerate(rows, start=1)
        )
    else:
        body = '<div class="healthy-state">Sin quiebres en esta selección</div>'

    st.html(
        f"""
        <article class="ranking-card">
          <div class="card-head">
            <h3>{html.escape(title)}</h3>
            <span>{html.escape(badge)}</span>
          </div>
          <div class="ranking-body">{body}</div>
        </article>
        """
    )


def status_figure(frame: pd.DataFrame) -> go.Figure:
    counts = clean_status(text_series(frame, "STATUS ACTUAL")).value_counts()
    labels = counts.index.tolist()
    values = counts.values.tolist()

    figure = go.Figure(
        go.Pie(
            labels=labels,
            values=values,
            hole=0.62,
            sort=False,
            textinfo="none",
            marker={
                "colors": [status_color(label) for label in labels],
                "line": {"color": WHITE, "width": 2},
            },
            hovertemplate="<b>%{label}</b><br>%{value:,} registros · %{percent}<extra></extra>",
        )
    )
    figure.update_layout(
        height=300,
        margin={"l": 8, "r": 8, "t": 14, "b": 8},
        paper_bgcolor=WHITE,
        plot_bgcolor=WHITE,
        showlegend=False,
        annotations=[
            {
                "text": f"<b>{len(frame):,}</b><br><span style='font-size:11px'>REGISTROS</span>",
                "x": 0.5,
                "y": 0.5,
                "showarrow": False,
                "font": {"size": 21, "color": INK, "family": "Arial"},
            }
        ],
    )
    return figure


def store_figure(
    frame: pd.DataFrame,
    selected_city: str,
    limit: int = 30,
) -> tuple[go.Figure | None, str]:
    if frame.empty:
        return None, "Sin tiendas para mostrar"

    working = pd.DataFrame(
        {
            "store": text_series(frame, "WAREHOUSE_NAME").replace("", "SIN TIENDA"),
            "city": text_series(frame, "CITY"),
            "healthy": healthy_mask(frame).astype(int),
        },
        index=frame.index,
    )
    grouped = (
        working.groupby("store", as_index=False)
        .agg(
            city=("city", "first"),
            nominal=("healthy", "sum"),
            target=("healthy", "size"),
        )
    )
    grouped["avl"] = grouped["nominal"] / grouped["target"] * 100
    grouped["label"] = grouped.apply(
        lambda row: (
            f"{row['store']} · {row['city']}"
            if selected_city == "Todas" and row["city"]
            else row["store"]
        ),
        axis=1,
    )

    total_stores = len(grouped)
    visible = grouped.nsmallest(limit, "avl").sort_values(
        ["avl", "label"], ascending=[False, True]
    )
    colors = [
        GREEN if value >= 90 else BLUE if value >= 75 else RED
        for value in visible["avl"]
    ]

    figure = go.Figure(
        go.Bar(
            x=visible["avl"],
            y=visible["label"],
            orientation="h",
            marker={"color": colors, "line": {"color": INK, "width": 1}},
            customdata=visible[["nominal", "target"]],
            hovertemplate=(
                "<b>%{y}</b><br>AVL: %{x:.1f}%<br>"
                "Sanos: %{customdata[0]:,} / %{customdata[1]:,}<extra></extra>"
            ),
        )
    )
    figure.update_layout(
        height=max(390, min(850, len(visible) * 26 + 90)),
        margin={"l": 10, "r": 20, "t": 18, "b": 40},
        paper_bgcolor=WHITE,
        plot_bgcolor=WHITE,
        showlegend=False,
        bargap=0.28,
        font={"family": "Arial", "color": INK, "size": 12},
        xaxis={
            "title": "AVL %",
            "range": [0, 100],
            "ticksuffix": "%",
            "gridcolor": "#E8E5DE",
            "zeroline": False,
        },
        yaxis={"title": "", "automargin": True, "gridcolor": "rgba(0,0,0,0)"},
    )
    note = (
        f"Mostrando las {len(visible)} tiendas con menor AVL de {total_stores:,}"
        if total_stores > limit
        else f"{total_stores:,} tiendas en la selección"
    )
    return figure, note


def cedis_cell(label: str, value: object) -> str:
    number = scalar_number(value)
    tone = "positive" if number > 0 else "empty"
    return (
        f'<div class="cedis-cell {tone}"><small>{label}</small>'
        f"<strong>{format_number(number)}</strong></div>"
    )


def render_detail_table(frame: pd.DataFrame) -> None:
    if frame.empty:
        rows_html = (
            '<tr><td colspan="5" class="empty-table">'
            "Sin registros para los filtros seleccionados</td></tr>"
        )
    else:
        rows: list[str] = []
        for _, row in frame.head(100).iterrows():
            status = re.sub(
                r"^[0-9]+\.\s*",
                "",
                str(row.get("STATUS ACTUAL", "SIN STATUS")),
            ).strip() or "SIN STATUS"
            comment = str(row.get("COMMENT", "")).strip()
            comment_html = (
                f'<div class="table-muted">{escape(comment)}</div>'
                if comment
                else ""
            )
            rows.append(
                f"""
                <tr>
                  <td>
                    <div class="table-primary">{escape(row.get("PRODUCT_NAME"))}</div>
                    <div class="table-muted">{escape(row.get("PRODUCT_ID"))} · {escape(row.get("IGA"))}</div>
                  </td>
                  <td>
                    <div class="table-primary">{escape(row.get("WAREHOUSE_NAME"))}</div>
                    <div class="table-muted">{escape(row.get("CITY"))}</div>
                  </td>
                  <td>
                    <div class="inventory-line"><strong>{format_number(row.get("STOCK TIENDA", 0))}</strong> físico</div>
                    <div class="table-muted">+{format_number(row.get("INCOMING", 0))} incoming</div>
                  </td>
                  <td>
                    <span class="status-tag {status_tone(status)}">{escape(status)}</span>
                    {comment_html}
                  </td>
                  <td>
                    <div class="cedis-grid">
                      {cedis_cell("444", row.get("444", 0))}
                      {cedis_cell("831", row.get("831", 0))}
                      {cedis_cell("811", row.get("811", 0))}
                      {cedis_cell("834", row.get("834", 0))}
                    </div>
                  </td>
                </tr>
                """
            )
        rows_html = "".join(rows)

    visible_text = (
        f"Mostrando 100 de {len(frame):,}"
        if len(frame) > 100
        else f"Mostrando {len(frame):,}"
    )
    st.html(
        f"""
        <section class="detail-card">
          <div class="detail-head">
            <div>
              <span>DETALLE OPERATIVO</span>
              <h3>Visibilidad CEDIS</h3>
            </div>
            <strong>{len(frame):,} resultados</strong>
          </div>
          <div class="table-scroll">
            <table class="data-table">
              <thead>
                <tr>
                  <th>SKU / Producto</th>
                  <th>Tienda</th>
                  <th>Inventario</th>
                  <th>Estado actual</th>
                  <th>Stock origen</th>
                </tr>
              </thead>
              <tbody>{rows_html}</tbody>
            </table>
          </div>
          <div class="table-footer">
            <span>Supply control · Golden &amp; Infaltables</span>
            <span>{visible_text} registros</span>
          </div>
        </section>
        """
    )


def inject_css() -> None:
    st.html(
        f"""
        <style>
          :root {{
            --ink:{INK}; --paper:{PAPER}; --white:{WHITE}; --soft:{SOFT};
            --muted:{MUTED}; --acid:{ACID}; --red:{RED}; --blue:{BLUE}; --green:{GREEN};
          }}

          .stApp {{
            background-color:var(--paper);
            background-image:
              linear-gradient(rgba(23,23,23,.045) 1px, transparent 1px),
              linear-gradient(90deg, rgba(23,23,23,.045) 1px, transparent 1px);
            background-size:28px 28px;
            color:var(--ink);
          }}
          .block-container {{
            max-width:1580px;
            padding:1.25rem 2rem 3.5rem;
          }}
          header[data-testid="stHeader"] {{ background:transparent; }}
          #MainMenu, footer {{ visibility:hidden; }}
          html, body, [class*="css"] {{
            font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Arial,sans-serif;
          }}

          .hero {{
            display:flex;
            align-items:flex-end;
            justify-content:space-between;
            gap:24px;
            margin-bottom:28px;
            padding:28px 30px;
            background:var(--ink);
            color:var(--white);
            border:2px solid var(--ink);
            box-shadow:5px 5px 0 var(--acid);
          }}
          .hero-kicker {{
            margin-bottom:8px;
            color:var(--acid);
            font-size:11px;
            font-weight:800;
            letter-spacing:.12em;
            text-transform:uppercase;
          }}
          .hero h1 {{
            margin:0;
            color:var(--white);
            font-size:clamp(32px,4vw,58px);
            font-weight:850;
            line-height:.98;
            letter-spacing:-.04em;
          }}
          .hero p {{
            margin:11px 0 0;
            color:#D8D6D0;
            font-size:14px;
          }}
          .live-badge {{
            display:flex;
            align-items:center;
            gap:8px;
            padding:9px 12px;
            border:1px solid #565656;
            font-size:11px;
            font-weight:800;
            letter-spacing:.08em;
            white-space:nowrap;
          }}
          .live-badge i {{
            width:8px;
            height:8px;
            background:var(--acid);
            border-radius:50%;
          }}

          .section-heading {{
            display:flex;
            align-items:flex-start;
            gap:13px;
            margin:30px 0 14px;
          }}
          .section-heading > span {{
            margin-top:2px;
            padding:5px 7px;
            background:var(--ink);
            color:var(--white);
            font-size:10px;
            font-weight:800;
            letter-spacing:.08em;
          }}
          .section-heading h2 {{
            margin:0;
            color:var(--ink);
            font-size:22px;
            font-weight:850;
            letter-spacing:-.02em;
          }}
          .section-description {{
            margin:4px 0 0;
            color:var(--muted);
            font-size:12px;
          }}

          .st-key-filter_panel {{
            padding:16px 17px 13px;
            background:var(--white);
            border:2px solid var(--ink);
            border-radius:0;
            box-shadow:4px 4px 0 var(--ink);
          }}
          .st-key-filter_panel label {{
            color:var(--ink)!important;
            font-size:11px!important;
            font-weight:800!important;
            letter-spacing:.04em;
            text-transform:uppercase;
          }}
          .st-key-filter_panel div[data-baseweb="input"] > div,
          .st-key-filter_panel div[data-baseweb="select"] > div {{
            min-height:43px;
            background:#FAF9F6!important;
            border:1.5px solid var(--ink)!important;
            border-radius:0!important;
            box-shadow:none!important;
          }}

          .kpi-grid {{
            display:grid;
            grid-template-columns:repeat(4,minmax(0,1fr));
            gap:16px;
            margin-bottom:8px;
          }}
          .kpi-card {{
            position:relative;
            min-height:158px;
            padding:18px 19px;
            overflow:hidden;
            background:var(--white);
            border:2px solid var(--ink);
            box-shadow:4px 4px 0 var(--ink);
          }}
          .kpi-card::before {{
            content:"";
            position:absolute;
            inset:0 auto 0 0;
            width:7px;
            background:var(--ink);
          }}
          .kpi-card.green::before {{ background:var(--green); }}
          .kpi-card.red::before {{ background:var(--red); }}
          .kpi-card.blue::before {{ background:var(--blue); }}
          .kpi-label {{
            color:var(--muted);
            font-size:11px;
            font-weight:850;
            letter-spacing:.07em;
            text-transform:uppercase;
          }}
          .kpi-value {{
            margin:14px 0 10px;
            color:var(--ink);
            font-size:clamp(38px,4vw,58px);
            font-weight:900;
            line-height:.9;
            letter-spacing:-.045em;
          }}
          .kpi-detail {{
            color:var(--muted);
            font-size:12px;
            line-height:1.35;
          }}

          .ranking-card,
          .st-key-status_card,
          .st-key-network_card {{
            background:var(--white);
            border:2px solid var(--ink);
            border-radius:0;
            box-shadow:4px 4px 0 var(--ink);
          }}
          .ranking-card,
          .st-key-status_card {{
            min-height:344px;
          }}
          .card-head {{
            min-height:54px;
            display:flex;
            align-items:center;
            justify-content:space-between;
            gap:12px;
            padding:13px 15px;
            border-bottom:2px solid var(--ink);
          }}
          .card-head h3 {{
            margin:0;
            color:var(--ink);
            font-size:13px;
            font-weight:850;
            letter-spacing:.01em;
          }}
          .card-head span {{
            padding:5px 7px;
            background:var(--acid);
            border:1px solid var(--ink);
            font-size:9px;
            font-weight:850;
            letter-spacing:.05em;
            text-transform:uppercase;
          }}
          .ranking-body {{ padding:5px 15px 10px; }}
          .rank-row {{
            display:grid;
            grid-template-columns:30px minmax(0,1fr) auto;
            align-items:center;
            gap:9px;
            min-height:52px;
            border-bottom:1px solid #DCD9D1;
          }}
          .rank-row:last-child {{ border-bottom:0; }}
          .rank-number {{
            display:grid;
            place-items:center;
            width:25px;
            height:25px;
            background:var(--ink);
            color:var(--white);
            font-size:10px;
            font-weight:850;
          }}
          .rank-name {{
            overflow:hidden;
            color:var(--ink);
            font-size:12px;
            font-weight:700;
            text-overflow:ellipsis;
            white-space:nowrap;
          }}
          .rank-row strong {{
            min-width:38px;
            padding:5px 7px;
            background:#FFE7E4;
            border:1px solid var(--red);
            color:var(--ink);
            font-size:12px;
            text-align:center;
          }}
          .healthy-state {{
            padding:35px 0;
            color:var(--green);
            font-size:12px;
            font-weight:800;
          }}
          .st-key-status_card .stPlotlyChart {{
            padding:0 6px 8px;
          }}

          .st-key-network_card {{
            padding-bottom:8px;
          }}
          .network-head {{
            display:flex;
            align-items:center;
            justify-content:space-between;
            gap:16px;
            padding:16px 18px;
            border-bottom:2px solid var(--ink);
          }}
          .network-head h3 {{
            margin:0;
            color:var(--ink);
            font-size:17px;
            font-weight:850;
          }}
          .network-head span {{
            color:var(--muted);
            font-size:11px;
            text-align:right;
          }}

          .detail-card {{
            overflow:hidden;
            background:var(--white);
            border:2px solid var(--ink);
            box-shadow:4px 4px 0 var(--ink);
          }}
          .detail-head {{
            display:flex;
            align-items:center;
            justify-content:space-between;
            gap:16px;
            padding:15px 18px;
            border-bottom:2px solid var(--ink);
          }}
          .detail-head span {{
            color:var(--muted);
            font-size:9px;
            font-weight:850;
            letter-spacing:.09em;
          }}
          .detail-head h3 {{
            margin:3px 0 0;
            color:var(--ink);
            font-size:20px;
            font-weight:850;
          }}
          .detail-head > strong {{
            padding:7px 9px;
            background:var(--ink);
            color:var(--white);
            font-size:10px;
            letter-spacing:.04em;
            text-transform:uppercase;
          }}
          .table-scroll {{
            max-height:620px;
            overflow:auto;
          }}
          .data-table {{
            width:100%;
            min-width:1050px;
            border-collapse:separate;
            border-spacing:0;
            font-size:12px;
          }}
          .data-table th {{
            position:sticky;
            top:0;
            z-index:2;
            padding:11px 12px;
            background:#EEECE6;
            color:var(--ink);
            border-right:1px solid #D4D1CA;
            border-bottom:1.5px solid var(--ink);
            font-size:10px;
            font-weight:850;
            letter-spacing:.055em;
            text-align:left;
            text-transform:uppercase;
          }}
          .data-table td {{
            padding:11px 12px;
            border-right:1px solid #E2DFD8;
            border-bottom:1px solid #E2DFD8;
            vertical-align:middle;
          }}
          .data-table tbody tr:nth-child(even) {{ background:#FAF9F6; }}
          .data-table tbody tr:hover {{ background:#F3F7D9; }}
          .table-primary {{
            max-width:330px;
            color:var(--ink);
            font-size:12px;
            font-weight:750;
            line-height:1.25;
          }}
          .table-muted {{
            max-width:260px;
            margin-top:4px;
            color:var(--muted);
            font-size:10px;
            line-height:1.3;
          }}
          .inventory-line strong {{
            font-size:15px;
            font-weight:900;
          }}
          .status-tag {{
            display:inline-block;
            max-width:250px;
            padding:5px 7px;
            border:1px solid var(--ink);
            color:var(--ink);
            font-size:9px;
            font-weight:850;
            line-height:1.2;
            text-transform:uppercase;
          }}
          .status-tag.red {{ background:#FFE2DF; border-color:var(--red); }}
          .status-tag.blue {{ background:#E8ECFF; border-color:var(--blue); }}
          .status-tag.green {{ background:#DFF5E9; border-color:var(--green); }}
          .status-tag.gray {{ background:#EEECE7; border-color:#AAA69E; }}
          .cedis-grid {{
            display:grid;
            grid-template-columns:repeat(4,minmax(46px,1fr));
            gap:5px;
          }}
          .cedis-cell {{
            min-width:48px;
            padding:5px 6px;
            border:1px solid var(--ink);
            text-align:center;
          }}
          .cedis-cell small {{
            display:block;
            margin-bottom:2px;
            font-size:8px;
            font-weight:800;
          }}
          .cedis-cell strong {{
            display:block;
            font-size:11px;
          }}
          .cedis-cell.positive {{
            background:var(--ink);
            color:var(--white);
          }}
          .cedis-cell.empty {{
            background:var(--white);
            color:#99968F;
            border-color:#C8C5BE;
          }}
          .empty-table {{
            padding:42px!important;
            color:var(--muted);
            font-weight:700;
            text-align:center;
          }}
          .table-footer {{
            display:flex;
            justify-content:space-between;
            gap:12px;
            padding:10px 13px;
            background:var(--ink);
            color:#D7D4CD;
            font-size:9px;
            font-weight:700;
            letter-spacing:.05em;
            text-transform:uppercase;
          }}

          div.stDownloadButton > button,
          div.stButton > button {{
            border:1.5px solid var(--ink);
            border-radius:0;
            background:var(--white);
            color:var(--ink);
            font-size:11px;
            font-weight:800;
            box-shadow:3px 3px 0 var(--ink);
          }}
          div.stDownloadButton > button:hover,
          div.stButton > button:hover {{
            border-color:var(--ink);
            background:var(--acid);
            color:var(--ink);
          }}
          ::-webkit-scrollbar {{ width:10px; height:10px; }}
          ::-webkit-scrollbar-track {{ background:#ECE9E2; }}
          ::-webkit-scrollbar-thumb {{ background:#A8A49C; border:2px solid #ECE9E2; }}

          @media(max-width:1050px) {{
            .kpi-grid {{ grid-template-columns:repeat(2,minmax(0,1fr)); }}
          }}
          @media(max-width:700px) {{
            .block-container {{ padding:1rem .8rem 2.5rem; }}
            .hero {{ align-items:flex-start; flex-direction:column; padding:22px 20px; }}
            .hero h1 {{ font-size:36px; }}
            .kpi-grid {{ grid-template-columns:1fr; }}
            .table-footer {{ flex-direction:column; }}
          }}
        </style>
        """
    )


def main() -> None:
    inject_css()
    render_header()

    with st.sidebar:
        st.markdown("### Fuente de datos")
        st.caption("GOLDEN / INFALTABLES TRACKER SKUS")
        st.caption("Pestaña: BASE · Cabeceras: fila 2")
        if st.button("Actualizar datos", use_container_width=True):
            load_data.clear()
            st.rerun()

    try:
        with st.spinner("Conectando con BASE y preparando el dashboard..."):
            raw_data, metadata = load_data()
    except Exception as exc:
        st.error("No fue posible cargar la hoja BASE.")
        st.code(str(exc))
        st.info(
            "Verifica que el archivo continúe habilitado para lectura pública. "
            "El dashboard ya contiene el ID, la pestaña y la fila de cabeceras correctos."
        )
        st.stop()

    section_title(
        "FILTROS",
        "Enfoque operativo",
        "Los indicadores y visualizaciones responden a esta selección.",
    )
    with st.container(key="filter_panel"):
        search_col, city_col, store_col, segment_col = st.columns(
            [1.8, 1, 1.2, 1]
        )
        with search_col:
            search = st.text_input(
                "SKU o producto",
                placeholder="Buscar por nombre o código...",
            )
        with city_col:
            city = st.selectbox(
                "Ciudad",
                ["Todas", *unique_options(raw_data, "CITY")],
            )

        store_source = (
            raw_data
            if city == "Todas"
            else raw_data.loc[text_series(raw_data, "CITY").eq(city)]
        )
        with store_col:
            store = st.selectbox(
                "Tienda / Warehouse",
                ["Todas", *unique_options(store_source, "WAREHOUSE_NAME")],
            )
        with segment_col:
            segment = st.selectbox(
                "Segmento / IGA",
                ["Todos", *unique_options(raw_data, "IGA")],
            )

    filtered = apply_filters(raw_data, search, city, store, segment)

    section_title(
        "RESUMEN",
        "Disponibilidad de la red",
        "Lectura ejecutiva del universo filtrado.",
    )
    render_kpis(filtered)

    section_title(
        "RIESGO",
        "Principales ofensores",
        "Tiendas, productos y estados que concentran los quiebres.",
    )
    stores_col, products_col, status_col = st.columns([1, 1, 0.95], gap="large")
    with stores_col:
        render_ranking_card(
            "Tiendas con más quiebres",
            "Warehouse",
            top_counts(filtered, "WAREHOUSE_NAME"),
        )
    with products_col:
        render_ranking_card(
            "Productos más quebrados",
            "SKU",
            top_counts(filtered, "PRODUCT_NAME"),
        )
    with status_col, st.container(key="status_card"):
        st.html(
            '<div class="card-head"><h3>Composición del estatus</h3><span>Mix</span></div>'
        )
        st.plotly_chart(
            status_figure(filtered),
            use_container_width=True,
            config={"displayModeBar": False, "responsive": True},
        )

    section_title(
        "RED",
        "AVL por tienda",
        "Se priorizan las tiendas con menor disponibilidad para mantener la gráfica legible.",
    )
    network_figure, network_note = store_figure(filtered, city)
    with st.container(key="network_card"):
        st.html(
            f"""
            <div class="network-head">
              <h3>Rendimiento de tiendas</h3>
              <span>{html.escape(network_note)}</span>
            </div>
            """
        )
        if network_figure is None:
            st.info("Sin tiendas para mostrar con los filtros actuales.")
        else:
            st.plotly_chart(
                network_figure,
                use_container_width=True,
                config={"displayModeBar": False, "responsive": True},
            )

    section_title(
        "EJECUCIÓN",
        "Detalle operativo",
        "Inventario de tienda, incoming y disponibilidad en CEDIS.",
    )
    action_left, action_right = st.columns([5, 1])
    with action_right:
        st.download_button(
            "Descargar vista CSV",
            data=filtered.to_csv(index=False).encode("utf-8-sig"),
            file_name="golden_infaltables_filtrado.csv",
            mime="text/csv",
            use_container_width=True,
        )
    render_detail_table(filtered)

    loaded_at = (
        str(metadata["loaded_at"])
        .replace("T", " ")
        .replace("+00:00", " UTC")
    )
    st.caption(
        f"BASE · {metadata['rows']:,} registros cargados · "
        f"Lectura: {loaded_at} · Caché: 5 minutos"
    )


if __name__ == "__main__":
    main()
