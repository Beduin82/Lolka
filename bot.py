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
URL_STOLOTO = "https://www.stoloto.ru/dvazhdydva"
CSV_FILE = "data.csv"

last_processed_draw = None
last_success_time = time.time()

# Структура счетчиков
streaks = {
    "mirror": {"current_loss": 0, "max_loss": 0, "current_win": 0, "max_win": 0},
    "two_even": {"current_loss": 0, "max_loss": 0, "current_win": 0, "max_win": 0},
    "total_less": {"current_loss": 0, "max_loss": 0, "current_win": 0, "max_win": 0}
}

hit_stats = {"hot": {"yes": 0, "no": 0}, "cold": {"yes": 0, "no": 0}}
previous_prediction = [] 

# --- УМНЫЙ ВЫБОР ХОЛОДНЫХ ЧИСЕЛ ---
def find_best_strategy():
    draws = load_draws()
    if len(draws) < 100: return "Стандартная (7-12)", 7, 12
    test_draws = draws[-500:]
    strategies = [("Ультра-холод (15+)", 15, 100), ("Глубокий холод (10-15)", 10, 15), ("Золотая середина (7-12)", 7, 12), ("Умеренный холод (4-8)", 4, 8), ("Просто 4 самых холодных", 0, 100)]
    best_name = "Золотая середина (7-12)"; best_range = (7, 12); max_profit = -999999
    
    for strat_name, min_gap, max_gap in strategies:
        virtual_bank = 0; current_gap = {n: 0 for n in range(1, 27)}
        for _, nums in draws[:-500]:
            for n in range(1, 27):
                if n in nums: current_gap[n] = 0
                else: current_gap[n] += 1
        for _, actual_nums in test_draws:
            if strat_name == "Просто 4 самых холодных": prediction = [n for n, _ in sorted(current_gap.items(), key=lambda x: x[1], reverse=True)[:4]]
            else:
                filtered = [n for n, gap in current_gap.items() if min_gap <= gap <= max_gap]
                prediction = filtered[:4] if len(filtered) >= 4 else [n for n, _ in sorted(current_gap.items(), key=lambda x: x[1], reverse=True)[:4]]
            hits = [n for n in prediction if n in actual_nums]
            virtual_bank += ((4 - len(hits)) * 100 * 1.143) - (4 * 100)
            for n in range(1, 27):
                if n in actual_nums: current_gap[n] = 0
                else: current_gap[n] += 1
        if virtual_bank > max_profit: 
            max_profit = virtual_bank; best_name = strat_name; best_range = (min_gap, max_gap)
    return best_name, best_range[0], best_range[1]

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
    
    strat_name, min_gap, max_gap = find_best_strategy()
    if strat_name == "Просто 4 самых холодных":
        cold_numbers = [n for n, _ in sorted(current_gap.items(), key=lambda x: x[1], reverse=True)[:4]]
    else:
        filtered = [n for n, gap in current_gap.items() if min_gap <= gap <= max_gap]
        cold_numbers = filtered[:4] if len(filtered) >= 4 else [n for n, _ in sorted(current_gap.items(), key=lambda x: x[1], reverse=True)[:4]]
    
    return hot_numbers, cold_numbers

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
        
        if "streaks" in loaded_stats:
            for event in ["mirror", "two_even", "total_less"]:
                if event in loaded_stats["streaks"]:
                    old = loaded_stats["streaks"][event]
                    if "current_loss" in old: 
                        streaks[event].update(old)
                    elif "current" in old: 
                        streaks[event]["current_loss"] = old.get("current", 0)
                        streaks[event]["max_loss"] = old.get("max", 0)
                        
        if "hit_stats" in loaded_stats: hit_stats.update(loaded_stats["hit_stats"])
        print("📥 Счетчики загружены.", flush=True)
    except Exception as e: 
        print(f"⚠️ Ошибка загрузки: {e}", flush=True)

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
        print(f"❌ Ошибка сохранения: {e}", flush=True)

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
        streaks["mirror"]["current_loss"] = 0
        streaks["mirror"]["current_win"] += 1
        streaks["mirror"]["max_win"] = max(streaks["mirror"]["max_win"], streaks["mirror"]["current_win"])
    else:
        streaks["mirror"]["current_win"] = 0
        streaks["mirror"]["current_loss"] += 1
        streaks["mirror"]["max_loss"] = max(streaks["mirror"]["max_loss"], streaks["mirror"]["current_loss"])

    has_two_even = (sum(1 for x in nums if x % 2 == 0) == 2)
    if has_two_even:
        streaks["two_even"]["current_loss"] = 0
        streaks["two_even"]["current_win"] += 1
        streaks["two_even"]["max_win"] = max(streaks["two_even"]["max_win"], streaks["two_even"]["current_win"])
    else:
        streaks["two_even"]["current_win"] = 0
        streaks["two_even"]["current_loss"] += 1
        streaks["two_even"]["max_loss"] = max(streaks["two_even"]["max_loss"], streaks["two_even"]["current_loss"])

    has_total_less = sum(nums) < 53.5
    if has_total_less:
        streaks["total_less"]["current_loss"] = 0
        streaks["total_less"]["current_win"] += 1
        streaks["total_less"]["max_win"] = max(streaks["total_less"]["max_win"], streaks["total_less"]["current_win"])
    else:
        streaks["total_less"]["current_win"] = 0
        streaks["total_less"]["current_loss"] += 1
        streaks["total_less"]["max_loss"] = max(streaks["total_less"]["max_loss"], streaks["total_less"]["current_loss"])

