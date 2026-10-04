"""
Importa o ESTOQUE INICIAL de uma empresa a partir de uma planilha (Excel .xlsx ou .csv).
Não usa IA: custo zero em tokens. Roda no SEU computador (usa a chave do Supabase do seu .env),
então só você consegue importar. Ninguém importa pelo WhatsApp.

Como usar (3 passos):

  1) Gerar a planilha modelo para o cliente preencher:
        python importar_estoque.py --modelo

  2) PRÉVIA (não grava nada): mostra o que seria cadastrado e o que tem problema:
        python importar_estoque.py estoque_do_cliente.xlsx --empresa "VTech Embalagens"

  3) Se a prévia estiver boa, rode de novo com --confirmar para gravar:
        python importar_estoque.py estoque_do_cliente.xlsx --empresa "VTech Embalagens" --confirmar

Regras:
  - Nome repetido (na planilha ou já cadastrado no sistema): PULA e lista. Rodar duas vezes é seguro.
  - Sem preço de custo: aceita e grava 0 (lista quantos ficaram assim).
  - Linha com problema (nome vazio, quantidade inválida...): não grava e lista o motivo.
  - Tudo que foi pulado ou deu problema vai para o arquivo pendencias_importacao.csv.
  - Cada item entra com o movimento "Estoque inicial" no histórico, em nome de um administrador.
"""
import argparse
import csv
import html
import re
import sys
import unicodedata
from datetime import datetime
from pathlib import Path

# Cabeçalhos aceitos para cada coluna (comparados sem acento e sem maiúsculas)
COLUNAS = {
    "nome": {"nome", "item", "produto", "descricao", "descricao do item", "nome do produto"},
    "quantidade": {"quantidade", "qtd", "qtde", "estoque", "saldo", "qt. estoque", "qt estoque"},
    "preco_custo": {"preco de custo", "preco custo", "custo", "preco", "valor", "valor de custo"},
    "minimo": {"estoque minimo", "minimo", "min", "qt. minima", "qt minima"},
    "local": {"local", "localizacao", "prateleira"},
    "maquina": {"maquina", "maquina compativel", "equipamento", "observacao"},
    "codigo": {"codigo", "cod", "sku"},
}
MAX_LINHAS = 5000          # trava de segurança: planilhas maiores devem ser divididas


def normalizar(texto) -> str:
    """Minúsculo, sem acento e sem espaços repetidos (para comparar cabeçalhos e nomes)."""
    sem = unicodedata.normalize("NFKD", str(texto or "")).encode("ascii", "ignore").decode()
    return " ".join(sem.lower().split())


def nome_parecido(a: str, b: str) -> bool:
    """Quase igual E com os mesmos números (8kg e 10kg não contam como parecidos)."""
    from difflib import SequenceMatcher
    if re.findall(r"\d+", a) != re.findall(r"\d+", b):
        return False
    return SequenceMatcher(None, a, b).ratio() >= 0.9


# ---------------------------------------------------------------- leitura do arquivo
def ler_planilha(caminho: str) -> list:
    """Lê .xlsx ou .csv e devolve uma lista de linhas (cada linha é uma lista de células)."""
    arquivo = Path(caminho)
    if not arquivo.exists():
        raise SystemExit(f"❌ Arquivo não encontrado: {caminho}")
    if arquivo.suffix.lower() in (".xlsx", ".xlsm"):
        from openpyxl import load_workbook          # instale com: pip install openpyxl
        livro = load_workbook(arquivo, read_only=True, data_only=True)
        aba = livro.worksheets[0]                    # primeira aba
        return [list(l) for l in aba.iter_rows(values_only=True)]
    if arquivo.suffix.lower() == ".csv":
        for codificacao in ("utf-8-sig", "latin-1"):    # Excel brasileiro costuma salvar em latin-1
            try:
                # html.unescape: alguns sistemas gravam aspas como "&quot;" (o ";" quebraria a linha)
                texto = html.unescape(arquivo.read_text(encoding=codificacao))
                break
            except UnicodeDecodeError:
                continue
        primeira = texto.splitlines()[0] if texto.strip() else ""
        separador = max(";,\t", key=primeira.count)     # o separador mais frequente no cabeçalho
        return [list(l) for l in csv.reader(texto.splitlines(), delimiter=separador)]
    raise SystemExit("❌ Use um arquivo .xlsx ou .csv.")


