import os
import sys
import time
import threading
import re
import requests
import base64
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
WEBHOOK_BETS = os.environ.get('LOLKA_WEBHOOK_BETS', 'https://lolka.app/api/webhooks/959166700160000/OTU5MTY2NzAwMTYwMDAw.F9cmMMafG_5i_MU1xVPx6LcIjZDNUZK2VqS14vYvGw4')
GITHUB_TOKEN = os.environ.get('GITHUB_TOKEN')
GITHUB_REPO = os.environ.get('GITHUB_REPO', 'Beduin82/Lolka') 
GITHUB_FILE = "data.csv"
ARCHIVE_URL = "https://www.lotonews.ru/draws/archive/dvazhdydva"
CSV_FILE = "data.csv"

BETS_TRIGGERS = {
    "big_diff": {"name": "🟢 Разность (Max-Min) > 14.5", "check": lambda n: (max(n) - min(n)) > 14.5, "threshold": 3, "streak": 0},
    "total_less_53": {"name": "🟢 Тотал < 53.5", "check": lambda n: sum(n) < 53.5, "threshold": 4, "streak": 0},
    "two_even": {"name": "🟢 Ровно 2 четных", "check": lambda n: sum(1 for x in n if x % 2 == 0) == 2, "threshold": 4, "streak": 0},
    "total_big_45": {"name": "🟡 Тотал > 45.5", "check": lambda n: sum(n) > 45.5, "threshold": 4, "streak": 0},
    "sum_even_big": {"name": "🟡 Сумма четных > 18.5", "check": lambda n: sum(x for x in n if x % 2 == 0) > 18.5, "threshold": 3, "streak": 0},
}

last_processed_draw = None
duplicate_streak = 0

# --- ПЕРЕМЕННЫЕ ДЛЯ СИМУЛЯЦИИ СТАВОК ---
previous_prediction = []
success_predictions = 0
fail_predictions = 0
bank_spent = 0.0
bank_won = 0.0
BET_PER_NUMBER = 100 

# --- ЗАГРУЗКА БАЗЫ ---
def load_draws(filename=CSV_FILE):
    draws = []
    try:
        with open(filename, 'r', encoding='utf-8-sig') as f:
            for line in f:
                match = re.search(r'\b(\d{5,6})\b[^\d]*\b([1-9]|1[0-9]|2[0-6])\b[^\d]*\b([1-9]|1[0-9]|2[0-6])\b[^\d]*\b([1-9]|1[0-9]|2[0-6])\b[^\d]*\b([1-9]|1[0-9]|2[0-6])\b', line)
                if match:
                    draws.append((int(match.group(1)), [int(match.group(i)) for i in range(2, 6)]))
    except Exception:
        pass
    return draws

# --- ГЕНЕРАЦИЯ ПРОГНОЗА ---
def get_4_cold_numbers():
    draws = load_draws()
    if not draws:
        return [1, 2, 3, 4] 
        
    current_gap = {n: 0 for n in range(1, 27)}
    for _, nums in draws:
        for n in range(1, 27):
            if n in nums: current_gap[n] = 0
            else: current_gap[n] += 1
            
    sorted_gaps = sorted(current_gap.items(), key=lambda x: x[1], reverse=True)
    return [n for n, _ in sorted_gaps[:4]]

# --- ОБНОВЛЕНИЕ GITHUB ---
def update_github_csv(draw_num, numbers):
    if not GITHUB_TOKEN: return
    api_url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_FILE}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}
    try:
        r = requests.get(api_url, headers=headers)
        if r.status_code == 404: return
        file_data = r.json()
        current_sha = file_data['sha']
        current_content = base64.b64decode(file_data['content']).decode('utf-8')
        if f"\n{draw_num}," in current_content or current_content.startswith(f"{draw_num},"): return
        if current_content.endswith('\n'): current_content = current_content[:-1]
        new_line = f"\n{draw_num},{numbers[0]},{numbers[1]},{numbers[2]},{numbers[3]}"
        encoded_content = base64.b64encode((current_content + new_line).encode('utf-8')).decode('utf-8')
        payload = {"message": f"Auto-update: add draw {draw_num}", "content": encoded_content, "sha": current_sha}
        requests.put(api_url, headers=headers, json=payload).raise_for_status()
        print(f"✅ data.csv обновлен (тираж {draw_num}).", flush=True)
        with open(CSV_FILE, 'a', encoding='utf-8') as f: f.write(new_line)
    except Exception as e: print(f"❌ Ошибка GitHub: {e}", flush=True)

# --- СЕРВЕР ДЛЯ RENDER ---
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
        if driver: driver.quit()

# --- ОТПРАВКА В LOLKA ---
def send_to_lolka(webhook_url, text):
    if not webhook_url: return
    if len(text) > 1900: text = text[:1900] + "\n...(обрезано)"
    try:
        res = requests.post(webhook_url, json={"content": text, "username": "Lotto Analytics"}, timeout=10)
        res.raise_for_status()
        print("✅ Сообщение отправлено в Lolka.", flush=True)
    except Exception as e:
        print(f"❌ Ошибка отправки: {e}", flush=True)

