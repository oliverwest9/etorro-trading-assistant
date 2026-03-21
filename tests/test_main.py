"""Tests for agent.main — CLI argument parsing and entry point."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import ANY, MagicMock, patch

import pytest

from agent.main import _build_telegram_summary, build_parser, main


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------


class TestBuildParser:
    """Tests for the argument parser construction."""

    def test_valid_market_open(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["--run-type", "market_open"])
        assert args.run_type == "market_open"
        assert args.verbose is False
        assert args.text_logs is False

    def test_valid_market_close(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["--run-type", "market_close"])
        assert args.run_type == "market_close"

    def test_verbose_flag(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["--run-type", "market_open", "--verbose"])
        assert args.verbose is True

    def test_text_logs_flag(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["--run-type", "market_open", "--text-logs"])
        assert args.text_logs is True

    def test_send_telegram_flag(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["--run-type", "market_open", "--send-telegram"])
        assert args.send_telegram is True

    def test_cache_report_flag(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["--run-type", "market_open", "--cache-report"])
        assert args.cache_report is True

    def test_invalid_run_type_rejected(self) -> None:
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["--run-type", "invalid"])

    def test_missing_run_type_rejected(self) -> None:
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args([])


# ---------------------------------------------------------------------------
# main() function
# ---------------------------------------------------------------------------


class TestMain:
    """Tests for the main() entry point."""

    @patch("agent.main.configure_logging")
    @patch("agent.main.get_settings")
    @patch("agent.main.Orchestrator")
    @patch("agent.main.format_terminal")
    @patch("agent.main.format_markdown", return_value="# Report")
    @patch("agent.main._maybe_send_telegram_summary")
    @patch("agent.main.cache_report")
    def test_successful_run_returns_zero(
        self,
        mock_cache_report: MagicMock,
        mock_send_telegram: MagicMock,
        mock_format_md: MagicMock,
        mock_format_term: MagicMock,
        mock_orch_cls: MagicMock,
        mock_settings: MagicMock,
        mock_logging: MagicMock,
        tmp_path: object,
    ) -> None:
        """A successful pipeline run returns exit code 0."""
        mock_report = MagicMock()
        mock_orch = MagicMock()
        mock_orch.run_data_pipeline.return_value = {
            "report": mock_report,
            "duration_ms": 1234,
        }
        mock_orch.__enter__ = MagicMock(return_value=mock_orch)
        mock_orch.__exit__ = MagicMock(return_value=False)
        mock_orch_cls.return_value = mock_orch

        with patch("agent.main.Path") as mock_path_cls:
            mock_dir = MagicMock()
            mock_path_cls.return_value = mock_dir
            mock_dir.__truediv__ = MagicMock(return_value=MagicMock())

            exit_code = main(["--run-type", "market_open"])

        assert exit_code == 0
        mock_orch.run_data_pipeline.assert_called_once_with("market_open")
        mock_format_term.assert_called_once_with(
            mock_report,
            verbose=False,
            currency_symbol=ANY,
        )
        mock_cache_report.assert_not_called()
        mock_send_telegram.assert_called_once()

    @patch("agent.main.configure_logging")
    @patch("agent.main.get_settings")
    @patch("agent.main.Orchestrator")
    def test_pipeline_error_returns_one(
        self,
        mock_orch_cls: MagicMock,
        mock_settings: MagicMock,
        mock_logging: MagicMock,
    ) -> None:
        """A PipelineError causes exit code 1."""
        from agent.orchestrator import PipelineError

        mock_orch = MagicMock()
        mock_orch.run_data_pipeline.side_effect = PipelineError("API down")
        mock_orch.__enter__ = MagicMock(return_value=mock_orch)
        mock_orch.__exit__ = MagicMock(return_value=False)
        mock_orch_cls.return_value = mock_orch

        exit_code = main(["--run-type", "market_open"])
        assert exit_code == 1

    @patch("agent.main.configure_logging")
    @patch("agent.main.get_settings")
    @patch("agent.main.Orchestrator")
    def test_unexpected_error_returns_one(
        self,
        mock_orch_cls: MagicMock,
        mock_settings: MagicMock,
        mock_logging: MagicMock,
    ) -> None:
        """An unexpected exception causes exit code 1."""
        mock_orch = MagicMock()
        mock_orch.run_data_pipeline.side_effect = RuntimeError("DB gone")
        mock_orch.__enter__ = MagicMock(return_value=mock_orch)
        mock_orch.__exit__ = MagicMock(return_value=False)
        mock_orch_cls.return_value = mock_orch

        exit_code = main(["--run-type", "market_open"])
        assert exit_code == 1

    @patch("agent.main.configure_logging")
    @patch("agent.main.get_settings")
    @patch("agent.main.Orchestrator")
    @patch("agent.main.format_terminal")
    @patch("agent.main.format_markdown", return_value="# Report")
    @patch("agent.main._maybe_send_telegram_summary")
    @patch("agent.main.cache_report")
    def test_verbose_passed_through(
        self,
        mock_cache_report: MagicMock,
        mock_send_telegram: MagicMock,
        mock_format_md: MagicMock,
        mock_format_term: MagicMock,
        mock_orch_cls: MagicMock,
        mock_settings: MagicMock,
        mock_logging: MagicMock,
    ) -> None:
        """The --verbose flag is forwarded to formatters."""
        mock_report = MagicMock()
        mock_orch = MagicMock()
        mock_orch.run_data_pipeline.return_value = {
            "report": mock_report,
            "duration_ms": 500,
        }
        mock_orch.__enter__ = MagicMock(return_value=mock_orch)
        mock_orch.__exit__ = MagicMock(return_value=False)
        mock_orch_cls.return_value = mock_orch

        with patch("agent.main.Path") as mock_path_cls:
            mock_dir = MagicMock()
            mock_path_cls.return_value = mock_dir
            mock_dir.__truediv__ = MagicMock(return_value=MagicMock())

            main(["--run-type", "market_close", "--verbose"])

        mock_format_term.assert_called_once_with(
            mock_report,
            verbose=True,
            currency_symbol=ANY,
        )
        mock_format_md.assert_called_once_with(
            mock_report,
            verbose=True,
            currency_symbol=ANY,
        )
        mock_cache_report.assert_not_called()
        mock_send_telegram.assert_called_once()

    @patch("agent.main.configure_logging")
    @patch("agent.main.get_settings")
    @patch("agent.main.Orchestrator")
    @patch("agent.main.format_terminal")
    @patch("agent.main.format_markdown", return_value="# Report")
    @patch("agent.main._maybe_send_telegram_summary")
    @patch("agent.main.cache_report", return_value="reports/cache/run-123.json")
    def test_cache_report_flag_writes_cache(
        self,
        mock_cache_report: MagicMock,
        mock_send_telegram: MagicMock,
        mock_format_md: MagicMock,
        mock_format_term: MagicMock,
        mock_orch_cls: MagicMock,
        mock_settings: MagicMock,
        mock_logging: MagicMock,
    ) -> None:
        mock_report = MagicMock()
        mock_orch = MagicMock()
        mock_orch.run_data_pipeline.return_value = {
            "report": mock_report,
            "duration_ms": 500,
        }
        mock_orch.__enter__ = MagicMock(return_value=mock_orch)
        mock_orch.__exit__ = MagicMock(return_value=False)
        mock_orch_cls.return_value = mock_orch

        with patch("agent.main.Path") as mock_path_cls:
            mock_dir = MagicMock()
            mock_path_cls.return_value = mock_dir
            mock_dir.__truediv__ = MagicMock(return_value=MagicMock())

            exit_code = main(["--run-type", "market_open", "--cache-report"])

        assert exit_code == 0
        mock_cache_report.assert_called_once_with(mock_report)
        mock_send_telegram.assert_called_once()

    @patch("agent.main.configure_logging")
    @patch("agent.main.get_settings")
    @patch("agent.main.Orchestrator")
    @patch("agent.main.format_terminal")
    @patch("agent.main.format_markdown", return_value="# Report")
    @patch("agent.main.TelegramClient")
    def test_send_telegram_calls_client_when_enabled_and_configured(
        self,
        mock_telegram_cls: MagicMock,
        mock_format_md: MagicMock,
        mock_format_term: MagicMock,
        mock_orch_cls: MagicMock,
        mock_settings: MagicMock,
        mock_logging: MagicMock,
    ) -> None:
        mock_report = MagicMock()
        mock_report.run_type = "market_open"
        mock_report.snapshot.open_positions = 2
        mock_report.commentary = MagicMock()
        mock_report.commentary.recommendations = []

        mock_orch = MagicMock()
        mock_orch.run_data_pipeline.return_value = {
            "report": mock_report,
            "duration_ms": 500,
            "instruments_processed": 2,
            "analyses_created": 2,
            "instruments_failed": 0,
        }
        mock_orch.__enter__ = MagicMock(return_value=mock_orch)
        mock_orch.__exit__ = MagicMock(return_value=False)
        mock_orch_cls.return_value = mock_orch

        settings_obj = MagicMock()
        settings_obj.telegram_bot_token = "bot-token"
        settings_obj.telegram_chat_id = "1234"
        mock_settings.return_value = settings_obj

        telegram_client = MagicMock()
        telegram_client.__enter__ = MagicMock(return_value=telegram_client)
        telegram_client.__exit__ = MagicMock(return_value=False)
        mock_telegram_cls.return_value = telegram_client

        with patch("agent.main.Path") as mock_path_cls:
            mock_dir = MagicMock()
            mock_path_cls.return_value = mock_dir
            mock_dir.__truediv__ = MagicMock(return_value=MagicMock())

            exit_code = main(["--run-type", "market_open", "--send-telegram"])

        assert exit_code == 0
        mock_telegram_cls.assert_called_once_with("bot-token")
        telegram_client.send_message.assert_called_once()

    @patch("agent.main.configure_logging")
    @patch("agent.main.get_settings")
    @patch("agent.main.Orchestrator")
    @patch("agent.main.format_terminal")
    @patch("agent.main.format_markdown", return_value="# Report")
    @patch("agent.main.TelegramClient")
    def test_send_telegram_missing_credentials_is_non_fatal(
        self,
        mock_telegram_cls: MagicMock,
        mock_format_md: MagicMock,
        mock_format_term: MagicMock,
        mock_orch_cls: MagicMock,
        mock_settings: MagicMock,
        mock_logging: MagicMock,
    ) -> None:
        mock_report = MagicMock()
        mock_report.run_type = "market_open"
        mock_report.snapshot.open_positions = 1
        mock_report.commentary = None

        mock_orch = MagicMock()
        mock_orch.run_data_pipeline.return_value = {
            "report": mock_report,
            "duration_ms": 500,
            "instruments_processed": 1,
            "analyses_created": 1,
            "instruments_failed": 0,
        }
        mock_orch.__enter__ = MagicMock(return_value=mock_orch)
        mock_orch.__exit__ = MagicMock(return_value=False)
        mock_orch_cls.return_value = mock_orch

        settings_obj = MagicMock()
        settings_obj.telegram_bot_token = ""
        settings_obj.telegram_chat_id = ""
        mock_settings.return_value = settings_obj

        with patch("agent.main.Path") as mock_path_cls:
            mock_dir = MagicMock()
            mock_path_cls.return_value = mock_dir
            mock_dir.__truediv__ = MagicMock(return_value=MagicMock())

            exit_code = main(["--run-type", "market_open", "--send-telegram"])

        assert exit_code == 0
        mock_telegram_cls.assert_not_called()

    @patch("agent.main.configure_logging")
    @patch("agent.main.get_settings")
    @patch("agent.main.Orchestrator")
    @patch("agent.main.format_terminal")
    @patch("agent.main.format_markdown", return_value="# Report")
    @patch("agent.main.TelegramClient")
    def test_send_telegram_error_is_non_fatal(
        self,
        mock_telegram_cls: MagicMock,
        mock_format_md: MagicMock,
        mock_format_term: MagicMock,
        mock_orch_cls: MagicMock,
        mock_settings: MagicMock,
        mock_logging: MagicMock,
    ) -> None:
        mock_report = MagicMock()
        mock_report.run_type = "market_open"
        mock_report.snapshot.open_positions = 2
        mock_report.commentary = None

        mock_orch = MagicMock()
        mock_orch.run_data_pipeline.return_value = {
            "report": mock_report,
            "duration_ms": 500,
            "instruments_processed": 2,
            "analyses_created": 2,
            "instruments_failed": 0,
        }
        mock_orch.__enter__ = MagicMock(return_value=mock_orch)
        mock_orch.__exit__ = MagicMock(return_value=False)
        mock_orch_cls.return_value = mock_orch

        settings_obj = MagicMock()
        settings_obj.telegram_bot_token = "bot-token"
        settings_obj.telegram_chat_id = "1234"
        mock_settings.return_value = settings_obj

        telegram_client = MagicMock()
        telegram_client.__enter__ = MagicMock(return_value=telegram_client)
        telegram_client.__exit__ = MagicMock(return_value=False)
        telegram_client.send_message.side_effect = RuntimeError("telegram down")
        mock_telegram_cls.return_value = telegram_client

        with patch("agent.main.Path") as mock_path_cls:
            mock_dir = MagicMock()
            mock_path_cls.return_value = mock_dir
            mock_dir.__truediv__ = MagicMock(return_value=MagicMock())

            exit_code = main(["--run-type", "market_open", "--send-telegram"])

        assert exit_code == 0

    def test_invalid_run_type_exits(self) -> None:
        """An invalid --run-type causes argparse to exit."""
        with pytest.raises(SystemExit) as exc_info:
            main(["--run-type", "invalid_value"])
        assert exc_info.value.code == 2  # argparse error exit code


class TestTelegramSummaryBuilder:
    """Tests for conversational Telegram summary formatting."""

    def test_summary_has_conversational_portfolio_overview_and_action_footer(self) -> None:
        from datetime import datetime, timezone

        report = SimpleNamespace(
            run_type="market_open",
            generated_at=datetime(2026, 3, 19, 5, 30, 0, tzinfo=timezone.utc),
            snapshot=SimpleNamespace(
                open_positions=3,
                total_value=1234.56,
                cash_available=123.45,
                total_pnl=-12.34,
            ),
            commentary=SimpleNamespace(
                summary="Mixed market signals overall.",
                market_context="Tech showing strength while energy sector struggles. Your portfolio has balanced exposure.",
                recommendations=[
                    SimpleNamespace(symbol="BTC", action="sell", conviction="High"),
                    SimpleNamespace(symbol="AAPL", action="hold", conviction="Medium"),
                    SimpleNamespace(symbol="TSLA", action="reduce", conviction="Low"),
                    SimpleNamespace(symbol="MSFT", action="INCREASE", conviction="Medium"),
                ]
            ),
        )

        message = _build_telegram_summary(report, currency_symbol="£")

        assert "📊 Portfolio Snapshot" in message
        assert "Market Open" in message
        assert "⏰ 2026-03-19 05:30 UTC" in message
        assert "£1,234.56" in message
        assert "🌍 Market Overview" in message
        assert "Mixed market signals overall." in message
        assert "💼 Portfolio Impact" in message
        assert "Tech showing strength" in message
        assert "📈 Recommended Actions" in message
        assert "- SELL: 1" in message
        assert "- REDUCE: 1" in message
        assert "- INCREASE: 1" in message
        assert "- HOLD:" not in message  # HOLD actions are filtered out
        assert "🎯 Top Actions" in message
        assert "- BTC: SELL (High)" in message
        assert "- MSFT: INCREASE (Medium)" in message
        # HOLD actions filtered out from both sections
        assert "- LAR: HOLD" not in message
        assert "- TSLA: HOLD" not in message
        # Verify run summary is NOT present
        assert "Run summary:" not in message

    def test_summary_handles_missing_commentary(self) -> None:
        from datetime import datetime, timezone

        report = SimpleNamespace(
            run_type="market_close",
            generated_at=datetime(2026, 3, 19, 16, 30, 0, tzinfo=timezone.utc),
            snapshot=SimpleNamespace(
                open_positions=1,
                total_value=50.0,
                cash_available=10.0,
                total_pnl=0.0,
            ),
            commentary=None,
        )

        message = _build_telegram_summary(report, currency_symbol="£")

        assert "📊 Portfolio Snapshot" in message
        assert "Market Close" in message
        assert "⏰ 2026-03-19 16:30 UTC" in message
        assert "£50.00" in message
        assert "No recommendations were generated" in message
        assert "🎯 Top Actions" in message
        assert "- No actionable recommendations" in message
