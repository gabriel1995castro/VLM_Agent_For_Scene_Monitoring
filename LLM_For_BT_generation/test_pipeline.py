import asyncio

from src.utils.generate_plan import generate_bt_llm

async def test():
      result = await generate_bt_llm("""Preciso encontrar os notebook na mesa, mas ele deve ser pego com cuidado, logo preciso remover todos os objetos que interfiram nesse processo.
""")
      print(f"Plano gerado: {result.agent_id}")
      print(f"Root node: {result.root.type}")


if __name__ == "__main__":
      
      asyncio.run(test())