def achar_cabecalho(linhas: list):
    """Acha a linha do cabeçalho (nas 10 primeiras) e devolve (índice da linha, {coluna: posição})."""
    for i, linha in enumerate(linhas[:10]):
        mapa = {}
        for posicao, celula in enumerate(linha):
            nome = normalizar(celula)
            for coluna, apelidos in COLUNAS.items():
                if nome in apelidos and coluna not in mapa:
                    mapa[coluna] = posicao
        if "nome" in mapa:
            return i, mapa
    raise SystemExit("❌ Não achei a coluna 'Nome' no cabeçalho. Use o modelo (python importar_estoque.py --modelo).")


# ---------------------------------------------------------------- conversão de valores
def _vazio(v) -> bool:
    return v is None or str(v).strip() == ""


def _inteiro(v):
    """Converte a célula em inteiro >= 0. Devolve (valor, erro).
    Entende "1.500" (milhar), "1,5" (decimal) e rejeita fracionados, que o sistema não usa."""
    if _vazio(v):
        return None, "quantidade vazia"
    if isinstance(v, bool):
        return None, "quantidade inválida"
    if isinstance(v, (int, float)):
        numero = float(v)
    else:
        t = str(v).strip().replace(" ", "")
        if re.fullmatch(r"-?\d{1,3}(\.\d{3})+", t):          # 1.500 ou -150.000 = milhar
            t = t.replace(".", "")
        else:
            t = t.replace(".", "").replace(",", ".") if "," in t else t
        try:
            numero = float(t)
        except ValueError:
            return None, f"quantidade inválida ('{v}')"
    if abs(numero) > 1_000_000:
        return None, f"quantidade fora do limite ('{v}')"
    if not numero.is_integer():
        return None, f"quantidade fracionada ('{v}'): o sistema só usa números inteiros"
    return int(numero), None


def _dinheiro(v):
    """Converte a célula em preço >= 0 (vazio vira 0). Devolve (valor, erro)."""
    if _vazio(v):
        return 0.0, None
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        numero = float(v)
    else:
        limpo = str(v).strip().replace("R$", "").replace(" ", "")
        if "," in limpo:                                   # 1.234,56 -> 1234.56
            limpo = limpo.replace(".", "").replace(",", ".")
        try:
            numero = float(limpo)
        except ValueError:
            return None, f"preço de custo inválido ('{v}')"
    if numero < 0:
        return None, "preço de custo negativo"
    return round(numero, 2), None


def _texto(v) -> str:
    return " ".join(str(v).replace(";", " ").split()) if not _vazio(v) else ""


