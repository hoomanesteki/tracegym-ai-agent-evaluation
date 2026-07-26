"""SPC catches sustained drift, ignores transients, and never fabricates one."""

from __future__ import annotations

from tracegym.spc import change_point, cusum, drift_check, ewma


def test_ewma_tracks_a_step_and_is_bounded_by_the_data():
    z = ewma([0, 0, 0, 1, 1, 1], lam=0.5)
    assert z[0] == 0.0
    assert 0.0 < z[-1] < 1.0
    assert z[-1] > z[3]  # climbs toward the new level


def test_ewma_of_empty_is_empty_not_an_error():
    assert len(ewma([])) == 0


def test_cusum_accumulates_only_in_the_shifted_direction():
    hi, lo = cusum([0, 0, 0, 2, 2, 2], target=0.0, k=0.5)
    assert lo[-1] == 0.0  # nothing pushed below target
    assert hi[-1] > hi[0]  # upward shift accumulates


def test_change_point_locates_the_shift():
    assert change_point([1, 1, 1, 1, 9, 9, 9, 9]) == 3


def test_too_few_samples_is_insufficient_not_drift():
    r = drift_check([1, 1, 1], "up", min_samples=8)
    assert r["status"] == "insufficient"
    assert r["drift"] is False


def test_constant_series_is_flat_never_drift():
    r = drift_check([1.0] * 10, "up")
    assert r["status"] == "flat"
    assert r["drift"] is False


def test_sustained_downward_slide_is_ongoing_drift():
    r = drift_check([1.0, 0.99, 0.98, 0.95, 0.9, 0.86, 0.82, 0.78, 0.74, 0.70], "up")
    assert r["status"] == "drift"
    assert r["drift"] is True
    assert r["change_point"] is not None


def test_recovered_transient_is_flagged_but_not_ongoing():
    # A two-run dip that returns to baseline: an excursion happened, but the
    # process is back in control, so it is context, not an actionable drift.
    r = drift_check([1, 1, 1, 0.83, 0.83, 1, 1, 1], "up")
    assert r["status"] == "recovered"
    assert r["drift"] is False
    assert r["excursion"] is True
    assert r["change_point"] == 4


def test_cost_creeping_up_is_drift_for_a_down_metric():
    r = drift_check([0.1, 0.1, 0.1, 0.11, 0.12, 0.14, 0.16, 0.19, 0.22, 0.26], "down")
    assert r["status"] == "drift"
    assert r["drift"] is True


def test_a_single_run_blip_does_not_trip_the_chart():
    r = drift_check([1, 1, 1, 1, 1, 0.99, 1, 1], "up")
    assert r["status"] == "stable"
    assert r["drift"] is False


def test_a_favorable_monotone_trend_never_fabricates_an_excursion():
    # An up-metric climbing steadily, or a cost falling steadily, is good news. The
    # CUSUM must center on the baseline, not the whole-series median, so the low
    # starting level is not misread as an adverse excursion.
    up = drift_check([0.70, 0.74, 0.78, 0.82, 0.86, 0.90, 0.95, 0.98, 0.99, 1.0], "up")
    assert up["status"] == "stable"
    assert up["excursion"] is False
    down = drift_check([1.4, 1.3, 1.2, 1.1, 1.0, 0.9, 0.8, 0.7], "down")  # cost falling
    assert down["status"] == "stable"
    assert down["excursion"] is False
