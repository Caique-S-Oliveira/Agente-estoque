"""
Testa a lógica de INTEGRIDADE do Python com um banco falso (não toca o Supabase, não chama IA nem Meta):
estados da mensagem, falha segura na checagem de duplicidade, retomada sem duplicar, chave de
idempotência, respostas honestas quando o banco falha e estado da conversa indisponível.

ATENÇÃO: a atomicidade e a concorrência de verdade vivem no SQL (migracoes/010). Elas só podem ser
provadas num Postgres real com as migrações aplicadas; este arquivo NÃO prova isso.

Rode:  python -m tests.testar_integridade
"""
import hashlib
import hmac
import json
import sys
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import cerebro, database, main
from app.config import META_APP_SECRET

falhas = []


def checar(ok, descricao):
    print(("✅ " if ok else "❌ ") + descricao)
    if not ok:
        falhas.append(descricao)


# ------------------------------------------------------------------ banco falso
class Resp:
    def __init__(self, data=None):
        self.data = data


class Consulta:
    def __init__(self, banco, nome):
        self.b, self.nome, self.filtros, self.op, self.dados = banco, nome, {}, "select", None

    def select(self, *_):
        return self

    def insert(self, dados):
        self.op, self.dados = "insert", dados
        return self

    def update(self, dados):
        self.op, self.dados = "update", dados
        return self

    def eq(self, col, val):
        self.filtros[col] = val
        return self

    def execute(self):
        if self.b.fora:
            raise ConnectionError("banco fora do ar")
        linhas = self.b.tabelas.setdefault(self.nome, [])
        if self.op == "insert":
            if any(l["wa_message_id"] == self.dados["wa_message_id"] for l in linhas):
                raise Exception("duplicate key value violates unique constraint 23505")
            linhas.append(dict(self.dados))
            return Resp([self.dados])
        achadas = [l for l in linhas if all(l.get(k) == v for k, v in self.filtros.items())]
        if self.op == "update":
            for l in achadas:
                l.update(self.dados)
        return Resp([dict(l) for l in achadas])


class BancoFalso:
    def __init__(self):
        self.tabelas, self.fora, self.rpcs = {}, False, []
        self.movs, self.saldo, self.falha_rpc = {}, 10, False

    def table(self, nome):
        return Consulta(self, nome)

    def rpc(self, nome, params):
        banco = self

        class Chamada:
            def execute(self_):
                if banco.fora or banco.falha_rpc:
                    raise ConnectionError("banco fora do ar")
                banco.rpcs.append((nome, params))
                chave = params.get("p_chave")
                if chave and chave in banco.movs:                 # idempotência, como no SQL
                    return Resp({"item": {"nome": "Fita", "quantidade": banco.saldo}, "repetida": True})
                qtd = params["p_quantidade"]
                novo = banco.saldo + qtd if params["p_tipo"] == "entrada" else banco.saldo - qtd
                if novo < 0:
                    return Resp({"erro": "saldo_insuficiente", "nome": "Fita", "saldo": banco.saldo})
                banco.saldo = novo
                if chave:
                    banco.movs[chave] = qtd
                return Resp({"item": {"nome": "Fita", "quantidade": novo}})
        return Chamada()


def com_banco():
    banco = BancoFalso()
    return banco, patch.object(database, "supabase", banco)


# ------------------------------------------------------------------ 1) estados da mensagem
banco, p = com_banco()
with p:
    checar(database.reservar_mensagem("W1") == "nova", "mensagem nova: reservada")
    checar(database.reservar_mensagem("W1") == "em_andamento", "mesma mensagem ainda processando: ignorada")
    database.finalizar_mensagem("W1", "falhou")
    checar(database.reservar_mensagem("W1") == "retomar", "mensagem que falhou: retomada")
    checar(banco.tabelas["mensagens_processadas"][0]["tentativas"] == 2, "retomada conta a tentativa")
    database.finalizar_mensagem("W1", "concluida")
    checar(database.reservar_mensagem("W1") == "concluida", "mensagem concluída: não reprocessa")

    banco.fora = True
    try:
        database.reservar_mensagem("W2")
        checar(False, "banco fora na checagem: deve levantar erro (falhar com segurança)")
    except ConnectionError:
        checar(True, "banco fora na checagem: levanta erro, não segue às cegas")

