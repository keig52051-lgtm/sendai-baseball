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

                # 3. 日付（年月日）の選択
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
                time_slots = ["午前", "午後", "夕方", "夜間"]

                # 5. 「次の施設」ボタンで全野球場をページ送り巡回（最大15施設）
                for f_idx in range(15):
                    # --- 施設名（球場名）の抽出 ---
                    facility_name = ""
                    try:
                        # 左側エリアの「館名」テーブルから読み取る
                        left_box = page.locator("table:has-text('館名'), td:has-text('館名')").first
                        if await left_box.is_visible(timeout=1000):
                            text = await left_box.inner_text()
                            lines = [l.strip() for l in text.split("\n") if l.strip()]
                            for idx, line in enumerate(lines):
                                if "館名" in line and idx + 1 < len(lines):
                                    target = lines[idx + 1]
                                    if "所在地" not in target and "電話" not in target:
                                        facility_name = target
                                        break
                    except Exception:
                        pass

                    if not facility_name:
                        facility_name = f"野球場_{f_idx+1}"

                    # --- メイン空き状況テーブルの精査 ---
                    # 凡例エリア（「凡例」というテキストが含まれるテーブル/div）を除外して抽出
                    rows = await page.locator("table tr").all()
                    for row in rows:
                        row_text = await row.inner_text()
                        row_html = await row.inner_html()

                        # 凡例・お知らせ行を厳重に除外
                        if "凡例" in row_text or "お知らせ" in row_text or "館機能" in row_text:
                            continue

                        cells = await row.locator("td").all()
                        # 空き枠テーブル行（4コマ配置）の判定
                        if len(cells) == 4:
                            for c_idx in range(4):
                                cell = cells[c_idx]
                                c_text = await cell.inner_text()
                                c_html = await cell.inner_html()

                                # 「✕」や「不可」がなく、予約アイコン（img）が存在する場合
                                if "×" not in c_text and "予約不可" not in c_text and "<img" in c_html.lower():
                                    # 凡例画像でないことを最終確認
                                    if "hanrei" not in c_html.lower() and "legend" not in c_html.lower():
                                        found_today_date.append({
                                            "date": date_display,
                                            "facility": facility_name,
                                            "time_slot": time_slots[c_idx]
                                        })

                    # 次の施設へ進むボタンがあるか確認してクリック
                    next_btn = page.locator("input[value*='次'], button:has-text('次'), a:has-text('次の施設')").first
                    if await next_btn.is_visible(timeout=1000):
                        await next_btn.click()
                        await page.wait_for_load_state("domcontentloaded")
                        await asyncio.sleep(1)
                    else:
                        break  # 次の施設がなければその日程の巡回終了

                # 重複の除外
                seen = set()
                unique_found = []
                for f in found_today_date:
                    key = (f['facility'], f['time_slot'])
                    if key not in seen:
                        seen.add(key)
                        unique_found.append(f)

                if unique_found:
                    fac_set = set([x['facility'] for x in unique_found])
                    print(f" → ★【{len(fac_set)}施設で空き検知】 計 {len(unique_found)} 件")
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
