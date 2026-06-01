import asyncio
import base64
import aiohttp
from pathlib import Path
from pydantic_ai import Agent
from pydantic_ai.models.ollama import OllamaModel
from pydantic_ai.providers.ollama import OllamaProvider
from src.schemas.validator_agent import BTFinalDecision
from src.schemas.vision_agent_schema import SceneDescription

MODEL_NAME = 'qwen2.5vl:latest'
OLLAMA_BASE_URL = 'http://127.0.0.1:11434'

with open("src/utils/prompts/vision_agent_prompt.md", "r", encoding="utf-8") as f:
    vision_prompt = f.read()

Decision_agent = Agent(
    model=OllamaModel(MODEL_NAME, provider=OllamaProvider(base_url=OLLAMA_BASE_URL)),
    model_settings={
        'temperature': 0.1,
    },
    system_prompt=vision_prompt,
)

async def describe_scene(image_path: str | None = None, frame_b64: str | None = None) -> SceneDescription:
    """Descreve a cena da imagem usando o VLM."""

    # Carrega e codifica a imagem
    if frame_b64 is None and image_path is not None:
        img_bytes = Path(image_path).read_bytes()
        b64_img = base64.b64encode(img_bytes).decode("utf-8")
    elif frame_b64 is not None:
        b64_img = frame_b64
    else:
        raise ValueError("Você precisa fornecer um image_path ou um frame_b64")

    url = f"{OLLAMA_BASE_URL}/api/chat"
    data = {
        "model": MODEL_NAME,
        "messages": [
            {"role": "system", "content": vision_prompt},
            {"role": "user", "content": "Descreva esta cena em formato estruturado (JSON).", "images": [b64_img]},
        ],
        "stream": False,
        "options": {"temperature": 0.1}
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=data) as resp:
            result = await resp.json()
            raw_output = result['message']['content']
            print(f"DEBUG VLM OUTPUT:\n{raw_output}\n")  # Debug

    # Parse o JSON da resposta
    import json
    clean = raw_output.strip()
    if clean.startswith("```json"):
        clean = clean[7:]
    if clean.endswith("```"):
        clean = clean[:-3]
    clean = clean.strip()

    data_dict = json.loads(clean)
    return SceneDescription.model_validate(data_dict)