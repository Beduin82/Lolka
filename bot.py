import os
import sys
import time
import threading
import re
import requests
import base64
import json
import statistics
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from bs4 import BeautifulSoup

if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

# --- НАСТРОЙКИ ---
WEBHOOK_BETS = os.environ.get('LOLKA_WEBHOOK_BETS', 'https://lolka.app/api/webhooks/959166700160000/OTU5MTY2NzAwMTYwMDAw.F9cmMMafG_5i_MU1xVPx6LcIjZDNUZK2VqS14vYvGw4')
GITHUB_TOKEN = os.environ.get('GITHUB_TOKEN')
GITHUB_REPO = os.environ.get('GITHUB_REPO', 'Beduin82/Lolka')
GITHUB_DATA_FILE = "data.csv"
GITHUB_STATS_FILE = "bot_stats.json"
URL_LOTODATA = "https://lotodata.ru/duel/"
CSV_FILE = "data.csv"

last_processed_draw = None
last_success_time = time.time()

# --- ТОЛЬКО ЛУЧШИЕ СОБЫТИЯ (ВИНРЕЙТ > 60%) ---
EVENTS = {
    "Сумма < 57.5": lambda n: sum(n) < 57.5,
    "Сумма < 61.5": lambda n: sum(n) < 61.5,
    "Разн < 18.5": lambda n: (max(n) - min(n)) < 18.5,
    "Кратен 2": lambda n: any(x % 2 == 0 for x in n),
    "Кратен 3": lambda x: any(n % 3 == 0 for n in x),
}

state = {
    "duplicate_streak": 0,
    "prev_preds": {},
    "prev_hot": [],
    "prev_cold": []
}

# --- РАБОТА С GITHUB ---
def load_bot_stats():
    global state
    if not GITHUB_TOKEN: return
    try:
        r = requests.get(f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_STATS_FILE}",
                         headers={"Authorization": f"token {GITHUB_TOKEN}"})
        if r.status_code == 200:
            state.update(json.loads(base64.b64decode(r.json()['content']).decode('utf-8')))
            print("📥 Стейт загружен.", flush=True)
    except Exception: pass

def save_bot_stats():
    if not GITHUB_TOKEN: return
    try:
        r = requests.get(f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_STATS_FILE}",
                         headers={"Authorization": f"token {GITHUB_TOKEN}"})
        sha = r.json().get('sha') if r.status_code == 200 else None
        payload = {"message": "stats", "content": base64.b64encode(json.dumps(state).encode()).decode()}
        if sha: payload["sha"] = sha
        requests.put(f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_STATS_FILE}",
                     headers={"Authorization": f"token {GITHUB_TOKEN}"}, json=payload)
    except Exception: pass

# --- МАТЕМАТИКА (Z-SCORE) ---
def get_hot_cold(draws):
    freq = {n: 0 for n in range(1, 27)}
    last_seen = {n: -1 for n in range(1, 27)}
    for i, (_, nums) in enumerate(draws):
        for n in nums: freq[n] += 1; last_seen[n] = i
    hot = [n for n, _ in sorted(freq.items(), key=lambda x: x[1], reverse=True)[:4]]
    cold = [n for n, _ in sorted(last_seen.items(), key=lambda x: x[1])[:4]]
    return hot, cold

def calculate_z_scores(draws):
    results = {}
    for name, func in EVENTS.items():
        gaps = []
        current_gap = 0
        
        for _, nums in draws:
            if func(nums):
                if current_gap > 0 or len(gaps) == 0:
                    gaps.append(current_gap)
                current_gap = 0
            else:
                current_gap += 1
                
        active_gap = current_gap

        if len(gaps) > 1:
            mean_gap = statistics.mean(gaps)
            stdev_gap = statistics.stdev(gaps)
            if stdev_gap == 0: stdev_gap = 1.0
            z_score = (active_gap - mean_gap) / stdev_gap
        else:
            mean_gap, z_score = active_gap, 0
            
        pred_yes = z_score > 1.5 
        
        results[name] = {
            "pred": pred_yes,
            "z_score": round(z_score, 2),
            "gap": active_gap,
            "mean": round(mean_gap, 1)
        }
        
    return results

