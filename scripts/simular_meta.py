import httpx

# Troque pelo seu telefone cadastrado, SEM o 9 extra, para testar a normalização
# (5511949188851 vira 551149188851)
TELEFONE_SEM_9 = "551149188851"

pacote = {
    "entry": [{
        "changes": [{
            "value": {
                "messages": [{
                    "from": TELEFONE_SEM_9,
                    "type": "text",
                    "text": {"body": "Entrou 5 válvulas X"},
                }]
            }
        }]
    }]
}

resposta = httpx.post("http://127.0.0.1:8000/webhook", json=pacote)
print("Status:", resposta.status_code)
print("Resposta:", resposta.json())