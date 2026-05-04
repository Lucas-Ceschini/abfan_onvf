from __future__ import annotations

from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from dash import Dash, Input, Output, State, dash_table, dcc, html

try:
    from python_dashboard.ai_commentary import generate_data_commentary, get_ai_status
except ModuleNotFoundError:
    from ai_commentary import generate_data_commentary, get_ai_status


app = Dash(__name__, title="Observatório Nacional da Vida e Família | ABFAN")
server = app.server

BASE_DIR = Path(__file__).resolve().parents[1]
DASHBOARD_DATA_PATH = BASE_DIR / "data" / "pnadca_dashboard_brasil.xlsx"
FOCUS_LABEL = "6 moradores ou mais"
CATEGORY_ORDER = [
    "1 morador",
    "2 moradores",
    "3 moradores",
    "4 moradores",
    "5 moradores",
    "6 moradores ou mais",
]

LINKS_RAPIDOS = [
    ("abfanonvf.ocm", "https://abfan.developforweb.com.br/"),
    ("Blog em Quarto", "http://127.0.0.1:4200/"),
    ("Publicar estudos em Quarto", "http://127.0.0.1:4200/guia-postagens.html"),
    ("Atualizar base PNAD", "http://127.0.0.1:8000/pnadca/export/dashboard"),
]

COLOR_MAP = {
    "1 morador": "#b8c4cc",
    "2 moradores": "#9fb6be",
    "3 moradores": "#7fa3ad",
    "4 moradores": "#5e8e99",
    "5 moradores": "#e6a15a",
    "6 moradores ou mais": "#d85f1f",
}


def _numeric(series: pd.Series) -> pd.Series:
    def normalize_value(value):
        text = str(value).strip()
        if text in {"...", "..", "X", "-", ""}:
            return None
        if "," in text and "." in text:
            text = text.replace(".", "").replace(",", ".")
        elif "," in text:
            text = text.replace(",", ".")
        return text

    return pd.to_numeric(series.map(normalize_value), errors="coerce")


def format_mil(value: float) -> str:
    return f"{value:,.0f}".replace(",", ".")


def format_signed_mil(value: float) -> str:
    sign = "+" if value >= 0 else "-"
    return f"{sign}{format_mil(abs(value))}"


def format_pct(value: float) -> str:
    return f"{value:.1f}%".replace(".", ",")


def format_ratio(value: float) -> str:
    return f"{value:.1f}".replace(".", ",")


def load_household_data() -> tuple[pd.DataFrame, dict]:
    if not DASHBOARD_DATA_PATH.exists():
        empty = pd.DataFrame(columns=["ano", "categoria", "variavel", "valor"])
        return empty, {"loaded": False}

    raw = pd.read_excel(DASHBOARD_DATA_PATH, sheet_name="domicilios_numero_moradores")
    raw["valor"] = _numeric(raw["V"])
    raw["ano"] = pd.to_numeric(raw["D2N"], errors="coerce")
    raw = raw.rename(columns={"D3N": "variavel", "D4N": "categoria"})
    raw = raw[raw["ano"].notna()].copy()
    raw["ano"] = raw["ano"].astype(int)

    counts = raw[raw["variavel"] == "Domicílios"].copy()
    shares = raw[raw["variavel"] == "Distribuição percentual dos domicílios"].copy()
    categories = [category for category in CATEGORY_ORDER if category in counts["categoria"].values]
    years = sorted(counts["ano"].unique().tolist())

    return raw, {
        "loaded": True,
        "counts": counts,
        "shares": shares,
        "categories": categories,
        "years": years,
        "first_year": min(years) if years else None,
        "latest_year": max(years) if years else None,
    }


RAW_DF, DATA_CONTEXT = load_household_data()


def compute_snapshot(year: int) -> dict:
    counts = DATA_CONTEXT["counts"]
    shares = DATA_CONTEXT["shares"]

    total = float(
        counts.loc[(counts["ano"] == year) & (counts["categoria"] == "Total"), "valor"].iloc[0]
    )
    six_plus = float(
        counts.loc[(counts["ano"] == year) & (counts["categoria"] == FOCUS_LABEL), "valor"].iloc[0]
    )
    five = float(
        counts.loc[(counts["ano"] == year) & (counts["categoria"] == "5 moradores"), "valor"].iloc[0]
    )
    share = float(
        shares.loc[(shares["ano"] == year) & (shares["categoria"] == FOCUS_LABEL), "valor"].iloc[0]
    )

    first_year = DATA_CONTEXT["first_year"]
    first_value = float(
        counts.loc[(counts["ano"] == first_year) & (counts["categoria"] == FOCUS_LABEL), "valor"].iloc[0]
    )
    change_abs = six_plus - first_value
    change_pct = ((six_plus / first_value) - 1) * 100
    share_inside_five_plus = six_plus / (six_plus + five) * 100
    gap_vs_five = six_plus - five
    per_100_households = share
    ratio_vs_five = six_plus / five * 100

    return {
        "year": year,
        "total": total,
        "six_plus": six_plus,
        "five": five,
        "share": share,
        "change_abs": change_abs,
        "change_pct": change_pct,
        "share_inside_five_plus": share_inside_five_plus,
        "gap_vs_five": gap_vs_five,
        "per_100_households": per_100_households,
        "ratio_vs_five": ratio_vs_five,
    }


