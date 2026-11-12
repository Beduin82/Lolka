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

# --- ПЕРЕМЕННЫЕ СТАТИСТИКИ (ЗАГРУЖАЮТСЯ ИЗ JSON) ---
bot_stats = {
    "bank_spent": 0.0,
    "bank_won": 0.0,
    "success_predictions": 0,
    "fail_predictions": 0,
    "max_win_streak": 0,
    "current_win_streak": 0,
    "max_loss_streak": 0,
    "current_loss_streak": 0
}

previous_prediction = []
BET_PER_NUMBER = 100 
CURRENT_STRATEGY_NAME = "Не определена"
best_event_data = {}

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

def get_max_draw_from_csv():
    draws = load_draws()
    if not draws:
        return 0
    return max(d[0] for d in draws)

# --- СИСТЕМА САМОКОНТРОЛЯ (GITHUB JSON) ---
def load_bot_stats():
    global bot_stats
    if not GITHUB_TOKEN: return
    api_url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_STATS_FILE}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}
    try:
        r = requests.get(api_url, headers=headers)
        if r.status_code == 404:
            print("⚠️ Файл bot_stats.json не найден в GitHub. Будет создан новый.", flush=True)
            return
            
        file_data = r.json()
        
        # Защита от пустого файла
        if 'content' not in file_data or not file_data['content'].strip():
            print("⚠️ Файл bot_stats.json пуст. Используем начальные значения.", flush=True)
            return
            
        content = base64.b64decode(file_data['content']).decode('utf-8').strip()
        if not content:
             print("⚠️ Файл bot_stats.json пуст. Используем начальные значения.", flush=True)
             return

        # Пытаемся прочитать JSON
        loaded_stats = json.loads(content)
        bot_stats.update(loaded_stats)
        print(f"📥 Статистика загружена: Успехов {bot_stats['success_predictions']}, Банк {bot_stats['bank_won']:.0f}", flush=True)
    except json.JSONDecodeError:
        print("⚠️ Ошибка формата в bot_stats.json. Бот начнет статистику с нуля.", flush=True)
    except Exception as e:
        print(f"❌ Ошибка загрузки статистики: {e}", flush=True)

def save_bot_stats():
    if not GITHUB_TOKEN: return
    api_url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_STATS_FILE}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}
    try:
        r = requests.get(api_url, headers=headers)
        current_sha = r.json()['sha'] if r.status_code == 200 else None
        
        content_str = json.dumps(bot_stats, indent=2)
        encoded_content = base64.b64encode(content_str.encode('utf-8')).decode('utf-8')
        
        payload = {"message": "Auto-update bot stats", "content": encoded_content}
        if current_sha: payload["sha"] = current_sha
        
        requests.put(api_url, headers=headers, json=payload).raise_for_status()
        print("💾 Статистика бота сохранена в GitHub.", flush=True)
    except Exception as e:
        print(f"❌ Ошибка сохранения статистики: {e}", flush=True)

# --- АДАПТИВНЫЙ АНАЛИЗ СТАВОК ---
def find_best_bet_event_adaptive():
    draws = load_draws()
    if len(draws) < 100:
        return {"name": "Нет данных", "roi": 0, "win_rate": 0, "max_loss_streak": 0, "current_loss_streak": 0, "depth": 0, "shift": 0}

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
            virtual_bank = 0
            wins = 0
            loss_streak = 0
            max_loss_streak = 0
            current_loss_streak = 0
            
            for _, nums in test_draws:
                if check_func(nums):
                    virtual_bank += (koef - 1) * 100
                    wins += 1
                    loss_streak = 0
                else:
                    virtual_bank -= 100
                    loss_streak += 1
                    max_loss_streak = max(max_loss_streak, loss_streak)
                    current_loss_streak = loss_streak
                    
            roi = (virtual_bank / (len(test_draws) * 100)) * 100
            
            if roi > best_result["roi"]:
                recent_50 = test_draws[-50:]
                prev_50 = test_draws[-100:-50] if len(test_draws) >= 100 else recent_50
                
                recent_hits = sum(1 for _, n in recent_50 if check_func(n))
                prev_hits = sum(1 for _, n in prev_50 if check_func(n))
                
                recent_rate = recent_hits / len(recent_50) if recent_50 else 0
                prev_rate = prev_hits / len(prev_50) if prev_50 else 0
                shift = (recent_rate - prev_rate) * 100

                best_result = {
                    "name": name,
                    "koef": koef,
                    "roi": roi,
                    "win_rate": (wins / len(test_draws)) * 100,
                    "max_loss_streak": max_loss_streak,
                    "current_loss_streak": current_loss_streak,
                    "depth": depth,
                    "shift": shift
                }
    return best_result

