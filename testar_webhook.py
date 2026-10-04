"""
Testa a PORTA DE ENTRADA (main.py) sem gastar nada: não chama a IA, não envia WhatsApp de verdade
e não mexe no banco. Ele "finge" essas partes e confere só a lógica do webhook:
assinatura da Meta, mensagem repetida, áudio, vários envios juntos, /teste desligado.

Rode:  python testar_webhook.py
Precisa do META_APP_SECRET no .env (o mesmo que o servidor usa).
"""
import hashlib
import hmac
import json
import sys
from unittest.mock import patch

from fastapi.testclient import TestClient

import main
from config import META_APP_SECRET

cliente = TestClient(main.app)
falhas = []
TEL = "5511949188851"


def assinar(corpo: bytes) -> str:
    """Gera o mesmo carimbo que a Meta geraria."""
    return "sha256=" + hmac.new(META_APP_SECRET.encode(), corpo, hashlib.sha256).hexdigest()


def envio(*mensagens):
    """Monta o corpo no formato que a Meta manda."""
    return json.dumps({"entry": [{"changes": [{"value": {"messages": list(mensagens)}}]}]}).encode()


def texto(id_, conteudo):
    return {"id": id_, "from": TEL, "type": "text", "text": {"body": conteudo}}


def checar(ok, descricao):
    print(("✅ " if ok else "❌ ") + descricao)
    if not ok:
        falhas.append(descricao)


def postar(corpo, assinatura="auto"):
    cab = {"Content-Type": "application/json"}
    if assinatura == "auto":
        cab["X-Hub-Signature-256"] = assinar(corpo)
    elif assinatura:
        cab["X-Hub-Signature-256"] = assinatura
    return cliente.post("/webhook", content=corpo, headers=cab)


vistos = set()


def marcar_falso(id_, empresa_id=None):
    """Finge o banco: devolve True na 1ª vez de cada ID e False nas seguintes."""
    if id_ in vistos:
        return False
    vistos.add(id_)
    return True


with patch.object(main, "processar_mensagem", side_effect=lambda t, x: "resp:" + x) as cerebro, \
     patch.object(main, "enviar_mensagem") as zap, \
     patch.object(main.database, "marcar_mensagem_processada", side_effect=marcar_falso), \
     patch.object(main.database, "limpar_mensagens_antigas"):

    def zerar():
        cerebro.reset_mock()
        zap.reset_mock()

    r = postar(envio(texto("T1", "saiu 3 fitas")))
    checar(r.status_code == 200 and cerebro.call_count == 1 and zap.call_count == 1, "assinatura certa: processa e responde")

    zerar(); r = postar(envio(texto("T1", "saiu 3 fitas")))
    checar(r.status_code == 200 and cerebro.call_count == 0 and zap.call_count == 0, "mesma mensagem reenviada (mesmo ID): ignorada")

    zerar(); r = postar(envio(texto("T2", "x")), assinatura="sha256=" + "0" * 64)
    checar(r.status_code == 403 and cerebro.call_count == 0, "assinatura falsa: recusada (403)")

    zerar(); r = postar(envio(texto("T3", "x")), assinatura=None)
    checar(r.status_code == 403 and cerebro.call_count == 0, "sem assinatura: recusada (403)")

    zerar(); r = postar(envio(texto("T4", "a"), texto("T5", "b")))
    checar(cerebro.call_count == 2 and zap.call_count == 2, "duas mensagens no mesmo envio: as duas respondidas")

    zerar(); r = postar(envio({"id": "T6", "from": TEL, "type": "audio"}))
    checar(cerebro.call_count == 0 and zap.call_count == 1, "áudio: resposta educada, sem gastar IA")

    zerar(); r = cliente.post("/teste", json={"telefone": TEL, "texto": "oi"})
    checar(r.status_code == 404 or main.ENDPOINT_TESTE, "/teste desligado no servidor (404)")

r = cliente.get("/webhook", params={"hub.mode": "subscribe", "hub.verify_token": "errado", "hub.challenge": "1"})
checar(r.status_code == 403, "verificação com token errado: recusada")
r = cliente.get("/")
checar(r.json() == {"status": "online"}, "página inicial online")

print("\n" + ("🎉 WEBHOOK OK" if not falhas else f"🚨 {len(falhas)} falha(s): " + "; ".join(falhas)))
sys.exit(1 if falhas else 0)
