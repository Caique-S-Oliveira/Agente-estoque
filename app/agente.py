import re
import unicodedata
from difflib import SequenceMatcher
from datetime import datetime, timezone, timedelta
from app.config import MARGEM_SUGERIDA, PRECO_IA_ENTRADA_USD, PRECO_IA_SAIDA_USD
from app.cerebro import interpretar, cancelar_pendente, _achar_itens, _executar_movimento, _extrair_custo
from app.resumo import montar_resumo
from app.database import (
    ultimas_entradas,
    usuario_autorizado, salvar_mensagem,
    buscar_item_por_codigo, buscar_itens, adicionar_item,
    registrar_movimentacao, ajustar_saldo, resumo_estoque, itens_baixos,
    historico_movimentacoes, listar_usuarios, buscar_usuarios_por_nome,
    definir_usuario_ativo, todos_itens, movimentacoes_periodo,
    buscar_empresa, atualizar_item, remover_item, uso_ia_desde, registrar_auditoria,
)

# Horário de Brasília (UTC-3). O Brasil não tem mais horário de verão.
FUSO_BRASILIA = timezone(timedelta(hours=-3))

# Comandos que só administradores podem usar
COMANDOS_ADMIN = {"/historico", "/usuarios", "/desativar", "/reativar", "/remover", "/uso", "/editar", "/resumo"}


def eh_admin(usuario: dict) -> bool:
    """
    Diz se a pessoa é administradora. É esta função que protege os comandos de admin.
    """
    return usuario.get("papel") == "admin"


def formatar_data(iso: str) -> str:
    """Converte a data do banco (UTC) para 03/10 14:32 no horário de Brasília."""
    return datetime.fromisoformat(iso).astimezone(FUSO_BRASILIA).strftime("%d/%m %H:%M")


# ---------- Funções auxiliares ----------

def moeda(valor: float) -> str:
    """Formata 1234.5 como R$ 1.234,50"""
    texto = f"{valor:,.2f}"  # 1,234.50
    texto = texto.replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {texto}"


def numero(texto: str) -> float:
    """Converte '12,50', '12.50' ou 'R$ 12,50' em número."""
    limpo = texto.strip().replace("R$", "").replace(" ", "")
    return float(limpo.replace(",", "."))


def preco_sugerido(preco_custo: float) -> float:
    """Custo + margem (30%), arredondado em centavos."""
    return round(preco_custo * (1 + MARGEM_SUGERIDA), 2)


def formatar_item(item: dict, mostrar_custo: bool = False) -> str:
    """
    Monta o texto de um item para mostrar no WhatsApp. O custo de compra só aparece quando
    mostrar_custo=True (ex.: logo após cadastrar); nas consultas aparece o valor sugerido
    (custo + margem definida no config.py).
    """
    custo = float(item["preco_custo"])
    texto = (
        f"📦 {item['nome']} ({item['codigo']})\n"
        f"   Quantidade: {item['quantidade']} (mínimo {item['estoque_minimo']})\n"
        f"   Valor sugerido: {moeda(preco_sugerido(custo))}\n"
    )
    if mostrar_custo:
        texto += f"   Custo de compra: {moeda(custo)}\n"
    if item["maquina_compativel"]:
        texto += f"   Máquina: {item['maquina_compativel']}\n"
    if item["localizacao"]:
        texto += f"   Local: {item['localizacao']}\n"
    return texto.rstrip()


