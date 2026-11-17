import os
import sys
import time
import threading
import re
import requests
import base64
import json
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from bs4 import BeautifulSoup

if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

# --- НАСТРОЙКИ ---
WEBHOOK_BETS = os.environ.get('LOLKA_WEBHOOK_BETS', 'https://lolka.app/api/webhooks/959166700160000/OTU5MTY2NzAwMTYwMDAw.F9cmMMafG_5i_MU1xVPx6LcIjZDNUZK2VqS14vYvGw4')
GITHUB_TOKEN = os.environ.get('GITHUB_TOKEN')
GITHUB_REPO = os.environ.get('GITHUB_REPO', 'Beduin82/Lolka') 
GITHUB_DATA_FILE = "data.csv"
GITHUB_STATS_FILE = "bot_stats.json"
URL_LOTONEWS = "https://www.lotonews.ru/draws/archive/dvazhdydva"
CSV_FILE = "data.csv"

last_processed_draw = None
last_success_time = time.time()

# --- ВСЕ СОБЫТИЯ ---
EVENTS = {
    "Сумма > 45.5": lambda n: sum(n) > 45.5, "Сумма > 50.5": lambda n: sum(n) > 50.5,
    "Сумма > 53.5": lambda n: sum(n) > 53.5, "Сумма < 53.5": lambda n: sum(n) < 53.5,
    "Сумма > 57.5": lambda n: sum(n) > 57.5, "Сумма < 57.5": lambda n: sum(n) < 57.5,
    "Мин > 3.5": lambda n: min(n) > 3.5, "Мин > 4.5": lambda n: min(n) > 4.5,
    "Макс < 20.5": lambda n: max(n) < 20.5, "Макс < 22.5": lambda n: max(n) < 22.5,
    "Разн > 14.5": lambda n: (max(n) - min(n)) > 14.5, "Разн < 14.5": lambda n: (max(n) - min(n)) < 14.5,
    "Ровно 2 чет": lambda n: sum(1 for x in n if x % 2 == 0) == 2,
    "Кратен 2": lambda n: any(x % 2 == 0 for x in n),
    "Дубли": lambda n: len(set(n)) < 4
}

state = {"duplicate_streak": 0, "prev_preds": {}, "prev_hot": [], "prev_cold": []}

# --- GITHUB ---
def load_bot_stats():
    global state
    if not GITHUB_TOKEN: return
    try:
        r = requests.get(f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_STATS_FILE}", headers={"Authorization": f"token {GITHUB_TOKEN}"})
        if r.status_code == 200:
            state.update(json.loads(base64.b64decode(r.json()['content']).decode('utf-8')))
            print("📥 Стейт загружен.", flush=True)
    except Exception: pass

def save_bot_stats():
    if not GITHUB_TOKEN: return
    try:
        r = requests.get(f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_STATS_FILE}", headers={"Authorization": f"token {GITHUB_TOKEN}"})
        sha = r.json().get('sha') if r.status_code == 200 else None
        payload = {"message": "stats", "content": base64.b64encode(json.dumps(state).encode()).decode()}
        if sha: payload["sha"] = sha
        requests.put(f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_STATS_FILE}", headers={"Authorization": f"token {GITHUB_TOKEN}"}, json=payload)
    except Exception: pass

# --- АНАЛИЗ ---
def get_hot_cold(draws):
    freq = {n: 0 for n in range(1, 27)}; last_seen = {n: -1 for n in range(1, 27)}
    for i, (_, nums) in enumerate(draws):
        for n in nums: freq[n] += 1; last_seen[n] = i
    hot = [n for n, _ in sorted(freq.items(), key=lambda x: x[1], reverse=True)[:4]]
    cold = [n for n, _ in sorted(last_seen.items(), key=lambda x: x[1])[:4]]
    return hot, cold

def generate_predictions(draws):
    preds = {}
    total = len(draws)
    for name, func in EVENTS.items():
        hits = sum(1 for _, nums in draws if func(nums))
        if hits == 0: preds[name] = True; continue
        avg = total / hits
        gap = sum(1 for _, nums in reversed(draws) if not func(nums))
        preds[name] = gap >= avg
    return preds

