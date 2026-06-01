import re
import json
import logging
from json_repair import repair_json
from pydantic_ai import Agent
from pydantic_ai.models.ollama import OllamaModel
from pydantic_ai.providers.ollama import OllamaProvider
from src.schemas.validator_agent import BTFinalDecision

logger = logging.getLogger(__name__)

MODEL_NAME = 'qwen2.5:14b'
OLLAMA_URL = 'http://127.0.0.1:11434/v1'

with open("src/utils/prompts/validator_agent_prompt.md", "r", encoding="utf-8") as f:
    validator_prompt = f.read()

Decision_agent = Agent(
    model=OllamaModel(MODEL_NAME, provider=OllamaProvider(base_url=OLLAMA_URL)),
    model_settings={'temperature': 0.1},
    system_prompt=validator_prompt,
)

async def run_decision_agent(validation_input: str) -> BTFinalDecision:
    result = await Decision_agent.run(validation_input)
    raw = result.output

    clean = re.sub(r"```(?:json)?", "", raw).replace("```", "").strip()
    start = clean.find("{")
    end = clean.rfind("}") + 1

    if start == -1 or end == 0:
        raise ValueError(f"Nenhum JSON na resposta do validador: {raw[:300]}")

    clean = clean[start:end]

    try:
        data = json.loads(clean)
    except json.JSONDecodeError:
        logger.warning("JSON inválido do validador, aplicando repair_json")
        data = json.loads(repair_json(clean))

    logger.info(f"Validador retornou: {data}")
    return BTFinalDecision.model_validate(data)