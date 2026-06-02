from __future__ import annotations

import contextlib
import io
import json
import os
import platform
import queue
import re
import subprocess
import threading
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aqt import mw
from aqt.qt import (
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
    QTimer,
    QUrl,
    QVBoxLayout,
    QWidget,
    qconnect,
)
from aqt.utils import showWarning
from aqt.webview import AnkiWebView

from .adr_button_usage import prompt_and_export


ADDON_TITLE = "Linear ADR"
ADDON_ROOT = Path(__file__).resolve().parent
EXPORTS_DIR = ADDON_ROOT / "exports"
OUTPUTS_DIR = ADDON_ROOT / "outputs"

ENV_BINARY = "ADR_OPTIMIZER_BINARY"

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
    "aggressive_calm_regret_pct",
    "ignore_safety",
    "safety_checks",
    "safety_s_max",
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


class SimulationDialog(QDialog):
    def __init__(self, export_path: Path, rows: list[dict[str, Any]]) -> None:
        super().__init__(mw)
        self.export_path = export_path
        self.rows = rows
        self.fields: dict[str, QWidget] = {}
        self.request: SimulationRequest | None = None
        self._refreshing_quality = False

        self.setWindowTitle(ADDON_TITLE)
        self.resize(920, 720)

        layout = QVBoxLayout(self)
        file_label = QLabel(f"Button usage file:\n{export_path}", self)
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
        tabs.addTab(self._make_tab(BASIC_FIELDS), "Settings")
        tabs.addTab(self._make_tab([(name, name) for name in ADVANCED_FIELDS]), "Advanced")
        tabs.addTab(self._make_tab([(name, name) for name in CONTROL_FIELDS]), "Control")
        tabs.addTab(self._make_tab([(name, name) for name in HIDDEN_FIELDS]), "Hidden")
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

    def _make_tab(self, fields: list[tuple[str, str]]) -> QScrollArea:
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        container = QWidget(scroll)
        grid = QGridLayout(container)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(8)

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

            row = index // 2
            col = index % 2
            grid.addWidget(field_box, row, col)

        grid.setRowStretch((len(fields) + 1) // 2, 1)
        scroll.setWidget(container)
        return scroll

    def _make_field(self, key: str) -> QWidget:
        if key in BOOL_FIELDS:
            widget = QCheckBox(self)
            return widget
        if key in INT_FIELDS:
            widget = NoWheelSpinBox(self)
            widget.setRange(0, 2_000_000_000)
            widget.setSingleStep(1)
            widget.setKeyboardTracking(False)
            widget.setMaximumWidth(170)
            return widget
        if key in FLOAT_FIELDS:
            widget = NoWheelDoubleSpinBox(self)
            widget.setRange(-1_000_000.0, 1_000_000.0)
            widget.setDecimals(6)
            widget.setSingleStep(0.01)
            widget.setKeyboardTracking(False)
            widget.setMaximumWidth(170)
            return widget
        widget = QLineEdit(self)
        widget.setMaximumWidth(260)
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


def config_for_quality(name: str) -> dict[str, Any]:
    if name not in QUALITY_PRESET_VALUES:
        name = DEFAULT_QUALITY
    config = dict(DEFAULT_VALUES)
    config.update(FULL_HORIZON_VALUES)
    config.update(QUALITY_PRESET_VALUES[name])
    return config


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


def _show_result(result: OptimizerRunResult, log: str) -> None:
    dialog = QDialog(mw)
    dialog.setWindowTitle(ADDON_TITLE)
    dialog.resize(1180, 780)
    layout = QVBoxLayout(dialog)

    web = AnkiWebView(parent=dialog, title=ADDON_TITLE)
    web.setHtml(_anki_plot_html(result.summary_path))
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


def _anki_plot_html(summary_path: Path) -> str:
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    addon = mw.addonManager.addonFromModule(__name__)
    summary_json = _script_json(summary)
    source_json = _script_json(summary_path.name)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>ADR Pareto Plot</title>
  <link rel="stylesheet" href="/_addons/{addon}/web/adr_plot.css?v=linear-adr-addon-1">
</head>
<body>
  <main class="app-shell">
    <header class="toolbar">
      <div class="title-block">
        <h1>ADR Pareto Plot</h1>
        <p id="summary-title">Loading summary...</p>
      </div>
      <form id="summary-form" class="summary-form">
        <input id="summary-path" name="summary" type="text" autocomplete="off" spellcheck="false" aria-label="Summary JSON path">
        <button type="submit">Load</button>
        <label class="file-button">
          <span>Open JSON</span>
          <input id="summary-file" type="file" accept="application/json,.json">
        </label>
      </form>
    </header>
    <section class="plot-panel">
      <div class="plot-frame">
        <div id="plot" class="plot" aria-label="ADR Pareto Plot"></div>
        <aside id="result-box" class="result-box" aria-label="ADR plot labels"></aside>
      </div>
      <div id="status" class="status" role="status"></div>
    </section>
  </main>
  <script>
    window.ADR_INITIAL_SUMMARY = {summary_json};
    window.ADR_INITIAL_SOURCE = {source_json};
  </script>
  <script src="/_addons/{addon}/web/vendor/plotly.min.js"></script>
  <script src="/_addons/{addon}/web/adr_plot.js?v=linear-adr-addon-1"></script>
</body>
</html>
"""


def _script_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False).replace("</", "<\\/")


def _open_output_folder() -> None:
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    opened = QDesktopServices.openUrl(QUrl.fromLocalFile(str(OUTPUTS_DIR)))
    if not opened:
        showWarning(f"Could not open output folder:\n{OUTPUTS_DIR}", title=ADDON_TITLE)


def _addon_config() -> dict[str, Any]:
    config = mw.addonManager.getConfig(__name__) or {}
    presets = config.get("custom_quality_presets")
    if not isinstance(presets, list):
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


def _install_menu() -> None:
    if getattr(mw, "_linear_adr_optimizer_menu", None) is not None:
        return

    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    mw.addonManager.setWebExports(__name__, r"web/.*(css|js)")

    adr_menu = QMenu("ADR", mw)
    menubar = mw.form.menubar
    help_action = mw.form.menuHelp.menuAction()
    menubar.insertMenu(help_action, adr_menu)

    write_action = QAction("Write button usage file", mw)
    simulate_action = QAction("Simulate Preset with button usage file", mw)
    qconnect(write_action.triggered, prompt_and_export)
    qconnect(simulate_action.triggered, _simulate_with_export)
    adr_menu.addAction(write_action)
    adr_menu.addAction(simulate_action)
    mw._linear_adr_optimizer_menu = adr_menu


if mw is not None:
    _install_menu()
