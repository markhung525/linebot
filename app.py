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
# 4. 機器人「大腦」處理邏輯 (支援多行與自訂日期)
# ==========================================
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    msg = event.message.text.strip()
    
    if msg in ['月報', '明細']:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"👨‍💻 老闆，【{msg}】功能還在開發中喔！"))
        return

    try:
        sheet = get_gsheet()
        current_year = datetime.now().year
        
        # 把收到的訊息用「換行」切開，變成一行一行的清單
        lines = msg.split('\n')
        reply_messages = [] # 用來收集要回傳給你的話
        
        for line in lines:
            line = line.strip()
            if not line: # 如果是空行就跳過
                continue
                
            # --- 步驟 A：找找看有沒有日期 (支援 4/5 或 04/05 格式) ---
            # 預設為今天的日期與月份
            record_date = datetime.now().strftime("%Y/%m/%d")
            month_str = f"{datetime.now().month}月"
            
            # 用正規表達式尋找開頭是不是日期
            date_match = re.match(r'^(\d{1,2})[/-](\d{1,2})\s*', line)
            if date_match:
                # 如果有找到日期，就把日期抽出來，並把該月的工作表名稱設好
                month = int(date_match.group(1))
                day = int(date_match.group(2))
                record_date = f"{current_year}/{month:02d}/{day:02d}"
                month_str = f"{month}月"
                # 把日期從這行文字裡拿掉，避免干擾後面的辨識
                line = line[date_match.end():].strip()

            # --- 步驟 B：智慧解析剩下的文字 ---
            record_type = "收入" if "收入" in line else "支出"
            
            amount_match = re.search(r'\d+', line)
            if not amount_match:
                reply_messages.append(f"❌ 「{line}」找不到金額，已跳過。")
                continue
            amount = int(amount_match.group())
            
            clean_text = re.sub(r'\d+|收入|支出|\$|元', '', line).strip()
            parts = clean_text.split()
            
            category = "未分類"
            note = ""
            
            if len(parts) > 0:
                category = parts[0]
            if len(parts) > 1:
                note = " ".join(parts[1:])
                
            meal_keywords = ['早餐', '午餐', '晚餐', '早午餐', '宵夜', '便當']
            if category in meal_keywords:
                note = category + (" " + note if note else "") 
                category = "餐費"

            # --- 步驟 C：精準連線與寫入 ---
            try:
                worksheet = sheet.worksheet(month_str)
            except gspread.exceptions.WorksheetNotFound:
                reply_messages.append(f"❌ 找不到「{month_str}」標籤頁，無法記錄 {record_date[5:]} 的帳。")
                continue

            if record_type == "收入":
                col_d = worksheet.col_values(4)
                next_row = len([x for x in col_d if x.strip()]) + 1
                if next_row < 3: next_row = 3 
                worksheet.update(range_name=f"D{next_row}:G{next_row}", values=[[record_date, amount, category, note]])
                
            elif record_type == "支出":
                col_i = worksheet.col_values(9)
                next_row = len([x for x in col_i if x.strip()]) + 1
                if next_row < 3: next_row = 3 
                worksheet.update(range_name=f"I{next_row}:L{next_row}", values=[[record_date, amount, category, note]])
                
            # --- 步驟 D：把成功的結果加進回覆清單 ---
            # 為了讓回覆簡潔，只顯示 月/日 和 類別、金額
            reply_messages.append(f"✅ {record_date[5:]} {category} ${amount}")

        # 最後一次把所有回覆訊息傳給你
        final_reply = "\n".join(reply_messages)
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=final_reply))

    except Exception as e:
        error_msg = f"❌ 系統發生錯誤：\n{str(e)}"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=error_msg))

if __name__ == "__main__":
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