def formatar_item_admin(item: dict, entrada: dict = None) -> str:
    """
    Versão do administrador: tudo que o operador vê, mais custo unitário, valor em estoque
    (quantidade x custo) e a última entrada (data, quantidade e, se foi informado, o custo pago).
    `entrada` é a última movimentação de entrada do item (ou None se nunca houve compra).
    """
    custo = float(item["preco_custo"])
    texto = (
        f"📦 {item['nome']} ({item['codigo']})\n"
        f"   Quantidade: {item['quantidade']} (mínimo {item['estoque_minimo']})\n"
        f"   Custo unitário: {moeda(custo)}\n"
        f"   Valor em estoque: {moeda(item['quantidade'] * custo)}\n"
        f"   Valor sugerido: {moeda(preco_sugerido(custo))}\n"
    )
    if entrada:
        detalhe = f"+{entrada['quantidade']} un"
        if entrada.get("custo_unitario") is not None:
            detalhe += f" a {moeda(float(entrada['custo_unitario']))} cada"
        texto += f"   Última entrada: {formatar_data(entrada['criado_em'])} ({detalhe})\n"
    else:
        texto += "   Última entrada: nenhuma registrada\n"
    if item["maquina_compativel"]:
        texto += f"   Máquina: {item['maquina_compativel']}\n"
    if item["localizacao"]:
        texto += f"   Local: {item['localizacao']}\n"
    return texto.rstrip()


def texto_itens(empresa_id: str, itens: list, admin: bool = False) -> str:
    """
    Monta o texto de uma lista de itens: formato simples para operador, completo para admin.
    Para o admin, a última entrada de TODOS os itens vem de UMA consulta só (não uma por item).
    Serve tanto para o /buscar quanto para as consultas em linguagem natural (via IA).
    """
    if not admin:
        return "\n\n".join(formatar_item(i) for i in itens)
    entradas = ultimas_entradas(empresa_id, [i["id"] for i in itens])
    return "\n\n".join(formatar_item_admin(i, entradas.get(i["id"])) for i in itens)


AJUDA = (
    "Comandos disponíveis:\n"
    "/estoque - resumo rápido\n"
    "/baixo - itens acabando\n"
    "/relatorio [dias] - análise do período (padrão 30 dias)\n"
    "/buscar termo (ou /busca) - procura por código, nome ou máquina\n"
    "/itens - lista todos os itens (código, nome e quantidade)\n"
    "/entrada CODIGO QTD [custo 3,50] - dá entrada (o custo é opcional)\n"
    "/saida CODIGO QTD - dá saída\n"
    "/ajuste CODIGO NOVO_SALDO - corrige o saldo para um valor exato\n"
    "/novo - cadastra item (digite /novo para ver o formato)"
)


AJUDA_ADMIN = (
    "\n\nSó para administradores:\n"
    "/editar CODIGO campo valor - corrige nome, preco, minimo, local ou maquina\n"
    "/historico [CODIGO] - últimas movimentações (e quem fez)\n"
    "/usuarios - lista os usuários da empresa\n"
    "/desativar NOME - bloqueia o acesso de uma pessoa\n"
    "/reativar NOME - devolve o acesso\n"
    "/remover CODIGO - apaga item cadastrado por engano (só se nunca movimentado)\n"
    "/uso - quanto a IA gastou nos últimos 30 dias\n"
    "/resumo - resumo da manhã agora (estoque baixo, movimentos de ontem, itens parados)"
)


def ajuda_para(usuario: dict) -> str:
    """Operador vê só os comandos dele; admin vê também os de administração."""
    return AJUDA + (AJUDA_ADMIN if eh_admin(usuario) else "")


# ---------- Um comando = uma função ----------
# Todas recebem empresa_id: é ele que separa os dados de cada empresa.

def inteiro(n: int) -> str:
    """3480 vira 3.480"""
    return f"{n:,}".replace(",", ".")


def cmd_estoque(empresa_id: str) -> str:
    """
    /estoque: retrato rápido (itens, unidades, valor investido, alertas, última movimentação).
    """
    r = resumo_estoque(empresa_id)
    linhas = [
        "📊 Estoque",
        f"Itens cadastrados: {inteiro(r['total_itens'])} | Unidades: {inteiro(r['total_unidades'])}",
        f"Valor investido (custo): {moeda(r['valor_total'])}",
        f"⚠️ Abaixo do mínimo: {r['abaixo_minimo']} | ❌ Zerados: {r['zerados']}",
    ]
    movs, _ = historico_movimentacoes(empresa_id, limite=1)
    if movs:
        m = movs[0]
        linhas.append(
            f"Última movimentação: {formatar_data(m['criado_em'])} "
            f"({m['usuario_nome'] or 'sem registro'})"
        )
    return "\n".join(linhas)


