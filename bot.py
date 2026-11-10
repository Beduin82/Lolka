import os
import sys
import time
import threading
import random
import requests
from bs4 import BeautifulSoup
from http.server import BaseHTTPRequestHandler, HTTPServer

if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

# --- НАСТРОЙКИ ---
WEBHOOK_URL = os.environ.get('LOLKA_WEBHOOK_URL')
# ИСПОЛЬЗУЕМ НОВЫЙ РАБОЧИЙ URL
ARCHIVE_URL = "https://www.lotonews.ru/draws/archive/dvazhdydva"
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

# --- НОВАЯ ФУНКЦИЯ ПАРСИНГА (ПОД НОВЫЙ URL) ---
def get_last_draw():
    try:
        response = requests.get(ARCHIVE_URL, headers=HEADERS, timeout=15)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, "html.parser")
        
        # Ищем первую строку таблицы с результатами
        # Судя по структуре страницы, строки находятся в элементах <tr> внутри <tbody>
        # или просто являются блоками. Пробуем найти строку, содержащую номер тиража.
        # Ищем ссылку, которая ведет на тираж, например: /draws/archive/dvazhdydva/343750
        draw_link = soup.select_one("a[href*='/draws/archive/dvazhdydva/']")
        if not draw_link:
            print("⚠️ Не удалось найти ссылку на последний тираж.", flush=True)
            return None
        
        # Извлекаем номер тиража из ссылки или текста
        draw_num_text = draw_link.get_text(strip=True)
        if not draw_num_text.isdigit():
            # Если текст не число, пробуем вытащить из URL
            import re
            match = re.search(r'/(\d+)$', draw_link['href'])
            if match:
                draw_num_text = match.group(1)
            else:
                print(f"⚠️ Не удалось определить номер тиража из '{draw_num_text}'", flush=True)
                return None
        
        draw_num = int(draw_num_text)
        
        # Ищем строку (или родительский блок), содержащую этот тираж
        # Находим родительский элемент, который содержит и ссылку, и числа
        parent = draw_link.find_parent()
        if not parent:
            print("⚠️ Не удалось найти родительский блок для тиража.", flush=True)
            return None
        
        # Извлекаем все числа из текста родительского блока
        # Числа выглядят как "3 19 24 17" в тексте
        text = parent.get_text(" ", strip=True)
        # Ищем последовательность из 4 чисел
        import re
        numbers_match = re.search(r'(\d+)\s+(\d+)\s+(\d+)\s+(\d+)', text)
        if not numbers_match:
            print(f"⚠️ Не удалось найти 4 числа в блоке тиража. Текст: {text[:100]}...", flush=True)
            return None
        
        numbers = [int(numbers_match.group(i)) for i in range(1, 5)]
        
        # Извлекаем дату
        date_match = re.search(r'(\d{2}\.\d{2}\.\d{4})', text)
        date_str = date_match.group(1) if date_match else "Неизвестно"
        
        print(f"✅ Успешно спарсен тираж №{draw_num}. Числа: {numbers}, Дата: {date_str}", flush=True)
        
        return {
            "draw": draw_num,
            "date": date_str,
            "numbers": numbers
        }
        
    except Exception as e:
        print(f"❌ Ошибка при парсинге страницы: {e}", flush=True)
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

# --- ОСНОВНАЯ ЛОГИКА (БЕЗ ИЗМЕНЕНИЙ) ---
def main_loop():
    global last_processed_draw, gaps
    print("🚀 Мониторинг запущен (новый URL). Отслеживаем совпадения.", flush=True)
    
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