# --- ОБУЧЕНИЕ СТРАТЕГИИ ЧИСЕЛ ---
def find_best_strategy():
    draws = load_draws()
    if len(draws) < 100:
        return "Стандартная (7-12)", 7, 12

    test_draws = draws[-500:] 
    strategies = [
        ("Ультра-холод (15+)", 15, 100),
        ("Глубокий холод (10-15)", 10, 15),
        ("Золотая середина (7-12)", 7, 12),
        ("Умеренный холод (4-8)", 4, 8),
        ("Просто 4 самых холодных", 0, 100)
    ]

    best_strategy_name = "Золотая середина (7-12)"
    best_strategy_range = (7, 12)
    max_profit = -999999

    for strat_name, min_gap, max_gap in strategies:
        virtual_bank = 0
        current_gap = {n: 0 for n in range(1, 27)}
        
        for _, nums in draws[:-500]:
            for n in range(1, 27):
                if n in nums: current_gap[n] = 0
                else: current_gap[n] += 1

        for _, actual_nums in test_draws:
            if strat_name == "Просто 4 самых холодных":
                sorted_gaps = sorted(current_gap.items(), key=lambda x: x[1], reverse=True)
                prediction = [n for n, _ in sorted_gaps[:4]]
            else:
                filtered = [n for n, gap in current_gap.items() if min_gap <= gap <= max_gap]
                if len(filtered) >= 4:
                    prediction = filtered[:4]
                else:
                    sorted_gaps = sorted(current_gap.items(), key=lambda x: x[1], reverse=True)
                    prediction = [n for n, _ in sorted_gaps[:4]]

            hits = [n for n in prediction if n in actual_nums]
            cost = 4 * 100
            win = (4 - len(hits)) * 100 * 1.14
            virtual_bank += (win - cost)

            for n in range(1, 27):
                if n in actual_nums: current_gap[n] = 0
                else: current_gap[n] += 1

        if virtual_bank > max_profit:
            max_profit = virtual_bank
            best_strategy_name = strat_name
            best_strategy_range = (min_gap, max_gap)

    return best_strategy_name, best_strategy_range[0], best_strategy_range[1]

# --- ГЕНЕРАЦИЯ ПРОГНОЗА ЧИСЕЛ ---
def get_4_cold_numbers():
    global CURRENT_STRATEGY_NAME
    draws = load_draws()
    if not draws:
        return [1, 2, 3, 4] 
        
    current_gap = {n: 0 for n in range(1, 27)}
    for _, nums in draws:
        for n in range(1, 27):
            if n in nums: current_gap[n] = 0
            else: current_gap[n] += 1
            
    strategy_name, min_gap, max_gap = find_best_strategy()
    CURRENT_STRATEGY_NAME = strategy_name
    
    if strategy_name == "Просто 4 самых холодных":
        sorted_gaps = sorted(current_gap.items(), key=lambda x: x[1], reverse=True)
        return [n for n, _ in sorted_gaps[:4]]
    
    filtered = [n for n, gap in current_gap.items() if min_gap <= gap <= max_gap]
    
    if len(filtered) >= 4:
        return filtered[:4]
        
    sorted_gaps = sorted(current_gap.items(), key=lambda x: x[1], reverse=True)
    return [n for n, _ in sorted_gaps[:4]]

