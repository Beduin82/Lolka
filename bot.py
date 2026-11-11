import os
import sys
import time
import threading
import random
import re
import requests
from http.server import BaseHTTPRequestHandler, HTTPServer
from bs4 import BeautifulSoup

# --- Selenium Imports ---
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

# --- НАСТРОЙКИ ---
WEBHOOK_URL = os.environ.get('LOLKA_WEBHOOK_URL')
ARCHIVE_URL = "https://www.lotonews.ru/draws/archive/dvazhdydva"
STREAK_ALERT_COUNT = 5

# --- БАЗА ЦИТАТ (70 штук для примера) ---
# Если вы создадите файл quotes.txt рядом с bot.py, бот будет брать цитаты оттуда!
QUOTES = [
    "«Случайность — это псевдоним Бога, когда он не хочет подписываться своим именем.» — Анатоль Франс",
    "«Лотерея — это дополнительный налог для тех, кто плохо считает.» — Народная мудрость",
    "«Бог не играет в кости со Вселенной.» — Альберт Эйнштейн",
    "«Удача сопутствует смелым.» — Вергилий",
    "«В лотерею выигрывают только те, кто в нее играет. Но и теряют тоже они.» — Народная мудрость",
    "«Случай — это то, что случается с нами, когда мы строим планы.» — Уильям Шекспир",
    "«Вероятность выиграть в лотерею примерно равна вероятности, что вас ударит молния.» — Математики",
    "«Мы сами творим свою удачу.» — Мигель де Сервантес",
    "«Фортуна улыбается тем, кто к ней готов.» — Луи Пастер",
    "«Не важно, как часто ты падаешь. Важно, как часто ты покупаешь билет.» — Народная мудрость",
    "«Успех — это способность идти от неудачи к неудаче, не теряя энтузиазма.» — Уинстон Черчилль",
    "«Риск — это цена, которую мы платим за возможность мечтать.» — Неизвестный автор",
    "«Деньги — хороший слуга, но плохой хозяин.» — Фрэнсис Бэкон",
    "«Не откладывай на завтра то, что можно сделать сегодня.» — Бенджамин Франклин",
    "«Жизнь — это то, что происходит с тобой, пока ты строишь другие планы.» — Джон Леннон",
    "«Единственный способ сделать великую работу — любить то, что ты делаешь.» — Стив Джобс",
    "«В мире нет ничего более постоянного, чем перемены.» — Гераклит",
    "«Я предпочитаю быть оптимистом. Кажется, это не приносит больших преимуществ, но это гораздо приятнее.» — Альберт Эйнштейн",
    "«Тот, кто владеет информацией, владеет миром.» — Натан Ротшильд",
    "«Свобода — это осознанная необходимость.» — Бенедикт Спиноза",
    "«Рукописи не горят.» — Михаил Булгаков",
    "«Все счастливые семьи похожи друг на друга, каждая несчастливая семья несчастлива по-своему.» — Лев Толстой",
    "«Умом Россию не понять, аршином общим не измерить.» — Фёдор Тютчев",
    "«Я жить хочу, чтоб мыслить и страдать.» — Александр Пушкин",
    "«Красота спасёт мир.» — Фёдор Достоевский",
    "«В человеке всё должно быть прекрасно: и лицо, и одежда, и душа, и мысли.» — Антон Чехов",
    "«И вечный бой! Покой нам только снится.» — Александр Блок",
    "«Мы в ответе за тех, кого приручили.» — Антуан де Сент-Экзюпери",
    "«Больше всего говорит тот, кому нечего сказать.» — Лев Толстой",
    "«Счастливые часов не наблюдают.» — Александр Грибоедов",
    "«Быть или не быть — вот в чём вопрос.» — Уильям Шекспир",
    "«Я мыслю, следовательно, я существую.» — Рене Декарт",
    "«Познай самого себя.» — Сократ",
    "«Никто не может причинить тебе боль без твоего согласия.» — Марк Аврелий",
    "«Мы не можем изменить направление ветра, но можем настроить паруса.» — Сенека",
    "«Всё течёт, всё меняется.» — Гераклит",
    "«Человек — это лишь тростник, слабейшее из творений природы, но он — тростник мыслящий.» — Блез Паскаль",
    "«Я не согласен с тем, что вы говорите, но я буду до смерти защищать ваше право это говорить.» — Вольтер",
    "«Тот, кто не помнит своего прошлого, обречён пережить его вновь.» — Джордж Сантаяна",
    "«Самое непостижимое в этом мире — это то, что он постижим.» — Альберт Эйнштейн",
    "«Есть три способа делать что-либо: правильный, неправильный и военный.» — Неизвестный автор",
    "«Сначала вы не понимаете, потом привыкаете, потом забываете.» — Неизвестный автор",
    "«Оптимист — это человек, который считает, что лотерея выиграет его билет.» — Народная мудрость",
    "«Всё гениальное просто, и всё простое гениально.» — Иосиф Бродский",
    "«Хорошо смеётся тот, кто смеётся последним. А кто не смеётся вообще — тот просто умный.» — Неизвестный автор",
    "«Мудрый человек всегда найдёт себе занятие.» — Цицерон",
    "«Ничто не обходится нам так дёшево и не ценится так дорого, как вежливость.» — Мигель де Сервантес",
    "«Сердце имеет свои законы, которых не знает разум.» — Блез Паскаль",
    "«Сложнее всего начать действовать, все остальное зависит только от упорства.» — Амелия Эрхарт",
    "«Если вы думаете, что на что-то способны, вы правы; если думаете, что у вас ничего не получится — вы тоже правы.» — Генри Форд",
    "«Будь тем изменением, которое ты хочешь видеть в мире.» — Махатма Ганди",
    "«Мы должны жить так, чтобы наши дети могли гордиться нами.» — Неизвестный автор",
    "«Никогда не сдавайтесь, потому что в самый последний момент все может измениться.» — Джим Валвано",
    "«Делай что можешь, с тем что имеешь, там где ты есть.» — Теодор Рузвельт",
    "«Всё, что нас не убивает, делает нас сильнее.» — Фридрих Ницше",
    "«Лучший способ предсказать будущее — создать его.» — Питер Друкер",
    "«Не бойтесь медленно идти, бойтесь стоять на месте.» — Китайская пословица",
    "«Лучше сделать и пожалеть, чем не сделать и жалеть всю жизнь.» — Неизвестный автор",
    "«Счастье — это когда тебя понимают.» — Из кинофильма",
    "«Мы — то, что мы делаем постоянно. Совершенство — не акт, а привычка.» — Аристотель",
    "«Там, где есть воля, есть и путь.» — Немецкая пословица",
    "«Не позволяй вчерашнему дню занять слишком много места в сегодняшнем.» — Уилл Роджерс",
    "«Жизнь коротка. Улыбайтесь, пока у вас есть зубы.» — Неизвестный автор",
    "«Никогда не поздно стать тем, кем ты мог бы быть.» — Джордж Элиот",
    "«Всегда выбирайте самый трудный путь — на нем вы не встретите конкурентов.» — Шарль де Голль",
    "«Чтобы дойти до цели, надо прежде всего идти.» — Оноре де Бальзак",
    "«Секрет успеха — не в предсказании будущего, а в создании его.» — Неизвестный автор",
    "«Вчера — это история. Завтра — это тайна. Сегодня — это подарок.» — Алиса в Стране чудес"
]
EMOJIS = ["❤️", "💖", "💘", "💝", "✨", "🎯", "🎲", "🍀", "🔥"]

