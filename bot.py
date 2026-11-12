
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
last_success_time = time.time()

bot_stats = {
    "bank_spent": 0.0, "bank_won": 0.0, "success_predictions": 0, "fail_predictions": 0,
    "max_win_streak": 0, "current_win_streak": 0, "max_loss_streak": 0, "current_loss_streak": 0
}

previous_prediction = []
BET_PER_NUMBER = 100 
CURRENT_STRATEGY_NAME = "Не определена"
best_event_data = {}

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

def load_bot_stats():
    global bot_stats
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
        bot_stats.update(loaded_stats)
    except Exception: pass

def save_bot_stats():
    if not GITHUB_TOKEN: return
    api_url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_STATS_FILE}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}
    try:
        r = requests.get(api_url, headers=headers)
        current_sha = r.json()['sha'] if r.status_code == 200 else None
        encoded_content = base64.b64encode(json.dumps(bot_stats, indent=2).encode('utf-8')).decode('utf-8')
        payload = {"message": "Auto-update stats", "content": encoded_content}
        if current_sha: payload["sha"] = current_sha
        requests.put(api_url, headers=headers, json=payload).raise_for_status()
    except Exception as e: print(f"❌ Ошибка сохранения статистики: {e}", flush=True)

def find_best_bet_event_adaptive():
    draws = load_draws()
    if len(draws) < 100: return {"name": "Нет данных", "roi": 0, "win_rate": 0, "max_loss_streak": 0, "current_loss_streak": 0, "depth": 0, "shift": 0}
    events = {
        "Разность > 14.5": (lambda n: (max(n) - min(n)) > 14.5, 1.596),
        "Тотал < 53.5": (lambda n: sum(n) < 53.5, 1.988),
        "Ровно 2 четных": (lambda n: sum(1 for x in n if x % 2 == 0) == 2, 2.516),
        "Тотал > 45.5": (lambda n: sum(n) > 45.5, 1.363),
        "Сумма четных > 18.5": (lambda n: sum(x for x in n if x % 2 == 0) > 18.5, 1.426)
    }
    depths = [100, 250, 500, 1000, 2500, len(draws)]
    best_result = {"roi": -9999}
    for depth in depths:
        if depth > len(draws): continue
        test_draws = draws[-depth:]
        for name, (check_func, koef) in events.items():
            virtual_bank = 0; wins = 0; loss_streak = 0; max_loss_streak = 0; current_loss_streak = 0
            for _, nums in test_draws:
                if check_func(nums): virtual_bank += (koef - 1) * 100; wins += 1; loss_streak = 0
                else: virtual_bank -= 100; loss_streak += 1; max_loss_streak = max(max_loss_streak, loss_streak); current_loss_streak = loss_streak
            roi = (virtual_bank / (len(test_draws) * 100)) * 100
            if roi > best_result["roi"]:
                recent_50 = test_draws[-50:]; prev_50 = test_draws[-100:-50] if len(test_draws) >= 100 else recent_50
                recent_hits = sum(1 for _, n in recent_50 if check_func(n)); prev_hits = sum(1 for _, n in prev_50 if check_func(n))
                shift = ((recent_hits/len(recent_50) if recent_50 else 0) - (prev_hits/len(prev_50) if prev_50 else 0)) * 100
                best_result = {"name": name, "koef": koef, "roi": roi, "win_rate": (wins / len(test_draws)) * 100, "max_loss_streak": max_loss_streak, "current_loss_streak": current_loss_streak, "depth": depth, "shift": shift}
    return best_result

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
            virtual_bank += ((4 - len(hits)) * 100 * 1.14) - (4 * 100)
            for n in range(1, 27):
                if n in actual_nums: current_gap[n] = 0
                else: current_gap[n] += 1
        if virtual_bank > max_profit: max_profit = virtual_bank; best_name = strat_name; best_range = (min_gap, max_gap)
    return best_name, best_range[0], best_range[1]

def get_4_cold_numbers():
    global CURRENT_STRATEGY_NAME
    draws = load_draws()
    if not draws: return [1, 2, 3, 4]
    current_gap = {n: 0 for n in range(1, 27)}
    for _, nums in draws:
        for n in range(1, 27):
            if n in nums: current_gap[n] = 0
            else: current_gap[n] += 1
    CURRENT_STRATEGY_NAME, min_gap, max_gap = find_best_strategy()
    if CURRENT_STRATEGY_NAME == "Просто 4 самых холодных": return [n for n, _ in sorted(current_gap.items(), key=lambda x: x[1], reverse=True)[:4]]
    filtered = [n for n, gap in current_gap.items() if min_gap <= gap <= max_gap]
    return filtered[:4] if len(filtered) >= 4 else [n for n, _ in sorted(current_gap.items(), key=lambda x: x[1], reverse=True)[:4]]

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

