"""
Algorithm for optimal playlist generation (KnapSack DP variant).

Given a set of filtered tracks and a target duration, finds the subset
whose total duration is as close as possible to the target (without exceeding it,
though slight overage is tolerated as a fallback).
"""
import logging
from typing import Optional

from tracks.models import Track

logger = logging.getLogger(__name__)


def generate_playlist(
    queryset,
    target_seconds: int,
    max_tracks: int = 200,
) -> dict:
    """
    Generate an optimal playlist using dynamic programming.

    Args:
        queryset: Filtered Track queryset.
        target_seconds: Desired total duration in seconds.
        max_tracks: Max number of tracks to consider (performance safeguard).

    Returns:
        dict with keys:
            - track_ids: list of UUIDs in optimal order
            - total_duration: sum of durations (float)
            - algorithm: str describing the method used
            - relaxation: bool indicating if overage was allowed
    """
    tracks = list(
        queryset.values('id', 'duration')
        .order_by('-duration')[:max_tracks]
    )
    n = len(tracks)

    if n == 0:
        return {
            'track_ids': [],
            'total_duration': 0.0,
            'algorithm': 'dp_knapsack',
            'relaxation': False,
        }

    # Convert durations to integer seconds (DP works with ints)
    # Multiply by 10 for decisecond precision, then divide back later
    precision = 10
    target_scaled = target_seconds * precision
    durations_scaled = [max(1, int(t['duration'] * precision)) for t in tracks]

    # ---- Pass 1: Strict (no overage) ----
    result = _knapsack_dp(tracks, durations_scaled, target_scaled)

    best_duration_scaled = result['best_sum']
    best_ids = result['best_ids']

    # If result is too far from target (> 40% gap), allow slight overage
    relaxation = False
    if best_duration_scaled < target_scaled * 0.6:
        logger.info(
            "Relaxation activee : resultat strict = %.1fs pour cible %ds",
            best_duration_scaled / precision,
            target_seconds,
        )
        relaxed_target = int(target_scaled * 1.2)
        result = _knapsack_dp(tracks, durations_scaled, relaxed_target)
        best_duration_scaled = result['best_sum']
        best_ids = result['best_ids']
        relaxation = True

    total_duration = round(best_duration_scaled / precision, 2)

    return {
        'track_ids': best_ids,
        'total_duration': total_duration,
        'algorithm': 'dp_knapsack',
        'relaxation': relaxation,
    }


def _knapsack_dp(
    tracks: list,
    durations: list,
    target: int,
) -> dict:
    """
    Core KnapSack DP: maximize total duration without exceeding target.

    Complexity: O(n × target) where n = len(tracks).

    Args:
        tracks: List of {'id': UUID, 'duration': float} dicts.
        durations: Pre-scaled integer durations.
        target: Target sum (integer, scaled).

    Returns:
        {'best_sum': int, 'best_ids': list}
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

    # Find the best achievable sum
    best_sum = max(dp)
    best_idx = dp.index(best_sum)
    best_ids = chosen[best_idx]

    return {
        'best_sum': best_sum,
        'best_ids': best_ids,
    }
