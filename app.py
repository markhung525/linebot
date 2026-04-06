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
# 這裡會自動從 Render 的 Environment Variables 抓取你的金鑰
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get('LINE_CHANNEL_ACCESS_TOKEN', '在這裡填入你的_LINE_TOKEN')
LINE_CHANNEL_SECRET = os.environ.get('LINE_CHANNEL_SECRET', '在這裡填入你的_LINE_SECRET')

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# 你的「2026收支表」專屬 ID
SHEET_ID = '1zMpiq3L55D4YjKyoRJVvIz_2nQPKkchM6lhLg6JOOdU'

# ==========================================
# 2. Google Sheets 連線模組
# ==========================================
def get_gsheet():
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    
    # 讀取 Google 服務帳號的憑證 (假設你有上傳 credentials.json 到 GitHub)
    try:
        creds = ServiceAccountCredentials.from_json_keyfile_name('credentials.json', scope)
    except Exception:
        # 如果是把 JSON 內容貼在 Render 環境變數 (GOOGLE_CREDENTIALS) 裡則用這個：
        creds_dict = json.loads(os.environ.get('GOOGLE_CREDENTIALS', '{}'))
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        
    client = gspread.authorize(creds)
    return client.open_by_key(SHEET_ID)

# ==========================================
# 3. LINE Webhook 接收模組 (請勿更動)
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
# 4. 機器人「大腦」處理邏輯 (左右分欄寫入)
# ==========================================
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    msg = event.message.text.strip()
    
    # 預留未來的指令
    if msg in ['月報', '明細']:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"👨‍💻 老闆，【{msg}】功能還在開發中喔！"))
        return

    try:
        # --- 步驟 A：智慧解析你說的話 ---
        parts = msg.split()
        record_type = "支出" # 預設是支出
        amount = 0
        category = "未分類"
        note = ""
        
        # 判斷是收入還是支出
        if "收入" in parts[0]:
            record_type = "收入"
            parts.pop(0)
        elif "支出" in parts[0]:
            record_type = "支出"
            parts.pop(0)
            
        # 尋找金額 (自動去除 $ 符號)
        for p in parts:
            num_str = re.sub(r'[^\d]', '', p) 
            if num_str.isdigit():
                amount = int(num_str)
                parts.remove(p)
                break
                
        # 剩下的字就當作類別跟備註
        if len(parts) > 0:
            category = parts[0]
        if len(parts) > 1:
            note = " ".join(parts[1:])
            
        if amount == 0:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 記帳格式錯囉！請輸入像這樣：\n午餐 120\n收入 5000 薪水"))
            return

        # --- 步驟 B：連線到 Google Sheet ---
        sheet = get_gsheet()
        # 自動抓現在是幾月，去尋找對應的標籤 (例如 "4月")
        current_month_str = f"{datetime.now().month}月"
        
        try:
            worksheet = sheet.worksheet(current_month_str)
        except gspread.exceptions.WorksheetNotFound:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"❌ 找不到叫做「{current_month_str}」的工作表標籤！請檢查 Google 表格。"))
            return

        date_str = datetime.now().strftime("%Y/%m/%d")

        # --- 步驟 C：判斷要寫在左邊還是右邊 ---
        if record_type == "收入":
            # 抓取左邊 C 欄 (第3欄) 有資料的最後一行
            col_c = worksheet.col_values(3)
            # 過濾掉空白行來計算真實列數
            next_row = len([x for x in col_c if x.strip()]) + 1
            if next_row < 3: next_row = 3 # 保留前兩行給標題
            
            # 寫入 C ~ F 欄
            worksheet.update(range_name=f"C{next_row}:F{next_row}", values=[[date_str, amount, category, note]])
            
        elif record_type == "支出":
            # 抓取右邊 G 欄 (第7欄) 有資料的最後一行
            col_g = worksheet.col_values(7)
            next_row = len([x for x in col_g if x.strip()]) + 1
            if next_row < 3: next_row = 3 
            
            # 寫入 G ~ J 欄
            worksheet.update(range_name=f"G{next_row}:J{next_row}", values=[[date_str, amount, category, note]])
            
        # --- 步驟 D：回傳成功通知給你 ---
        reply_text = f"✅ 已記錄{record_type}！\n項目：{category}\n金額：${amount}"
        if note:
            reply_text += f"\n備註：{note}"
            
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))

    except Exception as e:
        # 🔥 最貼心的防護：如果機器人當機，它會直接在 LINE 跟你說為什麼！
        error_msg = f"❌ 系統發生錯誤，寫入失敗：\n{str(e)}"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=error_msg))

if __name__ == "__main__":
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
