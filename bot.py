import os
import sys
import time
import threading
import re
import requests
import base64
import json
import subprocess
import gc
from datetime import datetime
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
GITHUB_DATA_FILE = "data.csv"
GITHUB_STATS_FILE = "bot_stats.json"
URL_LOTONEWS = "https://www.lotonews.ru/draws/archive/dvazhdydva"
CSV_FILE = "data.csv"

last_processed_draw = None
last_success_time = time.time()

# --- СПРАВОЧНИК ВСЕХ СОБЫТИЙ ---
# (Название, функция проверки)
EVENTS = {
    # Сумма
    "Сумма > 45.5": lambda n: sum(n) > 45.5,
    "Сумма > 50.5": lambda n: sum(n) > 50.5,
    "Сумма > 53.5": lambda n: sum(n) > 53.5,
    "Сумма < 53.5": lambda n: sum(n) < 53.5,
    "Сумма > 57.5": lambda n: sum(n) > 57.5,
    "Сумма < 57.5": lambda n: sum(n) < 57.5,
    "Сумма > 61.5": lambda n: sum(n) > 61.5,
    "Сумма < 61.5": lambda n: sum(n) < 61.5,
    # Мин/Макс
    "Мин > 3.5": lambda n: min(n) > 3.5,
    "Мин > 4.5": lambda n: min(n) > 4.5,
    "Макс < 20.5": lambda n: max(n) < 20.5,
    "Макс < 22.5": lambda n: max(n) < 22.5,
    # Разность
    "Разн > 14.5": lambda n: (max(n) - min(n)) > 14.5,
    "Разн < 14.5": lambda n: (max(n) - min(n)) < 14.5,
    "Разн > 16.5": lambda n: (max(n) - min(n)) > 16.5,
    "Разн < 16.5": lambda n: (max(n) - min(n)) < 16.5,
    "Разн > 18.5": lambda n: (max(n) - min(n)) > 18.5,
    "Разн < 18.5": lambda n: (max(n) - min(n)) < 18.5,
    # Четность
    "Ровно 2 чет": lambda n: sum(1 for x in n if x % 2 == 0) == 2,
    "Ровно 1 чет": lambda n: sum(1 for x in n if x % 2 == 0) == 1,
    "Ровно 3 чет": lambda n: sum(1 for x in n if x % 2 == 0) == 3,
    # Кратность
    "Кратен 2": lambda n: any(x % 2 == 0 for x in n),
    "Кратен 3": lambda n: any(x % 3 == 0 for x in n),
    # Зеркала
    "Дубли (Зеркала)": lambda n: len(set(n)) < 4
}

state = {
    "duplicate_streak": 0,
    "prev_preds": {}, # Сюда сохраняем прогнозы: {"Сумма > 45.5": True, ...}
    "prev_hot": [],
    "prev_cold": []
}

# --- РАБОТА С GITHUB JSON ---
def load_bot_stats():
    global state
    if not GITHUB_TOKEN: return
    api_url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_STATS_FILE}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}
    try:
        r = requests.get(api_url, headers=headers)
        if r.status_code == 404: return
        file_data = r.json()
        if 'content' not in file_data or not file_data['content'].strip(): return
        content = base64.b64decode(file_data['content']).decode('utf-8').strip()
        if not content: return
        state.update(json.loads(content))
        print("📥 Стейт загружен.", flush=True)
    except Exception: pass

def save_bot_stats():
    if not GITHUB_TOKEN: return
    api_url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_STATS_FILE}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}
    try:
        r = requests.get(api_url, headers=headers)
        current_sha = r.json()['sha'] if r.status_code == 200 else None
        encoded_content = base64.b64encode(json.dumps(state, indent=2).encode('utf-8')).decode('utf-8')
        payload = {"message": "Auto-update stats", "content": encoded_content}
        if current_sha: payload["sha"] = current_sha
        requests.put(api_url, headers=headers, json=payload).raise_for_status()
    except Exception: pass

# --- АНАЛИЗ И ПРОГНОЗ ---
def get_hot_cold(draws):
    freq = {n: 0 for n in range(1, 27)}
    last_seen = {n: -1 for n in range(1, 27)}
    for i, (_, nums) in enumerate(draws):
        for n in nums:
            freq[n] += 1
            last_seen[n] = i

    sorted_hot = sorted(freq.items(), key=lambda x: x[1], reverse=True)
    hot = [n for n, _ in sorted_hot[:4]]
    
    sorted_cold = sorted(last_seen.items(), key=lambda x: x[1])
    cold = [n for n, _ in sorted_cold[:4]]
    return hot, cold

def generate_predictions(draws):
    """Анализирует историю и выдает прогноз для каждого события."""
    preds = {}
    total_draws = len(draws)
    
    for name, func in EVENTS.items():
        # Считаем сколько раз событие выпадало
        hits = sum(1 for _, nums in draws if func(nums))
        if hits == 0:
            preds[name] = True # Если не выпадало никогда, ставим Да
            continue
            
        avg_period = total_draws / hits
        
        # Считаем текущий пропуск
        current_gap = 0
        for _, nums in reversed(draws):
            if func(nums):
                break
            current_gap += 1
            
        # Логика прогноза: если пропуск больше среднего, ставим Да
        preds[name] = current_gap >= avg_period
        
    return preds

