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
from http.server import BaseHTTPRequestHandler, HTTPServer
from bs4 import BeautifulSoup

# ============================================================
# v.10.2 - Устранено дублирование сообщений в вебхук Lolka
# ============================================================

if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

# --- НАСТРОЙКИ ---
WEBHOOK_BETS = os.environ.get(
    'LOLKA_WEBHOOK_BETS',
    'https://lolka.app/api/webhooks/959166700160000/OTU5MTY2NzAwMTYwMDAw.F9cmMMafG_5i_MU1xVPx6LcIjZDNUZK2VqS14vYvGw4'
)
GITHUB_TOKEN = os.environ.get('GITHUB_TOKEN')
GITHUB_REPO = os.environ.get('GITHUB_REPO', 'Beduin82/Lolka')
GITHUB_DATA_FILE = "data.csv"
GITHUB_STATS_FILE = "bot_stats.json"

URL_LOTODATA = "https://lotodata.ru/duel/"
CSV_FILE = "data.csv"

last_processed_draw = None
last_firework_draw = None

EVENTS = {
    "Сумма > 45.5": lambda n: sum(n) > 45.5,
    "Сумма < 57.5": lambda n: sum(n) < 57.5,
    "Сумма < 61.5": lambda n: sum(n) < 61.5,
    "Разн < 18.5": lambda n: (max(n) - min(n)) < 18.5,
    "Кратен 2": lambda n: any(x % 2 == 0 for x in n),
    "Кратен 3": lambda x: any(n % 3 == 0 for n in x),
    "Кратен 4": lambda x: any(n % 4 == 0 for n in x),
}

state = {
    "duplicate_streak": 0,
    "prev_preds": {},
    "prev_hot": [],
    "prev_cold": []
}

# --- GITHUB ---
def load_bot_stats():
    global state
    if not GITHUB_TOKEN:
        return
    try:
        r = requests.get(
            f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_STATS_FILE}",
            headers={"Authorization": f"token {GITHUB_TOKEN}"},
            timeout=10,
        )
        if r.status_code == 200:
            content = base64.b64decode(r.json()['content']).decode('utf-8')
            data = json.loads(content)
            if isinstance(data, dict):
                state.update(data)
                print("📥 Стейт загружен.", flush=True)
    except Exception as e:
        print(f"⚠️ load_bot_stats: {e}", flush=True)

def save_bot_stats():
    if not GITHUB_TOKEN:
        return
    try:
        r = requests.get(
            f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_STATS_FILE}",
            headers={"Authorization": f"token {GITHUB_TOKEN}"},
            timeout=10,
        )
        sha = r.json().get('sha') if r.status_code == 200 else None
        payload = {
            "message": "stats",
            "content": base64.b64encode(json.dumps(state, ensure_ascii=False).encode('utf-8')).decode('utf-8'),
        }
        if sha:
            payload["sha"] = sha
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
            "pred": z_score > 1.5,
            "z_score": round(z_score, 2),
            "gap": active_gap,
            "mean": round(mean_gap, 1),
            "samples": samples,
        }
    return results

def check_sequential_columns(prev_nums, curr_nums):
    result = []
    if not prev_nums or not curr_nums or len(prev_nums) != len(curr_nums):
        return result
    for i, (a, b) in enumerate(zip(prev_nums, curr_nums), start=1):
        if b == a + 1:
            result.append((i, a, b))
    return result

# --- CSV ---
def load_draws(filename=CSV_FILE):
    draws = []
    if not os.path.exists(filename):
        return draws
    try:
        with open(filename, 'r', encoding='utf-8-sig') as f:
            for line in f:
                line_str = line.strip()
                if not line_str:
                    continue
                match = re.search(
                    r'^\s*(\d{5,6})\s*,\s*(2[0-6]|1[0-9]|[1-9])\s*,\s*(2[0-6]|1[0-9]|[1-9])\s*,\s*(2[0-6]|1[0-9]|[1-9])\s*,\s*(2[0-6]|1[0-9]|[1-9])\b',
                    line_str
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
                    if re.search(rf'^{draw_num},', f.read(), re.MULTILINE):
                        return False
            with open(CSV_FILE, 'a', encoding='utf-8') as f:
                f.write("\n" + new_line)
            return True
        except Exception as e:
            print(f"⚠️ Локальная запись CSV: {e}", flush=True)
            return False

    try:
        r = requests.get(
            f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_DATA_FILE}",
            headers={"Authorization": f"token {GITHUB_TOKEN}"},
            timeout=10,
        )
        if r.status_code == 404:
            content = ""
            sha = None
        else:
            file_data = r.json()
            sha = file_data.get('sha')
            content = base64.b64decode(file_data.get('content', '')).decode('utf-8')

        if re.search(rf'^{draw_num},', content, re.MULTILINE):
            return False

        content = content.rstrip('\r\n')
        new_content = (content + "\n" + new_line).lstrip('\n')
        enc = base64.b64encode(new_content.encode('utf-8')).decode('utf-8')

        payload = {"message": f"add {draw_num}", "content": enc}
        if sha:
            payload["sha"] = sha

        res = requests.put(
            f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_DATA_FILE}",
            headers={"Authorization": f"token {GITHUB_TOKEN}"},
            json=payload,
            timeout=10,
        )
        res.raise_for_status()

        with open(CSV_FILE, 'a', encoding='utf-8') as f:
            f.write("\n" + new_line)
        return True
    except Exception as e:
        print(f"⚠️ update_github_csv({draw_num}): {e}", flush=True)
        return False

