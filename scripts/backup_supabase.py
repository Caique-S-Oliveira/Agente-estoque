"""
BACKUP dos dados do sistema (plano gratuito do Supabase não faz backup automático).

O que faz: lê todas as tabelas importantes, grava cada uma num arquivo .json e junta tudo
num .zip com a data no nome. Roda no SEU computador, usa a chave do seu .env e não gasta nada.

Rode:   python backup_supabase.py
Opções: --pasta CAMINHO   (onde guardar; padrão: pasta "agente-estoque-backups" ao lado do projeto)
        --guardar N       (quantos .zip manter; padrão 8, os mais antigos são apagados)

IMPORTANTE:
  - O backup contém dados de clientes (telefones, estoque). Guarde fora do GitHub e de pastas
    compartilhadas. Por isso o padrão é uma pasta FORA do projeto.
  - Faça também uma cópia do .zip em outro lugar (pen drive, Google Drive pessoal): se o PC
    quebrar, o backup que está nele quebra junto.
  - O estado das conversas e os IDs de mensagens não entram: são temporários.
"""
import argparse
import json
import sys
import zipfile
from datetime import datetime
from pathlib import Path

# Tabelas que contêm dados que não se pode perder (ordem não importa)
TABELAS = ["empresas", "usuarios", "itens", "movimentacoes", "mensagens", "uso_ia", "auditoria"]
PAGINA = 1000          # o Supabase devolve no máximo 1000 linhas por consulta


def ler_tabela(banco, tabela: str) -> list:
    """Lê TODAS as linhas de uma tabela, de 1000 em 1000 (ordenado por id para não repetir/pular)."""
    linhas, inicio = [], 0
    while True:
        consulta = banco.table(tabela).select("*").order("id").range(inicio, inicio + PAGINA - 1)
        pagina = consulta.execute().data
        linhas += pagina
        if len(pagina) < PAGINA:
            return linhas
        inicio += PAGINA


def contar(banco, tabela: str) -> int:
    """Pergunta ao banco quantas linhas a tabela tem (para conferir se o backup veio completo)."""
    resposta = banco.table(tabela).select("id", count="exact").limit(1).execute()
    return resposta.count


def fazer_backup(banco, pasta_destino: Path, guardar: int = 8, agora=None):
    """Exporta as tabelas, confere as contagens e gera o .zip. Devolve (caminho, resumo)."""
    agora = agora or datetime.now()
    pasta_destino.mkdir(parents=True, exist_ok=True)
    nome_zip = pasta_destino / f"backup_{agora:%Y-%m-%d_%H%M}.zip"
    resumo, erros = {}, []
    with zipfile.ZipFile(nome_zip, "w", zipfile.ZIP_DEFLATED) as pacote:
        for tabela in TABELAS:
            linhas = ler_tabela(banco, tabela)
            esperado = contar(banco, tabela)
            if esperado is not None and esperado != len(linhas):
                erros.append(f"{tabela}: banco tem {esperado}, backup pegou {len(linhas)}")
            pacote.writestr(f"{tabela}.json", json.dumps(linhas, ensure_ascii=False, indent=1, default=str))
            resumo[tabela] = len(linhas)
        pacote.writestr("LEIA-ME.txt",
                        f"Backup feito em {agora:%d/%m/%Y %H:%M}.\n"
                        "Cada arquivo .json é uma tabela do Supabase.\n"
                        "Para recriar o banco: rode as migrações (pasta migracoes) e depois importe estes dados.\n")
    if erros:
        nome_zip.unlink()                                 # backup incompleto não serve: não deixa lixo
        raise RuntimeError("Backup incompleto, nada foi guardado: " + "; ".join(erros))
    _apagar_antigos(pasta_destino, guardar)
    return nome_zip, resumo


def _apagar_antigos(pasta: Path, guardar: int):
    """Mantém só os N backups mais recentes (apaga apenas arquivos backup_*.zip desta pasta)."""
    zips = sorted(pasta.glob("backup_*.zip"))
    for antigo in zips[:-guardar] if guardar > 0 else []:
        antigo.unlink()


def main():
    ap = argparse.ArgumentParser(description="Backup dos dados do Supabase (grátis, local).")
    ap.add_argument("--pasta", default=str(Path(__file__).resolve().parent.parent.parent / "agente-estoque-backups"))
    ap.add_argument("--guardar", type=int, default=8)
    args = ap.parse_args()
    from app.database import supabase                     # só aqui, para o teste não precisar do banco
    try:
        caminho, resumo = fazer_backup(supabase, Path(args.pasta), args.guardar)
    except Exception as erro:
        print("🚨 BACKUP FALHOU:", erro)
        sys.exit(1)
    print("✅ Backup pronto:", caminho)
    for tabela, qtd in resumo.items():
        print(f"   {tabela}: {qtd} linhas")
    print("Lembre de copiar este .zip para outro lugar (pen drive ou nuvem pessoal).")


if __name__ == "__main__":
    main()
