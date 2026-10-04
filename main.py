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

async def main():
    target_dates = get_target_dates()
    print(f"【検証開始】対象日付: 全 {len(target_dates)} 日間 (土日・祝日)\n")
    
    # 最初の1日分で画面構造を完全特定
    sample_date = target_dates[0]
    date_display = f"{sample_date['date_str']}({sample_date['weekday']}) {sample_date['note']}".strip()
    print(f"=== 画面解析デバッグ実行中 [{date_display}] ===")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=['--no-sandbox', '--disable-setuid-sandbox'])
        context = await browser.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
        page = await context.new_page()

        try:
            # 1. 初期ページ移動
            await page.goto("https://www.cm2.epss.jp/sendai/web/view/user/c019RsvEmptyState.html", timeout=60000)
            await page.wait_for_load_state("networkidle")

            # 2. ページ内の全フォーム・アンカー要素の構造を出力
            print("\n【解析1: 初期ページのフォーム・リンク構造】")
            forms_info = await page.evaluate('''
                () => {
                    let info = [];
                    document.querySelectorAll('form').forEach((f, i) => {
                        info.push(`Form ${i}: action=${f.action}, id=${f.id}, name=${f.name}`);
                    });
                    document.querySelectorAll('a, input[type="button"], input[type="submit"]').forEach((e) => {
                        let txt = e.innerText || e.value || e.alt || '';
                        if (txt.includes('野球') || txt.includes('スポーツ') || txt.includes('屋外') || txt.includes('検索')) {
                            info.push(`Elem: tag=${e.tagName}, text="${txt.trim()}", id=${e.id}, name=${e.name}, href=${e.href||''}`);
                        }
                    });
                    return info.join("\\n");
                }
            ''')
            print(forms_info)

            # 3. 野球/屋外スポーツカテゴリの確実なクリック試行
            clicked = False
            for selector in ["a:has-text('野球')", "a:has-text('屋外スポーツ')", "input[value*='野球']", "input[value*='屋外']"]:
                try:
                    elem = page.locator(selector).first
                    if await elem.is_visible(timeout=1000):
                        print(f"\n→ セレクター '{selector}' をクリックします")
                        await elem.click()
                        await page.wait_for_load_state("networkidle")
                        clicked = True
                        break
                except Exception:
                    continue

            # 4. 日付の指定
            selects = page.locator("select")
            if await selects.count() >= 3:
                await selects.nth(0).select_option(value=sample_date['year'])
                await selects.nth(1).select_option(value=sample_date['month'])
                await selects.nth(2).select_option(value=sample_date['day'])

            # 5. 検索実行
            search_btn = page.locator("input[type='submit'], input[type='button'], input[type='image'], button").filter(has_text="検索").first
            if await search_btn.is_visible(timeout=2000):
                print("→ 検索ボタンをクリックします")
                await search_btn.click()
                await page.wait_for_load_state("networkidle")
                await asyncio.sleep(2)

            # 6. 遷移後の現在URLとHTMLスナップショットの保存・ログ出力
            current_url = page.url
            print(f"\n【解析2: 検索後のURL】\n{current_url}\n")

            # 画面上のタイトル・テーブルヘッダー・主要タグの抽出ログ
            page_structure = await page.evaluate('''
                () => {
                    let res = [];
                    res.push("=== TITLE / HEADERS ===");
                    document.querySelectorAll('h1, h2, h3, h4, .title, .shisetsu_name, caption, th').forEach(e => {
                        let t = e.innerText.trim();
                        if (t.length > 0 && t.length < 100) {
                            res.push(`<${e.tagName.toLowerCase()} class="${e.className}"> ${t}`);
                        }
                    });
                    res.push("\\n=== TABLES COUNT ===");
                    res.push(`Total tables: ${document.querySelectorAll('table').length}`);
                    return res.join("\\n");
                }
            ''')
            print("【解析3: 遷移後画面のDOM構造ログ】")
            print(page_structure)

            # HTMLファイルとスクリーンショットを保存
            html_content = await page.content()
            with open("debug_search_result.html", "w", encoding="utf-8") as f:
                f.write(html_content)
            await page.screenshot(path="debug_search_result.png", full_page=True)
            print("\n★ `debug_search_result.html` および `debug_search_result.png` を保存しました。")

        except Exception as ex:
            print(f"⚠️ エラー発生: {ex}")
        finally:
            await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
