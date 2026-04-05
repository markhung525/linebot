import os
import re
import json
from datetime import datetime
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
import gspread
from google.oauth2.service_account import Credentials

app = Flask(__name__)

# ===== 設定區 =====
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET")
GOOGLE_SHEET_ID = os.environ.get("GOOGLE_SHEET_ID")
GOOGLE_CREDENTIALS_JSON = os.environ.get("GOOGLE_CREDENTIALS_JSON")

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# ===== Google Sheets 連線 =====
def get_sheet():
    creds_dict = json.loads(GOOGLE_CREDENTIALS_JSON)
    scopes = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    client = gspread.authorize(creds)
    sheet = client.open_by_key(GOOGLE_SHEET_ID).sheet1
    return sheet

# ===== 解析記帳訊息 =====
# 支援格式：
#   午餐 120
#   午餐 120 麥當勞
#   交通 50 捷運
#   收入 5000 薪水
CATEGORIES = ["餐飲", "早餐", "午餐", "晚餐", "飲料", "宵夜",
               "交通", "捷運", "公車", "計程車", "uber",
               "購物", "衣服", "日用品", "電子",
               "娛樂", "電影", "遊戲", "旅遊",
               "醫療", "健康", "藥局",
               "帳單", "水電", "房租", "網路",
               "收入", "薪水", "獎金", "兼職",
               "其他"]

def guess_category(keyword):
    mapping = {
        "早餐": "餐飲", "午餐": "餐飲", "晚餐": "餐飲",
        "飲料": "餐飲", "宵夜": "餐飲", "餐飲": "餐飲",
        "麥當勞": "餐飲", "便利商店": "餐飲", "超商": "餐飲",
        "捷運": "交通", "公車": "交通", "計程車": "交通", "uber": "交通", "交通": "交通",
        "購物": "購物", "衣服": "購物", "日用品": "購物",
        "娛樂": "娛樂", "電影": "娛樂", "遊戲": "娛樂",
        "醫療": "醫療", "藥局": "醫療", "健康": "醫療",
        "帳單": "帳單", "房租": "帳單", "水電": "帳單", "網路": "帳單",
        "收入": "收入", "薪水": "收入", "獎金": "收入", "兼職": "收入",
    }
    for k, v in mapping.items():
        if k in keyword:
            return v
    return "其他"

def parse_message(text):
    text = text.strip()
    # 格式：[名稱] [金額] [備註(可選)]
    pattern = r'^(.+?)\s+(\d+(?:\.\d+)?)\s*(.*)$'
    match = re.match(pattern, text)
    if not match:
        return None
    name = match.group(1).strip()
    amount = float(match.group(2))
    note = match.group(3).strip()
    category = guess_category(name)
    record_type = "收入" if category == "收入" else "支出"
    return {
        "date": datetime.now().strftime("%Y/%m/%d"),
        "time": datetime.now().strftime("%H:%M"),
        "name": name,
        "amount": amount,
        "category": category,
        "type": record_type,
        "note": note,
    }

# ===== 新增記帳資料到 Google Sheets =====
def add_record(record):
    sheet = get_sheet()
    # 如果是第一筆，補上標題列
    if sheet.row_count == 0 or sheet.cell(1, 1).value != "日期":
        sheet.insert_row(["日期", "時間", "項目", "金額", "類別", "類型", "備註"], 1)
    sheet.append_row([
        record["date"],
        record["time"],
        record["name"],
        record["amount"],
        record["category"],
        record["type"],
        record["note"],
    ])

# ===== 查詢本月統計 =====
def get_monthly_summary():
    sheet = get_sheet()
    records = sheet.get_all_records()
    now = datetime.now()
    month_str = now.strftime("%Y/%m")
    
    income = 0
    expense = 0
    category_totals = {}
    
    for row in records:
        if str(row.get("日期", "")).startswith(month_str):
            amount = float(row.get("金額", 0))
            rtype = row.get("類型", "")
            category = row.get("類別", "其他")
            if rtype == "收入":
                income += amount
            else:
                expense += amount
                category_totals[category] = category_totals.get(category, 0) + amount
    
    # 排序類別
    sorted_cats = sorted(category_totals.items(), key=lambda x: x[1], reverse=True)
    cat_lines = "\n".join([f"  {cat}：${int(amt)}" for cat, amt in sorted_cats[:5]])
    
    return (
        f"📊 {now.strftime('%Y年%m月')} 統計\n"
        f"━━━━━━━━━━\n"
        f"💰 收入：${int(income)}\n"
        f"💸 支出：${int(expense)}\n"
        f"💵 結餘：${int(income - expense)}\n"
        f"━━━━━━━━━━\n"
        f"📂 支出前5名：\n{cat_lines if cat_lines else '  (無資料)'}"
    )

# ===== 查詢最近10筆 =====
def get_recent_records():
    sheet = get_sheet()
    records = sheet.get_all_records()
    recent = records[-10:] if len(records) >= 10 else records
    recent = list(reversed(recent))
    
    lines = []
    for row in recent:
        icon = "💰" if row.get("類型") == "收入" else "💸"
        lines.append(f"{icon} {row.get('日期','')} {row.get('項目','')} ${int(float(row.get('金額',0)))}")
    
    return "📋 最近10筆紀錄\n━━━━━━━━━━\n" + "\n".join(lines) if lines else "尚無任何紀錄"

# ===== 幫助訊息 =====
HELP_MSG = """📖 記帳 Bot 使用說明
━━━━━━━━━━
【記帳格式】
  項目名稱 金額
  項目名稱 金額 備註

【範例】
  午餐 120
  捷運 30 上班
  薪水 30000 十月份

【查詢指令】
  月報 → 本月統計
  明細 → 最近10筆
  說明 → 顯示此說明
━━━━━━━━━━
💡 系統會自動分類，
   含「收入/薪水/獎金」關鍵字視為收入"""

# ===== Webhook 路由 =====
@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers["X-Line-Signature"]
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return "OK"

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    text = event.message.text.strip()
    reply = ""

    if text in ["說明", "help", "Help", "HELP", "使用說明"]:
        reply = HELP_MSG

    elif text in ["月報", "本月", "統計", "報告"]:
        try:
            reply = get_monthly_summary()
        except Exception as e:
            reply = f"❌ 查詢失敗：{str(e)}"

    elif text in ["明細", "記錄", "最近"]:
        try:
            reply = get_recent_records()
        except Exception as e:
            reply = f"❌ 查詢失敗：{str(e)}"

    else:
        record = parse_message(text)
        if record:
            try:
                add_record(record)
                icon = "💰" if record["type"] == "收入" else "💸"
                reply = (
                    f"{icon} 記帳成功！\n"
                    f"━━━━━━━━━━\n"
                    f"項目：{record['name']}\n"
                    f"金額：${int(record['amount'])}\n"
                    f"類別：{record['category']}\n"
                    f"類型：{record['type']}\n"
                    f"時間：{record['date']} {record['time']}"
                    + (f"\n備註：{record['note']}" if record['note'] else "")
                )
            except Exception as e:
                reply = f"❌ 記帳失敗：{str(e)}\n請確認 Google Sheets 設定是否正確"
        else:
            reply = (
                "❓ 看不懂這個格式\n\n"
                "請用：項目名稱 金額\n"
                "例如：午餐 120\n\n"
                "傳「說明」查看完整使用方式"
            )

    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=reply)
    )

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
