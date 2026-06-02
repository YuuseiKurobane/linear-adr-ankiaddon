from __future__ import annotations

import json
import re
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from aqt import mw
from aqt.qt import (
    QAction,
    QCheckBox,
    QDesktopServices,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QTimer,
    QUrl,
    QVBoxLayout,
    QWidget,
    qconnect,
)
from aqt.utils import showWarning


ADDON_TITLE = "Linear ADR"
MAX_DURATION_MS = 1_200_000

LEARNING = 1
REVIEW = 2
RELEARNING = 3
FILTERED = 4

DEFAULT_FIRST_RATING_PROB = [0.24, 0.094, 0.495, 0.171]
DEFAULT_REVIEW_RATING_PROB = [0.224, 0.631, 0.145]

NORMAL_DECK_HELP = (
    "A normal deck is a regular, non-filtered Anki deck. Subdecks and nested "
    "subdecks are included independently when their effective deck options "
    "preset matches a checked preset; sibling subdecks using other presets are "
    "skipped."
)


class ExportError(Exception):
    pass


def _coerce_int(value: Any) -> int:
    return int(value)


def _coerce_float(value: Any, default: float | None = None) -> float | None:
    if value in (None, ""):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _coerce_retention(value: Any) -> float | None:
    retention = _coerce_float(value)
    if retention is None:
        return None
    if retention > 1.0:
        retention /= 100.0
    return retention


def _round_list(values: list[float], places: int) -> list[float]:
    return [round(float(value), places) for value in values]


def _rating_prob(
    counts: Counter[int],
    ratings: tuple[int, ...],
    fallback: list[float],
) -> list[float]:
    total = sum(counts.get(rating, 0) for rating in ratings)
    if total <= 0:
        return list(fallback)
    return [round(counts.get(rating, 0) / total, 4) for rating in ratings]


def _normal_decks(col: Any) -> list[dict[str, Any]]:
    decks = []
    for deck in col.decks.all():
        if deck.get("dyn"):
            continue
        decks.append(deck)
    decks.sort(key=lambda deck: deck.get("name", ""))
    return decks


def _representative_deck(decks: list[dict[str, Any]]) -> dict[str, Any]:
    return decks[0]


def _deck_config(col: Any, deck: dict[str, Any]) -> dict[str, Any]:
    did = _coerce_int(deck["id"])
    config = col.decks.config_dict_for_deck_id(did)
    if not config:
        raise ExportError(f"Could not load deck options for deck id {did}.")
    return config


def _safe_path_part(text: str) -> str:
    safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", text).strip(" ._")
    return safe[:80] or "preset"


def _preset_name(config: dict[str, Any], preset_id: int) -> str:
    return str(config.get("name") or f"Preset {preset_id}")


def _preset_prefix(config: dict[str, Any], preset_id: int) -> str:
    return f"{preset_id}-{_safe_path_part(_preset_name(config, preset_id))}"


def _group_decks_by_preset(
    col: Any, decks: list[dict[str, Any]]
) -> dict[int, dict[str, Any]]:
    groups: dict[int, dict[str, Any]] = {}
    for deck in decks:
        config = _deck_config(col, deck)
        preset_id = _coerce_int(config.get("id", deck.get("conf", 0)))
        if preset_id not in groups:
            groups[preset_id] = {"config": config, "decks": []}
        groups[preset_id]["decks"].append(deck)
    return groups


def _sorted_groups(
    groups: dict[int, dict[str, Any]]
) -> list[tuple[int, dict[str, Any]]]:
    return sorted(
        groups.items(),
        key=lambda item: _preset_name(item[1]["config"], item[0]).casefold(),
    )


def _load_preset_groups() -> dict[int, dict[str, Any]]:
    if mw.col is None:
        raise ExportError("No collection is open.")
    decks = _normal_decks(mw.col)
    if not decks:
        raise ExportError("No normal decks found.")
    return _group_decks_by_preset(mw.col, decks)


def _extract_fsrs_params(config: dict[str, Any]) -> list[float]:
    for key in ("fsrsParams6", "fsrs_params_6"):
        values = config.get(key)
        if values:
            params = [float(value) for value in values]
            if len(params) == 21:
                return params
            raise ExportError(f"{key} has {len(params)} weights; FSRS-6 needs 21.")

    for key in ("fsrsParams5", "fsrs_params_5", "fsrsWeights", "fsrs_params_4"):
        values = config.get(key)
        if values:
            raise ExportError(
                f"Deck options only contain {key}. Please optimize/save FSRS-6 params first."
            )

    raise ExportError("No FSRS-6 params found in the selected preset options.")


def _nested_number(config: dict[str, Any], outer: str, inner: str) -> float | None:
    value = config.get(outer)
    if not isinstance(value, dict):
        return None
    return _coerce_float(value.get(inner))


