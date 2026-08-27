from pathlib import Path

from bazar_deals.cli import main
from bazar_deals.progress import emit


def test_progress_prints_github_notice(monkeypatch, capsys, tmp_path: Path) -> None:
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    emit("bazos: fetching")
    out = capsys.readouterr().out
    assert "bazos: fetching" in out
    assert "::notice title=hunt::bazos: fetching" in out
    assert "bazos: fetching" in summary.read_text(encoding="utf-8")


def test_fetch_only_writes_listings_and_does_not_score(tmp_path: Path, capsys) -> None:
    out = tmp_path / "bazos.json"
    assert main(["hunt", "--offline", "--source", "bazos", "--fetch-only", "--listings-out", str(out)]) == 0
    text = capsys.readouterr().out
    assert "filter:" not in text
    assert out.is_file()
    assert '"Commodore 1541-II disk drive"' in out.read_text(encoding="utf-8")


def test_listings_in_scores_cached_json(tmp_path: Path, capsys) -> None:
    cached = tmp_path / "bazos.json"
    assert main(["hunt", "--offline", "--source", "bazos", "--fetch-only", "--listings-out", str(cached)]) == 0
    capsys.readouterr()
    assert main(["hunt", "--offline", "--listings-in", str(cached)]) == 0
    text = capsys.readouterr().out
    assert "filter:" in text
    assert "No deals" in text
