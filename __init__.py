from __future__ import annotations

import contextlib
import datetime as dt
import html
import io
import json
import math
import os
import platform
import queue
import random
import re
import subprocess
import threading
import time
import traceback
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from anki.decks import FilteredDeckConfig
from aqt import mw
from aqt import gui_hooks
from aqt.operations.scheduling import add_or_update_filtered_deck
from aqt.qt import (
    QAbstractItemView,
    QAction,
    QCheckBox,
    QComboBox,
    QDesktopServices,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTimer,
    QUrl,
    QVBoxLayout,
    QWidget,
    Qt,
    qconnect,
)
from aqt.utils import showWarning
from aqt.webview import AnkiWebView

from .adr_button_usage import prompt_and_export


ADDON_TITLE = "Linear ADR"
ADDON_ROOT = Path(__file__).resolve().parent
EXPORTS_DIR = ADDON_ROOT / "exports"
OUTPUTS_DIR = ADDON_ROOT / "outputs"
CARS_PATH = ADDON_ROOT / "cars.json"

ENV_BINARY = "ADR_OPTIMIZER_BINARY"

CAR_SCHEMA_VERSION = 1
POLICY_MODE_ADR = "adr"
POLICY_MODE_FIXED_DR = "fixed_dr"
POLICY_MODE_NORMAL_ANKI = "normal_anki"

POLICY_MODE_LABELS = {
    POLICY_MODE_ADR: "ADR",
    POLICY_MODE_FIXED_DR: "Fixed DR",
    POLICY_MODE_NORMAL_ANKI: "Normal Anki",
}
POLICY_LABEL_TO_MODE = {label: mode for mode, label in POLICY_MODE_LABELS.items()}

SCHEDULING_SELECTIONS = [
    ("recommended", "Recommended"),
    ("aggressive", "Aggressive"),
    ("calm", "Calm"),
]
SELECTION_LABELS = {key: label for key, label in SCHEDULING_SELECTIONS}
SELECTION_TO_RESULT_LABEL = {
    "recommended": "Recommended",
    "aggressive": "Aggressive",
    "calm": "Calm",
}

DEFAULT_ADDON_CONFIG: dict[str, Any] = {
    "custom_quality_presets": [],
    "track_adr_optimization_history": True,
    "enable_advanced_safeguards": True,
    "warn_before_creating_new_car_when_active_exists": True,
    "warn_before_moving_car_forward": True,
    "warn_before_allowing_car_overtaking": True,
    "warn_before_editing_saved_adr_parameters": True,
    "enable_soft_interval_cap": True,
    "soft_interval_cap_threshold": 1000.0,
    "soft_interval_cap_power": 0.5,
    "filtered_deck_limit": 100,
    "filtered_deck_reschedule": True,
    "filtered_deck_order": "Due",
    "filtered_deck_drive_backward": True,
}

QUALITY_LABELS = {
    "potato": "Potato",
    "lite": "Lite",
    "medium": "Medium",
    "medium-high": "Medium-High",
    "high": "High",
}
BUILTIN_QUALITY_NAMES = ["potato", "lite", "medium", "medium-high", "high"]
DEFAULT_QUALITY = "medium-high"

BASIC_FIELDS = [
    ("target_dr", "Fixed DR target baseline"),
    ("learn_limit", "New cards per day"),
    ("deck_size", "Simulated deck size"),
    ("days", "Simulation duration"),
    ("include_original", "Compare with reference ADR parameters"),
    ("original", "Reference ADR parameters"),
]

ADVANCED_FIELDS = [
    "fixed_dr_start_pct",
    "fixed_dr_end_pct",
    "fixed_dr_label_step_pct",
    "ignore_safety",
    "safety_checks",
    "safety_s_max",
    "aggressive_calm_regret_pct",
    "seed",
]

CONTROL_FIELDS = [
    "legacy_unsafe_plot_display",
    "phase1_eval_weight",
    "phase2_eval_weight",
    "phase3_eval_weight",
    "phase4_eval_weight",
    "final_eval_weight",
    "fixed_curve_coarse_weight",
    "fixed_curve_refine_weight",
    "fixed_curve_coarse_step_pct",
    "fixed_curve_refine_step_pct",
    "fixed_curve_initial_radius_pct",
    "fixed_curve_adapt_margin_pct",
    "fixed_curve_adapt_top_per_bucket",
    "fixed_curve_adapt_max_points",
    "phase1_flat_step",
    "phase1_flat_half_steps",
    "phase1_s_step",
    "phase1_s_max",
    "phase1_d_step",
    "phase1_d_min",
    "phase1_expand_rounds",
    "promote_recommended",
    "promote_efficiency_potential",
    "promote_memory_potential",
    "promote_pareto_extra",
    "phase4_seeds_per_objective",
    "phase4_max_steps",
    "final_candidate_limit",
    "max_spread_final_candidates",
    "final_shortlist_recommended",
    "final_shortlist_efficiency",
    "final_shortlist_memory",
    "final_shortlist_frontier",
]

HIDDEN_FIELDS = [
    "dr_prune_weight",
    "phase1_expand",
    "phase1_expand_batch",
    "phase1_expand_overflow_factor",
    "phase2_flat_step",
    "phase2_s_step",
    "phase2_d_step",
    "phase3_flat_step",
    "phase3_s_step",
    "phase3_d_step",
    "phase4_flat_step",
    "phase4_s_step",
    "phase4_d_step",
    "bridge_midpoint_limit",
    "experimental_bridge_midpoint_neighborhoods",
    "scout_potential_band_pct",
    "final_potential_band_pct",
    "inspect_point",
]

FIELD_KEYS = [
    *(key for key, _label in BASIC_FIELDS),
    *ADVANCED_FIELDS,
    *CONTROL_FIELDS,
    *HIDDEN_FIELDS,
]
CUSTOM_PRESET_FIELDS = [key for key in FIELD_KEYS if key != "target_dr"]

DEFAULT_VALUES: dict[str, Any] = {
    "target_dr": 0.9,
    "days": 1825,
    "deck_size": 10000,
    "learn_limit": 10,
    "seed": 1234,
    "phase1_eval_weight": 2000.0,
    "phase2_eval_weight": 4000.0,
    "phase3_eval_weight": 4000.0,
    "phase4_eval_weight": 4000.0,
    "final_eval_weight": 30000.0,
    "dr_prune_weight": 1.0,
    "phase1_flat_step": 0.04,
    "phase1_flat_half_steps": 8,
    "phase1_s_step": 0.02,
    "phase1_s_max": 0.26,
    "phase1_d_step": 0.02,
    "phase1_d_min": -0.20,
    "phase1_expand": True,
    "phase1_expand_rounds": 8,
    "phase1_expand_batch": 2,
    "phase1_expand_overflow_factor": 2.0,
    "phase2_flat_step": 0.02,
    "phase2_s_step": 0.01,
    "phase2_d_step": 0.01,
    "phase3_flat_step": 0.01,
    "phase3_s_step": 0.005,
    "phase3_d_step": 0.005,
    "phase4_flat_step": 0.002,
    "phase4_s_step": 0.001,
    "phase4_d_step": 0.001,
    "phase4_seeds_per_objective": 6,
    "phase4_max_steps": 8,
    "promote_recommended": 50,
    "promote_efficiency_potential": 25,
    "promote_memory_potential": 25,
    "promote_pareto_extra": 100,
    "bridge_midpoint_limit": 50,
    "experimental_bridge_midpoint_neighborhoods": False,
    "final_candidate_limit": 180,
    "max_spread_final_candidates": 12,
    "final_shortlist_recommended": 120,
    "final_shortlist_efficiency": 70,
    "final_shortlist_memory": 70,
    "final_shortlist_frontier": 100,
    "scout_potential_band_pct": 0.3,
    "final_potential_band_pct": 0.1,
    "aggressive_calm_regret_pct": 0.50,
    "safety_s_max": 1000.0,
    "safety_checks": 3000,
    "ignore_safety": False,
    "legacy_unsafe_plot_display": False,
    "include_original": False,
    "original": (1.57, 0.135, -0.085),
    "inspect_point": (),
    "fixed_dr_start_pct": 60.0,
    "fixed_dr_end_pct": 96.0,
    "fixed_curve_coarse_weight": 10000.0,
    "fixed_curve_refine_weight": 80000.0,
    "fixed_curve_coarse_step_pct": 1.0,
    "fixed_curve_refine_step_pct": 0.2,
    "fixed_curve_initial_radius_pct": 1.0,
    "fixed_curve_adapt_margin_pct": 0.2,
    "fixed_curve_adapt_top_per_bucket": 8,
    "fixed_curve_adapt_max_points": 80,
    "fixed_dr_label_step_pct": 10.0,
}

FULL_HORIZON_VALUES = {
    "days": 1825,
    "deck_size": 10000,
    "learn_limit": 10,
}

QUALITY_PRESET_VALUES: dict[str, dict[str, Any]] = {
    "potato": {
        "phase1_eval_weight": 300.0,
        "phase2_eval_weight": 600.0,
        "phase3_eval_weight": 600.0,
        "phase4_eval_weight": 600.0,
        "final_eval_weight": 12000.0,
        "fixed_curve_coarse_weight": 3000.0,
        "fixed_curve_refine_weight": 30000.0,
        "fixed_curve_coarse_step_pct": 4.0,
        "fixed_curve_refine_step_pct": 1.0,
        "fixed_curve_initial_radius_pct": 0.4,
        "fixed_curve_adapt_margin_pct": 0.2,
        "fixed_curve_adapt_top_per_bucket": 1,
        "fixed_curve_adapt_max_points": 12,
        "phase1_flat_step": 0.08,
        "phase1_flat_half_steps": 3,
        "phase1_s_step": 0.05,
        "phase1_s_max": 0.25,
        "phase1_d_step": 0.05,
        "phase1_d_min": -0.20,
        "phase1_expand_rounds": 0,
        "promote_recommended": 6,
        "promote_efficiency_potential": 3,
        "promote_memory_potential": 3,
        "promote_pareto_extra": 8,
        "phase4_seeds_per_objective": 1,
        "phase4_max_steps": 1,
        "final_candidate_limit": 32,
        "max_spread_final_candidates": 2,
        "final_shortlist_recommended": 24,
        "final_shortlist_efficiency": 12,
        "final_shortlist_memory": 12,
        "final_shortlist_frontier": 16,
        "safety_checks": 3000,
    },
    "lite": {
        "phase1_eval_weight": 600.0,
        "phase2_eval_weight": 1200.0,
        "phase3_eval_weight": 1200.0,
        "phase4_eval_weight": 1200.0,
        "final_eval_weight": 60000.0,
        "fixed_curve_coarse_weight": 5000.0,
        "fixed_curve_refine_weight": 30000.0,
        "fixed_curve_coarse_step_pct": 2.5,
        "fixed_curve_refine_step_pct": 0.5,
        "fixed_curve_initial_radius_pct": 0.7,
        "fixed_curve_adapt_margin_pct": 0.25,
        "fixed_curve_adapt_top_per_bucket": 3,
        "fixed_curve_adapt_max_points": 30,
        "phase1_flat_step": 0.06,
        "phase1_flat_half_steps": 6,
        "phase1_s_step": 0.035,
        "phase1_s_max": 0.28,
        "phase1_d_step": 0.035,
        "phase1_d_min": -0.21,
        "phase1_expand_rounds": 0,
        "promote_recommended": 12,
        "promote_efficiency_potential": 6,
        "promote_memory_potential": 6,
        "promote_pareto_extra": 20,
        "phase4_seeds_per_objective": 1,
        "phase4_max_steps": 2,
        "final_candidate_limit": 60,
        "max_spread_final_candidates": 4,
        "final_shortlist_recommended": 50,
        "final_shortlist_efficiency": 25,
        "final_shortlist_memory": 25,
        "final_shortlist_frontier": 35,
        "safety_checks": 3000,
    },
    "medium": {
        "phase1_eval_weight": 2000.0,
        "phase2_eval_weight": 4000.0,
        "phase3_eval_weight": 4000.0,
        "phase4_eval_weight": 4000.0,
        "final_eval_weight": 100000.0,
        "fixed_curve_coarse_weight": 10000.0,
        "fixed_curve_refine_weight": 80000.0,
        "fixed_curve_coarse_step_pct": 1.5,
        "fixed_curve_refine_step_pct": 0.3,
        "fixed_curve_initial_radius_pct": 1.0,
        "fixed_curve_adapt_margin_pct": 0.2,
        "fixed_curve_adapt_top_per_bucket": 6,
        "fixed_curve_adapt_max_points": 60,
        "phase1_flat_step": 0.05,
        "phase1_flat_half_steps": 6,
        "phase1_s_step": 0.025,
        "phase1_s_max": 0.275,
        "phase1_d_step": 0.025,
        "phase1_d_min": -0.225,
        "phase1_expand_rounds": 1,
        "promote_recommended": 28,
        "promote_efficiency_potential": 14,
        "promote_memory_potential": 14,
        "promote_pareto_extra": 50,
        "phase4_seeds_per_objective": 3,
        "phase4_max_steps": 4,
        "final_candidate_limit": 100,
        "max_spread_final_candidates": 8,
        "final_shortlist_recommended": 90,
        "final_shortlist_efficiency": 50,
        "final_shortlist_memory": 50,
        "final_shortlist_frontier": 70,
        "safety_checks": 3000,
    },
    "medium-high": {
        "phase1_eval_weight": 2000.0,
        "phase2_eval_weight": 4000.0,
        "phase3_eval_weight": 4000.0,
        "phase4_eval_weight": 4000.0,
        "final_eval_weight": 200000.0,
        "fixed_curve_coarse_weight": 10000.0,
        "fixed_curve_refine_weight": 80000.0,
        "fixed_curve_coarse_step_pct": 1.0,
        "fixed_curve_refine_step_pct": 0.2,
        "fixed_curve_initial_radius_pct": 1.0,
        "fixed_curve_adapt_margin_pct": 0.2,
        "fixed_curve_adapt_top_per_bucket": 8,
        "fixed_curve_adapt_max_points": 80,
        "phase1_flat_step": 0.04,
        "phase1_flat_half_steps": 8,
        "phase1_s_step": 0.02,
        "phase1_s_max": 0.26,
        "phase1_d_step": 0.02,
        "phase1_d_min": -0.20,
        "phase1_expand_rounds": 8,
        "promote_recommended": 50,
        "promote_efficiency_potential": 25,
        "promote_memory_potential": 25,
        "promote_pareto_extra": 100,
        "phase4_seeds_per_objective": 6,
        "phase4_max_steps": 8,
        "final_candidate_limit": 180,
        "max_spread_final_candidates": 12,
        "final_shortlist_recommended": 120,
        "final_shortlist_efficiency": 70,
        "final_shortlist_memory": 70,
        "final_shortlist_frontier": 100,
        "safety_checks": 3000,
    },
    "high": {
        "phase1_eval_weight": 8000.0,
        "phase2_eval_weight": 20000.0,
        "phase3_eval_weight": 50000.0,
        "phase4_eval_weight": 50000.0,
        "final_eval_weight": 500000.0,
        "fixed_curve_coarse_weight": 20000.0,
        "fixed_curve_refine_weight": 160000.0,
        "fixed_curve_coarse_step_pct": 1.0,
        "fixed_curve_refine_step_pct": 0.2,
        "fixed_curve_initial_radius_pct": 1.2,
        "fixed_curve_adapt_margin_pct": 0.2,
        "fixed_curve_adapt_top_per_bucket": 10,
        "fixed_curve_adapt_max_points": 110,
        "phase1_flat_step": 0.04,
        "phase1_flat_half_steps": 9,
        "phase1_s_step": 0.02,
        "phase1_s_max": 0.28,
        "phase1_d_step": 0.02,
        "phase1_d_min": -0.22,
        "phase1_expand_rounds": 8,
        "promote_recommended": 65,
        "promote_efficiency_potential": 35,
        "promote_memory_potential": 35,
        "promote_pareto_extra": 140,
        "phase4_seeds_per_objective": 8,
        "phase4_max_steps": 10,
        "final_candidate_limit": 360,
        "max_spread_final_candidates": 16,
        "final_shortlist_recommended": 120,
        "final_shortlist_efficiency": 70,
        "final_shortlist_memory": 70,
        "final_shortlist_frontier": 120,
        "safety_checks": 5000,
    },
}

