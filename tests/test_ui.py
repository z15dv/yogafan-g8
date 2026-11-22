from __future__ import annotations

import os
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QAbstractAnimation, QByteArray, QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from control.controller import FanController
from control.fan_curve import BALANCED_GPU_CURVE, DEFAULT_CURVE, CurveProfile
from ec.lenovo_gen8_ec import Telemetry
from ec.mock_backend import MockEcBackend
from ec.protocol import ControlMode
from services.settings import AppSettings
from services.telemetry_worker import TelemetryWorker
from ui.curve_editor import CurveEditor, NoWheelSpinBox
from ui.fan_widget import FanWidget, IntentionalWheelSlider
from ui.main_window import (
    MainWindow,
    format_gpu_temperature,
    lenovo_mode_icon_asset,
)
from ui.telemetry_card import AspectRatioIcon


APP = QApplication.instance() or QApplication([])
PROJECT_ROOT = Path(__file__).resolve().parents[1]


class IgnoredEvent:
    def __init__(self) -> None:
        self.ignored = False

    def ignore(self) -> None:
        self.ignored = True


class MemorySettings:
    def load(self) -> dict:
        return {
            "geometry": QByteArray(),
            "state": QByteArray(),
            "link_fans": True,
            "fan1_rpm": 3000,
            "fan2_rpm": 3000,
            "curve": [list(point) for point in DEFAULT_CURVE],
            "custom_cpu_curve": [list(point) for point in DEFAULT_CURVE],
            "custom_gpu_curve": [list(point) for point in BALANCED_GPU_CURVE],
            "curve_profile": CurveProfile.BALANCED,
        }

    def save(self, _values: dict) -> None:
        pass


class DictQSettings:
    def __init__(self) -> None:
        self.values: dict[str, object] = {}

    def value(self, key: str, default=None, type=None):
        value = self.values.get(key, default)
        return type(value) if type is not None else value

    def setValue(self, key: str, value) -> None:
        self.values[key] = value

    def sync(self) -> None:
        pass


class FakeTray:
    def __init__(self) -> None:
        self.visible = True
        self.messages: list[tuple] = []

    def isVisible(self) -> bool:
        return self.visible

    def showMessage(self, *args) -> None:
        self.messages.append(args)

    def hide(self) -> None:
        self.visible = False


