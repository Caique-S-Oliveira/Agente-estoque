# Agente de Estoque (WhatsApp) — como funciona hoje e como deve ficar no plano final

**Escopo:** só o *agente* (o assistente que conversa pelo WhatsApp). Painel, planos e cobrança do SaaS aparecem apenas como "futuro".
**Atualizado em:** 07/10/2026 · **Cliente piloto:** VTech Embalagens (início 05/10/2026)

---

## 1. O que é o agente, em uma frase

Uma pessoa da empresa manda uma mensagem no WhatsApp ("saiu 5 fita azul", "quanto tem de fita pet?") e o agente **entende, confere, registra no banco e responde**. Também manda ao dono, uma vez por dia, um resumo do estoque.

**Regra de ouro do projeto:** a IA *interpreta* a frase; o código *valida e executa*. A IA nunca escreve direto no banco.

---

## 2. Como funciona HOJE

### 2.1 O caminho de uma mensagem

```
WhatsApp da pessoa
      │
      ▼
Meta (WhatsApp Cloud API) ──► /webhook  (main.py)
      │   1. confere a assinatura da Meta (anti-falsificação)
      │   2. ignora mensagem repetida (ID já visto)
      ▼
agente.py  ── quem é a pessoa? ativa? empresa ativa? cargo?
      │
      ├─ começa com "/"  → comando fixo (custa R$ 0, não usa IA)
      │
      └─ texto livre → cerebro.py
             ├─ 1º) atalho local (código): "saiu 5 fita azul", "ajusta X para 20",
             │       respostas a "Qual deles?", "sim/não"  → custo ZERO, resultado sempre igual
             └─ 2º) IA (Claude Haiku 4.5): só para o que o atalho não resolve
                    (consultas, várias coisas na mesma frase, cadastro, linguagem livre)
      ▼
database.py (Supabase) grava a movimentação  →  whatsapp.py responde
```

### 2.2 O que a pessoa pode fazer

| Ação | Exemplo em linguagem natural | Comando equivalente |
|---|---|---|
| Entrada | "entrou 20 fita pet custo 3,50" | `/entrada CODIGO QTD [custo X]` |
| Saída | "saiu 2 fita pet" | `/saida CODIGO QTD` |
| Corrigir saldo | "ajusta fita pet para 21" | `/ajuste CODIGO NOVO_SALDO` |
| Consultar | "quanto tem de fita branca?" | `/buscar termo` (ou `/busca`) |
| Ver o estoque | — | `/estoque`, `/baixo`, `/itens`, `/relatorio [dias]` |
| Cadastrar | pelo agente | `/novo` |

**Só administradores:** `/editar`, `/historico`, `/usuarios`, `/desativar`, `/reativar`, `/remover`, `/uso` (gasto de IA) e `/resumo`.

### 2.3 Cargos

- **Operador:** movimenta e consulta. `/buscar` mostra nome, código, quantidade, mínimo, valor sugerido, máquina e local.
- **Admin:** tudo isso, mais, no `/buscar`: **custo unitário, valor em estoque (qtd × custo) e última entrada** (data, quantidade e custo).
- A checagem de cargo está **no código que executa**, nunca nas instruções da IA. Ninguém desativa a si mesmo.

### 2.4 Custo na entrada e alerta de variação

- A entrada aceita o custo unitário ("custo 3,50"). Ele fica gravado na movimentação e vira o **"último custo"** do item.
- Se o novo custo diferir **5% ou mais** do anterior, o agente avisa: *"O custo do produto X subiu/caiu em N%"*.
- Só a entrada aceita custo. Custo inválido é ignorado (a entrada é feita mesmo assim).

### 2.5 Proteções (o que impede o agente de errar ou mentir)

- **Confirmação "sim/não"** (vale 5 min, só para quem pediu): para quantidade acima de 50 e para **todo** ajuste de saldo.
- **Saída maior que o saldo** é recusada na hora.
- **Itens parecidos** (ex.: fita branca 10mm em 8 kg, 10 kg, 12 kg): o agente pergunta **"Qual deles?"**. A pergunta pendente fica no banco e sobrevive a reinício do servidor.
- **Trava anti-mentira:** se a IA responder "ajustei para 21" sem ter executado nenhuma ação, o texto é trocado por um aviso. O saldo só muda quando uma ferramenta do código executa.
- **Segurança:** assinatura da Meta validada, mensagens repetidas ignoradas, endpoint de teste desligado em produção, isolamento entre empresas (`empresa_id` em toda consulta, com teste automático), auditoria de ações de admin, limite diário de IA por empresa e bloqueio de empresa suspensa.
- **Erros:** qualquer falha vira uma resposta amigável; o servidor não cai.

### 2.6 Resumo da manhã (sem IA)

