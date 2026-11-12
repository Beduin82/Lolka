import os
import sys
import csv
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
WEBHOOK_BETS = os.environ.get('LOLKA_WEBHOOK_BETS')
ARCHIVE_URL = "https://www.lotonews.ru/draws/archive/dvazhdydva"
CSV_FILE = "data.csv"

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

BETS_TRIGGERS = {
    "big_diff": {"name": "🟢 Разность (Max-Min) > 14.5", "check": lambda n: (max(n) - min(n)) > 14.5, "threshold": 3, "streak": 0},
    "total_less_53": {"name": "🟢 Тотал < 53.5", "check": lambda n: sum(n) < 53.5, "threshold": 4, "streak": 0},
    "two_even": {"name": "🟢 Ровно 2 четных", "check": lambda n: sum(1 for x in n if x % 2 == 0) == 2, "threshold": 4, "streak": 0},
    "total_big_45": {"name": "🟡 Тотал > 45.5", "check": lambda n: sum(n) > 45.5, "threshold": 4, "streak": 0},
    "sum_even_big": {"name": "🟡 Сумма четных > 18.5", "check": lambda n: sum(x for x in n if x % 2 == 0) > 18.5, "threshold": 3, "streak": 0},
}

last_processed_draw = None
draws_since_report = 0 
REPORT_EVERY_N_DRAWS = 50  # Отчет по CSV будет приходить каждые 50 новых тиражей

