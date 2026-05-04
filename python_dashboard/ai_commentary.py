from __future__ import annotations

import json
import os
from typing import Any


def get_ai_status() -> dict[str, Any]:
    try:
        from openai import OpenAI  # noqa: F401
    except ImportError:
        return {
            "ready": False,
            "message": (
                "A chave da OpenAI já existe, mas a biblioteca `openai` ainda não está instalada. "
                "Rode `python3 -m pip install openai`."
            ),
        }

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return {
            "ready": False,
            "message": (
                "A integração já está instalada. Agora basta configurar "
                "`OPENAI_API_KEY` para habilitar comentários automáticos."
            ),
        }

    return {
        "ready": True,
        "message": (
            "A integração com IA está pronta. Use o botão para gerar um comentário "
            "analítico a partir do recorte selecionado."
        ),
    }


def generate_data_commentary(payload: dict[str, Any]) -> dict[str, Any]:
    status = get_ai_status()
    if not status["ready"]:
        return {
            "title": "IA não configurada",
            "summary": status["message"],
            "bullets": [
                "Crie uma variável de ambiente `OPENAI_API_KEY`.",
                "Instale a biblioteca oficial `openai`.",
                "Reinicie o dashboard para usar a análise automática.",
            ],
            "caution": "Sem chave e sem SDK, o comentário não pode ser gerado automaticamente.",
        }

    from openai import OpenAI

    client = OpenAI()
    model = os.getenv("OPENAI_DASH_MODEL", "gpt-5.2")

    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "title": {"type": "string"},
            "summary": {"type": "string"},
            "bullets": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 3,
                "maxItems": 5,
            },
            "caution": {"type": "string"},
        },
        "required": ["title", "summary", "bullets", "caution"],
    }

    response = client.responses.create(
        model=model,
        input=[
            {
                "role": "system",
                "content": (
                    "Você é um analista de políticas familiares. Produza comentários curtos, "
                    "claros, profissionais e estritamente baseados nos dados enviados. "
                    "Não invente causalidade. Aponte tendências, comparações e limitações."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(payload, ensure_ascii=False),
            },
        ],
        text={
            "format": {
                "type": "json_schema",
                "name": "dashboard_commentary",
                "strict": True,
                "schema": schema,
            },
            "verbosity": "low",
        },
    )

    return json.loads(response.output_text)