# ---------------------------------------------------------------- análise (sem banco)
def analisar(linhas: list, existentes: list, codigo_manual: bool,
             negativos: str = "zero", repetidos: str = "codigo"):
    """
    Confere a planilha inteira SEM gravar nada. Devolve um dicionário com:
      prontos    -> linhas boas, na forma que o banco aceita
      pendencias -> o que foi pulado ou tem problema: (linha, nome, tipo, motivo)
      avisos     -> nomes parecidos e itens sem custo (informativo)
    `existentes` são os itens que a empresa já tem no sistema.
    negativos: "zero" (entra com saldo 0 e avisa) ou "pular".
    repetidos: "codigo" (nomes repetidos na planilha ganham o código antigo no nome, ex.: "MOLA (T7-1)")
               ou "pular" (entra o primeiro, os outros ficam na lista).
    """
    inicio, mapa = achar_cabecalho(linhas)
    dados = linhas[inicio + 1:]
    if len(dados) > MAX_LINHAS:
        raise SystemExit(f"❌ A planilha tem {len(dados)} linhas (máximo {MAX_LINHAS}). Divida em arquivos menores.")
    if codigo_manual and "codigo" not in mapa:
        raise SystemExit("❌ Esta empresa usa códigos digitados: a planilha precisa da coluna 'Código'.")

    celula = lambda linha, coluna: linha[mapa[coluna]] if coluna in mapa and mapa[coluna] < len(linha) else None
    nomes_existentes = {normalizar(i["nome"]): i for i in existentes}
    codigos_existentes = {str(i["codigo"]).upper() for i in existentes}
    vistos_nomes, vistos_codigos = {}, set()
    # nomes que aparecem mais de uma vez na própria planilha (para o modo "codigo")
    contagem = {}
    for linha in dados:
        chave_nome = normalizar(_texto(celula(linha, "nome")))
        if chave_nome:
            contagem[chave_nome] = contagem.get(chave_nome, 0) + 1
    prontos, pendencias, avisos = [], [], []

    for deslocamento, linha in enumerate(dados):
        numero_linha = inicio + 2 + deslocamento                  # número da linha como no Excel
        if all(_vazio(c) for c in linha):
            continue                                              # linha em branco
        nome = _texto(celula(linha, "nome"))
        if not nome:
            pendencias.append((numero_linha, "", "problema", "nome vazio"))
            continue
        if len(nome) > 120:
            pendencias.append((numero_linha, nome[:40] + "…", "problema", "nome com mais de 120 letras"))
            continue
        quantidade, erro = _inteiro(celula(linha, "quantidade"))
        if erro:
            pendencias.append((numero_linha, nome, "problema", erro)); continue
        if quantidade < 0:
            if negativos == "pular":
                pendencias.append((numero_linha, nome, "pulado", f"saldo negativo ({quantidade})")); continue
            pendencias.append((numero_linha, nome, "ajustado", f"saldo negativo ({quantidade}) entrou como 0: confira a contagem real"))
            quantidade = 0
        custo, erro = _dinheiro(celula(linha, "preco_custo"))
        if erro:
            pendencias.append((numero_linha, nome, "problema", erro)); continue
        minimo, erro = _inteiro(celula(linha, "minimo")) if not _vazio(celula(linha, "minimo")) else (0, None)
        if erro:
            pendencias.append((numero_linha, nome, "problema", erro.replace("quantidade", "mínimo"))); continue
        codigo = ""
        if codigo_manual:
            codigo = _texto(celula(linha, "codigo")).upper()
            if not codigo:
                pendencias.append((numero_linha, nome, "problema", "código vazio")); continue
            if codigo in codigos_existentes or codigo in vistos_codigos:
                pendencias.append((numero_linha, nome, "pulado", f"código {codigo} já existe")); continue

        codigo_antigo = _texto(celula(linha, "codigo"))
        if repetidos == "codigo" and contagem.get(normalizar(nome), 0) > 1 and codigo_antigo and not codigo_manual:
            nome = f"{nome} ({codigo_antigo})"[:120]       # distingue peças de mesmo nome
        chave = normalizar(nome)
        if chave in nomes_existentes:
            i = nomes_existentes[chave]
            pendencias.append((numero_linha, nome, "pulado", f"já cadastrado no sistema como '{i['nome']}' ({i['codigo']})"))
            continue
        if chave in vistos_nomes:
            pendencias.append((numero_linha, nome, "pulado", f"nome repetido na planilha (linha {vistos_nomes[chave]})"))
            continue

        parecidos = [i["nome"] for i in existentes if nome_parecido(normalizar(i["nome"]), chave)]
        parecidos += [p["nome"] for p in prontos if nome_parecido(normalizar(p["nome"]), chave)]
        if parecidos:
            avisos.append(f"linha {numero_linha}: '{nome}' parece com '{parecidos[0]}' (cadastrado mesmo assim)")
        if custo == 0:
            avisos.append(f"linha {numero_linha}: '{nome}' sem preço de custo (ficará 0)")

        vistos_nomes[chave] = numero_linha
        if codigo:
            vistos_codigos.add(codigo)
        prontos.append({"linha": numero_linha, "codigo": codigo, "nome": nome, "quantidade": quantidade,
                        "preco_custo": custo, "minimo": minimo,
                        "local": _texto(celula(linha, "local")), "maquina": _texto(celula(linha, "maquina"))[:100]})
    return {"prontos": prontos, "pendencias": pendencias, "avisos": avisos}


