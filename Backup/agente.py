from database import (
    usuario_autorizado, salvar_mensagem,
    buscar_item_por_codigo, buscar_itens, adicionar_item,
    registrar_movimentacao, resumo_estoque, itens_baixos,
)
from config import MARGEM_SUGERIDA


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
    custo = float(item["preco_custo"])
    texto = (
        f"📦 {item['nome']} ({item['codigo']})\n"
        f"   Quantidade: {item['quantidade']} (mínimo {item['estoque_minimo']})\n"
        f"   Valor sugerido: {moeda(preco_sugerido(custo))}\n"
    )
    if mostrar_custo:
        texto += f"   Custo de compra: {moeda(custo)}\n"
    # Só mostra máquina e local se existirem
    if item["maquina_compativel"]:
        texto += f"   Máquina: {item['maquina_compativel']}\n"
    if item["localizacao"]:
        texto += f"   Local: {item['localizacao']}\n"
    return texto.rstrip()


AJUDA = (
    "Comandos disponíveis:\n"
    "/estoque - resumo rápido\n"
    "/baixo - itens acabando\n"
    "/relatorio - valor total investido\n"
    "/buscar termo - procura por código, nome ou máquina\n"
    "/entrada CODIGO QTD - dá entrada\n"
    "/saida CODIGO QTD - dá saída\n"
    "/novo codigo;nome;qtd;preço de compra;mínimo;local;máquina\n"
    "   (local e máquina são opcionais)"
)


# ---------- Um comando = uma função ----------

def cmd_estoque() -> str:
    r = resumo_estoque()
    return (
        "📊 Resumo do estoque\n"
        f"Itens cadastrados: {r['total_itens']}\n"
        f"Unidades em estoque: {r['total_unidades']}\n"
        f"Valor investido: {moeda(r['valor_total'])}"
    )


def cmd_baixo() -> str:
    baixos = itens_baixos()
    if not baixos:
        return "✅ Nenhum item abaixo do estoque mínimo."
    linhas = ["⚠️ Estoque baixo:"]
    for i in baixos:
        linhas.append(
            f"- {i['nome']} ({i['codigo']}): {i['quantidade']} "
            f"(mínimo {i['estoque_minimo']})"
        )
    return "\n".join(linhas)


def cmd_relatorio() -> str:
    r = resumo_estoque()
    return f"💰 Valor total investido no estoque: {moeda(r['valor_total'])}"


def cmd_buscar(args: str) -> str:
    if not args:
        return "Use: /buscar termo"
    achados = buscar_itens(args)
    if not achados:
        return f"Nada encontrado para '{args}'."
    return "\n\n".join(formatar_item(i) for i in achados)


def cmd_movimentar(tipo: str, args: str, telefone: str) -> str:
    """Serve para /entrada e /saida (muda só o tipo)."""
    partes = args.split(maxsplit=2)  # CODIGO, QTD, observação (opcional)
    if len(partes) < 2 or not partes[1].isdigit() or int(partes[1]) <= 0:
        return f"Use: /{tipo} CODIGO QUANTIDADE   (ex: /{tipo} VLV-001 5)"

    codigo, qtd = partes[0], int(partes[1])
    obs = partes[2] if len(partes) > 2 else None

    item, erro = registrar_movimentacao(codigo, tipo, qtd, obs)
    if erro:
        return f"❌ {erro}"

    verbo = "Entrada" if tipo == "entrada" else "Saída"
    resposta = (
        f"✅ {verbo} registrada: {qtd}x {item['nome']}\n"
        f"Saldo atual: {item['quantidade']}"
    )
    # Alerta de estoque baixo logo após a saída
    if item["estoque_minimo"] > 0 and item["quantidade"] <= item["estoque_minimo"]:
        resposta += (
            f"\n⚠️ Atenção: abaixo do mínimo "
            f"({item['estoque_minimo']}). Hora de repor!"
        )
    return resposta


def cmd_novo(args: str) -> str:
    campos = [c.strip() for c in args.split(";")]

    # Obrigatórios: código, nome, qtd, preço, mínimo (5). Opcionais: local, máquina.
    if len(campos) < 5 or len(campos) > 7:
        return (
            "Use: /novo codigo;nome;qtd;preço de compra;mínimo;local;máquina\n"
            "Local e máquina são opcionais.\n\n"
            "Ex. fita (sem local nem máquina):\n"
            "/novo FIT-001;Fita isolante;50;3,20;10\n\n"
            "Ex. peça completa:\n"
            "/novo VLV-001;Válvula X;10;45,90;3;Prateleira B3;Torno Romi\n\n"
            "Ex. só com máquina (deixe o local vazio):\n"
            "/novo COR-020;Correia;4;120;2;;Prensa Schuler"
        )

    # Completa com vazio os opcionais que não vieram
    while len(campos) < 7:
        campos.append("")
    codigo, nome, qtd, preco, minimo, local, maquina = campos

    if not codigo or not nome:
        return "❌ Código e nome são obrigatórios."

    try:
        qtd, minimo = int(qtd), int(minimo)
        preco = numero(preco)
    except ValueError:
        return ("❌ Quantidade e mínimo devem ser números inteiros, "
                "e o preço um número (ex: 45,90).")

    if qtd < 0 or minimo < 0 or preco < 0:
        return "❌ Quantidade, mínimo e preço não podem ser negativos."

    if buscar_item_por_codigo(codigo):
        return f"❌ Já existe um item com o código {codigo.upper()}."

    # Atenção: adicionar_item recebe máquina ANTES de local (ordem do banco)
    item = adicionar_item(codigo, nome, maquina, qtd, preco, local, minimo)
    return "✅ Item cadastrado!\n" + formatar_item(item, mostrar_custo=True)


# ---------- A função principal ----------

def processar_mensagem(telefone: str, texto: str) -> str:
    """Recebe uma mensagem e devolve a resposta."""

    usuario = usuario_autorizado(telefone)
    if usuario is None:
        return "⛔ Número não autorizado."

    salvar_mensagem(telefone, texto)

    texto = texto.strip()

    # Se não começa com "/", ainda não sabemos interpretar (a IA virá depois)
    if not texto.startswith("/"):
        return (
            f"Olá, {usuario['nome']}! Ainda não entendo frases livres, "
            "mas já respondo aos comandos.\n\n" + AJUDA
        )

    # Separa "/entrada VLV-001 5" em comando="/entrada" e args="VLV-001 5"
    partes = texto.split(maxsplit=1)
    comando = partes[0].lower()
    args = partes[1] if len(partes) > 1 else ""

    try:
        if comando == "/estoque":
            return cmd_estoque()
        if comando == "/baixo":
            return cmd_baixo()
        if comando == "/relatorio":
            return cmd_relatorio()
        if comando == "/buscar":
            return cmd_buscar(args)
        if comando == "/entrada":
            return cmd_movimentar("entrada", args, telefone)
        if comando == "/saida":
            return cmd_movimentar("saida", args, telefone)
        if comando == "/novo":
            return cmd_novo(args)
        return "Comando não reconhecido.\n\n" + AJUDA
    except Exception as erro:
        print("Erro no comando:", erro)
        return "❌ Deu um erro interno. Veja o terminal para detalhes."