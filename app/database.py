from datetime import datetime, timezone, timedelta
from supabase import create_client, Client
from app.config import SUPABASE_URL, SUPABASE_KEY

# Cria UMA conexão que o projeto inteiro reutiliza
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


# =========================================
# USUÁRIOS E MENSAGENS
# =========================================

def usuario_autorizado(telefone: str):
    """Procura o telefone na tabela usuarios. Retorna o usuário (com empresa_id) ou None."""
    resposta = (
        supabase.table("usuarios")
        .select("*")
        .eq("telefone", telefone)
        .eq("ativo", True)
        .execute()
    )
    return resposta.data[0] if resposta.data else None


def salvar_mensagem(empresa_id: str, telefone: str, conteudo: str, tipo: str = "texto"):
    """Grava a mensagem recebida, ligada à empresa."""
    supabase.table("mensagens").insert({
        "empresa_id": empresa_id,
        "telefone": telefone,
        "tipo": tipo,
        "conteudo": conteudo,
    }).execute()


def ultimas_mensagens(empresa_id: str, limite: int = 5):
    """Lê as últimas mensagens gravadas de UMA empresa."""
    resposta = (
        supabase.table("mensagens")
        .select("*")
        .eq("empresa_id", empresa_id)
        .order("criado_em", desc=True)
        .limit(limite)
        .execute()
    )
    return resposta.data


# =========================================
# FUNÇÕES DE ESTOQUE
# Toda função recebe empresa_id PRIMEIRO e filtra por ele,
# para uma empresa nunca enxergar os dados de outra.
# =========================================

def buscar_item_por_codigo(empresa_id: str, codigo: str):
    """Procura UM item pelo código exato. Retorna o item ou None."""
    resposta = (
        supabase.table("itens")
        .select("*")
        .eq("empresa_id", empresa_id)
        .eq("codigo", codigo.strip().upper())
        .execute()
    )
    return resposta.data[0] if resposta.data else None


