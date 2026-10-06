"""
Resumo da manhã: UMA mensagem por dia para o(s) administrador(es) de cada empresa.
NÃO usa IA (é só código e consultas ao banco), então o custo de IA é zero.

MAPA DO ARQUIVO (de cima para baixo):
  1. periodo_de_referencia() -> qual dia o resumo conta ("ontem"; na segunda, o fim de semana)
  2. dados_resumo()          -> consulta o banco e monta o texto (estoque baixo, movimentos, parados)
  3. montar_resumo()         -> só o texto (usado também pelo comando /resumo)
  4. _enviar_a_um()          -> manda pelo WhatsApp: texto livre; se passou de 24 h sem o dono escrever
                                (a Meta recusa), manda o MODELO aprovado com o botão "Ver resumo"
  5. enviar_resumos()        -> o que o cron-job.org dispara: percorre as empresas e os admins,
                                com trava para não enviar duas vezes no mesmo dia
  6. token_valido()          -> confere a senha do chamador externo (cron)

Por que o plano B (modelo): a Meta só deixa o agente escrever primeiro, sem custo, se a pessoa falou
com ele nas últimas 24 h. Fora disso só vale um modelo de mensagem aprovado (cobrado por mensagem,
centavos). Se o dono usa o agente todo dia, quase sempre o texto livre passa e não custa nada.
"""
import hmac
import os
from datetime import datetime, timedelta, timezone

import database
import whatsapp
from cerebro import _moeda

FUSO_BRASILIA = timezone(timedelta(hours=-3))
DIAS_ENVIO = {0, 1, 2, 3, 4, 5}            # segunda (0) a sábado (5). Domingo a empresa fecha
DIAS_PARADO = 60                            # item com saldo e sem movimento há mais que isto = parado
MAX_BAIXOS = 8                              # quantos itens abaixo do mínimo aparecem na lista
MAX_MAIS_SAIRAM = 3
NOMES_DIA = ["seg", "ter", "qua", "qui", "sex", "sáb", "dom"]

# Nome do modelo criado no painel da Meta (precisa ser igual). Pode ser trocado por variável do Render.
TEMPLATE_RESUMO = os.getenv("TEMPLATE_RESUMO", "resumo_estoque_diario")
# Códigos de erro da Meta que significam "passou de 24 h desde que a pessoa escreveu"
FORA_DA_JANELA = {131047, 470}


# ---------------------------------------------------------------- 1) período
def _rotulo_dia(d) -> str:
    """date -> 'seg 05/10'"""
    return f"{NOMES_DIA[d.weekday()]} {d:%d/%m}"


def periodo_de_referencia(hoje):
    """
    Devolve (dias, texto_com_movimento, texto_sem_movimento). Normalmente conta "ontem". Na segunda
    a empresa esteve fechada no domingo, então conta o fim de semana (sábado e domingo).
    """
    if hoje.weekday() == 0:
        sab, dom = hoje - timedelta(days=2), hoje - timedelta(days=1)
        nome = f"({_rotulo_dia(sab)} e {_rotulo_dia(dom)})"
        return [sab, dom], f"do fim de semana {nome}", f"no fim de semana {nome}"
    ontem = hoje - timedelta(days=1)
    nome = f"ontem ({_rotulo_dia(ontem)})"
    return [ontem], f"de {nome}", nome