BOOL_FIELDS = {
    key for key, value in DEFAULT_VALUES.items() if isinstance(value, bool)
}
INT_FIELDS = {
    key
    for key, value in DEFAULT_VALUES.items()
    if isinstance(value, int) and not isinstance(value, bool)
}
FLOAT_FIELDS = {
    key for key, value in DEFAULT_VALUES.items() if isinstance(value, float)
}


class OptimizerBinaryNotFound(RuntimeError):
    pass


class NoWheelSpinBox(QSpinBox):
    def wheelEvent(self, event: Any) -> None:
        event.ignore()


class NoWheelDoubleSpinBox(QDoubleSpinBox):
    def wheelEvent(self, event: Any) -> None:
        event.ignore()


@dataclass
class SimulationRequest:
    export_path: Path
    deck_preset: str
    quality_preset: str
    values: dict[str, Any]


@dataclass
class OptimizerRunResult:
    summary_path: Path
    plot_path: Path | None
    returncode: int
    partial: bool = False


@dataclass
class BatchOptimizeRequest:
    export_path: Path
    jobs: list[dict[str, Any]]
    batch_config_path: Path
    batch_output_path: Path


@dataclass
class BatchOptimizeResult:
    summary_path: Path
    returncode: int
    output: str


class _QueueWriter(io.TextIOBase):
    def __init__(self, out_queue: queue.Queue[tuple[str, Any]]) -> None:
        self._out_queue = out_queue

    @property
    def encoding(self) -> str:
        return "utf-8"

    @property
    def errors(self) -> str:
        return "backslashreplace"

    def isatty(self) -> bool:
        return False

    def writable(self) -> bool:
        return True

    def write(self, text: str) -> int:
        if text:
            self._out_queue.put(("log", text))
        return len(text)

    def flush(self) -> None:
        pass


def _queue_log(
    out_queue: queue.Queue[tuple[str, Any]] | None,
    text: str,
) -> None:
    if out_queue is not None:
        out_queue.put(("log", text))


class RunProgressDialog(QDialog):
    def __init__(self, request: SimulationRequest) -> None:
        super().__init__(mw)
        self._request = request
        self._queue: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._line_buffer = ""
        self._log_text = ""
        self._result: OptimizerRunResult | None = None
        self._finished = False
        self._graph_opened = False

        self.setWindowTitle(f"{ADDON_TITLE} - Simulation")
        self.resize(920, 640)

        layout = QVBoxLayout(self)
        self.status_label = QLabel("Running ADR simulation...", self)
        layout.addWidget(self.status_label)

        self.progress = QProgressBar(self)
        self.progress.setRange(0, 0)
        layout.addWidget(self.progress)

        self.log = QPlainTextEdit(self)
        self.log.setReadOnly(True)
        try:
            self.log.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        except AttributeError:
            self.log.setLineWrapMode(QPlainTextEdit.NoWrap)
        layout.addWidget(self.log, 1)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.show_graph = QPushButton("Show graph", self)
        self.show_graph.setEnabled(False)
        self.open_outputs = QPushButton("Open output folder", self)
        self.open_outputs.setEnabled(False)
        self.close_button = QPushButton("Close", self)
        self.close_button.setEnabled(False)
        buttons.addWidget(self.show_graph)
        buttons.addWidget(self.open_outputs)
        buttons.addWidget(self.close_button)
        layout.addLayout(buttons)

        qconnect(self.show_graph.clicked, self._open_graph)
        qconnect(self.open_outputs.clicked, _open_output_folder)
        qconnect(self.close_button.clicked, self.accept)

        self._timer = QTimer(self)
        self._timer.setInterval(100)
        qconnect(self._timer.timeout, self._drain_queue)
        self._timer.start()

        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def closeEvent(self, event: Any) -> None:
        if not self._finished:
            self.hide()
            event.ignore()
            return
        super().closeEvent(event)

    def _run(self) -> None:
        writer = _QueueWriter(self._queue)
        try:
            self._queue.put(("log", "Starting ADR simulation...\n"))
            with contextlib.redirect_stdout(writer), contextlib.redirect_stderr(writer):
                result = run_optimizer_subprocess(self._request, self._queue)
            if result.returncode == 0:
                self._queue.put(("done", result))
            elif result.summary_path.exists():
                result.partial = True
                self._queue.put(("partial", result))
            else:
                self._queue.put(("error", f"Optimizer exited with code {result.returncode}."))
        except Exception:
            self._queue.put(("error", traceback.format_exc()))

    def _drain_queue(self) -> None:
        while True:
            try:
                kind, payload = self._queue.get_nowait()
            except queue.Empty:
                break

            if kind == "log":
                self._append_log(str(payload))
            elif kind == "done":
                self._finish(payload, partial=False)
            elif kind == "partial":
                self._finish(payload, partial=True)
            elif kind == "error":
                self._fail(str(payload))

    def _append_log(self, text: str) -> None:
        self._log_text += text
        self._line_buffer += text.replace("\r\n", "\n").replace("\r", "\n")
        while "\n" in self._line_buffer:
            line, self._line_buffer = self._line_buffer.split("\n", 1)
            self.log.appendPlainText(line)
        self.log.verticalScrollBar().setValue(self.log.verticalScrollBar().maximum())

    def _flush_log(self) -> None:
        if self._line_buffer:
            self.log.appendPlainText(self._line_buffer)
            self._line_buffer = ""
        self.log.verticalScrollBar().setValue(self.log.verticalScrollBar().maximum())

    def _finish(self, result: OptimizerRunResult, *, partial: bool) -> None:
        self._result = result
        self._finished = True
        self._timer.stop()
        self._flush_log()
        if partial:
            self.status_label.setText("ADR simulation wrote a summary, but the optimizer reported an error.")
        else:
            self.status_label.setText("ADR simulation complete.")
        self.progress.setRange(0, 1)
        self.progress.setValue(1)
        self.show_graph.setEnabled(result.summary_path.exists())
        self.open_outputs.setEnabled(True)
        self.close_button.setEnabled(True)
        if result.summary_path.exists():
            self._open_graph()

    def _fail(self, details: str) -> None:
        self._finished = True
        self._timer.stop()
        self._append_log(details)
        self._flush_log()
        self.status_label.setText("ADR simulation failed.")
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.open_outputs.setEnabled(True)
        self.close_button.setEnabled(True)
        showWarning(details, title=ADDON_TITLE)

    def _open_graph(self) -> None:
        if self._result is None or not self._result.summary_path.exists():
            return
        if self._graph_opened:
            _show_result(self._result, self.log.toPlainText())
            return
        self._graph_opened = True
        _show_result(self._result, self.log.toPlainText())


class BatchRunProgressDialog(QDialog):
    def __init__(self, request: BatchOptimizeRequest) -> None:
        super().__init__(mw)
        self._request = request
        self._queue: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._line_buffer = ""
        self._log_text = ""
        self._result: BatchOptimizeResult | None = None
        self._finished = False
        self._saved = False

        self.setWindowTitle(f"{ADDON_TITLE} - Optimize ADR Parameters")
        self.resize(920, 640)

        layout = QVBoxLayout(self)
        self.status_label = QLabel("Optimizing ADR parameters...", self)
        layout.addWidget(self.status_label)

        self.progress = QProgressBar(self)
        self.progress.setRange(0, 0)
        layout.addWidget(self.progress)

        self.log = QPlainTextEdit(self)
        self.log.setReadOnly(True)
        try:
            self.log.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        except AttributeError:
            self.log.setLineWrapMode(QPlainTextEdit.NoWrap)
        layout.addWidget(self.log, 1)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.review_car = QPushButton("Review car", self)
        self.review_car.setEnabled(False)
        self.open_outputs = QPushButton("Open output folder", self)
        self.open_outputs.setEnabled(False)
        self.close_button = QPushButton("Close", self)
        self.close_button.setEnabled(False)
        buttons.addWidget(self.review_car)
        buttons.addWidget(self.open_outputs)
        buttons.addWidget(self.close_button)
        layout.addLayout(buttons)

        qconnect(self.review_car.clicked, self._review_result)
        qconnect(self.open_outputs.clicked, _open_output_folder)
        qconnect(self.close_button.clicked, self.accept)

        self._timer = QTimer(self)
        self._timer.setInterval(100)
        qconnect(self._timer.timeout, self._drain_queue)
        self._timer.start()

        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def closeEvent(self, event: Any) -> None:
        if not self._finished:
            self.hide()
            event.ignore()
            return
        super().closeEvent(event)

    def _run(self) -> None:
        writer = _QueueWriter(self._queue)
        try:
            self._queue.put(("log", "Starting ADR parameter optimization...\n"))
            with contextlib.redirect_stdout(writer), contextlib.redirect_stderr(writer):
                result = run_batch_optimizer_subprocess(self._request, self._queue)
            if result.returncode == 0:
                self._queue.put(("done", result))
            elif result.summary_path.exists():
                self._queue.put(("partial", result))
            else:
                self._queue.put(("error", f"Batch optimizer exited with code {result.returncode}."))
        except Exception:
            self._queue.put(("error", traceback.format_exc()))

    def _drain_queue(self) -> None:
        while True:
            try:
                kind, payload = self._queue.get_nowait()
            except queue.Empty:
                break

            if kind == "log":
                self._append_log(str(payload))
            elif kind == "done":
                self._finish(payload, partial=False)
            elif kind == "partial":
                self._finish(payload, partial=True)
            elif kind == "error":
                self._fail(str(payload))

    def _append_log(self, text: str) -> None:
        self._log_text += text
        self._line_buffer += text.replace("\r\n", "\n").replace("\r", "\n")
        while "\n" in self._line_buffer:
            line, self._line_buffer = self._line_buffer.split("\n", 1)
            self.log.appendPlainText(line)
        self.log.verticalScrollBar().setValue(self.log.verticalScrollBar().maximum())

    def _flush_log(self) -> None:
        if self._line_buffer:
            self.log.appendPlainText(self._line_buffer)
            self._line_buffer = ""
        self.log.verticalScrollBar().setValue(self.log.verticalScrollBar().maximum())

    def _finish(self, result: BatchOptimizeResult, *, partial: bool) -> None:
        self._result = result
        self._finished = True
        self._timer.stop()
        self._flush_log()
        if partial:
            self.status_label.setText("ADR optimization wrote a summary, but the optimizer reported an error.")
        else:
            self.status_label.setText("ADR optimization complete.")
        self.progress.setRange(0, 1)
        self.progress.setValue(1)
        self.review_car.setEnabled(result.summary_path.exists())
        self.open_outputs.setEnabled(True)
        self.close_button.setEnabled(True)
        if result.summary_path.exists():
            self._review_result()

    def _fail(self, details: str) -> None:
        self._finished = True
        self._timer.stop()
        self._append_log(details)
        self._flush_log()
        self.status_label.setText("ADR optimization failed.")
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.open_outputs.setEnabled(True)
        self.close_button.setEnabled(True)
        showWarning(details, title=ADDON_TITLE)

    def _review_result(self) -> None:
        if self._result is None or self._saved:
            return
        saved = _review_batch_optimizer_result(self._request, self._result)
        if saved:
            self._saved = True
            self.review_car.setEnabled(False)
            self.status_label.setText("ADR optimization complete; car saved.")


