import os
import sys
import time
import threading
import random
import requests
from http.server import BaseHTTPRequestHandler, HTTPServer

if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

# --- НАСТРОЙКИ ---
WEBHOOK_URL = os.environ.get('LOLKA_WEBHOOK_URL')

# Пробуем разные возможные названия для игры в мобильном API
GAME_NAMES = ["2x26", "dvazhdydva", "2x26-duel"]
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

QUOTES = [
    "«Случайность — это псевдоним Бога, когда он не хочет подписываться своим именем.» — Анатоль Франс",
    "«В лотерею выигрывают только те, кто в нее играет. Но и теряют тоже они.» — Народная мудрость",
    "«Вероятность выиграть в лотерею примерно равна вероятности, что вас ударит молния.»",
    "«Математика — царица наук, но удача — её любимая дочь.»",
    "«Не важно, как часто ты падаешь. Важно, как часто ты покупаешь билет.»"
]
EMOJIS = ["❤️", "💖", "💘", "💝", "✨", "🎯", "🎲", "🍀", "🔥"]

last_processed_draw = None
gaps = {"total_less": 0, "two_even": 0, "matches": 0}

class DummyHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is running")
    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()

def run_dummy_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(('0.0.0.0', port), DummyHandler)
    server.serve_forever()

# --- НОВАЯ ФУНКЦИЯ ПАРСИНГА (ЧЕРЕЗ МОБИЛЬНОЕ API) ---
def get_last_draw():
    for game_name in GAME_NAMES:
        # Используем версию API v34, как в рабочих примерах
        api_url = f"https://www.stoloto.ru/p/api/mobile/api/v34/service/draws/archive?count=1&game={game_name}"
        try:
            response = requests.get(api_url, headers=HEADERS, timeout=15)
            # Если API вернуло 404 (неверное имя игры), пробуем следующее
            if response.status_code == 404:
                print(f"ℹ️ Игра '{game_name}' не найдена в API, пробуем другую...", flush=True)
                continue
            response.raise_for_status()
            
            data = response.json()
            # Проверяем, что в ответе есть список тиражей
            if "draws" not in data or not data["draws"]:
                print(f"⚠️ API для игры '{game_name}' вернуло пустой список.", flush=True)
                continue
                
            last_draw = data["draws"][0]
            draw_num = last_draw["draw"]
            
            # Числа могут приходить в поле "numbers" или "combinations"
            numbers = None
            if "numbers" in last_draw:
                numbers = [int(n) for n in last_draw["numbers"]]
            elif "combinations" in last_draw:
                # Иногда числа лежат в "combinations"
                numbers = [int(n) for n in last_draw["combinations"]]
                
            if not numbers or len(numbers) < 4:
                print(f"⚠️ Не удалось извлечь 4 числа для игры '{game_name}'.", flush=True)
                continue
                
            date_str = last_draw.get("date", "Неизвестно").split("T")[0]
            
            print(f"✅ Успешно получен тираж №{draw_num} через API (игра: {game_name}). Числа: {numbers[:4]}", flush=True)
            return {
                "draw": draw_num,
                "date": date_str,
                "numbers": numbers[:4]
            }
            
        except Exception as e:
            print(f"❌ Ошибка при запросе к API для '{game_name}': {e}", flush=True)
            continue
            
    # Если ни одна игра не подошла
    print("⚠️ Не удалось получить данные ни для одного из вариантов названия игры.", flush=True)
    return None

# --- ФУНКЦИЯ ОТПРАВКИ В LOLKA ---
def send_to_lolka(text):
    if not WEBHOOK_URL:
        print("❌ ОШИБКА: LOLKA_WEBHOOK_URL не задана!", flush=True)
        return
    payload = {"content": text, "username": "Lotto Observer"}
    try:
        res = requests.post(WEBHOOK_URL, json=payload, timeout=10)
        res.raise_for_status()
        print("✅ Сообщение отправлено в Lolka.", flush=True)
    except Exception as e:
        print(f"❌ Ошибка отправки в Lolka: {e}", flush=True)

# --- ОСНОВНАЯ ЛОГИКА ---
def main_loop():
    global last_processed_draw, gaps
    print("🚀 Мониторинг запущен (мобильное API). Отслеживаем совпадения.", flush=True)
    
    while True:
        print("Начинаю новую проверку...", flush=True)
        draw_data = get_last_draw()
        
        if draw_data:
            draw_num = draw_data["draw"]
            nums = draw_data["numbers"]
            
            if draw_num != last_processed_draw:
                print(f"\n🔍 Новый тираж: №{draw_num}, числа: {nums}", flush=True)
                last_processed_draw = draw_num
                
                total_sum = sum(nums)
                even_count = sum(1 for x in nums if x % 2 == 0)
                has_duplicates = len(set(nums)) < 4
                
                alerts = []

                if total_sum < 53.5: gaps["total_less"] = 0
                else:
                    gaps["total_less"] += 1
                    if gaps["total_less"] >= 3:
                        alerts.append(f"📉 **Тотал Меньше 53.5** не выпадает уже **{gaps['total_less']}** тиража подряд.")
                
                if even_count == 2: gaps["two_even"] = 0
                else:
                    gaps["two_even"] += 1
                    if gaps["two_even"] >= 3:
                        alerts.append(f"⚖️ **Ровно 2 четных** не выпадает уже **{gaps['two_even']}** тиража подряд.")

                if has_duplicates:
                    gaps["matches"] = 0
                    dup_str = ", ".join([str(x) for x in set(nums) if nums.count(x) > 1])
                    alerts.append(f"🎯 **Совпадение!** Выпали одинаковые числа: **{dup_str}**")
                else:
                    gaps["matches"] += 1
                    if gaps["matches"] >= 8:
                        alerts.append(f"👯 **Совпадений** нет уже **{gaps['matches']}** тиражей подряд.")
                
                if alerts:
                    heart = random.choice(EMOJIS)
                    quote = random.choice(QUOTES)
                    msg = f"{heart} **Анализ тиража №{draw_num}** {heart}\n"
                    msg += f"📅 {draw_data['date']} | Числа: {', '.join(map(str, nums))}\n\n"
                    for alert in alerts: msg += f"🔔 {alert}\n\n"
                    msg += f"> *{quote}*"
                    send_to_lolka(msg)
                else:
                    print("События в пределах нормы, уведомлений нет.", flush=True)
            else:
                print(f"Тираж №{draw_num} уже был обработан ранее.", flush=True)
        else:
            print("Не удалось получить данные. Ждем 5 минут.", flush=True)
        
        print("--- Пауза 5 минут ---", flush=True)
        time.sleep(300)

if __name__ == '__main__':
    threading.Thread(target=run_dummy_server, daemon=True).start()
    main_loop()