"""app.py 命令行解析的回归测试（argparse help 里出现 % 会直接崩，必须守住）。"""

from __future__ import annotations

import app


def test_parser_builds_help():
    parser = app.build_parser()
    text = parser.format_help()
    assert "--config" in text
    assert "--exit-after" in text


def test_parser_parses_defaults_and_overrides():
    parser = app.build_parser()
    args, rest = parser.parse_known_args(["--config", "x.json", "--shift-minutes", "-29"])
    assert args.config == "x.json"
    assert args.shift_minutes == -29
    assert args.exit_after is None
    assert rest == []


def test_parser_ignores_qt_specific_arguments():
    parser = app.build_parser()
    args, rest = parser.parse_known_args(["-platform", "windows", "--exit-after", "3"])
    assert args.exit_after == 3.0
    assert rest == ["-platform", "windows"]