# --- ЗАГРУЗКА CSV ---
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

def update_github_csv(draw_num, numbers):
    if not GITHUB_TOKEN: return False
    try:
        r = requests.get(f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_DATA_FILE}",
                         headers={"Authorization": f"token {GITHUB_TOKEN}"})
        if r.status_code == 404: return False
        file_data = r.json(); sha = file_data['sha']
        content = base64.b64decode(file_data['content']).decode('utf-8')
        if f"\n{draw_num}," in content or content.startswith(f"{draw_num},"): return True
        if content.endswith('\n'): content = content[:-1]
        new_line = f"\n{draw_num},{numbers[0]},{numbers[1]},{numbers[2]},{numbers[3]}"
        enc = base64.b64encode((content + new_line).encode('utf-8')).decode('utf-8')
        res = requests.put(f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_DATA_FILE}",
                           headers={"Authorization": f"token {GITHUB_TOKEN}"},
                           json={"message": f"add {draw_num}", "content": enc, "sha": sha})
        res.raise_for_status()
        with open(CSV_FILE, 'a', encoding='utf-8') as f: f.write(new_line)
        return True
    except Exception: return False

def send_to_lolka(text):
    if not WEBHOOK_BETS: return
    for i in range(0, len(text), 1800):
        try:
            requests.post(WEBHOOK_BETS, json={"content": text[i:i+1800], "username": "Lotto Analytics"}, timeout=5)
        except Exception: pass

class DummyHandler(BaseHTTPRequestHandler):
    def do_GET(self): self.send_response(200); self.end_headers(); self.wfile.write(b"Bot is running")
    def do_HEAD(self): self.send_response(200); self.end_headers()

def run_dummy_server():
    HTTPServer(('0.0.0.0', int(os.environ.get("PORT", 10000))), DummyHandler).serve_forever()

# --- ПАРСИНГ LOTODATA.RU ---
def get_recent_draws():
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    try:
        res = requests.get(URL_LOTODATA, headers=headers, timeout=15)
        res.raise_for_status()
        soup = BeautifulSoup(res.text, 'html.parser')
        rows = soup.find_all('tr')
        parsed = []
        
        for row in rows:
            cells = [c.get_text(strip=True) for c in row.find_all('td')]
            if len(cells) >= 5 and re.fullmatch(r'\d{5,6}', cells[0]):
                draw_num = int(cells[0])
                nums = [int(c) for c in cells[1:5] if re.fullmatch(r'[1-9]|1[0-9]|2[0-6]', c)]
                if len(nums) == 4:
                    msk_time = datetime.utcnow() + timedelta(hours=3)
                    parsed.append({
                        "draw": draw_num, 
                        "numbers": nums, 
                        "datetime": msk_time.strftime("%d.%m.%Y %H:%M")
                    })
        return sorted(parsed, key=lambda x: x["draw"])
    except Exception as e:
        print(f"❌ Ошибка LotoData: {e}", flush=True)
        return []

