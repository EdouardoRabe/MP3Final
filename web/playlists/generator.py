"""
Playlist generation — Priority-first algorithm.

Logic:
  Phase 1 — Priority tracks (matching user filters) are always included first.
  Phase 2 — If a target duration is set and time remains after phase 1,
             fill the gap with fallback tracks via knapsack DP.

Cases:
  - Filters + no duration  → return all priority tracks (no DP).
  - Filters + duration     → all priority tracks first, then fill remaining time.
  - Filters + duration, but priority tracks exceed target
                           → knapsack DP within priority tracks only.
  - No filters + duration  → knapsack DP on all tracks (classic behaviour).
  - No filters + no duration → return all tracks (handled by the view).
"""
import logging

logger = logging.getLogger(__name__)


def generate_playlist(
    priority_queryset,
    fallback_queryset=None,
    target_seconds=None,
    max_tracks: int = 200,
) -> dict:
    """
    Generate a playlist that respects user preferences before duration.

    Args:
        priority_queryset: Tracks matching the user's filters (always included first).
        fallback_queryset: Other tracks used only to fill remaining time.
        target_seconds:    Target total duration in seconds, or None for no limit.
        max_tracks:        Hard cap on total tracks considered.

    Returns dict with keys:
        track_ids      — ordered list of UUIDs (priority tracks first)
        total_duration — float seconds
        algorithm      — str description
        relaxation     — bool (True if overage was allowed)
    """
    priority_tracks = list(
        priority_queryset
        .values('id', 'duration')
        .order_by('artist', 'title')
        [:max_tracks]
    )

    if not priority_tracks:
        return {
            'track_ids': [],
            'total_duration': 0.0,
            'algorithm': 'priority_first',
            'relaxation': False,
        }

    # ── No duration target ────────────────────────────────────────────────
    if target_seconds is None:
        total = round(sum(float(t['duration'] or 0) for t in priority_tracks), 2)
        return {
            'track_ids': [t['id'] for t in priority_tracks],
            'total_duration': total,
            'algorithm': 'priority_only',
            'relaxation': False,
        }

    # ── Duration target set ───────────────────────────────────────────────
    precision = 10  # work in deciseconds (×10) for integer DP
    target_scaled = int(target_seconds * precision)

    priority_total = sum(float(t['duration'] or 0) for t in priority_tracks)
    priority_scaled = int(priority_total * precision)

    if priority_scaled <= target_scaled:
        # All priority tracks fit → include them all, then fill remaining time
        selected_ids = [t['id'] for t in priority_tracks]
        remaining_scaled = target_scaled - priority_scaled
        total_duration = round(priority_total, 2)

        if remaining_scaled >= precision * 10 and fallback_queryset is not None:
            max_filler = max(0, max_tracks - len(priority_tracks))
            if max_filler > 0:
                fallback_tracks = list(
                    fallback_queryset
                    .values('id', 'duration')
                    .order_by('-duration')
                    [:max_filler]
                )
                if fallback_tracks:
                    durations_scaled = [
                        max(1, int(float(t['duration'] or 0) * precision))
                        for t in fallback_tracks
                    ]
                    filler = _knapsack_dp(fallback_tracks, durations_scaled, remaining_scaled)
                    selected_ids = selected_ids + filler['best_ids']
                    total_duration = round(priority_total + filler['best_sum'] / precision, 2)

        return {
            'track_ids': selected_ids,
            'total_duration': total_duration,
            'algorithm': 'priority_first',
            'relaxation': False,
        }

    # Priority tracks exceed the target → DP within priority tracks only
    logger.info(
        "Priorité dépasse la cible (%.0fs > %.0fs) — DP sur morceaux prioritaires",
        priority_total, target_seconds,
    )
    durations_scaled = [
        max(1, int(float(t['duration'] or 0) * precision))
        for t in priority_tracks
    ]
    result = _knapsack_dp(priority_tracks, durations_scaled, target_scaled)

    relaxation = False
    if result['best_sum'] < target_scaled * 0.6:
        relaxed = _knapsack_dp(priority_tracks, durations_scaled, int(target_scaled * 1.2))
        if relaxed['best_sum'] > result['best_sum']:
            result = relaxed
            relaxation = True

    return {
        'track_ids': result['best_ids'],
        'total_duration': round(result['best_sum'] / precision, 2),
        'algorithm': 'priority_knapsack',
        'relaxation': relaxation,
    }


def _knapsack_dp(tracks: list, durations: list, target: int) -> dict:
    """
    0/1 Knapsack DP: maximize total duration without exceeding target.
    Complexity: O(n × target).
    """
    n = len(tracks)
    dp = [0] * (target + 1)
    chosen = [[] for _ in range(target + 1)]

    for i in range(n):
        dur = durations[i]
        if dur > target:
            continue
        track_id = tracks[i]['id']
        for d in range(target, dur - 1, -1):
            candidate = dp[d - dur] + dur
            if candidate > dp[d]:
                dp[d] = candidate
                chosen[d] = chosen[d - dur] + [track_id]

    best_sum = max(dp)
    best_ids = chosen[dp.index(best_sum)]
    return {'best_sum': best_sum, 'best_ids': best_ids}
