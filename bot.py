import os
import sys
import time
import threading
import requests
import base64
import json
import re
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer

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
    except Exception: pass

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
    except Exception as e: 
        print(f"❌ Ошибка архива: {e}", flush=True)
        return False

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

def send_to_lolka(text):
    if not WEBHOOK_BETS: return
    try:
        requests.post(WEBHOOK_BETS, json={"content": text, "username": "Lotto Analytics"}, timeout=10)
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

# --- ПАРСИНГ (ТОЛЬКО LOTONEWS ЧЕРЕЗ REQUESTS) ---
def get_recent_draws():
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.8,en-US;q=0.5,en;q=0.3",
    }

    try:
        res = requests.get(URL_LOTONEWS, headers=headers, timeout=15)
        res.raise_for_status()
        
        # Убираем HTML-теги, чтобы получить чистый текст (имитация работы Selenium)
        clean_text = re.sub(r'<[^>]+>', ' ', res.text)
        
        # Ищем: Дата Время Тираж Число1 Число2 Число3 Число4
        pattern = r'(\d{2}\.\d{2}\.\d{4}\s+\d{1,2}:\d{2})\s+(\d{5,6})\s+([1-9]|1[0-9]|2[0-6])\s+([1-9]|1[0-9]|2[0-6])\s+([1-9]|1[0-9]|2[0-6])\s+([1-9]|1[0-9]|2[0-6])'
        matches = re.findall(pattern, clean_text)
        
        if not matches:
            print("⚠️ LotoNews: не удалось найти тиражи в HTML.", flush=True)
            return []

        parsed = []
        for m in matches:
            parsed.append({
                "draw": int(m[1]),
                "numbers": [int(m[i]) for i in range(2, 6)],
                "datetime": m[0]
            })
            
        # Сортируем и убираем дубликаты по номеру тиража
        unique_draws = {}
        for d in parsed:
            if d["draw"] not in unique_draws:
                unique_draws[d["draw"]] = d
                
        sorted_draws = [unique_draws[k] for k in sorted(unique_draws.keys())]
        print(f"✅ LotoNews: получено {len(sorted_draws)} тиражей.", flush=True)
        return sorted_draws

    except Exception as e:
        print(f"❌ Ошибка парсинга LotoNews: {e}", flush=True)
        return []

def main_loop():
    global last_processed_draw, last_success_time, previous_prediction, hit_stats
    
    load_bot_stats()
    draws = load_draws()
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
                        draw_num = draw_data["draw"]
                        nums = draw_data["numbers"]
                        dt_str = draw_data["datetime"]
                        
                        if not update_github_csv(draw_num, nums):
                            print(f"⚠️ Не удалось записать в архив. Пропуск.", flush=True)
                            continue
                        
                        last_processed_draw = draw_num

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
                            mirror_str = f" (Серия дубля: {streaks['mirror']['current_win']})"

                        msg = f"📅 {dt_str}\n\n"
                        msg += f"🎲 **№{draw_num}** | `{', '.join(map(str, nums))}`{mirror_str}\n\n"
                        
                        if check_msg: msg += check_msg
                        
                        msg += f"🔮 **Прогноз**\n"
                        msg += f"🔥 Горячие (за {total_draws_db} тир): **{', '.join(map(str, new_hot))}**\n"
                        msg += f"🧊 Холодные (за {total_draws_db} тир): **{', '.join(map(str, new_cold))}**\n\n"
                        
                        msg += f"📊 **Серии тиражей:**\n"
                        msg += f"-# 🪞 Совпадающие: x {streaks['mirror']['current_loss']} | ✅ {streaks['mirror']['current_win']} | Max x {streaks['mirror']['max_loss']} | Max ✅ {streaks['mirror']['max_win']}\n"
                        msg += f"-# 🟢 Ровно 2 чет: x {streaks['two_even']['current_loss']} | ✅ {streaks['two_even']['current_win']} | Max x {streaks['two_even']['max_loss']} | Max ✅ {streaks['two_even']['max_win']}\n"
                        msg += f"-# 🟡 Тотал < 53.5: x {streaks['total_less']['current_loss']} | ✅ {streaks['total_less']['current_win']} | Max x {streaks['total_less']['max_loss']} | Max ✅ {streaks['total_less']['max_win']}"
                        
                        send_to_lolka(msg)
                        print(f"✅ Тираж №{draw_num} отправлен.", flush=True)

                        if streaks["mirror"]["current_win"] >= 3:
                            firework_msg = f"🚨🚨🚨 **ВНИМАНИЕ! {streaks['mirror']['current_win']} ТИРАЖА ПОДРЯД С СОВПАДЕНИЯМИ!** 🚨🚨🚨\n📅 Тираж: №{draw_num} | Числа: {', '.join(map(str, nums))}\n"
                            for _ in range(5):
                                send_to_lolka(firework_msg)
                                time.sleep(1)
                        time.sleep(2) 
                else:
                    print(f"✅ Новых тиражей нет (последний {last_processed_draw}).", flush=True)
            else:
                print("⚠️ Не удалось получить данные. Ждем.", flush=True)
                
        except Exception as e:
            print(f"❌ Ошибка: {e}", flush=True)

        print("💤 Пауза 5 минут...", flush=True)
        time.sleep(300)

if __name__ == '__main__':
    threading.Thread(target=run_dummy_server, daemon=True).start()
    main_loop()