def cmd_baixo(empresa_id: str) -> str:
    """
    /baixo: lista os itens com saldo igual ou abaixo do mínimo.
    """
    baixos = itens_baixos(empresa_id)
    if not baixos:
        return "✅ Nenhum item abaixo do estoque mínimo."
    linhas = ["⚠️ Estoque baixo:"]
    for i in baixos:
        linhas.append(
            f"- {i['nome']} ({i['codigo']}): {i['quantidade']} "
            f"(mínimo {i['estoque_minimo']})"
        )
    return "\n".join(linhas)


def cmd_relatorio(empresa_id: str, args: str = "") -> str:
    """
    /relatorio [dias]: análise do período (entradas, saídas, mais movimentados). Padrão: 30 dias.
    """
    args = args.strip()
    if args and not (args.isdigit() and 1 <= int(args) <= 365):
        return "Use: /relatorio [dias]   (ex: /relatorio 7). Padrão 30, máximo 365."
    dias = int(args) if args else 30

    itens = todos_itens(empresa_id)
    por_id = {i["id"]: i for i in itens}
    valor = lambda i: i["quantidade"] * float(i["preco_custo"])
    total = sum(valor(i) for i in itens)
    margem = round(MARGEM_SUGERIDA * 100)

    linhas = [
        f"💰 Relatório — últimos {dias} dias",
        f"Valor investido (custo): {moeda(total)}",
        f"Valor de venda sugerido (+{margem}%): {moeda(total * (1 + MARGEM_SUGERIDA))}",
    ]

    # Onde está o dinheiro
    if total > 0:
        linhas += ["", "Onde está o dinheiro (top 5):"]
        for n, i in enumerate(sorted(itens, key=valor, reverse=True)[:5], 1):
            if valor(i) <= 0:
                break
            linhas.append(f"{n}. {i['nome']} — {moeda(valor(i))} ({valor(i) / total * 100:.0f}%)")

    # Movimento do período (a importação inicial não conta)
    movs = [m for m in movimentacoes_periodo(empresa_id, dias) if m.get("origem") != "importacao"]
    custo = lambda m: m["quantidade"] * float(por_id[m["item_id"]]["preco_custo"]) if m["item_id"] in por_id else 0
    entradas = [m for m in movs if m["tipo"] == "entrada"]
    saidas = [m for m in movs if m["tipo"] == "saida"]
    linhas += [
        "",
        f"Movimento: {len(entradas)} entradas ({moeda(sum(custo(m) for m in entradas))} a custo) · "
        f"{len(saidas)} saídas ({moeda(sum(custo(m) for m in saidas))} a custo)",
    ]

    saiu = {}
    for m in saidas:
        saiu[m["item_id"]] = saiu.get(m["item_id"], 0) + m["quantidade"]
    if saiu:
        top = sorted(saiu.items(), key=lambda x: x[1], reverse=True)[:5]
        linhas.append("Mais saíram: " + ", ".join(
            f"{por_id[i]['nome']} ({q} un)" for i, q in top if i in por_id))
    else:
        linhas.append("Sem saídas registradas no período.")

    # Itens parados: com saldo e sem nenhuma movimentação há 60+ dias
    limite = datetime.now(timezone.utc) - timedelta(days=60)
    parados = [i for i in itens
               if i["quantidade"] > 0 and datetime.fromisoformat(i["atualizado_em"]) < limite]
    if parados:
        linhas.append(
            f"Sem movimento há 60+ dias: {len(parados)} item(ns) "
            f"({moeda(sum(valor(i) for i in parados))} parados)"
        )
    return "\n".join(linhas)


def cmd_buscar(empresa_id: str, args: str, admin: bool = False) -> str:
    """
    /buscar termo (ou /busca): procura por código, nome ou máquina. Primeiro tenta o texto exato;
    se não achar, usa a busca por palavras (a mesma da IA), que entende 'fita 8kg' mesmo com
    'branca 10mm' no meio. Operador vê o formato simples; admin vê também custo unitário,
    valor em estoque e última entrada.
    """
    if not args:
        return "Use: /buscar termo"
    achados = buscar_itens(empresa_id, args) or _achar_itens(todos_itens(empresa_id), args)
    if not achados:
        return f"Nada encontrado para '{args}'."
    return texto_itens(empresa_id, achados, admin)


