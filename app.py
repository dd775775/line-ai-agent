import logging
import os

from flask import Flask, request, abort

from google import genai
from google.genai import types

from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    ApiClient,
    Configuration,
    MessagingApi,
    ReplyMessageRequest,
    TextMessage,
)
from linebot.v3.webhooks import (
    MessageEvent,
    TextMessageContent,
)


app = Flask(__name__)
logging.basicConfig(level=logging.INFO)


# =========================================================
# 環境變數
# =========================================================

LINE_CHANNEL_ACCESS_TOKEN = os.environ.get(
    "LINE_CHANNEL_ACCESS_TOKEN"
)
LINE_CHANNEL_SECRET = os.environ.get(
    "LINE_CHANNEL_SECRET"
)
GEMINI_API_KEY = os.environ.get(
    "GEMINI_API_KEY"
)

# 可在部署平台設定 GEMINI_MODEL
GEMINI_MODEL = os.environ.get(
    "GEMINI_MODEL",
    "gemini-3.5-flash",
)


def validate_environment():
    """啟動時檢查必要環境變數。"""

    required_variables = {
        "LINE_CHANNEL_ACCESS_TOKEN": LINE_CHANNEL_ACCESS_TOKEN,
        "LINE_CHANNEL_SECRET": LINE_CHANNEL_SECRET,
        "GEMINI_API_KEY": GEMINI_API_KEY,
    }

    missing_variables = [
        name
        for name, value in required_variables.items()
        if not value
    ]

    if missing_variables:
        raise RuntimeError(
            "缺少必要環境變數："
            + ", ".join(missing_variables)
        )


validate_environment()


# =========================================================
# LINE 初始化
# =========================================================

line_configuration = Configuration(
    access_token=LINE_CHANNEL_ACCESS_TOKEN
)

handler = WebhookHandler(
    LINE_CHANNEL_SECRET
)


# =========================================================
# Gemini 初始化
# =========================================================

gemini_client = genai.Client(
    api_key=GEMINI_API_KEY,
    http_options=types.HttpOptions(
        # 單位為毫秒，避免 Gemini 長時間無回應
        timeout=20000
    ),
)


SYSTEM_INSTRUCTION = """
你是大誠保險經紀人沈昊康經理的AI服務助理。

你的語氣必須專業、親切、有溫度，並使用繁體中文回答。

【主要任務】

一、現有客戶
1. 親切回應客戶問題。
2. 說明常見保險觀念，例如醫療險、實支實付、
   意外險、旅平險及車險處理流程。
3. 涉及個別保單條款、理賠認定或投保資格時，
   提醒客戶仍需由沈昊康經理確認。

二、轉介紹或新客戶
1. 感謝客戶主動諮詢。
2. 禮貌詢問客戶的稱呼。
3. 詢問介紹人的姓名。
4. 詢問主要想了解的保障需求，例如醫療、意外、
   癌症、重大傷病、退休規劃或家庭保障。

【重要限制】

1. 不得直接提供確定保費報價。
2. 不得保證承保、保證理賠或保證投資報酬。
3. 不得自行判定客戶一定符合投保資格。
4. 不得要求客戶提供身分證字號、銀行帳號、
   信用卡資料或完整病歷等敏感資訊。
5. 遇到重大疾病、理賠爭議、稅務或法律問題時，
   應說明需進一步確認資料。
6. 回答盡量控制在500字以內，適合LINE閱讀。
7. 回答結尾可自然告知：
   「沈昊康經理會再親自與您聯繫確認。」
"""


FALLBACK_MESSAGE = (
    "您好，您的訊息已經收到！\n\n"
    "目前系統暫時無法完整回覆，"
    "沈昊康經理會儘速親自與您聯繫確認，謝謝您。"
)


def generate_ai_reply(user_text: str) -> str:
    """呼叫 Gemini 產生回覆。"""

    response = gemini_client.models.generate_content(
        model=GEMINI_MODEL,
        contents=user_text,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_INSTRUCTION,
            temperature=0.4,
            max_output_tokens=800,
        ),
    )

    reply_text = (response.text or "").strip()

    if not reply_text:
        return FALLBACK_MESSAGE

    # LINE 一般文字訊息上限為 5000 字元
    return reply_text[:5000]


# =========================================================
# 網站狀態檢查
# =========================================================

@app.route("/", methods=["GET"])
def health_check():
    return {
        "status": "ok",
        "service": "LINE Insurance AI Assistant",
        "model": GEMINI_MODEL,
    }, 200


# =========================================================
# LINE Webhook
# =========================================================

@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers.get("X-Line-Signature")

    if not signature:
        app.logger.warning(
            "收到沒有 X-Line-Signature 的請求"
        )
        abort(400)

    body = request.get_data(as_text=True)

    try:
        handler.handle(body, signature)

    except InvalidSignatureError:
        app.logger.warning(
            "LINE Webhook 簽章驗證失敗"
        )
        abort(400)

    except Exception:
        app.logger.exception(
            "處理 LINE Webhook 時發生錯誤"
        )
        abort(500)

    return "OK", 200


@handler.add(
    MessageEvent,
    message=TextMessageContent,
)
def handle_message(event):
    user_text = (event.message.text or "").strip()

    if not user_text:
        return

    try:
        reply_text = generate_ai_reply(user_text)

    except Exception:
        # 詳細錯誤只寫入伺服器紀錄
        app.logger.exception(
            "Gemini API 呼叫失敗"
        )
        reply_text = FALLBACK_MESSAGE

    try:
        with ApiClient(line_configuration) as api_client:
            messaging_api = MessagingApi(api_client)

            messaging_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[
                        TextMessage(text=reply_text)
                    ],
                )
            )

    except Exception:
        app.logger.exception(
            "LINE 訊息回傳失敗"
        )


if __name__ == "__main__":
    port = int(
        os.environ.get("PORT", "5000")
    )

    app.run(
        host="0.0.0.0",
        port=port,
        debug=False,
    )