# --- РАБОТА С АРХИВОМ ---
def load_draws(filename=CSV_FILE):
    draws = []
    try:
        with open(filename, 'r', encoding='utf-8-sig') as f:
            for line in f:
                match = re.search(r'\b(\d{5,6})\b[^\d]*\b([1-9]|1[0-9]|2[0-6])\b[^\d]*\b([1-9]|1[0-9]|2[0-6])\b[^\d]*\b([1-9]|1[0-9]|2[0-6])\b[^\d]*\b([1-9]|1[0-9]|2[0-6])\b', line)
                if match:
                    draws.append((int(match.group(1)), [int(match.group(i)) for i in range(2, 6)]))
    except Exception: pass
    return draws

def get_max_draw_from_csv():
    draws = load_draws()
    return max(d[0] for d in draws) if draws else 0

def update_github_csv(draw_num, numbers):
    if not GITHUB_TOKEN: return False
    api_url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_DATA_FILE}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}
    try:
        r = requests.get(api_url, headers=headers)
        if r.status_code == 404: return False
        file_data = r.json(); current_sha = file_data['sha']
        current_content = base64.b64decode(file_data['content']).decode('utf-8')
        if f"\n{draw_num}," in current_content or current_content.startswith(f"{draw_num},"): return True
        if current_content.endswith('\n'): current_content = current_content[:-1]
        new_line = f"\n{draw_num},{numbers[0]},{numbers[1]},{numbers[2]},{numbers[3]}"
        encoded_content = base64.b64encode((current_content + new_line).encode('utf-8')).decode('utf-8')
        res = requests.put(api_url, headers=headers, json={"message": f"Auto-update: add draw {draw_num}", "content": encoded_content, "sha": current_sha})
        res.raise_for_status()
        with open(CSV_FILE, 'a', encoding='utf-8') as f: f.write(new_line)
        return True
    except Exception: return False

def send_to_lolka(text):
    if not WEBHOOK_BETS: return
    chunks = []
    while len(text) > 1800:
        split_idx = text.rfind('\n', 0, 1800)
        if split_idx == -1: split_idx = 1800
        chunks.append(text[:split_idx])
        text = text[split_idx:].strip()
    if text: chunks.append(text)
    
    for chunk in chunks:
        try:
            requests.post(WEBHOOK_BETS, json={"content": chunk, "username": "Lotto Analytics"}, timeout=10)
            time.sleep(1)
        except Exception: pass

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
    HTTPServer(('0.0.0.0', port), DummyHandler).serve_forever()

# --- НАДЕЖНЫЙ SELENIUM ---
def get_lotonews_thread(result_dict):
    driver = None
    try:
        subprocess.run("pkill -9 -f chromium", shell=True)
        subprocess.run("pkill -9 -f chrome", shell=True)
        time.sleep(2)
        chrome_options = Options()
        chrome_options.add_argument("--headless=new")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument("--disable-software-rasterizer")
        chrome_options.add_argument("--blink-settings=imagesEnabled=false")
        chrome_options.add_argument("--single-process")
        chrome_options.add_argument("--no-zygote")
        chrome_options.binary_location = "/usr/bin/chromium"

        print("🌐 Запускаю браузер...", flush=True)
        driver = webdriver.Chrome(options=chrome_options)
        driver.set_page_load_timeout(15)
        
        driver.get(f"{URL_LOTONEWS}?_={int(time.time())}")
        WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.TAG_NAME, "table")))
        time.sleep(1) 
        
        soup = BeautifulSoup(driver.page_source, 'html.parser')
        rows = soup.find_all('tr')
        parsed = []
        
        for row in rows:
            row_text = row.get_text(" ", strip=True)
            match = re.search(r'(\d{2}\.\d{2}\.\d{4}\s+\d{1,2}:\d{2})\s+(\d{5,6})\s+([1-9]|1[0-9]|2[0-6])\s+([1-9]|1[0-9]|2[0-6])\s+([1-9]|1[0-9]|2[0-6])\s+([1-9]|1[0-9]|2[0-6])', row_text)
            if match:
                parsed.append({
                    "draw": int(match.group(2)),
                    "numbers": [int(match.group(i)) for i in range(3, 7)],
                    "datetime": match.group(1)
                })
        
        if parsed:
            result_dict["draws"] = parsed
            print(f"✅ Найдено {len(parsed)} тиражей.", flush=True)
    except Exception as e:
        print(f"❌ Ошибка Selenium: {e}", flush=True)
    finally:
        if driver: 
            try: driver.quit()
            except: pass
        subprocess.run("pkill -9 -f chromium", shell=True)
        subprocess.run("pkill -9 -f chrome", shell=True)
        gc.collect()

def get_recent_draws():
    result_dict = {"draws": []}
    t = threading.Thread(target=get_lotonews_thread, args=(result_dict,))
    t.daemon = True
    t.start()
    t.join(timeout=30) 
    if t.is_alive():
        print("🚨 ТАЙМАУТ! LotoNews завис.", flush=True)
        subprocess.run("pkill -9 -f chromium", shell=True)
        subprocess.run("pkill -9 -f chrome", shell=True)
        return []
    return sorted(result_dict["draws"], key=lambda x: x["draw"])

