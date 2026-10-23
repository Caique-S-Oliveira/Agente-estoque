Você vai fazer uma auditoria completa de segurança e de conformidade com a LGPD (Lei 13.709/2018) em TODOS os projetos que você desenvolveu para mim, e corrigir os problemas encontrados.

## Objetivo
Eliminar o máximo possível de brechas de segurança e de não conformidades com a LGPD, com foco em zero falhas conhecidas. Como nenhuma auditoria garante ausência total de falhas, ao final você deve declarar explicitamente o que foi verificado, o que foi corrigido e o que ficou pendente e por quê.

## Regras de trabalho
- Antes de qualquer alteração, faça um inventário de todos os projetos (diretórios com código, package.json, requirements.txt, pyproject.toml, Dockerfile, docker-compose, .env*, configs de CI, etc.) e liste-os.
- Trabalhe projeto por projeto. Não avance para o próximo antes de concluir o atual.
- Antes de editar, crie um branch dedicado (ex.: `security-audit-2026-10`) ou um commit de checkpoint, para que cada correção seja revisável e reversível.
- Não quebre funcionalidades. Após cada correção, rode os testes existentes e, se não houver testes, descreva como validar manualmente.
- Não remova dados pessoais ou logs de auditoria sem confirmar comigo antes. Operações destrutivas (apagar dados, reescrever histórico, rotacionar chaves em produção) exigem minha aprovação explícita.
- Nunca imprima segredos reais no chat ou em relatórios. Mascare-os (ex.: `sk_live_****abcd`).
- Se encontrar um problema cuja correção dependa de decisão de negócio ou jurídica, não decida sozinho: registre no relatório com as opções.

## Fase 1 — Varredura automatizada
Use as ferramentas disponíveis (instale as que faltarem, quando possível):
- Segredos no código e no histórico git: gitleaks ou trufflehog. Verifique também arquivos .env commitados.
- Dependências vulneráveis: npm audit, pip-audit, osv-scanner ou equivalente para cada ecossistema.
- Análise estática (SAST): semgrep com regras de segurança, bandit (Python), eslint-plugin-security (JS/TS).
- Containers e infraestrutura: hadolint para Dockerfiles, verificação de usuário root, portas expostas e imagens base desatualizadas.
Salve a saída bruta em `audit/raw/` e não a dê como conclusão.

## Fase 2 — Revisão manual de segurança
Para cada projeto, verifique:
1. Autenticação e autorização: senhas com hash forte (bcrypt/argon2/scrypt), controle de acesso em todas as rotas (inclusive IDOR: um usuário acessar dados de outro trocando um ID), sessões com expiração, tokens com validade curta, proteção contra força bruta (rate limiting, bloqueio progressivo).
2. Injeção: SQL/NoSQL (uso de queries parametrizadas), command injection (`exec`, `os.system`, `shell=True`), template injection, path traversal em uploads e downloads, SSRF em qualquer requisição feita a URLs fornecidas pelo usuário.
3. XSS e CSRF: escape de saída, uso de `innerHTML`/`dangerouslySetInnerHTML` sem sanitização, proteção CSRF em formulários e endpoints que alteram estado, cookies com `HttpOnly`, `Secure` e `SameSite`.
4. Validação de entrada: schema validation em todos os endpoints, limites de tamanho de payload, validação de tipo de arquivo em uploads (por conteúdo, não só extensão), limite de tamanho de upload.
5. Configuração: CORS restritivo (sem `*` com credenciais), headers de segurança (CSP, HSTS, X-Content-Type-Options, X-Frame-Options ou frame-ancestors), modo debug desligado em produção, mensagens de erro sem stack trace para o usuário, `.env` fora do controle de versão, `.gitignore` adequado.
6. Criptografia e transporte: HTTPS obrigatório, sem algoritmos obsoletos (MD5, SHA1 para senhas, DES), tokens gerados com gerador criptográfico (`secrets`, `crypto.randomBytes`), não usar `Math.random` para segurança.
7. Logs: não registrar senhas, tokens, números de cartão, CPF completo ou corpo de requisições sensíveis.
8. Dependências e runtime: versões sem CVEs conhecidas críticas ou altas, containers sem root, mínimo de privilégios em credenciais de banco e de serviços externos.
9. Integrações de terceiros: webhooks com verificação de assinatura, chaves de API no servidor e não no frontend.

