import os
import time
import threading
import requests
from bs4 import BeautifulSoup
from http.server import BaseHTTPRequestHandler, HTTPServer

# --- НАСТРОЙКИ ---
# Вставьте сюда URL вебхука, который вы скопировали в настройках канала Lolka
WEBHOOK_URL = 'https://lolka.app/api/webhooks/959166700160000/OTU5MTY2NzAwMTYwMDAw.F9cmMMafG_5i_MU1xVPx6LcIjZDNUZK2VqS14vYvGw4'

BASE_URL = "https://www.lotonews.ru/archive/dvazhdydva"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

# Переменные для контроля (чтобы не отправлять один и тот же тираж дважды)
last_processed_draw = None
# Словарь для хранения истории повторов: {число: количество тиражей_подряд_с_повтором}
consecutive_repeats = {}

# --- ЗАГЛУШКА ДЛЯ RENDER (чтобы не было ошибки порта) ---
class DummyHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is running")

def run_dummy_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(('0.0.0.0', port), DummyHandler)
    server.serve_forever()

# --- ФУНКЦИЯ ПАРСИНГА ---
def get_last_draw():
    """Получает данные самого свежего тиража."""
    try:
        response = requests.get(BASE_URL, headers=HEADERS, timeout=15)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        
        # Берем первую строку таблицы
        row = soup.select_one("table tbody tr")
        if not row:
            return None

        cells = row.find_all("td")
        if len(cells) < 3:
            return None

        draw_link = cells[1].find("a")
        if not draw_link:
            return None
        
        draw_number_text = draw_link.get_text(strip=True)
        if not draw_number_text.isdigit():
            return None
        
        draw_number = int(draw_number_text)
        date_text = cells[0].get_text(strip=True)
        
        numbers_text = cells[2].get_text(" ", strip=True)
        numbers = [int(t) for t in numbers_text.split() if t.isdigit() and 1 <= int(t) <= 26]

        return {
            "draw": draw_number,
            "date": date_text,
            "numbers": numbers
        }
    except Exception as e:
        print(f"Ошибка при парсинге сайта: {e}")
        return None

# --- ФУНКЦИЯ ОТПРАВКИ В LOLKA ---
def send_to_lolka(text):
    """Отправляет текст в канал Lolka через вебхук."""
    if not WEBHOOK_URL or WEBHOOK_URL == 'https://lolka.app/api/webhooks/959166700160000/OTU5MTY2NzAwMTYwMDAw.F9cmMMafG_5i_MU1xVPx6LcIjZDNUZK2VqS14vYvGw4':
        print("❌ ОШИБКА: Не указан URL вебхука Lolka в коде!")
        return

    payload = {
        "content": text,
        "username": "Lotto Observer"  # Имя бота, которое будет отображаться в чате
    }
    try:
        response = requests.post(WEBHOOK_URL, json=payload, timeout=10)
        response.raise_for_status()
        print("✅ Сообщение успешно отправлено в Lolka.")
    except Exception as e:
        print(f"❌ Ошибка при отправке в Lolka: {e}")

# --- ОСНОВНАЯ ЛОГИКА ---
def main_loop():
    global last_processed_draw, consecutive_repeats
    print("🚀 Мониторинг лотереи запущен. Проверка каждые 5 минут.")
    
    while True:
        draw_data = get_last_draw()
        
        if draw_data:
            draw_num = draw_data["draw"]
            numbers = draw_data["numbers"]
            
            # Проверяем, обрабатывали ли мы уже этот тираж
            if draw_num != last_processed_draw:
                print(f"🔍 Новый тираж: №{draw_num}, числа: {numbers}")
                last_processed_draw = draw_num
                
                # Ищем совпадающие числа (дубликаты)
                duplicates = [str(x) for x in set(numbers) if numbers.count(x) > 1]
                
                if duplicates:
                    print(f"🎯 Найдены совпадения: {duplicates}")
                    # Обновляем счетчики для каждого совпавшего числа
                    for dup in duplicates:
                        consecutive_repeats[dup] = consecutive_repeats.get(dup, 0) + 1
                        print(f"   Число {dup}: {consecutive_repeats[dup]} повтор(а) подряд.")
                        
                        # Если число совпало 3 тиража подряд — шлем уведомление
                        if consecutive_repeats[dup] >= 3:
                            msg = (
                                f"🚨 **Три подряд повтора!**\n"
                                f"Число **{dup}** выпадало дважды в тиражах подряд!\n\n"
                                f"📅 Дата: {draw_data['date']}\n"
                                f"🔢 Тираж: `№{draw_num}`\n"
                                f"🎲 Числа: {', '.join(map(str, numbers))}"
                            )
                            send_to_lolka(msg)
                            # Сбрасываем счетчик после отправки
                            consecutive_repeats[dup] = 0
                else:
                    # Если в тираже не было дубликатов, сбрасываем всю историю
                    if consecutive_repeats:
                        print("В тираже не было совпадений. Счетчики сброшены.")
                        consecutive_repeats.clear()
            else:
                print(f"Ожидание нового тиража. Последний обработанный: №{draw_num}")
        
        # Ждем 5 минут (300 секунд) перед следующей проверкой
        time.sleep(300)

if __name__ == '__main__':
    # 1. Запускаем заглушку порта для Render
    threading.Thread(target=run_dummy_server, daemon=True).start()
    # 2. Запускаем мониторинг
    main_loop()