import asyncio
import os
from datetime import date
import calendar
import jpholiday
from playwright.async_api import async_playwright

def get_target_dates():
    today = date.today()
    target_dates = []
    for i in range(1): # 今回は最初の1日分（確認用）
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
                break
        if target_dates:
            break
    return target_dates

async def main():
    target_dates = get_target_dates()
    sample = target_dates[0]
    date_display = f"{sample['date_str']}({sample['weekday']}) {sample['note']}".strip()
    print(f"=== 1日分詳細デバッグ開始 [{date_display}] ===")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=['--no-sandbox', '--disable-setuid-sandbox'])
        context = await browser.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
        page = await context.new_page()

        try:
            # 1. 空き照会画面への直接アクセス
            await page.goto("https://www.cm2.epss.jp/sendai/web/view/user/c019RsvEmptyState.html", timeout=60000)
            await page.wait_for_load_state("domcontentloaded")
            await asyncio.sleep(1)

            # 2. 野球/屋外スポーツの選択
            buttons = await page.locator("a, input[type='button'], input[type='submit'], button").all()
            for btn in buttons:
                try:
                    txt = await btn.inner_text() if await btn.is_visible() else ""
                    val = await btn.get_attribute("value") or ""
                    target_str = txt + val
                    if "イベント" in target_str or "evtSearch" in (await btn.get_attribute("id") or ""):
                        continue
                    if "野球" in target_str or "屋外" in target_str:
                        await btn.click()
                        await page.wait_for_load_state("domcontentloaded")
                        await asyncio.sleep(1)
                        break
                except Exception:
                    continue

            # 3. 日付選択
            selects = page.locator("select")
            if await selects.count() >= 3:
                await selects.nth(0).select_option(value=sample['year'])
                await selects.nth(1).select_option(value=sample['month'])
                await selects.nth(2).select_option(value=sample['day'])

            # 4. 検索実行
            search_btns = await page.locator("input[type='submit'], input[type='button'], input[type='image'], button").all()
            for s_btn in search_btns:
                txt = (await s_btn.inner_text() if await s_btn.is_visible() else "") + (await s_btn.get_attribute("value") or "")
                if "検索" in txt and "イベント" not in txt:
                    await s_btn.click()
                    await page.wait_for_load_state("domcontentloaded")
                    await asyncio.sleep(2)
                    break

            # 5. ページ内の最初のテーブル構造とすべてのセル内容を強制出力
            print("\n=== 【テーブル・セル構造の強制出力】 ===")
            tables = await page.locator("table").all()
            print(f"検出されたテーブル総数: {len(tables)}")

            for t_idx, table in enumerate(tables[:3]): # 最初の3つのテーブルを解析
                t_text = await table.inner_text()
                if "イベント情報" in t_text or "ジャンル選択" in t_text:
                    continue
                
                print(f"\n--- テーブル #{t_idx} ---")
                rows = await table.locator("tr").all()
                for r_idx, row in enumerate(rows[:6]): # 各テーブルの最初の6行
                    cells = await row.locator("td, th").all()
                    cell_data = []
                    for cell in cells:
                        c_txt = (await cell.inner_text()).strip().replace('\n', ' ')
                        c_html = await cell.inner_html()
                        cell_data.append(f"[Text: '{c_txt}' | HTML: {c_html}]")
                    if cell_data:
                        print(f"  Row {r_idx}: " + " | ".join(cell_data))

        except Exception as ex:
            print(f"⚠️ エラー発生: {ex}")
        finally:
            await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
