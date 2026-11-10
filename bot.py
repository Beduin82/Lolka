import os
import sys
import time
import threading
import random
import requests
from http.server import BaseHTTPRequestHandler, HTTPServer

# Настройка кодировки для корректного вывода русских букв в логи
if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

# --- НАСТРОЙКИ ---
WEBHOOK_URL = os.environ.get('LOLKA_WEBHOOK_URL')
# Используем официальное API Столото для лотереи 2x26
API_URL = "https://www.stoloto.ru/api/v2/archive/2x26?count=1"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

# --- БАЗА ЦИТАТ И ЭМОДЗИ ---
QUOTES = [
    "«Случайность — это псевдоним Бога, когда он не хочет подписываться своим именем.» — Анатоль Франс",
    "«В лотерею выигрывают только те, кто в нее играет. Но и теряют тоже они.» — Народная мудрость",
    "«Вероятность выиграть в лотерею примерно равна вероятности, что вас ударит молния.»",
    "«Математика — царица наук, но удача — её любимая дочь.»",
    "«Не важно, как часто ты падаешь. Важно, как часто ты покупаешь билет.»"
]
EMOJIS = ["❤️", "💖", "💘", "💝", "✨", "🎯", "🎲", "🍀", "🔥"]

# --- ПЕРЕМЕННЫЕ ДЛЯ КОНТРОЛЯ ---
last_processed_draw = None
gaps = {
    "total_less": 0,
    "two_even": 0,
    "matches": 0
}

# --- ЗАГЛУШКА ДЛЯ RENDER ---
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

# --- ФУНКЦИЯ ПАРСИНГА (ЧЕРЕЗ API) ---
def get_last_draw():
    try:
        # Запрашиваем данные у API
        response = requests.get(API_URL, headers=HEADERS, timeout=15)
        response.raise_for_status()
        
        data = response.json()
        
        # Проверяем, что в ответе есть данные
        if not data.get("draws"):
            print("⚠️ API вернуло пустой список тиражей.", flush=True)
            return None
            
        # Берем самый последний тираж
        last_draw = data["draws"][0]
        draw_num = last_draw["draw"]
        
        # API возвращает числа в виде списка строк или чисел. Приводим к числам.
        # Обычно это массив из 4 чисел: [22, 24, 11, 9]
        numbers = [int(num) for num in last_draw["numbers"]]
        
        # Форматируем дату (она приходит в формате 2026-11-10T12:00:00)
        date_str = last_draw.get("date", "Неизвестно").split("T")[0]

        print(f"✅ Успешно получен тираж №{draw_num} через API. Числа: {numbers}", flush=True)
        
        return {
            "draw": draw_num,
            "date": date_str,
            "numbers": numbers
        }
        
    except Exception as e:
        print(f"❌ Ошибка при запросе к API: {e}", flush=True)
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
    print("🚀 Мониторинг запущен (через API). Отслеживаем совпадения и задержки.", flush=True)
    
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

                # 1. Тотал Меньше 53.5
                if total_sum < 53.5:
                    gaps["total_less"] = 0
                else:
                    gaps["total_less"] += 1
                    if gaps["total_less"] >= 3:
                        alerts.append(f"📉 **Тотал Меньше 53.5** не выпадает уже **{gaps['total_less']}** тиража подряд.")
                
                # 2. Ровно 2 четных
                if even_count == 2:
                    gaps["two_even"] = 0
                else:
                    gaps["two_even"] += 1
                    if gaps["two_even"] >= 3:
                        alerts.append(f"⚖️ **Ровно 2 четных** не выпадает уже **{gaps['two_even']}** тиража подряд.")

                # 3. Совпадения
                if has_duplicates:
                    gaps["matches"] = 0
                    dup_str = ", ".join([str(x) for x in set(nums) if nums.count(x) > 1])
                    alerts.append(f"🎯 **Совпадение!** Выпали одинаковые числа: **{dup_str}**")
                else:
                    gaps["matches"] += 1
                    if gaps["matches"] >= 8:
                        alerts.append(f"👯 **Совпадений** нет уже **{gaps['matches']}** тиражей подряд.")
                
                # Отправка
                if alerts:
                    print(f"🔔 Есть события для отправки: {len(alerts)} шт.", flush=True)
                    heart = random.choice(EMOJIS)
                    quote = random.choice(QUOTES)
                    
                    msg = f"{heart} **Анализ тиража №{draw_num}** {heart}\n"
                    msg += f"📅 {draw_data['date']} | Числа: {', '.join(map(str, nums))}\n\n"
                    for alert in alerts:
                        msg += f"🔔 {alert}\n\n"
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