def mostrar_previa(resultado: dict, empresa: dict):
    """Imprime o resumo da análise (o que será cadastrado, pulado e com problema)."""
    p, pend, av = resultado["prontos"], resultado["pendencias"], resultado["avisos"]
    pulados = [x for x in pend if x[2] == "pulado"]
    problemas = [x for x in pend if x[2] == "problema"]
    ajustados = [x for x in pend if x[2] == "ajustado"]
    sem_custo = [a for a in av if "sem preço" in a]
    print(f"\nEmpresa: {empresa['nome']} ({'código automático' if empresa.get('codigo_automatico') else 'código digitado'})")
    print(f"✅ Prontos para cadastrar: {len(p)}")
    print(f"⏭️  Pulados (já existem ou repetidos): {len(pulados)}")
    print(f"❌ Com problema (não serão gravados): {len(problemas)}")
    print(f"🔧 Entram com ajuste (ex.: saldo negativo virou 0): {len(ajustados)}")
    print(f"ℹ️  Sem preço de custo (ficam com 0): {len(sem_custo)}")
    for linha, nome, tipo, motivo in pend[:30]:
        print(f"   {'⏭️' if tipo == 'pulado' else '🔧' if tipo == 'ajustado' else '❌'} linha {linha}: {nome or '(sem nome)'} → {motivo}")
    if len(pend) > 30:
        print(f"   … e mais {len(pend) - 30} no arquivo pendencias_importacao.csv")
    for a in [x for x in av if "parece com" in x][:10]:
        print("   ⚠️ " + a)


def salvar_pendencias(pendencias: list, caminho: str = "pendencias_importacao.csv"):
    """Grava o que não foi importado, para o cliente corrigir (abre bem no Excel)."""
    if not pendencias:
        return None
    with open(caminho, "w", newline="", encoding="utf-8-sig") as f:
        escritor = csv.writer(f, delimiter=";")
        escritor.writerow(["linha", "nome", "situacao", "motivo"])
        escritor.writerows(pendencias)
    return caminho


# ---------------------------------------------------------------- planilha modelo
def criar_modelo(caminho: str = "modelo_importacao.xlsx", com_codigo: bool = False):
    """Cria a planilha modelo (com exemplos e instruções) para o cliente preencher."""
    from openpyxl import Workbook
    from openpyxl.styles import Font
    livro = Workbook()
    aba = livro.active
    aba.title = "Estoque"
    cabecalho = (["Código"] if com_codigo else []) + ["Nome", "Quantidade", "Preço de custo", "Estoque mínimo", "Local", "Máquina"]
    aba.append(cabecalho)
    exemplos = [["Fita isolante 10mm 8kg", 50, 3.20, 10, "", ""],
                ["Correia dentada B42", 4, 120.00, 2, "Prateleira B3", "Prensa Schuler"],
                ["Válvula solenoide", 8, "", 2, "", "Torno Romi"]]
    for i, ex in enumerate(exemplos, start=1):
        aba.append(([f"EX-{i:03d}"] if com_codigo else []) + ex)
    for celula in aba[1]:
        celula.font = Font(bold=True)
    for coluna in aba.columns:
        aba.column_dimensions[coluna[0].column_letter].width = 24
    ajuda = livro.create_sheet("Instruções")
    for linha in [
        "Preencha a aba 'Estoque', uma linha por item. APAGUE as 3 linhas de exemplo antes de enviar.",
        "Nome e Quantidade são obrigatórios. O resto é opcional.",
        "Coloque o peso/medida no nome (ex.: 'Fita branca 10mm 8kg'): itens diferentes precisam de nomes diferentes.",
        "Preço de custo: o que você paga por unidade (ex.: 3,50). Pode deixar vazio.",
        "Estoque mínimo: abaixo disso o sistema avisa que está acabando. Vazio = sem alerta.",
        "Não repita o mesmo nome em duas linhas.",
    ]:
        ajuda.append([linha])
    ajuda.column_dimensions["A"].width = 110
    livro.save(caminho)
    return caminho


