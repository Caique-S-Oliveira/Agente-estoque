import os
from dotenv import load_dotenv

load_dotenv()

# --- Supabase (obrigatórios) ---
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise ValueError("Faltou preencher SUPABASE_URL ou SUPABASE_KEY no arquivo .env")

# --- WhatsApp / Meta (novos) ---
# Se ainda não existirem no .env, ficam como None (vazio). Hoje isso é normal.
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID")
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN")
META_APP_SECRET = os.getenv("META_APP_SECRET", "")

# --- Regras de negócio ---
# Margem sobre o custo para o valor sugerido de venda (0.30 = 30%)
MARGEM_SUGERIDA = 0.30

QTD_CONFIRMAR_ACIMA_DE = 50

# --- IA (Anthropic) ---
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
MODELO_IA = "claude-haiku-4-5-20251001"   # para trocar de modelo, mude só esta linha
LIMITE_IA_DIARIO = 200                    # mensagens com IA por dia, por empresa

# Preços por milhão de tokens (US$). Confira no site da Anthropic: mudam com o tempo.
PRECO_IA_ENTRADA_USD = 1.0
PRECO_IA_SAIDA_USD = 5.0