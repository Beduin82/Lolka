import os
import sys
import time
import threading
import re
import requests
import base64
import json
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

URL_LOTONEWS = "https://www.lotonews.ru/draws/archive/dvazhdydva"
CSV_FILE = "data.csv"

last_processed_draw = None
last_success_time = time.time()

# Стейт для серий и гистограмм
streaks = {
    "mirror": {"current": 0, "max": 0, "dist": {}},
    "two_even": {"current": 0, "max": 0, "dist": {}},
    "total_less": {"current": 0, "max": 0, "dist": {}}
}

# Статистика попаданий (Да/Нет)
hit_stats = {
    "hot": {"yes": 0, "no": 0},
    "cold": {"yes": 0, "no": 0}
}

previous_prediction = [] 

# --- ФУНКЦИИ ---
def load_draws(filename=CSV_FILE):
    draws = []
    try:
        with open(filename, 'r', encoding='utf-8-sig') as f:
            for line in f:
                match = re.search(r'\b(\d{5,6})\b[^\d]*\b([1-9]|1[0-9]|2[0-6])\b[^\d]*\b([1-9]|1[0-9]|2[0-6])\b[^\d]*\b([1-9]|1[0-9]|2[0-6])\b[^\d]*\b([1-9]|1[0-9]|2[0-6])\b', line)
                if match:
                    draws.append((int(match.group(1)), [int(match.group(i)) for i in range(2, 6)]))
    except Exception as e: 
        print(f"Ошибка чтения CSV: {e}", flush=True)
    return draws

def get_max_draw_from_csv():
    draws = load_draws()
    return max(d[0] for d in draws) if draws else 0

def update_github_csv(draw_num, numbers):
    if not GITHUB_TOKEN: return
    api_url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_DATA_FILE}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}
    try:
        r = requests.get(api_url, headers=headers)
        if r.status_code == 404: return
        file_data = r.json(); current_sha = file_data['sha']
        current_content = base64.b64decode(file_data['content']).decode('utf-8')
        if f"\n{draw_num}," in current_content or current_content.startswith(f"{draw_num},"): return
        if current_content.endswith('\n'): current_content = current_content[:-1]
        new_line = f"\n{draw_num},{numbers[0]},{numbers[1]},{numbers[2]},{numbers[3]}"
        encoded_content = base64.b64encode((current_content + new_line).encode('utf-8')).decode('utf-8')
        requests.put(api_url, headers=headers, json={"message": f"Auto-update: add draw {draw_num}", "content": encoded_content, "sha": current_sha}).raise_for_status()
        with open(CSV_FILE, 'a', encoding='utf-8') as f: f.write(new_line)
        print(f"✅ Архив обновлен (тираж {draw_num}).", flush=True)
    except Exception as e:
        print(f"❌ Ошибка обновления архива: {e}", flush=True)

def update_streaks(nums):
    has_mirror = len(set(nums)) < 4
    if has_mirror:
        if streaks["mirror"]["current"] > 0:
            streaks["mirror"]["dist"][streaks["mirror"]["current"]] = streaks["mirror"]["dist"].get(streaks["mirror"]["current"], 0) + 1
        streaks["mirror"]["current"] = 0
    else:
        streaks["mirror"]["current"] += 1
        streaks["mirror"]["max"] = max(streaks["mirror"]["max"], streaks["mirror"]["current"])

    has_two_even = (sum(1 for x in nums if x % 2 == 0) == 2)
    if has_two_even:
        if streaks["two_even"]["current"] > 0:
            streaks["two_even"]["dist"][streaks["two_even"]["current"]] = streaks["two_even"]["dist"].get(streaks["two_even"]["current"], 0) + 1
        streaks["two_even"]["current"] = 0
    else:
        streaks["two_even"]["current"] += 1
        streaks["two_even"]["max"] = max(streaks["two_even"]["max"], streaks["two_even"]["current"])

    has_total_less = sum(nums) < 53.5
    if has_total_less:
        if streaks["total_less"]["current"] > 0:
            streaks["total_less"]["dist"][streaks["total_less"]["current"]] = streaks["total_less"]["dist"].get(streaks["total_less"]["current"], 0) + 1
        streaks["total_less"]["current"] = 0
    else:
        streaks["total_less"]["current"] += 1
        streaks["total_less"]["max"] = max(streaks["total_less"]["max"], streaks["total_less"]["current"])

