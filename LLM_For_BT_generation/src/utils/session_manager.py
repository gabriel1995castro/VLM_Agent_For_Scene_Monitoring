from __future__ import annotations

import asyncio
import logging
import uuid
import yaml
import httpx

from fastapi import HTTPException, WebSocket
from pathlib import Path
from typing import Any
from collections import deque
from src.utils.image_pre_processing import MovimentDetector

from src.utils.tree_planner import TaskTree, TreeConfig, AgentNode
from src.utils.tree_generation import generate_tasktree_from_bt_proposal
from src.utils.tree_ops import tree_to_recipe_steps, expand_node
from src.utils.generate_plan import generate_bt_llm
from src.agents.inference_agent import analyze_temporal_sequence

logger = logging.getLogger("uvicorn.error")

PHASE_IDLE = "IDLE"
PHASE_TREE_PLANNING = "TREE_PLANNING"
PHASE_GUIDING = "GUIDING"
PHASE_COMPLETED = "COMPLETED"
PHASE_ERROR = "ERROR"

DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434"
DEFAULT_OLLAMA_VLM_MODEL = "qwen2.5vl"
DEFAULT_OLLAMA_LLM_MODEL = "qwen2.5:14b"


class SessionEvent:
    def __init__(self, kind: str, payload: dict[str, Any] | None = None):
        self.kind = kind
        self.payload = payload or {}


class ControlSession:
    """Estado isolado de cada sessão do andador inteligente."""

    def __init__(self, session_id: str, websocket: WebSocket):
        self.session_id = session_id
        self.websocket = websocket
        self.phase = PHASE_IDLE
        self.ws_connected: bool = True

        # Árvore de comportamento atual
        self.current_tree: TaskTree | None = None
        self.tree_steps: list[dict[str, Any]] = []
        self.current_tree_node_index: int = 0

        # Fila de eventos — AgentNode consome daqui para confirmar ações via VLM
        self.queue: asyncio.Queue[SessionEvent] = asyncio.Queue()

        # Prompt ativo enviado ao VLM para verificar o passo atual
        self.active_prompt_text: str | None = None
        self.active_node: AgentNode | None = None

        # Controle do loop de visão
        self.vision_generation: int = 0
        self.vision_loop_task: asyncio.Task | None = None

        # Speech
        self.speech_epoch: int = 0
        self.current_speech_text: str | None = None

        #variaveis para o agente que avalia se uma tarefa foi concluida.
        self.buffer_size = 4
        self.frame_buffer: deque[str] = deque(maxlen=self.buffer_size)
        self.motion_detector = MovimentDetector(threshold=12.0, blur_threshold=80.0)
        self.last_inference_time: float = 0.0
        self.cooldown_seconds: float = 2.0


