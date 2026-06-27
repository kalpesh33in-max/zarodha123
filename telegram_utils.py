import requests
from env_config import (
    TELE_TOKEN,
    TELE_CHAT_ID,
    TELE_TOKEN_BN,
    TELE_CHAT_ID_BN,
    TELE_TOKEN_VELOCITY,
    TELE_CHAT_ID_VELOCITY,
)


def _is_valid_chat_id(chat_id):
    return bool(chat_id and chat_id != "YOUR_CHAT_ID")


def _resolve_telegram_target(chat_id=None, token=None, is_burst=False):
    if token or chat_id:
        target_token = token or TELE_TOKEN or TELE_TOKEN_BN or TELE_TOKEN_VELOCITY
        target_id = chat_id or next(
            (
                candidate
                for candidate in (
                    TELE_CHAT_ID_BN if is_burst else TELE_CHAT_ID,
                    TELE_CHAT_ID_VELOCITY,
                )
                if _is_valid_chat_id(candidate)
            ),
            None,
        )
        return target_token, target_id

    if is_burst and _is_valid_chat_id(TELE_CHAT_ID_BN) and TELE_TOKEN_BN:
        return TELE_TOKEN_BN, TELE_CHAT_ID_BN

    for target_token, target_id in (
        (TELE_TOKEN, TELE_CHAT_ID),
        (TELE_TOKEN_VELOCITY, TELE_CHAT_ID_VELOCITY),
    ):
        if target_token and _is_valid_chat_id(target_id):
            return target_token, target_id

    return None, None

def send_telegram_message(message, chat_id=None, token=None, is_burst=False):

    target_token, target_id = _resolve_telegram_target(
        chat_id=chat_id,
        token=token,
        is_burst=is_burst,
    )
    if not target_token:
        print("Telegram token missing!")
        return

    if not _is_valid_chat_id(target_id):
        print(f"Target Chat ID missing: {target_id}")
        return

    url = f"https://api.telegram.org/bot{target_token}/sendMessage"
    payload = {
        "chat_id": target_id,
        "text": message
    }

    try:
        response = requests.post(url, json=payload)
        return response.json()
    except Exception as e:
        print(f"Error sending Telegram message: {e}")
        return None
