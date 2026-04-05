# 記帳 LINE Bot 部署教學

## 📁 檔案說明
- `app.py` — 主程式（LINE Bot 核心邏輯）
- `requirements.txt` — 需要安裝的套件清單
- `SETUP.md` — 本教學

---

## 🚀 步驟一：申請 LINE Developers 帳號

1. 前往 https://developers.line.biz/zh-hant/
2. 用 LINE 帳號登入
3. 建立 **Provider**（填你的名字即可）
4. 建立 **Messaging API Channel**
5. 記下以下兩個值（等等要用）：
   - `Channel Secret`（在 Basic settings 頁面）
   - `Channel access token`（在 Messaging API 頁面，點 Issue 產生）

---

## 📊 步驟二：設定 Google Sheets

### 建立試算表
1. 前往 https://sheets.google.com 建立新試算表
2. 記下網址中的試算表 ID：
   `https://docs.google.com/spreadsheets/d/【這裡就是ID】/edit`

### 建立 Google Service Account
1. 前往 https://console.cloud.google.com/
2. 建立新專案（名稱隨意）
3. 搜尋並啟用 **Google Sheets API** 和 **Google Drive API**
4. 前往「憑證」→「建立憑證」→「服務帳戶」
5. 建立後，點進服務帳戶 → 「金鑰」→「新增金鑰」→「JSON」
6. 下載 JSON 檔，**整個 JSON 內容**等等要貼到環境變數

### 分享試算表
1. 打開你的 Google 試算表
2. 點右上角「共用」
3. 貼上服務帳戶的 Email（格式：`xxx@xxx.iam.gserviceaccount.com`）
4. 設定為「編輯者」

---

## ☁️ 步驟三：部署到 Render.com（免費）

1. 前往 https://render.com 用 GitHub 帳號登入
2. 把這三個檔案上傳到你的 GitHub repository
3. Render 點「New」→「Web Service」→ 選你的 repository
4. 設定如下：
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `python app.py`

### 設定環境變數（Environment Variables）
在 Render 的 Environment 頁面新增以下四個變數：

| 變數名稱 | 值 |
|---|---|
| `LINE_CHANNEL_ACCESS_TOKEN` | LINE 的 Channel access token |
| `LINE_CHANNEL_SECRET` | LINE 的 Channel Secret |
| `GOOGLE_SHEET_ID` | Google 試算表的 ID |
| `GOOGLE_CREDENTIALS_JSON` | 整個 JSON 憑證檔的內容（複製貼上） |

5. 部署完成後，記下你的網址：`https://你的服務名稱.onrender.com`

---

## 🔗 步驟四：設定 LINE Webhook

1. 回到 LINE Developers Console
2. 進入你的 Messaging API Channel
3. 找到「Webhook URL」，填入：
   ```
   https://你的服務名稱.onrender.com/callback
   ```
4. 點「Verify」確認連線成功（出現 Success 表示完成）
5. 開啟「Use webhook」開關

---

## ✅ 步驟五：測試

用 LINE 掃描你的 Bot QR Code，加好友後傳：

```
說明
```

出現使用說明就代表成功了！接著試試：

```
午餐 120
捷運 30 上班
月報
```

---

## 💬 支援的指令

| 傳送內容 | 功能 |
|---|---|
| `項目 金額` | 記帳（例：午餐 120） |
| `項目 金額 備註` | 記帳含備註（例：午餐 120 麥當勞） |
| `月報` | 查看本月收支統計 |
| `明細` | 查看最近10筆記錄 |
| `說明` | 顯示使用說明 |

## 🗂️ 自動分類規則

| 關鍵字 | 分類 |
|---|---|
| 早餐、午餐、晚餐、飲料、宵夜 | 餐飲 |
| 捷運、公車、計程車、uber | 交通 |
| 購物、衣服、日用品 | 購物 |
| 電影、遊戲、娛樂 | 娛樂 |
| 藥局、醫療、健康 | 醫療 |
| 房租、水電、帳單、網路 | 帳單 |
| 薪水、獎金、收入、兼職 | 收入 |
| 其他 | 其他 |