class MocktailSessionManager:
    """
    Gerenciador de sessões para o pipeline de Behavior Trees.
    Consome frames em tempo real da câmera ROS e alimenta os agentes de IA.
    """

    def __init__(
        self,
        *,
        ollama_base_url: str = DEFAULT_OLLAMA_BASE_URL,
        ollama_vlm_model: str = DEFAULT_OLLAMA_VLM_MODEL,
        ollama_llm_model: str = DEFAULT_OLLAMA_LLM_MODEL,
        skills_file_path: str = "skills/vwalker_skill.yaml",
    ) -> None:
        self._ollama_base_url = ollama_base_url
        self._ollama_vlm_model = ollama_vlm_model
        self._ollama_llm_model = ollama_llm_model
        self._ollama_http = httpx.AsyncClient(
            base_url=ollama_base_url, timeout=httpx.Timeout(60.0)
        )
        self._frame_queue: asyncio.Queue[str] = asyncio.Queue(maxsize=2)

        self._sessions: dict[str, ControlSession] = {}
        self._sessions_lock = asyncio.Lock()
        self.skills_context_string = self._load_skills_yaml(skills_file_path)

    #Carrega o arquivo yaml com as skill para geração da BT:
    def _load_skills_yaml(self, filepath: str) -> str:
       
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                robot_skill = yaml.safe_load(f)
            
            lines = []
            for category, skill_list in robot_skill.get("skills", {}).items():
                lines.append(f"**{category.upper()}**")
                for skill in skill_list:
                    lines.append(f"  - {skill['id']}: {skill['description']}")
                    
                    if skill.get("parameters"):
                        for param, desc in skill["parameters"].items():
                            lines.append(f"     param: {param} ({desc})")
                            
                    if skill.get("preconditions"):
                        for prec in skill["preconditions"]:
                            lines.append(f"     precondition: {prec}")
                            
                    if skill.get("failure_modes"):
                        for fm in skill["failure_modes"]:
                            lines.append(f"     failure: {fm}")
            
            logger.info("Skills YAML carregado com sucesso na memória.")
            return "\n".join(lines)
            
        except Exception as e:
            logger.error("Falha ao carregar arquivo de skills %s: %s", filepath, e)
            return "ERROR: Skills registry not loaded."

    # ------------------------------------------------------------------
    # Gerenciamento de sessões
    # ------------------------------------------------------------------
    async def _send_tree_to_hud(self, session: ControlSession) -> None:
        """Serializa a estrutura atual da árvore e envia via WebSocket para o HUD."""
        if session.current_tree and session.current_tree.root:
                       # Converte a árvore de objetos para um dicionário/JSON aninhado
            tree_dict = session.current_tree.root.to_dict()
            
            try:
                await session.websocket.send_json({
                    "type": "hud.tree",
                    "tree": tree_dict
                })
            except Exception as e:
                logger.warning(f"Falha ao enviar hud.tree: {e}")
    
    async def mark_disconnected(self, session_id: str) -> None:
        """Marca websocket como desconectado sem destruir a sessão."""
        async with self._sessions_lock:
            session = self._sessions.get(session_id)
        if session:
            session.ws_connected = False
            logger.info("session=%s HUD desconectou, pipeline continua", session_id)       
    
    async def create_control_session(
        self, session_id: str, websocket: WebSocket
    ) -> ControlSession:
        async with self._sessions_lock:
            session = ControlSession(session_id, websocket)
            self._sessions[session_id] = session
            logger.info("session=%s criada", session_id)
        return session

    async def destroy_session(self, session_id: str, reason: str = "") -> None:
        async with self._sessions_lock:
            session = self._sessions.pop(session_id, None)
        if session is not None:
            await self._stop_vision_loop(session)
            logger.info("session=%s finalizada. motivo=%s", session_id, reason)

    async def _require_session(self, session_id: str) -> ControlSession:
        async with self._sessions_lock:
            session = self._sessions.get(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="Sessão não encontrada")
        return session

    # ------------------------------------------------------------------
    # Entrada de mensagens de controle
    # ------------------------------------------------------------------

    async def handle_control_message(
        self, session_id: str, payload: dict[str, Any]
    ) -> None:
        session = await self._require_session(session_id)
        msg_type = payload.get("type")

        if msg_type == "user.instruction":
            text = payload.get("text", "").strip()
            if text:
                asyncio.create_task(
                    self._handle_user_dynamic_request(session, text),
                    name=f"bt-pipeline-{session_id}",
                )

    # ------------------------------------------------------------------
    # Pipeline principal: instrução → árvore → execução
    # ------------------------------------------------------------------

    async def _handle_user_dynamic_request(
        self, session: ControlSession, task_text: str
    ) -> None:
        """
        Fluxo completo:
          1. Captura frame atual da câmera
          2. Gera BTProposal via pipeline de agentes
          3. Converte para TaskTree executável
          4. Inicia loop de visão que confirma cada passo via VLM
          5. Executa a árvore tick a tick
        """
        try:
            session.phase = PHASE_TREE_PLANNING
            await self._speak_line(session, f"Planeando os passos para: {task_text}")

            # 1. Limpa frames velhos e captura o frame atual
            while not self._frame_queue.empty():
                try:
                    self._frame_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break

            logger.info("session=%s aguardando frame da câmera...", session.session_id)
            try:
                frame_b64 = await asyncio.wait_for(
                    self._frame_queue.get(), timeout=5.0
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "session=%s timeout ao capturar frame — usando fallback",
                    session.session_id,
                )
                frame_b64 = None

            # 2. Gera BTProposal via VLM + agentes paralelos + juiz
            bt_proposal = await generate_bt_llm(
                task_desc=task_text, frame_b64=frame_b64, skills_context=self.skills_context_string
            )

            # 3. Converte BTProposal → TaskTree executável
            cfg = TreeConfig(
                max_depth=10,
                max_attempts_before_expand=5,
                max_expansion_depth=3,
                max_expansions_per_node=2,
            )
            tree = await generate_tasktree_from_bt_proposal(
                bt_proposal=bt_proposal,
                cfg=cfg,
                session=session,
                manager=self,
            )

            # 4. Registra árvore na sessão
            session.current_tree = tree
            session.tree_steps = tree_to_recipe_steps(tree)
            session.current_tree_node_index = 0
            session.phase = PHASE_GUIDING

            # Envia estrutura da árvore para o frontend
            # await session.websocket.send_json({
            #     "type": "tree.generated",
            #     "steps": [s["description"] for s in session.tree_steps],
            # })
            try:
                if session.websocket.client_state.value == 1:
                    await session.websocket.send_json({
                        "type": "tree.updated",
                        "steps": [s["description"] for s in session.tree_steps],
                    })

                else:
                    logger.warning(f"HUD desconectado para sessão {session.session_id}, ignorando envio.")    
            
            except RuntimeError as e :
                logger.warning(f"Erro ao enviar para HUD: {e}")

            await self._send_tree_to_hud(session)
            await self._speak_line(
                session, "Plano criado. Iniciando execução dos passos."
            )

            # 5. Inicia loop de visão e executa a árvore
            await self._start_vision_loop(session)
            await self._run_tree(session, tree)

        except Exception:
            session.phase = PHASE_ERROR
            logger.exception(
                "session=%s erro no pipeline", session.session_id
            )
            await self._speak_line(
                session, "Falha ao gerar ou executar a árvore de comportamento."
            )

    # ------------------------------------------------------------------
    # Execução da árvore
    # ------------------------------------------------------------------

    async def _run_tree(self, session: ControlSession, tree: TaskTree) -> None:
        """
        Executa a TaskTree consumindo dinamicamente a lista de passos atualizada.
        """
        # Usa um loop while baseado no índice dinâmico
        
        while session.current_tree_node_index < len(session.tree_steps):
            step = session.tree_steps[session.current_tree_node_index]
            node: AgentNode = step["node_ref"]

            enter_speech = await self._generate_speech(session, node, "enter")
            await self._speak_line(session, enter_speech)
            await self._publish_hud_state(session)

            session.active_prompt_text = self._build_vlm_prompt(node)
            session.active_node = node
            confirmed = await self._wait_for_node_confirmation(session, node, tree)

            if not confirmed:
                session.phase = PHASE_ERROR
                await self._speak_line(
                    session,
                    f"Não consegui confirmar: {node.content}. Interrompendo.",
                )
                return

            if node.expand_count == 0:
                complete_speech = await self._generate_speech(session, node, "complete")
                await self._speak_line(session, complete_speech)
                session.current_tree_node_index += 1
            else:
                node.attempts = 0

        # Todos os passos confirmados
        session.phase = PHASE_COMPLETED
        session.active_prompt_text = None
        session.active_node = None
        await self.destroy_session(session.session_id, reason="pipeline completed")
        await self._stop_vision_loop(session)
        await self._publish_hud_state(session)
        await self._speak_line(session, "Tarefa concluída com sucesso.")
        
    async def _wait_for_node_confirmation(self, session: ControlSession, node: AgentNode, tree: TaskTree) -> bool:
            """
            Aguarda o VLM confirmar que o passo foi executado, usando tempos
            dinâmicos baseados no tipo de tarefa (skill_id).
            """
            skill = getattr(node, "skill_id", None)
            params = getattr(node, "parameters", {})

            # normaliza enum → string (SkillId.MOVE_TO → "move_to")
            if hasattr(skill, "value"):
                skill = skill.value

            # Skills de navegação: auto-confirmadas após delay fixo
            if skill in ("move_to", "stop", "replan_route"):
                await asyncio.sleep(4.0)  
                logger.info("session=%s skill '%s' auto-confirmada", session.session_id, skill)
                return True

            elif skill in ("detect_object", "check_state", "scan_environment", "reposition_for_view", "check_person_presence"):
                head_start = 1.0
                cooldown = 2.0
                max_attempts = 5

            elif skill in ("wait_for_human_action", "verify_pick_up", "verify_place", "verify_open", "verify_close", "verify_push_pull"):
                head_start = 5.0
                timeout_sec = float(params.get("timeout_seconds", 30.0))
                cooldown = 3.0
                max_attempts = max(3, int(timeout_sec / cooldown))

            else:
                head_start = 2.0
                cooldown = 3.0
                max_attempts = getattr(tree.cfg, "max_attempts_before_expand", 5)

            logger.info(
                "session=%s Iniciando skill '%s' | head_start: %ss | cooldown: %ss | max_attempts: %s",
                session.session_id, skill, head_start, cooldown, max_attempts
            )

            session.active_prompt_text = None 
            await asyncio.sleep(head_start)

            # Limpa eventos passados acumulados
            while not session.queue.empty():
                try:
                    session.queue.get_nowait()
                except asyncio.QueueEmpty:
                    break

            session.active_prompt_text = self._build_vlm_prompt(node)

            for attempt in range(max_attempts):
                try:
                    event = await asyncio.wait_for(session.queue.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    logger.warning("session=%s timeout aguardando VLM", session.session_id)
                    node.attempts += 1
                else:
                    if event.kind == "overshoot.result":
                        result = event.payload.get("result", {})
                        confirmed = bool(result.get("flag", False))
                        
                        logger.info("session=%s VLM '%s' → %s (tentativa %d/%d)", 
                                    session.session_id, node.content, confirmed, node.attempts + 1, max_attempts)
                        
                        if confirmed:
                            node.attempts = 0
                            return True
                            
                        node.attempts += 1
                        if node.attempts % 4 == 0:
                            fail_speech = await self._generate_speech(session, node, "fail")
                            await self._speak_line(session, fail_speech)

                if node.attempts >= max_attempts:
                    break
                
                session.active_prompt_text = None
                current_cooldown = cooldown + (node.attempts * 0.5) 
                await asyncio.sleep(current_cooldown)
                
                while not session.queue.empty():
                    try:
                        session.queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                        
                session.active_prompt_text = self._build_vlm_prompt(node)

            logger.info("session=%s expandindo nó '%s' (tentativas esgotadas)", session.session_id, node.content)
            scene_description = await self.run_vlm_scan(session, "Descreva o ambiente atual em detalhes.")

            existing_tasks = [step["description"] for step in session.tree_steps]
            if skill in ("detect_object", "scan_environment"):
                if node.expand_count >= 1:  
                    logger.info("session=%s objeto não encontrado após expansão, reportando ao usuário",
                                session.session_id)
                    await self._speak_line(
                        session,
                        "Não consigo encontrar o objeto na cena atual. "
                        "Tente me guiar até o local onde ele se encontra."
                    )
                    return False

            expanded = await expand_node(
                tree=tree,
                node=node,
                scene_description=scene_description,
                person_state="",
                existing_tasks=existing_tasks,
                session=session,
                manager=self,
            )
            
            if expanded:
                expand_speech = await self._generate_speech(session, node, "expand")
                await self._speak_line(session, expand_speech)
                
                # Atualiza os passos na interface
                try:
                    session.tree_steps = tree_to_recipe_steps(tree)
                    await session.websocket.send_json({
                        "type": "tree.updated",
                        "steps": [s["description"] for s in session.tree_steps],
                    })
                    await self._send_tree_to_hud(session)
               
                except Exception as e:
                    logger.warning(
                        "session=%s HUD desconectado durante expansão, continuando execução: %s",
                        session.session_id, e
                    )
                
                return True     
            return False
    
    def _build_vlm_prompt(self, node: AgentNode) -> str:
        """Monta o prompt que o VLM vai usar para confirmar se o passo foi concluído."""
        skill = getattr(node, "skill_id", None)
        task = node.content

        if hasattr(skill, "value"):
            skill = skill.value

        if skill in ("move_to", "replan_route", "stop", "scan_environment"):
            criteria = ("scene shows physical movement or repositioning.")
        
        elif skill in ("detect_object", "reposition_for_view"):
            clean_task = task.replace("Detectar a ", "").replace("Detectar o ", "").replace("Detectar ", "")
            criteria = f"Ignore human actions. Answer TRUE ONLY if the physical object '{clean_task}' is clearly visible in the current scene."
        
        elif skill in ("wait_for_human_action", "verify_pick_up", "verify_place",
                    "verify_open", "verify_close", "verify_push_pull"):
            criteria = ("human has visibly completed the interaction on the target object")
        
        else:
            criteria = "clear visible evidence the action was completed"

        return (f"Verify if task '{task}' was completed. Criteria: {criteria}")


    async def _generate_speech(self,session: "ControlSession",node: "AgentNode",moment: str,  ) -> str:
        """
        Gera uma fala contextual via LLM baseada no passo atual da árvore.
        """

        skill_id = getattr(node, "skill_id", None)
        skill_info = f" (skill: {skill_id})" if skill_id else ""
        
        prompts = {
            "enter": (
                "You are an assistive robot. Translate the technical task below into a short, friendly, imperative command in Brazilian Portuguese for the user to perform.\n"
                f"TASK: {node.content}{skill_info}\n"
                "CRITICAL: Output ONLY the Portuguese phrase. No explanations, no greetings, no internal thoughts."
            ),
            "complete": (
                "You are an assistive robot. The user completed a task. Generate a 2-word praise in Brazilian Portuguese.\n"
                f"TASK: {node.content}\n"
                "CRITICAL: Output ONLY the praise."
            ),
            "fail": (
                "You are an assistive robot. The user failed the task. Ask them to try again shortly in Brazilian Portuguese.\n"
                f"PENDING TASK: {node.content}\n"
                "CRITICAL: Output ONLY the request."
            ),
            "expand": (
                "You are an assistive robot. Inform the user in Brazilian Portuguese that the task will be broken down into easier steps.\n"
                "CRITICAL: Output ONLY the Portuguese sentence."
            ),
        }
        
        prompt = prompts.get(moment, prompts["enter"])

        payload = {
            "model": self._ollama_llm_model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "options": {"temperature": 0.1},
        }
        try:
            response = await self._ollama_http.post(
                "/api/chat", json=payload, timeout=httpx.Timeout(15.0)
            )
            if not response.is_success:
                return node.content
            data = response.json()
            speech = data.get("message", {}).get("content", "").strip()
            return speech if speech else node.content
        except Exception as exc:
            logger.warning(
                "session=%s speech generation failed: %s", session.session_id, exc
            )
            return node.content

    async def _start_vision_loop(self, session: ControlSession) -> None:
        """Inicia o loop que consome frames e injeta overshoot.result na queue."""
        await self._stop_vision_loop(session)
        session.vision_generation += 1
        generation = session.vision_generation
        session.vision_loop_task = asyncio.create_task(
            self._run_vision_loop(session, generation),
            name=f"vision-loop-{session.session_id}-{generation}",
        )
        logger.info(
            "session=%s vision_loop iniciado generation=%d",
            session.session_id,
            generation,
        )

    async def _stop_vision_loop(self, session: ControlSession) -> None:
        task = session.vision_loop_task
        session.vision_loop_task = None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    async def _run_vision_loop(self, session: ControlSession, generation: int) -> None:
        """
        Consome frames da _frame_queue, chama o VLM com o prompt ativo
        e injeta overshoot.result na session.queue.
        """
        logger.info("session=%s vision_loop rodando generation=%d",session.session_id, generation)
        
        try:
            while True:
                if session.vision_generation != generation:
                    return

                frame_b64 = await self._frame_queue.get()
                session.frame_buffer.append(frame_b64)
                
                if len(session.frame_buffer) < session.buffer_size:
                    continue

                target_condition = session.active_prompt_text
                
                if not target_condition:
                    continue
                
                now = asyncio.get_running_loop().time()
                
                if (now - session.last_inference_time) < session.cooldown_seconds:
                    continue

                report = session.motion_detector.analyze_sequence(list(session.frame_buffer))
                
                if report.noisy_frames / (session.buffer_size - 1) > 0.5:
                    continue 

                if not report.motion_detected:
                    continue 

                logger.info("Movimento detectado na sessão %s! Acionando VLM Temporal...", session.session_id)
                session.last_inference_time = now

                verification = await analyze_temporal_sequence(frames_b64=list(session.frame_buffer),target_condition=target_condition,
                                                               client=self._ollama_http, model_name=self._ollama_vlm_model,base_url=self._ollama_base_url)
                
                if verification is None:
                    continue
                
                logger.info(
                    "\n╔══════════════════════════════════╗"
                    "\n║  Condição Atendida? : %-11s║"
                    "\n║  Confiança          : %-11s║"
                    "\n╚══════════════════════════════════╝"
                    "\n%s\n",
                    str(verification.condition_met).upper(),
                    verification.confidence.upper(),
                    verification.description,
                )

                if session.current_tree:
                    session.current_tree.vision_result = {"flag": verification.condition_met}
                
                if session.vision_generation != generation:
                    return

                await session.queue.put(SessionEvent(
                    kind="overshoot.result",
                    payload={"generation": generation, "result": {"flag": verification.condition_met}},
                ))
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("session=%s vision_loop crashed generation=%d",
                            session.session_id, generation)

    # ------------------------------------------------------------------
    # HUD e comunicação com o frontend
    # ------------------------------------------------------------------

    async def _publish_hud_state(self, session: ControlSession) -> None:
        
        logger.info("session=%s publicando hud.state idx=%d", session.session_id, session.current_tree_node_index)
        
        steps = session.tree_steps
        idx = session.current_tree_node_index

        tasks_payload = []
        active_task_id = None

        for i, step in enumerate(steps):
            node = step.get("node_ref")
            task_id = f"step_{i}"
            if i == idx:
                active_task_id = task_id
            tasks_payload.append({
                "id": task_id,
                "text": node.content if node else step.get("description", ""),
                "completed": i < idx,
            })

        try:
            await session.websocket.send_json({
                "type": "hud.state",
                "phase": session.phase,
                "tasks": tasks_payload,
                "active_task_id": active_task_id,
                "speech_epoch": session.speech_epoch,
            })
        except Exception:
            pass

    async def _speak_line(self, session: ControlSession, text: str) -> None:
        line = text.strip()
        if not line:
            return
        session.speech_epoch += 1
        session.current_speech_text = line
        logger.info("session=%s fala: %s", session.session_id, line)
        if not session.ws_connected:
            return 
        try:
            await session.websocket.send_json({...})
        except Exception:
            session.ws_connected = False

    async def run_vlm_scan(self, session: ControlSession, prompt: str) -> str:
        """Scan pontual via VLM — usado por expand_node para descrever a cena."""
        while not self._frame_queue.empty():
            try:
                self._frame_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

        try:
            frame_b64 = await asyncio.wait_for(
                self._frame_queue.get(), timeout=5.0
            )
        except asyncio.TimeoutError:
            return "Não foi possível obter imagem da câmera."

        try:
            payload = {
                "model": self._ollama_vlm_model,
                "prompt": prompt,
                "options": {"temperature": 0.1},
                "stream": False,
                "images": [frame_b64],
            }
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(
                    f"{self._ollama_base_url}/api/generate", json=payload
                )
                resp.raise_for_status()
                return resp.json().get("response", "")
        except Exception as e:
            logger.error(
                "session=%s erro no VLM scan: %s", session.session_id, e
            )
            return "Erro técnico ao ler o ambiente."