import os
import time
import threading
import random
import requests
from bs4 import BeautifulSoup
from http.server import BaseHTTPRequestHandler, HTTPServer

# --- НАСТРОЙКИ ---
WEBHOOK_URL = os.environ.get('LOLKA_WEBHOOK_URL')

BASE_URL = "https://www.lotonews.ru/archive/dvazhdydva"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

# --- БАЗА ЦИТАТ ---
QUOTES = [
    "«Случайность — это псевдоним Бога, когда он не хочет подписываться своим именем.» — Анатоль Франс",
    "«В лотерею выигрывают только те, кто в нее играет. Но и теряют тоже они.» — Народная мудрость",
    "«Вероятность выиграть в лотерею примерно равна вероятности, что вас ударит молния.»",
    "«Математика — царица наук, но удача — её любимая дочь.»",
    "«Не важно, как часто ты падаешь. Важно, как часто ты покупаешь билет.»"
]
EMOJIS = ["❤️", "💖", "💘", "💝", "✨", "🎯", "🎲", "🍀", "🔥"]

# --- ПЕРЕМЕННЫЕ ДЛЯ ТРЕКЕРА ---
last_processed_draw = None
# Словарь для отслеживания текущих "задержек" событий
# Ключ: название события, Значение: сколько тиражей подряд оно НЕ выпадает
gaps = {
    "total_less": 0,  # Тотал < 53.5
    "two_even": 0,    # Ровно 2 четных
    "matches": 0      # Совпадение чисел
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

# --- ПАРСИНГ ---
def get_last_draw():
    try:
        response = requests.get(BASE_URL, headers=HEADERS, timeout=15)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        row = soup.select_one("table tbody tr")
        if not row: return None
        cells = row.find_all("td")
        if len(cells) < 3: return None
        draw_link = cells[1].find("a")
        if not draw_link: return None
        draw_number_text = draw_link.get_text(strip=True)
        if not draw_number_text.isdigit(): return None
        
        numbers_text = cells[2].get_text(" ", strip=True)
        numbers = [int(t) for t in numbers_text.split() if t.isdigit() and 1 <= int(t) <= 26]

        return {
            "draw": int(draw_number_text),
            "date": cells[0].get_text(strip=True),
            "numbers": numbers
        }
    except Exception as e:
        print(f"Ошибка при парсинге: {e}")
        return None

# --- ОТПРАВКА ---
def send_to_lolka(text):
    if not WEBHOOK_URL:
        print("❌ ОШИБКА: Переменная LOLKA_WEBHOOK_URL не задана!")
        return
    payload = {"content": text, "username": "Lotto Observer"}
    try:
        requests.post(WEBHOOK_URL, json=payload, timeout=10).raise_for_status()
        print("✅ Сообщение отправлено в Lolka.")
    except Exception as e:
        print(f"❌ Ошибка отправки: {e}")

# --- ОСНОВНАЯ ЛОГИКА ---
def main_loop():
    global last_processed_draw, gaps
    print("🚀 Мониторинг запущен. Отслеживаем периодичность.")
    
    while True:
        draw_data = get_last_draw()
        
        if draw_data:
            draw_num = draw_data["draw"]
            nums = draw_data["numbers"]
            
            if draw_num != last_processed_draw:
                print(f"🔍 Новый тираж: №{draw_num}, числа: {nums}")
                last_processed_draw = draw_num
                
                # Вычисляем исходы
                total_sum = sum(nums)
                even_count = sum(1 for x in nums if x % 2 == 0)
                has_duplicates = len(set(nums)) < 4
                
                # Списки для уведомлений
                alerts = []

                # 1. Проверка: Тотал Меньше 53.5
                if total_sum < 53.5:
                    gaps["total_less"] = 0 # Событие выпало, сбрасываем счетчик
                else:
                    gaps["total_less"] += 1
                    # Средний период ~2 тиража. Если не выпало 3 и более раз - алерт
                    if gaps["total_less"] >= 3:
                        alerts.append(f"📉 **Тотал Меньше 53.5** не выпадает уже **{gaps['total_less']}** тиража подряд (среднее ожидание: ~2).")
                
                # 2. Проверка: Ровно 2 четных
                if even_count == 2:
                    gaps["two_even"] = 0
                else:
                    gaps["two_even"] += 1
                    # Средний период ~2.5 тиража. Если не выпало 3 и более - алерт
                    if gaps["two_even"] >= 3:
                        alerts.append(f"⚖️ **Ровно 2 четных** не выпадает уже **{gaps['two_even']}** тиража подряд (среднее ожидание: ~2.5).")

                # 3. Проверка: Совпадение чисел
                if has_duplicates:
                    gaps["matches"] = 0
                    dup_str = ", ".join([str(x) for x in set(nums) if nums.count(x) > 1])
                    alerts.append(f"🎯 **Совпадение!** В тираже выпали одинаковые числа: **{dup_str}**")
                else:
                    gaps["matches"] += 1
                    # Средний период ~6.6 тиража. Если не выпало 8 и более - алерт
                    if gaps["matches"] >= 8:
                        alerts.append(f"👯 **Совпадения чисел** нет уже **{gaps['matches']}** тиражей подряд (среднее ожидание: ~6.6).")
                
                # Если есть алерты - отправляем их одним сообщением
                if alerts:
                    random_heart = random.choice(EMOJIS)
                    random_quote = random.choice(QUOTES)
                    
                    msg = f"{random_heart} **Анализ тиража №{draw_num}** {random_heart}\n"
                    msg += f"📅 {draw_data['date']} | Числа: {', '.join(map(str, nums))}\n\n"
                    
                    for alert in alerts:
                        msg += f"🔔 {alert}\n\n"
                        
                    msg += f"> *{random_quote}*"
                    send_to_lolka(msg)
                else:
                    print("Все события в пределах нормы, уведомлений нет.")
            else:
                print(f"Ожидание нового тиража. Последний: №{draw_num}")
        
        time.sleep(300)

if __name__ == '__main__':
    threading.Thread(target=run_dummy_server, daemon=True).start()
    main_loop()