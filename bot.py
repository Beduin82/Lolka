import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
import os
import requests
from bs4 import BeautifulSoup
import time
from datetime import datetime
import re

# --- НАСТРОЙКИ ---
WEBHOOK_URL = 'https://lolka.app/api/webhooks/959166700160000/OTU5MTY2NzAwMTYwMDAw.F9cmMMafG_5i_MU1xVPx6LcIjZDNUZK2VqS14vYvGw4'
CHECK_INTERVAL_MINUTES = 5  # Как часто проверять (в минутах)
BASE_URL = "https://www.lotonews.ru/archive/dvazhdydva"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}

# Хранилище для уже обработанных тиражей, чтобы не отправлять дубли
processed_draws = set()

def get_last_draw():
    """Получает данные последнего тиража с сайта lotonews.ru."""
    try:
        response = requests.get(BASE_URL, headers=HEADERS, timeout=15)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        
        # Ищем первую строку таблицы (самый свежий тираж)
        row = soup.select_one("table tbody tr")
        if not row:
            print("Не удалось найти таблицу с тиражами.")
            return None

        cells = row.find_all("td")
        if len(cells) < 3:
            return None

        # Извлекаем номер тиража
        draw_link = cells[1].find("a")
        if not draw_link:
            return None
        draw_number_text = draw_link.get_text(strip=True)
        if not draw_number_text.isdigit():
            return None
        draw_number = int(draw_number_text)

        # Извлекаем дату
        date_text = cells[0].get_text(strip=True)

        # Извлекаем все выпавшие числа
        numbers_text = cells[2].get_text(" ", strip=True)
        numbers = [int(t) for t in numbers_text.split() if t.isdigit() and 1 <= int(t) <= 26]

        return {
            "draw": draw_number,
            "date": date_text,
            "numbers": numbers
        }
    except Exception as e:
        print(f"Ошибка при получении данных с сайта: {e}")
        return None

def check_for_matches(numbers):
    """Проверяет, есть ли в списке из 4 чисел повторяющиеся."""
    # Если количество уникальных чисел меньше 4, значит есть дубликаты
    if len(set(numbers)) < 4:
        duplicates = [str(x) for x in set(numbers) if numbers.count(x) > 1]
        return duplicates
    return []

def send_to_lolka(text):
    """Отправляет сообщение в канал Lolka через вебхук."""
    try:
        payload = {
            "content": text,
            "username": "Lotto Observer"  # Имя, которое будет отображаться у сообщения
        }
        response = requests.post(WEBHOOK_URL, json=payload, timeout=10)
        response.raise_for_status()
        print(f"Сообщение успешно отправлено в Lolka.")
    except Exception as e:
        print(f"Ошибка при отправке в Lolka: {e}")

def main_loop():
    """Основной цикл работы программы."""
    print(f"Запуск мониторинга. Проверка каждые {CHECK_INTERVAL_MINUTES} минут.")
    
    while True:
        draw_data = get_last_draw()
        
        if draw_data:
            draw_num = draw_data["draw"]
            numbers = draw_data["numbers"]
            
            # Проверяем, не обрабатывали ли мы уже этот тираж
            if draw_num not in processed_draws:
                print(f"Новый тираж: №{draw_num}, числа: {numbers}")
                
                # Проверяем на наличие одинаковых чисел
                duplicates = check_for_matches(numbers)
                
                if duplicates:
                    # Формируем сообщение
                    dup_str = ", ".join(duplicates)
                    message = (
                        f"🎯 **Совпадение в тираже!**\n\n"
                        f"📅 Дата: {draw_data['date']}\n"
                        f"🔢 Тираж: `№{draw_num}`\n"
                        f"🎲 Числа: {', '.join(map(str, numbers))}\n"
                        f"🔄 Совпало число: **{dup_str}**"
                    )
                    print(f"Найдено совпадение! Отправка в Lolka...")
                    send_to_lolka(message)
                else:
                    print("Совпадений нет.")
                
                # Запоминаем тираж как обработанный
                processed_draws.add(draw_num)
            else:
                print(f"Тираж №{draw_num} уже обработан. Ждем следующий...")
        
        # Ждем перед следующей проверкой
        time.sleep(CHECK_INTERVAL_MINUTES * 60)

# --- ЗАГЛУШКА ДЛЯ RENDER (Чтобы сервис не выключался) ---
class DummyHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is running")

def run_dummy_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(('0.0.0.0', port), DummyHandler)
    server.serve_forever()

if __name__ == '__main__':
    # Запускаем заглушку в отдельном потоке
    threading.Thread(target=run_dummy_server, daemon=True).start()
    # Запускаем самого бота Lolka
    print("Запуск бота...")
    client.run(TOKEN):
    main_loop()