def send_to_lolka(text):
    """Отправляет ровно ОДНО цельное сообщение. Если текст превышает лимит Discord/Lolka, обрезает его без создания второго сообщения."""
    if not WEBHOOK_BETS:
        print("⚠️ LOLKA_WEBHOOK_BETS не задан.", flush=True)
        return False
    
    # Ограничение Discord/Lolka на одно сообщение - 2000 символов (оставляем запас до 1900)
    clean_text = text[:1900] if len(text) > 1900 else text

    try:
        res = requests.post(
            WEBHOOK_BETS,
            json={"content": clean_text, "username": "Lotto Analytics"},
            timeout=10,
        )
        return res.status_code in (200, 204)
    except Exception as e:
        print(f"⚠️ send_to_lolka: {e}", flush=True)
        return False

# --- HTTP SERVER ДЛЯ РАЗВЕРТЫВАНИЯ ---
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
def get_recent_draws():
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    try:
        res = requests.get(URL_LOTODATA, headers=headers, timeout=15)
        res.raise_for_status()
        soup = BeautifulSoup(res.text, 'html.parser')
        rows = soup.find_all('tr')
        parsed = {}

        for row in rows:
            text = row.get_text(separator=' ', strip=True)

            draw_match = re.search(r'\b(\d{5,6})\b', text)
            if not draw_match:
                continue

            draw_num = int(draw_match.group(1))
            text_after_draw = text[draw_match.end():]
            nums = [int(n) for n in re.findall(r'\b(2[0-6]|1[0-9]|[1-9])\b', text_after_draw)]

            valid_nums = nums[:4]
            if len(valid_nums) == 4:
                date_match = re.search(r'(\d{2}\.\d{2}\.\d{2,4})\s*(\d{2}:\d{2})?', text)
                if date_match:
                    dt_str = date_match.group(1)
                    if date_match.group(2):
                        dt_str += f" {date_match.group(2)}"
                else:
                    msk_time = datetime.now(timezone.utc) + timedelta(hours=3)
                    dt_str = msk_time.strftime("%d.%m.%Y %H:%M")

                parsed[draw_num] = {
                    "draw": draw_num,
                    "numbers": valid_nums,
                    "datetime": dt_str
                }

        return sorted(parsed.values(), key=lambda x: x["draw"])
    except Exception as e:
        print(f"❌ Ошибка LotoData: {e}", flush=True)
        return []

