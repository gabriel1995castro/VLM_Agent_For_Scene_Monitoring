from __future__ import annotations

import asyncio
import logging
import uuid
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import (
    FastAPI,
    HTTPException,
    Request,
    Response,
    WebSocket,
    WebSocketDisconnect,
)
from pydantic import BaseModel
from src.utils.audio_service import audio_manager
from src.utils.local_vision_bridge import GStreamerBridge
from src.utils.session_manager import (
    DEFAULT_OLLAMA_BASE_URL,
    DEFAULT_OLLAMA_VLM_MODEL,
    DEFAULT_OLLAMA_LLM_MODEL,
    MocktailSessionManager,
)

os.environ["PYTHONWARNINGS"] = "ignore"
os.environ["ALSA_LOG_LEVEL"] = "0"

logger = logging.getLogger("uvicorn.error")

class VisionSessionCreateRequest(BaseModel):
    offer_sdp: str


manager: MocktailSessionManager | None = None

@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    global manager

   
    ollama_base_url = os.getenv("OLLAMA_BASE_URL", DEFAULT_OLLAMA_BASE_URL).strip()
    ollama_vlm_model = os.getenv("OLLAMA_VLM_MODEL", DEFAULT_OLLAMA_VLM_MODEL).strip()
    ollama_llm_model = os.getenv("OLLAMA_LLM_MODEL", DEFAULT_OLLAMA_LLM_MODEL).strip()
    
    recipe_dir = Path(__file__).with_name("recipes")
    logger.info(f"DEBUG: Carregando diretório de contexto: {recipe_dir.absolute()}")

    ros_topic = os.getenv("ROS_CAMERA_TOPIC", "/camera/image_raw")
    mock_camera = os.getenv("MOCK_CAMERA", "false").lower() == "true"
    
 
    audio_manager.iniciar_audio()
    
    manager = MocktailSessionManager(
        ollama_base_url=ollama_base_url,
        ollama_vlm_model=ollama_vlm_model,
        ollama_llm_model=ollama_llm_model,
        
    )


    bridge = GStreamerBridge(
        ros_topic=ros_topic,
        target_fps=5.0,
        mock=mock_camera,
    )
    
   
    bridge_task = asyncio.create_task(
        bridge.run(manager._frame_queue),
        name="gstreamer-bridge",
    )
    

    try:
        yield
    finally:
    
        audio_manager.desligar_audio()
        bridge_task.cancel()       
        manager = None


app = FastAPI(lifespan=lifespan)


@app.websocket("/session/control")
async def session_control(websocket: WebSocket) -> None:
    current = require_manager()
    await websocket.accept()

    session_id = str(uuid.uuid4())
    await current.create_control_session(session_id, websocket)
    await websocket.send_json({"session_id": session_id})

    try:
        while True:
            payload = await websocket.receive_json()
            if not isinstance(payload, dict):
                await websocket.send_json({
                    "type": "hud.error",
                    "message": "Control message must be a JSON object.",
                })
                continue
            await current.handle_control_message(session_id, payload)

    except WebSocketDisconnect:
        pass
    finally:
        await current.mark_disconnected(session_id)

@app.post("/session/{session_id}/vision")
async def create_vision_session(
    session_id: str,
    payload: VisionSessionCreateRequest,
) -> dict[str, str]:
    current = require_manager()
    offer_sdp = payload.offer_sdp
    if not offer_sdp.strip():
        raise HTTPException(status_code=422, detail="offer_sdp must not be empty")

    answer_sdp = await current.create_vision_session(session_id, offer_sdp)
    return {"answer_sdp": answer_sdp}


@app.post("/session/{session_id}/realtime")
async def create_realtime_session(session_id: str, request: Request) -> Response:
    current = require_manager()
    offer_sdp = (await request.body()).decode()
    if not offer_sdp.strip():
        raise HTTPException(status_code=422, detail="offer SDP must not be empty")

    answer_sdp = await current.create_realtime_session(session_id, offer_sdp)
    return Response(content=answer_sdp, media_type="application/sdp")


def require_manager() -> MocktailSessionManager:
    if manager is None:
        raise RuntimeError("Session manager is not initialized")
    return manager

class InstructionPayload(BaseModel):
    text: str

@app.post("/session/{session_id}/instruction")
async def receive_external_instruction(session_id: str, payload: InstructionPayload):
    """Rota HTTP para injetar comandos na sessão ativa do HUD."""
    current = require_manager()
    
    try:
        session = await current._require_session(session_id)
    except HTTPException:
        return {"status": "error", "message": "Sessão não encontrada."}
    
    try:
        if session.websocket.client_state.value == 1: 
            
            await session.websocket.send_json({
                "type": "hud.speech",
                "text": f"Recebi o comando: '{payload.text}'. Gerando plano..."
            })
        
            await session.websocket.send_json({
                "type": "hud.state",
                "phase": "PLANEJANDO...",
                "tasks": [],
                "active_task_id": ""
            })
    except Exception as e:
        logger.warning(f"HUD não pôde receber o aviso inicial: {e}")

    asyncio.create_task(
        current._handle_user_dynamic_request(session, payload.text),
        name=f"bt-pipeline-external-{session_id}"
    )
    
    return {"status": "ok"}