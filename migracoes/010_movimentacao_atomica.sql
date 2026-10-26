-- 010: movimentacoes atomicas e idempotentes (plano P0-01, P0-02, P0-03).
-- REVISAR E APLICAR PRIMEIRO EM UM PROJETO SUPABASE DE TESTE. Nao altera nem apaga dados existentes.
-- Premissas (confirmar com `pg_dump --schema-only`): ids sao uuid; itens(id, empresa_id, codigo, quantidade,
-- preco_custo, atualizado_em); movimentacoes tem custo_unitario e valor_venda_unitario (migracoes 008/009).

-- Colunas usadas pela funcao (nao fazem nada se ja existirem; ficam vazias nas movimentacoes antigas).
alter table movimentacoes add column if not exists custo_unitario numeric;
alter table movimentacoes add column if not exists valor_venda_unitario numeric;
alter table movimentacoes add column if not exists chave_idempotencia text;
create unique index if not exists movimentacoes_chave_uq
    on movimentacoes (empresa_id, chave_idempotencia) where chave_idempotencia is not null;

create or replace function movimentar_estoque(
    p_empresa_id uuid, p_usuario_id uuid, p_codigo text, p_tipo text, p_quantidade int,
    p_observacao text, p_origem text, p_custo_unitario numeric, p_valor_venda_unitario numeric,
    p_chave text
) returns jsonb
language plpgsql security definer set search_path = public as $$
declare
    v_item itens%rowtype;
    v_mov  movimentacoes%rowtype;
    v_novo int;
begin
    if p_tipo not in ('entrada', 'saida') or p_quantidade is null or p_quantidade <= 0 then
        return jsonb_build_object('erro', 'pedido_invalido');
    end if;

    -- Idempotencia: mesma chave = mesmo efeito. Devolve o que ja foi gravado, sem repetir.
    if p_chave is not null then
        perform pg_advisory_xact_lock(hashtext(p_empresa_id::text || p_chave));
        select * into v_mov from movimentacoes
         where empresa_id = p_empresa_id and chave_idempotencia = p_chave;
        if found then
            select * into v_item from itens where id = v_mov.item_id and empresa_id = p_empresa_id;
            return jsonb_build_object('item', to_jsonb(v_item), 'repetida', true);
        end if;
    end if;

    -- Bloqueia a linha do item: movimentacoes simultaneas do mesmo item passam uma de cada vez.
    select * into v_item from itens
     where empresa_id = p_empresa_id and codigo = upper(btrim(p_codigo)) for update;
    if not found then
        return jsonb_build_object('erro', 'item_nao_encontrado');
    end if;

    if p_tipo = 'entrada' then
        v_novo := v_item.quantidade + p_quantidade;
    else
        v_novo := v_item.quantidade - p_quantidade;
        if v_novo < 0 then
            return jsonb_build_object('erro', 'saldo_insuficiente',
                                      'nome', v_item.nome, 'saldo', v_item.quantidade);
        end if;
    end if;

    insert into movimentacoes (empresa_id, usuario_id, item_id, tipo, quantidade, saldo_anterior,
                               saldo_novo, observacao, origem, custo_unitario, valor_venda_unitario,
                               chave_idempotencia)
    values (p_empresa_id, p_usuario_id, v_item.id, p_tipo, p_quantidade, v_item.quantidade, v_novo,
            p_observacao, coalesce(p_origem, 'whatsapp'),
            case when p_tipo = 'entrada' then p_custo_unitario
                 when p_valor_venda_unitario is not null then v_item.preco_custo end,
            case when p_tipo = 'saida' then p_valor_venda_unitario end,
            p_chave);

    update itens
       set quantidade = v_novo,
           preco_custo = case when p_tipo = 'entrada' and p_custo_unitario is not null
                              then p_custo_unitario else preco_custo end,
           atualizado_em = now()
     where id = v_item.id and empresa_id = p_empresa_id
     returning * into v_item;

    return jsonb_build_object('item', to_jsonb(v_item));
end $$;

create or replace function ajustar_saldo_tx(
    p_empresa_id uuid, p_usuario_id uuid, p_codigo text, p_novo_saldo int,
    p_observacao text, p_origem text, p_chave text
) returns jsonb
language plpgsql security definer set search_path = public as $$
declare
    v_item itens%rowtype;
    v_mov  movimentacoes%rowtype;
begin
    if p_novo_saldo is null or p_novo_saldo < 0 then
        return jsonb_build_object('erro', 'saldo_negativo');
    end if;

    if p_chave is not null then
        perform pg_advisory_xact_lock(hashtext(p_empresa_id::text || p_chave));
        select * into v_mov from movimentacoes
         where empresa_id = p_empresa_id and chave_idempotencia = p_chave;
        if found then
            select * into v_item from itens where id = v_mov.item_id and empresa_id = p_empresa_id;
            return jsonb_build_object('item', to_jsonb(v_item), 'repetida', true);
        end if;
    end if;

    select * into v_item from itens
     where empresa_id = p_empresa_id and codigo = upper(btrim(p_codigo)) for update;
    if not found then
        return jsonb_build_object('erro', 'item_nao_encontrado');
    end if;
    if v_item.quantidade = p_novo_saldo then
        return jsonb_build_object('erro', 'sem_mudanca', 'nome', v_item.nome, 'saldo', v_item.quantidade);
    end if;

    insert into movimentacoes (empresa_id, usuario_id, item_id, tipo, quantidade, saldo_anterior,
                               saldo_novo, observacao, origem, chave_idempotencia)
    values (p_empresa_id, p_usuario_id, v_item.id, 'ajuste', abs(p_novo_saldo - v_item.quantidade),
            v_item.quantidade, p_novo_saldo, p_observacao, coalesce(p_origem, 'whatsapp'), p_chave);

    update itens set quantidade = p_novo_saldo, atualizado_em = now()
     where id = v_item.id and empresa_id = p_empresa_id
     returning * into v_item;

    return jsonb_build_object('item', to_jsonb(v_item));
end $$;

-- So o backend (service_role) pode chamar; anon/authenticated nao.
revoke all on function movimentar_estoque(uuid, uuid, text, text, int, text, text, numeric, numeric, text) from public, anon, authenticated;
revoke all on function ajustar_saldo_tx(uuid, uuid, text, int, text, text, text) from public, anon, authenticated;
grant execute on function movimentar_estoque(uuid, uuid, text, text, int, text, text, numeric, numeric, text) to service_role;
grant execute on function ajustar_saldo_tx(uuid, uuid, text, int, text, text, text) to service_role;
