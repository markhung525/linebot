import os
import json
import re
from datetime import datetime
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
import gspread
from oauth2client.service_account import ServiceAccountCredentials

app = Flask(__name__)

# ==========================================
# 1. 密碼與金鑰設定區
# ==========================================
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get('LINE_CHANNEL_ACCESS_TOKEN', '')
LINE_CHANNEL_SECRET = os.environ.get('LINE_CHANNEL_SECRET', '')

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

SHEET_ID = '1zMpiq3L55D4YjKyoRJVvIz_2nQPKkchM6lhLg6JOOdU'

# ==========================================
# 2. Google Sheets 連線模組
# ==========================================
def get_gsheet():
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    try:
        creds = ServiceAccountCredentials.from_json_keyfile_name('credentials.json', scope)
    except Exception:
        creds_dict = json.loads(os.environ.get('GOOGLE_CREDENTIALS', '{}'))
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        
    client = gspread.authorize(creds)
    return client.open_by_key(SHEET_ID)

# ==========================================
# 3. LINE Webhook 接收模組
# ==========================================
@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'

# ==========================================
# 4. 機器人「大腦」處理邏輯 (智慧解析+自動分類)
# ==========================================
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    msg = event.message.text.strip()
    
    if msg in ['月報', '明細']:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"👨‍💻 老闆，【{msg}】功能還在開發中喔！"))
        return

    try:
        # --- 步驟 A：智慧解析你說的話 (全新進化版) ---
        # 1. 判斷收支 (只要有提到"收入"就是收入，否則預設為支出)
        record_type = "收入" if "收入" in msg else "支出"
        
        # 2. 抓出金額 (自動把文字裡的數字挑出來)
        amount_match = re.search(r'\d+', msg)
        if not amount_match:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 找不到金額耶！請輸入像這樣：\n午餐 120"))
            return
        amount = int(amount_match.group())
        
        # 3. 抓出類別與備註 (把數字、收入、支出等字眼清掉，剩下的就是類別)
        clean_text = re.sub(r'\d+|收入|支出|\$|元', '', msg).strip()
        parts = clean_text.split()
        
        category = "未分類"
        note = ""
        
        if len(parts) > 0:
            category = parts[0]
        if len(parts) > 1:
            note = " ".join(parts[1:])
            
        # 🌟 4. 關鍵字自動分類小秘書
        meal_keywords = ['早餐', '午餐', '晚餐', '早午餐', '宵夜', '便當']
        if category in meal_keywords:
            # 把原本的「午餐」字眼保留到備註去，主類別改為「餐費」
            note = category + (" " + note if note else "") 
            category = "餐費"

        # --- 步驟 B：連線到 Google Sheet ---
        sheet = get_gsheet()
        current_month_str = f"{datetime.now().month}月"
        
        try:
            worksheet = sheet.worksheet(current_month_str)
        except gspread.exceptions.WorksheetNotFound:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"❌ 找不到叫做「{current_month_str}」的工作表標籤！請檢查 Google 表格。"))
            return

        date_str = datetime.now().strftime("%Y/%m/%d")

        # --- 步驟 C：精準判斷並寫入對應的欄位 ---
        if record_type == "收入":
            col_d = worksheet.col_values(4)
            next_row = len([x for x in col_d if x.strip()]) + 1
            if next_row < 3: next_row = 3 
            worksheet.update(range_name=f"D{next_row}:G{next_row}", values=[[date_str, amount, category, note]])
            
        elif record_type == "支出":
            col_i = worksheet.col_values(9)
            next_row = len([x for x in col_i if x.strip()]) + 1
            if next_row < 3: next_row = 3 
            worksheet.update(range_name=f"I{next_row}:L{next_row}", values=[[date_str, amount, category, note]])
            
        # --- 步驟 D：回傳成功通知給你 ---
        reply_text = f"✅ 已記錄{record_type}！\n項目：{category}\n金額：${amount}"
        if note:
            reply_text += f"\n備註：{note}"
            
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))

    except Exception as e:
        error_msg = f"❌ 系統發生錯誤，寫入失敗：\n{str(e)}"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=error_msg))

if __name__ == "__main__":
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