# ---------------------------------------------------------------- execução (com banco)
def main():
    """Lê os argumentos, mostra a prévia e, com --confirmar, grava no banco."""
    ap = argparse.ArgumentParser(description="Importa estoque inicial de uma planilha (sem IA).")
    ap.add_argument("arquivo", nargs="?", help="planilha .xlsx ou .csv")
    ap.add_argument("--empresa", help="nome (ou parte do nome) da empresa no sistema")
    ap.add_argument("--usuario", help="administrador em nome de quem entram os itens (padrão: o primeiro admin)")
    ap.add_argument("--negativos", choices=["zero", "pular"], default="zero",
                    help="saldo negativo: entra com 0 (padrão) ou é pulado")
    ap.add_argument("--repetidos", choices=["codigo", "pular"], default="codigo",
                    help="nomes repetidos: acrescenta o código antigo ao nome (padrão) ou pula")
    ap.add_argument("--confirmar", action="store_true", help="grava de verdade (sem isso é só prévia)")
    ap.add_argument("--modelo", action="store_true", help="cria a planilha modelo e sai")
    ap.add_argument("--com-codigo", action="store_true", help="com --modelo: inclui a coluna Código")
    args = ap.parse_args()

    if args.modelo:
        print("✅ Modelo criado:", criar_modelo(com_codigo=args.com_codigo))
        return
    if not args.arquivo or not args.empresa:
        ap.error("informe o arquivo e --empresa (ou use --modelo)")

    import database as db                            # só aqui: o modelo não precisa de banco
    achadas = db.supabase.table("empresas").select("*").ilike("nome", f"%{args.empresa.strip()}%").execute().data
    if len(achadas) != 1:
        nomes = ", ".join(e["nome"] for e in achadas) or "nenhuma"
        raise SystemExit(f"❌ Esperava 1 empresa com '{args.empresa}' e achei: {nomes}")
    empresa = achadas[0]
    empresa_id = empresa["id"]
    admins = [u for u in db.listar_usuarios(empresa_id) if u["papel"] == "admin" and u["ativo"]
              and (not args.usuario or args.usuario.lower() in u["nome"].lower())]
    if not admins:
        raise SystemExit("❌ Nenhum administrador ativo encontrado nesta empresa para assinar a importação.")
    usuario = admins[0]

    resultado = analisar(ler_planilha(args.arquivo), db.todos_itens(empresa_id),
                         not empresa.get("codigo_automatico"), args.negativos, args.repetidos)
    mostrar_previa(resultado, empresa)
    pendencias = list(resultado["pendencias"])

    if not args.confirmar:
        salvar_pendencias(pendencias)
        print("\nNada foi gravado (isto é só a prévia)." + (" Pendências em pendencias_importacao.csv." if pendencias else ""))
        print("Se estiver certo, rode de novo com --confirmar.")
        return

    pendencias = [p for p in pendencias]     # (ajustados também ficam na lista para conferência)
    print(f"\nGravando {len(resultado['prontos'])} itens em nome de {usuario['nome']}...")
    gravados = 0
    for n, p in enumerate(resultado["prontos"], start=1):
        try:
            db.adicionar_item(empresa_id, usuario["id"], p["codigo"], p["nome"], p["maquina"], p["quantidade"],
                              p["preco_custo"], p["local"], p["minimo"])
            gravados += 1
        except Exception as erro:                     # um item com erro não derruba os outros
            pendencias.append((p["linha"], p["nome"], "problema", f"erro ao gravar: {str(erro)[:150]}"))
        if n % 25 == 0:
            print(f"   {n}/{len(resultado['prontos'])}...")
    try:
        db.registrar_auditoria(empresa_id, usuario["id"], "importacao",
                               {"arquivo": Path(args.arquivo).name, "gravados": gravados, "pendencias": len(pendencias)})
    except Exception as erro:
        print("Aviso: não consegui registrar a auditoria:", erro)
    arquivo_pend = salvar_pendencias(pendencias)
    print(f"\n🎉 {gravados} itens cadastrados em {empresa['nome']}." +
          (f" {len(pendencias)} pendência(s) em {arquivo_pend}." if arquivo_pend else ""))


if __name__ == "__main__":
    main()