## Fase 3 — Conformidade com a LGPD
Para cada projeto, identifique e documente:
1. Inventário de dados pessoais: quais dados são coletados (nome, e-mail, CPF, telefone, endereço, IP, cookies, geolocalização, dados sensíveis do art. 5º, II, como saúde, biometria, religião, etc.), em quais campos, tabelas, arquivos e logs.
2. Base legal (art. 7º e 11º): para cada tratamento, qual base se aplica e se está documentada.
3. Finalidade e necessidade (princípios do art. 6º): coletar apenas o necessário. Remova campos desnecessários e marque os que devem ser justificados.
4. Consentimento: quando for a base utilizada, deve ser livre, informado e inequívoco, com registro da obtenção e possibilidade de revogação. Checks pré-marcados não são válidos.
5. Transparência: existe política de privacidade acessível, com controlador, finalidades, compartilhamentos, prazo de retenção e canal de contato? Crie ou atualize se faltar, com base nos dados reais do projeto.
6. Direitos do titular (arts. 18 a 20): verifique se é possível, no sistema, acessar, corrigir, exportar (portabilidade), anonimizar, bloquear ou eliminar dados, e revogar consentimento. Se não houver funcionalidade, proponha ou implemente.
7. Retenção e eliminação (art. 15 e 16): defina prazos de retenção por tipo de dado e implemente rotina de exclusão ou anonimização. Dados não podem ficar indefinidamente.
8. Minimização em ambientes não produtivos: dados reais não devem ir para staging, testes ou logs sem anonimização.
9. Compartilhamento e operadores: liste terceiros que recebem dados (APIs, analytics, e-mail, nuvem). Verifique se há contrato ou cláusula de proteção e se há transferência internacional, que exige base adequada (art. 33).
10. Segurança dos dados (art. 46 a 49): criptografia em repouso para dados pessoais sensíveis, controle de acesso por necessidade, trilha de auditoria para acessos.
11. Incidentes (art. 48): verifique se há plano ou ao menos registro para detectar, registrar e comunicar incidentes. Documente o procedimento de comunicação à ANPD e aos titulares.
12. Cookies e rastreadores: liste cookies de terceiros e de rastreamento, e verifique se o banner de consentimento é efetivo (não carrega rastreadores antes do aceite).
13. Encarregado (DPO) e canal de contato: verifique se há e-mail ou formulário público para titulares.

## Fase 4 — Correção
- Corrija por ordem de severidade: crítica, alta, média, baixa.
- Para cada correção: explique a causa, aplique a mudança mínima necessária, adicione ou ajuste teste que demonstre a correção quando possível, e registre o commit.
- Onde a correção exigir mudança de comportamento visível ao usuário (ex.: exigir senha forte, pedir novo consentimento), sinalize antes de implementar.
- Não use "desabilitar a verificação" como solução. Corrija a causa.

## Fase 5 — Verificação
- Rode novamente as ferramentas da Fase 1 e confirme que os achados foram resolvidos ou justificados.
- Rode os testes de cada projeto e confirme que nada regrediu.
- Para cada correção crítica ou alta, descreva como testar manualmente.

## Entrega
Gere um relatório em `audit/RELATORIO_SEGURANCA_LGPD.md` com:
1. Resumo executivo: quantos projetos, quantos achados por severidade, quantos corrigidos, quantos pendentes.
2. Tabela por projeto: achado, severidade, arquivo e linha, status (corrigido, pendente, aceito com justificativa), commit relacionado.
3. Seção LGPD: inventário de dados pessoais por projeto, bases legais, lacunas e ações tomadas.
4. Pendências que exigem decisão humana ou assessoria jurídica.
5. Limitações da auditoria: o que não foi possível verificar (ex.: infraestrutura em produção, políticas de provedores de nuvem, contratos).
6. Recomendações de médio prazo: testes de penetração externos, revisão jurídica da política de privacidade e dos contratos, treinamento da equipe.

Ao final, responda no chat com um resumo curto (máximo 15 linhas) e o caminho do relatório. Não me entregue apenas uma lista de sugestões: faça as correções.