def cmd_itens(empresa_id: str) -> str:
    """
    /itens: lista TODOS os itens, uma linha cada (código - nome - quantidade), em ordem de código.
    Útil para conferir o catálogo. Mensagens longas são divididas pelo whatsapp.py.
    """
    itens = sorted(todos_itens(empresa_id), key=lambda i: i["codigo"])
    if not itens:
        return "Nenhum item cadastrado ainda."
    linhas = [f"{i['codigo']} - {i['nome']} ({i['quantidade']})" for i in itens]
    return f"📋 {len(itens)} itens (código - nome - quantidade)\n" + "\n".join(linhas)


def cmd_movimentar(empresa_id: str, usuario_id: str, tipo: str, args: str) -> str:
    """
    Serve para /entrada e /saida (muda só o tipo). Na entrada, o custo é opcional:
    /entrada FIT-001 50 custo 3,50. Quem grava e monta a resposta é o mesmo código das frases
    livres (_executar_movimento, no cerebro.py), assim o comportamento é igual nos dois caminhos.
    """
    partes = args.split(maxsplit=2)  # CODIGO, QTD, observação e/ou custo (opcionais)
    if len(partes) < 2 or not partes[1].isdigit() or int(partes[1]) <= 0:
        dica = "  ou com custo: /entrada VLV-001 5 custo 3,50" if tipo == "entrada" else ""
        return f"Use: /{tipo} CODIGO QUANTIDADE   (ex: /{tipo} VLV-001 5){dica}"

    codigo, qtd = partes[0], int(partes[1])
    resto = partes[2] if len(partes) > 2 else ""
    custo = None
    if tipo == "entrada" and resto:
        resto, custo = _extrair_custo(resto, tambem_no_inicio=True)   # o que sobrar vira a observação

    dados = {"tipo": tipo, "codigo": codigo, "quantidade": qtd}
    if custo:
        dados["custo_unitario"] = custo
    return _executar_movimento(empresa_id, usuario_id, resto, dados)


def cmd_ajuste(empresa_id: str, usuario_id: str, args: str) -> str:
    """Corrige o saldo para um valor exato. Ex: /ajuste FIT-001 150"""
    partes = args.split(maxsplit=2)  # CODIGO, NOVO_SALDO, observação (opcional)
    if len(partes) < 2 or not partes[1].isdigit():
        return "Use: /ajuste CODIGO NOVO_SALDO   (ex: /ajuste FIT-001 150)"

    codigo, novo_saldo = partes[0], int(partes[1])
    obs = partes[2] if len(partes) > 2 else None

    item_antes = buscar_item_por_codigo(empresa_id, codigo)
    saldo_antes = item_antes["quantidade"] if item_antes else None

    item, erro = ajustar_saldo(empresa_id, usuario_id, codigo, novo_saldo, obs)
    if erro:
        return f"❌ {erro}"

    return (
        f"✅ Saldo ajustado: {item['nome']}\n"
        f"De {saldo_antes} para {item['quantidade']}"
    )


def cmd_historico(empresa_id: str, args: str) -> str:
    """
    /historico [CODIGO]: últimas movimentações com saldo antes/depois e quem fez. Só admin.
    """
    codigo = args.strip() or None
    movs, erro = historico_movimentacoes(empresa_id, codigo)
    if erro:
        return f"❌ {erro}"
    if not movs:
        return "Nenhuma movimentação encontrada."

    titulo = f"🧾 Últimas movimentações de {codigo.upper()}" if codigo else "🧾 Últimas movimentações"
    linhas = [titulo]
    for m in movs:
        item = m["item"]
        nome_item = f"{item['nome']} ({item['codigo']})" if item else "item removido"
        quem = m["usuario_nome"] or "sem registro"

        if m["tipo"] == "entrada":
            acao = f"Entrada +{m['quantidade']}"
        elif m["tipo"] == "saida":
            acao = f"Saída -{m['quantidade']}"
        else:
            acao = "Ajuste"

        saldo = ""
        if m.get("saldo_anterior") is not None and m.get("saldo_novo") is not None:
            saldo = f" | saldo {m['saldo_anterior']} → {m['saldo_novo']}"

        linha = f"{formatar_data(m['criado_em'])} | {acao} | {nome_item}{saldo} | por {quem}"
        if m.get("observacao"):
            linha += f" ({m['observacao']})"
        linhas.append(linha)
    return "\n".join(linhas)


