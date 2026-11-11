import os
import sys
import time
import threading
import random
import re
import requests
from http.server import BaseHTTPRequestHandler, HTTPServer
from bs4 import BeautifulSoup

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

# --- НАСТРОЙКИ ---
# Берем ссылку из переменной окружения на Render
WEBHOOK_BETS = os.environ.get('LOLKA_WEBHOOK_BETS')
ARCHIVE_URL = "https://www.lotonews.ru/draws/archive/dvazhdydva"

# База цитат (если создадите quotes.txt в GitHub, бот возьмет их оттуда)
QUOTES = [
    "«Случайность — это псевдоним Бога, когда он не хочет подписываться своим именем.» — Анатоль Франс",
    "«Бог не играет в кости со Вселенной.» — Альберт Эйнштейн",
    "«Удача сопутствует смелым.» — Вергилий",
    "«Случай — это то, что случается с нами, когда мы строим планы.» — Уильям Шекспир",
    "«Мы сами творим свою удачу.» — Мигель де Сервантес",
    "«Фортуна улыбается тем, кто к ней готов.» — Луи Пастер",
    "«Успех — это способность идти от неудачи к неудаче, не теряя энтузиазма.» — Уинстон Черчилль",
    "«Деньги — хороший слуга, но плохой хозяин.» — Фрэнсис Бэкон",
    "«Рукописи не горят.» — Михаил Булгаков",
    "«Красота спасёт мир.» — Фёдор Достоевский",
    "«Я мыслю, следовательно, я существую.» — Рене Декарт",
    "«Познай самого себя.» — Сократ",
    "«Всё течёт, всё меняется.» — Гераклит",
    "«Самое непостижимое в этом мире — это то, что он постижим.» — Альберт Эйнштейн",
    "«Всё, что нас не убивает, делает нас сильнее.» — Фридрих Ницше",
    "«Лучший способ предсказать будущее — создать его.» — Питер Друкер",
    "«Мы — то, что мы делаем постоянно. Совершенство — не акт, а привычка.» — Аристотель",
    "«В лотерею выигрывают только те, кто в нее играет. Но и теряют тоже они.» — Народная мудрость"
]

def load_quotes():
    global QUOTES
    if os.path.exists('quotes.txt'):
        try:
            with open('quotes.txt', 'r', encoding='utf-8') as f:
                file_quotes = [line.strip() for line in f if line.strip()]
                if file_quotes:
                    QUOTES = file_quotes
                    print(f"📚 Загружено {len(QUOTES)} цитат из quotes.txt", flush=True)
        except Exception as e:
            print(f"⚠️ Ошибка чтения quotes.txt: {e}", flush=True)

# --- ТРИГГЕРЫ ДЛЯ КАНАЛА СТАВОК ---
# Пороги подобраны так, чтобы бот молчал при нормальной статистике.
BETS_TRIGGERS = {
    "big_diff": {
        "name": "🟢 Разность (Max-Min) > 14.5", 
        "check": lambda n: (max(n) - min(n)) > 14.5, 
        "threshold": 3, "streak": 0
    },
    "total_less_53": {
        "name": "🟢 Тотал < 53.5", 
        "check": lambda n: sum(n) < 53.5, 
        "threshold": 4, "streak": 0
    },
    "two_even": {
        "name": "🟢 Ровно 2 четных", 
        "check": lambda n: sum(1 for x in n if x % 2 == 0) == 2, 
        "threshold": 4, "streak": 0
    },
    "total_big_45": {
        "name": "🟡 Тотал > 45.5", 
        "check": lambda n: sum(n) > 45.5, 
        "threshold": 4, "streak": 0
    },
    "sum_even_big": {
        "name": "🟡 Сумма четных > 18.5", 
        "check": lambda n: sum(x for x in n if x % 2 == 0) > 18.5, 
        "threshold": 3, "streak": 0
    },
}

last_processed_draw = None

# --- ЗАГЛУШКА ДЛЯ RENDER (чтобы сервер не отключался) ---
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

