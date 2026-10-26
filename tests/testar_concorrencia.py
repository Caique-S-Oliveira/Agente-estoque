"""
Prova de CONCORRÊNCIA no banco real, só com um item de teste (nunca use um item de cliente).
Dispara várias saídas ao mesmo tempo no mesmo item e confere que nenhuma se perde e o saldo não fica negativo.

Rode:  python -m tests.testar_concorrencia EMPRESA_ID USUARIO_ID CODIGO_DO_ITEM_DE_TESTE
O item é preparado com saldo 10 (ajuste registrado no histórico). Precisa das migrações 010/011 aplicadas.
"""
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor

from app import database

if len(sys.argv) != 4:
    sys.exit(__doc__)
empresa, usuario, codigo = sys.argv[1:]
falhas = []


def checar(ok, descricao):
    print(("✅ " if ok else "❌ ") + descricao)
    if not ok:
        falhas.append(descricao)


def saldo():
    return database.buscar_item_por_codigo(empresa, codigo)["quantidade"]


def saida(qtd):
    # Cada chamada tem sua chave própria: são pedidos DIFERENTES, que competem pelo mesmo saldo.
    token = database.definir_chave_base("conc-" + uuid.uuid4().hex)
    try:
        return database.registrar_movimentacao(empresa, usuario, codigo, "saida", qtd)
    finally:
        database.restaurar_chave_base(token)


def preparar(valor):
    if saldo() != valor:
        token = database.definir_chave_base("prep-" + uuid.uuid4().hex)
        try:
            database.ajustar_saldo(empresa, usuario, codigo, valor, "preparo do teste de concorrência")
        finally:
            database.restaurar_chave_base(token)


# 1) 10 saídas de 1 ao mesmo tempo, saldo 10: todas passam e o saldo termina em 0 (nenhuma atualização perdida)
preparar(10)
with ThreadPoolExecutor(10) as pool:
    r1 = list(pool.map(lambda _: saida(1), range(10)))
checar(all(erro is None for _, erro in r1) and saldo() == 0, "10 saídas simultâneas de 1: todas aplicadas, saldo final 0")

# 2) 10 saídas de 1 com saldo 6: exatamente 6 passam, 4 recusadas, saldo nunca negativo
preparar(6)
with ThreadPoolExecutor(10) as pool:
    r2 = list(pool.map(lambda _: saida(1), range(10)))
ok = sum(1 for _, erro in r2 if erro is None)
checar(ok == 6 and saldo() == 0, f"10 saídas simultâneas com saldo 6: {ok} aplicadas (esperado 6), saldo final {saldo()} (esperado 0)")

print("\n" + ("🎉 CONCORRÊNCIA OK" if not falhas else f"🚨 {len(falhas)} falha(s): " + "; ".join(falhas)))
sys.exit(1 if falhas else 0)