def build_cards(snapshot: dict) -> list[dict]:
    return [
        {
            "valor": format_mil(snapshot["six_plus"]),
            "rotulo": f"Domicílios com 6+ moradores ({snapshot['year']}, mil)",
        },
        {
            "valor": format_pct(snapshot["share"]),
            "rotulo": "Participação no total de domicílios",
        },
        {
            "valor": format_signed_mil(snapshot["change_abs"]),
            "rotulo": f"Variação absoluta desde {DATA_CONTEXT['first_year']} (mil)",
        },
        {
            "valor": format_pct(snapshot["change_pct"]),
            "rotulo": f"Variação percentual desde {DATA_CONTEXT['first_year']}",
        },
        {
            "valor": format_pct(snapshot["share_inside_five_plus"]),
            "rotulo": "Peso dos domicílios 6+ entre os domicílios 5+",
        },
        {
            "valor": format_signed_mil(snapshot["gap_vs_five"]),
            "rotulo": "Diferença frente aos domicílios com 5 moradores",
        },
    ]


def build_story(snapshot: dict) -> list[str]:
    direction = "cresceu" if snapshot["change_abs"] >= 0 else "caiu"
    gap_direction = "abaixo" if snapshot["gap_vs_five"] < 0 else "acima"
    return [
        (
            f"Em {snapshot['year']}, o Brasil registrou {format_mil(snapshot['six_plus'])} mil "
            f"domicílios com 6 ou mais moradores."
        ),
        (
            f"Esse grupo representa {format_pct(snapshot['share'])} do total de domicílios, "
            f"ou {format_pct(snapshot['per_100_households'])} a cada 100 domicílios."
        ),
        (
            f"Desde {DATA_CONTEXT['first_year']}, esse contingente {direction} "
            f"{format_signed_mil(snapshot['change_abs'])} mil, equivalente a "
            f"{format_pct(snapshot['change_pct'])}."
        ),
        (
            f"No ano selecionado, os domicílios 6+ estão {gap_direction} dos domicílios "
            f"com 5 moradores em {format_signed_mil(snapshot['gap_vs_five'])} mil."
        ),
    ]


def build_trend_figure(metric: str, years_range: list[int]) -> go.Figure:
    counts = DATA_CONTEXT["counts"]
    shares = DATA_CONTEXT["shares"]
    start_year, end_year = years_range

    if metric == "share":
        frame = shares[
            (shares["categoria"] == FOCUS_LABEL)
            & (shares["ano"] >= start_year)
            & (shares["ano"] <= end_year)
        ].sort_values("ano")
        y_col = "valor"
        title = "Participação dos domicílios com 6+ moradores"
        yaxis_title = "% do total de domicílios"
    else:
        frame = counts[
            (counts["categoria"] == FOCUS_LABEL)
            & (counts["ano"] >= start_year)
            & (counts["ano"] <= end_year)
        ].sort_values("ano")
        y_col = "valor"
        title = "Evolução dos domicílios com 6+ moradores"
        yaxis_title = "Mil domicílios"

    fig = px.line(
        frame,
        x="ano",
        y=y_col,
        markers=True,
        title=title,
    )
    fig.update_traces(line_color="#d85f1f", marker_size=10, line_width=4)
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        margin=dict(l=20, r=20, t=60, b=20),
        xaxis_title="Ano",
        yaxis_title=yaxis_title,
        font=dict(color="#1c2a33"),
    )
    return fig


