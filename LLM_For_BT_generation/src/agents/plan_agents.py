import json
import re
from pydantic_ai import Agent
from pydantic_ai.models.ollama import OllamaModel
from pydantic_ai.providers.ollama import OllamaProvider
from src.schemas.plan_agents import BTProposal

MODEL_NAME = 'qwen2.5:14b'
OLLAMA_URL = 'http://127.0.0.1:11434/v1'

with open("src/utils/prompts/plan_agent_prompt.md", "r", encoding="utf-8") as f:
    prompt_agent = f.read()


def parse_bt_proposal(raw: str) -> BTProposal:
    clean = re.sub(r"```(?:json)?", "", raw).strip().rstrip("`").strip()
    data = json.loads(clean)
    return BTProposal.model_validate(data)



Plan_agent_1 = Agent(
    model=OllamaModel(MODEL_NAME, provider=OllamaProvider(base_url=OLLAMA_URL)),
    model_settings={
        'temperature': 0.3,
    },
    system_prompt=prompt_agent,
)

Plan_agent_2 = Agent(
    model=OllamaModel(MODEL_NAME, provider=OllamaProvider(base_url=OLLAMA_URL)),
    model_settings={
        'temperature': 0.3,
        'extra_body': {'think': False},
    },
    system_prompt=prompt_agent,
)