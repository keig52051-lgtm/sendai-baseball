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

async def parse_current_facility(page, date_display, default_name="野球場"):
    time_slots = ["午前", "午後", "夕方", "夜間"]
    facility_name = ""

    # 1. 「館名」セルに隣接する値（例：西花苑公園野球場）をピンポイント取得
    try:
        for label in ["館名", "施設名", "施設"]:
            kan_cell = page.locator(f"th:has-text('{label}'), td:has-text('{label}')").first
            if await kan_cell.is_visible(timeout=800):
                # 隣接する td セルを取得
                next_td = kan_cell.locator("xpath=following-sibling::td[1]")
                if await next_td.count() > 0:
                    txt = (await next_td.inner_text()).strip()
                    if txt and len(txt) > 1 and "所在地" not in txt:
                        facility_name = txt
                        break
    except Exception:
        pass

    # 2. バックアップ：正規表現検索
    if not facility_name:
        try:
            body_text = await page.locator("body").inner_text()
            matches = re.findall(r'([一-龥ぁ-んァ-ヶa-zA-Z0-90-９\-_]{2,20}(?:野球場|公園野球場|グラウンド|運動場))', body_text)
            for m in matches:
                if "検索" not in m and "利用" not in m and "案内" not in m and "凡例" not in m:
                    facility_name = m.strip()
                    break
        except Exception:
            pass

    if not facility_name:
        facility_name = default_name

    found = []
    rows = await page.locator("table tr").all()
    for row in rows:
        row_text = await row.inner_text()
        if "凡例" in row_text or "お知らせ" in row_text or "館機能" in row_text:
            continue

        cells = await row.locator("td").all()
        if len(cells) == 4:
            for c_idx in range(4):
                cell = cells[c_idx]
                c_text = await cell.inner_text()
                c_html = await cell.inner_html()

                if "×" not in c_text and "予約不可" not in c_text and "<img" in c_html.lower():
                    if "hanrei" not in c_html.lower() and "legend" not in c_html.lower():
                        found.append({
                            "date": date_display,
                            "facility": facility_name,
                            "time_slot": time_slots[c_idx]
                        })

    return found, facility_name

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
                # 1. トップページへアクセス
                await page.goto("https://www.cm2.epss.jp/sendai/web/view/user/c019RsvEmptyState.html", timeout=60000)
                await page.wait_for_load_state("domcontentloaded")
                await asyncio.sleep(1)

                # 2. 野球カテゴリ選択
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

                # 3. 日付選択
                selects = page.locator("select")
                if await selects.count() >= 3:
                    await selects.nth(0).select_option(value=item['year'])
                    await selects.nth(1).select_option(value=item['month'])
                    await selects.nth(2).select_option(value=item['day'])

                # 4. 検索実行
                search_btn = page.locator("input[type='submit'], input[type='button'], button, a").filter(has_text="検索").first
                if await search_btn.is_visible(timeout=2000):
                    await search_btn.click()
                    await page.wait_for_load_state("domcontentloaded")
                    await asyncio.sleep(1.5)

                found_today_date = []

                # 5. 施設巡回（ドロップダウンまたはページ送りボタン）
                all_selects = await page.locator("select").all()
                facility_select = None
                for sel in all_selects:
                    try:
                        options = await sel.locator("option").all_inner_texts()
                        if any("野球" in opt or "公園" in opt or "グラウンド" in opt for opt in options):
                            facility_select = sel
                            break
                    except Exception:
                        continue

                if facility_select:
                    options = await facility_select.locator("option").all()
                    for opt_idx in range(len(options)):
                        opt_elem = options[opt_idx]
                        opt_value = await opt_elem.get_attribute("value")
                        opt_text = (await opt_elem.inner_text()).strip()

                        await facility_select.select_option(value=opt_value)
                        change_btn = page.locator("input[value*='表示'], input[value*='変更'], button:has-text('表示'), button:has-text('変更')").first
                        if await change_btn.is_visible(timeout=800):
                            await change_btn.click()

                        await page.wait_for_load_state("domcontentloaded")
                        await asyncio.sleep(0.8)

                        v_list, f_name = await parse_current_facility(page, date_display, default_name=opt_text)
                        found_today_date.extend(v_list)
                else:
                    for f_idx in range(15):
                        v_list, f_name = await parse_current_facility(page, date_display, default_name=f"野球場_{f_idx+1}")
                        found_today_date.extend(v_list)

                        # ページ送りボタンを検出・クリック
                        next_clicked = False
                        next_selectors = [
                            "input[value*='次']", "input[alt*='次']", "input[title*='次']",
                            "button:has-text('次')", "a:has-text('次')", "a:has-text('次の施設')",
                            "input[value*='館']", "button:has-text('館')", "a[href*='Next']", "a[href*='next']"
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
                            # 1回目のループでボタンが見つからなかった場合、画面上のボタン・リンク要素をデバッグログに出力
                            if f_idx == 0 and item == target_dates[0]:
                                try:
                                    inputs = await page.locator("input, button, a").all()
                                    input_info = []
                                    for inp in inputs[:15]:
                                        val = await inp.get_attribute("value") or ""
                                        txt = await inp.inner_text() or ""
                                        alt = await inp.get_attribute("alt") or ""
                                        info = (val or txt or alt).strip().replace("\n", " ")
                                        if info:
                                            input_info.append(info)
                                    print(f" [Debug:画面ボタン要素] {input_info[:8]}")
                                except Exception:
                                    pass
                            break

                # 重複除外
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
