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

# 設定 Gemini AI
genai.configure(api_key=GEMINI_API_KEY)
# 使用推薦的 gemini-1.5-flash 模型處理多模態任務
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
# 4. 共用寫入邏輯模組 (收斂重複程式碼)
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
        
    return f"✅ {record_date[5:]} {category} ${amount}"

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

            # 呼叫共用寫入模組
            result_msg = write_to_sheet(sheet, record_date, amount, category, note, record_type)
            reply_messages.append(result_msg)

        final_reply = "\n".join(reply_messages)
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=final_reply))

    except Exception as e:
        error_msg = f"❌ 系統發生錯誤：\n{str(e)}"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=error_msg))

# ==========================================
# 6. 圖片訊息處理邏輯 (Gemini 視覺引擎)
# ==========================================
@handler.add(MessageEvent, message=ImageMessage)
def handle_image(event):
    # 讓機器人先回覆「處理中」，避免你覺得它當機了
    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text="🕵️‍♂️ 收到截圖！啟動視覺引擎解析中，請稍候...")
    )
    
    try:
        # 1. 把圖片從 LINE 抓下來
        message_content = line_bot_api.get_message_content(event.message.id)
        image_bytes = b''
        for chunk in message_content.iter_content():
            image_bytes += chunk
            
        # 轉換為 Gemini 看得懂的格式
        img = Image.open(io.BytesIO(image_bytes))

        # 2. 建構結構化的 Prompt 指令
        current_date = datetime.now().strftime("%Y/%m/%d")
        prompt = f"""
        這是一張收據、發票或消費截圖。請幫我從中提取記帳所需的資訊。
        今天的日期是：{current_date}。

        請嚴格按照以下 JSON 格式回傳，不要包含任何其他多餘的文字：
        {{
            "record_type": "支出",  // 判斷是收入還是支出，通常是支出
            "date": "YYYY/MM/DD", // 盡量從圖片中尋找消費日期，若無則填寫今日日期
            "amount": 100,        // 提取總金額，必須是純數字 (整數)
            "category": "餐費",     // 根據消費內容推斷最適合的類別 (例如：餐費、交通、雜費、治裝、日用品)
            "note": "便利商店飲料"    // 擷取消費品項或店家名稱作為簡短備註
        }}
        """

        # 3. 呼叫 Gemini 解析圖片
        response = model.generate_content([prompt, img])
        
        # 4. 清理並解析回傳的 JSON 資料
        # 移除可能被 Gemini 包覆的 markdown 語法 (例如 ```json ... ```)
        json_str = response.text.replace('```json', '').replace('```', '').strip()
        data = json.loads(json_str)

        record_type = data.get('record_type', '支出')
        record_date = data.get('date', current_date)
        amount = int(data.get('amount', 0))
        category = data.get('category', '未分類')
        note = data.get('note', '')

        if amount == 0:
            # 如果 AI 找不到金額，透過 Push API 推播錯誤訊息
            line_bot_api.push_message(event.source.user_id, TextSendMessage(text="❌ 視覺引擎無法從圖片中辨識出金額，請確認圖片清晰度或改用手動輸入。"))
            return

        # 5. 連線並寫入 Sheet
        sheet = get_gsheet()
        result_msg = write_to_sheet(sheet, record_date, amount, category, note, record_type)
        
        # 6. 推播成功訊息
        final_reply = f"🤖 視覺解析完成：\n{result_msg}\n(備註：{note})"
        line_bot_api.push_message(event.source.user_id, TextSendMessage(text=final_reply))

    except Exception as e:
        error_msg = f"❌ 視覺模組發生錯誤：\n{str(e)}"
        line_bot_api.push_message(event.source.user_id, TextSendMessage(text=error_msg))

if __name__ == "__main__":
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