def send_to_lolka(webhook_url, text):
    if not webhook_url: return
    if len(text) > 2000:
        text = text[:1950] + "\n...(обрезано)"
    try:
        requests.post(webhook_url, json={"content": text, "username": "Lotto Analytics"}, timeout=10).raise_for_status()
    except Exception as e: 
        print(f"Ошибка отправки: {e}", flush=True)

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

def watchdog():
    global last_success_time
    print("🐕 Сторож запущен.", flush=True)
    while True:
        time.sleep(60)
        if time.time() - last_success_time > 1800: 
            print("🚨 СТОРОЖ: Бот завис более чем на 30 минут! Принудительная перезагрузка...", flush=True)
            os.execl(sys.executable, sys.executable, *sys.argv)

def extract_draws_from_text(text):
    pattern = r'(\d{2}\.\d{2}\.\d{4}\s+\d{1,2}:\d{2})\s+(\d{5,6})\s+([1-9]|1[0-9]|2[0-6])\s+([1-9]|1[0-9]|2[0-6])\s+([1-9]|1[0-9]|2[0-6])\s+([1-9]|1[0-9]|2[0-6])'
    matches = re.findall(pattern, text)
    draws = {}
    for m in matches:
        dt = m[0]
        d_num = int(m[1])
        nums = [int(m[i]) for i in range(2, 6)]
        if d_num not in draws:
            draws[d_num] = {"numbers": nums, "datetime": dt}
            
    if not draws:
        pattern2 = r'\b(\d{5,6})\b[^\d]*?\b([1-9]|1[0-9]|2[0-6])\b[^\d]*?\b([1-9]|1[0-9]|2[0-6])\b[^\d]*?\b([1-9]|1[0-9]|2[0-6])\b[^\d]*?\b([1-9]|1[0-9]|2[0-6])\b'
        matches2 = re.findall(pattern2, text)
        for m in matches2:
            d_num = int(m[0])
            nums = [int(m[i]) for i in range(1, 5)]
            if d_num not in draws:
                draws[d_num] = {"numbers": nums, "datetime": datetime.now().strftime("%d.%m.%Y %H:%M")}
    return draws

