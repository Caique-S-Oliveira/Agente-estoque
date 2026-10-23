# Registro de tratamento de dados pessoais (RASCUNHO)

> Rascunho técnico (auditoria LGPD, itens L-01, L-04, L-06). Bases legais, papéis e prazos são **sugestões** e precisam de decisão sua e revisão jurídica. Campos `[PREENCHER]` dependem de você.

**Controlador / operador:** `[PREENCHER]` (em regra a empresa-cliente é controladora e quem opera o sistema é operador).
**Canal do titular / encarregado:** `[PREENCHER e-mail]`

| Dado | Finalidade | Base legal sugerida | Onde fica | Retenção sugerida | Compartilhado com |
|---|---|---|---|---|---|
| Telefone e nome do usuário | Autorizar o acesso e identificar quem fez cada ação | Art. 7º, V ou IX | `usuarios` | Enquanto ativo; anonimizar X meses após desativar | Supabase, Meta |
| Texto das mensagens | Operar o estoque por conversa; diagnóstico | Art. 7º, IX (necessidade a justificar) | `mensagens`, `estado_conversa` | 30–90 dias (mensagens); 24 h–7 dias (estado) | Supabase, Meta, Anthropic (texto, sem telefone) |
| Movimentações com usuário | Histórico de estoque | Art. 7º, IX | `movimentacoes` | Manter; anonimizar o usuário ao sair | Supabase |
| Auditoria de administração | Segurança e prestação de contas | Art. 7º, IX | `auditoria` | 5 anos (confirmar) | Supabase |
| Uso da IA | Controle de custo | Art. 7º, IX | `uso_ia` | 12 meses | Supabase |
| ID da mensagem | Evitar duplicidade | Art. 7º, IX | `mensagens_processadas` | 7 dias (já automático) | Supabase |

## Operadores e transferência internacional (art. 33)
| Terceiro | Dados | País | Mecanismo / DPA |
|---|---|---|---|
| Supabase | Todos | `[VERIFICAR região]` | `[PREENCHER]` |
| Render | Tráfego e logs | `[VERIFICAR]` | `[PREENCHER]` |
| Meta (WhatsApp) | Telefone e mensagens | EUA/global | `[PREENCHER]` |
| Anthropic | Texto da mensagem, histórico recente, catálogo código/nome | EUA | `[PREENCHER]` |

## Aviso ao usuário (texto sugerido para o bot, a aprovar antes de enviar)
"Este assistente registra suas mensagens e ações para operar o estoque e manter a segurança, e usa uma IA (Anthropic, EUA) para interpretá-las. Dúvidas ou pedidos sobre seus dados: `[e-mail]`."