# --- ФУНКЦИЯ АНАЛИЗА CSV ---
def analyze_csv(filename=CSV_FILE):
    draws = []
    try:
        with open(filename, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    draw_num = int(row['draw'])
                    nums = [int(row['n1']), int(row['n2']), int(row['n3']), int(row['n4'])]
                    if all(1 <= n <= 26 for n in nums):
                        draws.append((draw_num, nums))
                except (ValueError, KeyError):
                    continue
    except FileNotFoundError:
        return "⚠️ Файл data.csv не найден. Загрузите его на GitHub для анализа статистики."
    except Exception as e:
        return f"❌ Ошибка чтения файла data.csv: {e}"

    if not draws:
        return "❌ В файле data.csv нет валидных тиражей."

    total = len(draws)

    # 1. Частота чисел
    freq = {n: 0 for n in range(1, 27)}
    for _, nums in draws:
        for n in nums:
            freq[n] += 1
    hot = sorted(freq.items(), key=lambda x: x[1], reverse=True)[:5]

    # 2. Текущий и максимальный пропуск
    current_gap = {n: 0 for n in range(1, 27)}
    max_gap = {n: 0 for n in range(1, 27)}
    for _, nums in draws:
        for n in range(1, 27):
            if n in nums:
                current_gap[n] = 0
            else:
                current_gap[n] += 1
                if current_gap[n] > max_gap[n]:
                    max_gap[n] = current_gap[n]
    cold = sorted(current_gap.items(), key=lambda x: x[1], reverse=True)[:5]

    # 3. Серии совпадений
    duplicate_streak = 0
    max_dup_streak = 0
    dup_count = 0
    for _, nums in draws:
        if len(set(nums)) < 4:
            duplicate_streak += 1
            dup_count += 1
            if duplicate_streak > max_dup_streak:
                max_dup_streak = duplicate_streak
        else:
            duplicate_streak = 0

    # 4. Статистика сумм и четности
    even_count_total = 0
    odd_count_total = 0
    totals = []
    for _, nums in draws:
        totals.append(sum(nums))
        for n in nums:
            if n % 2 == 0: even_count_total += 1
            else: odd_count_total += 1
    avg_sum = sum(totals) / total

    two_even_gap = 0
    max_two_even_gap = 0
    for _, nums in draws:
        ec = sum(1 for n in nums if n % 2 == 0)
        if ec == 2:
            two_even_gap = 0
        else:
            two_even_gap += 1
            if two_even_gap > max_two_even_gap:
                max_two_even_gap = two_even_gap

    report = f"📊 **АНАЛИЗ БАЗЫ ДАННЫХ** (тиражей: {total})\n\n"
    report += "🔥 **ТОП-5 ЧАСТЫХ ЧИСЕЛ:**\n"
    for n, c in hot:
        report += f"   • {n}: {c} раз\n"
    report += "\n🧊 **ТОП-5 ХОЛОДНЫХ (текущий пропуск):**\n"
    for n, g in cold:
        report += f"   • {n}: не выпадало {g} тираж(ей) (макс: {max_gap[n]})\n"
    report += f"\n🪞 **СОВПАДЕНИЯ (зеркала):**\n"
    report += f"   • Всего тиражей с совпадением: {dup_count} ({dup_count/total*100:.1f}%)\n"
    report += f"   • Максимальная серия подряд: {max_dup_streak}\n"
    report += f"\n⚖️ **ЧЕТНОСТЬ:**\n"
    report += f"   • Четных чисел: {even_count_total}\n"
    report += f"   • Нечетных чисел: {odd_count_total}\n"
    report += f"   • Текущий пропуск «Ровно 2 четных»: {two_even_gap} (макс: {max_two_even_gap})\n"
    report += f"\n📈 **СУММЫ ТИРАЖЕЙ:**\n"
    report += f"   • Средняя: {avg_sum:.1f}\n"
    report += f"   • Минимум: {min(totals)}\n"
    report += f"   • Максимум: {max(totals)}\n"
    return report

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

# --- ПАРСИНГ САЙТА ---
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
        print(f"❌ ОШИБКА: URL вебхука LOLKA_WEBHOOK_BETS не задан!", flush=True)
        return
    # Lolka ограничивает размер сообщения (2000 символов). Режем, если слишком длинно.
    if len(text) > 1900:
        text = text[:1900] + "\n...(обрезано)"
    try:
        res = requests.post(webhook_url, json={"content": text, "username": "Lotto Analytics"}, timeout=10)
        res.raise_for_status()
        print("✅ Сообщение отправлено в Lolka.", flush=True)
    except Exception as e:
        print(f"❌ Ошибка отправки в Lolka: {e}", flush=True)

# --- ОСНОВНОЙ ЦИКЛ ---
def main_loop():
    global last_processed_draw, draws_since_report
    print("🚀 Мониторинг запущен. Отслеживаем сигналы + анализ CSV.", flush=True)

    # 1. Отправляем первый отчет сразу при запуске
    print("📊 Формирую стартовый отчёт по CSV...", flush=True)
    initial_report = analyze_csv()
    send_to_lolka(WEBHOOK_BETS, initial_report)

    while True:
        print("Начинаю новую проверку (запуск браузера)...", flush=True)
        draw_data = get_last_draw()

        if draw_data:
            draw_num = draw_data["draw"]
            nums = draw_data["numbers"]

            if draw_num != last_processed_draw:
                print(f"\n🔍 Новый тираж: №{draw_num}, числа: {nums}", flush=True)
                last_processed_draw = draw_num
                draws_since_report += 1

                # Проверка триггеров для ставок
                bets_alerts = []
                for key, trig in BETS_TRIGGERS.items():
                    if trig["check"](nums):
                        if trig["streak"] > 0:
                            print(f"   ✅ {trig['name']} выпало (серия {trig['streak']} прервана).", flush=True)
                        trig["streak"] = 0
                    else:
                        trig["streak"] += 1
                        if trig["streak"] >= trig["threshold"]:
                            bets_alerts.append(f"{trig['name']} — не выпадает **{trig['streak']}** тираж(ей) подряд (порог: {trig['threshold']}).")

                # Если есть сигналы - шлем
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

                # 2. Раз в 50 тиражей отправляем общий отчет
                if draws_since_report >= REPORT_EVERY_N_DRAWS:
                    print(f"📊 Прошло {draws_since_report} тиражей. Отправляю свежий отчёт...", flush=True)
                    report = analyze_csv()
                    send_to_lolka(WEBHOOK_BETS, report)
                    draws_since_report = 0

            else:
                print(f"Тираж №{draw_num} уже был обработан ранее.", flush=True)
        else:
            print("Не удалось получить данные. Ждем 5 минут.", flush=True)

        print("--- Пауза 5 минут ---", flush=True)
        time.sleep(300)

if __name__ == '__main__':
    threading.Thread(target=run_dummy_server, daemon=True).start()
    main_loop()