def get_hot_cold(draws):
    freq = {n: 0 for n in range(1, 27)}
    current_gap = {n: 0 for n in range(1, 27)}
    for _, nums in draws:
        for n in range(1, 27):
            if n in nums: 
                freq[n] += 1
                current_gap[n] = 0
            else: 
                current_gap[n] += 1
    
    sorted_hot = sorted(freq.items(), key=lambda x: x[1], reverse=True)
    hot_numbers = [n for n, _ in sorted_hot[:4]]
    
    sorted_cold = sorted(current_gap.items(), key=lambda x: x[1], reverse=True)
    cold_numbers = [n for n, _ in sorted_cold if current_gap[n] >= 15][:4]
    
    return hot_numbers, cold_numbers

def format_hist(dist):
    if not dist: return "-"
    sorted_dist = sorted(dist.items(), key=lambda x: x[1], reverse=True)[:2]
    return ", ".join([f"{k}т:{v}" for k, v in sorted_dist])

def send_to_lolka(webhook_url, text):
    if not webhook_url: return
    chunks = []
    while len(text) > 1800:
        split_idx = text.rfind('\n', 0, 1800)
        if split_idx == -1: split_idx = 1800
        chunks.append(text[:split_idx])
        text = text[split_idx:].strip()
    if text: chunks.append(text)
    
    for chunk in chunks:
        try:
            requests.post(webhook_url, json={"content": chunk, "username": "Lotto Analytics"}, timeout=10).raise_for_status()
            time.sleep(1)
        except Exception as e: 
            print(f"❌ Ошибка отправки: {e}", flush=True)

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

def get_recent_draws():
    chrome_options = Options()
    chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--disable-software-rasterizer")
    chrome_options.add_argument("--disable-extensions")
    chrome_options.add_argument("--blink-settings=imagesEnabled=false")
    chrome_options.add_argument("--single-process")
    chrome_options.add_argument("--no-zygote")
    chrome_options.binary_location = "/usr/bin/chromium"

    driver = None
    try:
        print("🌐 Запускаю браузер...", flush=True)
        driver = webdriver.Chrome(options=chrome_options)
        driver.set_page_load_timeout(40)
        driver.get(f"{URL_LOTONEWS}?_={int(time.time())}")
        
        wait = WebDriverWait(driver, 20)
        wait.until(EC.presence_of_element_located((By.TAG_NAME, "table")))
        time.sleep(2) 
        
        body_text = driver.find_element(By.TAG_NAME, "body").text
        pattern = r'\b(\d{5,6})\s+([1-9]|1[0-9]|2[0-6])\s+([1-9]|1[0-9]|2[0-6])\s+([1-9]|1[0-9]|2[0-6])\s+([1-9]|1[0-9]|2[0-6])\b'
        matches = re.findall(pattern, body_text)
        
        if not matches:
            print("⚠️ Не удалось найти тиражи. Первые 300 символов текста:", flush=True)
            print(body_text[:300], flush=True)
            return []

        unique_draws = {}
        for m in matches:
            d_num = int(m[0])
            nums = [int(m[i]) for i in range(1, 5)]
            if d_num not in unique_draws:
                unique_draws[d_num] = nums
                
        sorted_draws = [{"draw": k, "numbers": v} for k, v in sorted(unique_draws.items())]
        print(f"✅ Найдено тиражей: {len(sorted_draws)} (от {sorted_draws[0]['draw']} до {sorted_draws[-1]['draw']})", flush=True)
        return sorted_draws
        
    except Exception as e:
        print(f"❌ Ошибка Selenium: {e}", flush=True)
        return []
    finally:
        if driver: 
            try: driver.quit()
            except: pass

