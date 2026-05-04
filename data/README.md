# Dados PNAD Contínua anual

Esta pasta concentra a API local e os artefatos de exportação da PNAD Contínua anual via IBGE SIDRA.

## Arquivos principais

- `pnad_continua_api.py`: cliente SIDRA, API local em Flask e comandos de exportação
- `pnadca_catalogo.json`: catálogo de tabelas anuais PNADCA
- `pnadca_anual_brasil.xlsx`: exportação ampla das tabelas consultadas
- `pnadca_dashboard_brasil.xlsx`: exportação resumida usada pelo dashboard
- `pnadca_dashboard_summary.json`: resumo estruturado para conferência rápida

## Como usar

Atualizar catálogo:

```bash
python3 data/pnad_continua_api.py catalog
```

Gerar os dados do dashboard:

```bash
python3 data/pnad_continua_api.py export-dashboard
```

Gerar exportação ampla da PNADCA para Brasil:

```bash
python3 data/pnad_continua_api.py export-all
```

Para um teste mais curto:

```bash
python3 data/pnad_continua_api.py export-all --limit 10
```

Subir a API local:

```bash
python3 data/pnad_continua_api.py serve --port 8000
```

## Endpoints

- `GET /health`
- `GET /pnadca/catalog`
- `GET /pnadca/export/dashboard`
- `GET /pnadca/export/all?limit=10`
- `GET /pnadca/dashboard-summary`

## Observação

A exportação completa da PNADCA pode levar tempo, porque o catálogo anual do SIDRA contém muitas tabelas. O fluxo do dashboard usa um conjunto curado de tabelas oficiais para manter a interface leve e útil.
