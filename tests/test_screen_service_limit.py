"""回归：Web 选股 limit 只裁返回，不截断扫描池。"""

from unittest.mock import patch

from modules.screener import StockScore


def _score(ts_code: str, score: float) -> StockScore:
    return StockScore(
        ts_code=ts_code,
        name=ts_code,
        score=score,
        b1_score=score,
        trend_score=50,
        volume_score=50,
        risk_score=50,
        reasons=["mock"],
        warnings=[],
    )


def test_run_screen_limit_is_result_cap_not_scan_cap():
    from api.services.screen_service import run_screen

    fake = [
        _score("000001.SZ", 80),
        _score("000002.SZ", 70),
        _score("000003.SZ", 60),
    ]

    with patch("modules.screener.screen_stocks", return_value=fake) as mock_screen:
        out = run_screen(strategy="B1", limit=2, use_parallel=True)

    assert mock_screen.call_args.kwargs["max_stocks"] == 0
    assert out["count"] == 2
    assert out["matched"] == 3
    assert [s["ts_code"] for s in out["stocks"]] == ["000001.SZ", "000002.SZ"]
