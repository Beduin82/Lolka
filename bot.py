import os
import requests
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

# Берем ссылку из переменной Render
WEBHOOK_URL = os.environ.get('LOLKA_WEBHOOK_URL')

# Заглушка, чтобы Render не ругался на порт
class DummyHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")
    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()

def run_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(('0.0.0.0', port), DummyHandler)
    server.serve_forever()

# --- ТЕСТОВАЯ ОТПРАВКА ---
print("🚀 ТЕСТ ЗАПУЩЕН", flush=True)

if not WEBHOOK_URL:
    print("❌ ОШИБКА: Переменная LOLKA_WEBHOOK_URL пуста или не найдена!", flush=True)
else:
    print(f"🔗 Ссылка найдена, начинается с: {WEBHOOK_URL[:30]}...", flush=True)
    try:
        payload = {
            "content": "🔔 **ВНИМАНИЕ! ЭТО ТЕСТОВОЕ СООБЩЕНИЕ ОТ БОТА!** Если вы его видите, значит связь работает.",
            "username": "Lotto Observer"
        }
        # Отправляем сообщение
        response = requests.post(WEBHOOK_URL, json=payload, timeout=10)
        print(f"✅ Ответ от Lolka: Код {response.status_code}", flush=True)
        
        if response.status_code in [200, 204]:
            print("🎉 УСПЕХ! Сообщение отправлено.", flush=True)
        else:
            print(f"⚠️ Lolka не приняла сообщение. Ответ сервера: {response.text}", flush=True)
            
    except Exception as e:
        print(f"❌ ОШИБКА ПРИ ОТПРАВКЕ: {e}", flush=True)

# Запускаем сервер в фоне, чтобы Render не выключал бота
threading.Thread(target=run_server, daemon=True).start()

# Держим программу запущенной
import time
while True:
    time.sleep(60)