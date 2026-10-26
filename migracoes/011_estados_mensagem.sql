-- 011: estados de processamento do webhook (plano P0-05). Linhas antigas viram 'concluida'.
alter table mensagens_processadas add column if not exists estado text not null default 'concluida';
alter table mensagens_processadas add column if not exists tentativas int not null default 1;
alter table mensagens_processadas add column if not exists atualizado_em timestamptz not null default now();
alter table mensagens_processadas drop constraint if exists mensagens_processadas_estado_ck;
alter table mensagens_processadas add constraint mensagens_processadas_estado_ck
    check (estado in ('processando', 'concluida', 'falhou'));
