import os
import sys
import time
import threading
import re
import requests
import base64
import json
import statistics
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from bs4 import BeautifulSoup

# ============================================================
# v.12.2 - Исправлена опечатка в лямбда-функции (Кратен 4)
# ============================================================

if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

# --- НАСТРОЙКИ ---
WEBHOOK_BETS = os.environ.get('LOLKA_WEBHOOK_BETS', 'https://lolka.app/api/webhooks/959166700160000/OTU5MTY2NzAwMTYwMDAw.F9cmMMafG_5i_MU1xVPx6LcIjZDNUZK2VqS14vYvGw4')
GITHUB_TOKEN = os.environ.get('GITHUB_TOKEN')
GITHUB_REPO = os.environ.get('GITHUB_REPO', 'Beduin82/Lolka')
GITHUB_DATA_FILE = "data.csv"
GITHUB_STATS_FILE = "bot_stats.json"

# --- TELEGRAM ---
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHANNEL_ID = os.environ.get('TELEGRAM_CHANNEL_ID')

URL_LOTODATA = "https://lotodata.ru/duel/"
CSV_FILE = "data.csv"

last_processed_draw = None
last_firework_draw = None
START_BANK = 10000
BET_PER_EVENT = 100  # Ставка на каждое событие (100 руб)

# События (аномалии) - ИСПРАВЛЕНО
EVENTS = {
    "Сумма > 45.5": lambda n: sum(n) > 45.5,
    "Сумма < 57.5": lambda n: sum(n) < 57.5,
    "Сумма < 61.5": lambda n: sum(n) < 61.5,
    "Разн < 18.5": lambda n: (max(n) - min(n)) < 18.5,
    "Кратен 2": lambda n: any(x % 2 == 0 for x in n),
    "Кратен 3": lambda n: any(x % 3 == 0 for x in n),
    "Кратен 4": lambda n: any(x % 4 == 0 for x in n), # Теперь тут 'n' и 'x' на своих местах
}

# Коэффициенты для каждого события (можно менять)
EVENT_KOEFS = {
    "Сумма > 45.5": 1.363,
    "Сумма < 57.5": 1.500,
    "Сумма < 61.5": 1.600,
    "Разн < 18.5": 1.596,
    "Кратен 2": 1.200,
    "Кратен 3": 1.400,
    "Кратен 4": 1.800,
}

state = {
    "duplicate_streak": 0,
    "prev_preds": {},
    "prev_hot": [],
    "prev_cold": [],
    # Статистика банка
    "bank_spent": 0.0, "bank_won": 0.0, 
    "success_predictions": 0, "fail_predictions": 0,
}

# --- GITHUB ---
def load_bot_stats():
    global state
    if not GITHUB_TOKEN: return
    try:
        r = requests.get(
            f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_STATS_FILE}",
            headers={"Authorization": f"token {GITHUB_TOKEN}"},
            timeout=10,
        )
        if r.status_code == 200:
            data = json.loads(base64.b64decode(r.json()['content']).decode('utf-8'))
            if isinstance(data, dict):
                state.update(data)
                print("📥 Стейт загружен.", flush=True)
    except Exception as e:
        print(f"⚠️ load_bot_stats: {e}", flush=True)

def save_bot_stats():
    if not GITHUB_TOKEN: return
    try:
        r = requests.get(
            f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_STATS_FILE}",
            headers={"Authorization": f"token {GITHUB_TOKEN}"},
            timeout=10,
        )
        sha = r.json().get('sha') if r.status_code == 200 else None
        payload = {
            "message": "stats",
            "content": base64.b64encode(json.dumps(state).encode()).decode(),
        }
        if sha: payload["sha"] = sha
        res = requests.put(
            f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_STATS_FILE}",
            headers={"Authorization": f"token {GITHUB_TOKEN}"},
            json=payload,
            timeout=10,
        )
        res.raise_for_status()
    except Exception as e:
        print(f"⚠️ save_bot_stats: {e}", flush=True)

