from __future__ import annotations

import datetime as dt
import math
import random
from collections import defaultdict
from typing import Any

from aqt import mw


FUZZ_RANGES = [
    {"start": 2.5, "end": 7.0, "factor": 0.15},
    {"start": 7.0, "end": 20.0, "factor": 0.1},
    {"start": 20.0, "end": math.inf, "factor": 0.05},
]


def sched_current_date() -> dt.date:
    now = dt.datetime.now()
    try:
        rollover = float(mw.col.get_config("rollover") or 0)
    except Exception:
        rollover = 0
    return (now - dt.timedelta(hours=rollover)).date()


def get_fuzz_range(interval: int, last_interval: int, maximum_interval: int) -> tuple[int, int]:
    delta = 1.0
    for fuzz_range in FUZZ_RANGES:
        delta += fuzz_range["factor"] * max(
            min(interval, fuzz_range["end"]) - fuzz_range["start"],
            0.0,
        )
    interval = min(interval, maximum_interval)
    min_ivl = int(round(interval - delta))
    max_ivl = int(round(interval + delta))
    min_ivl = max(2, min_ivl)
    max_ivl = min(max_ivl, maximum_interval)
    if interval > last_interval:
        min_ivl = max(min_ivl, last_interval + 1)
    min_ivl = min(min_ivl, max_ivl)
    return min_ivl, max_ivl


def check_review_distribution(actual_reviews: list[int], percentages: list[float]) -> list[int]:
    easy_days_modifier = []
    percentages = [p if p != 0 else 0.0001 for p in percentages]
    possible_days_cnt = len(actual_reviews)
    if possible_days_cnt <= 1:
        return [1] * possible_days_cnt
    total_review_count = sum(actual_reviews)
    for percentage, review_count in zip(percentages, actual_reviews):
        if percentage == 1:
            easy_days_modifier.append(1)
        elif percentage == 0.0001:
            easy_days_modifier.append(0)
        else:
            other_days_count_total = total_review_count - review_count
            other_days_p_total = sum(percentages) - percentage
            if review_count / percentage >= other_days_count_total / other_days_p_total:
                easy_days_modifier.append(0)
            else:
                easy_days_modifier.append(1)
    return easy_days_modifier


def rotate_number_by_k(number: int, k: int) -> int:
    text = str(number)
    if not text:
        return 0
    k = k % len(text)
    return int(text[k:] + text[:k])


def true_due_for_card(card: Any) -> int:
    return int(card.odue if getattr(card, "odid", 0) else card.due)


def update_card_due_interval(card: Any, interval: int, last_review_day: int) -> Any:
    interval = max(1, int(round(interval)))
    card.ivl = interval
    new_due = int(last_review_day) + interval
    if getattr(card, "odid", 0):
        card.odue = new_due if new_due != 0 else 1
    else:
        card.due = new_due
    return card


def deck_maximum_interval(deck_id: int) -> int:
    try:
        config = mw.col.decks.config_dict_for_deck_id(int(deck_id))
        return int(config.get("rev", {}).get("maxIvl") or 36500)
    except Exception:
        return 36500