def buscar_itens(empresa_id: str, termo: str):
    """Procura itens cujo código, nome ou máquina contenham o termo."""
    for ch in [",", "(", ")"]:
        termo = termo.replace(ch, " ")
    termo = termo.strip()

    resposta = (
        supabase.table("itens")
        .select("*")
        .eq("empresa_id", empresa_id)
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


def buscar_empresa(empresa_id: str):
    """Dados da empresa, incluindo a configuração codigo_automatico."""
    r = supabase.table("empresas").select("*").eq("id", empresa_id).execute()
    return r.data[0] if r.data else None


def gerar_codigo(empresa_id: str) -> str:
    """Próximo código sequencial da empresa (0001, 0002...). Pula os que já existem."""
    for _ in range(20):
        # A função gerar_codigo do banco soma 1 de forma segura (sem repetir número)
        numero = supabase.rpc("gerar_codigo", {"p_empresa_id": empresa_id}).execute().data
        codigo = str(numero).zfill(4)
        if buscar_item_por_codigo(empresa_id, codigo) is None:
            return codigo
    raise RuntimeError("Não consegui gerar um código livre.")


def atualizar_item(empresa_id: str, codigo: str, campos: dict):
    """Altera dados de um item (nunca o saldo: para isso existe o ajuste)."""
    item = buscar_item_por_codigo(empresa_id, codigo)
    if item is None:
        return None, f"Não achei nenhum item com o código {codigo.upper()}."
    r = (
        supabase.table("itens").update(campos)
        .eq("id", item["id"]).eq("empresa_id", empresa_id).execute()
    )
    return r.data[0], None


def remover_item(empresa_id: str, codigo: str):
    """Apaga um item só se ele nunca foi movimentado (além do estoque inicial)."""
    item = buscar_item_por_codigo(empresa_id, codigo)
    if item is None:
        return None, f"Não achei nenhum item com o código {codigo.upper()}."
    movs = (
        supabase.table("movimentacoes").select("id,observacao")
        .eq("item_id", item["id"]).eq("empresa_id", empresa_id).execute().data
    )
    if len(movs) > 1 or any(m.get("observacao") != "Estoque inicial" for m in movs):
        return None, (
            f"{item['nome']} já teve movimentações e não pode ser apagado "
            f"(o histórico é preservado). Para zerar, use /ajuste {item['codigo']} 0."
        )
    supabase.table("itens").delete().eq("id", item["id"]).eq("empresa_id", empresa_id).execute()
    return item, None


def adicionar_item(empresa_id, usuario_id, codigo, nome, maquina, quantidade,
                   preco_custo, localizacao, estoque_minimo):
    """Cadastra um item novo. Sem código informado, o sistema gera um."""
    codigo = (codigo or "").strip().upper() or gerar_codigo(empresa_id)

    maquina = (maquina or "").strip() or None
    localizacao = (localizacao or "").strip() or None

    resposta = supabase.table("itens").insert({
        "empresa_id": empresa_id,
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
            "empresa_id": empresa_id,
            "usuario_id": usuario_id,
            "item_id": item["id"],
            "tipo": "entrada",
            "quantidade": quantidade,
            "saldo_anterior": 0,
            "saldo_novo": quantidade,
            "observacao": "Estoque inicial",
        }).execute()

    return item


def registrar_movimentacao(empresa_id: str, usuario_id: str, codigo: str, tipo: str,
                           quantidade: int, observacao: str = None,
                           origem: str = "whatsapp", custo_unitario: float = None,
                           valor_venda_unitario: float = None):
    """
    Dá entrada ou saída em um item.
    Se for ENTRADA e vier o custo unitário (preço pago por unidade nesta compra), ele é gravado
    no histórico e passa a ser o custo do item (preco_custo = custo da última compra).
    Se for SAÍDA e vier o valor de venda unitário, ele é gravado no histórico (base do faturamento),
    junto com o custo que o item tinha na hora (base do lucro).
    Retorna (item_atualizado, mensagem_de_erro). Um dos dois é None.
    """
    item = buscar_item_por_codigo(empresa_id, codigo)
    if item is None:
        return None, f"Não achei nenhum item com o código {codigo.upper()}."

    saldo_anterior = item["quantidade"]  # guarda ANTES de qualquer alteração

    if tipo == "entrada":
        nova_qtd = saldo_anterior + quantidade
    else:  # saida
        nova_qtd = saldo_anterior - quantidade
        if nova_qtd < 0:
            return None, (
                f"Saldo insuficiente: {item['nome']} tem só "
                f"{saldo_anterior} unidade(s)."
            )

    # Só entrada tem custo de compra. Saída e ajuste ignoram o valor, mesmo que alguém mande.
    usar_custo = tipo == "entrada" and custo_unitario is not None
    usar_venda = tipo == "saida" and valor_venda_unitario is not None

    # Campos extras do histórico (colunas das migrações 008 e 009): só são enviados quando há valor,
    # assim movimentações comuns continuam iguais e não dependem das colunas novas.
    extras = {}
    if usar_custo:
        extras["custo_unitario"] = custo_unitario
    if usar_venda:
        extras["valor_venda_unitario"] = valor_venda_unitario
        extras["custo_unitario"] = float(item["preco_custo"])   # custo do item na hora da venda

    # 1) Atualiza o saldo do item (e o custo, se esta entrada trouxe o preço pago)
    dados_item = {
        "quantidade": nova_qtd,
        "atualizado_em": datetime.now(timezone.utc).isoformat(),
    }
    if usar_custo:
        dados_item["preco_custo"] = custo_unitario
    resposta = (
        supabase.table("itens")
        .update(dados_item)
        .eq("id", item["id"])
        .eq("empresa_id", empresa_id)
        .execute()
    )

    # 2) Grava no histórico: quem fez, quanto, e o saldo antes e depois
    supabase.table("movimentacoes").insert({
        "empresa_id": empresa_id,
        "usuario_id": usuario_id,
        "item_id": item["id"],
        "tipo": tipo,
        "quantidade": quantidade,
        "saldo_anterior": saldo_anterior,
        "saldo_novo": nova_qtd,
        "observacao": observacao,
        "origem": origem,
        **extras,
    }).execute()

    return resposta.data[0], None


def ultimas_entradas(empresa_id: str, item_ids: list):
    """
    Última ENTRADA de cada item da lista (para o /buscar do administrador).
    Devolve {item_id: movimentação}. A carga inicial (estoque inicial/importação) não conta como
    compra, então fica de fora. Itens sem nenhuma entrada real não aparecem no resultado.
    """
    if not item_ids:
        return {}
    movs = (
        supabase.table("movimentacoes")
        .select("*")
        .eq("empresa_id", empresa_id)
        .eq("tipo", "entrada")
        .in_("item_id", list(item_ids))
        .order("criado_em", desc=True)
        .limit(1000)
        .execute()
        .data
    )
    ultimas = {}
    for m in movs:                       # da mais recente para a mais antiga: vale a primeira de cada item
        if m["item_id"] not in item_ids or m["item_id"] in ultimas:
            continue
        if m.get("observacao") == "Estoque inicial" or m.get("origem") == "importacao":
            continue
        ultimas[m["item_id"]] = m
    return ultimas


def ajustar_saldo(empresa_id: str, usuario_id: str, codigo: str, novo_saldo: int,
                  observacao: str = None, origem: str = "whatsapp"):
    """
    Corrige o saldo de um item para um valor exato (ex: contagem física).
    Retorna (item_atualizado, mensagem_de_erro). Um dos dois é None.
    """
    if novo_saldo < 0:
        return None, "O saldo não pode ser negativo."

    item = buscar_item_por_codigo(empresa_id, codigo)
    if item is None:
        return None, f"Não achei nenhum item com o código {codigo.upper()}."

    saldo_anterior = item["quantidade"]
    if novo_saldo == saldo_anterior:
        return None, f"{item['nome']} já está com {saldo_anterior} unidade(s). Nada a ajustar."

    # 1) Atualiza o saldo do item
    resposta = (
        supabase.table("itens")
        .update({
            "quantidade": novo_saldo,
            "atualizado_em": datetime.now(timezone.utc).isoformat(),
        })
        .eq("id", item["id"])
        .eq("empresa_id", empresa_id)
        .execute()
    )

    # 2) Histórico: "quantidade" guarda o tamanho da diferença;
    #    saldo_anterior e saldo_novo mostram se subiu ou desceu
    supabase.table("movimentacoes").insert({
        "empresa_id": empresa_id,
        "usuario_id": usuario_id,
        "item_id": item["id"],
        "tipo": "ajuste",
        "quantidade": abs(novo_saldo - saldo_anterior),
        "saldo_anterior": saldo_anterior,
        "saldo_novo": novo_saldo,
        "observacao": observacao,
        "origem": origem,
    }).execute()

    return resposta.data[0], None


def todos_itens(empresa_id: str):
    """Todos os itens da empresa. O Supabase devolve no máximo 1000 por consulta,
    então buscamos em páginas de 1000 até acabar."""
    itens, inicio = [], 0
    while True:
        pagina = (
            supabase.table("itens")
            .select("*")
            .eq("empresa_id", empresa_id)
            .order("codigo")
            .range(inicio, inicio + 999)
            .execute()
            .data
        )
        itens += pagina
        if len(pagina) < 1000:
            return itens
        inicio += 1000


def resumo_estoque(empresa_id: str):
    """Totais do estoque: itens, unidades, valor investido, itens baixos e zerados."""
    itens = todos_itens(empresa_id)
    return {
        "total_itens": len(itens),
        "total_unidades": sum(i["quantidade"] for i in itens),
        "valor_total": sum(i["quantidade"] * float(i["preco_custo"]) for i in itens),
        "abaixo_minimo": sum(
            1 for i in itens
            if i["estoque_minimo"] > 0 and i["quantidade"] <= i["estoque_minimo"]
        ),
        "zerados": sum(1 for i in itens if i["quantidade"] == 0),
    }


def movimentacoes_periodo(empresa_id: str, dias: int):
    """Movimentações dos últimos N dias (até 1000, o limite de uma consulta)."""
    desde = (datetime.now(timezone.utc) - timedelta(days=dias)).isoformat()
    return (
        supabase.table("movimentacoes")
        .select("*")
        .eq("empresa_id", empresa_id)
        .gte("criado_em", desde)
        .limit(1000)
        .execute()
        .data
    )


def itens_baixos(empresa_id: str):
    """Itens com quantidade igual ou menor que o estoque mínimo."""
    itens = (
        supabase.table("itens")
        .select("*")
        .eq("empresa_id", empresa_id)
        .execute()
        .data
    )
    baixos = [
        i for i in itens
        if i["estoque_minimo"] > 0 and i["quantidade"] <= i["estoque_minimo"]
    ]
    return sorted(baixos, key=lambda i: i["quantidade"])


# =========================================
# HISTÓRICO E USUÁRIOS (usados só pelos comandos de admin)
# =========================================

def historico_movimentacoes(empresa_id: str, codigo: str = None, limite: int = 10):
    """
    Últimas movimentações da empresa (ou de um item, se passar o código).
    Retorna (lista, erro). Cada linha já vem com o item e o nome de quem fez.
    """
    consulta = (
        supabase.table("movimentacoes")
        .select("*")
        .eq("empresa_id", empresa_id)
    )

    if codigo:
        item = buscar_item_por_codigo(empresa_id, codigo)
        if item is None:
            return None, f"Não achei nenhum item com o código {codigo.upper()}."
        consulta = consulta.eq("item_id", item["id"])

    movs = consulta.order("criado_em", desc=True).limit(limite).execute().data
    if not movs:
        return [], None

    # Busca de uma vez os itens e usuários citados, para mostrar nomes em vez de códigos internos
    ids_itens = list({m["item_id"] for m in movs})
    ids_users = list({m["usuario_id"] for m in movs if m.get("usuario_id")})

    itens = {
        i["id"]: i
        for i in supabase.table("itens").select("id,codigo,nome")
        .eq("empresa_id", empresa_id).in_("id", ids_itens).execute().data
    }
    usuarios = {}
    if ids_users:
        usuarios = {
            u["id"]: u["nome"]
            for u in supabase.table("usuarios").select("id,nome")
            .eq("empresa_id", empresa_id).in_("id", ids_users).execute().data
        }

    for m in movs:
        m["item"] = itens.get(m["item_id"])
        m["usuario_nome"] = usuarios.get(m.get("usuario_id"))
    return movs, None


def listar_usuarios(empresa_id: str):
    """Todos os usuários da empresa (ativos e inativos)."""
    resposta = (
        supabase.table("usuarios")
        .select("*")
        .eq("empresa_id", empresa_id)
        .order("nome")
        .execute()
    )
    return resposta.data


def buscar_usuarios_por_nome(empresa_id: str, nome: str):
    """Usuários da empresa cujo nome contém o texto (sem diferenciar maiúsculas)."""
    resposta = (
        supabase.table("usuarios")
        .select("*")
        .eq("empresa_id", empresa_id)
        .ilike("nome", f"%{nome.strip()}%")
        .execute()
    )
    return resposta.data


def definir_usuario_ativo(empresa_id: str, usuario_id: str, ativo: bool):
    """Liga (True) ou desliga (False) o acesso de um usuário."""
    supabase.table("usuarios").update({"ativo": ativo}) \
        .eq("id", usuario_id).eq("empresa_id", empresa_id).execute()


# =========================================
# USO DA IA (para controlar custo)
# =========================================

def registrar_uso_ia(empresa_id, usuario_id, modelo, tokens_entrada, tokens_saida, duracao_ms):
    """Guarda quantos tokens (pedaços de texto) cada mensagem gastou."""
    supabase.table("uso_ia").insert({
        "empresa_id": empresa_id,
        "usuario_id": usuario_id,
        "modelo": modelo,
        "tokens_entrada": tokens_entrada,
        "tokens_saida": tokens_saida,
        "duracao_ms": duracao_ms,
    }).execute()


def uso_ia_desde(empresa_id: str, desde: datetime):
    """Registros de uso da IA a partir de um instante (até 1000)."""
    return (
        supabase.table("uso_ia")
        .select("tokens_entrada,tokens_saida,duracao_ms")
        .eq("empresa_id", empresa_id)
        .gte("criado_em", desde.isoformat())
        .limit(1000)
        .execute()
        .data
    )


# =========================================
# SEGURANÇA E ESTADO (Fase 2)
# =========================================

def marcar_mensagem_processada(wa_message_id: str, empresa_id: str = None) -> bool:
    """
    Anti-duplicidade. A Meta às vezes reenvia a mesma mensagem; cada uma tem um ID único.
    Tenta gravar o ID: se for NOVO, devolve True (pode processar). Se já existia, devolve False
    (é repetida: ignorar). A própria chave primária do banco decide, então funciona mesmo que
    duas cópias cheguem ao mesmo tempo.
    """
    try:
        supabase.table("mensagens_processadas").insert({
            "wa_message_id": wa_message_id,
            "empresa_id": empresa_id,
        }).execute()
        return True
    except Exception as erro:
        texto = str(erro).lower()
        if "23505" in texto or "duplicate" in texto:      # violação de chave única = já vista
            return False
        raise                                              # outro erro: não esconder


def limpar_mensagens_antigas(dias: int = 7):
    """Apaga IDs de mensagens com mais de N dias (a Meta só reenvia nas primeiras horas)."""
    limite = datetime.now(timezone.utc) - timedelta(days=dias)
    supabase.table("mensagens_processadas").delete().lt("criado_em", limite.isoformat()).execute()


def carregar_estado(empresa_id: str, usuario_id: str) -> dict:
    """Estado da conversa desta pessoa (últimas mensagens, confirmação pendente, escolha). {} se não há."""
    r = (
        supabase.table("estado_conversa")
        .select("estado")
        .eq("empresa_id", empresa_id)
        .eq("usuario_id", usuario_id)
        .execute()
    )
    return r.data[0]["estado"] if r.data else {}


def salvar_estado(empresa_id: str, usuario_id: str, estado: dict):
    """Grava (ou substitui) o estado da conversa desta pessoa."""
    supabase.table("estado_conversa").upsert({
        "usuario_id": usuario_id,
        "empresa_id": empresa_id,
        "estado": estado,
        "atualizado_em": datetime.now(timezone.utc).isoformat(),
    }, on_conflict="usuario_id").execute()


def registrar_auditoria(empresa_id: str, usuario_id: str, acao: str, detalhe: dict):
    """Registra uma ação de administração: quem, o quê e quando."""
    supabase.table("auditoria").insert({
        "empresa_id": empresa_id,
        "usuario_id": usuario_id,
        "acao": acao,
        "detalhe": detalhe,
    }).execute()


def limite_ia_da_empresa(empresa_id: str, padrao: int) -> int:
    """Limite diário de mensagens com IA desta empresa (coluna do banco). Usa o padrão se não houver."""
    try:
        valor = (buscar_empresa(empresa_id) or {}).get("limite_ia_diario")
        return valor if isinstance(valor, int) and not isinstance(valor, bool) and valor >= 0 else padrao
    except Exception:
        return padrao


# =========================================
# RESUMO DIÁRIO DA MANHÃ
# =========================================

def empresas_ativas():
    """Empresas com a conta ativa (o resumo da manhã é enviado para cada uma)."""
    return supabase.table("empresas").select("id,nome").eq("ativa", True).execute().data


def reservar_envio_resumo(empresa_id: str, dia: str) -> bool:
    """
    Garante UM resumo por empresa por dia. Tenta gravar (empresa, dia): se for novo, devolve True
    (pode enviar). Se já existia, devolve False. A chave única do banco decide, então funciona mesmo
    que dois pedidos cheguem ao mesmo tempo.
    """
    try:
        supabase.table("resumos_enviados").insert({"empresa_id": empresa_id, "dia": dia}).execute()
        return True
    except Exception as erro:
        texto = str(erro).lower()
        if "23505" in texto or "duplicate" in texto:
            return False
        raise


def liberar_envio_resumo(empresa_id: str, dia: str):
    """Desfaz a reserva do dia (usada quando o envio falhou para todos, para permitir nova tentativa)."""
    supabase.table("resumos_enviados").delete().eq("empresa_id", empresa_id).eq("dia", dia).execute()

