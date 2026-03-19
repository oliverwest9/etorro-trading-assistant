"""Tests for agent.main — CLI argument parsing and entry point."""

from __future__ import annotations

from unittest.mock import ANY, MagicMock, patch

import pytest

from agent.main import build_parser, main


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
    def test_successful_run_returns_zero(
        self,
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
        mock_format_term.assert_called_once_with(mock_report, verbose=False, currency_symbol=ANY)

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
    def test_verbose_passed_through(
        self,
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

        mock_format_term.assert_called_once_with(mock_report, verbose=True, currency_symbol=ANY)
        mock_format_md.assert_called_once_with(mock_report, verbose=True, currency_symbol=ANY)

    def test_invalid_run_type_exits(self) -> None:
        """An invalid --run-type causes argparse to exit."""
        with pytest.raises(SystemExit) as exc_info:
            main(["--run-type", "invalid_value"])
        assert exc_info.value.code == 2  # argparse error exit code
