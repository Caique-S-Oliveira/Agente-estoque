from agente import processar_mensagem

# Troque pelo seu telefone cadastrado (com o 9)
MEU_TELEFONE = "5511949188851"

print("Chat local do agente. Digite 'sair' para encerrar.\n")

while True:
    texto = input("Você: ")
    if texto.strip().lower() == "sair":
        break
    print("\nAgente:", processar_mensagem(MEU_TELEFONE, texto), "\n")