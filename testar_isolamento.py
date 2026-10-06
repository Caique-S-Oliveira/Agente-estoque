"""
Teste de ISOLAMENTO entre empresas: prova que uma empresa não enxerga nem mexe nos dados de outra.

Por que existe: o servidor usa uma chave do Supabase que ignora o RLS. Então a separação entre
empresas depende de TODA consulta do database.py filtrar por empresa_id. Se um dia alguém
esquecer esse filtro, os dados de um cliente vazam para outro. Este teste pega esse erro.

Rode:  python testar_isolamento.py        (não altera nem apaga nada dos seus dados)

Duas partes:
  1. ESTÁTICA: lê o database.py e confere que cada função que mexe em tabela de empresa usa empresa_id.
  2. DINÂMICA: usa o banco de verdade, mas finge ser uma empresa inexistente (id aleatório) tentando
     ler os dados da VTech. Tudo tem que voltar vazio. As tentativas de escrita só usam valores
     que não mudam nada (ou param antes de gravar), para não colocar seus dados em risco.
"""
import ast
import re
import sys
import uuid
from pathlib import Path

TABELAS_DE_EMPRESA = {"itens", "movimentacoes", "usuarios", "mensagens", "uso_ia",
                      "estado_conversa", "auditoria", "resumos_enviados"}
# Funções que de propósito NÃO recebem empresa_id (e por quê):
EXCECOES = {
    "usuario_autorizado": "descobre a empresa a partir do telefone de quem escreveu",
    "marcar_mensagem_processada": "o ID da mensagem da Meta é único no mundo; só grava, não lê dados de empresa",
    "limpar_mensagens_antigas": "manutenção da tabela de IDs de mensagens; não toca em dados de empresa",
}

problemas = []


def checar(ok: bool, descricao: str):
    """
    Mostra ✅ ou ❌ para uma verificação e guarda o problema, se houver.
    """
    print(("✅ " if ok else "❌ ") + descricao)
    if not ok:
        problemas.append(descricao)


# ---------------------------------------------------------------- 1) ESTÁTICA
def parte_estatica():
    """
    Lê o database.py e confere, consulta por consulta, que todas filtram por empresa_id.
    """
    print("\n=== 1) ESTÁTICA: toda consulta filtra por empresa_id? ===")
    arquivo = Path(__file__).with_name("database.py")
    fonte = arquivo.read_text(encoding="utf-8")
    arvore = ast.parse(fonte)
    verificadas = 0
    for no in arvore.body:
        if not isinstance(no, ast.FunctionDef):
            continue
        codigo = ast.get_source_segment(fonte, no)
        consultas = codigo.split(".table(")[1:]          # cada pedaço começa numa consulta ao banco
        tabelas = [re.match(r'"(\w+)"', c).group(1) for c in consultas]
        if not any(t in TABELAS_DE_EMPRESA for t in tabelas):
            continue
        verificadas += 1
        if no.name in EXCECOES:
            print(f"   (exceção justificada) {no.name}: {EXCECOES[no.name]}")
            continue
        recebe = "empresa_id" in [a.arg for a in no.args.args]
        # CADA consulta (até o seu .execute()) precisa citar empresa_id: no filtro ou nos dados gravados
        sem_filtro = [t for t, c in zip(tabelas, consultas)
                      if t in TABELAS_DE_EMPRESA and "empresa_id" not in c.split(".execute()")[0]]
        checar(recebe and not sem_filtro,
               f"{no.name}() filtra por empresa_id em TODAS as consultas ({len(consultas)})"
               + (f" — faltou em: {', '.join(sem_filtro)}" if sem_filtro else ""))
    # a tabela empresas é filtrada pelo próprio id
    for no in arvore.body:
        if isinstance(no, ast.FunctionDef) and no.name == "buscar_empresa":
            codigo = ast.get_source_segment(fonte, no)
            checar('.eq("id", empresa_id)' in codigo, "buscar_empresa() filtra pelo id da empresa")
    checar(verificadas >= 15, f"conferi {verificadas} funções do database.py (esperado 15 ou mais)")