def build_distribution_figure(year: int, metric: str) -> go.Figure:
    frame = DATA_CONTEXT["counts"] if metric == "count" else DATA_CONTEXT["shares"]
    frame = frame[
        (frame["ano"] == year)
        & (frame["categoria"].isin(DATA_CONTEXT["categories"]))
    ].copy()
    frame["categoria"] = pd.Categorical(frame["categoria"], CATEGORY_ORDER, ordered=True)
    frame = frame.sort_values("categoria")

    title = f"Distribuição por tamanho do domicílio em {year}"
    yaxis_title = "Mil domicílios" if metric == "count" else "% do total"
    fig = px.bar(
        frame,
        x="categoria",
        y="valor",
        color="categoria",
        title=title,
        color_discrete_map=COLOR_MAP,
    )
    fig.update_layout(
        showlegend=False,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        margin=dict(l=20, r=20, t=60, b=20),
        xaxis_title="Faixa de moradores",
        yaxis_title=yaxis_title,
        font=dict(color="#1c2a33"),
    )
    return fig


def build_comparison_figure(metric: str, years_range: list[int], selected_categories: list[str]) -> go.Figure:
    frame = DATA_CONTEXT["counts"] if metric == "count" else DATA_CONTEXT["shares"]
    start_year, end_year = years_range
    filtered = frame[
        (frame["ano"] >= start_year)
        & (frame["ano"] <= end_year)
        & (frame["categoria"].isin(selected_categories))
    ].copy()
    filtered["categoria"] = pd.Categorical(filtered["categoria"], CATEGORY_ORDER, ordered=True)
    filtered = filtered.sort_values(["categoria", "ano"])

    fig = px.line(
        filtered,
        x="ano",
        y="valor",
        color="categoria",
        markers=True,
        color_discrete_map=COLOR_MAP,
        title="Comparação entre faixas de moradores",
    )
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        margin=dict(l=20, r=20, t=60, b=20),
        xaxis_title="Ano",
        yaxis_title="Mil domicílios" if metric == "count" else "% do total",
        legend_title="Faixa",
        font=dict(color="#1c2a33"),
    )
    return fig


def build_table_rows(year: int) -> list[dict]:
    counts = DATA_CONTEXT["counts"]
    shares = DATA_CONTEXT["shares"]
    rows = []
    for category in DATA_CONTEXT["categories"]:
        count_value = float(
            counts.loc[(counts["ano"] == year) & (counts["categoria"] == category), "valor"].iloc[0]
        )
        share_value = float(
            shares.loc[(shares["ano"] == year) & (shares["categoria"] == category), "valor"].iloc[0]
        )
        rows.append(
            {
                "Faixa": category,
                "Domicilios_mil": format_mil(count_value),
                "Participacao_pct": format_pct(share_value),
                "Indice_6mais": "Grupo focal" if category == FOCUS_LABEL else "",
            }
        )
    return rows


def card_component(item: dict) -> html.Div:
    emphasis = "stat-card stat-card--focus" if "6+" in item["rotulo"] else "stat-card"
    return html.Div(
        className=emphasis,
        children=[
            html.P(item["rotulo"], className="stat-card__label"),
            html.H3(item["valor"], className="stat-card__value"),
        ],
    )


def ai_panel_default_message() -> str:
    status = get_ai_status()
    return status["message"]


initial_year = DATA_CONTEXT.get("latest_year")
initial_range = [DATA_CONTEXT.get("first_year"), DATA_CONTEXT.get("latest_year")] if DATA_CONTEXT.get("loaded") else [2016, 2025]
initial_categories = ["3 moradores", "4 moradores", "5 moradores", "6 moradores ou mais"]