# --- ОБНОВЛЕНИЕ CSV ---
def update_github_csv(draw_num, numbers):
    if not GITHUB_TOKEN: return
    api_url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_DATA_FILE}"
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
    except Exception as e: print(f"❌ Ошибка GitHub (CSV): {e}", flush=True)

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
    global previous_prediction, best_event_data, bot_stats
    
    print("🚀 Мониторинг запущен. Самоконтроль (JSON) активен.", flush=True)
    
    # 1. Загружаем историю самого бота
    load_bot_stats()
    
    last_processed_draw = get_max_draw_from_csv()
    print(f"📌 Последний тираж в базе: {last_processed_draw}.", flush=True)
    
    best_event_data = find_best_bet_event_adaptive()
    previous_prediction = get_4_cold_numbers()

    while True:
        try:
            print("Начинаю новую проверку...", flush=True)
            draw_data = get_last_draw()

            if draw_data:
                draw_num = draw_data["draw"]
                nums = draw_data["numbers"]

                if draw_num > last_processed_draw:
                    print(f"\n🔍 Новый тираж: №{draw_num}, числа: {nums}", flush=True)
                    last_processed_draw = draw_num

                    update_github_csv(draw_num, nums)

                    # 1. Проверка прогноза чисел и обновление банка
                    pred_msg = ""
                    if previous_prediction:
                        hits = [n for n in previous_prediction if n in nums]
                        bot_stats["bank_spent"] += 4 * BET_PER_NUMBER
                        win_amount = (4 - len(hits)) * BET_PER_NUMBER * 1.14
                        bot_stats["bank_won"] += win_amount
                        
                        if len(hits) == 0:
                            bot_stats["success_predictions"] += 1
                            bot_stats["current_win_streak"] += 1
                            bot_stats["current_loss_streak"] = 0
                            bot_stats["max_win_streak"] = max(bot_stats["max_win_streak"], bot_stats["current_win_streak"])
                            pred_msg = "✅ **Прогноз чисел сбылся!** Ни одно из 4 чисел не выпало.\n"
                        else:
                            bot_stats["fail_predictions"] += 1
                            bot_stats["current_loss_streak"] += 1
                            bot_stats["current_win_streak"] = 0
                            bot_stats["max_loss_streak"] = max(bot_stats["max_loss_streak"], bot_stats["current_loss_streak"])
                            pred_msg = f"❌ **Прогноз чисел провален.** Выпали: {', '.join(map(str, hits))}.\n"
                        
                        # Сохраняем обновленную статистику в GitHub
                        save_bot_stats()

                    # 2. Генерация нового прогноза
                    new_prediction = get_4_cold_numbers()
                    previous_prediction = new_prediction
                    best_event_data = find_best_bet_event_adaptive()

                    # 3. Статистика самого бота (Блок Самоанализа)
                    total_preds = bot_stats["success_predictions"] + bot_stats["fail_predictions"]
                    win_rate = (bot_stats["success_predictions"] / total_preds * 100) if total_preds > 0 else 0
                    profit = bot_stats["bank_won"] - bot_stats["bank_spent"]
                    margin = ((bot_stats["bank_won"] / bot_stats["bank_spent"] - 1) * 100) if bot_stats["bank_spent"] > 0 else 0
                    
                    self_analysis_msg = (
                        f"🤖 **Самоанализ бота (за всё время):**\n"
                        f"📈 Успехов: {bot_stats['success_predictions']} | 📉 Провалов: {bot_stats['fail_predictions']}\n"
                        f"🎯 Win Rate: {win_rate:.1f}%\n"
                        f"🔥 Серия побед: {bot_stats['current_win_streak']} (макс: {bot_stats['max_win_streak']})\n"
                        f"❄️ Серия провалов: {bot_stats['current_loss_streak']} (макс: {bot_stats['max_loss_streak']})\n"
                        f"💵 Банк: {bot_stats['bank_spent']:.0f} ₽ -> {bot_stats['bank_won']:.0f} ₽ (Маржа: {margin:+.1f}%)\n"
                        f"📈 Прибыль: {profit:+.0f} ₽\n"
                    )

                    # 4. Проверка триггеров и зеркал
                    bets_alerts = []
                    for key, trig in BETS_TRIGGERS.items():
                        if trig["check"](nums): trig["streak"] = 0
                        else:
                            trig["streak"] += 1
                            if trig["streak"] >= trig["threshold"]:
                                bets_alerts.append(f"{trig['name']} (нет {trig['streak']} тиража)")

                    has_duplicates = len(set(nums)) < 4
                    if has_duplicates:
                        duplicate_streak += 1
                        dup_str = ", ".join([str(x) for x in set(nums) if nums.count(x) > 1])
                        bets_alerts.append(f"🪞 Зеркало: **{dup_str}** (серия: {duplicate_streak})")
                    else:
                        duplicate_streak = 0

                    # 5. ИИ-Рекомендация
                    roi_val = best_event_data.get('roi', 0)
                    shift_val = best_event_data.get('shift', 0)
                    depth_val = best_event_data.get('depth', 0)
                    curr_loss = best_event_data.get('current_loss_streak', 0)
                    max_loss = best_event_data.get('max_loss_streak', 0)

                    if roi_val > 0:
                        if shift_val > 5:
                            rec_strategy = "Флэт 2-3% (Тренд 📈)"
                        elif shift_val < -5:
                            rec_strategy = "Флэт 1% (Тренд падает 📉)"
                        else:
                            rec_strategy = "Флэт 2%"
                        
                        if curr_loss >= max_loss and max_loss >= 3:
                            rec_strategy += " + Легкий догон"
                    else:
                        rec_strategy = "Пропуск или Флэт 1%"

                    ai_msg = (
                        f"🧠 **Адаптивный ИИ-Анализ:**\n"
                        f"▪️ Фаворит: `{best_event_data.get('name', '—')}` (ROI: {roi_val:+.1f}%, WR: {best_event_data.get('win_rate', 0):.1f}%)\n"
                        f"▪️ Сдвиг: `{shift_val:+.1f}%` | Глубина: `{depth_val}` т.\n"
                        f"▪️ **Совет:** {rec_strategy}\n"
                        f"▪️ **Стратегия чисел:** `{CURRENT_STRATEGY_NAME}`\n\n"
                    )

                    # 6. Формирование общего сообщения
                    msg = f"🎲 **Тираж №{draw_num}** | Числа: **{', '.join(map(str, nums))}**\n\n"
                    msg += pred_msg + "\n"
                    
                    if bets_alerts:
                        msg += "⚠️ **Сигналы:**\n"
                        for alert in bets_alerts: msg += f"💰 {alert}\n"
                        msg += "\n"
                    
                    msg += self_analysis_msg + "\n"
                    msg += ai_msg
                    msg += f"🔮 **Прогноз чисел:** `{', '.join(map(str, new_prediction))}` НЕ выпадут."
                    
                    send_to_lolka(WEBHOOK_BETS, msg)

                    # 7. Залп при 3+ совпадениях
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
                    print(f"Тираж №{draw_num} уже есть в базе.", flush=True)
            else:
                print("Не удалось получить данные. Ждем 5 минут.", flush=True)
                
        except Exception as e:
            print(f"❌ Критическая ошибка в цикле: {e}", flush=True)

        print("--- Пауза 5 минут ---", flush=True)
        time.sleep(300)

if __name__ == '__main__':
    threading.Thread(target=run_dummy_server, daemon=True).start()
    main_loop()