# --- МАТЕМАТИКА ---
def get_hot_cold(draws):
    freq = {n: 0 for n in range(1, 27)}
    last_seen = {n: -1 for n in range(1, 27)}
    for i, (_, nums) in enumerate(draws):
        for n in nums:
            freq[n] += 1
            last_seen[n] = i
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
                gaps.append(current_gap)
                current_gap = 0
            else:
                current_gap += 1
        active_gap = current_gap
        samples = len(gaps)

        if samples > 1:
            mean_gap = statistics.mean(gaps)
            stdev_gap = statistics.stdev(gaps)
            if stdev_gap == 0:
                z_score = float(active_gap)
            else:
                z_score = (active_gap - mean_gap) / stdev_gap
        else:
            mean_gap = statistics.mean(gaps) if gaps else float(active_gap)
            z_score = 0.0

        results[name] = {
            "pred": z_score > 1.5, # Бот ставит, если Z > 1.5
            "z_score": round(z_score, 2),
            "gap": active_gap,
            "mean": round(mean_gap, 1),
            "samples": samples,
        }
    return results

def check_sequential_columns(prev_nums, curr_nums):
    result = []
    if not prev_nums or not curr_nums: return result
    if len(prev_nums) != len(curr_nums): return result
    for i, (a, b) in enumerate(zip(prev_nums, curr_nums), start=1):
        if b == a + 1:
            result.append((i, a, b))
    return result

# --- CSV ---
def load_draws(filename=CSV_FILE):
    draws = []
    try:
        with open(filename, 'r', encoding='utf-8-sig') as f:
            for line in f:
                match = re.search(
                    r'\b(\d{5,6})\b[^\d]*\b([1-9]|1[0-9]|2[0-6])\b[^\d]*'
                    r'\b([1-9]|1[0-9]|2[0-6])\b[^\d]*\b([1-9]|1[0-9]|2[0-6])\b'
                    r'[^\d]*\b([1-9]|1[0-9]|2[0-6])\b',
                    line,
                )
                if match:
                    draws.append((
                        int(match.group(1)),
                        [int(match.group(i)) for i in range(2, 6)],
                    ))
    except Exception as e:
        print(f"⚠️ load_draws: {e}", flush=True)
    return sorted(draws, key=lambda d: d[0])

def update_github_csv(draw_num, numbers):
    new_line = f"{draw_num},{numbers[0]},{numbers[1]},{numbers[2]},{numbers[3]}"
    if not GITHUB_TOKEN:
        try:
            if os.path.exists(CSV_FILE):
                with open(CSV_FILE, 'r', encoding='utf-8-sig') as f:
                    if re.search(rf'^{draw_num},', f.read(), re.MULTILINE): return False 
            with open(CSV_FILE, 'a', encoding='utf-8') as f:
                f.write("\n" + new_line)
            return True
        except: return False

    try:
        r = requests.get(
            f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_DATA_FILE}",
            headers={"Authorization": f"token {GITHUB_TOKEN}"},
            timeout=10,
        )
        if r.status_code == 404: return False
        file_data = r.json()
        sha = file_data['sha']
        content = base64.b64decode(file_data['content']).decode('utf-8')
        
        if re.search(rf'^{draw_num},', content, re.MULTILINE): return False
            
        if content.endswith('\n'): content = content[:-1]
        enc = base64.b64encode((content + "\n" + new_line).encode('utf-8')).decode('utf-8')
        res = requests.put(
            f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_DATA_FILE}",
            headers={"Authorization": f"token {GITHUB_TOKEN}"},
            json={"message": f"add {draw_num}", "content": enc, "sha": sha},
            timeout=10,
        )
        res.raise_for_status()
        with open(CSV_FILE, 'a', encoding='utf-8') as f:
            f.write("\n" + new_line)
        return True
    except Exception as e:
        return False

def send_to_lolka(text):
    if not WEBHOOK_BETS: return
    for i in range(0, len(text), 1800):
        try:
            requests.post(
                WEBHOOK_BETS,
                json={"content": text[i:i + 1800], "username": "Lotto Analytics"},
                timeout=5,
            )
        except Exception as e:
            print(f"⚠️ send_to_lolka: {e}", flush=True)

