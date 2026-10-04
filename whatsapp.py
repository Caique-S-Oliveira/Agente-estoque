import httpx
from config import WHATSAPP_TOKEN, PHONE_NUMBER_ID

# O WhatsApp recusa mensagens com mais de 4096 caracteres. Ficamos abaixo, com folga.
LIMITE_CARACTERES = 4000


def _partir_texto(texto: str, limite: int = LIMITE_CARACTERES) -> list:
    """
    Divide um texto longo (ex.: relatório com muitos itens) em partes que cabem no WhatsApp,
    sempre cortando em quebra de linha para não rasgar uma frase ao meio.
    """
    partes, atual = [], ""
    for linha in texto.split("\n"):
        while len(linha) > limite:                       # linha gigante: corta à força
            if atual:
                partes.append(atual)
                atual = ""
            partes.append(linha[:limite])
            linha = linha[limite:]
        if len(atual) + len(linha) + 1 > limite and atual:
            partes.append(atual)
            atual = linha
        else:
            atual = f"{atual}\n{linha}" if atual else linha
    if atual:
        partes.append(atual)
    return partes or [""]


def _enviar_uma(telefone: str, texto: str) -> bool:
    """Envia UMA mensagem de texto pelo WhatsApp. Devolve True se a Meta aceitou."""

    # Endereço da API da Meta. O PHONE_NUMBER_ID diz de qual número a mensagem sai.
    url = f"https://graph.facebook.com/v21.0/{PHONE_NUMBER_ID}/messages"

    # O "crachá" que prova que você tem permissão para enviar
    cabecalho = {"Authorization": f"Bearer {WHATSAPP_TOKEN}"}

    # O conteúdo da mensagem, no formato que a Meta exige
    corpo = {
        "messaging_product": "whatsapp",
        "to": telefone,
        "type": "text",
        "text": {"body": texto},
    }

    try:
        resposta = httpx.post(url, headers=cabecalho, json=corpo, timeout=15)
    except httpx.HTTPError as erro:          # sem internet, tempo esgotado etc.
        print("Erro ao enviar (conexão):", repr(erro))
        return False

    # Se a Meta recusar, mostra o motivo no terminal (ajuda a depurar)
    if resposta.status_code != 200:
        print("Erro ao enviar:", resposta.status_code, resposta.text)

    return resposta.status_code == 200


def enviar_mensagem(telefone: str, texto: str) -> bool:
    """Envia o texto pelo WhatsApp, dividindo em várias mensagens se for muito longo."""
    return all([_enviar_uma(telefone, parte) for parte in _partir_texto(texto)])