# --- ОСНОВНОЙ ЦИКЛ ---
def main_loop():
    global last_processed_draw, last_firework_draw
    print("🚀 Бот запущен.", flush=True)

    load_bot_stats()

    # Считываем максимальный номер тиража из базы при запуске
    initial_draws = load_draws()
    if initial_draws:
        last_processed_draw = max(d[0] for d in initial_draws)
        print(f"📌 Последний тираж в локальной базе: №{last_processed_draw}", flush=True)

    while True:
        try:
            recent_draws = get_recent_draws()
            if recent_draws:
                # Если база была пуста, берем последний с сайта, чтобы не слать старую историю
                if last_processed_draw is None:
                    last_processed_draw = recent_draws[-1]["draw"]
                    print(f"📌 Инициализация: точка отсчета тираж №{last_processed_draw}", flush=True)

                # Отбираем только строго новые тиражи
                missing = [d for d in recent_draws if d["draw"] > last_processed_draw]
                
                # Защита от спама: если скопилось много тиражей, берем последние 5
                if len(missing) > 5:
                    missing = missing[-5:]

                if missing:
                    print(f"⚡ Найдено {len(missing)} новых тиражей!", flush=True)
                    for d in missing:
                        draw_num = d["draw"]
                        nums = d["numbers"]
                        dt_str = d["datetime"]

                        # Защита: фиксируем тираж сразу, чтобы не обрабатывать повторно при ошибках сети
                        last_processed_draw = max(last_processed_draw, draw_num)

                        # История до добавления текущего тиража
                        history = load_draws()

                        # Сверка последовательности чисел (+1) по столбцам с прошлым тиражом
                        seq_msg = ""
                        if history:
                            prev_draw_num, prev_nums = history[-1]
                            seq_cols = check_sequential_columns(prev_nums, nums)
                            if seq_cols:
                                details = ", ".join([f"ст.{pos} ({p}➔{c})" for pos, p, c in seq_cols])
                                seq_msg = f"📊 **Последовательность (+1):** {details}"

                        # Проверка дублей внутри тиража
                        if len(nums) != len(set(nums)):
                            state["duplicate_streak"] += 1
                        else:
                            state["duplicate_streak"] = 0

                        # Проверка сбываемости прошлых прогнозов
                        check_msg = ""
                        if state.get("prev_preds"):
                            hits = []
                            for name, pred_data in state["prev_preds"].items():
                                if pred_data.get('pred'):
                                    func = EVENTS.get(name)
                                    if func:
                                        icon = "✅" if func(nums) else "❌"
                                        hits.append(f"{icon} {name}")
                            if hits:
                                check_msg = "🎯 **Сверка прогноза:** " + ", ".join(hits) + "\n"

                        # Запись в CSV и обновление истории
                        update_github_csv(draw_num, nums)
                        current_history = load_draws()

                        # Пересчёт математики на актуальной истории
                        new_preds = calculate_z_scores(current_history)
                        state["prev_preds"] = new_preds
                        new_hot, new_cold = get_hot_cold(current_history)
                        state["prev_hot"] = new_hot
                        state["prev_cold"] = new_cold
                        save_bot_stats()

                        # Формирование ОДНОГО компактного сообщения
                        total = sum(nums)
                        msg_lines = [
                            f"📅 {dt_str}",
                            f"🎲 **№{draw_num}** | `{', '.join(map(str, nums))}` | Σ = **{total}**"
                            + (f" (Дубли: {state['duplicate_streak']})" if state["duplicate_streak"] > 0 else "")
                        ]

                        if check_msg:
                            msg_lines.append(check_msg.strip())

                        # Аномалии
                        active = []
                        for name, data in new_preds.items():
                            if data['pred']:
                                dogon = max(1, round(data['mean']))
                                active.append(f"-# {name} (Z={data['z_score']}) -> ждать ~{dogon} тир.")
                        
                        msg_lines.append("🔮 **Аномалии (Z > 1.5):**")
                        if active:
                            msg_lines.extend(active)
                        else:
                            msg_lines.append("-# Нет сильных аномалий.")

                        zline = " · ".join(
                            f"{name}({d['z_score']}{'🔴' if d['pred'] else ''})"
                            for name, d in new_preds.items()
                        )
                        msg_lines.append(f"📋 Z: {zline}")
                        msg_lines.append(f"🔥 Горячие: **{', '.join(map(str, new_hot))}** | 🧊 Холодные: **{', '.join(map(str, new_cold))}**")

                        if seq_msg:
                            msg_lines.append(seq_msg)

                        msg_lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

                        full_msg = "\n".join(msg_lines)

                        # Отправка ровно 1 раз
                        send_to_lolka(full_msg)
                        print(f"✅ Тираж №{draw_num} отправлен.", flush=True)

                        # Оповещение при серии дублей (отправляется 1 раз, а не 5)
                        if state["duplicate_streak"] >= 3 and last_firework_draw != draw_num:
                            last_firework_draw = draw_num
                            firework = (
                                f"🚨🚨🚨 **{state['duplicate_streak']} ТИРАЖА ПОДРЯД С ДУБЛЯМИ!** 🚨🚨🚨\n"
                                f"📅 Тираж №{draw_num}"
                            )
                            send_to_lolka(firework)

                        time.sleep(1)
                else:
                    print(f"✅ Новых нет (последний №{last_processed_draw}).", flush=True)
            else:
                print("⚠️ Не удалось получить данные с сайта.", flush=True)

        except Exception as e:
            print(f"❌ Ошибка в цикле: {e}", flush=True)

        print("💤 Пауза 5 минут...", flush=True)
        time.sleep(300)

if __name__ == '__main__':
    threading.Thread(target=run_dummy_server, daemon=True).start()
    main_loop()