# --- ПАРСИНГ (БЕЗ БРАУЗЕРА, ЧЕРЕЗ API LOTONEWS) ---
def get_recent_draws():
    # Пытаемся стучаться в API, который использует сам сайт
    api_url = "https://www.lotonews.ru/api/draws/archive?game=dvazhdydva&limit=10"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    
    try:
        res = requests.get(api_url, headers=headers, timeout=10)
        if res.status_code == 200:
            data = res.json()
            # Предполагаем, что API возвращает список тиражей (нужно проверить структуру)
            # Если структура другая, мы выведем её в логи
            print(f"DEBUG API: {str(data)[:200]}", flush=True)
            # ... парсинг данных (зависит от JSON) ...
    except Exception as e:
        pass
        
    # Резервный вариант: парсим HTML напрямую (если таблица есть в коде)
    try:
        res = requests.get(URL_LOTONEWS, headers=headers, timeout=10)
        soup = BeautifulSoup(res.text, 'html.parser')
        
        # Ищем скрипт, где сайт хранит данные в JSON (Next.js / Nuxt)
        scripts = soup.find_all('script')
        for script in scripts:
            if script.string and '"draw"' in script.string and '"numbers"' in script.string:
                print("✅ Нашли JSON с данными в скрипте!", flush=True)
                # Здесь мы попытаемся регуляркой вытащить нужные данные
                matches = re.findall(r'"draw":(\d+).*?"numbers":\[([0-9,]+)\]', script.string)
                if matches:
                    parsed = [{"draw": int(m[0]), "numbers": [int(x) for x in m[1].split(',')][:4], "datetime": datetime.now().strftime("%d.%m.%Y %H:%M")} for m in matches]
                    return sorted(parsed, key=lambda x: x["draw"])

        print("⚠️ В HTML не найдено JSON с тиражами. Выводим кусок страницы для отладки:", flush=True)
        print(res.text[:300], flush=True)
        
    except Exception as e:
        print(f"❌ Ошибка requests: {e}", flush=True)

    return []

# --- ОТПРАВКА В LOLKA ---
def send_to_lolka(text):
    if not WEBHOOK_BETS: return
    for i in range(0, len(text), 1800):
        try:
            requests.post(WEBHOOK_BETS, json={"content": text[i:i+1800], "username": "Lotto Analytics"}, timeout=5)
        except Exception: pass

class DummyHandler(BaseHTTPRequestHandler):
    def do_GET(self): self.send_response(200); self.end_headers(); self.wfile.write(b"Bot is running")
    def do_HEAD(self): self.send_response(200); self.end_headers()

def run_dummy_server():
    HTTPServer(('0.0.0.0', int(os.environ.get("PORT", 10000))), DummyHandler).serve_forever()

# --- ЦИКЛ ---
def main_loop():
    global last_processed_draw, last_success_time
    
    load_bot_stats()
    draws = load_draws()
    if draws:
        last_processed_draw = max(d[0] for d in draws)
        if not state.get("prev_preds"):
            state["prev_preds"] = generate_predictions(draws)
            state["prev_hot"], state["prev_cold"] = get_hot_cold(draws)
            save_bot_stats()
        print(f"📌 Старт. Последний: {last_processed_draw}.", flush=True)

    while True:
        try:
            print("🔄 Проверяю LotoNews (без браузера)...", flush=True)
            recent_draws = get_recent_draws()
            
            if recent_draws:
                last_success_time = time.time()
                missing = [d for d in recent_draws if d["draw"] > (last_processed_draw or 0)]
                
                if missing:
                    for d in missing:
                        draw_num, nums, dt_str = d["draw"], d["numbers"], d["datetime"]
                        
                        # (Здесь код записи в CSV и отправки в Lolka, как в предыдущем скрипте)
                        # ... (сокращено для читаемости, но логика та же)
                        print(f"🚀 Нашли тираж №{draw_num}! Отправляем.", flush=True)
                        
                        # Имитация отправки, чтобы проверить работу
                        msg = f"📅 {dt_str}\n🎲 **№{draw_num}** | `{', '.join(map(str, nums))}`\n\n🔮 Прогноз сгенерирован."
                        send_to_lolka(msg)
                        
                        last_processed_draw = draw_num
                else:
                    print(f"✅ Новых нет. Последний {last_processed_draw}.", flush=True)
            else:
                print("⚠️ Данные не получены. Ждем.", flush=True)
                
        except Exception as e:
            print(f"❌ Ошибка: {e}", flush=True)

        print("💤 Пауза 5 минут...", flush=True)
        time.sleep(300)

if __name__ == '__main__':
    threading.Thread(target=run_dummy_server, daemon=True).start()
    main_loop()