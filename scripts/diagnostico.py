from app.config import SUPABASE_URL, SUPABASE_KEY
import socket

print("URL lida do .env:", repr(SUPABASE_URL))
print("Tamanho da chave:", len(SUPABASE_KEY))

# Extrai só o domínio (tira o https://)
dominio = SUPABASE_URL.replace("https://", "").replace("http://", "").strip("/")
print("Domínio testado:", repr(dominio))

try:
    ip = socket.gethostbyname(dominio)
    print("✅ Domínio encontrado! IP:", ip)
except socket.gaierror:
    print("❌ Domínio NÃO encontrado. Veja o Passo 3.")