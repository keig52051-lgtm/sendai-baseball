import asyncio
import os
import smtplib
from email.mime.text import MIMEText
from datetime import date
import calendar
import jpholiday
from playwright.async_api import async_playwright

# 環境変数からメール設定を取得
GMAIL_USER = os.environ.get("GMAIL_USER")
GMAIL_PASS = os.environ.get("GMAIL_PASS")
TO_EMAIL = os.environ.get("TO_EMAIL", GMAIL_USER)  # 未設定の場合は送信元と同じアドレスへ

def send_email_notification(vacancies):
    if not vacancies or not GMAIL_USER or not GMAIL_PASS:
        print("※ メール通知をスキップしました (空き枠なし、またはGMAIL_USER/GMAIL_PASS未設定)")
        return

    subject = f"【野球場 空き枠通知】仙台市施設予約 ({len(vacancies)}件)"
    body = f"仙台市市民利用施設予約システムで{len(vacancies)}件の空きが見つかりました。\n\n"
    
    current_date = ""
    for v in vacancies:
        if v['date'] != current_date:
            current_date = v['date']
            body += f"\n■ {current_date}"
        body += f"\n  ・【{v['facility']}】 {v['time_slot']}"

    body += "\n\n▼予約ログインはこちら\nhttps://www.cm2.epss.jp/sendai/web/view/user/c019RsvEmptyState.html"

    msg = MIMEText(body)
    msg['Subject'] = subject
    msg['From'] = GMAIL_USER
    msg['To'] = TO_EMAIL

    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
            server.login(GMAIL_USER, GMAIL_PASS)
            server.send_message(msg)
        print("★【成功】メール通知を送信しました。")
    except Exception as e:
        print(f"メール送信エラー: {e}")

def get_target_dates():
    today = date.today()
    target_dates = []
    for i in range(3):
        year = today.year + (today.month + i - 1) // 12
        month = (today.month + i - 1) % 12 + 1
        _, last_day = calendar.monthrange(year, month)
        for day in range(1, last_day + 1):
            d = date(year, month, day)
            if d >= today and (d.weekday() in [5, 6] or jpholiday.is_holiday(d)):
                holiday_name = jpholiday.is_holiday_name(d)
                type_str = f"({holiday_name})" if holiday_name else ""
                target_dates.append({
                    "date_str": d.strftime("%Y/%m/%d"),
                    "year": str(d.year),
                    "month": str(d.month),
                    "day": str(d.day),
                    "weekday": ["月", "火", "水", "木", "金", "土", "日"][d.weekday()],
                    "note": type_str
                })
    return target_dates

async def main():
    target_dates = get_target_dates()
    print(f"【検証開始】対象日付: 全 {len(target_dates)} 日間 (土日・祝日)\n")
    all_vacancies = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=['--no-sandbox', '--disable-setuid-sandbox'])
        context = await browser.new_context()
        page = await context.new_page()

        for item in target_dates:
            date_display = f"{item['date_str']}({item['weekday']}) {item['note']}".strip()
            print(f"[{date_display}] を確認中...", end="", flush=True)
            try:
                await page.goto("https://www.cm2.epss.jp/sendai/web/view/user/c019RsvEmptyState.html", timeout=60000)
                await page.wait_for_load_state("networkidle")

                try:
                    baseball_btn = page.locator("a, button, input").filter(has_text="野球").first
                    if await baseball_btn.is_visible(timeout=3000):
                        await baseball_btn.click()
                        await page.wait_for_load_state("networkidle")
                except Exception:
                    pass

                await page.wait_for_selector("select", timeout=10000)
                selects = await page.locator("select").all()

                if len(selects) >= 3:
                    await selects[0].select_option(value=item['year'])
                    await selects[1].select_option(value=item['month'])
                    await selects[2].select_option(value=item['day'])
                else:
                    await page.select_option("select:nth-of-type(1)", value=item['year'])
                    await page.select_option("select:nth-of-type(2)", value=item['month'])
                    await page.select_option("select:nth-of-type(3)", value=item['day'])

                search_btn = page.locator("input[type='submit'], input[type='button'], button").filter(has_text="検索").first
                await search_btn.click()
                await page.wait_for_load_state("networkidle")
                await asyncio.sleep(1)

                found_today = []
                current_facility = ""
                rows = await page.locator("table tr").all()

                for row in rows:
                    row_text = await row.inner_text()
                    if "凡例" in row_text or "お知らせ" in row_text:
                        continue
                    cells = await row.locator("td, th").all()
                    for cell in cells:
                        txt = (await cell.inner_text()).strip()
                        if txt and ("野球場" in txt or "グラウンド" in txt or "球場" in txt) and len(txt) > 3:
                            current_facility = txt

                    if current_facility:
                        for c_idx, cell in enumerate(cells):
                            has_vacancy = await cell.locator("img[alt*='予約可'], img[title*='予約可'], td:has-text('○')").count() > 0
                            if has_vacancy:
                                time_slots = ["午前", "午後", "夕方", "夜間"]
                                slot_name = time_slots[max(0, min(c_idx - 1, len(time_slots) - 1))]
                                found_today.append({
                                    "date": date_display,
                                    "facility": current_facility,
                                    "time_slot": slot_name
                                })

                seen = set()
                unique_found = []
                for f in found_today:
                    key = (f['facility'], f['time_slot'])
                    if key not in seen:
                        seen.add(key)
                        unique_found.append(f)

                if unique_found:
                    print(f" → ★空き {len(unique_found)} 件発見")
                    all_vacancies.extend(unique_found)
                else:
                    print(" → 空きなし")

            except Exception as ex:
                print(f" → ⚠️ スキップ: {ex}")
                continue

        await browser.close()

    print(f"\n全巡回完了: 計 {len(all_vacancies)} 件の空き枠を検知")
    if all_vacancies:
        send_email_notification(all_vacancies)

if __name__ == "__main__":
    asyncio.run(main())