def _deck_desired_retention(deck: dict[str, Any], config: dict[str, Any]) -> float:
    deck_retention = _coerce_retention(
        deck.get("desiredRetention", deck.get("desired_retention"))
    )
    if deck_retention is not None:
        return deck_retention
    config_retention = _coerce_retention(
        config.get("desiredRetention", config.get("desired_retention"))
    )
    return config_retention if config_retention is not None else 0.9


def _revlog_state(review_kind: int) -> int:
    if review_kind == 0:
        return LEARNING
    if review_kind == 1:
        return REVIEW
    if review_kind == 2:
        return RELEARNING
    return FILTERED


def _day_from_revlog_id(revlog_id: int) -> int:
    return int(revlog_id // 1000 // 86_400)


def _query_deck_counts(col: Any, deck_ids_sql: str) -> dict[str, int]:
    where = f"(did in ({deck_ids_sql}) or odid in ({deck_ids_sql}))"
    total = col.db.scalar(f"select count() from cards where {where}") or 0
    active = col.db.scalar(f"select count() from cards where {where} and queue >= 0") or 0
    return {"card_count": int(total), "active_card_count": int(active)}


def _query_revlog_rows(col: Any, deck_ids_sql: str) -> list[tuple[Any, ...]]:
    return col.db.all(
        f"""
        select r.cid, r.id, r.ease, r.type, r.time
        from revlog r
        join cards c on c.id = r.cid
        where (c.did in ({deck_ids_sql}) or c.odid in ({deck_ids_sql}))
          and r.ease between 1 and 4
          and r.type in (0, 1, 2, 3)
        order by r.cid, r.id
        """
    )


def _session_summary(rows: list[tuple[Any, ...]]) -> dict[str, Any]:
    first_rating_counts: Counter[int] = Counter()
    review_success_counts: Counter[int] = Counter()
    cost_sum: dict[tuple[int, int], float] = defaultdict(float)
    cost_count: Counter[tuple[int, int]] = Counter()
    unique_cards: set[int] = set()

    sessions = 0
    review_events = 0
    remembered_sessions = 0
    skipped_duration = 0

    current_key: tuple[int, int] | None = None
    current_ratings: list[int] = []
    current_state = 0
    current_duration = 0

    def finish_session() -> None:
        nonlocal sessions, remembered_sessions, review_events
        if current_key is None or not current_ratings:
            return
        sessions += 1
        review_events += len(current_ratings)
        first_rating = current_ratings[0]
        if first_rating > 1:
            remembered_sessions += 1

        if current_state == LEARNING:
            first_rating_counts[first_rating] += 1
        elif current_state == REVIEW and first_rating in (2, 3, 4):
            review_success_counts[first_rating] += 1

        if current_state in (LEARNING, REVIEW):
            key = (current_state, first_rating)
            cost_sum[key] += current_duration / 1000.0
            cost_count[key] += 1

    for cid_raw, revlog_id_raw, rating_raw, review_kind_raw, duration_raw in rows:
        cid = int(cid_raw)
        revlog_id = int(revlog_id_raw)
        rating = int(rating_raw)
        review_kind = int(review_kind_raw)
        duration = int(duration_raw or 0)

        if duration <= 0 or duration >= MAX_DURATION_MS:
            skipped_duration += 1
            continue

        unique_cards.add(cid)
        key = (cid, _day_from_revlog_id(revlog_id))
        if key != current_key:
            finish_session()
            current_key = key
            current_ratings = []
            current_state = _revlog_state(review_kind)
            current_duration = 0

        current_ratings.append(rating)
        current_duration += duration

    finish_session()

    learn_costs = []
    review_costs = []
    for rating in (1, 2, 3, 4):
        learn_key = (LEARNING, rating)
        review_key = (REVIEW, rating)
        learn_costs.append(
            round(cost_sum[learn_key] / cost_count[learn_key], 2)
            if cost_count[learn_key]
            else 0.0
        )
        review_costs.append(
            round(cost_sum[review_key] / cost_count[review_key], 2)
            if cost_count[review_key]
            else 0.0
        )

    true_retention = remembered_sessions / sessions if sessions else 0.0

    return {
        "review_count": review_events,
        "reviewed_card_count": len(unique_cards),
        "session_count": sessions,
        "skipped_duration_count": skipped_duration,
        "true_retention": round(true_retention, 3),
        "first_rating_prob": _rating_prob(
            first_rating_counts, (1, 2, 3, 4), DEFAULT_FIRST_RATING_PROB
        ),
        "review_rating_prob": _rating_prob(
            review_success_counts, (2, 3, 4), DEFAULT_REVIEW_RATING_PROB
        ),
        "learn_costs": learn_costs,
        "review_costs": review_costs,
    }


def _write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records)
    path.write_text(text, encoding="utf-8")