# ------------------------------------------------------------------ 2) chave de idempotência
banco, p = com_banco()
with p:
    token = database.definir_chave_base("WA9")
    database.registrar_movimentacao("e", "u", "A1", "saida", 3)
    database.registrar_movimentacao("e", "u", "A1", "saida", 2)
    database.restaurar_chave_base(token)
    chaves = [c[1]["p_chave"] for c in banco.rpcs]
    checar(chaves == ["WA9:1", "WA9:2"], "chaves derivadas do ID da mensagem (uma por movimentação)")
    database.registrar_movimentacao("e", "u", "A1", "entrada", 1)
    checar(banco.rpcs[-1][1]["p_chave"] is None, "sem mensagem associada (uso local): sem chave")

    banco.saldo, banco.movs, banco.rpcs = 10, {}, []
    for _ in range(3):                                           # reenvio repetido do mesmo pedido
        t = database.definir_chave_base("WA10")
        database.registrar_movimentacao("e", "u", "A1", "saida", 4)
        database.restaurar_chave_base(t)
    checar(banco.saldo == 6, "mesmo pedido 3x com a mesma chave: aplicado uma única vez (saldo 6)")

    item, erro = database.registrar_movimentacao("e", "u", "A1", "saida", 99)
    checar(item is None and "Saldo insuficiente" in erro, "saída acima do saldo: recusada com mensagem")

# ------------------------------------------------------------------ 3) respostas honestas
banco, p = com_banco()
with p:
    banco.falha_rpc = True
    r = cerebro._executar_movimento("e", "u", "saiu 3", {"tipo": "saida", "codigo": "A1", "quantidade": 3})
    checar("✅" not in r and "/historico" in r, "banco falhou na movimentação: não afirma sucesso")
    r = cerebro._executar_pedido("e", "u", "ajusta", {"tipo": "ajuste", "codigo": "A1", "quantidade": 3})
    checar("✅" not in r and "/historico" in r, "banco falhou no ajuste: não afirma sucesso")

# estado da conversa indisponível: nada é executado
with patch.object(cerebro, "carregar_estado", side_effect=ConnectionError("fora")), \
     patch.object(cerebro, "_interpretar_fluxo") as fluxo:
    r = cerebro.interpretar("e", {"id": "u"}, "sim", lambda i: "")
    checar(fluxo.call_count == 0 and "não executei nada" in r, "estado ilegível: suspende e pede para repetir")

# ------------------------------------------------------------------ 4) webhook ponta a ponta
cliente = TestClient(main.app)


def postar(id_):
    corpo = json.dumps({"entry": [{"changes": [{"value": {"messages": [
        {"id": id_, "from": "5511949188851", "type": "text", "text": {"body": "saiu 4 fita"}}]}}]}]}).encode()
    assin = "sha256=" + hmac.new(META_APP_SECRET.encode(), corpo, hashlib.sha256).hexdigest()
    return cliente.post("/webhook", content=corpo, headers={"X-Hub-Signature-256": assin})


def processar_com_movimento(telefone, texto):
    item, erro = database.registrar_movimentacao("e", "u", "A1", "saida", 4)
    return "ok" if not erro else erro


banco, p = com_banco()
with p, patch.object(main, "enviar_mensagem") as zap, patch.object(main.database, "limpar_mensagens_antigas"), \
        patch.object(main, "processar_mensagem", side_effect=processar_com_movimento) as proc:
    banco.fora = True
    r = postar("WX1")
    checar(r.status_code == 503 and proc.call_count == 0 and zap.call_count == 0,
           "banco fora na checagem: 503, nada processado, nada enviado (a Meta reenvia)")
    banco.fora = False

    proc.side_effect = RuntimeError("caiu no meio")
    r = postar("WX2")
    estado = banco.tabelas["mensagens_processadas"][0]["estado"]
    checar(r.status_code == 503 and estado == "falhou" and zap.call_count == 0,
           "falha no processamento: 503 e estado 'falhou' (não vira 'concluida')")

    proc.side_effect = processar_com_movimento
    r = postar("WX2")                                            # reenvio da Meta
    checar(r.status_code == 200 and banco.tabelas["mensagens_processadas"][0]["estado"] == "concluida"
           and banco.saldo == 6 and zap.call_count == 1, "reenvio após falha: retomado, concluído e aplicado 1x")

    r = postar("WX2")
    checar(banco.saldo == 6 and zap.call_count == 1, "novo reenvio depois de concluída: ignorado, saldo intacto")

print("\n" + ("🎉 INTEGRIDADE (lógica Python) OK" if not falhas else f"🚨 {len(falhas)} falha(s): " + "; ".join(falhas)))
sys.exit(1 if falhas else 0)
