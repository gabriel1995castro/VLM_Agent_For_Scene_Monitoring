import json
import re
from json_repair import repair_json
from src.schemas.plan_agents import BTProposal
import logging

logger = logging.getLogger(__name__)

def parse_bt_proposal(raw: str) -> BTProposal:
    # Remove markdown code fences
    clean = re.sub(r"```(?:json)?", "", raw).replace("```", "").strip()

    start = clean.find("{")
    end = clean.rfind("}") + 1

    if start == -1 or end == 0:
        raise ValueError(f"Nenhum JSON encontrado no output do planner. Raw:\n{raw[:500]}")

    clean = clean[start:end]

    # Tenta parse direto primeiro — repair_json mascara erros reais
    try:
        data = json.loads(clean)
    except json.JSONDecodeError as e:
        logger.warning(f"JSON inválido, tentando repair_json. Erro original: {e}")
        repaired = repair_json(clean)
        logger.warning(f"JSON após repair:\n{repaired[:500]}")
        data = json.loads(repaired)

    # Valida e loga o agent_id para detectar o problema "robot1" 
    agent_id = data.get("agent_id", "MISSING")
    if agent_id not in ("plan_a", "plan_b"):
        logger.error(
            f"agent_id inválido recebido do planner: '{agent_id}'. "
            f"O LLM deve retornar exatamente 'plan_a' ou 'plan_b'. "
            f"Verifique o prompt."
        )

    try:
        return BTProposal.model_validate(data)
    except Exception as e:
        logger.error(f"BTProposal.model_validate falhou.\nDados recebidos:\n{json.dumps(data, indent=2, ensure_ascii=False)[:1000]}")
        raise