# --- ОСНОВНОЙ ЦИКЛ ---
def main_loop():
    global last_processed_draw, duplicate_streak
    global previous_prediction, success_predictions, fail_predictions, bank_spent, bank_won
    
    print("🚀 Мониторинг запущен. Симулятор прогнозов активен.", flush=True)
    previous_prediction = get_4_cold_numbers()

    while True:
        try:
            print("Начинаю новую проверку...", flush=True)
            draw_data = get_last_draw()

            if draw_data:
                draw_num = draw_data["draw"]
                nums = draw_data["numbers"]

                if draw_num != last_processed_draw:
                    print(f"\n🔍 Новый тираж: №{draw_num}, числа: {nums}", flush=True)
                    last_processed_draw = draw_num

                    update_github_csv(draw_num, nums)

                    # --- 1. ПРОВЕРКА ПРЕДЫДУЩЕГО ПРОГНОЗА ---
                    pred_msg = ""
                    if previous_prediction:
                        hits = [n for n in previous_prediction if n in nums]
                        bank_spent += 4 * BET_PER_NUMBER
                        win_amount = (4 - len(hits)) * BET_PER_NUMBER * 1.14
                        bank_won += win_amount
                        
                        if len(hits) == 0:
                            success_predictions += 1
                            pred_msg = "✅ **Прогноз сбылся!** Ни одно из 4 чисел не выпало.\n"
                        else:
                            fail_predictions += 1
                            pred_msg = f"❌ **Прогноз провален.** Выпали: {', '.join(map(str, hits))}.\n"

                    # --- 2. ГЕНЕРАЦИЯ НОВОГО ПРОГНОЗА ---
                    new_prediction = get_4_cold_numbers()
                    previous_prediction = new_prediction
                    
                    # --- 3. СТАТИСТИКА БАНКА ---
                    total_preds = success_predictions + fail_predictions
                    win_rate = (success_predictions / total_preds * 100) if total_preds > 0 else 0
                    profit = bank_won - bank_spent
                    margin = ((bank_won / bank_spent - 1) * 100) if bank_spent > 0 else 0
                    
                    stats_msg = (
                        f"📊 **Статистика прогнозов:**\n"
                        f"📉 Провалов: {fail_predictions} | 📈 Успехов: {success_predictions}\n"
                        f"🎯 Win Rate: {win_rate:.1f}%\n"
                        f"💵 Банк: {bank_spent:.0f} ₽ -> {bank_won:.0f} ₽ (Маржа: {margin:+.1f}%)\n"
                        f"📈 Чистая прибыль: {profit:+.0f} ₽\n"
                    )

                    # --- 4. ПРОВЕРКА СИГНАЛОВ ---
                    bets_alerts = []
                    for key, trig in BETS_TRIGGERS.items():
                        if trig["check"](nums): trig["streak"] = 0
                        else:
                            trig["streak"] += 1
                            if trig["streak"] >= trig["threshold"]:
                                bets_alerts.append(f"{trig['name']} (нет {trig['streak']} тиража)")

                    # --- 5. ПРОВЕРКА ЗЕРКАЛ ---
                    has_duplicates = len(set(nums)) < 4
                    if has_duplicates:
                        duplicate_streak += 1
                        dup_str = ", ".join([str(x) for x in set(nums) if nums.count(x) > 1])
                        bets_alerts.append(f"🪞 Зеркало: **{dup_str}** (серия: {duplicate_streak})")
                    else:
                        duplicate_streak = 0

                    # --- 6. ФОРМИРОВАНИЕ СООБЩЕНИЯ ---
                    msg = f"🎲 **Тираж №{draw_num}** | Числа: **{', '.join(map(str, nums))}**\n\n"
                    msg += pred_msg + "\n"
                    
                    if bets_alerts:
                        msg += "⚠️ **Сигналы:**\n"
                        for alert in bets_alerts: msg += f"💰 {alert}\n"
                        msg += "\n"
                    
                    msg += stats_msg
                    msg += f"\n🔮 **Прогноз на следующий тираж:** Числа `{', '.join(map(str, new_prediction))}` НЕ выпадут."
                    
                    send_to_lolka(WEBHOOK_BETS, msg)

                    # Залп при 3+ совпадениях
                    if duplicate_streak >= 3:
                        streak_msg = (
                            f"🚨🚨🚨 **ВНИМАНИЕ! {duplicate_streak} ТИРАЖА ПОДРЯД С СОВПАДЕНИЯМИ!** 🚨🚨🚨\n\n"
                            f"📅 Дата: {draw_data['date']} | Тираж: №{draw_num}\n"
                            f"🎲 Числа: {', '.join(map(str, nums))}"
                        )
                        for _ in range(5):
                            send_to_lolka(WEBHOOK_BETS, streak_msg)
                            time.sleep(1)

                else:
                    print(f"Тираж №{draw_num} уже был обработан ранее.", flush=True)
            else:
                print("Не удалось получить данные. Ждем 5 минут.", flush=True)
                
        except Exception as e:
            print(f"❌ Критическая ошибка в цикле: {e}", flush=True)

        print("--- Пауза 5 минут ---", flush=True)
        time.sleep(300)

if __name__ == '__main__':
    threading.Thread(target=run_dummy_server, daemon=True).start()
    main_loop()