app.layout = html.Div(
    className="app-shell",
    children=[
        html.Header(
            className="topbar",
            children=[
                html.Div(
                    className="brand-block",
                    children=[
                        html.P("ONVF | ABFAN", className="brand"),
                        html.P(
                            "Painel profissional sobre domicílios com 6 ou mais moradores",
                            className="subtitle",
                        ),
                    ],
                ),
                html.Nav(
                    className="top-links",
                    children=[
                        html.A("Blog em Quarto", href="http://127.0.0.1:4200/", target="_blank", rel="noreferrer", className="header-link"),
                        html.A("abfanonvf.ocm", href="https://abfan.developforweb.com.br/", target="_blank", rel="noreferrer", className="header-link"),
                        html.A("API PNAD", href="http://127.0.0.1:8000/pnadca/dashboard-summary", target="_blank", rel="noreferrer", className="header-link"),
                    ],
                ),
            ],
        ),
        html.Main(
            className="page-shell",
            children=[
                html.Section(
                    className="hero-banner",
                    children=[
                        html.Div(
                            className="hero-copy",
                            children=[
                                html.Span("PNAD CONTÍNUA ANUAL | IBGE", className="eyebrow"),
                                html.H1("Famílias numerosas em foco"),
                                html.P(
                                    "Um dashboard executivo, interativo e orientado a evidências "
                                    "para acompanhar a evolução dos domicílios com 6 ou mais pessoas "
                                    "na mesma residência."
                                ),
                                html.Div(
                                    className="hero-actions",
                                    children=[
                                        html.A("Atualizar base PNAD", href="http://127.0.0.1:8000/pnadca/export/dashboard", target="_blank", rel="noreferrer", className="btn-primary"),
                                        html.A("Abrir blog em Quarto", href="http://127.0.0.1:4200/", target="_blank", rel="noreferrer", className="btn-secondary"),
                                    ],
                                ),
                            ],
                        ),
                        html.Div(
                            className="hero-highlight",
                            children=[
                                html.P("Recorte analítico", className="hero-highlight__label"),
                                html.H2("6 moradores ou mais", className="hero-highlight__value"),
                                html.P(
                                    "Categoria oficial da tabela 6678 da PNAD Contínua anual, "
                                    "código c68/47267.",
                                    className="hero-highlight__note",
                                ),
                            ],
                        ),
                    ],
                ),
                html.Section(
                    className="control-panel",
                    children=[
                        html.Div(
                            className="control-card control-card--wide",
                            children=[
                                html.P("Ano de referência", className="control-label"),
                                dcc.Dropdown(
                                    id="year-dropdown",
                                    options=[{"label": str(year), "value": year} for year in DATA_CONTEXT.get("years", [])],
                                    value=initial_year,
                                    clearable=False,
                                ),
                            ],
                        ),
                        html.Div(
                            className="control-card",
                            children=[
                                html.P("Métrica principal", className="control-label"),
                                dcc.RadioItems(
                                    id="metric-radio",
                                    options=[
                                        {"label": "Mil domicílios", "value": "count"},
                                        {"label": "Participação %", "value": "share"},
                                    ],
                                    value="count",
                                    className="radio-group",
                                    inputClassName="radio-input",
                                    labelClassName="radio-label",
                                ),
                            ],
                        ),
                        html.Div(
                            className="control-card control-card--wide",
                            children=[
                                html.P("Janela temporal", className="control-label"),
                                dcc.RangeSlider(
                                    id="year-range",
                                    min=initial_range[0],
                                    max=initial_range[1],
                                    step=None,
                                    value=initial_range,
                                    marks={year: str(year) for year in DATA_CONTEXT.get("years", [])},
                                ),
                            ],
                        ),
                        html.Div(
                            className="control-card control-card--wide",
                            children=[
                                html.P("Faixas comparadas", className="control-label"),
                                dcc.Dropdown(
                                    id="category-dropdown",
                                    options=[{"label": category, "value": category} for category in DATA_CONTEXT.get("categories", [])],
                                    value=initial_categories,
                                    multi=True,
                                    clearable=False,
                                ),
                            ],
                        ),
                    ],
                ),
                html.Section(id="cards-grid", className="stats-grid"),
                html.Section(
                    className="executive-grid",
                    children=[
                        html.Div(className="panel chart-panel", children=[dcc.Graph(id="trend-graph", config={"displayModeBar": False})]),
                        html.Div(
                            className="panel insight-panel",
                            children=[
                                html.P("Leitura executiva", className="panel-kicker"),
                                html.Div(id="story-panel", className="story-list"),
                            ],
                        ),
                    ],
                ),
                html.Section(
                    className="chart-grid chart-grid--professional",
                    children=[
                        html.Div(className="panel chart-panel", children=[dcc.Graph(id="distribution-graph", config={"displayModeBar": False})]),
                        html.Div(className="panel chart-panel", children=[dcc.Graph(id="comparison-graph", config={"displayModeBar": False})]),
                    ],
                ),
                html.Section(
                    className="panel",
                    children=[
                        html.Div(
                            className="panel-head",
                            children=[
                                html.Div(
                                    children=[
                                        html.P("Base analítica", className="panel-kicker"),
                                        html.H2("Detalhamento por faixa de moradores"),
                                    ]
                                )
                            ],
                        ),
                        dash_table.DataTable(
                            id="analysis-table",
                            columns=[
                                {"name": "Faixa", "id": "Faixa"},
                                {"name": "Domicílios (mil)", "id": "Domicilios_mil"},
                                {"name": "Participação", "id": "Participacao_pct"},
                                {"name": "Observação", "id": "Indice_6mais"},
                            ],
                            style_table={"overflowX": "auto"},
                            style_cell={
                                "padding": "14px 12px",
                                "border": "none",
                                "backgroundColor": "transparent",
                                "color": "#1c2a33",
                                "fontFamily": "Segoe UI, sans-serif",
                            },
                            style_header={
                                "backgroundColor": "#edf3f4",
                                "fontWeight": "700",
                                "color": "#08272e",
                                "border": "none",
                            },
                            style_data_conditional=[
                                {
                                    "if": {"filter_query": "{Faixa} = '6 moradores ou mais'"},
                                    "backgroundColor": "#fff2e8",
                                    "color": "#9d3d0f",
                                    "fontWeight": "700",
                                }
                            ],
                        ),
                    ],
                ),
                html.Section(
                    className="panel ai-panel",
                    children=[
                        html.Div(
                            className="panel-head",
                            children=[
                                html.Div(
                                    children=[
                                        html.P("Assistente analítico", className="panel-kicker"),
                                        html.H2("Comentários com IA sobre os dados"),
                                    ]
                                ),
                                html.Button("Gerar comentário com IA", id="ai-generate-button", n_clicks=0, className="ai-button"),
                            ],
                        ),
                        dcc.Loading(
                            children=html.Div(id="ai-commentary", className="ai-commentary"),
                            type="default",
                        ),
                        html.P(
                            "A análise automática usa um resumo do ano selecionado e da série temporal filtrada.",
                            className="ai-footnote",
                        ),
                    ],
                ),
                html.Section(
                    className="panel links-panel",
                    children=[
                        html.P("Operação e fontes", className="panel-kicker"),
                        html.H2("Fluxo do projeto"),
                        html.Ul(
                            className="quick-links",
                            children=[
                                html.Li(html.A(texto, href=url, target="_blank", rel="noreferrer"))
                                for texto, url in LINKS_RAPIDOS
                            ],
                        ),
                        html.P(
                            "Os dados vêm do arquivo `data/pnadca_dashboard_brasil.xlsx`, gerado pela API local "
                            "em `data/pnad_continua_api.py` com base na PNAD Contínua anual do IBGE."
                        ),
                    ],
                ),
            ],
        ),
    ],
)


