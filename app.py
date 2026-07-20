import os
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
import google.generativeai as genai

app = Flask(__name__)

# 從環境變數取得金鑰
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# 初始化 Google Gemini API
genai.configure(api_key=GEMINI_API_KEY)

# 系統指令人設 (保險經紀人特助)
SYSTEM_INSTRUCTION = """
你是一位專業、親切且有溫度的保險經紀人AI助理。你的任務是代表保險經紀人顧問在第一時間回應客戶。

【主要任務】
1. 對現有客戶：提供親切招呼，解答常見保險觀念（如醫療險、實支實付、車險流程）。
2. 對轉介紹的新客人：展現熱情，感謝對方諮詢，並禮貌詢問稱呼、介紹人及主要想了解的保障需求。
3. 絕不做出具體保費報價或保證承保承諾，並告知顧問會盡快親自聯繫。
"""

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers.get('X-Line-Signature')
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_text = event.message.text
    reply_text = ""
    
    # 嘗試多個可用模型名稱（避免單一模型 404）
    candidate_models = ['gemini-pro', 'models/gemini-pro', 'gemini-1.5-pro']
    
    for model_name in candidate_models:
        try:
            model = genai.GenerativeModel(
                model_name=model_name,
                system_instruction=SYSTEM_INSTRUCTION
            )
            response = model.generate_content(user_text)
            reply_text = response.text
            break # 成功呼叫就跳出迴圈
        except Exception as e:
            print(f"模型 {model_name} 嘗試失敗: {e}")
            last_error = str(e)

    # 如果所有模型都失敗
    if not reply_text:
        reply_text = f"您好！訊息已收到，保險顧問會儘速親自回覆您！（系統提示: {last_error[:60]}）"

    # 回傳給 LINE 使用者
    try:
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=reply_text)
        )
    except Exception as line_e:
        print(f"LINE 回傳訊息失敗: {line_e}")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