def send_to_telegram(text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHANNEL_ID: return
    def _to_html(chunk):
        esc = chunk.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
        esc = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', esc, flags=re.DOTALL)
        esc = re.sub(r'`(.+?)`', r'<code>\1</code>', esc, flags=re.DOTALL)
        return esc
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    for i in range(0, len(text), 4000):
        chunk = text[i:i + 4000]
        try:
            requests.post(
                url,
                json={
                    "chat_id": TELEGRAM_CHANNEL_ID,
                    "text": _to_html(chunk),
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                },
                timeout=(5, 8),
            )
        except Exception as e:
            print(f"⚠️ send_to_telegram: {e}", flush=True)

# --- HTTP ---
class DummyHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is running")
    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()

def run_dummy_server():
    ThreadingHTTPServer(('0.0.0.0', int(os.environ.get("PORT", 10000))), DummyHandler).serve_forever()

# --- ПАРСИНГ ---
def get_recent_draws():
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    try:
        res = requests.get(URL_LOTODATA, headers=headers, timeout=15)
        res.raise_for_status()
        soup = BeautifulSoup(res.text, 'html.parser')
        rows = soup.find_all('tr')
        parsed = []
        
        for row in rows:
            text = row.get_text(separator=' ', strip=True)
            draw_match = re.search(r'\b(\d{5,6})\b', text)
            if not draw_match: continue
                
            draw_num = int(draw_match.group(1))
            text_after_draw = text[draw_match.end():]
            nums = [int(n) for n in re.findall(r'\b([1-9]|1[0-9]|2[0-6])\b', text_after_draw)]
            valid_nums = nums[:4]
            
            if len(valid_nums) == 4:
                date_match = re.search(r'(\d{2}\.\d{2}\.\d{2,4})\s*(\d{2}:\d{2})?', text)
                if date_match:
                    dt_str = date_match.group(1)
                    if date_match.group(2): dt_str += f" {date_match.group(2)}"
                else:
                    msk_time = datetime.now(timezone.utc) + timedelta(hours=3)
                    dt_str = msk_time.strftime("%d.%m.%Y %H:%M")
                    
                parsed.append({"draw": draw_num, "numbers": valid_nums, "datetime": dt_str})
                
        unique_parsed = {p['draw']: p for p in parsed}.values()
        return sorted(unique_parsed, key=lambda x: x["draw"])
    except Exception as e:
        print(f"❌ Ошибка LotoData: {e}", flush=True)
        return []

# --- ОСНОВНОЙ ЦИКЛ ---
def main_loop():
    global last_processed_draw, last_firework_draw

    load_bot_stats()

    draws = load_draws()
    if draws:
        last_processed_draw = max(d[0] for d in draws)
        state["prev_preds"] = calculate_z_scores(draws)
        state["prev_hot"], state["prev_cold"] = get_hot_cold(draws)
        save_bot_stats()
        print(f"📌 Старт. Последний тираж: {last_processed_draw}.", flush=True)

    while True:
        try:
            print("🔄 Проверяю LotoData...", flush=True)
            recent_draws = get_recent_draws()

            if recent_draws:
                missing = [d for d in recent_draws if d["draw"] > (last_processed_draw or 0)]
                if len(missing) > 20: missing = missing[-20:]

                if missing:
                    print(f"🚀 Найдено {len(missing)} новых тиражей!", flush=True)
                    for d in missing:
                        draw_num = d["draw"]
                        nums = d["numbers"]
                        dt_str = d["datetime"]

                        if not update_github_csv(draw_num, nums):
                            last_processed_draw = max(last_processed_draw or 0, draw_num)
                            continue

                        if len(set(nums)) < 4:
                            state["duplicate_streak"] += 1
                        else:
                            state["duplicate_streak"] = 0

                        # === БЛОК СТАВОК НА АНОМАЛИИ ===
                        total_bet = 0
                        total_payout = 0
                        results_lines = []
                        win_count = 0
                        lose_count = 0

                        # Проверяем прогнозы (аномалии), которые были сделаны в прошлом тираже
                        prev_anomalies = state.get("prev_preds", {})
                        active_bets = [name for name, data in prev_anomalies.items() if data.get("pred")]

                        if active_bets:
                            for name in active_bets:
                                kf = EVENT_KOEFS.get(name, 1.5)
                                total_bet += BET_PER_EVENT
                                
                                # Проверяем, сработало ли событие
                                is_hit = EVENTS[name](nums)
                                if is_hit:
                                    payout = BET_PER_EVENT * kf
                                    total_payout += payout
                                    win_count += 1
                                    results_lines.append(f"💵 {name}: {BET_PER_EVENT} ₽ @ {kf} — ✅ +{payout - BET_PER_EVENT:.2f} ₽")
                                else:
                                    lose_count += 1
                                    results_lines.append(f"💵 {name}: {BET_PER_EVENT} ₽ @ {kf} — ❌ -{BET_PER_EVENT:.2f} ₽")
                            
                            # Обновляем банк и статистику
                            state["bank_spent"] = state.get("bank_spent", 0) + total_bet
                            state["bank_won"] = state.get("bank_won", 0) + total_payout
                            state["success_predictions"] = state.get("success_predictions", 0) + win_count
                            state["fail_predictions"] = state.get("fail_predictions", 0) + lose_count
                            save_bot_stats()
                        else:
                            results_lines.append("-# В прошлом тираже аномалий не было (ставок нет).")

                        current_history = load_draws()
                        
                        # Пересчитываем аномалии на СЛЕДУЮЩИЙ тираж
                        new_preds = calculate_z_scores(current_history)
                        state["prev_preds"] = new_preds
                        new_hot, new_cold = get_hot_cold(current_history)
                        state["prev_hot"], state["prev_cold"] = new_hot, new_cold

                        # Формируем список аномалий для следующего тиража
                        next_anomalies = [name for name, data in new_preds.items() if data['pred']]

                        # === ФОРМИРОВАНИЕ СООБЩЕНИЯ ===
                        profit_total = state.get("bank_won", 0) - state.get("bank_spent", 0)
                        margin = (profit_total / state.get("bank_spent", 1) * 100) if state.get("bank_spent", 0) > 0 else 0
                        current_bank = START_BANK + profit_total

                        msg = f"🎲 **Lotto Analytics** | #{draw_num}\n"
                        msg += f"🏆 **Дуэль (4 из 26)**\n"
                        msg += f"⚔️ **Выпали:** {', '.join(map(str, nums))} | Σ = {sum(nums)}\n"
                        msg += f"📅 {dt_str}\n\n"
                        
                        # Блок прогноза с коэффициентами
                        msg += f"🔮 **Прогноз на след. тираж (Аномалии):**\n"
                        if next_anomalies:
                            for a in next_anomalies:
                                z = new_preds[a]['z_score']
                                kf = EVENT_KOEFS.get(a, 1.5)
                                msg += f"-# {a} (Z={z}, кэф: {kf})\n"
                        else:
                            msg += "-# Нет сильных аномалий (ставок не будет).\n"
                        msg += "\n"

                        msg += f"📋 **РЕЗУЛЬТАТ СТАВОК**\n"
                        if active_bets:
                            msg += "\n".join(results_lines) + "\n"
                            round_profit = total_payout - total_bet
                            icon = "🏆" if round_profit >= 0 else "💥"
                            status = "ЗАШЛО" if round_profit >= 0 else "ПРОВАЛ"
                            msg += f"\n{icon} **Итог тиража:** {status} {round_profit:+.2f} ₽\n"
                        else:
                            msg += "\n".join(results_lines) + "\n"
                        
                        msg += "\n───────────────\n"
                        msg += f"📊 Всего: ✅ {state.get('success_predictions', 0)} | ❌ {state.get('fail_predictions', 0)} | доход {profit_total:+.2f} ₽ (ROI {margin:+.1f}%)\n"
                        msg += f"🏦 Банк: {current_bank:.2f} ₽ (старт {START_BANK} ₽, {profit_total:+.2f} ₽)"

                        send_to_lolka(msg)
                        send_to_telegram(msg) 
                        print(f"✅ Тираж №{draw_num} отправлен.", flush=True)

                        last_processed_draw = draw_num
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