def _export_timestamp() -> tuple[str, str]:
    return time.strftime("%Y%m%d-%H%M%S"), time.strftime("%Y-%m-%dT%H:%M:%S%z")


def _unique_export_path(export_root: Path, timestamp: str) -> Path:
    base = export_root / f"adr-input-{timestamp}.jsonl"
    if not base.exists():
        return base
    for suffix in range(2, 1000):
        path = export_root / f"adr-input-{timestamp}-{suffix}.jsonl"
        if not path.exists():
            return path
    raise ExportError("Could not find a unique export filename.")


def _export_preset_record(
    col: Any,
    decks: list[dict[str, Any]],
    config: dict[str, Any],
    exported_at: str,
) -> dict[str, Any]:
    selected = _representative_deck(decks)
    preset_id = _coerce_int(config.get("id", selected.get("conf", 0)))
    preset_name = _preset_name(config, preset_id)
    fsrs_params = _extract_fsrs_params(config)
    deck_ids = [_coerce_int(deck["id"]) for deck in decks]
    deck_ids_sql = ",".join(str(deck_id) for deck_id in deck_ids)
    counts = _query_deck_counts(col, deck_ids_sql)
    rows = _query_revlog_rows(col, deck_ids_sql)
    usage = _session_summary(rows)
    desired_retention = _deck_desired_retention(selected, config)

    return {
        "exported_at": exported_at,
        "deck_preset": {
            "id": preset_id,
            "name": preset_name,
            "prefix": _preset_prefix(config, preset_id),
        },
        "decks": [
            {"id": _coerce_int(deck["id"]), "name": deck.get("name", "")}
            for deck in decks
        ],
        "deck_size": counts["card_count"],
        "active_card_count": counts["active_card_count"],
        "new_cards_per_day": int(_nested_number(config, "new", "perDay") or 20),
        "desired_retention": round(desired_retention, 4),
        "fsrs6_weights": _round_list(fsrs_params, 8),
        "button_usage": {
            "first_rating_prob": usage["first_rating_prob"],
            "review_rating_prob": usage["review_rating_prob"],
            "learn_costs": usage["learn_costs"],
            "review_costs": usage["review_costs"],
        },
        "stats": {
            "deck_count": len(decks),
            "revlog_rows": len(rows),
            "review_count": usage["review_count"],
            "reviewed_card_count": usage["reviewed_card_count"],
            "session_count": usage["session_count"],
            "skipped_duration_count": usage["skipped_duration_count"],
            "true_retention": usage["true_retention"],
        },
    }


def export_adr_inputs(preset_ids: list[int]) -> dict[str, Any]:
    if mw.col is None:
        raise ExportError("No collection is open.")
    if not preset_ids:
        raise ExportError("Select at least one preset to export.")

    col = mw.col
    decks = _normal_decks(col)
    if not decks:
        raise ExportError("No normal decks found.")

    groups = _group_decks_by_preset(col, decks)
    missing = [preset_id for preset_id in preset_ids if preset_id not in groups]
    if missing:
        raise ExportError(f"Preset(s) not used by any normal deck: {missing}")

    filename_timestamp, exported_at = _export_timestamp()
    export_root = Path(__file__).resolve().parent / "exports"
    export_path = _unique_export_path(export_root, filename_timestamp)

    records = []
    summaries = []
    selected_set = set(preset_ids)
    selected_groups = [
        (preset_id, group)
        for preset_id, group in _sorted_groups(groups)
        if preset_id in selected_set
    ]
    for preset_id, group in selected_groups:
        record = _export_preset_record(
            col,
            group["decks"],
            group["config"],
            exported_at,
        )
        records.append(record)
        summaries.append(
            {
                "preset_id": preset_id,
                "preset_name": record["deck_preset"]["name"],
                "matched_decks": len(group["decks"]),
                "deck_size": record["deck_size"],
                "review_count": record["stats"]["review_count"],
                "session_count": record["stats"]["session_count"],
            }
        )

    _write_jsonl(export_path, records)

    return {
        "export_path": str(export_path),
        "export_folder": str(export_root),
        "preset_count": len(records),
        "matched_decks": sum(summary["matched_decks"] for summary in summaries),
        "deck_size": sum(summary["deck_size"] for summary in summaries),
        "review_count": sum(summary["review_count"] for summary in summaries),
        "session_count": sum(summary["session_count"] for summary in summaries),
        "presets": summaries,
    }


def _exec_dialog(dialog: QDialog) -> int:
    execute = getattr(dialog, "exec", None) or getattr(dialog, "exec_", None)
    return int(execute())


