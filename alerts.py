import os
import requests
import logging

logger = logging.getLogger("alerts")

def send_alert(title, message, status="info", details=None):
    """
    Sends alerts to Discord and/or Telegram based on environment configuration.
    
    :param title: The title of the alert.
    :param message: The descriptive message of the alert.
    :param status: Severity status: "info", "warning", "critical", or "success".
    :param details: A dictionary of key-value details to include in the notification.
    """
    discord_url = os.getenv("DISCORD_WEBHOOK_URL")
    telegram_token = os.getenv("TELEGRAM_BOT_TOKEN")
    telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID")
    
    # 1. Discord Webhook Embed Integration
    if discord_url:
        colors = {
            "info": 0x3498db,      # Blue
            "warning": 0xf1c40f,   # Yellow
            "critical": 0xe74c3c,  # Red
            "success": 0x2ecc71    # Green
        }
        color = colors.get(status.lower(), 0x3498db)
        
        embed = {
            "title": title,
            "description": message,
            "color": color,
            "fields": []
        }
        
        if details:
            for k, v in details.items():
                embed["fields"].append({
                    "name": str(k),
                    "value": str(v),
                    "inline": True
                })
                
        payload = {
            "embeds": [embed]
        }
        
        try:
            res = requests.post(discord_url, json=payload, timeout=10)
            res.raise_for_status()
            logger.info("Discord alert sent successfully.")
        except Exception as e:
            logger.error(f"Failed to send Discord alert: {e}")
            
    # 2. Telegram Bot API Integration
    if telegram_token and telegram_chat_id:
        emoji = {
            "info": "ℹ️",
            "warning": "⚠️",
            "critical": "🚨",
            "success": "✅"
        }.get(status.lower(), "ℹ️")
        
        tg_text = f"{emoji} *{title}*\n\n{message}"
        if details:
            tg_text += "\n\n*Détails :*"
            for k, v in details.items():
                tg_text += f"\n- *{k}* : {v}"
                
        url = f"https://api.telegram.org/bot{telegram_token}/sendMessage"
        payload = {
            "chat_id": telegram_chat_id,
            "text": tg_text,
            "parse_mode": "Markdown"
        }
        
        try:
            res = requests.post(url, json=payload, timeout=10)
            res.raise_for_status()
            logger.info("Telegram alert sent successfully.")
        except Exception as e:
            logger.error(f"Failed to send Telegram alert: {e}")
