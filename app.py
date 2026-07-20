import os
import json
from flask import Flask, request, abort
import google.generativeai as genai
import gspread
from oauth2client.service_account import ServiceAccountCredentials

from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    Configuration, ApiClient, MessagingApi, ReplyMessageRequest, TextMessage
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent, FollowEvent

app = Flask(__name__)

# 1. 環境變數讀取
LINE_CHANNEL_SECRET = os.getenv('LINE_CHANNEL_SECRET')
LINE_CHANNEL_ACCESS_TOKEN = os.getenv('LINE_CHANNEL_ACCESS_TOKEN')
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
GOOGLE_SHEET_ID = os.getenv('GOOGLE_SHEET_ID')

# LINE SDK 初始化
configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# Gemini AI 初始化
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel('gemini-1.5-flash')

# Google Sheet 初始化 (讀取 JSON 服務帳號金鑰)
def get_google_sheet():
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds_json = os.getenv('GOOGLE_CREDENTIALS_JSON')
    if creds_json:
        creds_dict = json.loads(creds_json)
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        client = gspread.authorize(creds)
        return client.open_by_key(GOOGLE_SHEET_ID)
    return None

# 記憶暫存 (簡單短期記憶)
user_sessions = {}

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers.get('X-Line-Signature')
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'

# 🌟 1. 新用戶加入 (FollowEvent) 分流邏輯
@handler.add(FollowEvent)
def handle_follow(event):
    user_id = event.source.user_id
    sheet = get_google_sheet()
    
    is_old_customer = False
    if sheet:
        try:
            wks = sheet.worksheet("客戶名單與體況")
            records = wks.get_all_records()
            for r in records:
                if str(r.get('LINE_User_ID')) == str(user_id):
                    is_old_customer = True
                    break
        except Exception as e:
            print("Sheet Error:", e)

    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        if is_old_customer:
            welcome_msg = "歡迎回來！我是您的數位保險特助。\n您可以輸入『查詢保單』查看摘要，或點選選單尋求專屬服務！"
        else:
            welcome_msg = (
                "您好！我是大誠保險經紀人沈顧問的 AI 數位特助 🤖\n\n"
                "歡迎加為好友！請問您這次是由哪位朋友介紹過來的呢？\n"
                "或是想先了解哪一類型的保障規劃？（例如：醫療險、儲蓄理財、車險續保）"
            )
        line_bot_api.reply_message(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[TextMessage(text=welcome_msg)]
            )
        )

# 🌟 2. 訊息處理與 AI 對話機制
@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    user_id = event.source.user_id
    user_msg = event.message.text.strip()
    
    # 建立短期記憶 Session
    if user_id not in user_sessions:
        user_sessions[user_id] = []
    user_sessions[user_id].append(f"客戶: {user_msg}")
    
    # 限制僅保留最近 6 條歷史紀錄
    if len(user_sessions[user_id]) > 6:
        user_sessions[user_id] = user_sessions[user_id][-6:]
        
    history_context = "\n".join(user_sessions[user_id])
    sheet = get_google_sheet()

    # 特殊觸發：查詢保單 (B方案隱私過濾)
    if "查詢保單" in user_msg or "保單存摺" in user_msg:
        reply_text = get_policy_summary(sheet, user_id)
    else:
        # Prompt 角色設定與對話導引
        prompt = f"""
你是一位專業、有溫度且值得信賴的大誠保險經紀人 AI 助手（服務顧問為沈經理）。
請根據與客戶的對話紀錄親切回答。如果客戶提及體況，請引導其確認 A~D 體況分級。
說話請保持簡潔、專利且富有禮貌。

對話歷史：
{history_context}

請生成適當的回覆：
"""
        response = model.generate_content(prompt)
        reply_text = response.text.strip()

    user_sessions[user_id].append(f"AI: {reply_text}")

    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        line_bot_api.reply_message(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[TextMessage(text=reply_text)]
            )
        )

# B 方案隱私保單過濾邏輯
def get_policy_summary(sheet, user_id):
    if not sheet:
        return "資料庫連線中，請稍後再試。"
    try:
        wks = sheet.worksheet("保單存摺 (B方案)")
        records = wks.get_all_records()
        user_policies = []
        for r in records:
            if str(r.get('LINE_User_ID')) == str(user_id):
                # 判斷隱私開關
                if str(r.get('隱私隱藏')).strip() != '是':
                    user_policies.append(f"• {r.get('險種名稱')} ({r.get('投保公司')}): 狀態-{r.get('保單狀態/到期日')}")
        
        if user_policies:
            return "【您的保障摘要紀錄】\n" + "\n".join(user_policies) + "\n\n註：部分隱私保護設定之保單不在此處列出。"
        else:
            return "查無您公開的保單資料，若需查詢私房/儲蓄型保單，請聯繫沈顧問本人專人協助。"
    except Exception as e:
        return "保單資料庫處理異常，請聯繫顧問。"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
