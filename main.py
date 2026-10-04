import asyncio
import os
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
                # 1. 施設予約トップページへアクセス
                await page.goto("https://www.cm2.epss.jp/sendai/web/view/user/c019RsvEmptyState.html", timeout=60000)
                await page.wait_for_load_state("domcontentloaded")
                await asyncio.sleep(1)

                # 2. 画面上のボタンやリンク（「利用目的」「分類」「野球」等）を柔軟にクリックして検索条件画面を開く
                clicked = False
                for target_text in ["野球", "屋外スポーツ", "スポーツ", "目的から探す", "分類から探す"]:
                    try:
                        elem = page.locator(f"a:has-text('{target_text}'), input[value*='{target_text}'], button:has-text('{target_text}')").first
                        if await elem.is_visible(timeout=2000):
                            await elem.click()
                            await page.wait_for_load_state("domcontentloaded")
                            await asyncio.sleep(1)
                            clicked = True
                            break
                    except Exception:
                        continue

                # 3. セレクトボックス（年・月・日）を探して日付を指定
                # 画面内にselectが存在しない場合はinput[type='text']やJavaScriptの変数をフォールバック
                selects = page.locator("select")
                select_count = await selects.count()

                if select_count >= 3:
                    await selects.nth(0).select_option(value=item['year'])
                    await selects.nth(1).select_option(value=item['month'])
                    await selects.nth(2).select_option(value=item['day'])
                elif select_count > 0:
                    # selectが存在する分だけ順番にセットを試みる
                    for idx, val in enumerate([item['year'], item['month'], item['day']]):
                        if idx < select_count:
                            try:
                                await selects.nth(idx).select_option(value=val)
                            except Exception:
                                pass
                else:
                    # selectが無い画面タイプの場合、フォーム直接入力を試行
                    year_input = page.locator("input[name*='year'], input[name*='Year']").first
                    if await year_input.is_visible(timeout=2000):
                        await year_input.fill(item['year'])
                        await page.locator("input[name*='month'], input[name*='Month']").first.fill(item['month'])
                        await page.locator("input[name*='day'], input[name*='Day']").first.fill(item['day'])

                # 4. 検索を実行
                search_btn = page.locator("input[type='submit'], input[type='button'], button, a").filter(has_text="検索").first
                if await search_btn.is_visible(timeout=3000):
                    await search_btn.click()
                    await page.wait_for_load_state("domcontentloaded")
                    await asyncio.sleep(1.5)

                # 5. 空き枠情報の解析
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
                            # 「○」または予約可能アイコンの判定
                            has_vacancy = (
                                await cell.locator("img[alt*='可'], img[title*='可']").count() > 0 or
                                "○" in (await cell.inner_text())
                            )
                            if has_vacancy:
                                time_slots = ["午前", "午後", "夕方", "夜間"]
                                slot_name = time_slots[max(0, min(c_idx - 1, len(time_slots) - 1))]
                                found_today.append({
                                    "date": date_display,
                                    "facility": current_facility,
                                    "time_slot": slot_name
                                })

                # 重複の除外
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