def get_recent_draws_thread(result_dict):
    """Функция для запуска Selenium в отдельном потоке с таймаутом"""
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
        chrome_options.add_argument("--disable-extensions")
        chrome_options.add_argument("--blink-settings=imagesEnabled=false")
        chrome_options.add_argument("--single-process")
        chrome_options.add_argument("--no-zygote")
        chrome_options.binary_location = "/usr/bin/chromium"

        print("🌐 Запускаю браузер...", flush=True)
        driver = webdriver.Chrome(options=chrome_options)
        driver.set_page_load_timeout(15)
        
        # 1. ПРИОРИТЕТ: LotoNews
        try:
            print("📥 Ищу на LotoNews...", flush=True)
            driver.get(f"{URL_LOTONEWS}?_={int(time.time())}")
            # Ждем появления таблицы, чтобы не парсить пустую страницу
            WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.TAG_NAME, "table")))
            time.sleep(1)
            
            lotonews_draws = extract_draws_from_text(driver.find_element(By.TAG_NAME, "body").text)
            if lotonews_draws:
                print(f"✅ LotoNews: найдено {len(lotonews_draws)} тиражей.", flush=True)
                result_dict["draws"].update(lotonews_draws)
                return # Если здесь сработало, Stoloto не трогаем!
        except Exception as e: 
            print(f"❌ Ошибка LotoNews: {e}", flush=True)

        # 2. ЗАПАСНОЙ ВАРИАНТ: Stoloto (только если LotoNews не дал данных)
        try:
            print("📥 LotoNews не дал данных. Ищу на Stoloto...", flush=True)
            driver.get(f"{URL_STOLOTO}?_={int(time.time())}")
            WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.TAG_NAME, "table")))
            time.sleep(1)
            
            stoloto_draws = extract_draws_from_text(driver.find_element(By.TAG_NAME, "body").text)
            if stoloto_draws:
                print(f"✅ Stoloto: найдено {len(stoloto_draws)} тиражей.", flush=True)
                for k, v in stoloto_draws.items():
                    if k not in result_dict["draws"]: result_dict["draws"][k] = v
        except Exception as e: 
            print(f"❌ Ошибка Stoloto: {e}", flush=True)
        
    except Exception as e:
        print(f"❌ Ошибка в потоке Selenium: {e}", flush=True)
        result_dict["error"] = str(e)
    finally:
        if driver: 
            try: driver.quit()
            except: pass
        subprocess.run("pkill -9 -f chromium", shell=True)
        subprocess.run("pkill -9 -f chrome", shell=True)
        gc.collect()

def get_recent_draws():
    result_dict = {"draws": {}, "error": None}
    t = threading.Thread(target=get_recent_draws_thread, args=(result_dict,))
    t.daemon = True
    t.start()
    # Ждем максимум 40 секунд
    t.join(timeout=40) 
    
    if t.is_alive():
        print("🚨 ТАЙМАУТ SELENIUM! Браузер завис, убиваем...", flush=True)
        subprocess.run("pkill -9 -f chromium", shell=True)
        subprocess.run("pkill -9 -f chrome", shell=True)
        # Мы не возвращаем [] сразу, а проверяем, есть ли данные, которые успели собраться

    all_draws = result_dict.get("draws", {})
    if not all_draws: 
        return []
    
    sorted_draws = [{"draw": k, "numbers": v["numbers"], "datetime": v["datetime"]} for k, v in sorted(all_draws.items())]
    return sorted_draws