- O cron-job.org chama `/cron/resumo` (protegido por senha `CRON_TOKEN`), de **segunda a sábado**, de manhã.
- Conteúdo: **estoque baixo** (itens no mínimo ou abaixo), **movimentações de ontem** (na segunda, sábado + domingo) e **itens parados** há 60+ dias.
- Vai para os **administradores ativos**, **uma vez por dia por empresa** (trava no banco).
- **Limite da Meta:** texto livre só vale dentro de 24 h da última mensagem da pessoa. Passou disso (ex.: segunda de manhã), o envio é recusado; o plano B é um **modelo de mensagem aprovado** com o botão "Ver resumo" (ainda **não criado**). Enquanto isso, o dono digita `/resumo`.
- O comando `/resumo` mostra o mesmo texto a qualquer hora.

### 2.7 Custo e hospedagem no piloto

| Item | Custo |
|---|---|
| IA (Haiku 4.5) | ~US$ 1–3 por mês (frases simples nem chamam a IA) |
| Supabase | grátis |
| WhatsApp (respostas) | grátis no volume atual (~300/mês) |
| Render | grátis; dorme após 15 min parado, por isso o "despertador" no cron-job.org |

### 2.8 Limitações conhecidas hoje

- **Só texto.** Áudio recebe uma resposta educada pedindo para escrever.
- **Saída não guarda valor de venda:** em "saiu 2 fita pet por 330" o 330 é descartado.
- **Sem alerta imediato** de estoque baixo: o aviso vem no resumo da manhã.
- **Resumo na segunda** pode não chegar sozinho (janela de 24 h) até existir o modelo de mensagem.
- **Servidor grátis** pode demorar ~1 min na primeira mensagem após ficar parado.
- Busca ainda por texto simples (sem apelidos de itens).

---

## 3. Como deve funcionar no PLANO FINAL

> Tudo desta seção é **plano**, depende do resultado do piloto e do gate de validação (meados de novembro). Nada aqui está implementado.

### 3.1 Depois do piloto (refino do agente)

| Melhoria | O que muda |
|---|---|
| **Valor de venda na saída** | "saiu 2 fita pet a 165" grava o **valor unitário**. Faturamento = Σ(qtd × valor) no `/relatorio` do admin, com margem sobre o custo. Decidir: aviso quando o número parecer total; quem vê valores. |
| **Apelidos e busca aproximada** | "fita pet", "pet azul" e "FT-PET-AZ" levam ao mesmo item; erros de digitação toleráveis. |
| **Alerta proativo de estoque baixo** | Aviso ao dono assim que um item cruza o mínimo (dentro da janela de 24 h; fora dela, só com modelo aprovado e pago). |
| **Modelo de mensagem aprovado** | Resolve o resumo de segunda e os avisos fora da janela de 24 h. |
| **Alerta de erro** | Aviso ao dono do produto quando o WhatsApp ou a IA falharem. |
| **Backup com restauração** | Hoje o backup existe; a restauração é manual. Criar script e testar. |
| **Cartilha de uma tela** | Guia rápido para a equipe usar o agente. |

### 3.2 Se o gate aprovar (produto para vários clientes)

- **Multiempresa de verdade:** uma aplicação única; cada empresa com seu número de WhatsApp (provável modelo "um chip por cliente, configurado por nós", conta da Meta no nome do cliente — confirmar regras vigentes).
- **Hospedagem paga** (~US$ 7/mês), sempre ligada, sem o atraso de acordar.
- **Áudio** (transcrição) como recurso de plano superior.
- **Planos, limites e cobrança** por empresa (a base já existe: limite de IA e plano por empresa).
- **Painel do cliente e painel administrativo** (métricas de uso, custo de IA por empresa).
- **Mais inteligência:** previsão de demanda, sugestão de compra, leitura de notas fiscais, ordens de serviço.

### 3.3 O que NÃO muda (princípios)

1. A IA interpreta; **o código valida e executa**.
2. Frase simples **não gasta IA** (atalho local); IA só no que o código não resolve.
3. Comandos `/` continuam como plano B, sempre a custo zero.
4. Ações sensíveis (ajuste, quantidade grande) **pedem confirmação**.
5. Permissão por cargo **no código**, dados separados **por `empresa_id`**.
6. Custo de IA baixo e **medido** (`/uso`).

---

## 4. Mapa dos arquivos do agente

| Arquivo | Papel |
|---|---|
| `main.py` (raiz) | Atalho que aponta para `app.main`, para o deploy `uvicorn main:app` continuar valendo |
| `app/main.py` | Porta de entrada: webhook da Meta, segurança, rota do cron do resumo |
| `app/agente.py` | Identifica a pessoa, comandos `/`, permissões, formatação das respostas |
| `app/cerebro.py` | Atalhos locais, chamada da IA, ferramentas, confirmações, trava anti-mentira |
| `app/resumo.py` | Resumo da manhã (sem IA) e envio com plano B de modelo |
| `app/database.py` | Todas as consultas ao Supabase (sempre filtradas por `empresa_id`) |
| `app/whatsapp.py` | Envio de texto e de modelo pela Meta |
| `migracoes/` | SQLs numerados do banco (008 custo unitário, 009 resumos enviados) |
| `tests/testar_*.py` | Testes de frases, de isolamento entre empresas e do webhook |
| `scripts/` | Ferramentas de apoio (importação inicial e backup) |