def cmd_usuarios(empresa_id: str) -> str:
    """
    /usuarios: lista as pessoas da empresa e o cargo de cada uma. Só admin.
    """
    usuarios = listar_usuarios(empresa_id)
    if not usuarios:
        return "Nenhum usuário cadastrado."
    linhas = ["👥 Usuários da empresa:"]
    for u in usuarios:
        situacao = "✅ ativo" if u["ativo"] else "⛔ desativado"
        linhas.append(f"- {u['nome']} ({u['papel']}) {situacao}")
    return "\n".join(linhas)


def cmd_ativar_desativar(empresa_id: str, quem_pede: dict, args: str, ativar: bool) -> str:
    """Serve para /desativar e /reativar (muda só o sentido)."""
    comando = "reativar" if ativar else "desativar"
    nome = args.strip()
    if not nome:
        return f"Use: /{comando} NOME   (ex: /{comando} Maria)"

    achados = buscar_usuarios_por_nome(empresa_id, nome)
    if not achados:
        return f"❌ Não achei nenhum usuário com o nome '{nome}'. Veja /usuarios."
    if len(achados) > 1:
        nomes = ", ".join(u["nome"] for u in achados)
        return f"Achei mais de um ({nomes}). Digite o nome completo."

    alvo = achados[0]

    # Proteção: ninguém desativa a si mesmo. Como só admins usam este comando,
    # isso garante que a empresa nunca fica sem nenhum admin ativo.
    if not ativar and alvo["id"] == quem_pede["id"]:
        return "❌ Você não pode desativar a si mesmo."

    if ativar and alvo["ativo"]:
        return f"O acesso de {alvo['nome']} já está ativo."
    if not ativar and not alvo["ativo"]:
        return f"O acesso de {alvo['nome']} já está desativado."

    definir_usuario_ativo(empresa_id, alvo["id"], ativar)
    if ativar:
        return f"✅ Acesso de {alvo['nome']} reativado."
    return f"✅ Acesso de {alvo['nome']} desativado. A pessoa não consegue mais usar o agente."


def normalizar(texto: str) -> str:
    """Minúsculo, sem acento e sem espaços repetidos (para comparar nomes)."""
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return " ".join(sem_acento.lower().split())


def nome_parecido(a: str, b: str) -> bool:
    """Quase igual E com os mesmos números (assim '8kg' e '10kg' não contam como parecidos)."""
    if re.findall(r"\d+", a) != re.findall(r"\d+", b):
        return False
    return SequenceMatcher(None, a, b).ratio() >= 0.9


CAMPOS_EDITAVEIS = {"nome": "nome", "preco": "preco_custo", "minimo": "estoque_minimo",
                    "local": "localizacao", "maquina": "maquina_compativel"}