# --- ФУНКЦИЯ ЗАГРУЗКИ ЦИТАТ ИЗ ФАЙЛА (Опционально) ---
def load_quotes():
    global QUOTES
    if os.path.exists('quotes.txt'):
        try:
            with open('quotes.txt', 'r', encoding='utf-8') as f:
                file_quotes = [line.strip() for line in f if line.strip()]
                if file_quotes:
                    QUOTES = file_quotes
                    print(f"📚 Загружено {len(QUOTES)} цитат из файла quotes.txt", flush=True)
        except Exception as e:
            print(f"⚠️ Ошибка чтения quotes.txt: {e}", flush=True)
    else:
        print(f"📚 Используется встроенная база из {len(QUOTES)} цитат.", flush=True)

last_processed_draw = None
gaps = {"total_less": 0, "two_even": 0}
duplicate_streak = 0 

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
        rows = soup.find_all('tr')
        
        for row in rows:
            row_text = row.get_text(" ", strip=True)
            pattern = r'\b(\d{5,6})\b[^\d]*\b([1-9]|1[0-9]|2[0-6])\b[^\d]*\b([1-9]|1[0-9]|2[0-6])\b[^\d]*\b([1-9]|1[0-9]|2[0-6])\b[^\d]*\b([1-9]|1[0-9]|2[0-6])\b'
            match = re.search(pattern, row_text)
            if match:
                draw_num = int(match.group(1))
                numbers = [int(match.group(i)) for i in range(2, 6)]
                date_str = time.strftime("%d.%m.%Y")
                print(f"✅ Успешно спарсен тираж с LotoNews №{draw_num}. Числа: {numbers}", flush=True)
                return {"draw": draw_num, "date": date_str, "numbers": numbers}
        
        print("⚠️ Не удалось найти подходящую строку с тиражом.", flush=True)
        return None

    except Exception as e:
        print(f"❌ Ошибка Selenium: {e}", flush=True)
        return None
    finally:
        if driver:
            driver.quit()

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

