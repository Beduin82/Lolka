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
GITHUB_STATS_FILE = "bot_stats.json"

URL_LOTONEWS = "https://www.lotonews.ru/draws/archive/dvazhdydva"
URL_STOLOTO = "https://www.stoloto.ru/dvazhdydva"
CSV_FILE = "data.csv"

last_processed_draw = None
last_success_time = time.time()

# Стейт для серий
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

# --- РАБОТА С GITHUB JSON ---
def load_bot_stats():
    global streaks, hit_stats
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
        loaded_stats = json.loads(content)
        if "streaks" in loaded_stats: streaks.update(loaded_stats["streaks"])
        if "hit_stats" in loaded_stats: hit_stats.update(loaded_stats["hit_stats"])
        print("📥 Счетчики успешно загружены из bot_stats.json", flush=True)
    except Exception as e: 
        print(f"⚠️ Ошибка загрузки счетчиков: {e}", flush=True)

def save_bot_stats():
    if not GITHUB_TOKEN: return
    api_url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_STATS_FILE}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}
    try:
        r = requests.get(api_url, headers=headers)
        current_sha = r.json()['sha'] if r.status_code == 200 else None
        payload_data = {"streaks": streaks, "hit_stats": hit_stats}
        encoded_content = base64.b64encode(json.dumps(payload_data, indent=2).encode('utf-8')).decode('utf-8')
        payload = {"message": "Auto-update bot stats", "content": encoded_content}
        if current_sha: payload["sha"] = current_sha
        requests.put(api_url, headers=headers, json=payload).raise_for_status()
    except Exception as e: 
        print(f"❌ Ошибка сохранения счетчиков: {e}", flush=True)

# --- ФУНКЦИИ ---
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
    except Exception: pass

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
    cold_numbers = [n for n, _ in sorted_cold[:4]]
    
    return hot_numbers, cold_numbers

def format_streak_counter(dist):
    if not dist: return "▪️ Нет данных"
    
    # ИСПРАВЛЕНИЕ ДУБЛИКАТОВ: используем set() для уникальных целых чисел
    unique_keys = set(int(k) for k in dist.keys() if dist[k] > 0)
    sorted_keys = sorted(list(unique_keys))
    
    lines = []
    for k_int in sorted_keys:
        k_str = str(k_int)
        
        if k_int == 1: word = "тираж"
        elif k_int in [2, 3, 4]: word = "тиража"
        else: word = "тиражей"
        
        count = dist[k_str]
        if count % 10 == 1 and count % 100 != 11: count_word = "раз"
        elif count % 10 in [2, 3, 4] and count % 100 not in [12, 13, 14]: count_word = "раза"
        else: count_word = "раз"
            
        lines.append(f"▪️ {k_int} {word} подряд: {count} {count_word}")
    return "\n".join(lines)

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

def extract_draws_from_text(text):
    pattern = r'\b(\d{5,6})\b[^\d]*?\b([1-9]|1[0-9]|2[0-6])\b[^\d]*?\b([1-9]|1[0-9]|2[0-6])\b[^\d]*?\b([1-9]|1[0-9]|2[0-6])\b[^\d]*?\b([1-9]|1[0-9]|2[0-6])\b'
    matches = re.findall(pattern, text)
    draws = {}
    for m in matches:
        d_num = int(m[0])
        nums = [int(m[i]) for i in range(1, 5)]
        if d_num not in draws:
            draws[d_num] = nums
    return draws

def get_recent_draws():
    # УБИВАЕМ ЗАВИСШИЕ ПРОЦЕССЫ CHROME ПЕРЕД ЗАПУСКОМ (Лечит зависания)
    os.system("pkill -f chrome")
    time.sleep(2)

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
    all_draws = {}
    
    try:
        driver = webdriver.Chrome(options=chrome_options)
        driver.set_page_load_timeout(25) # Снизили таймаут, чтобы быстрее отваливался при зависании

        try:
            print("🌐 Запускаю браузер (LotoNews)...", flush=True)
            driver.get(f"{URL_LOTONEWS}?_={int(time.time())}")
            time.sleep(4)
            body_text = driver.find_element(By.TAG_NAME, "body").text
            lotonews_draws = extract_draws_from_text(body_text)
            all_draws.update(lotonews_draws)
            print(f"📥 LotoNews: найдено {len(lotonews_draws)} тиражей.", flush=True)
        except Exception as e:
            print(f"❌ Ошибка LotoNews: {e}", flush=True)

        try:
            print("🌐 Запускаю браузер (Stoloto)...", flush=True)
            driver.get(f"{URL_STOLOTO}?_={int(time.time())}")
            time.sleep(4)
            body_text = driver.find_element(By.TAG_NAME, "body").text
            stoloto_draws = extract_draws_from_text(body_text)
            for k, v in stoloto_draws.items():
                if k not in all_draws: all_draws[k] = v
            print(f"📥 Stoloto: найдено {len(stoloto_draws)} тиражей.", flush=True)
        except Exception as e:
            print(f"❌ Ошибка Stoloto: {e}", flush=True)

        if not all_draws: return []

        sorted_draws = [{"draw": k, "numbers": v} for k, v in sorted(all_draws.items())]
        return sorted_draws
        
    except Exception as e:
        print(f"❌ Ошибка Selenium: {e}", flush=True)
        return []
    finally:
        if driver: 
            try: 
                driver.quit()
                os.system("pkill -f chrome") # Добиваем остатки
            except: pass

