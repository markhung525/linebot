import os
import json
import re
from datetime import datetime
import io
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, ImageMessage, TextSendMessage
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import google.generativeai as genai
from PIL import Image

app = Flask(__name__)

# ==========================================
# 1. 密碼與金鑰設定區
# ==========================================
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get('LINE_CHANNEL_ACCESS_TOKEN', '')
LINE_CHANNEL_SECRET = os.environ.get('LINE_CHANNEL_SECRET', '')
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY', '')

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel('gemini-2.5-flash')

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
# 3. 網頁喚醒與 LINE Webhook 接收模組
# ==========================================
@app.route("/", methods=['GET'])
def index():
    return "I'm alive!"

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
# 4. 共用寫入邏輯模組
# ==========================================
def write_to_sheet(sheet, record_date, amount, category, note, record_type):
    current_year = datetime.strptime(record_date, "%Y/%m/%d").year
    month_str = f"{datetime.strptime(record_date, '%Y/%m/%d').month}月"
    
    try:
        worksheet = sheet.worksheet(month_str)
    except gspread.exceptions.WorksheetNotFound:
        return f"❌ 找不到「{month_str}」標籤頁，無法記錄 {record_date[5:]} 的帳。"

    if record_type == "收入":
        col_d = worksheet.col_values(4)
        next_row = len([x for x in col_d if x.strip()]) + 1
        if next_row < 3: next_row = 3 
        worksheet.update(range_name=f"D{next_row}:G{next_row}", values=[[record_date, amount, category, note]])
    else:
        col_i = worksheet.col_values(9)
        next_row = len([x for x in col_i if x.strip()]) + 1
        if next_row < 3: next_row = 3 
        worksheet.update(range_name=f"I{next_row}:L{next_row}", values=[[record_date, amount, category, note]])
        
    return f"✅ {record_date[5:]} {record_type} ${amount}"

# ==========================================
# 5. 文字訊息處理邏輯
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
        lines = msg.split('\n')
        reply_messages = [] 
        
        for line in lines:
            line = line.strip()
            if not line: continue
                
            record_date = datetime.now().strftime("%Y/%m/%d")
            
            date_match = re.match(r'^(\d{1,2})[/-](\d{1,2})\s*', line)
            if date_match:
                month = int(date_match.group(1))
                day = int(date_match.group(2))
                record_date = f"{current_year}/{month:02d}/{day:02d}"
                line = line[date_match.end():].strip()

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
            
            if len(parts) > 0: category = parts[0]
            if len(parts) > 1: note = " ".join(parts[1:])
                
            meal_keywords = ['早餐', '午餐', '晚餐', '早午餐', '宵夜', '便當']
            if category in meal_keywords:
                note = category + (" " + note if note else "") 
                category = "餐費"

            result_msg = write_to_sheet(sheet, record_date, amount, category, note, record_type)
            reply_messages.append(f"{result_msg} [{category}]")

        final_reply = "\n".join(reply_messages)
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=final_reply))

    except Exception as e:
        error_msg = f"❌ 系統發生錯誤：\n{str(e)}"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=error_msg))

# ==========================================
# 6. 圖片訊息處理邏輯 (Gemini 2.5 視覺多點解析)
# ==========================================
@handler.add(MessageEvent, message=ImageMessage)
def handle_image(event):
    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text="🕵️‍♂️ 收到截圖！啟動視覺引擎批次解析中，請稍候...")
    )
    
    try:
        message_content = line_bot_api.get_message_content(event.message.id)
        image_bytes = b''
        for chunk in message_content.iter_content():
            image_bytes += chunk
            
        img = Image.open(io.BytesIO(image_bytes))

        current_date = datetime.now().strftime("%Y/%m/%d")
        # 🌟 核心修改：要求 AI 回傳 JSON Array (多筆資料的清單)
        prompt = f"""
        這是一張包含「單筆」或「多筆」消費/收入紀錄的截圖（例如發票、LINE Pay 轉帳紀錄、明細表）。
        今天的日期是：{current_date}。

        請幫我擷取圖片中的「每一筆」紀錄，判斷是收入(如+NT$)還是支出(如-NT$)，並嚴格按照以下 JSON Array 格式回傳（不要包含任何 markdown 語法或其他文字）：
        [
            {{
                "record_type": "收入", // "收入" 或 "支出"
                "date": "YYYY/MM/DD", // 從圖片擷取日期，若無則填寫今日日期
                "amount": 100,        // 提取該筆金額，必須是純數字 (整數)
                "category": "未分類", // 推斷類別 (如：餐費、轉帳、交通)
                "note": "備註內容"    // 擷取對象或品項 (如：林*伶 或 飲料)
            }}
        ]
        """

        response = model.generate_content([prompt, img])
        
        json_str = response.text.replace('```json', '').replace('```', '').strip()
        records = json.loads(json_str)

        # 確保資料是清單格式
        if not isinstance(records, list):
            records = [records]

        sheet = get_gsheet()
        reply_messages = []

        # 🌟 核心修改：將 AI 辨識出的每一筆資料，排隊寫入 Google Sheet
        for data in records:
            record_type = data.get('record_type', '支出')
            record_date = data.get('date', current_date)
            amount = int(data.get('amount', 0))
            category = data.get('category', '未分類')
            note = data.get('note', '')

            if amount > 0:
                result_msg = write_to_sheet(sheet, record_date, amount, category, note, record_type)
                reply_messages.append(f"{result_msg} [{category}] ({note})")

        if not reply_messages:
            line_bot_api.push_message(event.source.user_id, TextSendMessage(text="❌ 無法從圖片中辨識出任何有效金額。"))
            return
            
        final_reply = "🤖 視覺解析完成：\n" + "\n".join(reply_messages)
        line_bot_api.push_message(event.source.user_id, TextSendMessage(text=final_reply))

    except Exception as e:
        error_msg = f"❌ 視覺模組發生錯誤：\n{str(e)}"
        line_bot_api.push_message(event.source.user_id, TextSendMessage(text=error_msg))

if __name__ == "__main__":
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
