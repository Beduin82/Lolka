import lolka
import asyncio
import requests
from bs4 import BeautifulSoup
import re

TOKEN = 'OTU4MTg0NzUzODU4NTYw.Rt1BLgzvHPb5Td8swY1gKZGrdJd-x8lESNPIhYmqi1U'
intents = lolka.Intents.default()
intents.message_content = True
client = lolka.Client(intents=intents)

async def check_lottery_numbers(num1: int, num2: int, max_pages: int = 3):
    base_url = "https://www.lotonews.ru/archive/dvazhdydva"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    found_draws = []
    loop = asyncio.get_running_loop()
    for page in range(1, max_pages + 1):
        url = f"{base_url}?page={page}" if page > 1 else base_url
        try:
            response = await loop.run_in_executor(None, lambda: requests.get(url, headers=headers, timeout=10))
            response.raise_for_status()
        except Exception:
            continue
        soup = BeautifulSoup(response.text, "html.parser")
        rows = soup.select("table tbody tr")
        if not rows: break
        for row in rows:
            cells = row.find_all("td")
            if len(cells) < 3: continue
            draw_link = cells[1].find("a")
            if not draw_link: continue
            draw_number_text = draw_link.get_text(strip=True)
            if not draw_number_text.isdigit(): continue
            draw_number = int(draw_number_text)
            numbers_text = cells[2].get_text(" ", strip=True)
            numbers = [int(t) for t in numbers_text.split() if t.isdigit() and 1 <= int(t) <= 26]
            if num1 in numbers and num2 in numbers:
                date = cells[0].get_text(strip=True)
                found_draws.append((draw_number, date, numbers))
        await asyncio.sleep(0.5)
    return found_draws

@client.event
async def on_ready():
    print(f'✅ Бот {client.user} успешно запущен!')

@client.event
async def on_message(message):
    if message.author.bot: return
    if message.content.startswith('!check'):
        numbers_match = re.findall(r'\d+', message.content)
        if len(numbers_match) != 2:
            await message.channel.send("⚠️ Укажи ровно два числа. Пример: `!check 7 19`")
            return
        num1, num2 = int(numbers_match[0]), int(numbers_match[1])
        if not (1 <= num1 <= 26) or not (1 <= num2 <= 26):
            await message.channel.send("⚠️ Числа должны быть от 1 до 26.")
            return
        if num1 == num2:
            await message.channel.send("⚠️ Числа должны быть разными.")
            return
        status_msg = await message.channel.send(f"🔍 Ищу тиражи, где выпали **{num1}** и **{num2}**...")
        results = await check_lottery_numbers(num1, num2)
        await status_msg.delete()
        if results:
            response_text = f"🎯 **Найдены совпадения (последние 30 тиражей):**\n\n"
            for draw, date, nums in results:
                response_text += f"📅 Дата: {date}\n🔢 Тираж: `№{draw}`\n🎲 Числа: {', '.join(map(str, nums))}\n\n"
            await message.channel.send(response_text)
        else:
            await message.channel.send(f"🙁 В последних 30 тиражах числа **{num1}** и **{num2}** вместе не выпадали.")

if __name__ == '__main__':
    client.run(TOKEN)