def cmd_novo(empresa_id: str, usuario_id: str, args: str) -> str:
    """
    /novo: cadastra item. O formato depende da empresa: com código automático não se digita o
    código; com código manual ele vem primeiro. Antes de gravar: bloqueia nome idêntico
    (duplicado) e só avisa quando houver nome parecido com os mesmos números.
    """
    auto = buscar_empresa(empresa_id)["codigo_automatico"]
    campos = [c.strip() for c in args.split(";")]

    if auto:   # o sistema gera o código
        n_min, n_max = 4, 6
        uso = (
            "Use: /novo nome;qtd;preço de compra;mínimo;local;máquina\n"
            "Local e máquina são opcionais. O código é gerado pelo sistema.\n\n"
            "Ex. fita (sem local nem máquina):\n/novo Fita isolante;50;3,20;10\n\n"
            "Ex. peça completa:\n/novo Válvula X;10;45,90;3;Prateleira B3;Torno Romi\n\n"
            "Ex. só com máquina (local vazio):\n/novo Correia;4;120;2;;Prensa Schuler"
        )
    else:      # o código é digitado
        n_min, n_max = 5, 7
        uso = (
            "Use: /novo codigo;nome;qtd;preço de compra;mínimo;local;máquina\n"
            "Local e máquina são opcionais.\n\n"
            "Ex. fita:\n/novo FIT-001;Fita isolante;50;3,20;10\n\n"
            "Ex. peça completa:\n/novo VLV-001;Válvula X;10;45,90;3;Prateleira B3;Torno Romi\n\n"
            "Ex. só com máquina (local vazio):\n/novo COR-020;Correia;4;120;2;;Prensa Schuler"
        )

    if not (n_min <= len(campos) <= n_max):
        return uso

    if auto:
        campos = [""] + campos          # código vazio: o banco gera
    while len(campos) < 7:
        campos.append("")
    codigo, nome, qtd, preco, minimo, local, maquina = campos

    if not nome or (not auto and not codigo):
        return "❌ Nome" + ("" if auto else " e código") + " são obrigatórios."

    try:
        qtd, minimo = int(qtd), int(minimo)
        preco = numero(preco)
    except ValueError:
        return ("❌ Quantidade e mínimo devem ser números inteiros, "
                "e o preço um número (ex: 45,90).")

    if qtd < 0 or minimo < 0 or preco < 0:
        return "❌ Quantidade, mínimo e preço não podem ser negativos."

    if codigo and buscar_item_por_codigo(empresa_id, codigo):
        return f"❌ Já existe um item com o código {codigo.upper()}."

    # Nome idêntico (sem acento/maiúscula/espaço extra) = duplicado: bloqueia.
    # Nome parecido COM OS MESMOS NÚMEROS (ex: erro de digitação) = cadastra e avisa.
    # Números diferentes (8kg x 12kg) são itens diferentes e não geram aviso.
    nome_norm = normalizar(nome)
    existentes = todos_itens(empresa_id)
    for e in existentes:
        if normalizar(e["nome"]) == nome_norm:
            return (f"❌ Já existe '{e['nome']}' ({e['codigo']}). "
                    "Para mexer no saldo use /entrada, /saida ou /ajuste.")
    parecidos = [e for e in existentes if nome_parecido(normalizar(e["nome"]), nome_norm)][:3]

    item = adicionar_item(empresa_id, usuario_id, codigo, nome, maquina, qtd, preco, local, minimo)
    resposta = "✅ Item cadastrado!\n" + formatar_item(item, mostrar_custo=True)
    if parecidos:
        resposta += "\n\nℹ️ Parecidos já cadastrados: " + "; ".join(
            f"{e['nome']} ({e['codigo']})" for e in parecidos)
    return resposta


def cmd_editar(empresa_id: str, args: str) -> str:
    """
    /editar CODIGO campo valor: corrige nome, preço, mínimo, local ou máquina (nunca o saldo).
    """
    uso = ("Use: /editar CODIGO campo valor\n"
           "Campos: nome, preco, minimo, local, maquina\n"
           "Ex: /editar 0003 preco 3,50\n"
           "Para limpar local ou máquina, use - no valor.\n"
           "O saldo não se edita aqui: use /entrada, /saida ou /ajuste.")
    partes = args.split(maxsplit=2)
    if len(partes) < 3:
        return uso
    codigo, campo, valor = partes[0], partes[1].lower(), partes[2].strip()
    if campo not in CAMPOS_EDITAVEIS:
        return f"❌ O campo '{campo}' não existe.\n\n" + uso

    item = buscar_item_por_codigo(empresa_id, codigo)
    if item is None:
        return f"❌ Não achei nenhum item com o código {codigo.upper()}."

    coluna = CAMPOS_EDITAVEIS[campo]
    try:
        if campo == "preco":
            novo = numero(valor)
            if novo < 0:
                raise ValueError
        elif campo == "minimo":
            if not valor.isdigit():
                raise ValueError
            novo = int(valor)
        elif campo == "nome":
            novo = valor
            for e in todos_itens(empresa_id):
                if e["id"] != item["id"] and normalizar(e["nome"]) == normalizar(novo):
                    return f"❌ Já existe '{e['nome']}' ({e['codigo']}) com esse nome."
        else:  # local, maquina
            novo = None if valor == "-" else valor
    except ValueError:
        return "❌ Valor inválido para esse campo (preço: ex 3,50; mínimo: número inteiro)."

    _, erro = atualizar_item(empresa_id, item["codigo"], {coluna: novo})
    if erro:
        return f"❌ {erro}"
    mostrar = (lambda v: moeda(float(v))) if campo == "preco" else (lambda v: v if v not in (None, "") else "-")
    return f"✅ {campo} de {item['nome']} ({item['codigo']}) alterado: {mostrar(item[coluna])} → {mostrar(novo)}"


