import json
import logging
import httpx
import asyncio
import time
from analise_model import TemporalVmlModel
from collections import deque
from local_vision_bridge import GStreamerBridge
from image_pre_processing import MovimentDetector

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s", )
logger = logging.getLogger("vision.main")

MODEL_NAME = "qwen2.5vl:latest"
OLLAMA_BASE_URL = "http://127.0.0.1:11434"

with open("inference_prompt. md", "r", encoding="utf-8") as f:
    prompt_agent_inference = f.read()

#variveis para controle da taxa de inferencia
buffer_size = 4
min_interval_inference = 5.0
noise_ratio_max = 0.5
max_interval = 30
backoff_factor = 1.5


def create_a_user_message(n_frames: int) -> str :
    """
    Cria uma mensagem para o agente considerando o pedido do agente com os frames
    Considera o numero de frames  e as imagens a serem analisadas.
    """

    return (
        f"The following {n_frames} images are consecutive video frames "
        f"(Frame 1 = oldest, Frame {n_frames} = most recent).\n"
        "Analyze temporal changes from Frame 1 to Frame "
        f"{n_frames} and return the JSON."
    )

async def analyze_temporal_sequence(frames_b64: list[str],client: httpx.AsyncClient,) -> TemporalVmlModel | None:
    start_time = time.perf_counter()
    payload = {
        "model": MODEL_NAME,
        "messages": [
            {"role": "system", "content":prompt_agent_inference},
            {
                "role": "user",
                "content": create_a_user_message(len(frames_b64)),
                "images": frames_b64,
            },
        ],
        "stream": False,
        "format": "json",
        "options": {"temperature": 0, "seed": 42,"num_ctx": 8192 }, 
    }
    t0 = time.perf_counter()
    try:

        response = await client.post(f"{OLLAMA_BASE_URL}/api/chat",json=payload)
        response.raise_for_status()
        raw = response.json().get("message",{}).get("content","")
        elapsed = time.perf_counter() - t0
        logger.info("Inferência concluída em %.2fs", elapsed)

        parsed = json.loads(raw)
        result = TemporalVmlModel.model_validate(parsed)

        if result.confidence == "low":
            logger.warning("VLM reportou confiança BAIXA — resultado pode ser impreciso. "
                            "Considere ajustar threshold ou qualidade dos frames.")

        return result
    
    except json.JSONDecodeError as e:
        logger.warning("JSON inválido retornado pelo VLM: %s | raw=%r", e, raw[:200])
        return None
    
    except Exception as e:
        logger.warning("analyze_temporal_sequence falhou: %s", e)
        return None

async def  temporal_inference(frame_queue: asyncio.Queue)-> None:
    motion_detector = MovimentDetector(threshold=10.0, blur_threshold = 80.0)
    frame_buffer: deque[str] = deque(maxlen=buffer_size)
    last_inference_time = 0
    current_interval : float = min_interval_inference
    inference_lock = asyncio.Semaphore(1)
    
    async with httpx.AsyncClient(timeout=90.0) as client:
        while True:
            frame_b64 = await frame_queue.get()
            frame_buffer.append(frame_b64)
        
            if len (frame_buffer) < buffer_size:
                logger.debug("Buffer: %d/%d frames", len(frame_buffer), buffer_size)
                continue

            now = asyncio.get_running_loop().time()
           
            if (now - last_inference_time) < current_interval:
                continue
            
            if inference_lock.locked():
                            logger.debug("Inferência em andamento — ciclo ignorado.")
                            continue

            report = motion_detector.analyze_sequence(list(frame_buffer))
            logger.info(
                "MotionReport | detected=%s mean=%.2f max=%.2f "
                "valid_pairs=%d noisy=%d",
                report.motion_detected,report.mean_score, report.max_score,report.valid_pairs,report.noisy_frames,)

            total_pairs = buffer_size - 1

            if report.noisy_frames / total_pairs > noise_ratio_max:
                logger.warning("Muitos frames ruidosos (%d/%d) — inferência abortada.", report.noisy_frames,total_pairs,)
                continue

            if not report.motion_detected:
                 current_interval = min(current_interval * backoff_factor, max_interval)
                 logger.info("Sem movimento. Próximo intervalo: %.1fs", current_interval)
                 continue
            
            current_interval = min_interval_inference
            last_inference_time = now

            async with inference_lock:
                result = await analyze_temporal_sequence(list(frame_buffer), client)

            if result:
               logger.info(
                    "\n╔════════════════════════╗"
                    "\n║  scene_type : %-22s║"
                    "\n║  confidence : %-22s║"
                    "\n╚════════════════════════╝"
                    "\n%s\n",
                    result.scene_type,
                    result.confidence,
                    result.description,)
 

async def main ():
    frame_queue = asyncio.Queue(maxsize=2)

    bridge = GStreamerBridge(
        ros_topic="/camera/image_raw",
        target_fps=2.0,
        mock=False, 
    )

    bridge_task = asyncio.create_task(
        bridge.run(frame_queue)
    )

    consumer_task = asyncio.create_task(
        temporal_inference (frame_queue)
    )

    try:
        
        await asyncio.gather(bridge_task, consumer_task)
    
    except asyncio.CancelledError:
        logger.info("Pipeline encerrado.")




if __name__ == "__main__":
    asyncio.run(main())