# --- УМНЫЙ ПАРСИНГ ЧЕРЕЗ ТЕКСТ СТРАНИЦЫ ---
def get_last_draw():
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
        print("🌐 Запускаю браузер и захожу на сайт...", flush=True)
        driver = webdriver.Chrome(options=chrome_options)
        driver.set_page_load_timeout(30)
        driver.get(ARCHIVE_URL)
        
        # Ждем 5 секунд, чтобы весь JS отработал
        time.sleep(5) 
        
        # Берем ВЕСЬ видимый текст страницы (без HTML-тегов)
        body_text = driver.find_element(By.TAG_NAME, "body").text
        
        # Ищем слово "Тираж" и номер тиража после него
        draw_match = re.search(r'Тираж\s*№?\s*(\d{5,6})', body_text)
        if draw_match:
            draw_num = int(draw_match.group(1))
            
            # Берем текст, который идет ПОСЛЕ номера тиража
            text_after = body_text[draw_match.end():]
            
            # Ищем первые 4 числа от 1 до 26
            nums_pattern = r'\b([1-9]|1[0-9]|2[0-6])\b\D*?\b([1-9]|1[0-9]|2[0-6])\b\D*?\b([1-9]|1[0-9]|2[0-6])\b\D*?\b([1-9]|1[0-9]|2[0-6])\b'
            nums_match = re.search(nums_pattern, text_after)
            
            if nums_match:
                numbers = [int(nums_match.group(i)) for i in range(1, 5)]
                print(f"✅ Найден тираж №{draw_num} и числа {numbers}", flush=True)
                return {"draw": draw_num, "date": time.strftime("%d.%m.%Y"), "numbers": numbers}
            else:
                print("⚠️ Номер тиража найден, но 4 числа не найдены.", flush=True)
        else:
            print("⚠️ Ключевое слово 'Тираж' не найдено в тексте.", flush=True)

        # Если не получилось, выводим отладку
        print("⚠️ Не удалось распарсить. Первые 1000 символов текста:", flush=True)
        print(body_text[:1000], flush=True)
        return None
        
    except Exception as e:
        print(f"❌ Ошибка Selenium: {e}", flush=True)
        return None
    finally:
        if driver: 
            try: driver.quit()
            except: pass

def send_to_lolka(webhook_url, text):
    if not webhook_url: return
    if len(text) > 1900: text = text[:1900] + "\n..."
    try: requests.post(webhook_url, json={"content": text, "username": "Lotto Analytics"}, timeout=10).raise_for_status()
    except Exception: pass