class UiBehaviorTests(unittest.TestCase):
    def _window(self) -> tuple[MainWindow, MockEcBackend, TelemetryWorker]:
        backend = MockEcBackend()
        worker = TelemetryWorker(FanController(lambda: backend))
        with (
            patch("ui.main_window.AppSettings", return_value=MemorySettings()),
            patch.object(MainWindow, "_create_tray", lambda _self: None),
        ):
            window = MainWindow(worker)
        return window, backend, worker

    def test_curve_edits_are_staged_until_apply(self) -> None:
        editor = CurveEditor()
        applied: list[tuple[list[tuple[int, int]], list[tuple[int, int]]]] = []
        editor.apply_requested.connect(lambda cpu, gpu: applied.append((cpu, gpu)))

        editor.table.cellWidget(1, 1).setValue(3100)
        APP.processEvents()
        self.assertEqual(applied, [])
        self.assertTrue(editor.apply_button.isEnabled())

        editor.apply_button.click()
        APP.processEvents()
        self.assertEqual(applied[-1][0][1], (60, 3100))
        self.assertEqual(applied[-1][1], list(BALANCED_GPU_CURVE))

    def test_reset_stages_exact_default_without_applying(self) -> None:
        editor = CurveEditor()
        applied: list[tuple[object, object]] = []
        editor.apply_requested.connect(lambda cpu, gpu: applied.append((cpu, gpu)))
        custom = list(DEFAULT_CURVE)
        custom[0] = (50, 2500)
        editor.set_points(custom)

        editor.reset_button.click()
        APP.processEvents()

        self.assertEqual(editor.points(), list(DEFAULT_CURVE))
        self.assertEqual(editor.applied_points(), custom)
        self.assertEqual(applied, [])
        self.assertTrue(editor.apply_button.isEnabled())

    def test_curve_rpm_snaps_to_100_on_edit_completion(self) -> None:
        editor = CurveEditor()
        fan = editor.table.cellWidget(1, 1)
        fan.setValue(3055)
        editor._snap_fan(1)
        self.assertEqual(fan.value(), 3100)

    def test_invalid_curve_rows_are_highlighted_and_cannot_apply(self) -> None:
        editor = CurveEditor()
        editor.table.cellWidget(1, 0).setValue(50)
        APP.processEvents()

        self.assertTrue(editor.table.cellWidget(0, 0).property("invalid"))
        self.assertTrue(editor.table.cellWidget(1, 1).property("invalid"))
        self.assertFalse(editor.apply_button.isEnabled())

    def test_curve_spinbox_always_ignores_wheel(self) -> None:
        spinbox = NoWheelSpinBox()
        event = IgnoredEvent()
        spinbox.wheelEvent(event)
        self.assertTrue(event.ignored)

    def test_unfocused_slider_ignores_wheel(self) -> None:
        slider = IntentionalWheelSlider()
        slider.clearFocus()
        event = IgnoredEvent()
        slider.wheelEvent(event)
        self.assertTrue(event.ignored)

    def test_auto_slider_follows_actual_without_commands(self) -> None:
        window, backend, worker = self._window()
        while not worker._commands.empty():
            worker._commands.get_nowait()
        window._mode_changed(ControlMode.LENOVO_AUTO.value)
        sample = Telemetry(60, 0, 0, 41, 39)

        window._update_telemetry(sample)
        QTest.qWait(330)

        self.assertEqual(window.fan1.slider.value(), 4100)
        self.assertEqual(window.fan2.slider.value(), 3900)
        self.assertFalse(window.fan1.slider.isEnabled())
        self.assertEqual(window.fan1.target.text(), "Target  —")
        self.assertEqual(window.gpu_value.text(), "N/A")
        self.assertIn("GPU is sleeping", window.gpu_value.toolTip())
        self.assertEqual(backend.events, [])
        self.assertTrue(worker._commands.empty())
        window.deleteLater()

    def test_custom_curve_slider_tracks_target_not_actual(self) -> None:
        window, backend, worker = self._window()
        while not worker._commands.empty():
            worker._commands.get_nowait()
        window._mode_changed(ControlMode.CUSTOM_CURVE.value)
        window._update_telemetry(Telemetry(72, 55, 0, 31, 32))
        window._targets_changed([4300, 4300])
        QTest.qWait(330)

        self.assertEqual(window.fan1.actual.text(), "3100")
        self.assertEqual(window.fan1.actual_unit.text(), "RPM")
        self.assertEqual(window.fan1.slider.value(), 4300)
        self.assertEqual(window.fan1.target.text(), "Target  4300 RPM")
        self.assertEqual(backend.events, [])
        self.assertTrue(worker._commands.empty())
        window.deleteLater()

    def test_telemetry_and_committed_targets_do_not_overwrite_active_drag(self) -> None:
        window, backend, worker = self._window()
        while not worker._commands.empty():
            worker._commands.get_nowait()
        window._mode_changed(ControlMode.MANUAL.value)
        window._connection_changed(True, "Connected")
        window._targets_changed([3000, 3000])
        window.fan1._begin_drag()
        window.fan1.slider.setValue(5000)

        window._update_telemetry(Telemetry(64, 52, 0, 34, 35))
        window._targets_changed([3000, 3000])

        self.assertTrue(window.fan1.is_dragging)
        self.assertEqual(window.fan1.staged_target, 5000)
        self.assertEqual(window.fan1.slider.value(), 5000)
        self.assertEqual(window.fan1.target.text(), "Target  5000 RPM")
        self.assertEqual(window.fan1.actual.text(), "3400")
        self.assertEqual(backend.events, [])
        self.assertTrue(worker._commands.empty())
        window.deleteLater()

    def test_linked_peer_animates_after_drag_release_and_queues_one_pair(self) -> None:
        window, backend, worker = self._window()
        while not worker._commands.empty():
            worker._commands.get_nowait()
        window._mode_changed(ControlMode.MANUAL.value)
        window._targets_changed([3000, 3000])
        window.fan1.set_visual_rpm(3000, animated=False)
        window.fan2.set_visual_rpm(3000, animated=False)

        window.fan1._begin_drag()
        window.fan1.slider.setValue(5000)
        window.fan1._finish_drag()

        self.assertTrue(window.fan2.is_slider_animating())
        self.assertNotEqual(window.fan2.slider.value(), 5000)
        command = worker._commands.get_nowait()
        self.assertEqual(command[0], "manual")
        self.assertEqual(command[-2:], (5000, 5000))
        self.assertTrue(worker._commands.empty())
        self.assertEqual(backend.events, [])

        QTest.qWait(330)
        self.assertEqual(window.fan2.slider.value(), 5000)
        self.assertEqual(window.fan2.target.text(), "Target  5000 RPM")
        window.deleteLater()

    def test_track_click_animates_and_emits_exactly_one_snapped_commit(self) -> None:
        fan = FanWidget("Fan 1")
        fan.resize(620, 225)
        fan.set_control_mode(ControlMode.MANUAL)
        fan.set_controls_enabled(True)
        fan.set_actual(2400)
        fan.set_visual_rpm(2400, animated=False)
        commits: list[int] = []
        fan.target_changed.connect(commits.append)
        fan.show()
        APP.processEvents()

        QTest.mouseClick(
            fan.slider,
            Qt.LeftButton,
            Qt.NoModifier,
            QPoint(round(fan.slider.width() * 0.82), fan.slider.height() // 2),
        )
        APP.processEvents()

        self.assertEqual(len(commits), 1)
        self.assertEqual(commits[0] % 100, 0)
        self.assertTrue(fan.is_slider_animating())
        self.assertEqual(fan.fan_visual.actual_rpm, 2400)
        self.assertEqual(fan.fan_visual.display_rpm, 2400)
        QTest.qWait(330)
        self.assertEqual(fan.slider.value(), commits[0])
        fan.hide()
        fan.deleteLater()

    def test_slider_target_never_drives_measured_rotor(self) -> None:
        fan = FanWidget("Fan 1")
        fan.set_control_mode(ControlMode.MANUAL)
        fan.set_controls_enabled(True)
        fan.set_actual(2400)
        commits: list[int] = []
        fan.target_changed.connect(commits.append)

        fan._begin_drag()
        fan.slider.setValue(5300)
        self.assertEqual(fan.staged_target, 5300)
        self.assertEqual(fan.fan_visual.actual_rpm, 2400)
        self.assertEqual(fan.fan_visual.display_rpm, 2400)
        self.assertEqual(commits, [])

        fan._finish_drag()
        self.assertEqual(commits, [5300])
        self.assertEqual(fan.fan_visual.actual_rpm, 2400)
        self.assertEqual(fan.fan_visual.display_rpm, 2400)

        fan.set_actual(3300)
        fan.fan_visual.advance_for_test(0.05)
        self.assertGreater(fan.fan_visual.display_rpm, 2400)
        self.assertLess(fan.fan_visual.display_rpm, 3300)
        fan.set_actual(5300)
        for _ in range(30):
            fan.fan_visual.advance_for_test(0.05)
        self.assertGreater(fan.fan_visual.display_rpm, 5000)
        fan.deleteLater()

    def test_actual_zero_stops_rotor_and_empties_ring(self) -> None:
        fan = FanWidget("Fan 1")
        fan.set_actual(0)
        rotation = fan.fan_visual.rotation
        fan.fan_visual.advance_for_test(0.05)
        self.assertEqual(fan.actual.text(), "0")
        self.assertEqual(fan.fan_visual.display_rpm, 0)
        self.assertEqual(fan.fan_visual.arc_progress, 0)
        self.assertEqual(fan.fan_visual.rotation, rotation)
        self.assertEqual(fan.fan_visual.visual_rps_for_rpm(0), 0)
        fan.deleteLater()

    def test_actual_ring_and_equal_target_marker_share_one_scale(self) -> None:
        fan = FanWidget("Fan 1")
        for rpm in (1800, 2800, 3700, 4700, 5300):
            fan.set_actual(rpm)
            fan.fan_visual._display_rpm = float(rpm)
            fan.fan_visual.set_target_rpm(rpm)
            self.assertAlmostEqual(
                fan.fan_visual.arc_progress,
                fan.fan_visual.target_marker_progress,
            )
        fan.deleteLater()

    def test_two_fans_follow_independent_tachometer_values(self) -> None:
        first = FanWidget("Fan 1")
        second = FanWidget("Fan 2")
        first.set_actual(2400)
        second.set_actual(4100)
        first.fan_visual.advance_for_test(0.05)
        second.fan_visual.advance_for_test(0.05)
        self.assertEqual(first.fan_visual.actual_rpm, 2400)
        self.assertEqual(second.fan_visual.actual_rpm, 4100)
        self.assertLess(
            first.fan_visual.visual_rps_for_rpm(first.fan_visual.display_rpm),
            second.fan_visual.visual_rps_for_rpm(second.fan_visual.display_rpm),
        )
        first.deleteLater()
        second.deleteLater()

    def test_hidden_fan_visual_has_no_animation_timer(self) -> None:
        fan = FanWidget("Fan 1")
        fan.set_actual(3000)
        fan.hide()
        APP.processEvents()
        self.assertFalse(fan.fan_visual._timer.isActive())
        fan.deleteLater()

    def test_window_close_hides_without_stopping_worker(self) -> None:
        window, _backend, worker = self._window()
        window.tray = FakeTray()
        window.show()
        APP.processEvents()
        window.close()
        APP.processEvents()
        self.assertFalse(window.isVisible())
        self.assertFalse(worker._stop_event.is_set())
        self.assertTrue(window.tray.messages)
        window.deleteLater()

    def test_window_close_exits_when_background_mode_is_off(self) -> None:
        window, _backend, worker = self._window()
        window.tray = FakeTray()
        window.run_in_background.setChecked(False)
        worker.stop = Mock()
        worker.wait = Mock(return_value=True)
        with patch.object(QApplication, "quit", autospec=True) as quit_app:
            window.close()
            APP.processEvents()
        self.assertTrue(window._closing)
        worker.stop.assert_called_once_with()
        quit_app.assert_called_once()
        self.assertFalse(window.tray.visible)
        window.deleteLater()

    def test_live_monitoring_and_csv_logging_status_are_unambiguous(self) -> None:
        window, _backend, _worker = self._window()
        window._connection_changed(True, "Connected")
        self.assertEqual(window.logging_status.text(), "CSV telemetry logging is off")
        self.assertIn("CSV logging off", window.message.text())
        self.assertIn("not being saved", window.message.text())
        window.deleteLater()

    def test_temperature_notifications_can_be_disabled_without_disabling_protection(self) -> None:
        window, _backend, _worker = self._window()
        window.tray = FakeTray()
        window.temperature_notifications.setChecked(False)

        window._update_telemetry(Telemetry(90, 60, 0, 30, 31))
        self.assertEqual(window.tray.messages, [])
        self.assertTrue(window.thermal_protection.isChecked())
        self.assertEqual(window.warning.text(), "High CPU temperature")

        window._update_telemetry(Telemetry(80, 60, 0, 30, 31))
        window.temperature_notifications.setChecked(True)
        window._update_telemetry(Telemetry(90, 60, 0, 30, 31))
        self.assertEqual(len(window.tray.messages), 1)
        window.deleteLater()

    def test_checkbox_styles_never_fall_back_to_black_widget_background(self) -> None:
        window, _backend, _worker = self._window()
        style = window.styleSheet()
        self.assertIn("QCheckBox {\n    background:transparent", style)
        self.assertIn(
            "QCheckBox:hover, QCheckBox:focus, QCheckBox:pressed, QCheckBox:disabled",
            style,
        )
        window.deleteLater()

    def test_thermal_settings_warn_once_and_queue_service_configuration(self) -> None:
        window, _backend, worker = self._window()
        while not worker._commands.empty():
            worker._commands.get_nowait()
        with patch(
            "ui.main_window.QMessageBox.warning",
            # Real PySide builds may return the numeric enum value rather than
            # the exact singleton object.  Value equality must accept both.
            return_value=int(QMessageBox.StandardButton.Yes),
        ) as warning:
            window.cpu_critical_limit.setValue(96)
            window._thermal_settings_changed()
            window.thermal_protection.click()
        warning.assert_called_once()
        first = worker._commands.get_nowait()
        second = worker._commands.get_nowait()
        self.assertEqual(first, ("thermal_protection_settings", True, 96, 85))
        self.assertEqual(second, ("thermal_protection_settings", False, 96, 85))
        self.assertTrue(worker._commands.empty())
        self.assertFalse(window.thermal_protection.isChecked())
        self.assertEqual(window._accepted_thermal_settings, (False, 96, 85))
        window.deleteLater()

    def test_cancelled_thermal_change_restores_last_accepted_state(self) -> None:
        window, _backend, worker = self._window()
        while not worker._commands.empty():
            worker._commands.get_nowait()
        with patch(
            "ui.main_window.QMessageBox.warning",
            return_value=QMessageBox.StandardButton.Cancel,
        ):
            window.thermal_protection.click()
        self.assertTrue(window.thermal_protection.isChecked())
        self.assertEqual(window._accepted_thermal_settings, (True, 95, 85))
        self.assertTrue(worker._commands.empty())
        window.deleteLater()

    def test_tray_open_restores_window(self) -> None:
        window, _backend, _worker = self._window()
        window.tray = FakeTray()
        window.hide()
        window._show_from_tray()
        APP.processEvents()
        self.assertTrue(window.isVisible())
        window.hide()
        window.deleteLater()

    def test_explicit_exit_stops_worker_and_is_not_tray_close(self) -> None:
        window, _backend, worker = self._window()
        window.tray = FakeTray()
        worker.stop = Mock()
        worker.wait = Mock(return_value=True)
        with patch.object(QApplication, "quit", autospec=True) as quit_app:
            window.exit_application()
        worker.stop.assert_called_once_with()
        worker.wait.assert_called_once_with(7000)
        quit_app.assert_called_once()
        self.assertTrue(window._closing)
        self.assertFalse(window.tray.visible)
        window.deleteLater()

    def test_system_shutdown_uses_explicit_exit_path(self) -> None:
        window, _backend, worker = self._window()
        window.tray = FakeTray()
        worker.stop = Mock()
        worker.wait = Mock(return_value=True)
        with patch.object(QApplication, "quit", autospec=True):
            window.request_system_shutdown()
        self.assertTrue(window._session_ending)
        worker.stop.assert_called_once_with()
        window.deleteLater()

    def test_programmatic_slider_animation_never_commits(self) -> None:
        fan = FanWidget("Fan 1")
        commits: list[int] = []
        fan.target_changed.connect(commits.append)
        fan.set_visual_rpm(5000)
        QTest.qWait(330)

        self.assertEqual(fan.slider.value(), 5000)
        self.assertEqual(commits, [])
        fan.deleteLater()

    def test_lenovo_mode_icon_mapping_and_auto_modes_are_informational(self) -> None:
        self.assertEqual(lenovo_mode_icon_asset(0), "assets/modes/intelligent.png")
        self.assertEqual(lenovo_mode_icon_asset(1), "assets/modes/performance.png")
        self.assertEqual(lenovo_mode_icon_asset(2), "assets/leaf.svg")
        self.assertEqual(lenovo_mode_icon_asset(3), "assets/modes/unknown.svg")
        self.assertEqual(lenovo_mode_icon_asset(4), "assets/modes/unknown.svg")
        self.assertEqual(lenovo_mode_icon_asset(255), "assets/modes/unknown.svg")

        window, backend, worker = self._window()
        while not worker._commands.empty():
            worker._commands.get_nowait()
        window._connection_changed(True, "Connected")
        expected = {3: "APF (Auto Performance)", 4: "AQT (Auto Quiet)"}
        for fcmo in (3, 4):
            window._update_telemetry(Telemetry(60, 0, fcmo, 30, 31))
            self.assertEqual(window.lenovo_mode.text(), expected[fcmo])
            self.assertTrue(
                window.mode_card.icon.icon_path.replace("\\", "/").endswith(
                    "assets/modes/unknown.svg"
                )
            )
            self.assertEqual(window.ec_status.text(), "EC status: Connected")
        self.assertEqual(backend.events, [])
        self.assertTrue(worker._commands.empty())
        window.deleteLater()

    def test_supplied_mode_images_render_in_square_keep_aspect_viewport(self) -> None:
        for asset in (
            "assets/modes/intelligent.png",
            "assets/modes/performance.png",
        ):
            icon = AspectRatioIcon(str((PROJECT_ROOT / asset).resolve()), 52)
            APP.processEvents()
            rendered = icon.pixmap()
            self.assertFalse(rendered.isNull())
            self.assertEqual(icon.width(), icon.height())
            self.assertEqual(icon.source_size.width(), icon.source_size.height())
            self.assertEqual(rendered.width(), rendered.height())
            self.assertLessEqual(rendered.width(), icon.width())
            icon.deleteLater()

    def test_mode_buttons_have_distinct_icons_and_clean_active_state(self) -> None:
        window, _backend, _worker = self._window()
        window._mode_changed(ControlMode.MANUAL.value)
        APP.processEvents()

        keys = {button.icon().cacheKey() for button in window.mode_buttons.values()}
        self.assertEqual(len(keys), 3)
        self.assertTrue(window.mode_buttons[ControlMode.MANUAL].isChecked())
        self.assertIn("outline:none", window.styleSheet())
        window.deleteLater()

    def test_slider_styles_explicitly_cover_every_visual_state(self) -> None:
        window, _backend, _worker = self._window()
        style = window.styleSheet()
        for selector in (
            "QSlider {",
            "QSlider:hover, QSlider:pressed, QSlider:focus, QSlider:disabled",
            "QSlider::groove:horizontal {",
            "QSlider::groove:horizontal:hover",
            "QSlider::groove:horizontal:pressed",
            "QSlider::groove:horizontal:focus",
            "QSlider::groove:horizontal:disabled",
            "QSlider::add-page:horizontal {",
            "QSlider::add-page:horizontal:hover",
            "QSlider::add-page:horizontal:pressed",
            "QSlider::add-page:horizontal:focus",
            "QSlider::add-page:horizontal:disabled",
            "QSlider::sub-page:horizontal {",
            "QSlider::sub-page:horizontal:hover",
            "QSlider::sub-page:horizontal:pressed",
            "QSlider::sub-page:horizontal:focus",
            "QSlider::sub-page:horizontal:disabled",
            "QSlider::handle:horizontal {",
            "QSlider::handle:horizontal:hover",
            "QSlider::handle:horizontal:pressed",
            "QSlider::handle:horizontal:focus",
            "QSlider::handle:horizontal:disabled",
        ):
            self.assertIn(selector, style)
        self.assertNotIn("QSlider:disabled::", style)
        window.deleteLater()

    def test_invalid_gpu_temperature_is_presented_as_unavailable(self) -> None:
        self.assertEqual(
            format_gpu_temperature(0),
            ("N/A", "GPU sensor unavailable or GPU is sleeping."),
        )
        self.assertEqual(format_gpu_temperature(55), ("55 °C", ""))

    def test_link_fans_state_persists(self) -> None:
        store = DictQSettings()
        settings = AppSettings(store)
        settings.save({"fans/link": False})
        self.assertFalse(AppSettings(store).load()["link_fans"])
        settings.save({"fans/link": True})
        self.assertTrue(AppSettings(store).load()["link_fans"])

    def test_background_and_thermal_defaults_are_safe_and_persist(self) -> None:
        store = DictQSettings()
        defaults = AppSettings(store).load()
        self.assertTrue(defaults["run_in_background"])
        self.assertTrue(defaults["temperature_notifications_enabled"])
        self.assertTrue(defaults["thermal_protection_enabled"])
        self.assertEqual(defaults["cpu_critical_c"], 95)
        self.assertEqual(defaults["gpu_critical_c"], 85)
        AppSettings(store).save(
            {
                "app/run_in_background": False,
                "notifications/temperature_enabled": False,
                "safety/thermal_protection_enabled": False,
                "safety/cpu_critical_c": 98,
                "safety/gpu_critical_c": 88,
            }
        )
        loaded = AppSettings(store).load()
        self.assertFalse(loaded["run_in_background"])
        self.assertFalse(loaded["temperature_notifications_enabled"])
        self.assertFalse(loaded["thermal_protection_enabled"])
        self.assertEqual(loaded["cpu_critical_c"], 98)
        self.assertEqual(loaded["gpu_critical_c"], 88)

    def test_independent_custom_curves_and_profile_persist(self) -> None:
        store = DictQSettings()
        settings = AppSettings(store)
        cpu = [[50, 1800], [90, 5300]]
        gpu = [[45, 2400], [83, 5300]]
        settings.save(
            {
                "curves/custom_cpu": cpu,
                "curves/custom_gpu": gpu,
                "curves/selected_profile": CurveProfile.CUSTOM.value,
            }
        )
        loaded = AppSettings(store).load()
        self.assertEqual(loaded["custom_cpu_curve"], cpu)
        self.assertEqual(loaded["custom_gpu_curve"], gpu)
        self.assertIs(loaded["curve_profile"], CurveProfile.CUSTOM)

    def test_curve_panel_is_scrollable_at_supported_small_size_without_commands(self) -> None:
        window, backend, worker = self._window()
        while not worker._commands.empty():
            worker._commands.get_nowait()
        window.resize(760, 600)
        window.show()
        window._set_curve_visible(True, animated=False)
        APP.processEvents()

        self.assertTrue(window.curve_editor.isVisible())
        self.assertTrue(window.curve_editor.apply_button.isVisible())
        self.assertTrue(window.scroll_area.widgetResizable())
        self.assertLessEqual(
            window.scroll_area.widget().width(), window.scroll_area.viewport().width()
        )
        self.assertTrue(worker._commands.empty())
        self.assertEqual(backend.events, [])

        for width, height in ((820, 760), (1200, 900), (760, 600)):
            window.resize(width, height)
            APP.processEvents()
            self.assertLessEqual(
                window.scroll_area.widget().width(),
                window.scroll_area.viewport().width(),
            )
            if width < 900:
                self.assertGreater(window.fan2.y(), window.fan1.y())
                self.assertGreater(window.mode_card.y(), window.cpu_card.y())
            else:
                self.assertEqual(window.fan2.y(), window.fan1.y())
                self.assertEqual(window.mode_card.y(), window.cpu_card.y())
        window.fan1.set_actual(5300)
        self.assertGreaterEqual(
            window.fan1.actual.width(), window.fan1.actual.sizeHint().width()
        )
        window._update_telemetry(Telemetry(60, 0, 2, 30, 31))
        APP.processEvents()
        self.assertGreaterEqual(
            window.lenovo_mode.width(), window.lenovo_mode.sizeHint().width()
        )

        window._set_curve_visible(False, animated=False)
        self.assertFalse(window.curve_editor.isVisible())
        self.assertTrue(worker._commands.empty())
        window.hide()
        window.deleteLater()


if __name__ == "__main__":
    unittest.main()