# --- ПАРСИНГ САЙТА LOTONEWS ---
def get_last_draw():
    chrome_options = Options()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument("--single-process")
    chrome_options.add_argument("--no-zygote")
    chrome_options.add_argument("--disable-extensions")
    chrome_options.binary_location = "/usr/bin/chromium"

    driver = None
    try:
        driver = webdriver.Chrome(options=chrome_options)
        driver.get(ARCHIVE_URL)
        wait = WebDriverWait(driver, 20)
        wait.until(EC.presence_of_element_located((By.TAG_NAME, "table")))
        time.sleep(3)
        soup = BeautifulSoup(driver.page_source, 'html.parser')
        for row in soup.find_all('tr'):
            row_text = row.get_text(" ", strip=True)
            pattern = r'\b(\d{5,6})\b[^\d]*\b([1-9]|1[0-9]|2[0-6])\b[^\d]*\b([1-9]|1[0-9]|2[0-6])\b[^\d]*\b([1-9]|1[0-9]|2[0-6])\b[^\d]*\b([1-9]|1[0-9]|2[0-6])\b'
            match = re.search(pattern, row_text)
            if match:
                return {"draw": int(match.group(1)), "date": time.strftime("%d.%m.%Y"), "numbers": [int(match.group(i)) for i in range(2, 6)]}
        return None
    except Exception as e:
        print(f"❌ Ошибка Selenium: {e}", flush=True)
        return None
    finally:
        if driver:
            driver.quit()

# --- ОТПРАВКА В LOLKA ---
def send_to_lolka(webhook_url, text):
    if not webhook_url:
        print(f"❌ ОШИБКА: URL вебхука не задан!", flush=True)
        return
    try:
        res = requests.post(webhook_url, json={"content": text, "username": "Betting Signals"}, timeout=10)
        res.raise_for_status()
        print("✅ Сигнал отправлен в канал ставок.", flush=True)
    except Exception as e:
        print(f"❌ Ошибка отправки: {e}", flush=True)

# --- ОСНОВНОЙ ЦИКЛ ---
def main_loop():
    global last_processed_draw
    load_quotes()
    print("🚀 Мониторинг запущен. Отслеживаем только сигналы для ставок.", flush=True)

    while True:
        print("Начинаю новую проверку...", flush=True)
        draw_data = get_last_draw()

        if draw_data:
            draw_num = draw_data["draw"]
            nums = draw_data["numbers"]

            if draw_num != last_processed_draw:
                print(f"\n🔍 Новый тираж: №{draw_num}, числа: {nums}", flush=True)
                last_processed_draw = draw_num

                bets_alerts = []

                # Проверяем все триггеры
                for key, trig in BETS_TRIGGERS.items():
                    if trig["check"](nums):
                        # Событие выпало - сбрасываем счетчик
                        if trig["streak"] > 0:
                            print(f"   ✅ {trig['name']} выпало (серия {trig['streak']} прервана).", flush=True)
                        trig["streak"] = 0
                    else:
                        # Событие не выпало - увеличиваем счетчик
                        trig["streak"] += 1
                        if trig["streak"] >= trig["threshold"]:
                            bets_alerts.append(f"{trig['name']} — не выпадает **{trig['streak']}** тираж(ей) подряд (порог: {trig['threshold']}).")

                # Если есть сигналы - шлем сообщение
                if bets_alerts:
                    quote = random.choice(QUOTES)
                    msg = f"🎰 **Сигнал для ставки (тираж №{draw_num})** 🎰\n"
                    msg += f"📅 {draw_data['date']} | Числа: {', '.join(map(str, nums))}\n\n"
                    for alert in bets_alerts: 
                        msg += f"💰 {alert}\n"
                    msg += f"\n> *{quote}*"
                    send_to_lolka(WEBHOOK_BETS, msg)
                else:
                    print("Все события в пределах нормы. Сигналов нет.", flush=True)

            else:
                print(f"Тираж №{draw_num} уже был обработан ранее.", flush=True)
        else:
            print("Не удалось получить данные. Ждем 5 минут.", flush=True)

        print("--- Пауза 5 минут ---", flush=True)
        time.sleep(300)

if __name__ == '__main__':
    threading.Thread(target=run_dummy_server, daemon=True).start()
    main_loop()