class FSRSRescheduleBalancer:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.today = int(mw.col.sched.today)
        self.current_date = sched_current_date()
        self.load_balancer_enabled = self._collection_load_balancer_enabled()
        self.review_count_power = float(config.get("load_balancer_review_count_power", 2.15) or 2.15)
        self.interval_power = float(config.get("load_balancer_interval_power", 3.0) or 3.0)
        self.due_cnt_per_day_per_preset: dict[int, dict[int, int]] = defaultdict(lambda: defaultdict(int))
        self.due_today_per_preset: dict[int, int] = defaultdict(int)
        self.reviewed_today_per_preset: dict[int, int] = defaultdict(int)
        self.preset_id_to_easy_days_percentages: dict[int, list[float]] = {}
        if self.load_balancer_enabled:
            self._set_load_balance()

    def _collection_load_balancer_enabled(self) -> bool:
        for name in ("_get_load_balancer_enabled", "_get_enable_load_balancer"):
            getter = getattr(mw.col, name, None)
            if getter is None:
                continue
            try:
                return bool(getter())
            except Exception:
                continue
        return False

    def _preset_id_for_deck(self, deck_id: int) -> int | None:
        try:
            config = mw.col.decks.config_dict_for_deck_id(int(deck_id))
        except Exception:
            return None
        try:
            return int(config.get("id"))
        except (TypeError, ValueError):
            return None

    def _easy_days_for_preset(self, preset_id: int) -> list[float]:
        values = list(self.preset_id_to_easy_days_percentages.get(preset_id) or [1] * 7)
        if len(values) < 7:
            values.extend([1] * (7 - len(values)))
        return values[:7]

    def _set_load_balance(self) -> None:
        true_due = "CASE WHEN odid==0 THEN due ELSE odue END"
        original_did = "CASE WHEN odid==0 THEN did ELSE odid END"
        deck_stats = mw.col.db.all(
            f"""SELECT {original_did}, {true_due}, count()
                FROM cards
                WHERE type = 2
                AND queue != -1
                GROUP BY {original_did}, {true_due}"""
        )

        for deck_id, due_date, count in deck_stats:
            preset_id = self._preset_id_for_deck(int(deck_id))
            if preset_id is None:
                continue
            self.due_cnt_per_day_per_preset[preset_id][int(due_date)] += int(count)
            try:
                deck_config = mw.col.decks.config_dict_for_deck_id(int(deck_id))
                percentages = deck_config.get("easyDaysPercentages") or []
            except Exception:
                percentages = []
            self.preset_id_to_easy_days_percentages[preset_id] = [float(p) for p in percentages] if percentages else [1] * 7

        self.due_today_per_preset = defaultdict(
            int,
            {
                preset_id: sum(count for due, count in due_counts.items() if due <= self.today)
                for preset_id, due_counts in self.due_cnt_per_day_per_preset.items()
            },
        )

        reviewed_stats = mw.col.db.all(
            f"""SELECT {original_did}, count(distinct revlog.cid)
                FROM revlog
                JOIN cards ON revlog.cid = cards.id
                WHERE revlog.ease > 0
                AND (revlog.type < 3 OR revlog.factor != 0)
                AND revlog.id/1000 >= {mw.col.sched.day_cutoff - 86400}
                GROUP BY {original_did}"""
        )
        for deck_id, count in reviewed_stats:
            preset_id = self._preset_id_for_deck(int(deck_id))
            if preset_id is not None:
                self.reviewed_today_per_preset[preset_id] += int(count)

    def update_due_counts(self, preset_id: int | None, due_before: int, due_after: int) -> None:
        if not self.load_balancer_enabled or preset_id is None:
            return
        self.due_cnt_per_day_per_preset[preset_id][int(due_before)] -= 1
        self.due_cnt_per_day_per_preset[preset_id][int(due_after)] += 1
        if due_before <= self.today < due_after:
            self.due_today_per_preset[preset_id] -= 1
        if due_before > self.today >= due_after:
            self.due_today_per_preset[preset_id] += 1

    def apply_fuzz_and_balance(
        self,
        card: Any,
        interval: int,
        *,
        last_review_day: int,
        preset_id: int | None,
        deck_id: int,
    ) -> int:
        interval = max(1, int(round(interval)))
        if interval < 2.5:
            return interval

        if not self.load_balancer_enabled:
            fuzz_delta = getattr(mw.col, "fuzz_delta", None)
            if fuzz_delta is None:
                return interval
            try:
                return max(1, int(round(interval + fuzz_delta(card.id, interval))))
            except Exception:
                return interval

        if preset_id is None:
            preset_id = self._preset_id_for_deck(deck_id)
        if preset_id is None:
            return interval

        last_review, last_interval = self._last_review_day_and_interval(card, last_review_day)
        min_ivl, max_ivl = get_fuzz_range(interval, last_interval, deck_maximum_interval(deck_id))
        if last_review + max_ivl < self.today:
            return min(interval, max_ivl)

        min_ivl = max(min_ivl, self.today - last_review)
        possible_intervals = list(range(min_ivl, max_ivl + 1))
        if not possible_intervals:
            return interval

        due_counts = self.due_cnt_per_day_per_preset[preset_id]
        due_today = self.due_today_per_preset[preset_id]
        reviewed_today = self.reviewed_today_per_preset[preset_id]
        review_cnts = []
        for possible_interval in possible_intervals:
            check_due = last_review + possible_interval
            if check_due > self.today:
                review_cnts.append(due_counts[check_due])
            else:
                review_cnts.append(due_today + reviewed_today)

        weights = [
            1.0 if reviews == 0 else (1.0 / (reviews ** self.review_count_power)) * (1.0 / (delta_t ** self.interval_power))
            for reviews, delta_t in zip(review_cnts, possible_intervals)
        ]
        possible_dates = [
            self.current_date + dt.timedelta(days=(last_review + possible_interval - self.today))
            for possible_interval in possible_intervals
        ]
        weekdays = [date.weekday() for date in possible_dates]
        easy_days = self._easy_days_for_preset(preset_id)
        modifiers = check_review_distribution(review_cnts, [easy_days[weekday] for weekday in weekdays])
        final_weights = [weight * modifier for weight, modifier in zip(weights, modifiers)]
        rng = random.Random(rotate_number_by_k(int(card.id), 8) + int(getattr(card, "reps", 0) or 0))
        if sum(final_weights) > 0:
            return self._weighted_choice(rng, possible_intervals, final_weights)
        return self._weighted_choice(rng, possible_intervals, weights)

    def _weighted_choice(self, rng: random.Random, population: list[int], weights: list[float]) -> int:
        total = sum(weights)
        if total <= 0:
            return population[0]
        pick = rng.random() * total
        cumulative = 0.0
        for item, weight in zip(population, weights):
            cumulative += weight
            if pick <= cumulative:
                return item
        return population[-1]

    def _last_review_day_and_interval(self, card: Any, fallback_last_review_day: int) -> tuple[int, int]:
        try:
            revlogs = [
                revlog for revlog in mw.col.get_review_logs(card.id)
                if int(getattr(revlog, "button_chosen", getattr(revlog, "ease", 0)) or 0) >= 1
            ]
        except Exception:
            revlogs = []
        if not revlogs:
            return int(fallback_last_review_day), int(getattr(card, "ivl", 0) or 0)

        latest = revlogs[0]
        try:
            last_review = int(math.ceil((int(latest.time) - mw.col.sched.day_cutoff) / 86_400) + mw.col.sched.today)
        except Exception:
            last_review = int(fallback_last_review_day)

        last_interval_seconds = getattr(latest, "last_interval", None)
        if last_interval_seconds is None and len(revlogs) >= 2:
            try:
                last_interval_seconds = int(revlogs[0].time) - int(revlogs[1].time)
            except Exception:
                last_interval_seconds = None
        if last_interval_seconds is None:
            return last_review, int(getattr(card, "ivl", 0) or 0)
        last_interval = int(round(float(last_interval_seconds) / 86_400))
        return last_review, max(0, last_interval)