class SimulationDialog(QDialog):
    def __init__(self, export_path: Path, rows: list[dict[str, Any]]) -> None:
        super().__init__(mw)
        self.export_path = export_path
        self.rows = rows
        self.fields: dict[str, QWidget] = {}
        self.request: SimulationRequest | None = None
        self._refreshing_quality = False

        self.setWindowTitle(ADDON_TITLE)
        self.resize(720, 640)

        layout = QVBoxLayout(self)
        file_label = QLabel(f"Button usage file:\n{_display_path(export_path)}", self)
        file_label.setToolTip(str(export_path))
        file_label.setWordWrap(True)
        layout.addWidget(file_label)

        top_form = QFormLayout()
        self.preset_combo = QComboBox(self)
        for index, row in enumerate(rows):
            self.preset_combo.addItem(_row_label(row), index)
        qconnect(self.preset_combo.currentIndexChanged, self._preset_changed)
        top_form.addRow("Deck preset", self.preset_combo)

        quality_row = QWidget(self)
        quality_layout = QHBoxLayout(quality_row)
        quality_layout.setContentsMargins(0, 0, 0, 0)
        self.quality_combo = QComboBox(quality_row)
        self.quality_combo.setMinimumWidth(220)
        self.save_quality = QPushButton("Save Custom", quality_row)
        self.rename_quality = QPushButton("Rename", quality_row)
        self.delete_quality = QPushButton("Delete", quality_row)
        quality_layout.addWidget(self.quality_combo, 1)
        quality_layout.addWidget(self.save_quality)
        quality_layout.addWidget(self.rename_quality)
        quality_layout.addWidget(self.delete_quality)
        top_form.addRow("Search quality preset", quality_row)
        layout.addLayout(top_form)

        tabs = QTabWidget(self)
        tabs.addTab(self._make_settings_tab(BASIC_FIELDS), "Settings")
        tabs.addTab(
            self._make_grid_tab([(name, name) for name in ADVANCED_FIELDS], columns=3),
            "Advanced",
        )
        tabs.addTab(
            self._make_grid_tab([(name, name) for name in CONTROL_FIELDS], columns=3),
            "Control",
        )
        tabs.addTab(
            self._make_grid_tab([(name, name) for name in HIDDEN_FIELDS], columns=3),
            "Hidden",
        )
        layout.addWidget(tabs, 1)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("Cancel", self)
        run_button = QPushButton("Run simulation", self)
        buttons.addWidget(cancel)
        buttons.addWidget(run_button)
        layout.addLayout(buttons)

        qconnect(self.quality_combo.currentIndexChanged, self._quality_changed)
        qconnect(self.save_quality.clicked, self._save_custom_quality)
        qconnect(self.rename_quality.clicked, self._rename_custom_quality)
        qconnect(self.delete_quality.clicked, self._delete_custom_quality)
        qconnect(cancel.clicked, self.reject)
        qconnect(run_button.clicked, self._accept_request)

        self._refresh_quality_combo(select_builtin=DEFAULT_QUALITY)
        self._quality_changed()
        self._preset_changed()

    def _make_settings_tab(self, fields: list[tuple[str, str]]) -> QScrollArea:
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        container = QWidget(scroll)
        grid = QGridLayout(container)
        grid.setColumnStretch(0, 0)
        grid.setColumnStretch(1, 0)
        grid.setColumnStretch(2, 1)
        grid.setHorizontalSpacing(20)
        grid.setVerticalSpacing(10)

        for row, (key, label) in enumerate(fields):
            label_widget = QLabel(label, container)
            label_widget.setWordWrap(True)
            label_widget.setMaximumWidth(280)
            widget = self._make_field(key, settings=True)
            self.fields[key] = widget
            grid.addWidget(label_widget, row, 0)
            grid.addWidget(widget, row, 1)

        grid.setRowStretch(len(fields), 1)

        scroll.setWidget(container)
        return scroll

    def _make_grid_tab(self, fields: list[tuple[str, str]], *, columns: int) -> QScrollArea:
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        container = QWidget(scroll)
        grid = QGridLayout(container)
        for col in range(columns):
            grid.setColumnStretch(col, 1)
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(8)
        rows = max(1, (len(fields) + columns - 1) // columns)

        for index, (key, label) in enumerate(fields):
            field_box = QWidget(container)
            field_layout = QVBoxLayout(field_box)
            field_layout.setContentsMargins(0, 0, 0, 0)
            field_layout.setSpacing(3)

            label_widget = QLabel(label, field_box)
            label_widget.setWordWrap(True)
            field_layout.addWidget(label_widget)

            widget = self._make_field(key)
            self.fields[key] = widget
            field_layout.addWidget(widget)

            row = index % rows
            col = index // rows
            grid.addWidget(field_box, row, col)

        grid.setRowStretch(rows, 1)
        scroll.setWidget(container)
        return scroll

    def _make_field(self, key: str, *, settings: bool = False) -> QWidget:
        if key in BOOL_FIELDS:
            widget = QCheckBox(self)
            return widget
        if key in INT_FIELDS:
            widget = NoWheelSpinBox(self)
            widget.setRange(0, 2_000_000_000)
            widget.setSingleStep(1)
            widget.setKeyboardTracking(False)
            _set_field_width(widget, 170, fixed=settings)
            return widget
        if key in FLOAT_FIELDS:
            widget = QDoubleSpinBox(self) if key == "target_dr" else NoWheelDoubleSpinBox(self)
            widget.setRange(-1_000_000.0, 1_000_000.0)
            widget.setDecimals(6)
            widget.setSingleStep(0.01)
            widget.setKeyboardTracking(False)
            _set_field_width(widget, 170, fixed=settings)
            return widget
        widget = QLineEdit(self)
        _set_field_width(widget, 260, fixed=settings)
        return widget

    def _quality_changed(self, *_args: Any) -> None:
        if self._refreshing_quality:
            return
        data = self._current_quality_data()
        if data["kind"] == "custom":
            custom = self._custom_by_id(int(data["id"]))
            base = str(custom.get("base", DEFAULT_QUALITY)) if custom else DEFAULT_QUALITY
            values = config_for_quality(base)
            if custom:
                values.update(custom.get("values", {}))
        else:
            values = config_for_quality(str(data["key"]))
        self._apply_config_values(values)
        self._preset_changed()
        self._update_quality_buttons()

    def _preset_changed(self, *_args: Any) -> None:
        row = self.current_row()
        target = row.get("desired_retention", None)
        if target is not None:
            self._set_field_value("target_dr", float(target))

    def _apply_config_values(self, config: dict[str, Any]) -> None:
        for key, widget in self.fields.items():
            self._set_field_value(key, config.get(key, DEFAULT_VALUES.get(key)))

    def _set_field_value(self, key: str, value: Any) -> None:
        widget = self.fields.get(key)
        if widget is None:
            return
        if isinstance(widget, QCheckBox):
            widget.setChecked(bool(value))
        elif isinstance(widget, QSpinBox):
            widget.setValue(int(value))
        elif isinstance(widget, QDoubleSpinBox):
            widget.setValue(float(value) if value is not None else 0.0)
        elif isinstance(widget, QLineEdit):
            widget.setText(_format_tuple_value(value))

    def current_row(self) -> dict[str, Any]:
        index = self.preset_combo.currentData()
        if index is None:
            index = self.preset_combo.currentIndex()
        return self.rows[int(index)]

    def _accept_request(self) -> None:
        try:
            self.request = self.build_request()
        except Exception as exc:
            showWarning(str(exc), title=ADDON_TITLE)
            return
        if not self._confirm_high_target_dr(self.request.values):
            return
        self.accept()

    def build_request(self) -> SimulationRequest:
        values = self._read_all_fields()
        row = self.current_row()
        preset = row.get("deck_preset", {}).get("name") or ""
        if not preset:
            raise ValueError("The selected export row does not include a deck preset name.")
        return SimulationRequest(
            export_path=self.export_path,
            deck_preset=preset,
            quality_preset=self._current_quality_base(),
            values=values,
        )

    def _read_all_fields(self) -> dict[str, Any]:
        values = config_for_quality(self._current_quality_base())
        for key, widget in self.fields.items():
            values[key] = _read_field_value(key, widget)
        return values

    def _confirm_high_target_dr(self, values: dict[str, Any]) -> bool:
        target = float(values["target_dr"])
        target_fraction = target / 100.0 if target > 1.0 else target
        fixed_end = float(values["fixed_dr_end_pct"])
        if target_fraction < 0.94 or fixed_end >= 99.0:
            return True

        box = QMessageBox(self)
        box.setWindowTitle(ADDON_TITLE)
        box.setIcon(_message_icon("Warning"))
        box.setText(
            "Fixed DR target baseline is 94% or higher. Consider going to Advanced "
            "and setting fixed_dr_end_pct to 99 so the Fixed-DR comparison covers "
            "the target range."
        )
        run_button = box.addButton("Run anyway", _button_role("AcceptRole"))
        box.addButton("Go back", _button_role("RejectRole"))
        _exec_dialog(box)
        return box.clickedButton() is run_button

    def _refresh_quality_combo(
        self,
        *,
        select_builtin: str | None = None,
        select_custom_id: int | None = None,
    ) -> None:
        self._refreshing_quality = True
        try:
            self.quality_combo.clear()
            for name in BUILTIN_QUALITY_NAMES:
                self.quality_combo.addItem(
                    QUALITY_LABELS.get(name, name),
                    {"kind": "builtin", "key": name},
                )
            for number, preset in enumerate(_custom_quality_presets(), start=1):
                label = _custom_quality_label(number, preset)
                self.quality_combo.addItem(
                    label,
                    {"kind": "custom", "id": int(preset["id"])},
                )

            selected = 0
            for index in range(self.quality_combo.count()):
                data = self.quality_combo.itemData(index)
                if (
                    select_builtin
                    and isinstance(data, dict)
                    and data.get("kind") == "builtin"
                    and data.get("key") == select_builtin
                ):
                    selected = index
                    break
                if (
                    select_custom_id is not None
                    and isinstance(data, dict)
                    and data.get("kind") == "custom"
                    and int(data.get("id", -1)) == select_custom_id
                ):
                    selected = index
                    break
            self.quality_combo.setCurrentIndex(selected)
        finally:
            self._refreshing_quality = False
        self._update_quality_buttons()

    def _current_quality_data(self) -> dict[str, Any]:
        data = self.quality_combo.currentData()
        if isinstance(data, dict):
            return data
        return {"kind": "builtin", "key": DEFAULT_QUALITY}

    def _current_quality_base(self) -> str:
        data = self._current_quality_data()
        if data["kind"] == "custom":
            custom = self._custom_by_id(int(data["id"]))
            base = str(custom.get("base", DEFAULT_QUALITY)) if custom else DEFAULT_QUALITY
            return base if base in QUALITY_PRESET_VALUES else DEFAULT_QUALITY
        key = str(data.get("key", DEFAULT_QUALITY))
        return key if key in QUALITY_PRESET_VALUES else DEFAULT_QUALITY

    def _custom_by_id(self, preset_id: int) -> dict[str, Any] | None:
        for preset in _custom_quality_presets():
            if int(preset.get("id", -1)) == preset_id:
                return preset
        return None

    def _save_custom_quality(self) -> None:
        data = self._current_quality_data()
        values = self._current_custom_values()
        config = _addon_config()
        presets = list(config.get("custom_quality_presets", []))

        if data["kind"] == "custom":
            preset_id = int(data["id"])
            for preset in presets:
                if int(preset.get("id", -1)) == preset_id:
                    preset["values"] = values
                    preset["base"] = self._current_quality_base()
                    break
            config["custom_quality_presets"] = presets
            _write_addon_config(config)
            return

        name, ok = QInputDialog.getText(self, ADDON_TITLE, "Custom preset name")
        if not ok:
            return
        preset_id = _next_custom_preset_id(presets)
        presets.append(
            {
                "id": preset_id,
                "name": str(name).strip(),
                "base": self._current_quality_base(),
                "values": values,
            }
        )
        config["custom_quality_presets"] = presets
        _write_addon_config(config)
        self._refresh_quality_combo(select_custom_id=preset_id)

    def _rename_custom_quality(self) -> None:
        data = self._current_quality_data()
        if data["kind"] != "custom":
            showWarning("Select a custom quality preset to rename.", title=ADDON_TITLE)
            return
        preset_id = int(data["id"])
        config = _addon_config()
        presets = list(config.get("custom_quality_presets", []))
        current = ""
        for preset in presets:
            if int(preset.get("id", -1)) == preset_id:
                current = str(preset.get("name", ""))
                break
        name, ok = QInputDialog.getText(self, ADDON_TITLE, "Custom preset name", text=current)
        if not ok:
            return
        for preset in presets:
            if int(preset.get("id", -1)) == preset_id:
                preset["name"] = str(name).strip()
                break
        config["custom_quality_presets"] = presets
        _write_addon_config(config)
        self._refresh_quality_combo(select_custom_id=preset_id)

    def _delete_custom_quality(self) -> None:
        data = self._current_quality_data()
        if data["kind"] != "custom":
            showWarning("Select a custom quality preset to delete.", title=ADDON_TITLE)
            return
        preset_id = int(data["id"])
        if not _confirm_delete_custom_preset(self):
            return
        config = _addon_config()
        presets = [
            preset
            for preset in config.get("custom_quality_presets", [])
            if int(preset.get("id", -1)) != preset_id
        ]
        config["custom_quality_presets"] = presets
        _write_addon_config(config)
        self._refresh_quality_combo(select_builtin=DEFAULT_QUALITY)
        self._quality_changed()

    def _current_custom_values(self) -> dict[str, Any]:
        values = self._read_all_fields()
        return {
            key: _jsonable_value(values[key])
            for key in CUSTOM_PRESET_FIELDS
            if key in values
        }

    def _update_quality_buttons(self) -> None:
        is_custom = self._current_quality_data().get("kind") == "custom"
        self.save_quality.setText("Save" if is_custom else "Save Custom")
        self.rename_quality.setEnabled(is_custom)
        self.delete_quality.setEnabled(is_custom)


class BatchOptimizeDialog(QDialog):
    def __init__(self, export_path: Path, rows: list[dict[str, Any]]) -> None:
        super().__init__(mw)
        self.export_path = export_path
        self.rows = rows
        self.request: BatchOptimizeRequest | None = None

        self.setWindowTitle(f"{ADDON_TITLE} - Optimize ADR Parameters")
        self.resize(900, 560)

        layout = QVBoxLayout(self)
        file_label = QLabel(f"Button usage file:\n{_display_path(export_path)}", self)
        file_label.setToolTip(str(export_path))
        file_label.setWordWrap(True)
        layout.addWidget(file_label)

        self.table = QTableWidget(len(rows), 5, self)
        self.table.setHorizontalHeaderLabels(
            ["Use", "Deck preset", "DR %", "Scheduling policy", "Search quality"]
        )
        try:
            self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        except AttributeError:
            self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        try:
            header.setStretchLastSection(True)
        except Exception:
            pass
        layout.addWidget(self.table, 1)

        for row_index, row in enumerate(rows):
            checkbox = QCheckBox(self.table)
            checkbox.setChecked(True)
            checkbox_box = QWidget(self.table)
            checkbox_layout = QHBoxLayout(checkbox_box)
            checkbox_layout.setContentsMargins(8, 0, 8, 0)
            checkbox_layout.addWidget(checkbox)
            checkbox_layout.addStretch(1)
            self.table.setCellWidget(row_index, 0, checkbox_box)
            checkbox_box._linear_adr_checkbox = checkbox

            item = QTableWidgetItem(_row_label(row))
            item.setFlags(_readonly_item_flags(item))
            self.table.setItem(row_index, 1, item)

            target = NoWheelDoubleSpinBox(self.table)
            target.setRange(1.0, 99.5)
            target.setDecimals(2)
            target.setSingleStep(1.0)
            target.setKeyboardTracking(False)
            target_value = float(row.get("desired_retention", DEFAULT_VALUES["target_dr"]))
            if target_value <= 1.0:
                target_value *= 100.0
            target.setValue(target_value)
            self.table.setCellWidget(row_index, 2, target)

            selection_combo = QComboBox(self.table)
            for key, label in SCHEDULING_SELECTIONS:
                selection_combo.addItem(label, key)
            self.table.setCellWidget(row_index, 3, selection_combo)

            quality_combo = QComboBox(self.table)
            _populate_quality_combo(quality_combo, select_builtin=DEFAULT_QUALITY)
            self.table.setCellWidget(row_index, 4, quality_combo)

        self.table.resizeColumnsToContents()

        select_buttons = QHBoxLayout()
        select_all = QPushButton("Select all", self)
        clear = QPushButton("Clear", self)
        select_buttons.addWidget(select_all)
        select_buttons.addWidget(clear)
        select_buttons.addStretch(1)
        layout.addLayout(select_buttons)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("Cancel", self)
        run = QPushButton("Run optimizer", self)
        buttons.addWidget(cancel)
        buttons.addWidget(run)
        layout.addLayout(buttons)

        qconnect(select_all.clicked, lambda: self._set_all(True))
        qconnect(clear.clicked, lambda: self._set_all(False))
        qconnect(cancel.clicked, self.reject)
        qconnect(run.clicked, self._accept_request)

    def _set_all(self, checked: bool) -> None:
        for row_index in range(self.table.rowCount()):
            checkbox = self._checkbox(row_index)
            if checkbox is not None:
                checkbox.setChecked(checked)

    def _checkbox(self, row_index: int) -> QCheckBox | None:
        box = self.table.cellWidget(row_index, 0)
        return getattr(box, "_linear_adr_checkbox", None)

    def _accept_request(self) -> None:
        try:
            jobs = self._selected_jobs()
        except Exception as exc:
            showWarning(str(exc), title=ADDON_TITLE)
            return
        if not jobs:
            showWarning("Select at least one preset to optimize.", title=ADDON_TITLE)
            return

        OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        batch_config_path = OUTPUTS_DIR / f"adr-batch-config-{stamp}.json"
        batch_output_path = OUTPUTS_DIR / f"adr-batch-summary-{stamp}.json"
        self.request = BatchOptimizeRequest(
            export_path=self.export_path,
            jobs=jobs,
            batch_config_path=batch_config_path,
            batch_output_path=batch_output_path,
        )
        self.accept()

    def _selected_jobs(self) -> list[dict[str, Any]]:
        jobs = []
        for row_index, row in enumerate(self.rows):
            checkbox = self._checkbox(row_index)
            if checkbox is None or not checkbox.isChecked():
                continue
            preset = row.get("deck_preset", {})
            preset_name = str(preset.get("name") or "").strip()
            if not preset_name:
                raise ValueError(f"Row {row_index + 1} does not include a deck preset name.")

            target_widget = self.table.cellWidget(row_index, 2)
            selection_combo = self.table.cellWidget(row_index, 3)
            quality_combo = self.table.cellWidget(row_index, 4)
            if not isinstance(target_widget, QDoubleSpinBox):
                raise ValueError("Target DR widget missing.")
            if not isinstance(selection_combo, QComboBox):
                raise ValueError("Scheduling policy widget missing.")
            if not isinstance(quality_combo, QComboBox):
                raise ValueError("Quality preset widget missing.")

            selection = str(selection_combo.currentData() or "recommended")
            quality_base, quality_overrides, quality_label = _quality_config_for_combo(
                quality_combo
            )
            job_id = _safe_job_id(preset_name, row_index, selection)
            job: dict[str, Any] = {
                "id": job_id,
                "preset": preset_name,
                "target_dr": float(target_widget.value()),
                "quality_preset": quality_base,
                "selection": selection,
                "_row": row,
                "_quality_label": quality_label,
            }
            if quality_overrides:
                job["config"] = quality_overrides
            jobs.append(job)
        return jobs


class CarReviewDialog(QDialog):
    def __init__(
        self,
        request: BatchOptimizeRequest,
        summary: dict[str, Any],
    ) -> None:
        super().__init__(mw)
        self.request = request
        self.summary = summary
        self.policies: list[dict[str, Any]] = []

        self.setWindowTitle(f"{ADDON_TITLE} - Save Car")
        self.resize(980, 560)

        layout = QVBoxLayout(self)
        label = QLabel(
            "Review the policy snapshot to save as a new car. "
            "The optimizer strategy is stored as metadata; the active mode below is what scheduling uses.",
            self,
        )
        label.setWordWrap(True)
        layout.addWidget(label)

        self.table = QTableWidget(len(request.jobs), 7, self)
        self.table.setHorizontalHeaderLabels(
            [
                "Deck preset",
                "Mode",
                "ADR_FLAT",
                "ADR_S_MULTI",
                "ADR_D_MULTI",
                "Fixed DR %",
                "Action",
            ]
        )
        self.table.verticalHeader().setVisible(False)
        layout.addWidget(self.table, 1)

        results = {
            str(result.get("id")): result
            for result in summary.get("results", [])
            if isinstance(result, dict)
        }
        for row_index, job in enumerate(request.jobs):
            result = results.get(str(job["id"]), {})
            point = _selected_point_from_result(result, str(job.get("selection", "recommended")))

            preset_item = QTableWidgetItem(str(job["preset"]))
            preset_item.setFlags(_readonly_item_flags(preset_item))
            self.table.setItem(row_index, 0, preset_item)

            mode_combo = QComboBox(self.table)
            for mode in (
                POLICY_MODE_ADR,
                POLICY_MODE_FIXED_DR,
                POLICY_MODE_NORMAL_ANKI,
            ):
                mode_combo.addItem(POLICY_MODE_LABELS[mode], mode)
            if point is None:
                mode_combo.setCurrentIndex(1)
            self.table.setCellWidget(row_index, 1, mode_combo)

            flat = NoWheelDoubleSpinBox(self.table)
            s_multi = NoWheelDoubleSpinBox(self.table)
            d_multi = NoWheelDoubleSpinBox(self.table)
            for spin in (flat, s_multi, d_multi):
                spin.setRange(-1_000_000.0, 1_000_000.0)
                spin.setDecimals(6)
                spin.setSingleStep(0.01)
                spin.setKeyboardTracking(False)
            if point is not None:
                flat.setValue(float(point["flat"]))
                s_multi.setValue(float(point["s_multi"]))
                d_multi.setValue(float(point["d_multi"]))
            self.table.setCellWidget(row_index, 2, flat)
            self.table.setCellWidget(row_index, 3, s_multi)
            self.table.setCellWidget(row_index, 4, d_multi)

            fixed = NoWheelDoubleSpinBox(self.table)
            fixed.setRange(1.0, 99.5)
            fixed.setDecimals(2)
            fixed.setSingleStep(1.0)
            fixed.setKeyboardTracking(False)
            fixed.setValue(float(job.get("target_dr", 90.0)))
            self.table.setCellWidget(row_index, 5, fixed)

            action = QComboBox(self.table)
            action.addItem("Keep", "keep")
            action.addItem("Remove", "remove")
            self.table.setCellWidget(row_index, 6, action)

            self.table.item(row_index, 0).setData(_qt_user_role(), job)

        self.table.resizeColumnsToContents()

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("Cancel", self)
        save = QPushButton("Save car", self)
        buttons.addWidget(cancel)
        buttons.addWidget(save)
        layout.addLayout(buttons)

        qconnect(cancel.clicked, self.reject)
        qconnect(save.clicked, self._accept)

    def _accept(self) -> None:
        try:
            self.policies = self._read_policies()
        except Exception as exc:
            showWarning(str(exc), title=ADDON_TITLE)
            return
        if not self.policies:
            showWarning("No policies to save.", title=ADDON_TITLE)
            return
        self.accept()

    def _read_policies(self) -> list[dict[str, Any]]:
        policies = []
        for row_index in range(self.table.rowCount()):
            item = self.table.item(row_index, 0)
            job = item.data(_qt_user_role()) if item is not None else None
            if not isinstance(job, dict):
                continue
            row = job.get("_row", {})
            preset = row.get("deck_preset", {}) if isinstance(row, dict) else {}
            mode_combo = self.table.cellWidget(row_index, 1)
            fixed_widget = self.table.cellWidget(row_index, 5)
            if not isinstance(mode_combo, QComboBox):
                raise ValueError("Mode widget missing.")
            if not isinstance(fixed_widget, QDoubleSpinBox):
                raise ValueError("Fixed DR widget missing.")
            action_combo = self.table.cellWidget(row_index, 6)
            if isinstance(action_combo, QComboBox) and action_combo.currentData() == "remove":
                continue
            mode = str(mode_combo.currentData() or POLICY_MODE_ADR)
            policy: dict[str, Any] = {
                "preset_id": _optional_int(preset.get("id")),
                "preset_name": str(preset.get("name") or job.get("preset") or ""),
                "deck_ids": [
                    int(deck.get("id"))
                    for deck in row.get("decks", [])
                    if isinstance(deck, dict) and deck.get("id") is not None
                ],
                "mode": mode,
                "fixed_dr": float(fixed_widget.value()) / 100.0,
                "adr": None,
                "optimizer_metadata": {
                    "selection": job.get("selection"),
                    "selection_label": SELECTION_LABELS.get(str(job.get("selection")), ""),
                    "quality_preset": job.get("quality_preset"),
                    "quality_label": job.get("_quality_label"),
                    "target_dr": job.get("target_dr"),
                    "export_path": str(self.request.export_path),
                    "job_id": job.get("id"),
                    "summary_path": str(self.request.batch_output_path),
                },
            }
            if mode == POLICY_MODE_ADR:
                flat = self.table.cellWidget(row_index, 2)
                s_multi = self.table.cellWidget(row_index, 3)
                d_multi = self.table.cellWidget(row_index, 4)
                if not all(isinstance(widget, QDoubleSpinBox) for widget in (flat, s_multi, d_multi)):
                    raise ValueError("ADR parameter widgets missing.")
                policy["adr"] = {
                    "flat": float(flat.value()),  # type: ignore[union-attr]
                    "s_multi": float(s_multi.value()),  # type: ignore[union-attr]
                    "d_multi": float(d_multi.value()),  # type: ignore[union-attr]
                }
            policies.append(policy)
        return policies


class ManageCarsDialog(QDialog):
    def __init__(self) -> None:
        super().__init__(mw)
        self.setWindowTitle(f"{ADDON_TITLE} - Manage cars")
        self.resize(920, 600)

        layout = QVBoxLayout(self)
        self.table = QTableWidget(0, 5, self)
        self.table.setHorizontalHeaderLabels(
            ["Car", "Created", "Position date", "Policies", "Source"]
        )
        self.table.verticalHeader().setVisible(False)
        try:
            self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
            self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        except AttributeError:
            self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
            self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        layout.addWidget(self.table, 1)

        car_buttons = QHBoxLayout()
        self.move_position = QPushButton("Move position date", self)
        self.drive_beginning = QPushButton("Drive to beginning", self)
        self.delete_car = QPushButton("Delete", self)
        car_buttons.addWidget(self.move_position)
        car_buttons.addWidget(self.drive_beginning)
        car_buttons.addWidget(self.delete_car)
        car_buttons.addStretch(1)
        layout.addLayout(car_buttons)

        self.track_history = QCheckBox("Track entire ADR optimization history", self)
        self.warn_new = QCheckBox("Warn before creating a new car while another car is active", self)
        self.warn_forward = QCheckBox("Warn before moving a car forward", self)
        self.warn_overtake = QCheckBox("Warn before allowing car overtaking", self)
        self.warn_edit = QCheckBox("Warn before editing saved ADR parameters", self)
        self.soft_cap = QCheckBox("Enable soft interval cap", self)

        settings_grid = QGridLayout()
        settings_grid.addWidget(self.track_history, 0, 0, 1, 2)
        settings_grid.addWidget(self.warn_new, 1, 0, 1, 2)
        settings_grid.addWidget(self.warn_forward, 2, 0, 1, 2)
        settings_grid.addWidget(self.warn_overtake, 3, 0, 1, 2)
        settings_grid.addWidget(self.warn_edit, 4, 0, 1, 2)
        settings_grid.addWidget(self.soft_cap, 5, 0, 1, 2)

        self.soft_threshold = QDoubleSpinBox(self)
        self.soft_threshold.setRange(1.0, 1_000_000.0)
        self.soft_threshold.setDecimals(0)
        self.soft_threshold.setSingleStep(100.0)
        self.soft_power = QDoubleSpinBox(self)
        self.soft_power.setRange(0.01, 0.99)
        self.soft_power.setDecimals(2)
        self.soft_power.setSingleStep(0.05)
        settings_grid.addWidget(QLabel("Soft cap threshold", self), 6, 0)
        settings_grid.addWidget(self.soft_threshold, 6, 1)
        settings_grid.addWidget(QLabel("Soft cap power", self), 7, 0)
        settings_grid.addWidget(self.soft_power, 7, 1)
        layout.addLayout(settings_grid)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        save = QPushButton("Save settings", self)
        close = QPushButton("Close", self)
        buttons.addWidget(save)
        buttons.addWidget(close)
        layout.addLayout(buttons)

        qconnect(self.move_position.clicked, self._move_selected)
        qconnect(self.drive_beginning.clicked, self._drive_selected_to_beginning)
        qconnect(self.delete_car.clicked, self._delete_selected)
        qconnect(save.clicked, self._save_settings)
        qconnect(close.clicked, self.accept)

        self._load_settings()
        self._refresh_table()

    def _load_settings(self) -> None:
        config = _addon_config()
        self.track_history.setChecked(bool(config.get("track_adr_optimization_history", True)))
        self.warn_new.setChecked(bool(config.get("warn_before_creating_new_car_when_active_exists", True)))
        self.warn_forward.setChecked(bool(config.get("warn_before_moving_car_forward", True)))
        self.warn_overtake.setChecked(bool(config.get("warn_before_allowing_car_overtaking", True)))
        self.warn_edit.setChecked(bool(config.get("warn_before_editing_saved_adr_parameters", True)))
        self.soft_cap.setChecked(bool(config.get("enable_soft_interval_cap", True)))
        self.soft_threshold.setValue(float(config.get("soft_interval_cap_threshold", 1000.0)))
        self.soft_power.setValue(float(config.get("soft_interval_cap_power", 0.5)))

    def _save_settings(self) -> None:
        config = _addon_config()
        config["track_adr_optimization_history"] = self.track_history.isChecked()
        config["warn_before_creating_new_car_when_active_exists"] = self.warn_new.isChecked()
        config["warn_before_moving_car_forward"] = self.warn_forward.isChecked()
        config["warn_before_allowing_car_overtaking"] = self.warn_overtake.isChecked()
        config["warn_before_editing_saved_adr_parameters"] = self.warn_edit.isChecked()
        config["enable_soft_interval_cap"] = self.soft_cap.isChecked()
        config["soft_interval_cap_threshold"] = float(self.soft_threshold.value())
        config["soft_interval_cap_power"] = float(self.soft_power.value())
        _write_addon_config(config)
        _show_info("ADR car settings saved.")

    def _refresh_table(self) -> None:
        cars = _active_cars()
        self.table.setRowCount(len(cars))
        for row_index, car in enumerate(cars):
            items = [
                _short_car_id(car),
                str(car.get("created_at", "")),
                str(car.get("position_date", "")),
                _policy_summary(car),
                str(car.get("created_by", "")),
            ]
            for col, text in enumerate(items):
                item = QTableWidgetItem(text)
                item.setFlags(_readonly_item_flags(item))
                if col == 0:
                    item.setData(_qt_user_role(), str(car.get("id", "")))
                self.table.setItem(row_index, col, item)
        self.table.resizeColumnsToContents()

    def _selected_car_id(self) -> str | None:
        selected = self.table.selectedItems()
        if not selected:
            return None
        row = selected[0].row()
        item = self.table.item(row, 0)
        if item is None:
            return None
        car_id = item.data(_qt_user_role())
        return str(car_id) if car_id else None

    def _selected_car(self) -> dict[str, Any] | None:
        car_id = self._selected_car_id()
        if not car_id:
            return None
        for car in _active_cars():
            if str(car.get("id")) == car_id:
                return car
        return None

    def _move_selected(self) -> None:
        car = self._selected_car()
        if car is None:
            showWarning("Select a car first.", title=ADDON_TITLE)
            return
        current = str(car.get("position_date", dt.date.today().isoformat()))
        text, ok = QInputDialog.getText(
            self,
            ADDON_TITLE,
            "Position date (YYYY-MM-DD)",
            text=current,
        )
        if not ok:
            return
        new_date = str(text).strip()
        try:
            parsed_new = dt.date.fromisoformat(new_date)
            parsed_old = dt.date.fromisoformat(current[:10])
        except ValueError:
            showWarning("Use YYYY-MM-DD for the position date.", title=ADDON_TITLE)
            return

        config = _addon_config()
        if parsed_new > parsed_old and bool(config.get("warn_before_moving_car_forward", True)):
            if not _confirm("This moves the car forward. Continue?"):
                return
        if bool(config.get("warn_before_allowing_car_overtaking", True)):
            if _would_overtake(car, parsed_old, parsed_new) and not _confirm(
                "This position change crosses another active car. Continue?"
            ):
                return

        _update_active_car(str(car.get("id")), {"position_date": parsed_new.isoformat()})
        self._refresh_table()

    def _drive_selected_to_beginning(self) -> None:
        car = self._selected_car()
        if car is None:
            showWarning("Select a car first.", title=ADDON_TITLE)
            return
        if not _confirm("Drive this car to the beginning and remove it from active scheduling?"):
            return
        _archive_active_car(str(car.get("id")), reason="graduated")
        self._refresh_table()

    def _delete_selected(self) -> None:
        car = self._selected_car()
        if car is None:
            showWarning("Select a car first.", title=ADDON_TITLE)
            return
        if not _confirm("Delete this active car?"):
            return
        _archive_active_car(str(car.get("id")), reason="deleted")
        self._refresh_table()


def config_for_quality(name: str) -> dict[str, Any]:
    if name not in QUALITY_PRESET_VALUES:
        name = DEFAULT_QUALITY
    config = dict(DEFAULT_VALUES)
    config.update(FULL_HORIZON_VALUES)
    config.update(QUALITY_PRESET_VALUES[name])
    return config


def _populate_quality_combo(
    combo: QComboBox,
    *,
    select_builtin: str | None = None,
    select_custom_id: int | None = None,
) -> None:
    combo.clear()
    for name in BUILTIN_QUALITY_NAMES:
        combo.addItem(
            QUALITY_LABELS.get(name, name),
            {"kind": "builtin", "key": name},
        )
    for number, preset in enumerate(_custom_quality_presets(), start=1):
        combo.addItem(
            _custom_quality_label(number, preset),
            {"kind": "custom", "id": int(preset["id"])},
        )

    selected = 0
    for index in range(combo.count()):
        data = combo.itemData(index)
        if (
            select_builtin
            and isinstance(data, dict)
            and data.get("kind") == "builtin"
            and data.get("key") == select_builtin
        ):
            selected = index
            break
        if (
            select_custom_id is not None
            and isinstance(data, dict)
            and data.get("kind") == "custom"
            and int(data.get("id", -1)) == select_custom_id
        ):
            selected = index
            break
    combo.setCurrentIndex(selected)


def _quality_config_for_combo(combo: QComboBox) -> tuple[str, dict[str, Any], str]:
    data = combo.currentData()
    if not isinstance(data, dict):
        return DEFAULT_QUALITY, {}, QUALITY_LABELS[DEFAULT_QUALITY]
    if data.get("kind") != "custom":
        key = str(data.get("key", DEFAULT_QUALITY))
        if key not in QUALITY_PRESET_VALUES:
            key = DEFAULT_QUALITY
        return key, {}, QUALITY_LABELS.get(key, key)

    preset_id = int(data.get("id", -1))
    number = 0
    custom: dict[str, Any] | None = None
    for index, preset in enumerate(_custom_quality_presets(), start=1):
        if int(preset.get("id", -1)) == preset_id:
            custom = preset
            number = index
            break
    if custom is None:
        return DEFAULT_QUALITY, {}, QUALITY_LABELS[DEFAULT_QUALITY]

    base = str(custom.get("base", DEFAULT_QUALITY))
    if base not in QUALITY_PRESET_VALUES:
        base = DEFAULT_QUALITY
    overrides = {
        key: _jsonable_value(value)
        for key, value in dict(custom.get("values", {})).items()
        if key in CUSTOM_PRESET_FIELDS
    }
    return base, overrides, _custom_quality_label(number, custom)


def _safe_job_id(preset_name: str, row_index: int, selection: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", preset_name).strip("-").lower()
    safe = safe[:60] or "preset"
    return f"{row_index + 1}-{safe}-{selection}"


def _selected_point_from_result(
    result: dict[str, Any],
    selection: str,
) -> dict[str, float] | None:
    selected = result.get("selected")
    if not isinstance(selected, dict):
        label = SELECTION_TO_RESULT_LABEL.get(selection, "Recommended")
        by_label = result.get("selected_by_label")
        if isinstance(by_label, dict):
            selected = by_label.get(label)
    if not isinstance(selected, dict):
        return None
    try:
        return {
            "flat": float(selected["flat"]),
            "s_multi": float(selected["s_multi"]),
            "d_multi": float(selected["d_multi"]),
        }
    except (KeyError, TypeError, ValueError):
        return None


def _readonly_item_flags(item: QTableWidgetItem) -> Any:
    try:
        return item.flags() & ~Qt.ItemFlag.ItemIsEditable
    except AttributeError:
        return item.flags() & ~Qt.ItemIsEditable


def _qt_user_role() -> Any:
    try:
        return Qt.ItemDataRole.UserRole
    except AttributeError:
        return Qt.UserRole


def _optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _set_field_width(widget: QWidget, width: int, *, fixed: bool) -> None:
    if fixed:
        widget.setMinimumWidth(width)
    widget.setMaximumWidth(width)


def executable_name() -> str:
    return "adr-optimizer.exe" if os.name == "nt" else "adr-optimizer"


def platform_artifact_name() -> str | None:
    system = platform.system().lower()
    machine = platform.machine().lower()
    if system == "windows" and machine in {"amd64", "x86_64"}:
        return "adr-optimizer-windows-x86_64"
    if system == "darwin" and machine in {"arm64", "aarch64"}:
        return "adr-optimizer-macos-aarch64"
    if system == "darwin" and machine in {"amd64", "x86_64"}:
        return "adr-optimizer-macos-x86_64"
    if system == "linux" and machine in {"amd64", "x86_64"}:
        return "adr-optimizer-linux-x86_64"
    return None


def candidate_binaries() -> list[Path]:
    exe = executable_name()
    candidates: list[Path] = []
    env_path = os.environ.get(ENV_BINARY)
    if env_path:
        candidates.append(Path(env_path))

    candidates.append(ADDON_ROOT / "helper" / exe)
    artifact = platform_artifact_name()
    if artifact is not None:
        candidates.append(ADDON_ROOT / "helper" / artifact / exe)
    return candidates


def resolve_binary() -> Path:
    for path in candidate_binaries():
        if path.exists():
            if os.name != "nt":
                path.chmod(path.stat().st_mode | 0o755)
            return path
    searched = "\n".join(f"  {path}" for path in candidate_binaries())
    raise OptimizerBinaryNotFound(
        "No adr-optimizer binary found. Searched:\n" + searched
    )


def run_optimizer_subprocess(
    request: SimulationRequest,
    out_queue: queue.Queue[tuple[str, Any]],
) -> OptimizerRunResult:
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    before = {path.resolve() for path in OUTPUTS_DIR.glob("*.json")}
    started = time.time()
    binary = resolve_binary()
    args = build_optimizer_args(request)
    cmd = [str(binary), *args]
    out_queue.put(("log", "Command:\n" + _format_command(cmd) + "\n\n"))

    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    proc = subprocess.Popen(
        cmd,
        cwd=str(ADDON_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=creationflags,
    )

    output_parts: list[str] = []
    assert proc.stdout is not None
    for line in proc.stdout:
        output_parts.append(line)
        out_queue.put(("log", line))
    returncode = int(proc.wait())
    output = "".join(output_parts)
    result = _result_from_output(output, returncode)
    if result is not None:
        return result

    discovered = _discover_latest_summary(before, started)
    if discovered is not None:
        return OptimizerRunResult(
            summary_path=discovered,
            plot_path=_matching_plot_path(discovered),
            returncode=returncode,
            partial=returncode != 0,
        )

    missing = OUTPUTS_DIR / "missing-summary.json"
    return OptimizerRunResult(
        summary_path=missing,
        plot_path=None,
        returncode=returncode,
        partial=returncode != 0,
    )


def run_batch_optimizer_subprocess(
    request: BatchOptimizeRequest,
    out_queue: queue.Queue[tuple[str, Any]] | None = None,
) -> BatchOptimizeResult:
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    binary = resolve_binary()
    batch_config = _batch_config_for_request(request)
    request.batch_config_path.write_text(
        json.dumps(batch_config, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    cmd = [str(binary), "--batch-config", str(request.batch_config_path)]
    _queue_log(out_queue, "Command:\n" + _format_command(cmd) + "\n\n")
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    proc = subprocess.Popen(
        cmd,
        cwd=str(ADDON_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=creationflags,
    )
    output_parts: list[str] = []
    assert proc.stdout is not None
    for line in proc.stdout:
        output_parts.append(line)
        _queue_log(out_queue, line)
    returncode = int(proc.wait())
    output = "".join(output_parts)
    summary_path = _batch_summary_path_from_output(output) or request.batch_output_path
    return BatchOptimizeResult(
        summary_path=summary_path,
        returncode=returncode,
        output=output,
    )


def _batch_config_for_request(request: BatchOptimizeRequest) -> dict[str, Any]:
    jobs = []
    for job in request.jobs:
        clean = {
            "id": job["id"],
            "preset": job["preset"],
            "target_dr": job["target_dr"],
            "quality_preset": job["quality_preset"],
            "selection": job["selection"],
        }
        if isinstance(job.get("config"), dict) and job["config"]:
            clean["config"] = job["config"]
        jobs.append(clean)
    return {
        "export": str(request.export_path),
        "output_dir": str(OUTPUTS_DIR),
        "batch_output": str(request.batch_output_path),
        "quality_preset": DEFAULT_QUALITY,
        "jobs": jobs,
    }


def _batch_summary_path_from_output(output: str) -> Path | None:
    matches = re.findall(r"(?m)^BatchSummary:\s*(.+?)\s*$", output)
    if not matches:
        return None
    return Path(matches[-1]).expanduser()


def build_optimizer_args(request: SimulationRequest) -> list[str]:
    values = request.values
    args = [
        "--quality-preset",
        request.quality_preset,
        "--export",
        str(request.export_path),
        "--preset",
        request.deck_preset,
        "--target-dr",
        _cli_value(values["target_dr"]),
        "--output-dir",
        str(OUTPUTS_DIR),
    ]

    for key in FIELD_KEYS:
        if key in {"target_dr", "original", "inspect_point", "include_original"}:
            continue
        value = values[key]
        if key == "phase1_expand":
            args.append("--phase1-expand" if bool(value) else "--no-phase1-expand")
        elif key in BOOL_FIELDS:
            if bool(value):
                args.append(_flag_name(key))
        else:
            args.extend([_flag_name(key), _cli_value(value)])

    if bool(values.get("include_original")):
        args.append("--include-original")
        args.append("--original")
        args.extend(_cli_value(part) for part in _triplet_value(values.get("original")))

    for point in _inspect_points_value(values.get("inspect_point")):
        args.append("--inspect-point")
        args.extend(_cli_value(part) for part in point)

    return args


def _flag_name(key: str) -> str:
    return "--" + key.replace("_", "-")


def _cli_value(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.12g}"
    return str(value)


def _format_command(cmd: list[str]) -> str:
    return " ".join(_quote_arg(part) for part in cmd)


def _quote_arg(value: str) -> str:
    if re.search(r"\s", value):
        return '"' + value.replace('"', '\\"') + '"'
    return value


def _result_from_output(output: str, returncode: int) -> OptimizerRunResult | None:
    summary_matches = re.findall(r"(?m)^Summary:\s*(.+?)\s*$", output)
    if not summary_matches:
        return None
    summary_path = Path(summary_matches[-1]).expanduser()
    plot_matches = re.findall(r"(?m)^Plot:\s*(.+?)\s*$", output)
    plot_path = Path(plot_matches[-1]).expanduser() if plot_matches else None
    return OptimizerRunResult(
        summary_path=summary_path,
        plot_path=plot_path,
        returncode=returncode,
        partial=returncode != 0,
    )


def _discover_latest_summary(before: set[Path], started: float) -> Path | None:
    candidates = []
    for path in OUTPUTS_DIR.glob("*.json"):
        resolved = path.resolve()
        if resolved in before:
            continue
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        if mtime >= started - 2.0:
            candidates.append(path)
    if not candidates:
        return None
    candidates.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    return candidates[0]


def _matching_plot_path(summary_path: Path) -> Path | None:
    html = summary_path.with_suffix(".html")
    if html.exists():
        return html
    png = summary_path.with_suffix(".png")
    if png.exists():
        return png
    return None


def _format_tuple_value(value: Any) -> str:
    if value in (None, "", ()):
        return ""
    if isinstance(value, list):
        value = tuple(value)
    if isinstance(value, tuple) and value and isinstance(value[0], (tuple, list)):
        return "; ".join(", ".join(f"{float(part):g}" for part in item) for item in value)
    if isinstance(value, tuple):
        return ", ".join(f"{float(part):g}" for part in value)
    return str(value)


def _read_field_value(key: str, widget: QWidget) -> Any:
    if isinstance(widget, QCheckBox):
        return widget.isChecked()
    if isinstance(widget, QSpinBox):
        return int(widget.value())
    if isinstance(widget, QDoubleSpinBox):
        return float(widget.value())
    if isinstance(widget, QLineEdit):
        text = widget.text().strip()
        if key == "original":
            return _parse_triplet(text, key) if text else DEFAULT_VALUES["original"]
        if key == "inspect_point":
            return _parse_inspect_points(text)
        return text
    raise TypeError(f"Unsupported field widget for {key}")


def _parse_triplet(text: str, key: str) -> tuple[float, float, float]:
    parts = [part for part in re.split(r"[,\s]+", text.strip()) if part]
    if len(parts) != 3:
        raise ValueError(f"{key} must contain exactly three numbers.")
    return tuple(float(part) for part in parts)  # type: ignore[return-value]


def _parse_inspect_points(text: str) -> tuple[tuple[float, float, float], ...]:
    if not text:
        return ()
    chunks = [chunk.strip() for chunk in re.split(r"[;\n]+", text) if chunk.strip()]
    return tuple(_parse_triplet(chunk, "inspect_point") for chunk in chunks)


def _triplet_value(value: Any) -> tuple[float, float, float]:
    if isinstance(value, str):
        return _parse_triplet(value, "original")
    if isinstance(value, list):
        value = tuple(value)
    if isinstance(value, tuple) and len(value) == 3:
        return tuple(float(part) for part in value)  # type: ignore[return-value]
    return DEFAULT_VALUES["original"]


def _inspect_points_value(value: Any) -> tuple[tuple[float, float, float], ...]:
    if isinstance(value, str):
        return _parse_inspect_points(value)
    if not value:
        return ()
    return tuple(_triplet_value(point) for point in value)


def _jsonable_value(value: Any) -> Any:
    if isinstance(value, tuple):
        return [_jsonable_value(part) for part in value]
    if isinstance(value, list):
        return [_jsonable_value(part) for part in value]
    return value


def _load_cars() -> dict[str, Any]:
    default = {
        "schema_version": CAR_SCHEMA_VERSION,
        "active_cars": [],
        "history": [],
        "transactions": [],
    }
    if not CARS_PATH.exists():
        return default
    try:
        data = json.loads(CARS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return default
    if not isinstance(data, dict):
        return default
    data.setdefault("schema_version", CAR_SCHEMA_VERSION)
    if not isinstance(data.get("active_cars"), list):
        data["active_cars"] = []
    if not isinstance(data.get("history"), list):
        data["history"] = []
    if not isinstance(data.get("transactions"), list):
        data["transactions"] = []
    return data


def _write_cars(data: dict[str, Any]) -> None:
    data["schema_version"] = CAR_SCHEMA_VERSION
    CARS_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _make_car(policies: list[dict[str, Any]], export_path: Path) -> dict[str, Any]:
    now = _now_iso()
    event_id = str(uuid.uuid4())
    return {
        "id": str(uuid.uuid4()),
        "created_at": now,
        "position_date": dt.date.today().isoformat(),
        "optimization_event_id": event_id,
        "created_by": "optimizer",
        "export_path": str(export_path),
        "policies": policies,
    }


def _save_new_car(car: dict[str, Any]) -> None:
    data = _load_cars()
    data["active_cars"].append(car)
    _write_cars(data)


def _archive_active_car(
    car_id: str,
    *,
    reason: str,
) -> bool:
    data = _load_cars()
    active = data.get("active_cars", [])
    kept = []
    removed = None
    for car in active:
        if isinstance(car, dict) and str(car.get("id")) == car_id:
            removed = dict(car)
        else:
            kept.append(car)
    if removed is None:
        return False
    if bool(_addon_config().get("track_adr_optimization_history", True)):
        removed["archived_at"] = _now_iso()
        removed["archive_reason"] = reason
        data.setdefault("history", []).append(removed)
    data["active_cars"] = kept
    _write_cars(data)
    return True


def _update_active_car(car_id: str, updates: dict[str, Any]) -> bool:
    data = _load_cars()
    changed = False
    for car in data.get("active_cars", []):
        if isinstance(car, dict) and str(car.get("id")) == car_id:
            car.update(updates)
            changed = True
            break
    if changed:
        _write_cars(data)
    return changed


def _append_car_transaction(transaction: dict[str, Any]) -> None:
    data = _load_cars()
    data.setdefault("transactions", []).append(transaction)
    _write_cars(data)


def _active_cars() -> list[dict[str, Any]]:
    return [
        car for car in _load_cars().get("active_cars", [])
        if isinstance(car, dict)
    ]


def _now_iso() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def _short_car_id(car: dict[str, Any]) -> str:
    return str(car.get("id", ""))[:8] or "(missing)"


def _policy_for_card(
    preset_id: int | None,
    preset_name: str,
    last_review_day: int,
    cars: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    matches = []
    for car in cars if cars is not None else _active_cars():
        position_day = _position_date_to_anki_day(str(car.get("position_date", "")))
        if position_day is None or last_review_day <= position_day:
            continue
        policy = _policy_for_preset(car, preset_id, preset_name)
        if policy is None:
            continue
        matches.append(
            (
                position_day,
                str(car.get("created_at", "")),
                str(car.get("id", "")),
                policy,
            )
        )
    if not matches:
        return None
    matches.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
    return matches[0][3]


def _policy_for_preset(
    car: dict[str, Any],
    preset_id: int | None,
    preset_name: str,
) -> dict[str, Any] | None:
    name_key = preset_name.casefold()
    for policy in car.get("policies", []):
        if not isinstance(policy, dict):
            continue
        stored_id = _optional_int(policy.get("preset_id"))
        if preset_id is not None and stored_id == preset_id:
            return policy
        if str(policy.get("preset_name", "")).casefold() == name_key:
            return policy
    return None


def _position_date_to_anki_day(value: str) -> int | None:
    try:
        position = dt.date.fromisoformat(value[:10])
    except (TypeError, ValueError):
        return None
    return int(mw.col.sched.today + (position - dt.date.today()).days)


def _revlog_id_to_anki_day(revlog_id: int) -> int:
    timestamp = int(revlog_id) // 1000
    return int(math.ceil((timestamp - mw.col.sched.day_cutoff) / 86_400) + mw.col.sched.today)


def _preset_for_deck_id(deck_id: int) -> tuple[int | None, str]:
    try:
        config = mw.col.decks.config_dict_for_deck_id(deck_id)
    except Exception:
        config = None
    if not isinstance(config, dict):
        return None, ""
    preset_id = _optional_int(config.get("id"))
    return preset_id, str(config.get("name") or "")


def _candidate_card_rows(deck_id: int | None = None) -> list[dict[str, Any]]:
    if mw.col is None:
        raise ValueError("No collection is open.")
    where = [
        "c.type = 2",
        "c.queue >= 0",
        "c.odid = 0",
    ]
    if deck_id is not None:
        deck_ids = [int(did) for did in mw.col.decks.deck_and_child_ids(deck_id)]
        if not deck_ids:
            deck_ids = [deck_id]
        where.append(f"c.did in ({','.join(str(did) for did in deck_ids)})")
    sql = f"""
        select c.id, c.did, c.ivl, c.due, max(r.id) as last_revlog_id
        from cards c
        join revlog r on r.cid = c.id
        where {' and '.join(where)}
          and r.ease > 0
          and (r.type < 3 or r.factor != 0)
        group by c.id
    """
    rows = []
    for cid, did, ivl, due, last_revlog_id in mw.col.db.all(sql):
        last_review_day = _revlog_id_to_anki_day(int(last_revlog_id))
        preset_id, preset_name = _preset_for_deck_id(int(did))
        rows.append(
            {
                "cid": int(cid),
                "did": int(did),
                "ivl": int(ivl or 0),
                "due": int(due or 0),
                "last_review_day": last_review_day,
                "preset_id": preset_id,
                "preset_name": preset_name,
            }
        )
    return rows


def _card_target_interval(
    card: Any,
    policy: dict[str, Any],
    config: dict[str, Any],
) -> int | None:
    mode = str(policy.get("mode", POLICY_MODE_ADR))
    if mode == POLICY_MODE_NORMAL_ANKI:
        return None
    memory = _ensure_card_memory_state(card)
    if memory is None:
        return None
    stability = float(memory.stability)
    difficulty = float(memory.difficulty)
    if stability <= 0:
        return None

    if mode == POLICY_MODE_FIXED_DR:
        desired_retention = float(policy.get("fixed_dr", 0.9))
    else:
        adr = policy.get("adr")
        if not isinstance(adr, dict):
            return None
        desired_retention = _linear_adr_dr(
            stability,
            difficulty,
            float(adr.get("flat", 0.0)),
            float(adr.get("s_multi", 0.0)),
            float(adr.get("d_multi", 0.0)),
        )

    decay = -float(getattr(card, "decay", None) or 0.5)
    interval = _next_interval(stability, desired_retention, decay)
    return _soft_power_cap(interval, config)


def _ensure_card_memory_state(card: Any) -> Any | None:
    memory = getattr(card, "memory_state", None)
    if memory is not None and getattr(memory, "stability", None) is not None and getattr(memory, "difficulty", None) is not None:
        return memory
    try:
        memory = mw.col.compute_memory_state(card.id)
    except Exception:
        return None
    if memory is None or getattr(memory, "stability", None) is None or getattr(memory, "difficulty", None) is None:
        return None
    try:
        card.memory_state = memory
    except Exception:
        pass
    if hasattr(memory, "decay") and hasattr(card, "decay"):
        try:
            card.decay = memory.decay
        except Exception:
            pass
    return memory


def _linear_adr_dr(
    stability: float,
    difficulty: float,
    flat: float,
    s_multi: float,
    d_multi: float,
) -> float:
    logit = flat + s_multi * math.log(stability) + d_multi * difficulty
    logit = max(-10.0, min(10.0, logit))
    return max(1e-6, min(0.995, 1.0 / (1.0 + math.exp(-logit))))


def _next_interval(stability: float, desired_retention: float, decay: float) -> int:
    desired_retention = max(1e-6, min(0.995, desired_retention))
    if decay >= 0:
        decay = -0.5
    factor = 0.9 ** (1 / decay) - 1
    interval = stability / factor * (desired_retention ** (1 / decay) - 1)
    return max(1, int(round(interval)))


def _soft_power_cap(interval: int, config: dict[str, Any]) -> int:
    interval = max(1, int(round(interval)))
    if not bool(config.get("enable_soft_interval_cap", True)):
        return interval
    threshold = float(config.get("soft_interval_cap_threshold", 1000.0) or 1000.0)
    power = float(config.get("soft_interval_cap_power", 0.5) or 0.5)
    if threshold <= 0 or interval <= threshold:
        return interval
    power = max(0.01, min(0.99, power))
    excess = interval - threshold
    capped = threshold + excess / math.pow(1.0 + excess, 1.0 - power)
    return max(1, int(round(capped)))


def _update_card_due_interval(card: Any, interval: int, last_review_day: int) -> Any:
    interval = max(1, int(round(interval)))
    card.ivl = interval
    new_due = last_review_day + interval
    if getattr(card, "odid", 0):
        card.odue = new_due if new_due != 0 else 1
    else:
        card.due = new_due
    _write_card_custom_data(card, "linear_adr", "reschedule")
    return card


def _write_card_custom_data(card: Any, key: str, value: Any) -> None:
    try:
        data = json.loads(card.custom_data) if card.custom_data else {}
        if not isinstance(data, dict):
            data = {}
        data[key] = value
        card.custom_data = json.dumps(data, ensure_ascii=False)
    except Exception:
        pass


def _display_path(path: Path, max_chars: int = 92) -> str:
    text = str(path)
    if len(text) <= max_chars:
        return text
    filename = path.name
    parent = path.parent.name
    suffix = f"{parent}\\{filename}" if parent else filename
    room = max_chars - len(suffix) - 3
    if room <= 0:
        return "..." + suffix[-(max_chars - 3):]
    return text[:room] + "..." + suffix


def _row_label(row: dict[str, Any]) -> str:
    preset = row.get("deck_preset", {})
    name = preset.get("name") or "Preset"
    deck_count = len(row.get("decks", []))
    dr = row.get("desired_retention", "?")
    return f"{name} ({deck_count} decks, DR {dr})"


def _exec_dialog(dialog: QDialog) -> int:
    execute = getattr(dialog, "exec", None) or getattr(dialog, "exec_", None)
    return int(execute())


def _message_icon(name: str) -> Any:
    enum = getattr(QMessageBox, "Icon", QMessageBox)
    return getattr(enum, name)


def _button_role(name: str) -> Any:
    enum = getattr(QMessageBox, "ButtonRole", QMessageBox)
    return getattr(enum, name)


def _load_export_rows(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    if not rows:
        raise ValueError(f"No preset rows found in {path}.")
    return rows


def _pick_export_file() -> Path | None:
    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
    filename, _ = QFileDialog.getOpenFileName(
        mw,
        "Pick a button usage file",
        str(EXPORTS_DIR),
        "ADR input files (adr-input-*.jsonl *.jsonl);;JSONL files (*.jsonl);;All files (*)",
    )
    return Path(filename) if filename else None


def _simulate_with_export() -> None:
    export_path = _pick_export_file()
    if export_path is None:
        return
    try:
        rows = _load_export_rows(export_path)
    except Exception as exc:
        showWarning(str(exc), title=ADDON_TITLE)
        return

    dialog = SimulationDialog(export_path, rows)
    if _exec_dialog(dialog) != 1 or dialog.request is None:
        return

    run_dialog = RunProgressDialog(dialog.request)
    mw._linear_adr_run_dialog = run_dialog

    def forget_run_dialog(*_args: Any) -> None:
        if getattr(mw, "_linear_adr_run_dialog", None) is run_dialog:
            mw._linear_adr_run_dialog = None

    qconnect(run_dialog.finished, forget_run_dialog)
    run_dialog.show()
    run_dialog.raise_()
    run_dialog.activateWindow()


def _review_batch_optimizer_result(
    request: BatchOptimizeRequest,
    result: BatchOptimizeResult,
) -> bool:
    if result.returncode != 0 and not result.summary_path.exists():
        showWarning(
            "Batch optimizer failed.\n\n" + _tail_text(result.output),
            title=ADDON_TITLE,
        )
        return False
    if not result.summary_path.exists():
        showWarning(
            "Batch optimizer finished but no batch summary was found.\n\n"
            + _tail_text(result.output),
            title=ADDON_TITLE,
        )
        return False
    try:
        summary = json.loads(result.summary_path.read_text(encoding="utf-8"))
    except Exception as exc:
        showWarning(f"Could not read batch summary:\n{exc}", title=ADDON_TITLE)
        return False

    review = CarReviewDialog(request, summary)
    if _exec_dialog(review) != 1:
        return False
    try:
        car = _make_car(review.policies, request.export_path)
        _save_new_car(car)
    except Exception as exc:
        showWarning(f"Could not save car:\n{exc}", title=ADDON_TITLE)
        return False
    _show_info(
        f"Saved car {_short_car_id(car)} with {len(review.policies)} preset policy snapshots."
    )
    return True


def _optimize_adr_parameters() -> None:
    export_path = _pick_export_file()
    if export_path is None:
        return
    try:
        rows = _load_export_rows(export_path)
    except Exception as exc:
        showWarning(str(exc), title=ADDON_TITLE)
        return

    if _active_cars() and bool(
        _addon_config().get("warn_before_creating_new_car_when_active_exists", True)
    ):
        if not _confirm("There is already an active car. Create another car anyway?"):
            return

    dialog = BatchOptimizeDialog(export_path, rows)
    if _exec_dialog(dialog) != 1 or dialog.request is None:
        return
    request = dialog.request

    run_dialog = BatchRunProgressDialog(request)
    mw._linear_adr_batch_run_dialog = run_dialog

    def forget_run_dialog(*_args: Any) -> None:
        if getattr(mw, "_linear_adr_batch_run_dialog", None) is run_dialog:
            mw._linear_adr_batch_run_dialog = None

    qconnect(run_dialog.finished, forget_run_dialog)
    run_dialog.show()
    run_dialog.raise_()
    run_dialog.activateWindow()


def _manage_cars() -> None:
    dialog = ManageCarsDialog()
    _exec_dialog(dialog)


def _generate_combined_filtered_deck() -> None:
    _generate_filtered_deck_for_scope(deck_id=None)


def _generate_filtered_deck_for_scope(deck_id: int | None) -> None:
    if not _active_cars():
        showWarning("No active cars found. Optimize ADR parameters first.", title=ADDON_TITLE)
        return
    options = _prompt_filtered_deck_options(deck_id)
    if options is None:
        return

    def task() -> dict[str, Any]:
        return _collect_filtered_deck_cards(deck_id, options)

    def done(future: Any) -> None:
        try:
            result = future.result()
        except Exception as exc:
            showWarning(str(exc), title=ADDON_TITLE)
            return
        card_ids = result.get("card_ids", [])
        if not card_ids:
            _show_info("No cards are due under the active car policies.")
            return
        _create_filtered_deck_from_card_ids(card_ids, options, result.get("car_moves", []))

    mw.taskman.with_progress(
        task,
        done,
        parent=mw,
        label="Finding ADR due cards...",
        immediate=True,
        title=ADDON_TITLE,
    )


def _reschedule_all_cards_for_scope(deck_id: int | None) -> None:
    if not _active_cars():
        showWarning("No active cars found. Optimize ADR parameters first.", title=ADDON_TITLE)
        return
    scope = _deck_scope_label(deck_id)
    if not _confirm(f"Reschedule review cards for {scope} using active car policies?"):
        return

    def task() -> dict[str, Any]:
        return _reschedule_cards(deck_id)

    def done(future: Any) -> None:
        try:
            result = future.result()
        except Exception as exc:
            showWarning(str(exc), title=ADDON_TITLE)
            return
        try:
            mw.reset()
        except Exception:
            pass
        _show_info(
            "ADR reschedule complete.\n\n"
            f"Updated cards: {result['updated']}\n"
            f"Skipped cards: {result['skipped']}"
        )

    mw.taskman.with_progress(
        task,
        done,
        parent=mw,
        label="Rescheduling with Linear ADR...",
        immediate=True,
        title=ADDON_TITLE,
    )


def _show_result(result: OptimizerRunResult, log: str) -> None:
    dialog = QDialog(mw)
    dialog.setWindowTitle(ADDON_TITLE)
    dialog.resize(980, 640)
    layout = QVBoxLayout(dialog)

    web = AnkiWebView(parent=dialog, title=ADDON_TITLE)
    web.setZoomFactor(0.80)
    plot_problem = _plot_report_problem(result)
    if plot_problem is None and result.plot_path is not None:
        _set_report_html(web, result.plot_path)
    else:
        web.setHtml(_plot_report_fallback_html(result, log, plot_problem))
        showWarning(plot_problem or "No HTML plot report was found.", title=ADDON_TITLE)
    layout.addWidget(web, 1)

    plot_text = str(result.plot_path) if result.plot_path is not None else "(not found)"
    path_label = QLabel(
        f"Plot: {plot_text}\nSummary: {result.summary_path}",
        dialog,
    )
    path_label.setWordWrap(True)
    layout.addWidget(path_label)

    buttons = QHBoxLayout()
    buttons.addStretch(1)
    open_folder = QPushButton("Open output folder", dialog)
    close = QPushButton("Close", dialog)
    buttons.addWidget(open_folder)
    buttons.addWidget(close)
    layout.addLayout(buttons)

    qconnect(open_folder.clicked, _open_output_folder)
    qconnect(close.clicked, dialog.accept)
    _exec_dialog(dialog)


def _set_report_html(web: AnkiWebView, plot_path: Path) -> None:
    report_path = plot_path.resolve()
    report_html = report_path.read_text(encoding="utf-8")
    addon = mw.addonManager.addonFromModule(__name__)
    asset_base = f"/_addons/{addon}/outputs/adr_plot_assets/"
    report_html = re.sub(
        r'((?:href|src)=["\'])adr_plot_assets/',
        lambda match: match.group(1) + asset_base,
        report_html,
    )
    web.setHtml(report_html)


def _plot_report_problem(result: OptimizerRunResult) -> str | None:
    if result.plot_path is None:
        return "The optimizer did not report an HTML plot path."
    if not result.plot_path.exists():
        return f"The optimizer reported a plot path, but the file was not found:\n{result.plot_path}"
    if result.plot_path.suffix.lower() not in {".html", ".htm"}:
        return f"The optimizer reported a non-HTML plot file:\n{result.plot_path}"
    return None


def _plot_report_fallback_html(
    result: OptimizerRunResult,
    log: str,
    problem: str | None,
) -> str:
    plot_path = str(result.plot_path) if result.plot_path is not None else "(not reported)"
    escaped_problem = html.escape(problem or "No HTML plot report was found.")
    escaped_plot = html.escape(plot_path)
    escaped_summary = html.escape(str(result.summary_path))
    escaped_log = html.escape(_tail_text(log, 1600))
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>ADR Plot Report Missing</title>
  <style>
    body {{
      margin: 0;
      padding: 24px;
      color: #1f2933;
      background: #f5f7fa;
      font: 14px/1.45 "Segoe UI", system-ui, sans-serif;
    }}
    main {{
      max-width: 760px;
    }}
    h1 {{
      margin: 0 0 12px;
      font-size: 20px;
      font-weight: 600;
    }}
    p {{
      margin: 0 0 12px;
    }}
    pre {{
      overflow: auto;
      padding: 12px;
      border: 1px solid #d5dce5;
      background: #fff;
      white-space: pre-wrap;
    }}
  </style>
</head>
<body>
  <main>
    <h1>ADR Plot Report Missing</h1>
    <p>{escaped_problem}</p>
    <pre>Plot: {escaped_plot}
Summary: {escaped_summary}</pre>
    <pre>{escaped_log}</pre>
  </main>
</body>
</html>
"""


def _open_output_folder() -> None:
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    opened = QDesktopServices.openUrl(QUrl.fromLocalFile(str(OUTPUTS_DIR)))
    if not opened:
        showWarning(f"Could not open output folder:\n{OUTPUTS_DIR}", title=ADDON_TITLE)


def _prompt_filtered_deck_options(deck_id: int | None) -> dict[str, Any] | None:
    config = _addon_config()
    dialog = QDialog(mw)
    dialog.setWindowTitle(f"{ADDON_TITLE} - Generate filtered deck")
    dialog.resize(520, 260)
    layout = QVBoxLayout(dialog)
    layout.addWidget(QLabel(f"Scope: {_deck_scope_label(deck_id)}", dialog))

    form = QFormLayout()
    name = QLineEdit(dialog)
    name.setText(_default_filtered_deck_name(deck_id))
    limit = QSpinBox(dialog)
    limit.setRange(1, 50_000)
    limit.setValue(int(config.get("filtered_deck_limit", 100)))
    order = QComboBox(dialog)
    order_labels = _filtered_deck_order_labels()
    for index, label in enumerate(order_labels):
        order.addItem(label, index)
    saved_order = str(config.get("filtered_deck_order", "Due"))
    for index, label in enumerate(order_labels):
        if saved_order.casefold() in label.casefold() or label.casefold() in saved_order.casefold():
            order.setCurrentIndex(index)
            break
    reschedule = QCheckBox(dialog)
    reschedule.setChecked(bool(config.get("filtered_deck_reschedule", True)))
    drive_backward = QCheckBox(dialog)
    drive_backward.setChecked(bool(config.get("filtered_deck_drive_backward", True)))
    form.addRow("Deck name", name)
    form.addRow("Card limit", limit)
    form.addRow("Sort order", order)
    form.addRow("Reschedule reviews", reschedule)
    form.addRow("Drive oldest car backward if needed", drive_backward)
    layout.addLayout(form)

    buttons = QHBoxLayout()
    buttons.addStretch(1)
    cancel = QPushButton("Cancel", dialog)
    generate = QPushButton("Generate", dialog)
    buttons.addWidget(cancel)
    buttons.addWidget(generate)
    layout.addLayout(buttons)

    qconnect(cancel.clicked, dialog.reject)
    qconnect(generate.clicked, dialog.accept)

    if _exec_dialog(dialog) != 1:
        return None
    config["filtered_deck_limit"] = int(limit.value())
    config["filtered_deck_order"] = order.currentText()
    config["filtered_deck_reschedule"] = reschedule.isChecked()
    config["filtered_deck_drive_backward"] = drive_backward.isChecked()
    _write_addon_config(config)
    return {
        "name": name.text().strip() or _default_filtered_deck_name(deck_id),
        "limit": int(limit.value()),
        "order_index": int(order.currentData() or 0),
        "order_label": order.currentText(),
        "reschedule": reschedule.isChecked(),
        "drive_backward": drive_backward.isChecked(),
    }


def _filtered_deck_order_labels() -> list[str]:
    try:
        labels = [str(label) for label in mw.col.sched.filtered_deck_order_labels()]
    except Exception:
        labels = [
            "Oldest seen first",
            "Random",
            "Increasing intervals",
            "Decreasing intervals",
            "Most lapses",
            "Order added",
            "Order due",
        ]
    return labels or ["Order due"]


def _default_filtered_deck_name(deck_id: int | None) -> str:
    if deck_id is None:
        return "Linear ADR - Combined"
    return f"Linear ADR - {_deck_name(deck_id)}"


def _deck_scope_label(deck_id: int | None) -> str:
    if deck_id is None:
        return "all decks"
    return f"{_deck_name(deck_id)} and child decks"


def _deck_name(deck_id: int) -> str:
    try:
        deck = mw.col.decks.get(deck_id)
    except Exception:
        deck = None
    if isinstance(deck, dict):
        return str(deck.get("name") or deck_id)
    return str(deck_id)


def _collect_filtered_deck_cards(
    deck_id: int | None,
    options: dict[str, Any],
) -> dict[str, Any]:
    config = _addon_config()
    cars = _active_cars()
    today = int(mw.col.sched.today)
    candidate_rows = _candidate_card_rows(deck_id)
    due_rows = []
    skipped = 0
    for row in candidate_rows:
        policy = _policy_for_card(
            row["preset_id"], row["preset_name"], row["last_review_day"], cars
        )
        if policy is None:
            skipped += 1
            continue
        card = mw.col.get_card(row["cid"])
        interval = _card_target_interval(card, policy, config)
        if interval is None:
            skipped += 1
            continue
        adr_due = int(row["last_review_day"]) + int(interval)
        if adr_due <= today:
            due_rows.append(
                {
                    **row,
                    "target_ivl": interval,
                    "target_due": adr_due,
                    "policy_mode": policy.get("mode"),
                }
            )
    _sort_action_rows(due_rows, str(options.get("order_label", "")))
    limit = int(options.get("limit", 100))
    car_moves = []
    if len(due_rows) < limit and bool(options.get("drive_backward", False)):
        extra_rows, move = _extra_rows_from_driving_oldest_car(
            candidate_rows,
            due_rows,
            cars,
            today,
            limit - len(due_rows),
            config,
            str(options.get("order_label", "")),
        )
        due_rows.extend(extra_rows)
        if move:
            car_moves.append(move)
        _sort_action_rows(due_rows, str(options.get("order_label", "")))
    card_ids = [row["cid"] for row in due_rows[:limit]]
    return {
        "card_ids": card_ids,
        "matched": len(due_rows),
        "skipped": skipped,
        "car_moves": car_moves,
    }


def _extra_rows_from_driving_oldest_car(
    candidate_rows: list[dict[str, Any]],
    existing_due_rows: list[dict[str, Any]],
    cars: list[dict[str, Any]],
    today: int,
    needed: int,
    config: dict[str, Any],
    order_label: str,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    if needed <= 0:
        return [], None
    car = _oldest_drive_car(cars)
    if car is None:
        return [], None
    old_position_day = _position_date_to_anki_day(str(car.get("position_date", "")))
    if old_position_day is None:
        return [], None
    existing_cids = {int(row["cid"]) for row in existing_due_rows}
    extra_rows = []
    for row in candidate_rows:
        if int(row["cid"]) in existing_cids:
            continue
        if int(row["last_review_day"]) > old_position_day:
            continue
        policy = _policy_for_preset(car, row["preset_id"], row["preset_name"])
        if policy is None:
            continue
        card = mw.col.get_card(row["cid"])
        interval = _card_target_interval(card, policy, config)
        if interval is None:
            continue
        adr_due = int(row["last_review_day"]) + int(interval)
        if adr_due <= today:
            extra_rows.append(
                {
                    **row,
                    "target_ivl": interval,
                    "target_due": adr_due,
                    "policy_mode": policy.get("mode"),
                }
            )
    if not extra_rows:
        return [], None
    _sort_action_rows(extra_rows, order_label)
    chosen = extra_rows[:needed]
    new_position_day = min(int(row["last_review_day"]) for row in chosen) - 1
    old_position = str(car.get("position_date", ""))
    new_position = _anki_day_to_date(new_position_day)
    move = {
        "car_id": str(car.get("id")),
        "short_id": _short_car_id(car),
        "old_position_date": old_position,
        "new_position_date": new_position,
    }
    return chosen, move


def _oldest_drive_car(cars: list[dict[str, Any]]) -> dict[str, Any] | None:
    valid = []
    for car in cars:
        position_day = _position_date_to_anki_day(str(car.get("position_date", "")))
        if position_day is not None:
            valid.append((position_day, str(car.get("created_at", "")), car))
    if not valid:
        return None
    valid.sort(key=lambda item: (item[0], item[1]))
    return valid[0][2]


def _anki_day_to_date(day: int) -> str:
    delta = int(day) - int(mw.col.sched.today)
    return (dt.date.today() + dt.timedelta(days=delta)).isoformat()


def _sort_action_rows(rows: list[dict[str, Any]], order_label: str) -> None:
    label = order_label.casefold()
    if "random" in label:
        rng = random.Random(int(_addon_config().get("filtered_deck_random_seed", 1234)))
        rng.shuffle(rows)
    elif "decreasing" in label:
        rows.sort(key=lambda row: (int(row.get("target_ivl", row.get("ivl", 0))), row["cid"]), reverse=True)
    elif "increasing" in label:
        rows.sort(key=lambda row: (int(row.get("target_ivl", row.get("ivl", 0))), row["cid"]))
    elif "oldest" in label or "seen" in label:
        rows.sort(key=lambda row: (int(row.get("last_review_day", 0)), row["cid"]))
    elif "latest" in label:
        rows.sort(key=lambda row: row["cid"], reverse=True)
    elif "added" in label:
        rows.sort(key=lambda row: row["cid"])
    else:
        rows.sort(key=lambda row: (int(row.get("target_due", row.get("due", 0))), row["cid"]))


def _create_filtered_deck_from_card_ids(
    card_ids: list[int],
    options: dict[str, Any],
    car_moves: list[dict[str, Any]] | None = None,
) -> None:
    search = "cid:" + ",".join(str(cid) for cid in card_ids)
    try:
        deck = mw.col.sched.get_or_create_filtered_deck(deck_id=0)
        deck.name = str(options.get("name") or "Linear ADR")
        deck.allow_empty = True
        config = deck.config
        config.reschedule = bool(options.get("reschedule", True))
        del config.delays[:]
        del config.search_terms[:]
        config.search_terms.extend(
            [
                FilteredDeckConfig.SearchTerm(
                    search=search,
                    limit=len(card_ids),
                    order=int(options.get("order_index", 0)),
                )
            ]
        )
    except Exception as exc:
        showWarning(f"Could not prepare filtered deck:\n{exc}", title=ADDON_TITLE)
        return

    def success(out: Any) -> None:
        applied_moves = []
        for move in car_moves or []:
            car_id = str(move.get("car_id", ""))
            new_position = str(move.get("new_position_date", ""))
            if car_id and new_position and _update_active_car(car_id, {"position_date": new_position}):
                applied_moves.append(move)
        _append_car_transaction(
            {
                "id": str(uuid.uuid4()),
                "type": "generate_filtered_deck",
                "created_at": _now_iso(),
                "filtered_deck_id": _optional_int(getattr(out, "id", None)),
                "filtered_deck_name": str(options.get("name") or ""),
                "card_ids": [int(cid) for cid in card_ids],
                "car_moves": applied_moves,
                "reviewed_since_creation": False,
            }
        )
        try:
            mw.reset()
        except Exception:
            pass
        move_text = ""
        if applied_moves:
            move_lines = [
                f"Moved car {move.get('short_id')} from {move.get('old_position_date')} to {move.get('new_position_date')}."
                for move in applied_moves
            ]
            move_text = "\n" + "\n".join(move_lines)
        _show_info(f"Generated filtered deck with {len(card_ids)} cards.{move_text}")

    add_or_update_filtered_deck(parent=mw, deck=deck).success(success).run_in_background()


def _reschedule_cards(deck_id: int | None) -> dict[str, int]:
    config = _addon_config()
    cars = _active_cars()
    cards = []
    skipped = 0
    for row in _candidate_card_rows(deck_id):
        policy = _policy_for_card(
            row["preset_id"], row["preset_name"], row["last_review_day"], cars
        )
        if policy is None:
            skipped += 1
            continue
        card = mw.col.get_card(row["cid"])
        interval = _card_target_interval(card, policy, config)
        if interval is None:
            skipped += 1
            continue
        cards.append(_update_card_due_interval(card, interval, row["last_review_day"]))

    if cards:
        undo_entry = mw.col.add_custom_undo_entry("Linear ADR reschedule")
        mw.col.update_cards(cards)
        mw.col.merge_undo_entries(undo_entry)
    return {"updated": len(cards), "skipped": skipped}


def _addon_config() -> dict[str, Any]:
    config = mw.addonManager.getConfig(__name__) or {}
    for key, value in DEFAULT_ADDON_CONFIG.items():
        if key not in config:
            config[key] = value
    if not isinstance(config.get("custom_quality_presets"), list):
        config["custom_quality_presets"] = []
    return config


def _write_addon_config(config: dict[str, Any]) -> None:
    mw.addonManager.writeConfig(__name__, config)


def _custom_quality_presets() -> list[dict[str, Any]]:
    presets = _addon_config().get("custom_quality_presets", [])
    normalized = []
    for preset in presets:
        if not isinstance(preset, dict):
            continue
        if "id" not in preset:
            continue
        normalized.append(preset)
    return normalized


def _next_custom_preset_id(presets: list[dict[str, Any]]) -> int:
    used = [int(preset.get("id", 0)) for preset in presets if isinstance(preset, dict)]
    return (max(used) if used else 0) + 1


def _custom_quality_label(number: int, preset: dict[str, Any]) -> str:
    name = str(preset.get("name", "")).strip()
    if name:
        return f"Custom {number} ({name})"
    return f"Custom {number}"


def _confirm_delete_custom_preset(parent: QWidget) -> bool:
    box = QMessageBox(parent)
    box.setWindowTitle(ADDON_TITLE)
    box.setIcon(_message_icon("Question"))
    box.setText("Delete this custom quality preset?")
    delete_button = box.addButton("Delete", _button_role("DestructiveRole"))
    box.addButton("Cancel", _button_role("RejectRole"))
    _exec_dialog(box)
    return box.clickedButton() is delete_button


def _confirm(text: str) -> bool:
    box = QMessageBox(mw)
    box.setWindowTitle(ADDON_TITLE)
    box.setIcon(_message_icon("Question"))
    box.setText(text)
    yes_button = box.addButton("Continue", _button_role("AcceptRole"))
    box.addButton("Cancel", _button_role("RejectRole"))
    _exec_dialog(box)
    return box.clickedButton() is yes_button


def _show_info(text: str) -> None:
    box = QMessageBox(mw)
    box.setWindowTitle(ADDON_TITLE)
    box.setIcon(_message_icon("Information"))
    box.setText(text)
    box.addButton("OK", _button_role("AcceptRole"))
    _exec_dialog(box)


def _tail_text(text: str, max_chars: int = 4000) -> str:
    text = text.strip()
    if len(text) <= max_chars:
        return text
    return "...\n" + text[-max_chars:]


def _policy_summary(car: dict[str, Any]) -> str:
    counts: dict[str, int] = {}
    for policy in car.get("policies", []):
        if not isinstance(policy, dict):
            continue
        mode = str(policy.get("mode", POLICY_MODE_ADR))
        label = POLICY_MODE_LABELS.get(mode, mode)
        counts[label] = counts.get(label, 0) + 1
    if not counts:
        return "0 policies"
    return ", ".join(f"{label}: {count}" for label, count in counts.items())


def _would_overtake(
    moving_car: dict[str, Any],
    old_date: dt.date,
    new_date: dt.date,
) -> bool:
    car_id = str(moving_car.get("id"))
    if old_date == new_date:
        return False
    low = min(old_date, new_date)
    high = max(old_date, new_date)
    for car in _active_cars():
        if str(car.get("id")) == car_id:
            continue
        try:
            other = dt.date.fromisoformat(str(car.get("position_date", ""))[:10])
        except ValueError:
            continue
        if low < other <= high:
            return True
    return False


def _install_menu() -> None:
    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    mw.addonManager.setWebExports(__name__, r"outputs/adr_plot_assets/.*(css|js)")

    if getattr(mw, "_linear_adr_optimizer_menu", None) is not None:
        return

    adr_menu = QMenu("ADR", mw)
    menubar = mw.form.menubar
    help_action = mw.form.menuHelp.menuAction()
    menubar.insertMenu(help_action, adr_menu)

    write_action = QAction("Write button usage file", mw)
    optimize_action = QAction("Optimize ADR Parameters", mw)
    manage_cars_action = QAction("Manage cars 🚙", mw)
    combined_filtered_action = QAction("Generate combined filtered deck", mw)
    simulate_action = QAction("Draw pareto plot for one preset", mw)
    qconnect(write_action.triggered, prompt_and_export)
    qconnect(optimize_action.triggered, _optimize_adr_parameters)
    qconnect(manage_cars_action.triggered, _manage_cars)
    qconnect(combined_filtered_action.triggered, _generate_combined_filtered_deck)
    qconnect(simulate_action.triggered, _simulate_with_export)
    adr_menu.addAction(write_action)
    adr_menu.addSeparator()
    adr_menu.addAction(optimize_action)
    adr_menu.addAction(manage_cars_action)
    adr_menu.addAction(combined_filtered_action)
    adr_menu.addSeparator()
    adr_menu.addAction(simulate_action)
    mw._linear_adr_optimizer_menu = adr_menu

    if not getattr(mw, "_linear_adr_deck_gear_hook_installed", False):
        gui_hooks.deck_browser_will_show_options_menu.append(_add_deck_gear_actions)
        mw._linear_adr_deck_gear_hook_installed = True


def _add_deck_gear_actions(menu: QMenu, deck_id: int) -> None:
    submenu = menu.addMenu("ADR Helper")
    generate_action = QAction("Generate filtered deck 🚙", submenu)
    reschedule_action = QAction("Reschedule all cards 🚙", submenu)
    qconnect(generate_action.triggered, lambda _checked=False, did=deck_id: _generate_filtered_deck_for_scope(int(did)))
    qconnect(reschedule_action.triggered, lambda _checked=False, did=deck_id: _reschedule_all_cards_for_scope(int(did)))
    submenu.addAction(generate_action)
    submenu.addAction(reschedule_action)


if mw is not None:
    _install_menu()
