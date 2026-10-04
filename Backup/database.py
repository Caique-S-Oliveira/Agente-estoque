from supabase import create_client, Client
from datetime import datetime, timezone
from config import SUPABASE_URL, SUPABASE_KEY

# Cria UMA conexão que o projeto inteiro reutiliza
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


def usuario_autorizado(telefone: str):
    """Procura o telefone na tabela usuarios. Retorna o usuário ou None."""
    resposta = (
        supabase.table("usuarios")
        .select("*")
        .eq("telefone", telefone)
        .eq("ativo", True)
        .execute()
    )
    # resposta.data é uma lista; se estiver vazia, o telefone não é autorizado
    return resposta.data[0] if resposta.data else None


def salvar_mensagem(telefone: str, conteudo: str, tipo: str = "texto"):
    """Grava a mensagem recebida na tabela mensagens."""
    supabase.table("mensagens").insert({
        "telefone": telefone,
        "tipo": tipo,
        "conteudo": conteudo,
    }).execute()


def ultimas_mensagens(limite: int = 5):
    """Lê as últimas mensagens gravadas."""
    resposta = (
        supabase.table("mensagens")
        .select("*")
        .order("criado_em", desc=True)
        .limit(limite)
        .execute()
    )
    return resposta.data

# =========================================
# FUNÇÕES DE ESTOQUE
# =========================================

def buscar_item_por_codigo(codigo: str):
    """Procura UM item pelo código exato. Retorna o item ou None."""
    resposta = (
        supabase.table("itens")
        .select("*")
        .eq("codigo", codigo.strip().upper())
        .execute()
    )
    return resposta.data[0] if resposta.data else None


def buscar_itens(termo: str):
    """Procura itens cujo código, nome ou máquina contenham o termo."""
    # Tira caracteres que atrapalham o filtro do Supabase
    for ch in [",", "(", ")"]:
        termo = termo.replace(ch, " ")
    termo = termo.strip()

    resposta = (
        supabase.table("itens")
        .select("*")
        .or_(
            f"codigo.ilike.%{termo}%,"
            f"nome.ilike.%{termo}%,"
            f"maquina_compativel.ilike.%{termo}%"
        )
        .order("nome")
        .limit(10)
        .execute()
    )
    return resposta.data


def adicionar_item(codigo, nome, maquina, quantidade, preco_custo,
                   localizacao, estoque_minimo):
    """Cadastra um item novo. Máquina e localização podem ser vazias."""
    codigo = codigo.strip().upper()

    # Texto vazio vira None, que é "vazio de verdade" no banco
    maquina = (maquina or "").strip() or None
    localizacao = (localizacao or "").strip() or None

    resposta = supabase.table("itens").insert({
        "codigo": codigo,
        "nome": nome.strip(),
        "maquina_compativel": maquina,
        "quantidade": quantidade,
        "preco_custo": preco_custo,
        "localizacao": localizacao,
        "estoque_minimo": estoque_minimo,
    }).execute()

    item = resposta.data[0]

    if quantidade > 0:
        supabase.table("movimentacoes").insert({
            "item_id": item["id"],
            "tipo": "entrada",
            "quantidade": quantidade,
            "observacao": "Estoque inicial",
        }).execute()

    return item


def registrar_movimentacao(codigo: str, tipo: str, quantidade: int,
                           observacao: str = None, origem: str = "whatsapp"):
    """
    Dá entrada ou saída em um item.
    Retorna (item_atualizado, mensagem_de_erro). Um dos dois é None.
    """
    item = buscar_item_por_codigo(codigo)
    if item is None:
        return None, f"Não achei nenhum item com o código {codigo.upper()}."

    if tipo == "entrada":
        nova_qtd = item["quantidade"] + quantidade
    else:  # saida
        nova_qtd = item["quantidade"] - quantidade
        if nova_qtd < 0:
            return None, (
                f"Saldo insuficiente: {item['nome']} tem só "
                f"{item['quantidade']} unidade(s)."
            )

    # 1) Atualiza o saldo do item
    resposta = (
        supabase.table("itens")
        .update({
            "quantidade": nova_qtd,
            "atualizado_em": datetime.now(timezone.utc).isoformat(),
        })
        .eq("id", item["id"])
        .execute()
    )

    # 2) Grava no histórico (é daqui que sairá a previsão de demanda)
    supabase.table("movimentacoes").insert({
        "item_id": item["id"],
        "tipo": tipo,
        "quantidade": quantidade,
        "observacao": observacao,
        "origem": origem,
    }).execute()

    return resposta.data[0], None


def resumo_estoque():
    """Calcula: total de itens cadastrados, unidades e valor investido."""
    itens = supabase.table("itens").select("*").execute().data

    total_itens = len(itens)
    total_unidades = sum(i["quantidade"] for i in itens)
    valor_total = sum(i["quantidade"] * float(i["preco_custo"]) for i in itens)

    return {
        "total_itens": total_itens,
        "total_unidades": total_unidades,
        "valor_total": valor_total,
    }


def itens_baixos():
    """Itens com quantidade igual ou menor que o estoque mínimo."""
    itens = supabase.table("itens").select("*").execute().data
    baixos = [
        i for i in itens
        if i["estoque_minimo"] > 0 and i["quantidade"] <= i["estoque_minimo"]
    ]
    return sorted(baixos, key=lambda i: i["quantidade"])