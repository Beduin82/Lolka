import os
import sys
import time
import threading
import random
import re
import requests
from http.server import BaseHTTPRequestHandler, HTTPServer

# --- Selenium Imports ---
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
# -----------------------

if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

# --- НАСТРОЙКИ ---
WEBHOOK_URL = os.environ.get('LOLKA_WEBHOOK_URL')
ARCHIVE_URL = "https://www.stoloto.ru/dvazhdydva"

QUOTES = [
    "«Случайность — это псевдоним Бога, когда он не хочет подписываться своим именем.» — Анатоль Франс",
    "«В лотерею выигрывают только те, кто в нее играет. Но и теряют тоже они.» — Народная мудрость",
    "«Вероятность выиграть в лотерею примерно равна вероятности, что вас ударит молния.»",
    "«Математика — царица наук, но удача — её любимая дочь.»",
    "«Не важно, как часто ты падаешь. Важно, как часто ты покупаешь билет.»"
]
EMOJIS = ["❤️", "💖", "💘", "💝", "✨", "🎯", "🎲", "🍀", "🔥"]

last_processed_draw = None
gaps = {"total_less": 0, "two_even": 0, "matches": 0}

# --- ЗАГЛУШКА ДЛЯ RENDER ---
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

# --- УЛУЧШЕННАЯ ФУНКЦИЯ ПАРСИНГА (ЧЕРЕЗ DOM) ---
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

        # Ждем, пока на странице не появится блок с текстом "Тираж №"
        wait = WebDriverWait(driver, 20)
        draw_element = wait.until(EC.presence_of_element_located((By.XPATH, "//p[contains(text(), 'Тираж №')]")))
        time.sleep(3) # Даем JS время обновить данные
        
        # Получаем номер тиража из этого элемента
        draw_num_text = draw_element.text
        draw_match = re.search(r'Тираж\s*№\s*(\d+)', draw_num_text)
        if not draw_match:
            print("⚠️ Не удалось прочитать номер тиража.", flush=True)
            return None
        draw_num = int(draw_match.group(1))
        
        # Ищем числа в родительских блоках этого элемента (вверх по дереву DOM)
        # Обычно числа лежат в том же контейнере, что и заголовок
        numbers = []
        parent = draw_element
        num_pattern = r'\b([1-9]|1[0-9]|2[0-6])\b'
        
        for _ in range(4): # Поднимаемся на 4 уровня вверх
            parent = parent.find_element(By.XPATH, "..")
            parent_text = parent.text
            
            # Ищем все числа от 1 до 26 в тексте этого блока
            found_nums = [int(n) for n in re.findall(num_pattern, parent_text)]
            
            # Если нашли ровно 4 числа (или больше, но кратные 4), берем первые 4
            if len(found_nums) >= 4:
                numbers = found_nums[:4]
                break
                
        if not numbers:
            print("⚠️ Не удалось найти 4 числа в блоке с тиражом.", flush=True)
            return None
        
        date_str = time.strftime("%d.%m.%Y")
        print(f"✅ Selenium спарсил тираж №{draw_num}. Числа: {numbers}", flush=True)
        return {"draw": draw_num, "date": date_str, "numbers": numbers}

    except Exception as e:
        print(f"❌ Ошибка Selenium: {e}", flush=True)
        return None
    finally:
        if driver:
            driver.quit()

# --- ФУНКЦИЯ ОТПРАВКИ В LOLKA ---
def send_to_lolka(text):
    if not WEBHOOK_URL:
        print("❌ ОШИБКА: LOLKA_WEBHOOK_URL не задана!", flush=True)
        return
    payload = {"content": text, "username": "Lotto Observer"}
    try:
        res = requests.post(WEBHOOK_URL, json=payload, timeout=10)
        res.raise_for_status()
        print("✅ Сообщение отправлено в Lolka.", flush=True)
    except Exception as e:
        print(f"❌ Ошибка отправки в Lolka: {e}", flush=True)

# --- ОСНОВНАЯ ЛОГИКА ---
def main_loop():
    global last_processed_draw, gaps
    print("🚀 Мониторинг запущен (Selenium DOM). Отслеживаем совпадения.", flush=True)
    
    while True:
        print("Начинаю новую проверку (запуск браузера)...", flush=True)
        draw_data = get_last_draw()
        
        if draw_data:
            draw_num = draw_data["draw"]
            nums = draw_data["numbers"]
            
            if draw_num != last_processed_draw:
                print(f"\n🔍 Новый тираж: №{draw_num}, числа: {nums}", flush=True)
                last_processed_draw = draw_num
                
                total_sum = sum(nums)
                even_count = sum(1 for x in nums if x % 2 == 0)
                has_duplicates = len(set(nums)) < 4
                
                alerts = []

                if total_sum < 53.5: gaps["total_less"] = 0
                else:
                    gaps["total_less"] += 1
                    if gaps["total_less"] >= 3:
                        alerts.append(f"📉 **Тотал Меньше 53.5** не выпадает уже **{gaps['total_less']}** тиража подряд.")
                
                if even_count == 2: gaps["two_even"] = 0
                else:
                    gaps["two_even"] += 1
                    if gaps["two_even"] >= 3:
                        alerts.append(f"⚖️ **Ровно 2 четных** не выпадает уже **{gaps['two_even']}** тиража подряд.")

                if has_duplicates:
                    gaps["matches"] = 0
                    dup_str = ", ".join([str(x) for x in set(nums) if nums.count(x) > 1])
                    alerts.append(f"🎯 **Совпадение!** Выпали одинаковые числа: **{dup_str}**")
                else:
                    gaps["matches"] += 1
                    if gaps["matches"] >= 8:
                        alerts.append(f"👯 **Совпадений** нет уже **{gaps['matches']}** тиражей подряд.")
                
                if alerts:
                    heart = random.choice(EMOJIS)
                    quote = random.choice(QUOTES)
                    msg = f"{heart} **Анализ тиража №{draw_num}** {heart}\n"
                    msg += f"📅 {draw_data['date']} | Числа: {', '.join(map(str, nums))}\n\n"
                    for alert in alerts: msg += f"🔔 {alert}\n\n"
                    msg += f"> *{quote}*"
                    send_to_lolka(msg)
                else:
                    print("События в пределах нормы, уведомлений нет.", flush=True)
            else:
                print(f"Тираж №{draw_num} уже был обработан ранее.", flush=True)
        else:
            print("Не удалось получить данные. Ждем 5 минут.", flush=True)
        
        print("--- Пауза 5 минут ---", flush=True)
        time.sleep(300)

if __name__ == '__main__':
    threading.Thread(target=run_dummy_server, daemon=True).start()
    main_loop()