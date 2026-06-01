import requests
import sys

def send_command():
    if len(sys.argv) < 2:
        print("ERRO: Você esqueceu de passar o ID da sessão!")
        print("Uso: python3 test_pipeline_3.py <SESSION_ID>")
        return

    session_id = sys.argv[1]
    url = f"http://localhost:8000/session/{session_id}/instruction"

    print("Digite a tarefa para o robô:")
    task = input("> ").strip()

    if not task:
        print("ERRO: Tarefa vazia. Abortando.")
        return

    payload = {"text": task}

    print(f"\nEnviando comando para a sessão: {session_id}")
    print(f"Tarefa: '{task}'")

    try:
        response = requests.post(url, json=payload)
        if response.status_code == 200:
            print("Sucesso! Resposta do servidor:", response.json())
            print("Olhe para a tela do HUD, ele deve mostrar 'PLANEJANDO...' agora!")
        else:
            print(f"Erro do servidor (Status {response.status_code}):", response.text)
    except requests.exceptions.ConnectionError:
        print("Erro de conexão: O servidor main.py está rodando na porta 8000?")

if __name__ == "__main__":
    send_command()