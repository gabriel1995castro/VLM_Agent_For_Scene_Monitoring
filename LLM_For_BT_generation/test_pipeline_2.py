import sys
import os
from pathlib import Path
import asyncio
import logging
from src.utils.local_vision_bridge import GStreamerBridge
from src.utils.session_manager import MocktailSessionManager, ControlSession


project_root = Path(__file__).resolve().parent
src_path = project_root / "src"
utils_path = src_path / "utils"
agents_path = src_path / "agents"

sys.path.append(str(project_root))
sys.path.append(str(src_path))
sys.path.append(str(utils_path))
sys.path.append(str(agents_path))
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("test_hardware_camera")


class MockWebSocket:
    async def accept(self): pass
    async def send_json(self, data):
        logger.info(f"[WebSocket OUT] Enviado para o hardware: {data}")

async def main_test():
    logger.info("=== INICIANDO TESTE COM CÂMERA REAL DO ROS ===")

    
    
    ros_topic = os.getenv("ROS_CAMERA_TOPIC", "/camera/image_raw").strip()
    logger.info(f"Conectando ao tópico do ROS: '{ros_topic}'")

    # Inicializa o session manager adaptado
    manager = MocktailSessionManager(
        ollama_base_url="http://127.0.0.1:11434",
        ollama_vlm_model="qwen2.5vl",
        ollama_llm_model="qwen2.5:14b",
        recipe_dir=None
    )

    # Configura a ponte para capturar frames reais (mock=False) do ROS
    bridge = GStreamerBridge(ros_topic=ros_topic, target_fps=2.0, mock=False)
    
    # Inicia a thread do ROS2 em background injetando frames na fila
    bridge_task = asyncio.create_task(bridge.run(manager._frame_queue), name="gstreamer-bridge")
    logger.info("Nó do ROS ativado. Aguardando estabilização dos frames...")
    
    # Aguarda 2 segundos para dar tempo do rclpy coletar os buffers iniciais
    await asyncio.sleep(2.0)

    # Cria uma sessão simulada para o teste
    mock_ws = MockWebSocket()
    session_id = "hardware_test_session"
    session = await manager.create_control_session(session_id, mock_ws)

    # Comando de teste (coloque algo no campo de visão da câmera para avaliar a assertividade)
    user_instruction = "preciso de algo para tomar agua."
    logger.info(f"Instrução simulada: '{user_instruction}'")

    # Dispara o gatilho inteligente do andador
    logger.info("Chamando o pipeline de agentes... O VLM vai processar o frame da sua câmera.")
    await manager._handle_user_dynamic_request(session, user_instruction)

    # Validação do resultado da Árvore de Comportamento
    if getattr(session, "current_tree", None) is not None:
        logger.info(" SUCCESSO: Árvore gerada com base nos dados do ambiente físico!")
        print("\n=== ESTRUTURA DA ÁRVORE DE COMPORTAMENTO EXECUTÁVEL ===")
        print(session.current_tree.summary())
        print("========================================================\n")
    else:
        logger.error(" FALHA: O pipeline terminou sem instanciar uma árvore executável.")

    # Cancela o bridge da câmera de forma limpa antes de fechar o script
    bridge_task.cancel()
    with asyncio.suppress(asyncio.CancelledError):
        await bridge_task

if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main_test())