def cmd_remover(empresa_id: str, args: str) -> str:
    """
    /remover CODIGO: apaga item cadastrado por engano. Só funciona se nunca teve movimentação. Só admin.
    """
    partes = args.split()
    if not partes:
        return "Use: /remover CODIGO"
    confirmou = len(partes) > 1 and partes[1].lower() == "confirmar"

    item = buscar_item_por_codigo(empresa_id, partes[0])
    if item is None:
        return f"❌ Não achei nenhum item com o código {partes[0].upper()}."

    if not confirmou:
        return (f"⚠️ Vai remover {item['nome']} ({item['codigo']}), se ele nunca foi movimentado.\n"
                f"Para confirmar, envie: /remover {item['codigo']} confirmar")

    # remover_item já recusa se o item teve movimentações além do estoque inicial
    _, erro = remover_item(empresa_id, item["codigo"])
    if erro:
        return f"❌ {erro}"
    return f"🗑️ {item['nome']} ({item['codigo']}) removido."


def cmd_uso(empresa_id: str) -> str:
    """
    /uso: quanto a IA gastou nos últimos 30 dias (tokens e custo estimado). Só admin.
    """
    desde = datetime.now(timezone.utc) - timedelta(days=30)
    usos = uso_ia_desde(empresa_id, desde)
    if not usos:
        return "🤖 Nenhum uso da IA nos últimos 30 dias."
    entrada = sum(u["tokens_entrada"] for u in usos)
    saida = sum(u["tokens_saida"] for u in usos)
    custo = entrada * PRECO_IA_ENTRADA_USD / 1e6 + saida * PRECO_IA_SAIDA_USD / 1e6
    ms = [u["duracao_ms"] for u in usos if u.get("duracao_ms")]
    linhas = [
        "🤖 Uso da IA — últimos 30 dias",
        f"Mensagens: {len(usos)}",
        f"Tokens: {inteiro(entrada)} de entrada / {inteiro(saida)} de saída",
        f"Custo estimado: US$ {custo:.2f} (US$ {custo / len(usos):.4f} por mensagem)".replace(".", ","),
    ]
    if ms:
        linhas.append(f"Tempo médio de resposta: {sum(ms) / len(ms) / 1000:.1f} s")
    linhas.append("(Valor estimado pela tabela de preços do config.py; confira o gasto real no painel da Anthropic.)")
    return "\n".join(linhas)


# ---------- A função principal ----------

COMANDOS_AUDITADOS = {"/editar", "/remover", "/desativar", "/reativar"}   # ações de administração


def executar_comando(usuario: dict, texto: str) -> str:
    """
    Executa um comando com barra (também usado pela IA para cadastro e resumos). Quando um
    comando de administração dá certo, deixa um registro de auditoria: quem fez e o quê.
    """
    resposta = _executar_comando_bruto(usuario, texto)
    partes = texto.split(maxsplit=1)
    comando = partes[0].lower() if partes else ""
    if comando in COMANDOS_AUDITADOS and resposta.startswith(("✅", "🗑️")):
        try:
            registrar_auditoria(usuario["empresa_id"], usuario["id"], comando.lstrip("/"),
                                {"argumentos": (partes[1] if len(partes) > 1 else "")[:200],
                                 "resultado": resposta[:200]})
        except Exception as erro:       # a auditoria falhar não pode desfazer o que já foi feito
            print("Falha ao registrar auditoria:", erro)
    return resposta


