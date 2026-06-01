import json
import re
from pydantic_ai import Agent
from pydantic_ai.models.ollama import OllamaModel
from pydantic_ai.providers.ollama import OllamaProvider
from src.schemas.expand_agent_schema import ExpandProposal

MODEL_NAME = "qwen2.5:14b"
OLLAMA_URL = "http://127.0.0.1:11434/v1"

with open("src/utils/prompts/expand_agent_prompt.md", "r", encoding="utf-8") as f:
    _system_prompt = f.read()

def parse_expand_proposal(raw: str) -> ExpandProposal:
    clean = re.sub(r"```(?:json)?", "", raw).strip().rstrip("`").strip()
    data = json.loads(clean)
    return ExpandProposal.model_validate(data)


expand_agent = Agent(
    model=OllamaModel(MODEL_NAME, provider=OllamaProvider(base_url=OLLAMA_URL)),
    model_settings={"temperature": 0.2},
    system_prompt=_system_prompt,
)