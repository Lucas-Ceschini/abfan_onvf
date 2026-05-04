from __future__ import annotations

import argparse
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
import requests
from bs4 import BeautifulSoup
from flask import Flask, jsonify, request


BASE_DIR = Path(__file__).resolve().parent
CATALOG_PATH = BASE_DIR / "pnadca_catalogo.json"
ALL_EXPORT_PATH = BASE_DIR / "pnadca_anual_brasil.xlsx"
DASHBOARD_EXPORT_PATH = BASE_DIR / "pnadca_dashboard_brasil.xlsx"
DASHBOARD_SUMMARY_PATH = BASE_DIR / "pnadca_dashboard_summary.json"

SIDRA_CATALOG_URL = "https://sidra.ibge.gov.br/pesquisa/pnadca/tabelas"
SIDRA_VALUES_URL = "https://apisidra.ibge.gov.br/values"
SIDRA_DESCRIPTOR_URL = "https://apisidra.ibge.gov.br/DescritoresTabela/t/{table_id}"
SIDRA_INTERNAL_DESCRIPTOR_URL = "https://sidra.ibge.gov.br/Ajax/JSon/Tabela/1/{table_id}?versao=-1"
REQUEST_TIMEOUT = 60
MAX_VALUES_PER_REQUEST = 100_000
DEFAULT_TERRITORY = "n1/1"
NORMALIZED_COLUMNS = [
    "tabela_id",
    "tabela_nome",
    "assunto",
    "pesquisa",
    "periodo_disponibilidade",
    "fonte",
    "NC",
    "NN",
    "MC",
    "MN",
    "V",
]
for dimension_index in range(1, 10):
    NORMALIZED_COLUMNS.extend([f"D{dimension_index}C", f"D{dimension_index}N"])

DASHBOARD_TABLES = {
    6678: "domicilios_numero_moradores",
}


@dataclass
class TableCatalogEntry:
    table_id: int
    table_name: str


