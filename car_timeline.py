from __future__ import annotations

from typing import Any, Callable


TIMELINE_START_DAY = -1_000_000_000


PositionDay = Callable[[dict[str, Any]], int | None]
IsTimelineStart = Callable[[dict[str, Any]], bool]


def latest_position_car(
    cars: list[dict[str, Any]] | None,
    position_day: PositionDay,
) -> dict[str, Any] | None:
    valid = []
    for car in cars or []:
        day = position_day(car)
        if day is not None:
            valid.append((day, str(car.get("created_at", "")), str(car.get("id", "")), car))
    if not valid:
        return None
    valid.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
    return valid[0][3]


def latest_drive_car(
    cars: list[dict[str, Any]] | None,
    position_day: PositionDay,
    is_timeline_start: IsTimelineStart,
) -> dict[str, Any] | None:
    return latest_position_car(
        [car for car in cars or [] if not is_timeline_start(car)],
        position_day,
    )


def oldest_position_car(
    cars: list[dict[str, Any]] | None,
    position_day: PositionDay,
) -> dict[str, Any] | None:
    valid = []
    for car in cars or []:
        day = position_day(car)
        if day is not None:
            valid.append((day, str(car.get("created_at", "")), str(car.get("id", "")), car))
    if not valid:
        return None
    valid.sort(key=lambda item: (item[0], item[1], item[2]))
    return valid[0][3]


def swept_car_ids(
    cars: list[dict[str, Any]],
    moving_car_id: str,
    old_day: int | None,
    new_day: int | None,
    position_day: PositionDay,
) -> set[str]:
    if old_day is None:
        return set()

    swept: set[str] = set()
    for car in cars:
        car_id = str(car.get("id", ""))
        if not car_id or car_id == moving_car_id:
            continue
        other_day = position_day(car)
        if other_day is None:
            continue
        if new_day is None:
            if other_day <= old_day:
                swept.add(car_id)
        elif new_day < old_day and new_day <= other_day <= old_day:
            swept.add(car_id)
    return swept
