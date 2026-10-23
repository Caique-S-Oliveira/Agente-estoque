"""
Porta de entrada do sistema: recebe as mensagens da Meta (WhatsApp) e devolve as respostas.

MAPA DO ARQUIVO (de cima para baixo):
  1. assinatura_valida()   -> confere se a mensagem veio MESMO da Meta (anti-falsificação)
  2. normalizar_telefone() -> ajusta o 9 extra dos celulares brasileiros
  3. _tratar_mensagem()    -> o que fazer com UMA mensagem (anti-duplicidade, texto, áudio, resposta)
  4. _limpeza_diaria()     -> apaga IDs de mensagens antigas (1 vez por dia)
  5. Rotas: GET /, POST /teste (desligada por padrão), /cron/resumo (resumo da manhã, protegida por senha),
     GET /webhook (verificação), POST /webhook (mensagens)
"""
import hashlib
import hmac
import json
import os
import time

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request, Response
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from app import database
from app import resumo
from app.agente import processar_mensagem
from app.config import META_APP_SECRET, VERIFY_TOKEN

# Corpo máximo aceito no webhook (a Meta manda poucos KB). Evita ler payloads gigantes.
LIMITE_CORPO_BYTES = 1_000_000
from app.whatsapp import enviar_mensagem

# Sem o App Secret não dá para conferir a assinatura: melhor nem ligar o servidor do que ficar aberto.
if not META_APP_SECRET:
    raise RuntimeError("META_APP_SECRET está vazio. Coloque no .env e em config.py (veja as instruções).")

# O endereço /teste processa mensagens SEM conferir quem mandou. Só liga se você pedir no .env
# (ENDPOINT_TESTE=1). No servidor de verdade, deixe desligado.
ENDPOINT_TESTE = os.getenv("ENDPOINT_TESTE") == "1"

# Senha do chamador externo (cron-job.org) que dispara o resumo da manhã em /cron/resumo.
# Sem CRON_TOKEN definido (no .env ou no Render), a rota fica desligada (responde 404).
CRON_TOKEN = os.getenv("CRON_TOKEN", "")

# Sem /docs, /redoc e /openapi.json: é uma API só para a Meta e o cron, não precisa expor o mapa das rotas.
# Para ver a documentação no seu computador, rode com EXPOR_DOCS=1 no .env.
_docs = os.getenv("EXPOR_DOCS") == "1"
app = FastAPI(
    title="Agente de Estoque",
    docs_url="/docs" if _docs else None,
    redoc_url="/redoc" if _docs else None,
    openapi_url="/openapi.json" if _docs else None,
)


class MensagemEntrada(BaseModel):
    telefone: str
    texto: str


# ---------------------------------------------------------------- 1) assinatura da Meta
def assinatura_valida(corpo: bytes, cabecalho) -> bool:
    """
    A Meta assina cada envio: calcula um "carimbo" (HMAC-SHA256) do corpo da mensagem usando o
    App Secret e manda no cabeçalho X-Hub-Signature-256 ("sha256=...").
    Nós refazemos a conta com o NOSSO App Secret. Se o resultado for igual, só a Meta poderia
    ter enviado. Se for diferente (ou faltar o cabeçalho), é falsificação: recusamos.
    """
    if not cabecalho or not cabecalho.startswith("sha256="):
        return False
    esperado = hmac.new(META_APP_SECRET.encode(), corpo, hashlib.sha256).hexdigest()
    # compare_digest compara em tempo constante (evita descobrir o carimbo por tentativa e erro)
    return hmac.compare_digest(esperado, cabecalho[len("sha256="):])


# ---------------------------------------------------------------- 2) telefone
def normalizar_telefone(telefone: str) -> str:
    """
    Celulares brasileiros às vezes chegam da Meta sem o 9 extra
    (55 + DDD + 8 dígitos = 12 dígitos). Nosso banco guarda com o 9
    (13 dígitos), então adicionamos.
    """
    if telefone.startswith("55") and len(telefone) == 12:
        return telefone[:4] + "9" + telefone[4:]
    return telefone


def texto_de_botao(msg: dict):
    """
    Quando alguém toca num botão de resposta rápida de um MODELO de mensagem (ex.: "Ver resumo", do
    resumo da manhã), a Meta manda uma mensagem do tipo "button". Devolve o comando que esse botão
    representa ("/resumo") ou None se não for um botão que conhecemos.
    """
    botao = msg.get("button") or {}
    texto = f"{botao.get('text', '')} {botao.get('payload', '')}".lower()
    return "/resumo" if "resumo" in texto else None