class PnadContinuaAnualClient:
    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": (
                    "abfan-onvf-pnad-client/1.0 "
                    "(dashboard institucional; contato local)"
                )
            }
        )

    def fetch_catalog(self, refresh: bool = False) -> list[dict[str, Any]]:
        if CATALOG_PATH.exists() and not refresh:
            return json.loads(CATALOG_PATH.read_text(encoding="utf-8"))

        response = self.session.get(SIDRA_CATALOG_URL, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        deduped: list[dict[str, Any]] = []
        seen: set[int] = set()
        pending_ids: dict[str, int] = {}

        for anchor in soup.find_all("a", href=True):
            href = anchor["href"]
            match = re.search(r"/tabela/(\d+)$", href)
            if not match:
                continue

            text = " ".join(anchor.get_text(" ", strip=True).split())
            if not text:
                continue

            table_id = int(match.group(1))
            if text.isdigit():
                pending_ids[href] = table_id
                continue

            pending_id = pending_ids.get(href, table_id)
            if pending_id in seen:
                continue

            seen.add(pending_id)
            deduped.append(
                {
                    "table_id": pending_id,
                    "table_name": text,
                    "table_url": f"https://sidra.ibge.gov.br{href}",
                }
            )

        deduped.sort(key=lambda item: item["table_id"])
        CATALOG_PATH.write_text(
            json.dumps(deduped, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return deduped

    def fetch_descriptor(self, table_id: int) -> dict[str, Any]:
        response = self.session.get(
            SIDRA_DESCRIPTOR_URL.format(table_id=table_id),
            timeout=REQUEST_TIMEOUT,
        )
        if response.ok:
            try:
                return response.json()
            except ValueError:
                pass

        fallback = self.session.get(
            SIDRA_INTERNAL_DESCRIPTOR_URL.format(table_id=table_id),
            timeout=REQUEST_TIMEOUT,
        )
        fallback.raise_for_status()
        data = fallback.json()
        return self._normalize_internal_descriptor(data)

    def _normalize_internal_descriptor(self, descriptor: dict[str, Any]) -> dict[str, Any]:
        periods = [
            {
                "Codigo": period["Codigo"],
                "Nome": period["Nome"],
                "DataLiberacao": period.get("DataLiberacao", ""),
            }
            for period in descriptor.get("Periodos", {}).get("Periodos", [])
        ]
        variables = [
            {
                "Id": variable["Id"],
                "Nome": variable["Nome"],
                "UnidadeMedida": (
                    variable.get("UnidadeDeMedida", [{}])[0].get("Unidade", "")
                    if isinstance(variable.get("UnidadeDeMedida", []), list)
                    else variable.get("UnidadeDeMedida", {}).get("Nome", "")
                ),
            }
            for variable in descriptor.get("Variaveis", [])
        ]
        classifications = []
        for classification in descriptor.get("Classificacoes", []):
            classifications.append(
                {
                    "Id": classification["Id"],
                    "Nome": classification["Nome"],
                    "Categorias": [
                        {
                            "Id": category["Id"],
                            "Nome": category["Nome"],
                        }
                        for category in classification.get("Categorias", [])
                    ],
                }
            )

        return {
            "Id": descriptor["Id"],
            "Nome": descriptor["Nome"],
            "Assunto": descriptor.get("Pesquisa", {}).get("Nome", ""),
            "Pesquisa": descriptor.get("Pesquisa", {}).get("Nome", ""),
            "PeriodoDisponibilidade": (
                f"{min(p['Codigo'] for p in periods)} a {max(p['Codigo'] for p in periods)}"
                if periods
                else ""
            ),
            "Variaveis": variables,
            "Classificacoes": classifications,
            "Periodos": periods,
        }

    def build_query_chunks(
        self,
        descriptor: dict[str, Any],
        territory: str = DEFAULT_TERRITORY,
    ) -> list[str]:
        classification_parts = [
            f"c{classification['Id']}/all"
            for classification in descriptor.get("Classificacoes", [])
        ]
        periods = [str(period["Codigo"]) for period in descriptor.get("Periodos", [])]

        if not periods:
            periods = ["all"]

        variables_count = max(len(descriptor.get("Variaveis", [])), 1)
        classifications_count = 1
        for classification in descriptor.get("Classificacoes", []):
            categories = classification.get("Categorias", [])
            classifications_count *= max(len(categories), 1)

        estimated_values = variables_count * len(periods) * classifications_count
        chunk_count = max(1, math.ceil(estimated_values / MAX_VALUES_PER_REQUEST))
        chunk_size = max(1, math.ceil(len(periods) / chunk_count))

        queries: list[str] = []
        for start in range(0, len(periods), chunk_size):
            period_chunk = periods[start : start + chunk_size]
            period_selector = "all" if period_chunk == ["all"] else ",".join(period_chunk)
            parts = [
                f"t/{descriptor['Id']}",
                territory,
                f"p/{period_selector}",
                "v/allxp",
                *classification_parts,
            ]
            queries.append("/".join(parts))

        return queries

    def fetch_table_values(
        self,
        table_id: int,
        table_name: str | None = None,
        territory: str = DEFAULT_TERRITORY,
        classification_filters: dict[int, list[int]] | None = None,
    ) -> pd.DataFrame:
        descriptor = self.fetch_descriptor(table_id)
        queries = self.build_query_chunks(descriptor=descriptor, territory=territory)

        if classification_filters:
            queries = []
            parts = [f"t/{descriptor['Id']}", territory]
            periods = [str(period["Codigo"]) for period in descriptor.get("Periodos", [])] or ["all"]
            period_selector = ",".join(periods)
            base = parts + [f"p/{period_selector}", "v/allxp"]
            for classification in descriptor.get("Classificacoes", []):
                classification_id = classification["Id"]
                if classification_id in classification_filters:
                    values = ",".join(str(value) for value in classification_filters[classification_id])
                    base.append(f"c{classification_id}/{values}")
                else:
                    base.append(f"c{classification_id}/all")
            queries = ["/".join(base)]

        frames: list[pd.DataFrame] = []
        for query in queries:
            response = self.session.get(
                f"{SIDRA_VALUES_URL}/{query}?formato=json",
                timeout=REQUEST_TIMEOUT,
            )
            response.raise_for_status()
            payload = response.json()
            if len(payload) <= 1:
                continue

            frame = pd.DataFrame(payload[1:])
            frame.insert(0, "tabela_id", descriptor["Id"])
            frame.insert(1, "tabela_nome", table_name or descriptor["Nome"])
            frame.insert(2, "assunto", descriptor.get("Assunto", ""))
            frame.insert(3, "pesquisa", descriptor.get("Pesquisa", ""))
            frame.insert(4, "periodo_disponibilidade", descriptor.get("PeriodoDisponibilidade", ""))
            frame.insert(5, "fonte", "IBGE SIDRA - PNAD Contínua Anual")
            frames.append(frame)

        if not frames:
            return pd.DataFrame(columns=NORMALIZED_COLUMNS)

        combined = pd.concat(frames, ignore_index=True)
        for column in NORMALIZED_COLUMNS:
            if column not in combined.columns:
                combined[column] = ""
        return combined[NORMALIZED_COLUMNS]


def save_tables_to_excel(
    catalog: list[dict[str, Any]],
    tables: list[pd.DataFrame],
    output_path: Path,
    errors: list[dict[str, Any]],
) -> None:
    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        pd.DataFrame(catalog).to_excel(writer, sheet_name="catalogo", index=False)

        if tables:
            pd.concat(tables, ignore_index=True).to_excel(
                writer,
                sheet_name="dados",
                index=False,
            )
        else:
            pd.DataFrame(columns=NORMALIZED_COLUMNS).to_excel(
                writer,
                sheet_name="dados",
                index=False,
            )

        pd.DataFrame(errors or [{"status": "ok"}]).to_excel(
            writer,
            sheet_name="falhas",
            index=False,
        )


def export_full_pnadca_dataset(
    territory: str = DEFAULT_TERRITORY,
    limit: int | None = None,
    refresh_catalog: bool = False,
    output_path: Path = ALL_EXPORT_PATH,
) -> dict[str, Any]:
    client = PnadContinuaAnualClient()
    catalog = client.fetch_catalog(refresh=refresh_catalog)
    selected_catalog = catalog[:limit] if limit else catalog

    tables: list[pd.DataFrame] = []
    errors: list[dict[str, Any]] = []
    for entry in selected_catalog:
        try:
            tables.append(
                client.fetch_table_values(
                    table_id=entry["table_id"],
                    table_name=entry["table_name"],
                    territory=territory,
                )
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(
                {
                    "table_id": entry["table_id"],
                    "table_name": entry["table_name"],
                    "error": str(exc),
                }
            )

    save_tables_to_excel(
        catalog=selected_catalog,
        tables=tables,
        output_path=output_path,
        errors=errors,
    )
    return {
        "output_path": str(output_path),
        "tables_requested": len(selected_catalog),
        "tables_exported": len(tables),
        "errors": len(errors),
        "territory": territory,
    }


def _to_numeric(series: pd.Series) -> pd.Series:
    def normalize_value(value: Any) -> Any:
        text = str(value).strip()
        if text in {"...", "..", "X", ""}:
            return None
        if "," in text and "." in text:
            text = text.replace(".", "").replace(",", ".")
        elif "," in text:
            text = text.replace(",", ".")
        return text

    return pd.to_numeric(series.map(normalize_value), errors="coerce")


def build_dashboard_workbook(output_path: Path = DASHBOARD_EXPORT_PATH) -> dict[str, Any]:
    client = PnadContinuaAnualClient()
    table_frames: dict[str, pd.DataFrame] = {}

    for table_id, alias in DASHBOARD_TABLES.items():
        table_frames[alias] = client.fetch_table_values(
            table_id=table_id,
            table_name=alias,
            territory=DEFAULT_TERRITORY,
            classification_filters={
                68: [9902, 1092, 1093, 1094, 1095, 1096, 47267],
            },
        )

    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        for sheet_name, frame in table_frames.items():
            frame.to_excel(writer, sheet_name=sheet_name[:31], index=False)

    summary = build_dashboard_summary_from_frames(table_frames)
    DASHBOARD_SUMMARY_PATH.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return {
        "output_path": str(output_path),
        "summary_path": str(DASHBOARD_SUMMARY_PATH),
        "sheets": list(table_frames.keys()),
        "latest_year": summary["latest_year"],
    }


def build_dashboard_summary_from_frames(
    table_frames: dict[str, pd.DataFrame],
) -> dict[str, Any]:
    households = table_frames["domicilios_numero_moradores"].copy()
    households["valor"] = _to_numeric(households["V"])
    households["ano"] = pd.to_numeric(households["D2N"], errors="coerce")

    total = households[
        (households["D3N"] == "Domicílios") & (households["D4N"] == "Total")
    ].sort_values("ano")
    six_plus = households[
        (households["D3N"] == "Domicílios") & (households["D4N"] == "6 moradores ou mais")
    ].sort_values("ano")
    five = households[
        (households["D3N"] == "Domicílios") & (households["D4N"] == "5 moradores")
    ].sort_values("ano")
    share = households[
        (households["D3N"] == "Distribuição percentual dos domicílios")
        & (households["D4N"] == "6 moradores ou mais")
    ].sort_values("ano")

    latest_year = int(six_plus["ano"].max())
    latest_six_plus = float(six_plus.loc[six_plus["ano"] == latest_year, "valor"].iloc[0])
    latest_total = float(total.loc[total["ano"] == latest_year, "valor"].iloc[0])
    latest_share = float(share.loc[share["ano"] == latest_year, "valor"].iloc[0])
    latest_five = float(five.loc[five["ano"] == latest_year, "valor"].iloc[0])
    latest_five_plus_share = round(
        latest_six_plus / (latest_five + latest_six_plus) * 100,
        1,
    )
    latest_gap_vs_five = latest_six_plus - latest_five

    first_year = int(six_plus["ano"].min())
    first_value = float(six_plus.loc[six_plus["ano"] == first_year, "valor"].iloc[0])
    abs_change = latest_six_plus - first_value
    pct_change = round((latest_six_plus / first_value - 1) * 100, 1)
    ratio_vs_five = round(latest_six_plus / latest_five * 100, 1)

    trend_large = [
        {
            "ano": int(row["ano"]),
            "domicilios_mil": float(row["valor"]),
            "participacao_pct": float(
                share.loc[share["ano"] == row["ano"], "valor"].iloc[0]
            ),
        }
        for _, row in six_plus.iterrows()
    ]
    comparison_latest = [
        {"categoria": "5 moradores", "valor": latest_five},
        {"categoria": "6 moradores ou mais", "valor": latest_six_plus},
        {"categoria": "Total", "valor": latest_total},
    ]
    category_distribution_latest = [
        {
            "categoria": row["D4N"],
            "domicilios_mil": float(row["valor"]),
            "participacao_pct": float(
                households[
                    (households["D3N"] == "Distribuição percentual dos domicílios")
                    & (households["D4N"] == row["D4N"])
                    & (households["ano"] == latest_year)
                ]["valor"].iloc[0]
            ),
        }
        for _, row in households[
            (households["D3N"] == "Domicílios")
            & (households["D4N"] != "Total")
            & (households["ano"] == latest_year)
        ]
        .sort_values("valor", ascending=False)
        .iterrows()
    ]

    return {
        "source": "IBGE SIDRA - PNAD Contínua Anual",
        "territory": "Brasil",
        "latest_year": latest_year,
        "focus": "Domicílios com 6 moradores ou mais",
        "cards": {
            "large_households_mil": latest_six_plus,
            "large_households_share_pct": latest_share,
            "change_since_first_year_mil": abs_change,
            "change_since_first_year_pct": pct_change,
            "ratio_vs_five_pct": ratio_vs_five,
            "share_inside_five_plus_pct": latest_five_plus_share,
            "gap_vs_five_mil": latest_gap_vs_five,
        },
        "trend_large_households": trend_large,
        "comparison_latest": comparison_latest,
        "category_distribution_latest": category_distribution_latest,
    }


def create_app() -> Flask:
    app = Flask(__name__)

    @app.get("/health")
    def health() -> Any:
        return jsonify({"status": "ok"})

    @app.get("/pnadca/catalog")
    def pnadca_catalog() -> Any:
        refresh = request.args.get("refresh", "false").lower() == "true"
        client = PnadContinuaAnualClient()
        catalog = client.fetch_catalog(refresh=refresh)
        return jsonify(
            {
                "count": len(catalog),
                "catalog_path": str(CATALOG_PATH),
                "items": catalog,
            }
        )

    @app.post("/pnadca/export/all")
    @app.get("/pnadca/export/all")
    def export_all() -> Any:
        limit = request.args.get("limit", type=int)
        territory = request.args.get("territory", default=DEFAULT_TERRITORY)
        refresh = request.args.get("refresh", "false").lower() == "true"
        result = export_full_pnadca_dataset(
            territory=territory,
            limit=limit,
            refresh_catalog=refresh,
        )
        return jsonify(result)

    @app.post("/pnadca/export/dashboard")
    @app.get("/pnadca/export/dashboard")
    def export_dashboard() -> Any:
        result = build_dashboard_workbook()
        return jsonify(result)

    @app.get("/pnadca/dashboard-summary")
    def dashboard_summary() -> Any:
        if not DASHBOARD_SUMMARY_PATH.exists():
            build_dashboard_workbook()
        return jsonify(json.loads(DASHBOARD_SUMMARY_PATH.read_text(encoding="utf-8")))

    return app


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="API local e exportador da PNAD Contínua anual (IBGE SIDRA)."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve_parser = subparsers.add_parser("serve", help="Sobe a API local em Flask.")
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8000)

    export_all_parser = subparsers.add_parser(
        "export-all",
        help="Baixa tabelas da PNADCA e salva em XLSX.",
    )
    export_all_parser.add_argument("--territory", default=DEFAULT_TERRITORY)
    export_all_parser.add_argument("--limit", type=int)
    export_all_parser.add_argument("--refresh-catalog", action="store_true")

    subparsers.add_parser(
        "export-dashboard",
        help="Gera o XLSX resumido usado pelo dashboard.",
    )
    subparsers.add_parser(
        "catalog",
        help="Atualiza e salva o catálogo local de tabelas da PNADCA.",
    )

    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.command == "serve":
        app = create_app()
        app.run(host=args.host, port=args.port, debug=False)
        return

    if args.command == "catalog":
        client = PnadContinuaAnualClient()
        catalog = client.fetch_catalog(refresh=True)
        print(
            json.dumps(
                {"count": len(catalog), "catalog_path": str(CATALOG_PATH)},
                ensure_ascii=False,
            )
        )
        return

    if args.command == "export-all":
        result = export_full_pnadca_dataset(
            territory=args.territory,
            limit=args.limit,
            refresh_catalog=args.refresh_catalog,
        )
        print(json.dumps(result, ensure_ascii=False))
        return

    if args.command == "export-dashboard":
        result = build_dashboard_workbook()
        print(json.dumps(result, ensure_ascii=False))
        return


if __name__ == "__main__":
    main()