def _prompt_for_presets(groups: dict[int, dict[str, Any]]) -> list[int] | None:
    dialog = QDialog(mw)
    dialog.setWindowTitle(ADDON_TITLE)
    dialog.resize(560, 420)

    layout = QVBoxLayout(dialog)
    title = QLabel("Presets to export")
    layout.addWidget(title)

    help_label = QLabel(NORMAL_DECK_HELP)
    help_label.setWordWrap(True)
    layout.addWidget(help_label)

    scroll = QScrollArea(dialog)
    scroll.setWidgetResizable(True)
    container = QWidget(scroll)
    checkbox_layout = QVBoxLayout(container)
    checkboxes: dict[int, QCheckBox] = {}

    for preset_id, group in _sorted_groups(groups):
        config = group["config"]
        preset_name = _preset_name(config, preset_id)
        deck_count = len(group["decks"])
        label = f"{preset_name} [{preset_id}] ({deck_count} deck{'s' if deck_count != 1 else ''})"
        checkbox = QCheckBox(label, container)
        checkbox.setChecked(True)
        checkbox_layout.addWidget(checkbox)
        checkboxes[preset_id] = checkbox

    checkbox_layout.addStretch(1)
    scroll.setWidget(container)
    layout.addWidget(scroll)

    select_layout = QHBoxLayout()
    select_all = QPushButton("Select all", dialog)
    clear_all = QPushButton("Clear", dialog)
    select_layout.addWidget(select_all)
    select_layout.addWidget(clear_all)
    select_layout.addStretch(1)
    layout.addLayout(select_layout)

    buttons = QHBoxLayout()
    buttons.addStretch(1)
    cancel = QPushButton("Cancel", dialog)
    export = QPushButton("Export", dialog)
    buttons.addWidget(cancel)
    buttons.addWidget(export)
    layout.addLayout(buttons)

    def set_all(checked: bool) -> None:
        for checkbox in checkboxes.values():
            checkbox.setChecked(checked)

    def accept_selected() -> None:
        selected = [
            preset_id for preset_id, checkbox in checkboxes.items() if checkbox.isChecked()
        ]
        if not selected:
            showWarning("Select at least one preset to export.", title=ADDON_TITLE)
            return
        dialog.selected_preset_ids = selected
        dialog.accept()

    qconnect(select_all.clicked, lambda: set_all(True))
    qconnect(clear_all.clicked, lambda: set_all(False))
    qconnect(cancel.clicked, dialog.reject)
    qconnect(export.clicked, accept_selected)

    if _exec_dialog(dialog) != 1:
        return None
    return getattr(dialog, "selected_preset_ids", None)


def _open_export_folder(folder: str) -> None:
    path = Path(folder).resolve()
    path.mkdir(parents=True, exist_ok=True)
    opened = QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
    if not opened:
        showWarning(f"Could not open export folder:\n{path}", title=ADDON_TITLE)


def _show_export_result(result: dict[str, Any]) -> None:
    dialog = QDialog(mw)
    dialog.setWindowTitle(ADDON_TITLE)
    dialog.resize(620, 360)

    lines = [
        "ADR input export complete.",
        "",
        f"Presets: {result['preset_count']}",
        f"Matched decks: {result['matched_decks']}",
        f"Cards: {result['deck_size']}",
        f"Review events: {result['review_count']}",
        f"Sessions: {result['session_count']}",
        "",
        f"Export file:\n{result['export_path']}",
        "",
        f"Output folder:\n{result['export_folder']}",
    ]

    layout = QVBoxLayout(dialog)
    label = QLabel("\n".join(lines), dialog)
    label.setWordWrap(True)
    layout.addWidget(label)

    buttons = QHBoxLayout()
    buttons.addStretch(1)
    open_folder = QPushButton("Open folder", dialog)
    ok = QPushButton("OK", dialog)
    buttons.addWidget(open_folder)
    buttons.addWidget(ok)
    layout.addLayout(buttons)

    qconnect(open_folder.clicked, lambda: _open_export_folder(result["export_folder"]))
    qconnect(ok.clicked, dialog.accept)

    _exec_dialog(dialog)


def prompt_and_export() -> None:
    try:
        groups = _load_preset_groups()
    except Exception as exc:
        showWarning(str(exc), title=ADDON_TITLE)
        return

    preset_ids = _prompt_for_presets(groups)
    if not preset_ids:
        return

    def task() -> dict[str, Any]:
        return export_adr_inputs(preset_ids)

    def done(future: Any) -> None:
        try:
            result = future.result()
        except Exception as exc:
            showWarning(str(exc), title=ADDON_TITLE)
            return
        QTimer.singleShot(100, lambda: _show_export_result(result))

    mw.taskman.with_progress(
        task,
        done,
        parent=mw,
        label="Exporting ADR inputs...",
        immediate=True,
        title=ADDON_TITLE,
    )