def _executar_comando_bruto(usuario: dict, texto: str) -> str:
    """Executa o comando e devolve a resposta (a checagem de permissão está aqui)."""
    empresa_id = usuario["empresa_id"]
    usuario_id = usuario["id"]
    partes = texto.split(maxsplit=1)
    comando = partes[0].lower()
    args = partes[1] if len(partes) > 1 else ""

    # Permissão: a checagem é feita AQUI, no código, antes de executar qualquer coisa
    if comando in COMANDOS_ADMIN and not eh_admin(usuario):
        return "🔒 Você não tem permissão para este comando."

    try:
        if comando == "/estoque":
            return cmd_estoque(empresa_id)
        if comando == "/baixo":
            return cmd_baixo(empresa_id)
        if comando == "/relatorio":
            return cmd_relatorio(empresa_id, args)
        if comando in ("/buscar", "/busca"):
            return cmd_buscar(empresa_id, args, admin=eh_admin(usuario))
        if comando == "/itens":
            return cmd_itens(empresa_id)
        if comando == "/entrada":
            return cmd_movimentar(empresa_id, usuario_id, "entrada", args)
        if comando == "/saida":
            return cmd_movimentar(empresa_id, usuario_id, "saida", args)
        if comando in ("/ajuda", "/start"):
            return ajuda_para(usuario)
        if comando == "/historico":
            return cmd_historico(empresa_id, args)
        if comando == "/usuarios":
            return cmd_usuarios(empresa_id)
        if comando == "/desativar":
            return cmd_ativar_desativar(empresa_id, usuario, args, ativar=False)
        if comando == "/reativar":
            return cmd_ativar_desativar(empresa_id, usuario, args, ativar=True)
        if comando == "/uso":
            return cmd_uso(empresa_id)
        if comando == "/resumo":
            return montar_resumo(empresa_id)
        if comando == "/ajuste":
            return cmd_ajuste(empresa_id, usuario_id, args)
        if comando == "/novo":
            return cmd_novo(empresa_id, usuario_id, args)
        if comando == "/editar":
            return cmd_editar(empresa_id, args)
        if comando == "/remover":
            return cmd_remover(empresa_id, args)
        return "Comando não reconhecido.\n\n" + ajuda_para(usuario)
    except Exception as erro:
        print("Erro no comando:", erro)
        return "❌ Deu um erro interno. Veja o terminal para detalhes."


def processar_mensagem(telefone: str, texto: str) -> str:
    """Recebe uma mensagem e devolve a resposta."""

    usuario = usuario_autorizado(telefone)
    if usuario is None:
        return "⛔ Número não autorizado."

    # A partir daqui, tudo é feito DENTRO da empresa deste usuário
    empresa_id = usuario["empresa_id"]
    usuario_id = usuario["id"]  # quem está fazendo a operação (vai para o histórico)

    empresa = buscar_empresa(empresa_id)
    if empresa is None or not empresa.get("ativa", True):
        return "⛔ A conta desta empresa está suspensa. Fale com o suporte."

    salvar_mensagem(empresa_id, telefone, texto)

    texto = texto.strip()

    if texto.startswith("/"):
        cancelar_pendente(empresa_id, usuario_id)   # um comando cancela qualquer confirmação pendente

    if not texto.startswith("/"):
        try:
            return interpretar(empresa_id, usuario, texto,
                               lambda itens: texto_itens(empresa_id, itens, eh_admin(usuario)),
                               lambda cmd, args="": executar_comando(usuario, f"{cmd} {args}".strip()))
        except Exception as erro:
            print("Erro na IA:", repr(erro))
            return ("❌ Não consegui interpretar agora. Tente de novo em instantes "
                    "ou use os comandos (/ajuda).")

    return executar_comando(usuario, texto)