def main_loop():
    global last_processed_draw, last_success_time, previous_prediction, hit_stats
    
    load_bot_stats()
    
    draws = load_draws()
    total_draws_db = len(draws)
    
    if draws:
        last_processed_draw = get_max_draw_from_csv()
        hot, cold = get_hot_cold(draws)
        previous_prediction = hot + cold
        print(f"📌 Старт. Последний тираж: {last_processed_draw}.", flush=True)

    while True:
        try:
            print("🔄 Начинаю новый цикл проверки...", flush=True)
            recent_draws = get_recent_draws()
            
            if recent_draws:
                last_success_time = time.time()
                missing_draws = [d for d in recent_draws if d["draw"] > (last_processed_draw or 0)]
                
                if missing_draws:
                    print(f"🚀 Найдено {len(missing_draws)} новых тиражей!", flush=True)
                    for draw_data in missing_draws:
                        try:
                            draw_num = draw_data["draw"]
                            nums = draw_data["numbers"]
                            dt_str = draw_data["datetime"]
                            
                            last_processed_draw = draw_num
                            update_github_csv(draw_num, nums)

                            check_msg = ""
                            if previous_prediction:
                                prev_hot = previous_prediction[:4]
                                prev_cold = previous_prediction[4:8]
                                
                                hot_check = ", ".join([f"✅ {n}" if n in nums else f"x {n}" for n in prev_hot])
                                cold_check = ", ".join([f"✅ {n}" if n in nums else f"x {n}" for n in prev_cold])
                                
                                hot_hits = [n for n in prev_hot if n in nums]
                                cold_hits = [n for n in prev_cold if n in nums]
                                
                                hit_stats["hot"]["yes"] += len(hot_hits)
                                hit_stats["hot"]["no"] += (4 - len(hot_hits))
                                hit_stats["cold"]["yes"] += len(cold_hits)
                                hit_stats["cold"]["no"] += (4 - len(cold_hits))
                                
                                total_draws_hot = (hit_stats['hot']['yes'] + hit_stats['hot']['no']) // 4
                                total_draws_cold = (hit_stats['cold']['yes'] + hit_stats['cold']['no']) // 4

                                check_msg = (
                                    f"📊 **Проверка прогноза:**\n"
                                    f"🔥 {hot_check}\n"
                                    f"-#                (✅ {hit_stats['hot']['yes']} / x {hit_stats['hot']['no']} / Σ {total_draws_hot})\n"
                                    f"🧊 {cold_check}\n"
                                    f"-#                (✅ {hit_stats['cold']['yes']} / x {hit_stats['cold']['no']} / Σ {total_draws_cold})\n\n"
                                )

                            update_streaks(nums)
                            save_bot_stats()
                            
                            current_history = load_draws()
                            new_hot, new_cold = get_hot_cold(current_history)
                            previous_prediction = new_hot + new_cold
                            
                            total_draws_db = len(current_history)

                            mirror_str = ""
                            if len(set(nums)) < 4:
                                current_series = streaks["mirror"]["current_win"]
                                mirror_str = f" (Серия дубля: {current_series})"

                            msg = f"📅 {dt_str}\n\n"
                            msg += f"🎲 **№{draw_num}** | `{', '.join(map(str, nums))}`{mirror_str}\n\n"
                            
                            if check_msg:
                                msg += check_msg
                            
                            msg += f"🔮 **Прогноз**\n"
                            msg += f"🔥 Горячие (за {total_draws_db} тир): **{', '.join(map(str, new_hot))}**\n"
                            msg += f"🧊 Холодные (за {total_draws_db} тир): **{', '.join(map(str, new_cold))}**\n\n"
                            
                            msg += f"📊 **Серии тиражей:**\n"
                            msg += f"-# 🪞 Совпадающие: x {streaks['mirror']['current_loss']} | ✅ {streaks['mirror']['current_win']} | Max x {streaks['mirror']['max_loss']} | Max ✅ {streaks['mirror']['max_win']}\n"
                            msg += f"-# 🟢 Ровно 2 чет: x {streaks['two_even']['current_loss']} | ✅ {streaks['two_even']['current_win']} | Max x {streaks['two_even']['max_loss']} | Max ✅ {streaks['two_even']['max_win']}\n"
                            msg += f"-# 🟡 Тотал < 53.5: x {streaks['total_less']['current_loss']} | ✅ {streaks['total_less']['current_win']} | Max x {streaks['total_less']['max_loss']} | Max ✅ {streaks['total_less']['max_win']}"
                            
                            send_to_lolka(WEBHOOK_BETS, msg)
                            print(f"✅ Тираж №{draw_num} успешно обработан.", flush=True)

                            if streaks["mirror"]["current_win"] >= 3:
                                firework_msg = (
                                    f"🚨🚨🚨 **ВНИМАНИЕ! {streaks['mirror']['current_win']} ТИРАЖА ПОДРЯД С СОВПАДЕНИЯМИ!** 🚨🚨🚨\n"
                                    f"📅 Тираж: №{draw_num} | Числа: {', '.join(map(str, nums))}\n"
                                )
                                for _ in range(5):
                                    send_to_lolka(WEBHOOK_BETS, firework_msg)
                                    time.sleep(1)
                                    
                            time.sleep(2) 
                        except Exception as inner_e:
                            print(f"❌ Ошибка при обработке тиража: {inner_e}", flush=True)
                else:
                    print(f"✅ Проверка завершена. Новых тиражей нет (последний в базе {last_processed_draw}).", flush=True)
            else:
                print("⚠️ Не удалось получить данные. Ждем 5 минут.", flush=True)
                
        except Exception as e:
            print(f"❌ Критическая ошибка в цикле: {e}", flush=True)

        print("💤 Пауза 5 минут...", flush=True)
        time.sleep(300)

if __name__ == '__main__':
    threading.Thread(target=run_dummy_server, daemon=True).start()
    threading.Thread(target=watchdog, daemon=True).start()
    main_loop()