# ---------------------------------------------------------------- 3) uma mensagem
def _tratar_mensagem(msg: dict):
    """
    Processa UMA mensagem recebida e envia a resposta. Passos:
      1. Anti-duplicidade: se a Meta reenviou a mesma mensagem (mesmo ID), ignora.
      2. Texto -> cérebro. Áudio/outros -> resposta educada.
      3. Qualquer erro vira uma resposta amigável (nunca fica sem resposta nem derruba o servidor).
    """
    id_msg = msg.get("id")
    telefone_original = msg.get("from", "")
    if not id_msg or not telefone_original:
        return

    try:
        if not database.marcar_mensagem_processada(id_msg):
            print("Mensagem repetida ignorada:", id_msg[-8:])
            return
    except Exception as erro:
        # Se a checagem falhar (banco fora do ar), seguimos: é melhor responder do que ficar mudo.
        print("Aviso: não consegui checar duplicidade:", repr(erro))

    try:
        if msg.get("type") == "text":
            telefone = normalizar_telefone(telefone_original)
            resposta = processar_mensagem(telefone, msg["text"]["body"])
        elif msg.get("type") == "button" and texto_de_botao(msg):
            resposta = processar_mensagem(normalizar_telefone(telefone_original), texto_de_botao(msg))
        elif msg.get("type") == "audio":
            resposta = "🎤 Ainda não entendo áudios. Por favor, escreva a mensagem."
        else:
            resposta = "Por enquanto só entendo mensagens de texto."
    except Exception as erro:
        print("Erro ao processar:", repr(erro))
        resposta = "❌ Tive um problema para processar sua mensagem. Tente de novo em instantes."

    enviar_mensagem(telefone_original, resposta)


# ---------------------------------------------------------------- 4) limpeza diária
_ultima_limpeza = 0.0


def _limpeza_diaria():
    """Uma vez por dia (na primeira mensagem do dia) apaga os IDs de mensagens com +7 dias."""
    global _ultima_limpeza
    if time.time() - _ultima_limpeza < 86400:
        return
    _ultima_limpeza = time.time()          # marca antes: se falhar, tenta de novo só amanhã
    try:
        database.limpar_mensagens_antigas(7)
    except Exception as erro:
        print("Aviso: limpeza de mensagens antigas falhou:", repr(erro))


# ---------------------------------------------------------------- 5) rotas
@app.get("/")
def raiz():
    return {"status": "online"}


@app.post("/teste")
def teste(mensagem: MensagemEntrada):
    """Só para desenvolvimento no seu computador. No servidor fica desligada (responde 404)."""
    if not ENDPOINT_TESTE:
        raise HTTPException(status_code=404)
    resposta = processar_mensagem(mensagem.telefone, mensagem.texto)
    return {"resposta": resposta}


# O cron-job.org chama este endereço de manhã (seg a sáb) para enviar o resumo ao dono.
# Senha: cabeçalho X-Cron-Token (preferido) ou ?token= na URL. ?forcar=1 ignora domingo e a trava
# de "já enviado hoje" (só para testar). O envio roda em segundo plano: a resposta é imediata.
@app.api_route("/cron/resumo", methods=["GET", "POST"])
async def cron_resumo(request: Request, tarefas: BackgroundTasks, forcar: int = 0):
    if not CRON_TOKEN:
        raise HTTPException(status_code=404)
    informado = request.headers.get("X-Cron-Token") or request.query_params.get("token")
    if not resumo.token_valido(informado, CRON_TOKEN):
        return Response(content="Token inválido", status_code=403)
    tarefas.add_task(resumo.enviar_resumos, None, bool(forcar))
    return {"status": "agendado"}


# A Meta chama este endereço UMA vez para verificar que o servidor é seu
@app.get("/webhook")
def verificar_webhook(
    modo: str = Query(None, alias="hub.mode"),
    token: str = Query(None, alias="hub.verify_token"),
    desafio: str = Query(None, alias="hub.challenge"),
):
    if modo == "subscribe" and token and hmac.compare_digest(token, VERIFY_TOKEN):
        return Response(content=desafio, media_type="text/plain")
    return Response(content="Token inválido", status_code=403)


# A Meta chama este endereço a cada mensagem recebida
@app.post("/webhook")
async def receber_webhook(request: Request):
    # Recusa corpo gigante antes de ler (o tamanho declarado) e depois de ler (o tamanho real)
    tamanho = request.headers.get("content-length", "")
    if tamanho.isdigit() and int(tamanho) > LIMITE_CORPO_BYTES:
        return Response(content="Corpo grande demais", status_code=413)
    corpo = await request.body()          # bytes EXATOS: a assinatura é calculada sobre eles

    if len(corpo) > LIMITE_CORPO_BYTES:
        return Response(content="Corpo grande demais", status_code=413)

    if not assinatura_valida(corpo, request.headers.get("X-Hub-Signature-256")):
        print("⛔ Webhook recusado: assinatura inválida ou ausente.")
        return Response(content="Assinatura inválida", status_code=403)

    try:
        dados = json.loads(corpo)
        mensagens = []
        # A Meta pode juntar vários eventos/mensagens num único envio: percorremos todos
        for entrada in dados.get("entry", []):
            for mudanca in entrada.get("changes", []):
                mensagens += mudanca.get("value", {}).get("messages") or []
        # Avisos "entregue/lida" não têm "messages": não há nada a fazer

        if mensagens:
            await run_in_threadpool(_limpeza_diaria)
        for msg in mensagens:
            # roda em outra "linha" para não travar o servidor enquanto a IA pensa
            await run_in_threadpool(_tratar_mensagem, msg)
    except Exception as erro:
        print("Erro no webhook:", repr(erro))

    # Sempre responde 200 para a Meta não ficar reenviando a mesma mensagem
    return {"status": "ok"}
