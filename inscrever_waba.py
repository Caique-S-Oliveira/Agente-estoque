import httpx
from config import WHATSAPP_TOKEN

WABA_ID = 2349238762576029

url = f"https://graph.facebook.com/v21.0/{WABA_ID}/subscribed_apps"
cabecalho = {"Authorization": f"Bearer {WHATSAPP_TOKEN}"}

# 1) Inscreve o app na conta
r = httpx.post(url, headers=cabecalho)
print("Inscrever:", r.status_code, r.text)

# 2) Lista os apps inscritos, para conferir
r = httpx.get(url, headers=cabecalho)
print("Inscritos:", r.status_code, r.text)