@app.callback(
    Output("cards-grid", "children"),
    Output("story-panel", "children"),
    Output("trend-graph", "figure"),
    Output("distribution-graph", "figure"),
    Output("comparison-graph", "figure"),
    Output("analysis-table", "data"),
    Input("year-dropdown", "value"),
    Input("metric-radio", "value"),
    Input("year-range", "value"),
    Input("category-dropdown", "value"),
)
def update_dashboard(year: int, metric: str, years_range: list[int], categories: list[str]):
    if not year or not categories:
        return [], [], go.Figure(), go.Figure(), go.Figure(), []

    snapshot = compute_snapshot(year)
    cards = [card_component(item) for item in build_cards(snapshot)]
    story = [html.Div(sentence, className="story-item") for sentence in build_story(snapshot)]
    trend_fig = build_trend_figure(metric=metric, years_range=years_range)
    distribution_fig = build_distribution_figure(year=year, metric=metric)
    comparison_fig = build_comparison_figure(metric=metric, years_range=years_range, selected_categories=categories)
    table_rows = build_table_rows(year)
    return cards, story, trend_fig, distribution_fig, comparison_fig, table_rows


@app.callback(
    Output("ai-commentary", "children"),
    Input("ai-generate-button", "n_clicks"),
    State("year-dropdown", "value"),
    State("metric-radio", "value"),
    State("year-range", "value"),
    State("category-dropdown", "value"),
)
def update_ai_commentary(n_clicks: int, year: int, metric: str, years_range: list[int], categories: list[str]):
    if not n_clicks:
        return dcc.Markdown(ai_panel_default_message())

    snapshot = compute_snapshot(year)
    series_frame = (DATA_CONTEXT["counts"] if metric == "count" else DATA_CONTEXT["shares"]).copy()
    series_frame = series_frame[
        (series_frame["ano"] >= years_range[0])
        & (series_frame["ano"] <= years_range[1])
        & (series_frame["categoria"].isin(categories))
    ].sort_values(["categoria", "ano"])

    payload = {
        "focus": FOCUS_LABEL,
        "year": year,
        "metric": metric,
        "snapshot": snapshot,
        "selected_categories": categories,
        "series": [
            {
                "ano": int(row["ano"]),
                "categoria": row["categoria"],
                "valor": float(row["valor"]),
            }
            for _, row in series_frame.iterrows()
        ],
    }
    commentary = generate_data_commentary(payload)
    bullets = "\n".join(f"- {item}" for item in commentary.get("bullets", []))
    markdown = (
        f"### {commentary['title']}\n\n"
        f"{commentary['summary']}\n\n"
        f"{bullets}\n\n"
        f"**Cautela:** {commentary['caution']}"
    )
    return dcc.Markdown(markdown)


if __name__ == "__main__":
    app.run(debug=False)