# ---------------------------------------------------------------- 2) dados e texto
def dados_resumo(empresa_id: str, agora=None) -> dict:
    """
    Consulta o banco (2 consultas) e monta o texto do resumo. Devolve
    {"texto": ..., "qtd_baixo": quantos itens estão abaixo do mínimo}.
    """
    agora = (agora or datetime.now(FUSO_BRASILIA)).astimezone(FUSO_BRASILIA)
    hoje = agora.date()

    itens = database.todos_itens(empresa_id)
    por_id = {i["id"]: i for i in itens}

    # Estoque baixo: mesma regra do /baixo (mínimo maior que zero e saldo igual ou abaixo dele)
    baixos = sorted(
        (i for i in itens if i["estoque_minimo"] > 0 and i["quantidade"] <= i["estoque_minimo"]),
        key=lambda i: i["quantidade"],
    )

    # Movimentações do período (a carga inicial do estoque e a importação não contam)
    dias, com_mov, sem_mov = periodo_de_referencia(hoje)
    movs = []
    for m in database.movimentacoes_periodo(empresa_id, 4):
        if m.get("origem") == "importacao" or m.get("observacao") == "Estoque inicial":
            continue
        if datetime.fromisoformat(m["criado_em"]).astimezone(FUSO_BRASILIA).date() in dias:
            movs.append(m)
    entradas = [m for m in movs if m["tipo"] == "entrada"]
    saidas = [m for m in movs if m["tipo"] == "saida"]
    ajustes = [m for m in movs if m["tipo"] == "ajuste"]

    # Itens parados: com saldo e sem nenhuma movimentação há DIAS_PARADO dias (igual ao /relatorio)
    limite = datetime.now(timezone.utc) - timedelta(days=DIAS_PARADO)
    parados = [i for i in itens
               if i["quantidade"] > 0 and datetime.fromisoformat(i["atualizado_em"]) < limite]

    linhas = [f"☀️ Bom dia! Resumo de estoque — {_rotulo_dia(hoje)}", ""]

    if baixos:
        linhas.append(f"⚠️ Estoque baixo: {len(baixos)} {'item' if len(baixos) == 1 else 'itens'}")
        for i in baixos[:MAX_BAIXOS]:
            linhas.append(f"• {i['nome']} ({i['codigo']}): {i['quantidade']} (mín. {i['estoque_minimo']})")
        if len(baixos) > MAX_BAIXOS:
            linhas.append(f"+ {len(baixos) - MAX_BAIXOS} outros: digite /baixo")
    else:
        linhas.append("✅ Nenhum item abaixo do mínimo")

    linhas.append("")
    if movs:
        un_e, un_s = sum(m["quantidade"] for m in entradas), sum(m["quantidade"] for m in saidas)
        linhas.append(f"📦 Movimentações {com_mov}")
        partes = [f"Entradas: {len(entradas)} (+{un_e} un)", f"Saídas: {len(saidas)} (-{un_s} un)"]
        if ajustes:
            partes.append(f"Ajustes: {len(ajustes)}")
        linhas.append(" · ".join(partes))
        saiu = {}
        for m in saidas:
            saiu[m["item_id"]] = saiu.get(m["item_id"], 0) + m["quantidade"]
        top = sorted(saiu.items(), key=lambda x: x[1], reverse=True)[:MAX_MAIS_SAIRAM]
        if top:
            linhas.append("Mais saíram: " + ", ".join(
                f"{por_id[i]['nome']} ({q} un)" for i, q in top if i in por_id))
    else:
        linhas.append(f"📦 Sem movimentações {sem_mov}")

    if parados:
        valor = sum(i["quantidade"] * float(i["preco_custo"]) for i in parados)
        linhas += ["", f"💤 Parados há {DIAS_PARADO}+ dias: {len(parados)} "
                       f"{'item' if len(parados) == 1 else 'itens'} ({_moeda(valor)} em custo)"]

    linhas += ["", "Para ver mais: /baixo · /relatorio · /buscar"]
    return {"texto": "\n".join(linhas)[:3900], "qtd_baixo": len(baixos)}


# ---------------------------------------------------------------- 3) só o texto
def montar_resumo(empresa_id: str, agora=None) -> str:
    """Só o texto do resumo. É o que o comando /resumo (admin) devolve."""
    return dados_resumo(empresa_id, agora)["texto"]


