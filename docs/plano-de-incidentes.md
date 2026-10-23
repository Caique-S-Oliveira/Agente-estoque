# Plano de resposta a incidentes de segurança (RASCUNHO)

> Rascunho técnico (auditoria LGPD, item L-05). Os campos `[PREENCHER]` dependem de decisão sua; revise com assessoria jurídica antes de adotar.

## 1. O que é um incidente
Qualquer acesso, perda, alteração ou divulgação indevida de dados pessoais ou de credenciais. Exemplos: chave do Supabase/Meta/Anthropic vazada, `.env` ou backup `.zip` perdido, mensagem enviada ao número errado, acesso de uma empresa aos dados de outra.

## 2. Como detectar
- Logs do Render (erros 403 repetidos no `/webhook`, picos de mensagens).
- Alertas do Supabase, do painel da Meta e da Anthropic (uso anormal).
- Aviso de usuário/empresa-cliente.

## 3. Primeiras ações (primeiras horas)
1. Registrar data/hora, quem detectou e o que foi observado (tabela abaixo).
2. Conter: desativar o usuário ou a empresa afetada (`ativo=False`); retirar `CRON_TOKEN` no Render para desligar a rota do cron, se for o caso.
3. Se houver chave exposta: **rotacionar** (Supabase service_role, `WHATSAPP_TOKEN`, `META_APP_SECRET`, `VERIFY_TOKEN`, `ANTHROPIC_API_KEY`, `CRON_TOKEN`), atualizar no Render e no `.env`. Rotação em produção só com aprovação do responsável.
4. Avaliar: quais dados, de quantas pessoas, risco ou dano relevante?

## 4. Comunicação
- Se atuamos como **operador**: avisar a empresa-cliente (controladora) imediatamente: `[PREENCHER prazo e canal]`.
- Se houver risco ou dano relevante aos titulares: o controlador comunica a **ANPD e os titulares** em prazo razoável (a ANPD indica 3 dias úteis; confirmar com o jurídico, Res. CD/ANPD nº 15/2024). Conteúdo mínimo: natureza dos dados, titulares afetados, medidas de proteção, riscos, medidas adotadas.

## 5. Registro interno (mesmo quando não houver comunicação)
| Data | Detectado por | Descrição | Dados/titulares afetados | Risco | Ações | Comunicado a (ANPD/titulares/cliente) |
|---|---|---|---|---|---|---|

## 6. Responsáveis
- Responsável pelo incidente: `[PREENCHER]`
- Contato com a ANPD / encarregado: `[PREENCHER]`
