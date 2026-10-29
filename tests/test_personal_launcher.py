from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_one_click_launcher_pins_local_address_and_python_312() -> None:
    script = (ROOT / "scripts" / "start_personal_app.ps1").read_text(encoding="utf-8")

    assert '"http://127.0.0.1:8512/"' in script
    assert "$port = 8512" in script
    assert '"3.12"' in script
    assert "8513" not in script
    assert "STOCKLENS_DEFAULT_TICKER = \"NVDA\"" in script


def test_one_click_launcher_prevents_duplicate_and_does_not_kill_processes() -> None:
    script = (ROOT / "scripts" / "start_personal_app.ps1").read_text(encoding="utf-8")

    assert "Get-NetTCPConnection" in script
    assert "중복 실행하지 않습니다" in script
    assert "다른 프로그램" in script
    assert "Stop-Process" not in script
    assert "taskkill" not in script.lower()


def test_cmd_launcher_keeps_error_window_open() -> None:
    command = (ROOT / "Start StockLens 200.cmd").read_text(encoding="utf-8")

    assert "start_personal_app.ps1" in command
    assert "pause" in command.lower()