def main_loop():
    global last_processed_draw, last_success_time, previous_prediction, hit_stats
    
    print("🔄 Загрузка истории из архива для счетчиков...", flush=True)
    draws = load_draws()
    
    if draws:
        for i in range(len(draws)):
            nums = draws[i][1]
            update_streaks(nums)
            
            if previous_prediction:
                prev_hot = previous_prediction[:4]
                prev_cold = previous_prediction[4:8]
                hot_hits = [n for n in prev_hot if n in nums]
                cold_hits = [n for n in prev_cold if n in nums]
                
                hit_stats["hot"]["yes"] += len(hot_hits)
                hit_stats["hot"]["no"] += (4 - len(hot_hits))
                hit_stats["cold"]["yes"] += len(cold_hits)
                hit_stats["cold"]["no"] += (4 - len(cold_hits))
            
            current_history = draws[:i+1]
            hot, cold = get_hot_cold(current_history)
            previous_prediction = hot + cold

        last_processed_draw = get_max_draw_from_csv()
        print(f"📌 Последний тираж в базе: {last_processed_draw}.", flush=True)

    while True:
        try:
            if time.time() - last_success_time > 3600:
                print("🚨 СТОРОЖ: Бот завис более чем на час! Перезагрузка...", flush=True)
                os.execl(sys.executable, sys.executable, *sys.argv)

            recent_draws = get_recent_draws()
            
            if recent_draws:
                last_success_time = time.time()
                missing_draws = [d for d in recent_draws if d["draw"] > (last_processed_draw or 0)]
                
                if missing_draws:
                    print(f"\n🚀 Найдено {len(missing_draws)} новых тиражей!", flush=True)
                    
                    for draw_data in missing_draws:
                        draw_num = draw_data["draw"]
                        nums = draw_data["numbers"]
                        
                        print(f"🔍 Обработка: №{draw_num}, числа: {nums}", flush=True)
                        last_processed_draw = draw_num
                        update_github_csv(draw_num, nums)

                        if previous_prediction:
                            prev_hot = previous_prediction[:4]
                            prev_cold = previous_prediction[4:8]
                            hot_hits = [n for n in prev_hot if n in nums]
                            cold_hits = [n for n in prev_cold if n in nums]
                            
                            hit_stats["hot"]["yes"] += len(hot_hits)
                            hit_stats["hot"]["no"] += (4 - len(hot_hits))
                            hit_stats["cold"]["yes"] += len(cold_hits)
                            hit_stats["cold"]["no"] += (4 - len(cold_hits))

                        update_streaks(nums)
                        
                        current_history = load_draws()
                        new_hot, new_cold = get_hot_cold(current_history)
                        previous_prediction = new_hot + new_cold

                        msg = f"🎲 **Тираж №{draw_num}** | Числа: **{', '.join(map(str, nums))}**\n\n"
                        
                        msg += f"🪞 **Зеркала (Совпадения)**\n"
                        msg += f"▪️ Серия: **{streaks['mirror']['current']}** / {streaks['mirror']['max']}\n"
                        msg += f"-# Гистограмма: {format_hist(streaks['mirror']['dist'])}\n\n"
                        
                        msg += f"🟢 **Ровно 2 четных**\n"
                        msg += f"▪️ Серия: **{streaks['two_even']['current']}** / {streaks['two_even']['max']}\n"
                        msg += f"-# Гистограмма: {format_hist(streaks['two_even']['dist'])}\n\n"
                        
                        msg += f"🟡 **Тотал < 53.5**\n"
                        msg += f"▪️ Серия: **{streaks['total_less']['current']}** / {streaks['total_less']['max']}\n"
                        msg += f"-# Гистограмма: {format_hist(streaks['total_less']['dist'])}\n\n"
                        
                        msg += f"📊 **Статистика чисел (Да / Нет)**\n"
                        msg += f"🔥 Горячие: {hit_stats['hot']['yes']} / {hit_stats['hot']['no']}\n"
                        msg += f"🧊 Холодные: {hit_stats['cold']['yes']} / {hit_stats['cold']['no']}\n\n"
                        
                        msg += f"🔮 **Модуляция на следующий тираж**\n"
                        msg += f"🔥 Горячие: `{', '.join(map(str, new_hot))}`\n"
                        msg += f"🧊 Холодные: `{', '.join(map(str, new_cold)) if new_cold else 'Нет (меньше 15)'}`"
                        
                        send_to_lolka(WEBHOOK_BETS, msg)

                        if streaks["mirror"]["current"] == 3:
                            firework_msg = (
                                f"🚨🚨🚨 **ВНИМАНИЕ! 3 ТИРАЖА ПОДРЯД С СОВПАДЕНИЯМИ!** 🚨🚨🚨\n"
                                f"📅 Тираж: №{draw_num} | Числа: {', '.join(map(str, nums))}\n"
                                f"Готовьтесь к отскоку."
                            )
                            for _ in range(5):
                                send_to_lolka(WEBHOOK_BETS, firework_msg)
                                time.sleep(1)
                                
                        time.sleep(2) 
                else:
                    print(f"Ожидание. Последний: {last_processed_draw}.", flush=True)
            else:
                print("Не удалось получить данные. Ждем 5 минут.", flush=True)
                
        except Exception as e:
            print(f"❌ Критическая ошибка: {e}", flush=True)

        time.sleep(300)

if __name__ == '__main__':
    threading.Thread(target=run_dummy_server, daemon=True).start()
    main_loop()