# --- ОСНОВНОЙ ЦИКЛ ---
def main_loop():
    global last_processed_draw, last_success_time, state
    
    load_bot_stats()
    draws = load_draws()
    if draws:
        last_processed_draw = get_max_draw_from_csv()
        if not state.get("prev_preds"):
            state["prev_preds"] = generate_predictions(draws)
            h, c = get_hot_cold(draws)
            state["prev_hot"] = h
            state["prev_cold"] = c
            save_bot_stats()
        print(f"📌 Старт. Последний тираж: {last_processed_draw}.", flush=True)

    while True:
        try:
            print("🔄 Проверяю LotoNews...", flush=True)
            recent_draws = get_recent_draws()
            
            if recent_draws:
                last_success_time = time.time()
                missing_draws = [d for d in recent_draws if d["draw"] > (last_processed_draw or 0)]
                
                if missing_draws:
                    print(f"🚀 Новых тиражей: {len(missing_draws)}", flush=True)
                    for draw_data in missing_draws:
                        draw_num = draw_data["draw"]
                        nums = draw_data["numbers"]
                        dt_str = draw_data["datetime"]
                        
                        if not update_github_csv(draw_num, nums):
                            continue
                        
                        last_processed_draw = draw_num

                        # 1. Считаем дубли
                        if len(set(nums)) < 4:
                            state["duplicate_streak"] += 1
                        else:
                            state["duplicate_streak"] = 0

                        # 2. Сверяем прошлые прогнозы
                        check_msg = "📊 **Результаты прошлых прогнозов:**\n"
                        yes_correct = 0
                        yes_total = 0
                        
                        for name, func in EVENTS.items():
                            if name in state["prev_preds"]:
                                predicted_yes = state["prev_preds"][name]
                                actual_result = func(nums)
                                
                                if predicted_yes:
                                    yes_total += 1
                                    if actual_result:
                                        yes_correct += 1
                                        check_msg += f"✅ {name}\n"
                                    else:
                                        check_msg += f"x {name}\n"
                                else:
                                    if not actual_result:
                                        pass # Если мы ставили "Нет" и не выпало, это норм, не спамим
                                    else:
                                        pass # Если ставили "Нет", а выпало, это просто пропущенная ставка
                        
                        if yes_total > 0:
                            accuracy = (yes_correct / yes_total) * 100
                            check_msg = f"📊 **Прогнозы (точность {accuracy:.0f}%):**\n" + check_msg.split('\n', 1)[1]
                        else:
                            check_msg = ""

                        # 3. Генерируем новые прогнозы
                        current_history = load_draws()
                        new_preds = generate_predictions(current_history)
                        state["prev_preds"] = new_preds
                        
                        new_hot, new_cold = get_hot_cold(current_history)
                        state["prev_hot"] = new_hot
                        state["prev_cold"] = new_cold

                        save_bot_stats()

                        # 4. Формируем сообщение
                        msg = f"📅 {dt_str}\n\n"
                        msg += f"🎲 **№{draw_num}** | `{', '.join(map(str, nums))}`"
                        if state["duplicate_streak"] > 0:
                            msg += f" (Дубли: {state['duplicate_streak']})"
                        msg += "\n\n"
                        
                        if check_msg:
                            msg += check_msg + "\n"
                        
                        # Блок прогнозов (только на "Да")
                        msg += f"🔮 **Прогноз (Ожидается выпадение):**\n"
                        active_preds = [name for name, val in new_preds.items() if val]
                        
                        # Разбиваем по 3-4 штуки на строку для компактности
                        for i in range(0, len(active_preds), 3):
                            msg += " | ".join(active_preds[i:i+3]) + "\n"
                        
                        msg += f"\n🔥 Горячие: **{', '.join(map(str, new_hot))}**\n"
                        msg += f"🧊 Холодные: **{', '.join(map(str, new_cold))}**"
                        
                        send_to_lolka(msg)
                        print(f"✅ Тираж №{draw_num} отправлен.", flush=True)

                        # 5. Фейерверк
                        if state["duplicate_streak"] >= 3:
                            firework = f"🚨🚨🚨 **ВНИМАНИЕ! {state['duplicate_streak']} ТИРАЖА ПОДРЯД С ДУБЛЯМИ!** 🚨🚨🚨\n📅 Тираж: №{draw_num}\n"
                            for _ in range(5):
                                send_to_lolka(firework)
                                time.sleep(1)
                        time.sleep(2) 
                else:
                    print(f"✅ Новых нет (последний {last_processed_draw}).", flush=True)
            else:
                print("⚠️ Ошибка получения данных.", flush=True)
                
        except Exception as e:
            print(f"❌ Ошибка: {e}", flush=True)

        print("💤 Пауза 5 минут...", flush=True)
        time.sleep(300)

if __name__ == '__main__':
    threading.Thread(target=run_dummy_server, daemon=True).start()
    main_loop()