def main_loop():
    global last_processed_draw, duplicate_streak, previous_prediction, best_event_data, last_success_time
    
    load_bot_stats()
    last_processed_draw = get_max_draw_from_csv()
    best_event_data = find_best_bet_event_adaptive()
    previous_prediction = get_4_cold_numbers()

    while True:
        try:
            if time.time() - last_success_time > 3600:
                print("🚨 СТОРОЖ: Бот завис более чем на час! Перезагрузка...", flush=True)
                os.execl(sys.executable, sys.executable, *sys.argv)

            draw_data = get_last_draw()
            
            if draw_data:
                last_success_time = time.time()
                draw_num = draw_data["draw"]
                nums = draw_data["numbers"]

                if draw_num > last_processed_draw:
                    print(f"\n🔍 Новый тираж: №{draw_num}, числа: {nums}", flush=True)
                    last_processed_draw = draw_num
                    update_github_csv(draw_num, nums)

                    pred_msg = ""
                    if previous_prediction:
                        hits = [n for n in previous_prediction if n in nums]
                        bot_stats["bank_spent"] += 4 * BET_PER_NUMBER
                        bot_stats["bank_won"] += (4 - len(hits)) * BET_PER_NUMBER * 1.14
                        
                        if len(hits) == 0:
                            bot_stats["success_predictions"] += 1
                            bot_stats["current_win_streak"] += 1
                            bot_stats["current_loss_streak"] = 0
                            bot_stats["max_win_streak"] = max(bot_stats["max_win_streak"], bot_stats["current_win_streak"])
                            pred_msg = "✅ **Прогноз сбылся!**\n"
                        else:
                            bot_stats["fail_predictions"] += 1
                            bot_stats["current_loss_streak"] += 1
                            bot_stats["current_win_streak"] = 0
                            bot_stats["max_loss_streak"] = max(bot_stats["max_loss_streak"], bot_stats["current_loss_streak"])
                            pred_msg = f"❌ **Прогноз провален.** Выпали: {', '.join(map(str, hits))}.\n"
                        save_bot_stats()

                    new_prediction = get_4_cold_numbers()
                    previous_prediction = new_prediction
                    best_event_data = find_best_bet_event_adaptive()

                    total_preds = bot_stats["success_predictions"] + bot_stats["fail_predictions"]
                    win_rate = (bot_stats["success_predictions"] / total_preds * 100) if total_preds > 0 else 0
                    profit = bot_stats["bank_won"] - bot_stats["bank_spent"]
                    
                    self_analysis_msg = (
                        f"🤖 **Самоанализ:** WR {win_rate:.1f}% | Банк: {bot_stats['bank_won']:.0f} ₽ (Профит: {profit:+.0f} ₽)\n"
                        f"🔥 Серия: {bot_stats['current_win_streak']} побед / {bot_stats['current_loss_streak']} провалов\n"
                    )

                    bets_alerts = []
                    for key, trig in BETS_TRIGGERS.items():
                        if trig["check"](nums): trig["streak"] = 0
                        else:
                            trig["streak"] += 1
                            if trig["streak"] >= trig["threshold"]: bets_alerts.append(f"{trig['name']} (нет {trig['streak']} т.)")

                    has_duplicates = len(set(nums)) < 4
                    if has_duplicates:
                        duplicate_streak += 1
                        dup_str = ", ".join([str(x) for x in set(nums) if nums.count(x) > 1])
                        bets_alerts.append(f"🪞 Зеркало: **{dup_str}** (серия: {duplicate_streak})")
                    else: duplicate_streak = 0

                    roi_val = best_event_data.get('roi', 0); shift_val = best_event_data.get('shift', 0)
                    rec_strategy = "Флэт 2%" if roi_val > 0 else "Пропуск"
                    if roi_val > 0 and shift_val > 5: rec_strategy = "Флэт 2-3% (Тренд 📈)"
                    elif roi_val > 0 and shift_val < -5: rec_strategy = "Флэт 1% (Тренд 📉)"

                    ai_msg = (
                        f"🧠 **ИИ-Анализ:** Фаворит: `{best_event_data.get('name', '—')}` (ROI: {roi_val:+.1f}%)\n"
                        f"▪️ Сдвиг: `{shift_val:+.1f}%` | Глубина: `{best_event_data.get('depth', 0)}` т.\n"
                        f"▪️ **Совет:** {rec_strategy} | Стратегия чисел: `{CURRENT_STRATEGY_NAME}`\n"
                    )

                    msg = f"🎲 **Тираж №{draw_num}** | Числа: **{', '.join(map(str, nums))}**\n\n"
                    msg += pred_msg + "\n"
                    if bets_alerts:
                        msg += "⚠️ **Сигналы:**\n" + "\n".join(f"💰 {a}" for a in bets_alerts) + "\n\n"
                    msg += self_analysis_msg + "\n" + ai_msg
                    msg += f"\n🔮 **Прогноз:** `{', '.join(map(str, new_prediction))}` НЕ выпадут."
                    
                    send_to_lolka(WEBHOOK_BETS, msg)

                    if duplicate_streak >= 3:
                        for _ in range(5):
                            send_to_lolka(WEBHOOK_BETS, f"🚨🚨🚨 **ВНИМАНИЕ! {duplicate_streak} ТИРАЖА С СОВПАДЕНИЯМИ!** 🚨🚨🚨\nТираж: №{draw_num} | Числа: {', '.join(map(str, nums))}")
                            time.sleep(1)
                else:
                    print(f"Тираж №{draw_num} уже есть в базе.", flush=True)
            else:
                print("Не удалось получить данные. Ждем 5 минут.", flush=True)
                
        except Exception as e:
            print(f"❌ Критическая ошибка: {e}", flush=True)

        time.sleep(300)

if __name__ == '__main__':
    threading.Thread(target=run_dummy_server, daemon=True).start()
    main_loop()