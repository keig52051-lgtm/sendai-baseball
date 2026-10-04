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

async def parse_page_facilities(page, date_display):
    time_slots = ["午前", "午後", "夕方", "夜間"]
    found = []

    tables = await page.locator("table").all()
    for table_idx, table in enumerate(tables):
        table_text = await table.inner_text()
        
        # 不要なフォームテーブルや案内用テーブルを除外
        if any(skip in table_text for skip in ["イベント情報", "ジャンル選択", "曜日指定", "利用環境", "ログイン", "お知らせ"]):
            continue

        facility_name = ""
        
        # 施設名の抽出（ヘッダー行・caption・td.title等から）
        title_elems = await table.locator("caption, th, td.tbl_title, .shisetsu_name, tr:first-child td, tr:first-child th").all()
        for elem in title_elems:
            txt = (await elem.inner_text()).strip()
            if txt and not any(k in txt for k in ["午前", "午後", "夕方", "夜間", "凡例", "区分", "利用時間", "時間帯", "検索"]):
                txt_clean = re.sub(r'\s+', ' ', txt)
                if len(txt_clean) >= 2:
                    facility_name = txt_clean
                    break

        if not facility_name:
            prev_txt = await table.evaluate('''
                (tbl) => {
                    let prev = tbl.previousElementSibling;
                    while (prev) {
                        let t = prev.innerText ? prev.innerText.trim() : '';
                        if (t && !t.includes("検索") && !t.includes("凡例") && t.length < 60) return t;
                        prev = prev.previousElementSibling;
                    }
                    return '';
                }
            ''')
            if prev_txt:
                facility_name = re.sub(r'\s+', ' ', prev_txt)

        if not facility_name or "ジャンル" in facility_name:
            continue

        # コマの空き判定
        rows = await table.locator("tr").all()
        for row in rows:
            row_text = await row.inner_text()
            if "凡例" in row_text or "館機能" in row_text or "時間帯" in row_text:
                continue

            cells = await row.locator("td").all()
            if len(cells) == 4:
                for c_idx in range(4):
                    cell = cells[c_idx]
                    c_text = (await cell.inner_text()).strip()
                    c_html = await cell.inner_html()

                    is_vacant = False
                    if "×" not in c_text and "不可" not in c_text:
                        if "<img" in c_html.lower():
                            if any(k in c_html.lower() for k in ["aki", "empty", "vacant", "alt=\"空", "alt=\"○", "alt=\"予約可"]):
                                is_vacant = True
                        elif c_text in ["空き", "空", "予約可"]:
                            is_vacant = True

                    if is_vacant and "hanrei" not in c_html.lower() and "legend" not in c_html.lower():
                        found.append({
                            "date": date_display,
                            "facility": facility_name,
                            "time_slot": time_slots[c_idx]
                        })

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
                # 空き照会画面への直接アクセス
                await page.goto("https://www.cm2.epss.jp/sendai/web/view/user/c019RsvEmptyState.html", timeout=60000)
                await page.wait_for_load_state("domcontentloaded")
                await asyncio.sleep(1)

                # 「イベント検索」などの不要リンクを徹底除外して「屋外スポーツ」「野球」を選択
                buttons = await page.locator("a, input[type='button'], input[type='submit'], button").all()
                for btn in buttons:
                    try:
                        txt = await btn.inner_text() if await btn.is_visible() else ""
                        val = await btn.get_attribute("value") or ""
                        target_str = txt + val
                        
                        # イベント検索（evtSearch / goEvtSearch）は無視
                        if "イベント" in target_str or "evtSearch" in (await btn.get_attribute("id") or ""):
                            continue

                        if "野球" in target_str or "屋外" in target_str:
                            await btn.click()
                            await page.wait_for_load_state("domcontentloaded")
                            await asyncio.sleep(1)
                            break
                    except Exception:
                        continue

                # 年・月・日セレクトの選択
                selects = page.locator("select")
                if await selects.count() >= 3:
                    await selects.nth(0).select_option(value=item['year'])
                    await selects.nth(1).select_option(value=item['month'])
                    await selects.nth(2).select_option(value=item['day'])

                # 検索ボタン押下
                search_btns = await page.locator("input[type='submit'], input[type='button'], input[type='image'], button").all()
                for s_btn in search_btns:
                    txt = (await s_btn.inner_text() if await s_btn.is_visible() else "") + (await s_btn.get_attribute("value") or "")
                    if "検索" in txt and "イベント" not in txt:
                        await s_btn.click()
                        await page.wait_for_load_state("domcontentloaded")
                        await asyncio.sleep(1.5)
                        break

                found_today_date = []

                # 次施設ループ
                for page_loop in range(20):
                    v_list = await parse_page_facilities(page, date_display)
                    found_today_date.extend(v_list)

                    next_clicked = False
                    next_selectors = [
                        "input[type='image'][alt*='次']", "input[type='image'][title*='次']",
                        "img[alt*='次']", "a:has(img[alt*='次'])",
                        "input[value*='次']", "input[alt*='次']", "input[name*='next']",
                        "button:has-text('次')", "a:has-text('次の施設')", "a:has-text('次へ')"
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
