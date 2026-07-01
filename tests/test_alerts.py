import pytest
from unittest.mock import patch, MagicMock
import os

from alerts import send_alert


class TestDiscordAlerts:
    """Tests pour l'intégration Discord webhook."""

    @patch("alerts.requests.post")
    def test_discord_alert_sends_correct_embed(self, mock_post):
        mock_post.return_value = MagicMock(status_code=200)
        mock_post.return_value.raise_for_status = MagicMock()

        with patch.dict(os.environ, {"DISCORD_WEBHOOK_URL": "https://discord.com/api/webhooks/test/token"}):
            send_alert(
                title="Test Alert",
                message="Something happened",
                status="warning",
                details={"Key1": "Value1", "Key2": "Value2"}
            )

        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args
        payload = call_kwargs[1]["json"] if "json" in call_kwargs[1] else call_kwargs.kwargs["json"]
        
        embed = payload["embeds"][0]
        assert embed["title"] == "Test Alert"
        assert embed["description"] == "Something happened"
        assert embed["color"] == 0xf1c40f  # Yellow for warning
        assert len(embed["fields"]) == 2
        assert embed["fields"][0]["name"] == "Key1"
        assert embed["fields"][0]["value"] == "Value1"

    @patch("alerts.requests.post")
    def test_discord_alert_color_mapping(self, mock_post):
        mock_post.return_value = MagicMock(status_code=200)
        mock_post.return_value.raise_for_status = MagicMock()

        color_map = {
            "info": 0x3498db,
            "warning": 0xf1c40f,
            "critical": 0xe74c3c,
            "success": 0x2ecc71,
        }
        
        for status, expected_color in color_map.items():
            with patch.dict(os.environ, {"DISCORD_WEBHOOK_URL": "https://discord.com/api/webhooks/test/token"}):
                send_alert(title="Test", message="msg", status=status)
            
            payload = mock_post.call_args[1]["json"]
            assert payload["embeds"][0]["color"] == expected_color, f"Wrong color for status '{status}'"
            mock_post.reset_mock()

    @patch("alerts.requests.post")
    def test_discord_alert_handles_network_error(self, mock_post):
        mock_post.side_effect = Exception("Network timeout")

        with patch.dict(os.environ, {"DISCORD_WEBHOOK_URL": "https://discord.com/api/webhooks/test/token"}):
            # Should not raise - errors are caught and logged
            send_alert(title="Test", message="msg", status="critical")

    @patch("alerts.requests.post")
    def test_discord_alert_without_details(self, mock_post):
        mock_post.return_value = MagicMock(status_code=200)
        mock_post.return_value.raise_for_status = MagicMock()

        with patch.dict(os.environ, {"DISCORD_WEBHOOK_URL": "https://discord.com/api/webhooks/test/token"}):
            send_alert(title="Simple Alert", message="No details")

        payload = mock_post.call_args[1]["json"]
        assert payload["embeds"][0]["fields"] == []


class TestTelegramAlerts:
    """Tests pour l'intégration Telegram Bot API."""

    @patch("alerts.requests.post")
    def test_telegram_alert_sends_correct_message(self, mock_post):
        mock_post.return_value = MagicMock(status_code=200)
        mock_post.return_value.raise_for_status = MagicMock()

        with patch.dict(os.environ, {
            "TELEGRAM_BOT_TOKEN": "bot12345:token",
            "TELEGRAM_CHAT_ID": "chat999",
            "DISCORD_WEBHOOK_URL": "",
        }):
            send_alert(
                title="Telegram Test",
                message="Alert body",
                status="success",
                details={"Vault": "0xABC"}
            )

        mock_post.assert_called_once()
        call_args = mock_post.call_args
        
        assert "bot12345:token" in call_args[0][0] or "bot12345:token" in str(call_args)
        payload = call_args[1]["json"]
        assert payload["chat_id"] == "chat999"
        assert "✅" in payload["text"]
        assert "*Telegram Test*" in payload["text"]
        assert "Alert body" in payload["text"]
        assert "*Vault*" in payload["text"]

    @patch("alerts.requests.post")
    def test_telegram_alert_handles_network_error(self, mock_post):
        mock_post.side_effect = Exception("Connection refused")

        with patch.dict(os.environ, {
            "TELEGRAM_BOT_TOKEN": "bot12345:token",
            "TELEGRAM_CHAT_ID": "chat999",
            "DISCORD_WEBHOOK_URL": "",
        }):
            # Should not raise
            send_alert(title="Test", message="msg", status="critical")


class TestNoConfigAlerts:
    """Tests pour le comportement quand aucun canal n'est configuré."""

    @patch("alerts.requests.post")
    def test_no_alerts_sent_without_config(self, mock_post):
        with patch.dict(os.environ, {
            "DISCORD_WEBHOOK_URL": "",
            "TELEGRAM_BOT_TOKEN": "",
            "TELEGRAM_CHAT_ID": "",
        }, clear=False):
            send_alert(title="Silent", message="No one listening")

        mock_post.assert_not_called()