# ---------------------------------------------------------------- 2) DINÂMICA
def parte_dinamica():
    """
    Usa o banco de verdade: uma empresa falsa tenta ler e mexer nos dados da empresa real.
    """
    print("\n=== 2) DINÂMICA: uma empresa falsa tenta ler os dados da VTech ===")
    import database as db

    # Pega dados REAIS para a empresa falsa tentar alcançar
    usuario = None
    for telefone in sys.argv[1:]:
        usuario = db.usuario_autorizado(telefone)
    if usuario is None:
        resposta = db.supabase.table("usuarios").select("*").eq("ativo", True).limit(1).execute()
        usuario = resposta.data[0] if resposta.data else None
    if usuario is None:
        print("   Nenhum usuário no banco: não há o que testar nesta parte.")
        return
    real = usuario["empresa_id"]
    itens = db.todos_itens(real)
    if not itens:
        print("   A empresa real não tem itens: cadastre 1 item e rode de novo.")
        return
    item = itens[0]
    falsa = str(uuid.uuid4())          # uma empresa que não existe
    print(f"   Empresa real: {real[:8]}…  |  empresa falsa: {falsa[:8]}…  |  item real usado: {item['codigo']}")

    checar(db.buscar_item_por_codigo(falsa, item["codigo"]) is None, "não acha item de outra empresa pelo código")
    checar(db.buscar_itens(falsa, item["nome"].split()[0]) == [], "não acha item de outra empresa pela busca")
    checar(db.todos_itens(falsa) == [], "lista de itens vem vazia")
    resumo = db.resumo_estoque(falsa)
    checar(resumo["total_itens"] == 0 and resumo["total_unidades"] == 0, "resumo do estoque vem zerado")
    checar(db.itens_baixos(falsa) == [], "itens baixos vem vazio")
    checar(not db.movimentacoes_periodo(falsa, 365), "movimentações do período vêm vazias")
    movs, _ = db.historico_movimentacoes(falsa, None)
    checar(not movs, "histórico geral vem vazio")
    movs, _ = db.historico_movimentacoes(falsa, item["codigo"])
    checar(not movs, "histórico do item de outra empresa vem vazio")
    checar(db.listar_usuarios(falsa) == [], "lista de usuários vem vazia")
    checar(db.buscar_usuarios_por_nome(falsa, usuario["nome"][:3]) == [], "busca de usuário por nome vem vazia")
    checar(db.uso_ia_desde(falsa, db.datetime(2000, 1, 1, tzinfo=db.timezone.utc)) == [], "uso da IA vem vazio")
    checar(db.ultimas_mensagens(falsa) == [], "mensagens vêm vazias")
    checar(db.carregar_estado(falsa, usuario["id"]) == {}, "estado da conversa de outra empresa vem vazio")
    checar(db.buscar_empresa(falsa) is None, "empresa falsa não existe")

    # Tentativas de ESCRITA seguras: se o filtro faltasse, o pior que acontece é gravar o mesmo valor.
    antes = db.buscar_item_por_codigo(real, item["codigo"])
    _, erro = db.registrar_movimentacao(falsa, usuario["id"], item["codigo"], "entrada", 1)
    checar(erro is not None, "entrada em item de outra empresa é recusada")
    _, erro = db.ajustar_saldo(falsa, usuario["id"], item["codigo"], antes["quantidade"] + 1)
    checar(erro is not None, "ajuste em item de outra empresa é recusado")
    db.atualizar_item(falsa, item["codigo"], {"nome": antes["nome"]})        # grava o MESMO nome
    db.definir_usuario_ativo(falsa, usuario["id"], True)                      # usuário já ativo: não muda
    depois = db.buscar_item_por_codigo(real, item["codigo"])
    checar(depois["quantidade"] == antes["quantidade"] and depois["nome"] == antes["nome"],
           "o item real continua exatamente igual depois das tentativas")
    checar(db.usuario_autorizado(usuario["telefone"]) is not None, "o usuário real continua ativo")


if __name__ == "__main__":
    parte_estatica()
    try:
        parte_dinamica()
    except Exception as erro:
        print("\n⚠️  A parte dinâmica não pôde rodar:", repr(erro))
        problemas.append("parte dinâmica não rodou")
    print("\n" + ("🎉 ISOLAMENTO OK: nenhuma empresa enxerga a outra." if not problemas
                  else f"🚨 {len(problemas)} problema(s) encontrados: " + "; ".join(problemas)))
    sys.exit(1 if problemas else 0)
