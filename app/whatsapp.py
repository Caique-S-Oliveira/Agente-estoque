import re

import httpx
from app.config import WHATSAPP_TOKEN, PHONE_NUMBER_ID

# O WhatsApp recusa mensagens com mais de 4096 caracteres. Ficamos abaixo, com folga.
LIMITE_CARACTERES = 4000


def _mascarar_telefones(texto: str) -> str:
    """Esconde números de telefone (10+ dígitos) em textos de log, deixando só os 4 últimos."""
    return re.sub(r"\d{6,}(\d{4})", lambda m: "****" + m.group(1), texto)


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


def _postar(corpo: dict):
    """
    Envia UM pedido à API da Meta. Devolve (deu_certo, codigo_do_erro).
    O PHONE_NUMBER_ID diz de qual número a mensagem sai e o token é o "crachá" de permissão.
    O código do erro (ex.: 131047 = passou de 24 h sem o cliente escrever) deixa o chamador decidir
    o que fazer; é None quando deu certo ou quando nem chegamos a falar com a Meta.
    """
    url = f"https://graph.facebook.com/v21.0/{PHONE_NUMBER_ID}/messages"
    cabecalho = {"Authorization": f"Bearer {WHATSAPP_TOKEN}"}

    try:
        resposta = httpx.post(url, headers=cabecalho, json=corpo, timeout=15)
    except httpx.HTTPError as erro:          # sem internet, tempo esgotado etc.
        print("Erro ao enviar (conexão):", repr(erro))
        return False, None

    if resposta.status_code == 200:
        return True, None

    # Se a Meta recusar, mostra o motivo no terminal (ajuda a depurar)
    print("Erro ao enviar:", resposta.status_code, _mascarar_telefones(resposta.text))
    try:
        codigo = resposta.json().get("error", {}).get("code")
    except ValueError:
        codigo = None
    return False, codigo


def enviar_texto_detalhado(telefone: str, texto: str):
    """Envia UM texto e devolve (deu_certo, codigo_do_erro). Usada pelo resumo da manhã."""
    return _postar({
        "messaging_product": "whatsapp",
        "to": telefone,
        "type": "text",
        "text": {"body": texto},
    })


def enviar_template(telefone: str, nome_template: str, parametros: list, idioma: str = "pt_BR"):
    """
    Envia um MODELO de mensagem aprovado pela Meta (necessário para falar com alguém que não
    escreveu nas últimas 24 h). `parametros` preenche, na ordem, as variáveis {{1}}, {{2}}... do
    modelo. Devolve (deu_certo, codigo_do_erro).
    """
    corpo = {
        "messaging_product": "whatsapp",
        "to": telefone,
        "type": "template",
        "template": {"name": nome_template, "language": {"code": idioma}},
    }
    if parametros:
        corpo["template"]["components"] = [{
            "type": "body",
            "parameters": [{"type": "text", "text": str(p)} for p in parametros],
        }]
    return _postar(corpo)


def _enviar_uma(telefone: str, texto: str) -> bool:
    """Envia UMA mensagem de texto pelo WhatsApp. Devolve True se a Meta aceitou."""
    return enviar_texto_detalhado(telefone, texto)[0]


def enviar_mensagem(telefone: str, texto: str) -> bool:
    """Envia o texto pelo WhatsApp, dividindo em várias mensagens se for muito longo."""
    return all([_enviar_uma(telefone, parte) for parte in _partir_texto(texto)])
