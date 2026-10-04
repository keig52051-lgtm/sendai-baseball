import asyncio
import os
import re
import smtplib
from email.mime.text import MIMEText
from datetime import date
import calendar
import jpholiday
from playwright.async_api import async_playwright

GMAIL_USER = os.environ.get("GMAIL_USER")
GMAIL_PASS = os.environ.get("GMAIL_PASS")
TO_EMAIL = os.environ.get("TO_EMAIL", GMAIL_USER)

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

async def parse_and_debug_page(page, date_display):
    time_slots = ["午前", "午後", "夕方", "夜間"]
    found = []

    # 1. 画面全体のテキスト構造を解析
    body_text = await page.locator("body").inner_text()
    
    # 2. 空きアイコン・コマが存在するセルを直接検索
    tables = await page.locator("table").all()
    for table_idx, table in enumerate(tables):
        rows = await table.locator("tr").all()
        for row in rows:
            row_text = await row.inner_text()
            if "凡例" in row_text or "お知らせ" in row_text:
                continue

            cells = await row.locator("td").all()
            if len(cells) == 4:
                for c_idx in range(4):
                    cell = cells[c_idx]
                    c_text = await cell.inner_text()
                    c_html = await cell.inner_html()

                    if "×" not in c_text and "不可" not in c_text and ("<img" in c_html.lower() or "○" in c_text or "空" in c_text):
                        if "hanrei" not in c_html.lower() and "legend" not in c_html.lower():
                            found.append({
                                "date": date_display,
                                "facility": f"未定_{table_idx+1}",
                                "time_slot": time_slots[c_idx],
                                "table_elem": table
                            })

    # 空きが見つかった場合、該当画面のHTMLスナップショット（デバッグ用）を出力
    if found:
        print("\n" + "="*50)
        print("【DEBUG: 空き枠検出画面のHTML解析ログ】")
        # テーブル周辺のHTML構造を極力簡潔に出力
        try:
            html_snippet = await page.evaluate('''
                () => {
                    let result = [];
                    let elems = document.querySelectorAll('h2, h3, h4, th, td, caption, div.title, .facility');
                    elems.forEach(e => {
                        let txt = e.innerText.trim().replace(/\\s+/g, ' ');
                        if (txt.length > 0 && txt.length < 100) {
                            result.push(`<${e.tagName.toLowerCase()} class="${e.className}"> ${txt} </${e.tagName.toLowerCase()}>`);
                        }
                    });
                    return result.slice(0, 30).join("\\n");
                }
            ''')
            print(html_snippet)
        except Exception as e:
            print(f"HTML解析エラー: {e}")
        print("="*50 + "\n")

    return found

async def main():
    target_dates = get_target_dates()
    print(f"【検証開始】対象日付: 全 {len(target_dates)} 日間 (土日・祝日)\n")
    all_vacancies = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=['--no-sandbox', '--disable-setuid-sandbox'])
        context = await browser.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
        page = await context.new_page()

        for item in target_dates:
            date_display = f"{item['date_str']}({item['weekday']}) {item['note']}".strip()
            print(f"[{date_display}] を確認中...", end="", flush=True)
            try:
                await page.goto("https://www.cm2.epss.jp/sendai/web/view/user/c019RsvEmptyState.html", timeout=60000)
                await page.wait_for_load_state("domcontentloaded")
                await asyncio.sleep(1)

                for target_text in ["野球", "屋外スポーツ", "スポーツ"]:
                    try:
                        elem = page.locator(f"a:has-text('{target_text}'), input[value*='{target_text}'], button:has-text('{target_text}')").first
                        if await elem.is_visible(timeout=1500):
                            await elem.click()
                            await page.wait_for_load_state("domcontentloaded")
                            await asyncio.sleep(1)
                            break
                    except Exception:
                        continue

                selects = page.locator("select")
                if await selects.count() >= 3:
                    await selects.nth(0).select_option(value=item['year'])
                    await selects.nth(1).select_option(value=item['month'])
                    await selects.nth(2).select_option(value=item['day'])

                search_btn = page.locator("input[type='submit'], input[type='button'], input[type='image'], button, a").filter(has_text="検索").first
                if await search_btn.is_visible(timeout=2000):
                    await search_btn.click()
                    await page.wait_for_load_state("domcontentloaded")
                    await asyncio.sleep(1.5)

                found_today_date = []

                for page_loop in range(15):
                    v_list = await parse_and_debug_page(page, date_display)
                    found_today_date.extend(v_list)

                    next_clicked = False
                    next_selectors = [
                        "input[type='image'][alt*='次']", "input[type='image'][title*='次']",
                        "img[alt*='次']", "a:has(img[alt*='次'])",
                        "input[value*='次']", "input[alt*='次']", "input[name*='next']", "input[name*='Next']",
                        "button:has-text('次')", "a:has-text('次')", "a:has-text('次の施設')", "a:has-text('次へ')"
                    ]

                    for selector in next_selectors:
                        try:
                            btn = page.locator(selector).first
                            if await btn.is_visible(timeout=800):
                                await btn.click()
                                await page.wait_for_load_state("domcontentloaded")
                                await asyncio.sleep(1)
                                next_clicked = True
                                break
                        except Exception:
                            continue

                    if not next_clicked:
                        break

                seen = set()
                unique_found = []
                for f in found_today_date:
                    key = (f['facility'], f['time_slot'])
                    if key not in seen:
                        seen.add(key)
                        unique_found.append(f)

                if unique_found:
                    details = ", ".join([f"【{x['facility']}】{x['time_slot']}" for x in unique_found])
                    print(f" → ★空き {len(unique_found)} 件発見: {details}")
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
