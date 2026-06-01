import base64
import json
import logging
import time
import httpx
from src.schemas.inference_agent_schema import TemporalVmlModel

logger = logging.getLogger("uvicorn.error")
MODEL_NAME = "qwen2.5vl:latest"
OLLAMA_BASE_URL = "http://127.0.0.1:11434"

with open("src/utils/prompts/inference_prompt. md", "r", encoding="utf-8") as f:
    prompt_agent = f.read()


def create_a_user_message(n_frames: int,target_condition: str) -> str :
    """
    Cria uma mensagem para o agente considerando o pedido do agente com os frames
    Considera o numero de frames  e as imagens a serem analisadas.
    """

    return (
        f"The following {n_frames} images are consecutive video frames "
        f"(Frame 1 = oldest, Frame {n_frames} = most recent).\n"
        f"TARGET CONDITION TO DETECT: '{target_condition}'\n"
        "Analyze the temporal sequence and return the JSON indicating if the target condition occurred."
    )

async def analyze_temporal_sequence(frames_b64: list[str], target_condition: str,client: httpx.AsyncClient,
    model_name: str = "qwen2.5vl",base_url: str = "http://localhost:11434" ) -> TemporalVmlModel | None:
    
    
    payload = {
        "model": model_name,
        "messages": [
            {"role": "system", "content": prompt_agent},
            {
                "role": "user",
                "content": create_a_user_message (len(frames_b64), target_condition),
                "images": frames_b64,
            },
        ],
        "stream": False,
        "format": "json",
        "options": {"temperature": 0, "seed": 42, "num_ctx": 8192}, 
    }
    
    t0 = time.perf_counter()
    try:
        response = await client.post(f"{base_url}/api/chat", json=payload)
        response.raise_for_status()
        raw = response.json().get("message", {}).get("content", "")
        
        elapsed = time.perf_counter() - t0
        logger.info("Inferência temporal concluída em %.2fs", elapsed)

        parsed = json.loads(raw)
        result = TemporalVmlModel.model_validate(parsed)
        
        if result.confidence == "low":
            logger.warning("VLM reportou confiança BAIXA — resultado pode ser impreciso.")

        return result
    
    except Exception as e:
        logger.warning("analyze_temporal_sequence falhou: %s", e)
        return None