def main_loop():
    global last_processed_draw, last_success_time, previous_prediction, hit_stats
    
    load_bot_stats()
    
    draws = load_draws()
    if draws:
        last_processed_draw = get_max_draw_from_csv()
        hot, cold = get_hot_cold(draws)
        previous_prediction = hot + cold
        print(f"📌 Старт. Последний тираж: {last_processed_draw}. Счетчики загружены.", flush=True)

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
                        save_bot_stats()
                        
                        current_history = load_draws()
                        new_hot, new_cold = get_hot_cold(current_history)
                        previous_prediction = new_hot + new_cold

                        mirror_str = ""
                        if len(set(nums)) < 4:
                            dups = [str(x) for x in set(nums) if nums.count(x) > 1]
                            mirror_str = f" (Повторы: {', '.join(dups)})"

                        msg = f"🎲 **Тираж №{draw_num}** | Числа: **{', '.join(map(str, nums))}**\n\n"
                        
                        msg += f"🪞 **Зеркала (Совпадения)**\n"
                        msg += f"▪️ Текущий пропуск: **{streaks['mirror']['current']}** (Макс: {streaks['mirror']['max']}){mirror_str}\n"
                        msg += f"{format_streak_counter(streaks['mirror']['dist'])}\n\n"
                        
                        msg += f"🟢 **Ровно 2 четных**\n"
                        msg += f"▪️ Текущий пропуск: **{streaks['two_even']['current']}** (Макс: {streaks['two_even']['max']})\n"
                        msg += f"{format_streak_counter(streaks['two_even']['dist'])}\n\n"
                        
                        msg += f"🟡 **Тотал < 53.5**\n"
                        msg += f"▪️ Текущий пропуск: **{streaks['total_less']['current']}** (Макс: {streaks['total_less']['max']})\n"
                        msg += f"{format_streak_counter(streaks['total_less']['dist'])}\n\n"
                        
                        msg += f"🔮 **Модуляция на следующий тираж**\n"
                        msg += f"🔥 Ожидается выпадение горячих:\n"
                        msg += f"**{', '.join(map(str, new_hot))}** (Да: {hit_stats['hot']['yes']} / Нет: {hit_stats['hot']['no']})\n"
                        msg += f"🧊 Ожидается выпадение ультра-холодных:\n"
                        msg += f"**{', '.join(map(str, new_cold))}** (Да: {hit_stats['cold']['yes']} / Нет: {hit_stats['cold']['no']})"
                        
                        send_to_lolka(WEBHOOK_BETS, msg)

                        if streaks["mirror"]["current"] == 3:
                            firework_msg = (
                                f"🚨🚨🚨 **ВНИМАНИЕ! 3 ТИРАЖА ПОДРЯД С СОВПАДЕНИЯМИ!** 🚨🚨🚨\n"
                                f"📅 Тираж: №{draw_num} | Числа: {', '.join(map(str, nums))}\n"
                            )
                            for _ in range(5):
                                send_to_lolka(WEBHOOK_BETS, firework_msg)
                                time.sleep(1)
                                
                        time.sleep(2) 
                else:
                    print(f"Ожидание. Последний в архиве: {last_processed_draw}.", flush=True)
            else:
                print("Не удалось получить данные. Ждем 5 минут.", flush=True)
                
        except Exception as e:
            print(f"❌ Критическая ошибка: {e}", flush=True)

        time.sleep(300)

if __name__ == '__main__':
    threading.Thread(target=run_dummy_server, daemon=True).start()
    main_loop()