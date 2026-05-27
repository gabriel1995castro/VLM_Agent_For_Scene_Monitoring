import json
import logging
import httpx
import asyncio
import time
from analise_model import TemporalVmlModel
from collections import deque
from local_vision_bridge import GStreamerBridge
from image_pre_processing import MovimentDetector

logger = logging.getLogger("uvicorn.error")

MODEL_NAME = "qwen2.5vl:latest"
OLLAMA_BASE_URL = "http://127.0.0.1:11434"

async def analyze_temporal_sequence(frames_b64: list[str],) -> TemporalVmlModel | None:
    start_time = time.perf_counter()
    payload = {
        "model": MODEL_NAME,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a temporal video understanding assistant.\n"
                    "The images represent consecutive moments in time.\n"
                    "Analyze the sequence chronologically from oldest to newest.\n"
                    "Focus ONLY on changes between frames.\n"
                    "Describe:\n"
                    "- movement\n"
                    "- object displacement\n"
                    "- hand interactions\n"
                    "- action progression\n"
                    "- state transitions\n\n"
                    "If nothing changes significantly, say that the scene remained mostly static.\n\n"
                    "Respond ONLY with valid JSON.\n"
                    'Format: {"description": "..."}'
                ),
            },
            {
                "role": "user",
                "content": (
                    "Response in Portuguese - BR\n"
                    "These images are consecutive moments in time.\n"
                    "Describe ONLY meaningful changes across the sequence.\n"
                    "If the scene remains mostly unchanged, say:\n"
                    "'No significant temporal change detected.'"
                ),
                "images": frames_b64,
            },
        ],
        "stream": False,
        "format": "json",
        "options": {
            "temperature": 0
        },
    }

    try:

        async with httpx.AsyncClient(timeout=60.0) as client:

            response = await client.post(f"{OLLAMA_BASE_URL}/api/chat",json=payload)

            response.raise_for_status()
            raw = response.json().get("message",{}).get("content","")
            end_time = time.perf_counter()
            inference_time = end_time - start_time

            print("\n====================================")
            print(f"Tempo de inferência: {inference_time:.2f} segundos")
            print("====================================\n")
            
            parsed = json.loads(raw)
            return TemporalVmlModel.model_validate(parsed)

    except Exception as e:

        logger.warning("analyze_temporal_sequence falhou: %s",e)
        return None

async def  temporal_inference(frame_queue):
    motion_detector = MovimentDetector(threshold=10.0)
    frame_buffer = deque (maxlen=8)
    last_inference_time = 0
    INFERENCE_INTERVAL = 5.0
    
    while True:
        frame_b64 = await frame_queue.get()
        frame_buffer.append(frame_b64)
        print(f"Número de frames no buffer:{len(frame_buffer)}")
       
        if len (frame_buffer) < 8:
            continue
       
        current_time = asyncio.get_event_loop().time()

        if (current_time - last_inference_time < INFERENCE_INTERVAL):
            continue

        motion_detected, score = (motion_detector.detection_moviment(frame_buffer[0],frame_buffer[-1]))

        print(f"Motion score: {score:.2f}")

        if not motion_detected:

            print("Sem movimento relevante.")
            continue

        last_inference_time = current_time

        result = await analyze_temporal_sequence(frames_b64 = list(frame_buffer))
        if result:
            print("\n==============================")
            print("Descrição do que o VLM esta enxergando.")
            print("==============================\n")
            print(result.description)
            print("\n==============================\n")

async def main ():
    frame_queue = asyncio.Queue(maxsize=2)

    bridge = GStreamerBridge(
        ros_topic="/camera/image_raw",
        target_fps=2.0
    )

    bridge_task = asyncio.create_task(
        bridge.run(frame_queue)
    )

    consumer_task = asyncio.create_task(
        temporal_inference (frame_queue)
    )

    await asyncio.gather(bridge_task,consumer_task)

if __name__ == "__main__":
    asyncio.run(main())