# ---------------------------------------------------------------- 4) envio
def _variantes_telefone(telefone: str) -> list:
    """
    Celular brasileiro pode existir na Meta com ou sem o 9 extra. Guardamos com o 9 (13 dígitos);
    se a Meta não achar assim, tentamos sem ele (12 dígitos).
    """
    variantes = [telefone]
    if telefone.startswith("55") and len(telefone) == 13 and telefone[4] == "9":
        variantes.append(telefone[:4] + telefone[5:])
    return variantes


def _enviar_a_um(admin: dict, dados: dict) -> str:
    """
    Envia o resumo a UM administrador. Devolve "texto", "modelo" ou "falhou".
    1) Texto livre (grátis dentro de 24 h após ele ter escrito).
    2) Se a Meta disser que passou das 24 h, manda o modelo aprovado com o botão "Ver resumo";
       ao tocar no botão, a janela abre e o main.py responde com o resumo completo.
    """
    primeiro_nome = ((admin.get("nome") or "").split() or ["tudo bem"])[0]
    for telefone in _variantes_telefone(admin["telefone"]):
        ok, erro = whatsapp.enviar_texto_detalhado(telefone, dados["texto"])
        if ok:
            return "texto"
        if erro in FORA_DA_JANELA:
            ok, erro = whatsapp.enviar_template(
                telefone, TEMPLATE_RESUMO, [primeiro_nome, str(dados["qtd_baixo"])])
            if ok:
                return "modelo"
            print(f"Resumo: o modelo '{TEMPLATE_RESUMO}' não foi aceito (erro {erro}). "
                  "Confira se ele foi criado e aprovado no painel da Meta.")
    return "falhou"


def _administradores(empresa_id: str) -> list:
    """Admins ativos com telefone. É quem recebe o resumo."""
    return [u for u in database.listar_usuarios(empresa_id)
            if u.get("papel") == "admin" and u.get("ativo") and u.get("telefone")]


# ---------------------------------------------------------------- 5) rotina diária
def enviar_resumos(agora=None, forcar: bool = False) -> dict:
    """
    Chamada pelo cron-job.org (rota /cron/resumo). Para cada empresa ativa: reserva o dia (para
    nunca enviar duas vezes), monta o resumo e envia aos admins. Se NINGUÉM recebeu, libera a
    reserva para uma nova tentativa (o cron pode ser configurado para chamar duas vezes).
    forcar=True ignora o domingo e a trava de "já enviado hoje" (serve para testar).
    """
    agora = (agora or datetime.now(FUSO_BRASILIA)).astimezone(FUSO_BRASILIA)
    if not forcar and agora.weekday() not in DIAS_ENVIO:
        print("Resumo da manhã: hoje não é dia de envio.")
        return {"status": "fora do dia de envio"}

    dia = agora.date().isoformat()
    resultado = {}
    for empresa in database.empresas_ativas():
        eid, nome = empresa["id"], empresa["nome"]
        reservou = False
        try:
            if not forcar:
                if not database.reservar_envio_resumo(eid, dia):
                    resultado[nome] = "já enviado hoje"
                    continue
                reservou = True
            admins = _administradores(eid)
            dados = dados_resumo(eid, agora) if admins else None
            enviados = {a["nome"]: _enviar_a_um(a, dados) for a in admins}
            if reservou and not any(r != "falhou" for r in enviados.values()):
                database.liberar_envio_resumo(eid, dia)      # ninguém recebeu: permite nova tentativa
            resultado[nome] = enviados or "sem administradores"
        except Exception as erro:
            print("Resumo da manhã falhou para", nome, repr(erro))
            if reservou:
                try:
                    database.liberar_envio_resumo(eid, dia)
                except Exception as erro2:
                    print("Também não consegui liberar a reserva:", repr(erro2))
            resultado[nome] = "erro"
    print("Resumo da manhã:", resultado)
    return resultado


# ---------------------------------------------------------------- 6) senha do cron
def token_valido(informado, esperado: str) -> bool:
    """Confere a senha enviada pelo cron-job.org (comparação em tempo constante)."""
    return bool(esperado) and bool(informado) and hmac.compare_digest(informado, esperado)