def main_loop():
    global last_processed_draw, gaps, duplicate_streak
    load_quotes()
    print("🚀 Мониторинг запущен (LotoNews). Отслеживаем совпадения и серии.", flush=True)
    
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
                
                # Обновляем счётчик серии
                if has_duplicates:
                    duplicate_streak += 1
                else:
                    duplicate_streak = 0
                
                alerts = []

                # 1. Тотал Меньше 53.5
                if total_sum < 53.5: gaps["total_less"] = 0
                else:
                    gaps["total_less"] += 1
                    if gaps["total_less"] >= 3:
                        alerts.append(f"📉 **Тотал Меньше 53.5** не выпадает уже **{gaps['total_less']}** тиража подряд.")
                
                # 2. Ровно 2 четных
                if even_count == 2: gaps["two_even"] = 0
                else:
                    gaps["two_even"] += 1
                    if gaps["two_even"] >= 3:
                        alerts.append(f"⚖️ **Ровно 2 четных** не выпадает уже **{gaps['two_even']}** тиража подряд.")

                # 3. ВОЗВРАЩАЕМ ОДИНОЧНОЕ УВЕДОМЛЕНИЕ О СОВПАДЕНИИ
                if has_duplicates:
                    dup_str = ", ".join([str(x) for x in set(nums) if nums.count(x) > 1])
                    alerts.append(f"🎯 **Совпадение!** Выпали одинаковые числа: **{dup_str}** (серия: {duplicate_streak})")

                # Отправляем обычные алерты (включая одиночные совпадения)
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
                
                # --- СПЕЦИАЛЬНЫЙ АЛЕРТ ДЛЯ СЕРИИ ИЗ 3 СОВПАДЕНИЙ ---
                if duplicate_streak == 3:
                    print(f"🚨 СЕРИЯ ИЗ 3 СОВПАДЕНИЙ! Отправляем {STREAK_ALERT_COUNT} сообщений...", flush=True)
                    heart = random.choice(EMOJIS)
                    quote = random.choice(QUOTES)
                    streak_msg = (
                        f"🚨🚨🚨 **ВНИМАНИЕ! ТРИ ТИРАЖА ПОДРЯД С СОВПАДЕНИЯМИ!** 🚨🚨🚨\n\n"
                        f"📅 Дата: {draw_data['date']} | Тираж: №{draw_num}\n"
                        f"🎲 Числа: {', '.join(map(str, nums))}\n"
                        f"🔄 В этом тираже тоже есть совпадение!\n\n"
                        f"> *{quote}*"
                    )
                    for i in range(STREAK_ALERT_COUNT):
                        send_to_lolka(streak_msg)
                        time.sleep(1)
            else:
                print(f"Тираж №{draw_num} уже был обработан ранее.", flush=True)
        else:
            print("Не удалось получить данные. Ждем 5 минут.", flush=True)
        
        print("--- Пауза 5 минут ---", flush=True)
        time.sleep(300)

if __name__ == '__main__':
    threading.Thread(target=run_dummy_server, daemon=True).start()
    main_loop()