# --- ОСНОВНОЙ ЦИКЛ ---
def main_loop():
    global last_processed_draw, last_success_time
    
    load_bot_stats()
    
    draws = load_draws()
    if draws:
        last_processed_draw = max(d[0] for d in draws)
        if not state.get("prev_preds"):
            state["prev_preds"] = calculate_z_scores(draws)
            state["prev_hot"], state["prev_cold"] = get_hot_cold(draws)
            save_bot_stats()
        print(f"📌 Старт. Последний тираж: {last_processed_draw}.", flush=True)

    while True:
        try:
            print("🔄 Проверяю LotoData...", flush=True)
            recent_draws = get_recent_draws()
            
            if recent_draws:
                last_success_time = time.time()
                missing = [d for d in recent_draws if d["draw"] > (last_processed_draw or 0)]
                
                if missing:
                    print(f"🚀 Найдено {len(missing)} новых тиражей!", flush=True)
                    for d in missing:
                        draw_num = d["draw"]
                        nums = d["numbers"]
                        dt_str = d["datetime"]
                        
                        if not update_github_csv(draw_num, nums):
                            continue
                        
                        last_processed_draw = draw_num

                        if len(set(nums)) < 4:
                            state["duplicate_streak"] += 1
                        else:
                            state["duplicate_streak"] = 0

                        # СВЕРКА ПРОГНОЗОВ (ТОЛЬКО ПО 5 СОБЫТИЯМ)
                        check_msg = ""
                        if state.get("prev_preds"):
                            check_msg = "📊 **Сверка прогнозов:**\n"
                            for name, data in state["prev_preds"].items():
                                if name not in EVENTS: continue
                                
                                if isinstance(data, bool):
                                    predicted_yes = data
                                    z_val = "?"
                                    dogon_val = "?"
                                else:
                                    predicted_yes = data.get("pred", False)
                                    z_val = data.get("z_score", 0)
                                    dogon_val = max(1, round(data.get("mean", 1)))
                                    
                                actual = EVENTS[name](nums)
                                
                                if predicted_yes:
                                    if actual:
                                        check_msg += f"-# ✅ {name} (Z={z_val}, д. {dogon_val})\n"
                                    else:
                                        check_msg += f"-# ❌ {name} (Z={z_val}, д. {dogon_val})\n"
                            check_msg += "\n"

                        current_history = load_draws()
                        new_preds = calculate_z_scores(current_history)
                        state["prev_preds"] = new_preds
                        new_hot, new_cold = get_hot_cold(current_history)
                        state["prev_hot"] = new_hot
                        state["prev_cold"] = new_cold
                        save_bot_stats()

                        msg = f"📅 {dt_str}\n"
                        msg += f"🎲 **№{draw_num}** | `{', '.join(map(str, nums))}`"
                        if state["duplicate_streak"] > 0:
                            msg += f" (Дубли: {state['duplicate_streak']})"
                        msg += "\n\n"
                        
                        if check_msg: msg += check_msg
                        
                        msg += "🔮 **Аномалии (Z-score > 1.5):**\n"
                        active = []
                        for name, data in new_preds.items():
                            if data['pred']:
                                dogon = max(1, round(data['mean']))
                                active.append(f"{name} (Z={data['z_score']}, д. {dogon})")
                        
                        if active:
                            for i in range(0, len(active), 2):
                                msg += " | ".join(active[i:i+2]) + "\n"
                        else:
                            msg += "Нет сильных аномалий.\n"
                        
                        msg += f"\n🔥 Горячие: **{', '.join(map(str, new_hot))}**\n"
                        msg += f"🧊 Холодные: **{', '.join(map(str, new_cold))}**"
                        
                        send_to_lolka(msg)
                        print(f"✅ Тираж №{draw_num} отправлен.", flush=True)

                        if state["duplicate_streak"] >= 3:
                            firework = f"🚨🚨🚨 **{state['duplicate_streak']} ТИРАЖА ПОДРЯД С ДУБЛЯМИ!** 🚨🚨🚨\n📅 №{draw_num}\n"
                            for _ in range(5):
                                send_to_lolka(firework)
                                time.sleep(1)
                        time.sleep(2)
                else:
                    print(f"✅ Новых нет (последний {last_processed_draw}).", flush=True)
            else:
                print("⚠️ Не удалось получить данные.", flush=True)
                
        except Exception as e:
            print(f"❌ Ошибка: {e}", flush=True)

        print("💤 Пауза 5 минут...", flush=True)
        time.sleep(300)

if __name__ == '__main__':
    threading.Thread(target=run_dummy_server, daemon=True).start()
    main_loop()