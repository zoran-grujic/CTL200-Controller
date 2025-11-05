import sys
import qdarktheme  # PyQt6-compatible dark theme, pip install pyqtdarktheme

import numpy as np
import time
import collections

# Configure PyQtGraph to use PyQt6 BEFORE importing it
import os

os.environ['PYQTGRAPH_QT_LIB'] = 'PyQt6'

import pyqtgraph as pg
from PyQt6 import QtCore, QtWidgets, QtGui

# Import the generated UI class
from gui import Ui_MainWindow
# Import MySerial class
from class_MySerial import MySerial
# Import ConfigManager
from config_manager import ConfigManager
# Import toggle switches
from toggle_switch import LaserToggle, TECToggle
# Import worker thread
from QtWorker import SerialWorker


class ConnectionWorker(QtCore.QObject):
    """Worker class for handling serial connection in a separate thread"""
    connection_success = QtCore.pyqtSignal()
    connection_failed = QtCore.pyqtSignal()
    status_message = QtCore.pyqtSignal(str)
    finished = QtCore.pyqtSignal()

    def __init__(self, serial_device, config):
        super().__init__()
        self.serial_device = serial_device
        self.config = config

    def connect_device(self):
        """Attempt to connect to the device (runs in separate thread)"""
        try:
            self.status_message.emit("Status: Searching for CTL200-0...")

            # Try last known port first if available
            last_port = self.config.get_last_port()
            if last_port:
                self.status_message.emit(f"ℹ Trying last known port: {last_port}")
                self.serial_device.port = last_port
                success = self.serial_device.connect()

                if success:
                    self.connection_success.emit()
                    self.finished.emit()
                    return
                else:
                    self.status_message.emit(f"ℹ Last port {last_port} failed, scanning all ports...")
                    self.serial_device.port = ""  # Clear to scan all ports

            # Try to connect (will auto-scan all ports)
            self.status_message.emit("Status: Scanning serial ports...")
            success = self.serial_device.connect()

            if success:
                self.connection_success.emit()
            else:
                self.connection_failed.emit()

        except Exception as e:
            self.status_message.emit(f"✗ Exception during auto-connect: {e}")
            self.connection_failed.emit()
        finally:
            self.finished.emit()


class MyUi(Ui_MainWindow):
    """
    CTL200-0 Laser Controller UI
    Features:
    - Automatic device detection and connection
    - Temperature monitoring with live plot (in-memory only)
    - Laser and TEC control (all in Laser tab)
    - Configuration persistence
    - Safety: Laser always OFF on startup/shutdown
    """

    def __init__(self):
        super(MyUi, self).__init__()

        # Configuration manager
        self.config = ConfigManager()

        # For serial communication
        self.my_serial = MySerial()

        # Create the plot widget instance after proper setup
        self.plotWindow = None
        self.arbPlot = None
        self.plot_curve = None  # Store the plot curve reference

        # Store main window reference for cleanup
        self.main_window = None

        # Worker thread for serial communication
        self.serial_thread = None
        self.serial_worker = None

        # Reconnection dialog tracking
        self.reconnection_dialog_shown = False
        self.is_reconnecting = False

        # Deques to store recent status values (timestamped). Appended when 'status' replies arrive.
        # Bounded size to avoid unbounded memory growth.
        self.status_time = collections.deque(maxlen=500)
        self.status_lason = collections.deque(maxlen=500)
        self.status_vlaser = collections.deque(maxlen=500)
        self.status_ilaser = collections.deque(maxlen=500)
        self.status_itec = collections.deque(maxlen=500)
        self.status_vtec = collections.deque(maxlen=500)
        self.status_rtact = collections.deque(maxlen=500)
        self.status_rtset = collections.deque(maxlen=500)
        self.status_iphd = collections.deque(maxlen=500)
        self.status_ain1 = collections.deque(maxlen=500)
        self.status_ain2 = collections.deque(maxlen=500)

        # Debounce infrastructure: prevent handlers firing more than once per interval
        # Keys are handler names (strings). Values store last value and QTimer instances.
        self._debounce_timers = {}
        self._debounce_values = {}
        # debounce interval in milliseconds (100ms as requested)
        self._debounce_interval_ms = 100

    def setupUi(self, MainWindow):
        # Store main window reference
        self.main_window = MainWindow

        # Call the parent setupUi method
        super(MyUi, self).setupUi(MainWindow)

        # Create pyqtgraph widgets after UI setup
        self.plotWindow = pg.GraphicsLayoutWidget()
        PG_layout = pg.GraphicsLayout()
        self.arbPlot = PG_layout.addPlot()
        self.arbPlot.setLabel('bottom', "Time (s)")
        self.arbPlot.setLabel('left', "Resistance (Ω)")
        self.arbPlot.showGrid(x=True, y=True)
        self.plotWindow.setCentralItem(PG_layout)

        # Initialize the plot curve (actual thermistor) and a setpoint curve (white dashed)
        # Provide `name=` so they appear in the legend
        self.plot_curve = self.arbPlot.plot([], [], pen=pg.mkPen(color='#00FF00', width=2), name='RT actual')
        try:
            # White dashed line for setpoint (rtset)
            self.plot_setpoint_curve = self.arbPlot.plot([], [], pen=pg.mkPen(color='#FFFFFF', width=1, style=QtCore.Qt.PenStyle.DashLine), name='RT set')
        except Exception:
            # Fallback in case PenStyle enum isn't accepted - use a solid thin white line
            self.plot_setpoint_curve = self.arbPlot.plot([], [], pen=pg.mkPen(color='#FFFFFF', width=1), name='RT set')

        # Try to add a standard legend; if it is not visible under the embedded UI
        # we'll add a manual TextItem-based legend as a reliable fallback.
        try:
            legend = self.arbPlot.addLegend(offset=(10, 10))
            if legend is not None:
                try:
                    legend.setParentItem(self.arbPlot.graphicsItem())
                except Exception:
                    pass
                try:
                    legend.setBrush(pg.mkBrush(40, 40, 40, 200))
                except Exception:
                    pass
        except Exception:
            legend = None

        # Manual overlay legend (pixel-based) - use QGraphicsTextItem to avoid affecting the view auto-range
        try:
            # Use HTML with a subtle semi-transparent background so the text is readable
            html_actual = '<div style="background-color: rgba(40,40,40,0.7); padding:4px; border-radius:4px; color: #00FF00; font-weight: bold">■ RT actual</div>'
            html_set = '<div style="background-color: rgba(40,40,40,0.7); padding:4px; border-radius:4px; color: #FFFFFF; font-weight: bold">— RT set</div>'

            # Preferred approach: QGraphicsTextItem added as a child of the plot graphicsItem and set to ignore transformations
            try:
                gtext_actual = QtWidgets.QGraphicsTextItem()
                gtext_actual.setHtml(html_actual)
                gtext_actual.setParentItem(self.arbPlot.graphicsItem())
                try:
                    gtext_actual.setFlag(QtWidgets.QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations, True)
                except Exception:
                    pass

                gtext_set = QtWidgets.QGraphicsTextItem()
                gtext_set.setHtml(html_set)
                gtext_set.setParentItem(self.arbPlot.graphicsItem())
                try:
                    gtext_set.setFlag(QtWidgets.QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations, True)
                except Exception:
                    pass

                self.legend_text_actual = gtext_actual
                self.legend_text_set = gtext_set

                # Position helper (scene coordinates -> fixed pixel positioning)
                def _position_manual_legend():
                    try:
                        if getattr(self, 'legend_text_actual', None) is None or getattr(self, 'legend_text_set', None) is None:
                            return
                        vb = self.arbPlot.getViewBox()
                        # Use the viewbox's scene bounding rect so we position in scene/pixel coordinates
                        srect = vb.sceneBoundingRect()
                        inset = 8  # pixels inset from top-right
                        # compute positions in scene coords: right - inset, top + inset
                        right = srect.right()
                        top = srect.top()

                        # Place the first item slightly below the top-right corner
                        a_rect = self.legend_text_actual.boundingRect()
                        s_rect = self.legend_text_set.boundingRect()

                        x_a = right - inset - a_rect.width()
                        y_a = top + inset
                        x_s = right - inset - s_rect.width()
                        y_s = y_a + a_rect.height() + 4

                        self.legend_text_actual.setPos(x_a, y_a)
                        self.legend_text_set.setPos(x_s, y_s)
                        try:
                            self.legend_text_actual.setZValue(1000)
                            self.legend_text_set.setZValue(1000)
                        except Exception:
                            pass
                    except Exception:
                        pass

                # Position once after layout and on view range changes
                try:
                    QtCore.QTimer.singleShot(250, _position_manual_legend)
                except Exception:
                    pass

                try:
                    vb = self.arbPlot.getViewBox()
                    try:
                        vb.sigRangeChanged.connect(lambda *args: _position_manual_legend())
                    except Exception:
                        # fallback: connect to sceneRect changed if available
                        try:
                            vb.sigTransformChanged.connect(lambda *args: _position_manual_legend())
                        except Exception:
                            pass
                except Exception:
                    pass

            except Exception:
                # If QGraphicsTextItem approach fails, fall back to pg.TextItem but ensure ignoreBounds when possible
                self.legend_text_actual = pg.TextItem(html=html_actual, anchor=(1, 0))
                self.legend_text_set = pg.TextItem(html=html_set, anchor=(1, 0))
                try:
                    self.arbPlot.addItem(self.legend_text_actual, ignoreBounds=True)
                except Exception:
                    self.arbPlot.addItem(self.legend_text_actual)
                try:
                    self.arbPlot.addItem(self.legend_text_set, ignoreBounds=True)
                except Exception:
                    self.arbPlot.addItem(self.legend_text_set)

                def _position_manual_legend_fallback():
                    try:
                        vb = self.arbPlot.getViewBox()
                        vr = vb.viewRange()
                        x_max = vr[0][1]
                        x_min = vr[0][0]
                        y_min, y_max = vr[1][0], vr[1][1]
                        x_range = x_max - x_min if (x_max - x_min) != 0 else 1.0
                        y_range = y_max - y_min if (y_max - y_min) != 0 else 1.0
                        x_pos = x_max - 0.01 * x_range
                        self.legend_text_actual.setPos(x_pos, y_max - 0.02 * y_range)
                        self.legend_text_set.setPos(x_pos, y_max - 0.08 * y_range)
                    except Exception:
                        pass

                try:
                    QtCore.QTimer.singleShot(500, _position_manual_legend_fallback)
                except Exception:
                    pass

                try:
                    vb = self.arbPlot.getViewBox()
                    vb.sigRangeChanged.connect(lambda *args: _position_manual_legend_fallback())
                except Exception:
                    pass

        except Exception:
            # If neither method works, leave legend attributes None
            self.legend_text_actual = None
            self.legend_text_set = None

        # Replace the plot placeholder with actual plot widget (now in Laser tab)
        try:
            # Find the PlotPlaceholder widget and replace it with our plot
            if hasattr(self, 'PlotPlaceholder') and self.PlotPlaceholder:
                # Get the parent layout
                parent_widget = self.PlotPlaceholder.parent()
                if parent_widget:
                    parent_layout = parent_widget.layout()
                    if parent_layout:
                        # Find the placeholder in the layout
                        for i in range(parent_layout.count()):
                            item = parent_layout.itemAt(i)
                            if item and item.widget() == self.PlotPlaceholder:
                                # Remove placeholder
                                parent_layout.removeWidget(self.PlotPlaceholder)
                                self.PlotPlaceholder.hide()
                                self.PlotPlaceholder.deleteLater()
                                # Add plot widget at the same position
                                parent_layout.insertWidget(i, self.plotWindow)
                                print("✓ Temperature plot added to Laser tab")
                                break
        except Exception as e:
            print(f"✗ Error setting up temperature plot: {e}")

        # Helper to replace a placeholder widget in its actual layout, preserving grid position
        def _replace_placeholder_widget(placeholder, new_widget):
            try:
                if placeholder is None:
                    return False
                parent = placeholder.parent()
                if parent is None:
                    return False

                # Extract text and tooltip from original checkbox before replacing
                try:
                    label_text = placeholder.text()
                    tooltip_text = placeholder.toolTip()

                    # Apply to new widget
                    if label_text:
                        new_widget.setText(label_text)
                    if tooltip_text:
                        new_widget.setToolTip(tooltip_text)
                except Exception as e:
                    print(f"⚠ Could not extract label/tooltip: {e}")

                # Get the parent's layout (where checkbox actually lives)
                layout = parent.layout()
                if layout is not None:
                    # Find the placeholder in this layout
                    for i in range(layout.count()):
                        item = layout.itemAt(i)
                        if item is None:
                            continue
                        if item.widget() is placeholder:
                            # For QGridLayout, preserve exact row/column/span
                            if isinstance(layout, QtWidgets.QGridLayout):
                                try:
                                    row, col, rowspan, colspan = layout.getItemPosition(i)
                                except Exception:
                                    row = col = rowspan = colspan = None

                                layout.removeWidget(placeholder)
                                placeholder.setParent(None)
                                placeholder.hide()

                                if row is not None:
                                    layout.addWidget(new_widget, row, col, rowspan, colspan)
                                else:
                                    # Fallback: add at top-left if position unknown
                                    layout.addWidget(new_widget, 0, 0, 1, 1)
                                return True
                            else:
                                # Non-grid layout: just replace by index
                                layout.removeWidget(placeholder)
                                placeholder.setParent(None)
                                placeholder.hide()
                                layout.insertWidget(i, new_widget)
                                return True

                return False
            except Exception as e:
                print(f"⚠ Error in _replace_placeholder_widget: {e}")
                return False

        # Create and add Laser toggle switch to Laser tab
        self.laser_toggle = None
        try:
            self.laser_toggle = LaserToggle(parent=self.tabLaserControll)

            # Try to replace checkbox in its actual layout (likely inside groupBox/gridLayout_6)
            replaced = False
            if hasattr(self, 'checkBox_LaserEnable'):
                replaced = _replace_placeholder_widget(self.checkBox_LaserEnable, self.laser_toggle)
                if replaced:
                    print("✓ Replaced Laser checkbox with animated toggle in original layout")

            # Fallback: add toggle manually if replacement failed
            if not replaced:
                print("ℹ No placeholder found for Laser checkbox, adding toggle manually...")
                if self.tabLaserControll.layout():
                    control_layout = QtWidgets.QHBoxLayout()
                    laser_label = QtWidgets.QLabel("Laser Enable:")
                    laser_label.setStyleSheet("font-size: 14pt; font-weight: bold;")
                    control_layout.addWidget(laser_label)
                    control_layout.addWidget(self.laser_toggle)
                    control_layout.addStretch()
                    self.tabLaserControll.layout().insertLayout(0, control_layout)
                    print("✓ Added laser toggle to tab layout")

            # Connect signal and disable initially
            if self.laser_toggle:
                self.laser_toggle.toggled.connect(self.on_laser_toggled)
                self.laser_toggle.setEnabled(False)
                print("✓ Laser toggle switch configured")
        except Exception as e:
            print(f"✗ Error setting up laser toggle: {e}")
            import traceback
            traceback.print_exc()
            if not self.laser_toggle:
                self.laser_toggle = LaserToggle(parent=self.tabLaserControll)

        # Create and add TEC toggle switch to Laser tab
        self.tec_toggle = None
        try:
            self.tec_toggle = TECToggle(parent=self.tabLaserControll)

            # Use the same helper to replace TEC checkbox
            replaced = False
            if hasattr(self, 'checkBox_TECEnable'):
                replaced = _replace_placeholder_widget(self.checkBox_TECEnable, self.tec_toggle)
                if replaced:
                    print("✓ Replaced TEC checkbox with animated toggle in original layout")

            if not replaced:
                print("ℹ No placeholder found for TEC checkbox, adding TEC toggle manually...")
                if self.tabLaserControll.layout():
                    tec_layout = QtWidgets.QHBoxLayout()
                    tec_label = QtWidgets.QLabel("TEC Enable:")
                    tec_label.setStyleSheet("font-size: 14pt; font-weight: bold;")
                    tec_layout.addWidget(tec_label)
                    tec_layout.addWidget(self.tec_toggle)
                    tec_layout.addStretch()
                    self.tabLaserControll.layout().insertLayout(1, tec_layout)
                    print("✓ Added TEC toggle to tab layout")

            if self.tec_toggle:
                self.tec_toggle.toggled.connect(self.on_tec_toggled)
                self.tec_toggle.setEnabled(False)
                print("✓ TEC toggle switch configured")
        except Exception as e:
            print(f"✗ Error setting up TEC toggle: {e}")
            import traceback
            traceback.print_exc()
            if not self.tec_toggle:
                self.tec_toggle = TECToggle(parent=self.tabLaserControll)

        # Initialize status labels with default values
        self.label_LaserCurrent_mA.setText("0.0 mA")
        self.label_TECCurrent_mA.setText("0.0 mA")
        self.label_thermistorR.setText("0.0 kΩ")

        # Connect laser current spinbox signal
        self.doubleSpinBox_LaserCurrent.valueChanged.connect(lambda val, name='on_laser_current_changed': self._debounce_call(name, val))
        self.doubleSpinBox_LaserCurrent.setEnabled(False)  # Disabled until connected

        # Connect Temperature spinbox signal
        self.doubleSpinBox_SetTemperature.valueChanged.connect(lambda val, name='on_temperature_changed': self._debounce_call(name, val))
        self.doubleSpinBox_SetTemperature.setEnabled(False)  # Disabled until connected

        # Connect PID gain spinbox signals
        self.doubleSpinBox_P.valueChanged.connect(lambda val, name='on_pgain_changed': self._debounce_call(name, val))
        self.doubleSpinBox_P.setEnabled(False)  # Disabled until connected
        self.doubleSpinBox_I.valueChanged.connect(lambda val, name='on_igain_changed': self._debounce_call(name, val))
        self.doubleSpinBox_I.setEnabled(False)  # Disabled until connected
        self.doubleSpinBox_D.valueChanged.connect(lambda val, name='on_dgain_changed': self._debounce_call(name, val))
        self.doubleSpinBox_D.setEnabled(False)  # Disabled until connected

        # Connect modulation control signals (ain1 for laser current, ain2 for temperature)
        if hasattr(self, 'checkBox_ain1Enable'):
            self.checkBox_ain1Enable.stateChanged.connect(self.on_ain1_enable_changed)
            self.checkBox_ain1Enable.setEnabled(False)  # Disabled until connected
        if hasattr(self, 'checkBox_ain2Enable'):
            self.checkBox_ain2Enable.stateChanged.connect(self.on_ain2_enable_changed)
            self.checkBox_ain2Enable.setEnabled(False)  # Disabled until connected
        if hasattr(self, 'doubleSpinBox_ain1CurrGain'):
            self.doubleSpinBox_ain1CurrGain.valueChanged.connect(lambda val, name='on_ain1_curr_gain_changed': self._debounce_call(name, val))
            self.doubleSpinBox_ain1CurrGain.setEnabled(False)  # Disabled until connected
        if hasattr(self, 'doubleSpinBox_ain2TempGain'):
            self.doubleSpinBox_ain2TempGain.valueChanged.connect(lambda val, name='on_ain2_temp_gain_changed': self._debounce_call(name, val))
            self.doubleSpinBox_ain2TempGain.setEnabled(False)  # Disabled until connected

        # Connect temperature and voltage limit spinbox signals
        if hasattr(self, 'doubleSpinBox_rtmax'):
            self.doubleSpinBox_rtmax.valueChanged.connect(lambda val, name='on_rtmax_changed': self._debounce_call(name, val))
            self.doubleSpinBox_rtmax.setEnabled(False)  # Disabled until connected
        if hasattr(self, 'doubleSpinBox_rtmin'):
            self.doubleSpinBox_rtmin.valueChanged.connect(lambda val, name='on_rtmin_changed': self._debounce_call(name, val))
            self.doubleSpinBox_rtmin.setEnabled(False)  # Disabled until connected
        if hasattr(self, 'doubleSpinBox_vtmax'):
            self.doubleSpinBox_vtmax.valueChanged.connect(lambda val, name='on_vtmax_changed': self._debounce_call(name, val))
            self.doubleSpinBox_vtmax.setEnabled(False)  # Disabled until connected
        if hasattr(self, 'doubleSpinBox_vtmin'):
            self.doubleSpinBox_vtmin.valueChanged.connect(lambda val, name='on_vtmin_changed': self._debounce_call(name, val))
            self.doubleSpinBox_vtmin.setEnabled(False)  # Disabled until connected

        # Connect save settings button
        self.pushButton_SaveSettings.clicked.connect(self.on_save_settings_clicked)

        # Connect the connect/disconnect button
        if hasattr(self, 'pushButton_connectDisconnect'):
            self.pushButton_connectDisconnect.clicked.connect(self.on_connect_disconnect_clicked)
            self.pushButton_connectDisconnect.setText("Disconnect")  # Initial state - will try to connect
            self.pushButton_connectDisconnect.setEnabled(False)  # Disabled until initial connection attempt completes


        # Communication log batching for performance
        self._log_buffer = []
        self._log_paused = False  # Flag to track if logging is paused
        self._log_timer = QtCore.QTimer()
        self._log_timer.timeout.connect(self._flush_log_buffer)
        self._log_timer.start(100)  # Flush every 100ms instead of realtime

        # Connect tab change signal to handle tab activation
        self.tabWidget.currentChanged.connect(self.on_tab_changed)

        # Update status label
        self.label_status.setText("Status: Connecting...")

        # Update window title
        MainWindow.setWindowTitle("CTL200-0 Laser Controller")

        # Start with Serial Connection tab active (will switch to Laser after connection)
        self.tabWidget.setCurrentIndex(1)  # Index 1 is Serial Connection tab

        # Auto-connect to CTL200-0 device
        QtCore.QTimer.singleShot(500, self.auto_connect_device)

        # Connect close event to ensure laser is turned off
        MainWindow.closeEvent = self.closeEvent

    def auto_connect_device(self):
        """Automatically connect to CTL200-0 device on startup (runs in separate thread)"""
        self.label_status.setText("Status: Searching for CTL200-0...")

        # Create a connection thread to avoid blocking GUI
        self.connection_thread = QtCore.QThread()
        self.connection_worker = ConnectionWorker(self.my_serial, self.config)
        self.connection_worker.moveToThread(self.connection_thread)

        # Connect signals
        self.connection_worker.connection_success.connect(self._on_connection_success)
        self.connection_worker.connection_failed.connect(self._on_connection_failure)
        self.connection_worker.status_message.connect(self._update_connection_status)

        # Start connection in separate thread
        self.connection_thread.started.connect(self.connection_worker.connect_device)
        self.connection_worker.finished.connect(self.connection_thread.quit)
        self.connection_worker.finished.connect(self.connection_worker.deleteLater)
        self.connection_thread.finished.connect(self.connection_thread.deleteLater)

        self.connection_thread.start()

    def _update_connection_status(self, message):
        """Update connection status message from worker thread"""
        self.label_status.setText(message)
        print(message)

    def _on_connection_success(self):
        """Handle successful connection"""
        # Update status label with device info
        self.label_status.setText(f"Status: Connected")
        self.label_SerialPort.setText(f"{self.my_serial.port}")
        self.label_Status.setText(f"{self.my_serial.name}")

        # Clear and setup text display for communication log
        self.textEdit_SerialPort.clear()
        self._add_communication_log_header()

        # Add connection info
        info_html = f"""<div style="color: #AAAAAA; margin: 10px 0;">
<b>Connected to CTL200-0 Device</b><br>
Port: {self.my_serial.port}<br>
Model: {self.my_serial.model_number}<br>
Firmware: {self.my_serial.firmware_version}<br>
Serial Number: {self.my_serial.serial_number}<br><br>
<span style="color: #00FF00;">Device is ready for operation.</span><br>
{'='*60}<br><br>
</div>"""
        self.textEdit_SerialPort.insertHtml(info_html)

        # Update text display
        info_text = f"""Connected to CTL200-0 Device

Port: {self.my_serial.port}
Model: {self.my_serial.model_number}
Firmware: {self.my_serial.firmware_version}
Serial Number: {self.my_serial.serial_number}

Device is ready for operation.
"""
        self.textEdit_SerialPort.setText(info_text)

        # Update status bar
        if hasattr(self, 'statusbar'):
            self.statusbar.showMessage(f"Connected to {self.my_serial.name} on {self.my_serial.port}")

        # Save connection info and device info to config
        self.config.set_last_port(self.my_serial.port)
        self.config.set_device_info(
            model=self.my_serial.model_number,
            firmware=self.my_serial.firmware_version,
            serial_number=self.my_serial.serial_number
        )

        print(f"✓ Auto-connected to {self.my_serial.name} on {self.my_serial.port}")
        print(f"✓ Port {self.my_serial.port} saved to config")

        # Read and display current laser state (don't force OFF)
        self._read_laser_state()

        # Switch to Laser tab after successful connection
        self.tabWidget.setCurrentIndex(0)  # Index 0 is Laser tab
        print("ℹ Switched to Laser Control tab")

        # Enable laser toggle switch
        self.laser_toggle.setEnabled(True)

        # Enable TEC toggle switch
        self.tec_toggle.setEnabled(True)

        # Enable laser current spinbox
        self.doubleSpinBox_LaserCurrent.setEnabled(True)

        # Enable temperature spinbox
        self.doubleSpinBox_SetTemperature.setEnabled(True)

        # Enable PID gain spinboxes
        self.doubleSpinBox_P.setEnabled(True)
        self.doubleSpinBox_I.setEnabled(True)
        self.doubleSpinBox_D.setEnabled(True)

        # Enable modulation control widgets
        if hasattr(self, 'checkBox_ain1Enable'):
            self.checkBox_ain1Enable.setEnabled(True)
        if hasattr(self, 'checkBox_ain2Enable'):
            self.checkBox_ain2Enable.setEnabled(True)
        if hasattr(self, 'doubleSpinBox_ain1CurrGain'):
            self.doubleSpinBox_ain1CurrGain.setEnabled(True)
        if hasattr(self, 'doubleSpinBox_ain2TempGain'):
            self.doubleSpinBox_ain2TempGain.setEnabled(True)

        # Enable temperature and voltage limit spinboxes
        if hasattr(self, 'doubleSpinBox_rtmax'):
            self.doubleSpinBox_rtmax.setEnabled(True)
        if hasattr(self, 'doubleSpinBox_rtmin'):
            self.doubleSpinBox_rtmin.setEnabled(True)
        if hasattr(self, 'doubleSpinBox_vtmax'):
            self.doubleSpinBox_vtmax.setEnabled(True)
        if hasattr(self, 'doubleSpinBox_vtmin'):
            self.doubleSpinBox_vtmin.setEnabled(True)

        # Update connect/disconnect button state
        if hasattr(self, 'pushButton_connectDisconnect'):
            self.pushButton_connectDisconnect.setText("Disconnect")
            self.pushButton_connectDisconnect.setEnabled(True)

        # Sequence initialization to prevent response collisions:
        # 1. Apply config settings at 300ms
        QtCore.QTimer.singleShot(300, lambda: self._apply_config_settings_on_startup())

        # 2. Auto-enable TEC at 1200ms (after config is applied)
        print("ℹ Auto-enabling TEC on startup...")
        QtCore.QTimer.singleShot(1200, lambda: self._auto_enable_tec())

        # 3. Start status polling at 1800ms (after all initialization is complete)
        QtCore.QTimer.singleShot(1800, lambda: self._start_status_polling())

    def _on_connection_failure(self):
        """Handle connection failure"""
        self.label_status.setText("Status: Connection failed")
        self.label_SerialPort.setText("No device found")
        self.label_Status.setText("Not connected")

        error_html = f"""<div style="color: #FF4444;">
<b>Could not connect to CTL200-0 Device</b><br><br>
Please check:<br>
- Device is powered on<br>
- USB cable is connected<br>
- Correct drivers are installed<br><br>
Available ports: {', '.join(self.my_serial.serial_ports()) if self.my_serial.serial_ports() else 'None'}<br><br>
Last error: {self.my_serial.last_error if self.my_serial.last_error else 'Unknown'}<br>
</div>"""
        self.textEdit_SerialPort.setHtml(error_html)

        if hasattr(self, 'statusbar'):
            self.statusbar.showMessage("Connection failed - No CTL200-0 device found")

        print(f"✗ Failed to connect to CTL200-0 device")
        print(f"✗ Last error: {self.my_serial.last_error}")

        # Update connect/disconnect button to show "Connect CTL200"
        if hasattr(self, 'pushButton_connectDisconnect'):
            self.pushButton_connectDisconnect.setText("Connect CTL200")
            self.pushButton_connectDisconnect.setEnabled(True)

    def _apply_config_settings_on_startup(self):
        """Check if config file exists and apply settings to device on startup"""
        try:
            # Check if config file exists
            if not self.config.config_file.exists():
                print("ℹ No config file found - device will use default settings")
                return

            print("✓ Config file found - applying saved settings to device...")

            # Get laser configuration
            laser_config = self.config.get_laser_config()

            # Get TEC configuration
            tec_config = self.config.get_tec_config()

            # Apply settings to device with small delays between commands
            settings_applied = []

            # Helper function to send command and clear response
            def send_and_clear(cmd, label):
                self.my_serial.sendToBox(cmd)
                time.sleep(0.15)  # Wait for device to respond
                # Clear the response to prevent buffer buildup
                if self.my_serial.box and self.my_serial.box.in_waiting > 0:
                    self.my_serial.box.flushInput()
                settings_applied.append(label)

            # Apply laser current (ilaser)
            if 'ilaser' in laser_config:
                ilaser = laser_config['ilaser']
                send_and_clear(f"ilaser {ilaser:.6f}", f"ilaser={ilaser:.3f}mA")
                # Update UI
                self.doubleSpinBox_LaserCurrent.blockSignals(True)
                self.doubleSpinBox_LaserCurrent.setValue(ilaser)
                self.doubleSpinBox_LaserCurrent.blockSignals(False)

            # Apply laser max current (ilmax) if present
            if 'ilmax' in laser_config:
                ilmax = laser_config['ilmax']
                send_and_clear(f"ilmax {ilmax:.6f}", f"ilmax={ilmax:.3f}mA")

            # Apply temperature setpoint (rtset)
            if 'rtset' in tec_config:
                rtset = tec_config['rtset']
                send_and_clear(f"rtset {rtset:.6f}", f"rtset={rtset:.3f}Ω")
                # Update UI
                self.doubleSpinBox_SetTemperature.blockSignals(True)
                self.doubleSpinBox_SetTemperature.setValue(rtset)
                self.doubleSpinBox_SetTemperature.blockSignals(False)

            # Apply PID gains
            if 'pgain' in tec_config:
                pgain = tec_config['pgain']
                send_and_clear(f"pgain {pgain:.6f}", f"pgain={pgain:.6f}")
                # Update UI
                self.doubleSpinBox_P.blockSignals(True)
                self.doubleSpinBox_P.setValue(pgain)
                self.doubleSpinBox_P.blockSignals(False)

            if 'igain' in tec_config:
                igain = tec_config['igain']
                send_and_clear(f"igain {igain:.6f}", f"igain={igain:.6f}")
                # Update UI
                self.doubleSpinBox_I.blockSignals(True)
                self.doubleSpinBox_I.setValue(igain)
                self.doubleSpinBox_I.blockSignals(False)

            if 'dgain' in tec_config:
                dgain = tec_config['dgain']
                send_and_clear(f"dgain {dgain:.6f}", f"dgain={dgain:.6f}")
                # Update UI
                self.doubleSpinBox_D.blockSignals(True)
                self.doubleSpinBox_D.setValue(dgain)
                self.doubleSpinBox_D.blockSignals(False)

            # Apply temperature protection (tprot) if present
            if 'tprot' in tec_config:
                tprot = tec_config['tprot']
                send_and_clear(f"tprot {tprot}", f"tprot={tprot}")

            # Apply modulation settings (ain1 for laser current)
            ain1_enable = laser_config.get('ain1_enable', 0)
            ain1_curr_gain = laser_config.get('ain1_curr_gain', 0.0)

            # Update UI checkbox
            if hasattr(self, 'checkBox_ain1Enable'):
                self.checkBox_ain1Enable.blockSignals(True)
                self.checkBox_ain1Enable.setChecked(ain1_enable == 1)
                self.checkBox_ain1Enable.blockSignals(False)

            # Update UI spinbox
            if hasattr(self, 'doubleSpinBox_ain1CurrGain'):
                self.doubleSpinBox_ain1CurrGain.blockSignals(True)
                self.doubleSpinBox_ain1CurrGain.setValue(ain1_curr_gain)
                self.doubleSpinBox_ain1CurrGain.blockSignals(False)
                # Enable/disable spinbox based on checkbox state
                self.doubleSpinBox_ain1CurrGain.setEnabled(ain1_enable == 1)

            # Send lmodgain command: 0 if disabled, actual gain if enabled
            if ain1_enable == 1:
                send_and_clear(f"lmodgain {ain1_curr_gain:.6f}", f"lmodgain={ain1_curr_gain:.6f}")
            else:
                send_and_clear("lmodgain 0.0", "lmodgain=0.0")

            # Apply modulation settings (ain2 for temperature)
            ain2_enable = tec_config.get('ain2_enable', 0)
            ain2_temp_gain = tec_config.get('ain2_temp_gain', 0.0)

            # Update UI checkbox
            if hasattr(self, 'checkBox_ain2Enable'):
                self.checkBox_ain2Enable.blockSignals(True)
                self.checkBox_ain2Enable.setChecked(ain2_enable == 1)
                self.checkBox_ain2Enable.blockSignals(False)

            # Update UI spinbox
            if hasattr(self, 'doubleSpinBox_ain2TempGain'):
                self.doubleSpinBox_ain2TempGain.blockSignals(True)
                self.doubleSpinBox_ain2TempGain.setValue(ain2_temp_gain)
                self.doubleSpinBox_ain2TempGain.blockSignals(False)
                # Enable/disable spinbox based on checkbox state
                self.doubleSpinBox_ain2TempGain.setEnabled(ain2_enable == 1)

            # Send tmodgain command: 0 if disabled, actual gain if enabled
            if ain2_enable == 1:
                send_and_clear(f"tmodgain {ain2_temp_gain:.6f}", f"tmodgain={ain2_temp_gain:.6f}")
            else:
                send_and_clear("tmodgain 0.0", "tmodgain=0.0")

            # Apply temperature and voltage limit settings
            if 'rtmax' in tec_config:
                rtmax = tec_config['rtmax']
                send_and_clear(f"rtmax {rtmax:.6f}", f"rtmax={rtmax:.3f}Ω")
                if hasattr(self, 'doubleSpinBox_rtmax'):
                    self.doubleSpinBox_rtmax.blockSignals(True)
                    self.doubleSpinBox_rtmax.setValue(rtmax)
                    self.doubleSpinBox_rtmax.blockSignals(False)

            if 'rtmin' in tec_config:
                rtmin = tec_config['rtmin']
                send_and_clear(f"rtmin {rtmin:.6f}", f"rtmin={rtmin:.3f}Ω")
                if hasattr(self, 'doubleSpinBox_rtmin'):
                    self.doubleSpinBox_rtmin.blockSignals(True)
                    self.doubleSpinBox_rtmin.setValue(rtmin)
                    self.doubleSpinBox_rtmin.blockSignals(False)

            if 'vtmax' in tec_config:
                vtmax = tec_config['vtmax']
                send_and_clear(f"vtmax {vtmax:.6f}", f"vtmax={vtmax:.3f}V")
                if hasattr(self, 'doubleSpinBox_vtmax'):
                    self.doubleSpinBox_vtmax.blockSignals(True)
                    self.doubleSpinBox_vtmax.setValue(vtmax)
                    self.doubleSpinBox_vtmax.blockSignals(False)

            if 'vtmin' in tec_config:
                vtmin = tec_config['vtmin']
                send_and_clear(f"vtmin {vtmin:.6f}", f"vtmin={vtmin:.3f}V")
                if hasattr(self, 'doubleSpinBox_vtmin'):
                    self.doubleSpinBox_vtmin.blockSignals(True)
                    self.doubleSpinBox_vtmin.setValue(vtmin)
                    self.doubleSpinBox_vtmin.blockSignals(False)

            print(f"✓ Settings applied: {', '.join(settings_applied)}")

            if hasattr(self, 'statusbar'):
                self.statusbar.showMessage("Settings loaded from config file", 3000)

        except Exception as e:
            print(f"✗ Error applying config settings: {e}")
            import traceback
            traceback.print_exc()

    def _auto_enable_tec(self):
        """Auto-enable TEC on startup (safety feature) and sync UI with actual device state"""
        try:
            # Read tecon state to sync UI (lason is already in status command)
            print("ℹ Reading TEC state from device...")
            tecon_state = self.my_serial.read_binary_state("tecon")
            if tecon_state is not None:
                print(f"ℹ Current tecon state: {tecon_state}")
                # Sync TEC toggle with actual state
                self.tec_toggle.blockSignals(True)
                self.tec_toggle.setChecked(tecon_state == 1)
                self.tec_toggle.blockSignals(False)
            else:
                print("⚠ Could not read tecon state from device")

            # Ensure laser is OFF (safety first)
            # Note: lason state is synced automatically via status command polling
            print("ℹ Ensuring laser is OFF for safety...")
            self.my_serial.sendToBox("lason 0")
            time.sleep(0.15)
            # Clear response buffer
            if self.my_serial.box and self.my_serial.box.in_waiting > 0:
                self.my_serial.box.flushInput()

            # Update laser toggle UI to OFF
            self.laser_toggle.blockSignals(True)
            self.laser_toggle.setChecked(False)
            self.laser_toggle.blockSignals(False)

            # Enable TEC
            print("ℹ Enabling TEC for temperature control...")
            self.my_serial.sendToBox("tecon 1")
            time.sleep(0.15)
            # Clear response buffer
            if self.my_serial.box and self.my_serial.box.in_waiting > 0:
                self.my_serial.box.flushInput()

            # Update TEC toggle UI to ON
            self.tec_toggle.blockSignals(True)
            self.tec_toggle.setChecked(True)
            self.tec_toggle.blockSignals(False)

            print("✓ TEC enabled, Laser OFF (safe state)")

            if hasattr(self, 'statusbar'):
                self.statusbar.showMessage("TEC enabled - Device ready", 3000)

        except Exception as e:
            print(f"✗ Error during auto-enable TEC: {e}")
            import traceback
            traceback.print_exc()

    def _start_status_polling(self):

        """Start the worker thread for periodic device status polling"""
        try:
            # Create worker and thread
            self.serial_worker = SerialWorker(self.my_serial)
            self.serial_thread = QtCore.QThread()

            # Configure worker (set poll interval and optional read intervals)
            try:
                self.serial_worker.poll_interval = 0.05
                # keep ilaser/tecon at 1s by default (can be adjusted elsewhere)
                self.serial_worker.ilaser_interval = getattr(self.serial_worker, 'ilaser_interval', 1.0)
                self.serial_worker.tecon_interval = getattr(self.serial_worker, 'tecon_interval', 1.0)
                # Enable detailed logging by default to show all TX/RX in textEdit_SerialPort
                self.serial_worker.detailed_logging = True
            except Exception:
                pass

            # Move worker to thread
            self.serial_worker.moveToThread(self.serial_thread)

            # Connect signals
            self.serial_worker.status_updated.connect(self._update_status_display)
            self.serial_worker.error_occurred.connect(self._handle_worker_error)
            self.serial_worker.command_completed.connect(self._handle_command_completed)
            self.serial_worker.communication_log.connect(self._update_communication_log)
            self.serial_worker.raw_data_received.connect(self._update_raw_data_display)

            # Connect thread started signal to worker's start method
            self.serial_thread.started.connect(self.serial_worker.start_polling)

            # Clear the deques for fresh data collection
            # (status_time and status_rtact/status_rtset used for plotting)
            self.status_time.clear()
            self.status_lason.clear()
            self.status_vlaser.clear()
            self.status_ilaser.clear()
            self.status_itec.clear()
            self.status_vtec.clear()
            self.status_rtact.clear()
            self.status_rtset.clear()
            self.status_iphd.clear()
            self.status_ain1.clear()
            self.status_ain2.clear()

            # Start the thread
            self.serial_thread.start()

            print("✓ Status polling thread started (50ms interval)")

        except Exception as e:
            print(f"✗ Error starting status polling: {e}")
            import traceback
            traceback.print_exc()

    def _stop_status_polling(self):
        """Stop the worker thread"""
        if self.serial_worker:
            self.serial_worker.stop_polling()

        if self.serial_thread:
            self.serial_thread.quit()
            self.serial_thread.wait(1000)  # Wait up to 1 second
            print("✓ Status polling thread stopped")

    def _update_status_display(self, status_data):

        # Timestamp for this status update
        current_timestamp = time.time()

        # Append incoming data to status deques (use status_data.get to avoid KeyError)
        self.status_time.append(current_timestamp)
        self.status_lason.append(status_data.get('lason', None))
        self.status_vlaser.append(status_data.get('vlaser', None))
        self.status_ilaser.append(status_data.get('ilaser', None))
        self.status_itec.append(status_data.get('itec', None))
        self.status_vtec.append(status_data.get('vtec', None))
        self.status_rtact.append(status_data.get('rtact', None))
        self.status_rtset.append(self.doubleSpinBox_SetTemperature.value())  # Use setpoint from UI
        self.status_iphd.append(status_data.get('iphd', None))
        self.status_ain1.append(status_data.get('ain1', None))
        self.status_ain2.append(status_data.get('ain2', None))

        """Update GUI labels with status data from worker thread"""
        try:
            # Update laser current
            if 'ilaser' in status_data:
                self.label_LaserCurrent_mA.setText(f"{status_data['ilaser']:.3f} mA")

            # Update TEC current
            if 'itec' in status_data:
                self.label_TECCurrent_mA.setText(f"{status_data['itec']:.2f} mA")

            # Update thermistor resistance (convert to kΩ for display)
            if 'rtact' in status_data:
                resistance_kohm = status_data['rtact'] / 1000.0
                self.label_thermistorR.setText(f"{resistance_kohm:.3f} kΩ")

                # Update the plot using status_time and status_rtact/status_rtset
                if self.plot_curve is not None and len(self.status_time) > 0:
                    base_time = np.array(self.status_time)[-1]

                    # rtact (actual thermistor reading)
                    valid_act = [i for i, v in enumerate(self.status_rtact) if v is not None]
                    if valid_act:
                        times_act = np.array(self.status_time)[valid_act]
                        values_act = np.array([self.status_rtact[i] for i in valid_act])
                        times_act = times_act - base_time
                        self.plot_curve.setData(times_act, values_act)
                    else:
                        # No valid actual data
                        self.plot_curve.setData([], [])

                    # rtset (setpoint) - white dashed line
                    try:
                        valid_set = [i for i, v in enumerate(self.status_rtset) if v is not None]
                        if valid_set:
                            times_set = np.array(self.status_time)[valid_set]
                            values_set = np.array([self.status_rtset[i] for i in valid_set])
                            times_set = times_set - base_time
                            self.plot_setpoint_curve.setData(times_set, values_set)
                        else:
                            self.plot_setpoint_curve.setData([], [])
                    except Exception:
                        # Keep setpoint curve empty on any error
                        try:
                            self.plot_setpoint_curve.setData([], [])
                        except Exception:
                            pass

                    # Position manual TextItem legend items (fallback) in top-right of view
                    try:
                        if getattr(self, 'legend_text_actual', None) is not None and getattr(self, 'legend_text_set', None) is not None:
                            vb = self.arbPlot.getViewBox()
                            vr = vb.viewRange()  # [[xMin,xMax], [yMin,yMax]]
                            x_max = vr[0][1]
                            x_min = vr[0][0]
                            y_min, y_max = vr[1][0], vr[1][1]
                            x_range = x_max - x_min if (x_max - x_min) != 0 else 1.0
                            y_range = y_max - y_min if (y_max - y_min) != 0 else 1.0
                            # Inset from top-right corner to avoid clipping
                            x_pos = x_max - 0.01 * x_range
                            # Slight offsets downward for the two lines
                            self.legend_text_actual.setPos(x_pos, y_max - 0.02 * y_range)
                            self.legend_text_set.setPos(x_pos, y_max - 0.08 * y_range)
                            # Ensure they draw on top
                            try:
                                self.legend_text_actual.setZValue(1000)
                                self.legend_text_set.setZValue(1000)
                            except Exception:
                                pass
                    except Exception:
                        pass

            # Update laser toggle state if it changed
            if 'lason' in status_data:
                laser_on = status_data['lason'] == 1
                if self.laser_toggle.isChecked() != laser_on:
                    self.laser_toggle.blockSignals(True)
                    self.laser_toggle.setChecked(laser_on)
                    self.laser_toggle.blockSignals(False)

                # Update laser current label color based on laser state
                if laser_on:
                    # Laser is ON - green color
                    self.label_LaserCurrent_mA.setStyleSheet("color: #4CAF50; font-weight: bold;")
                else:
                    # Laser is OFF - red color
                    self.label_LaserCurrent_mA.setStyleSheet("color: #F44336; font-weight: bold;")

            # Update TEC toggle state if it changed
            if 'tecon' in status_data:
                tec_on = status_data['tecon'] == 1
                if self.tec_toggle.isChecked() != tec_on:
                    self.tec_toggle.blockSignals(True)
                    self.tec_toggle.setChecked(tec_on)
                    self.tec_toggle.blockSignals(False)

        except Exception as e:
            print(f"✗ Error updating status display: {e}")

    def _handle_worker_error(self, error_msg):
        """Handle errors from the worker thread"""
        print(f"✗ Worker error: {error_msg}")
        if hasattr(self, 'statusbar'):
            self.statusbar.showMessage(f"Error: {error_msg}", 5000)

        # Check if this is a connection lost error
        if "not connected" in error_msg.lower() or "connection lost" in error_msg.lower():
            self._handle_connection_lost()

    def _handle_connection_lost(self):
        """Handle connection lost - show reconnection dialog"""
        # Prevent multiple dialogs from appearing
        if self.reconnection_dialog_shown or self.is_reconnecting:
            return

        self.reconnection_dialog_shown = True

        # Stop the status polling thread
        self._stop_status_polling()

        # Show reconnection dialog
        reply = QtWidgets.QMessageBox.question(
            self.main_window,
            'Connection Lost',
            'Connection to CTL200 lost. Reconnect?',
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
            QtWidgets.QMessageBox.StandardButton.Yes
        )

        if reply == QtWidgets.QMessageBox.StandardButton.Yes:
            print("ℹ User chose to reconnect...")
            self.is_reconnecting = True

            # Switch to Serial Connection tab (index 1)
            self.tabWidget.setCurrentIndex(1)

            # Update status
            self.label_status.setText("Status: Reconnecting...")
            self.textEdit_SerialPort.append('<span style="color: #FFA500;">--- Attempting to reconnect... ---</span>')

            # Attempt reconnection in separate thread
            self._attempt_reconnection()
        else:
            print("ℹ User chose not to reconnect")
            self.label_status.setText("Status: Disconnected")
            self.reconnection_dialog_shown = False

            # Update button to show "Connect CTL200"
            if hasattr(self, 'pushButton_connectDisconnect'):
                self.pushButton_connectDisconnect.setText("Connect CTL200")
                self.pushButton_connectDisconnect.setEnabled(True)

    def _attempt_reconnection(self):
        """Attempt to reconnect to the device"""
        # Create a reconnection thread
        self.reconnection_thread = QtCore.QThread()
        self.reconnection_worker = ConnectionWorker(self.my_serial, self.config)
        self.reconnection_worker.moveToThread(self.reconnection_thread)

        # Connect signals
        self.reconnection_worker.connection_success.connect(self._on_reconnection_success)
        self.reconnection_worker.connection_failed.connect(self._on_reconnection_failed)
        self.reconnection_worker.status_message.connect(self._update_connection_status)

        # Start reconnection in separate thread
        self.reconnection_thread.started.connect(self.reconnection_worker.connect_device)
        self.reconnection_worker.finished.connect(self.reconnection_thread.quit)
        self.reconnection_worker.finished.connect(self.reconnection_worker.deleteLater)
        self.reconnection_thread.finished.connect(self.reconnection_thread.deleteLater)

        self.reconnection_thread.start()

    def _on_reconnection_success(self):
        """Handle successful reconnection"""
        print("✓ Reconnection successful!")
        self.is_reconnecting = False
        self.reconnection_dialog_shown = False

        # Call the standard connection success handler
        self._on_connection_success()

        # Show success message in status bar temporarily (5 seconds)
        if hasattr(self, 'statusbar'):
            self.statusbar.showMessage('✓ Successfully reconnected to CTL200-0', 5000)

    def _on_reconnection_failed(self):
        """Handle failed reconnection"""
        print("✗ Reconnection failed")
        self.is_reconnecting = False
        self.reconnection_dialog_shown = False

        # Show failure message and ask to retry
        reply = QtWidgets.QMessageBox.question(
            self.main_window,
            'Reconnection Failed',
            'Failed to reconnect to CTL200-0. Try again?',
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
            QtWidgets.QMessageBox.StandardButton.Yes
        )

        if reply == QtWidgets.QMessageBox.StandardButton.Yes:
            # Retry reconnection
            QtCore.QTimer.singleShot(500, self._attempt_reconnection)
        else:
            self.label_status.setText("Status: Disconnected")
            self._on_connection_failure()

    def on_connect_disconnect_clicked(self):
        """Handle connect/disconnect button click"""
        button_text = self.pushButton_connectDisconnect.text()

        if button_text == "Connect CTL200":
            # User wants to connect
            print("ℹ User clicked Connect button")
            self.pushButton_connectDisconnect.setEnabled(False)  # Disable during connection attempt

            # Switch to Serial Connection tab (index 1)
            self.tabWidget.setCurrentIndex(1)

            # Update status
            self.label_status.setText("Status: Connecting...")
            self.textEdit_SerialPort.append('<span style="color: #FFA500;">--- Attempting to connect... ---</span>')

            # Reset reconnection flags
            self.reconnection_dialog_shown = False
            self.is_reconnecting = False

            # Attempt connection in separate thread
            self.auto_connect_device()

        elif button_text == "Disconnect":
            # User wants to disconnect
            print("ℹ User clicked Disconnect button")

            # Ask for confirmation
            reply = QtWidgets.QMessageBox.question(
                self.main_window,
                'Disconnect Device',
                'Are you sure you want to disconnect from CTL200-0?',
                QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
                QtWidgets.QMessageBox.StandardButton.No
            )

            if reply == QtWidgets.QMessageBox.StandardButton.Yes:
                # Stop polling
                self._stop_status_polling()

                # Disconnect the device
                if self.my_serial.is_connected():
                    try:
                        # Turn off laser for safety
                        self.my_serial.sendToBox("lason 0")
                        time.sleep(0.2)
                        print("✓ Laser turned OFF (safety)")
                    except:
                        pass

                    self.my_serial.disconnect()
                    print("✓ Disconnected from device")

                # Update UI
                self.label_status.setText("Status: Disconnected")
                self.pushButton_connectDisconnect.setText("Connect CTL200")

                # Switch to Serial Connection tab
                self.tabWidget.setCurrentIndex(1)

    def _handle_command_completed(self, command, success, response):
        """Handle command completion signal from worker thread"""
        if success:
            print(f"✓ Command '{command}' completed successfully")
            if "lason" in command:
                if "lason 1" in command:
                    if hasattr(self, 'statusbar'):
                        self.statusbar.showMessage("⚠ LASER ON", 5000)
                elif "lason 0" in command:
                    if hasattr(self, 'statusbar'):
                        self.statusbar.showMessage("Laser OFF", 3000)
        else:
            print(f"✗ Command '{command}' failed: {response}")
            if hasattr(self, 'statusbar'):
                self.statusbar.showMessage(f"Command failed: {command}", 5000)

            # Reset laser toggle if it was a laser command
            if "lason" in command:
                self.laser_toggle.blockSignals(True)
                self.laser_toggle.setChecked(False)
                self.laser_toggle.blockSignals(False)

    def _add_communication_log_header(self):
        """Add a header to the communication log"""
        try:
            header_html = """<div style="color: #00AAFF; font-weight: bold; margin: 5px 0;">
Serial Communication Log
</div>"""
            self.textEdit_SerialPort.insertHtml(header_html)
        except Exception as e:
            print(f"✗ Error adding communication log header: {e}")

    def _update_communication_log(self, timestamp, direction, message):
        """Update the communication log text display with batching for performance"""
        try:
            # Skip logging if paused
            if self._log_paused:
                return

            # Format the log entry
            if direction == 'TX':
                color = '#00FF00'  # Green for transmitted
                arrow = '→'
            else:
                color = '#00AAFF'  # Blue for received
                arrow = '←'

            log_entry = f'<span style="color: #888;">[{timestamp}]</span> <span style="color: {color};">{arrow} {message}</span><br>'

            # Add to buffer instead of immediate append (batched updates)
            self._log_buffer.append(log_entry)

            # Limit buffer size to prevent memory issues
            if len(self._log_buffer) > 1000:
                self._log_buffer = self._log_buffer[-500:]  # Keep last 500

        except Exception as e:
            print(f"✗ Error updating communication log: {e}")

    def _flush_log_buffer(self):
        """Flush buffered log entries to the text widget (called periodically)"""
        try:
            # Skip flushing if logging is paused
            if self.checkBox_pauseLogging.checkState() is QtCore.Qt.CheckState.Checked:
                return

            if self._log_buffer and hasattr(self, 'textEdit_SerialPort'):
                # Batch append all buffered entries at once
                html_content = ''.join(self._log_buffer)
                self.textEdit_SerialPort.insertHtml(html_content)

                # Limit total text size to prevent slowdown
                cursor = self.textEdit_SerialPort.textCursor()
                cursor.movePosition(QtGui.QTextCursor.MoveOperation.Start)
                # Keep only last ~10000 characters
                if len(self.textEdit_SerialPort.toPlainText()) > 10000:
                    cursor.movePosition(QtGui.QTextCursor.MoveOperation.End)
                    cursor.movePosition(QtGui.QTextCursor.MoveOperation.PreviousCharacter,
                                      QtGui.QTextCursor.MoveMode.KeepAnchor,
                                      len(self.textEdit_SerialPort.toPlainText()) - 8000)
                    cursor.removeSelectedText()

                # Auto-scroll to bottom
                scrollbar = self.textEdit_SerialPort.verticalScrollBar()
                scrollbar.setValue(scrollbar.maximum())

                # Clear buffer
                self._log_buffer.clear()

        except Exception as e:
            print(f"✗ Error flushing log buffer: {e}")

    def _update_raw_data_display(self, raw_data):
        """Display raw received data in textEdit_SerialPort_raw without any formatting"""
        try:
            # Skip logging if paused
            if self.checkBox_pauseLogging.checkState() is QtCore.Qt.CheckState.Checked:
                return

            if hasattr(self, 'textEdit_SerialPort_raw'):
                # Append raw data with only newlines preserved
                self.textEdit_SerialPort_raw.insertPlainText(raw_data)
                if not raw_data.endswith('\n'):
                    self.textEdit_SerialPort_raw.insertPlainText('\n')

                # Limit total text size to prevent slowdown
                plain_text = self.textEdit_SerialPort_raw.toPlainText()
                if len(plain_text) > 10000:
                    # Keep only last ~8000 characters
                    cursor = self.textEdit_SerialPort_raw.textCursor()
                    cursor.movePosition(QtGui.QTextCursor.MoveOperation.Start)
                    cursor.movePosition(QtGui.QTextCursor.MoveOperation.End)
                    cursor.movePosition(QtGui.QTextCursor.MoveOperation.PreviousCharacter,
                                      QtGui.QTextCursor.MoveMode.KeepAnchor,
                                      len(plain_text) - 8000)
                    cursor.removeSelectedText()

                # Auto-scroll to bottom
                scrollbar = self.textEdit_SerialPort_raw.verticalScrollBar()
                scrollbar.setValue(scrollbar.maximum())

        except Exception as e:
            print(f"✗ Error updating raw data display: {e}")

    def on_logging_toggled(self, state):
        """Toggle detailed communication logging on/off"""
        try:
            enabled = (state == QtCore.Qt.CheckState.Checked)
            if self.serial_worker:
                self.serial_worker.detailed_logging = enabled

            if enabled:
                print("ℹ Serial communication logging enabled")
                self.textEdit_SerialPort.append('<span style="color: #00FF00;">--- Logging enabled ---</span>')
            else:
                print("ℹ Serial communication logging disabled (for max performance)")
                self.textEdit_SerialPort.append('<span style="color: #FF6600;">--- Logging disabled ---</span>')

        except Exception as e:
            print(f"✗ Error toggling logging: {e}")

    def on_pause_logging_toggled(self, state):
        """Pause or resume logging display"""
        try:
            paused = (state == QtCore.Qt.CheckState.Checked)
            self._log_paused = paused

            if paused:
                print("ℹ Logging display paused (data still being collected)")
                if hasattr(self, 'textEdit_SerialPort'):
                    self.textEdit_SerialPort.append('<span style="color: #FFA500;">--- Logging display PAUSED ---</span>')
                if hasattr(self, 'textEdit_SerialPort_raw'):
                    self.textEdit_SerialPort_raw.append('--- Logging display PAUSED ---\n')
            else:
                print("ℹ Logging display resumed")
                if hasattr(self, 'textEdit_SerialPort'):
                    self.textEdit_SerialPort.append('<span style="color: #00FF00;">--- Logging display RESUMED ---</span>')
                if hasattr(self, 'textEdit_SerialPort_raw'):
                    self.textEdit_SerialPort_raw.append('--- Logging display RESUMED ---\n')

        except Exception as e:
            print(f"✗ Error toggling pause logging: {e}")

    def _read_laser_state(self):
        """Read and display current laser state"""
        try:
            if self.my_serial.is_connected():
                # Use the new read_binary_state method which properly filters responses
                laser_state = self.my_serial.read_binary_state("lason")

                if laser_state is not None:
                    if laser_state == 0:
                        print("ℹ Laser state: OFF")
                        self.laser_toggle.setChecked(False)
                    elif laser_state == 1:
                        print("⚠ Laser state: ON")
                        self.laser_toggle.setChecked(True)
                else:
                    print("ℹ Could not read laser state (no valid response)")
        except Exception as e:
            print(f"✗ Warning: Could not read laser state: {e}")


    def closeEvent(self, event):
        """Handle application close event - ensure laser is turned OFF"""
        print("\nℹ Application closing...")

        # Stop the worker thread first
        self._stop_status_polling()

        # SAFETY: Turn off laser before closing
        if self.my_serial.is_connected():
            try:
                print("ℹ Turning OFF laser for safety...")
                self.my_serial.sendToBox("lason 0")
                time.sleep(0.2)  # Give device time to process
                print("✓ Laser turned OFF")
            except Exception as e:
                print(f"✗ Warning: Could not turn off laser: {e}")

            # Disconnect from device
            try:
                self.my_serial.disconnect()
                print("✓ Disconnected from device")
            except Exception as e:
                print(f"✗ Warning: Error during disconnect: {e}")

        # Accept the close event
        event.accept()
        print("✓ Application closed safely")

    def on_laser_toggled(self, state):
        """Handle laser toggle switch state change"""
        if not self.my_serial.is_connected():
            print("✗ Cannot control laser: Not connected")
            # Reset toggle if not connected
            self.laser_toggle.blockSignals(True)
            self.laser_toggle.setChecked(False)
            self.laser_toggle.blockSignals(False)
            return

        # SAFETY CHECK: Prevent laser from turning ON if TEC is OFF
        if state and not self.tec_toggle.isChecked():
            print("✗ Cannot turn ON laser: TEC must be enabled first")
            if hasattr(self, 'statusbar'):
                self.statusbar.showMessage("⚠ Cannot enable laser: TEC must be ON first", 5000)
            # Reset toggle
            self.laser_toggle.blockSignals(True)
            self.laser_toggle.setChecked(False)
            self.laser_toggle.blockSignals(False)
            return

        # Disable toggle while command is being processed
        self.laser_toggle.setEnabled(False)

        try:
            if state:
                # Turn laser ON - queue command with verification
                print("ℹ Queuing laser ON command...")
                if self.serial_worker:
                    self.serial_worker.execute_command(
                        command="lason 1",
                        verify_command="lason",
                        expected_response="1"
                    )
                else:
                    # Fallback to direct command if worker not available
                    self.my_serial.sendToBox("lason 1")
                    time.sleep(0.1)
                    print("✓ Laser ON command sent (no verification)")
            else:
                # Turn laser OFF - queue command with verification
                print("ℹ Queuing laser OFF command...")
                if self.serial_worker:
                    self.serial_worker.execute_command(
                        command="lason 0",
                        verify_command="lason",
                        expected_response="0"
                    )
                else:
                    # Fallback to direct command if worker not available
                    self.my_serial.sendToBox("lason 0")
                    time.sleep(0.1)
                    print("✓ Laser OFF command sent (no verification)")

            # Re-enable toggle after a short delay
            QtCore.QTimer.singleShot(500, lambda: self.laser_toggle.setEnabled(True))

        except Exception as e:
            print(f"✗ Error controlling laser: {e}")
            # Reset toggle on error
            self.laser_toggle.blockSignals(True)
            self.laser_toggle.setChecked(False)
            self.laser_toggle.blockSignals(False)
            self.laser_toggle.setEnabled(True)

    def on_tec_toggled(self, state):
        """Handle TEC toggle switch state change"""
        if not self.my_serial.is_connected():
            print("✗ Cannot control TEC: Not connected")
            # Reset toggle if not connected
            self.tec_toggle.blockSignals(True)
            self.tec_toggle.setChecked(False)
            self.tec_toggle.blockSignals(False)
            return

        # SAFETY CHECK: Prevent TEC from turning OFF if Laser is ON
        if not state and self.laser_toggle.isChecked():
            print("✗ Cannot turn OFF TEC: Laser must be disabled first")
            if hasattr(self, 'statusbar'):
                self.statusbar.showMessage("⚠ Cannot disable TEC: Turn OFF laser first", 5000)
            # Reset toggle to ON
            self.tec_toggle.blockSignals(True)
            self.tec_toggle.setChecked(True)
            self.tec_toggle.blockSignals(False)
            self.tec_toggle.setEnabled(True)
            return

        # Disable toggle while command is being processed
        self.tec_toggle.setEnabled(False)

        try:
            if state:
                # Turn TEC ON - queue command with verification
                print("ℹ Queuing TEC ON command...")
                if self.serial_worker:
                    self.serial_worker.execute_command(
                        command="tecon 1",
                        verify_command="tecon",
                        expected_response="1"
                    )
                else:
                    # Fallback to direct command if worker not available
                    self.my_serial.sendToBox("tecon 1")
                    time.sleep(0.1)
                    print("✓ TEC ON command sent (no verification)")
            else:
                # Turn TEC OFF - queue command with verification
                print("ℹ Queuing TEC OFF command...")
                if self.serial_worker:
                    self.serial_worker.execute_command(
                        command="tecon 0",
                        verify_command="tecon",
                        expected_response="0"
                    )
                else:
                    # Fallback to direct command if worker not available
                    self.my_serial.sendToBox("tecon 0")
                    time.sleep(0.1)
                    print("✓ TEC OFF command sent (no verification)")

            # Re-enable toggle after a short delay
            QtCore.QTimer.singleShot(500, lambda: self.tec_toggle.setEnabled(True))

        except Exception as e:
            print(f"✗ Error controlling TEC: {e}")
            # Reset toggle on error
            self.tec_toggle.blockSignals(True)
            self.tec_toggle.setChecked(False)
            self.tec_toggle.blockSignals(False)
            self.tec_toggle.setEnabled(True)

    def on_laser_current_changed(self, value):
        """Handle laser current spinbox value change"""
        if not self.my_serial.is_connected():
            print("✗ Cannot set laser current: Not connected")
            return

        try:
            # Format command with 3 decimal places to match device precision
            command = f"ilaser {value:.3f}"
            print(f"ℹ Setting laser current to {value:.3f} mA...")

            if self.serial_worker:
                # Queue command with verification
                self.serial_worker.execute_command(
                    command=command,
                    verify_command="ilaser",
                    expected_response=f"{value:.3f}"
                )
            else:
                # Fallback to direct command if worker not available
                self.my_serial.sendToBox(command)
                time.sleep(0.1)
                print(f"✓ Laser current set to {value:.3f} mA (no verification)")

        except Exception as e:
            print(f"✗ Error setting laser current: {e}")
            if hasattr(self, 'statusbar'):
                self.statusbar.showMessage(f"Error setting laser current: {e}", 5000)

    def on_temperature_changed(self, value):
        """Handle temperature spinbox value change"""
        if not self.my_serial.is_connected():
            print("✗ Cannot set temperature: Not connected")
            return

        try:
            # Format command for resistance setpoint (rtset in Ohms)
            command = f"rtset {value:.3f}"
            print(f"ℹ Setting temperature setpoint to {value:.3f} Ω...")

            if self.serial_worker:
                # Queue command with verification
                self.serial_worker.execute_command(
                    command=command,
                    verify_command="rtset",
                    expected_response=f"{value:.3f}"
                )
            else:
                # Fallback to direct command if worker not available
                self.my_serial.sendToBox(command)
                time.sleep(0.1)
                print(f"✓ Temperature setpoint set to {value:.3f} Ω (no verification)")

        except Exception as e:
            print(f"✗ Error setting temperature: {e}")
            if hasattr(self, 'statusbar'):
                self.statusbar.showMessage(f"Error setting temperature: {e}", 5000)

    def on_pgain_changed(self, value):
        """Handle P gain spinbox value change"""
        if not self.my_serial.is_connected():
            print("✗ Cannot set P gain: Not connected")
            return

        try:
            # Format command with 6 decimal places for precision
            command = f"pgain {value:.6f}"
            print(f"ℹ Setting P gain to {value:.6f}...")

            if self.serial_worker:
                # Queue command with verification
                self.serial_worker.execute_command(
                    command=command,
                    verify_command="pgain",
                    expected_response=f"{value:.6f}"
                )
            else:
                # Fallback to direct command if worker not available
                self.my_serial.sendToBox(command)
                time.sleep(0.1)
                print(f"✓ P gain set to {value:.6f} (no verification)")

        except Exception as e:
            print(f"✗ Error setting P gain: {e}")
            if hasattr(self, 'statusbar'):
                self.statusbar.showMessage(f"Error setting P gain: {e}", 5000)

    def on_igain_changed(self, value):
        """Handle I gain spinbox value change"""
        if not self.my_serial.is_connected():
            print("✗ Cannot set I gain: Not connected")
            return

        try:
            # Format command with 6 decimal places for precision
            command = f"igain {value:.6f}"
            print(f"ℹ Setting I gain to {value:.6f}...")

            if self.serial_worker:
                # Queue command with verification
                self.serial_worker.execute_command(
                    command=command,
                    verify_command="igain",
                    expected_response=f"{value:.6f}"
                )
            else:
                # Fallback to direct command if worker not available
                self.my_serial.sendToBox(command)
                time.sleep(0.1)
                print(f"✓ I gain set to {value:.6f} (no verification)")

        except Exception as e:
            print(f"✗ Error setting I gain: {e}")
            if hasattr(self, 'statusbar'):
                self.statusbar.showMessage(f"Error setting I gain: {e}", 5000)

    def on_dgain_changed(self, value):
        """Handle D gain spinbox value change"""
        if not self.my_serial.is_connected():
            print("✗ Cannot set D gain: Not connected")
            return

        try:
            # Format command with 6 decimal places for precision
            command = f"dgain {value:.6f}"
            print(f"ℹ Setting D gain to {value:.6f}...")

            if self.serial_worker:
                # Queue command with verification
                self.serial_worker.execute_command(
                    command=command,
                    verify_command="dgain",
                    expected_response=f"{value:.6f}"
                )
            else:
                # Fallback to direct command if worker not available
                self.my_serial.sendToBox(command)
                time.sleep(0.1)
                print(f"✓ D gain set to {value:.6f} (no verification)")

        except Exception as e:
            print(f"✗ Error setting D gain: {e}")
            if hasattr(self, 'statusbar'):
                self.statusbar.showMessage(f"Error setting D gain: {e}", 5000)

    def on_ain1_enable_changed(self, state):
        """Handle ain1 (laser current modulation) enable checkbox change"""
        if not self.my_serial.is_connected():
            print("✗ Cannot set ain1 enable: Not connected")
            return

        try:
            # When enabling: enable spinbox and set to current gain value
            # When disabling: disable spinbox and set gain to 0
            if state == QtCore.Qt.CheckState.Checked.value:
                # Enable the spinbox
                if hasattr(self, 'doubleSpinBox_ain1CurrGain'):
                    self.doubleSpinBox_ain1CurrGain.setEnabled(True)
                    gain_value = self.doubleSpinBox_ain1CurrGain.value()
                else:
                    gain_value = 0.0
                command = f"lmodgain {gain_value:.6f}"
                print(f"ℹ Enabling laser current modulation (ain1) with gain {gain_value:.6f}...")
            else:
                # Disable the spinbox and set gain to 0
                if hasattr(self, 'doubleSpinBox_ain1CurrGain'):
                    self.doubleSpinBox_ain1CurrGain.setEnabled(False)
                gain_value = 0.0
                command = "lmodgain 0.0"
                print(f"ℹ Disabling laser current modulation (ain1)...")

            if self.serial_worker:
                # Queue command with verification
                self.serial_worker.execute_command(
                    command=command,
                    verify_command="lmodgain",
                    expected_response=f"{gain_value:.6f}"
                )
            else:
                # Fallback to direct command if worker not available
                self.my_serial.sendToBox(command)
                time.sleep(0.1)
                print(f"✓ Laser current modulation {'enabled' if state == QtCore.Qt.CheckState.Checked.value else 'disabled'} (no verification)")

        except Exception as e:
            print(f"✗ Error setting ain1 enable: {e}")
            if hasattr(self, 'statusbar'):
                self.statusbar.showMessage(f"Error setting ain1 enable: {e}", 5000)

    def on_ain1_curr_gain_changed(self, value):
        """Handle ain1 current gain spinbox value change"""
        if not self.my_serial.is_connected():
            print("✗ Cannot set ain1 current gain: Not connected")
            return

        try:
            # Format command with 6 decimal places for precision
            command = f"lmodgain {value:.6f}"
            print(f"ℹ Setting laser current modulation gain to {value:.6f}...")

            if self.serial_worker:
                # Queue command with verification
                self.serial_worker.execute_command(
                    command=command,
                    verify_command="lmodgain",
                    expected_response=f"{value:.6f}"
                )
            else:
                # Fallback to direct command if worker not available
                self.my_serial.sendToBox(command)
                time.sleep(0.1)
                print(f"✓ Laser current modulation gain set to {value:.6f} (no verification)")

        except Exception as e:
            print(f"✗ Error setting ain1 current gain: {e}")
            if hasattr(self, 'statusbar'):
                self.statusbar.showMessage(f"Error setting ain1 current gain: {e}", 5000)

    def on_ain2_enable_changed(self, state):
        """Handle ain2 (temperature modulation) enable checkbox change"""
        if not self.my_serial.is_connected():
            print("✗ Cannot set ain2 enable: Not connected")
            return

        try:
            # When enabling: enable spinbox and set to current gain value
            # When disabling: disable spinbox and set gain to 0
            if state == QtCore.Qt.CheckState.Checked.value:
                # Enable the spinbox
                if hasattr(self, 'doubleSpinBox_ain2TempGain'):
                    self.doubleSpinBox_ain2TempGain.setEnabled(True)
                    gain_value = self.doubleSpinBox_ain2TempGain.value()
                else:
                    gain_value = 0.0
                command = f"tmodgain {gain_value:.6f}"
                print(f"ℹ Enabling temperature modulation (ain2) with gain {gain_value:.6f}...")
            else:
                # Disable the spinbox and set gain to 0
                if hasattr(self, 'doubleSpinBox_ain2TempGain'):
                    self.doubleSpinBox_ain2TempGain.setEnabled(False)
                gain_value = 0.0
                command = "tmodgain 0.0"
                print(f"ℹ Disabling temperature modulation (ain2)...")

            if self.serial_worker:
                # Queue command with verification
                self.serial_worker.execute_command(
                    command=command,
                    verify_command="tmodgain",
                    expected_response=f"{gain_value:.6f}"
                )
            else:
                # Fallback to direct command if worker not available
                self.my_serial.sendToBox(command)
                time.sleep(0.1)
                print(f"✓ Temperature modulation {'enabled' if state == QtCore.Qt.CheckState.Checked.value else 'disabled'} (no verification)")

        except Exception as e:
            print(f"✗ Error setting ain2 enable: {e}")
            if hasattr(self, 'statusbar'):
                self.statusbar.showMessage(f"Error setting ain2 enable: {e}", 5000)

    def on_ain2_temp_gain_changed(self, value):
        """Handle ain2 temperature gain spinbox value change"""
        if not self.my_serial.is_connected():
            print("✗ Cannot set ain2 temperature gain: Not connected")
            return

        try:
            # Format command with 6 decimal places for precision
            command = f"tmodgain {value:.6f}"
            print(f"ℹ Setting temperature modulation gain to {value:.6f}...")

            if self.serial_worker:
                # Queue command with verification
                self.serial_worker.execute_command(
                    command=command,
                    verify_command="tmodgain",
                    expected_response=f"{value:.6f}"
                )
            else:
                # Fallback to direct command if worker not available
                self.my_serial.sendToBox(command)
                time.sleep(0.1)
                print(f"✓ Temperature modulation gain set to {value:.6f} (no verification)")

        except Exception as e:
            print(f"✗ Error setting ain2 temperature gain: {e}")
            if hasattr(self, 'statusbar'):
                self.statusbar.showMessage(f"Error setting ain2 temperature gain: {e}", 5000)

    def on_rtmax_changed(self, value):
        """Handle rtmax (maximum temperature resistance) spinbox value change"""
        if not self.my_serial.is_connected():
            print("✗ Cannot set rtmax: Not connected")
            return

        try:
            command = f"rtmax {value:.6f}"
            print(f"ℹ Setting maximum temperature resistance to {value:.3f} Ω...")

            if self.serial_worker:
                self.serial_worker.execute_command(
                    command=command,
                    verify_command="rtmax",
                    expected_response=f"{value:.6f}"
                )
            else:
                self.my_serial.sendToBox(command)
                time.sleep(0.1)
                print(f"✓ Maximum temperature resistance set to {value:.3f} Ω (no verification)")

        except Exception as e:
            print(f"✗ Error setting rtmax: {e}")
            if hasattr(self, 'statusbar'):
                self.statusbar.showMessage(f"Error setting rtmax: {e}", 5000)

    def on_rtmin_changed(self, value):
        """Handle rtmin (minimum temperature resistance) spinbox value change"""
        if not self.my_serial.is_connected():
            print("✗ Cannot set rtmin: Not connected")
            return

        try:
            command = f"rtmin {value:.6f}"
            print(f"ℹ Setting minimum temperature resistance to {value:.3f} Ω...")

            if self.serial_worker:
                self.serial_worker.execute_command(
                    command=command,
                    verify_command="rtmin",
                    expected_response=f"{value:.6f}"
                )
            else:
                self.my_serial.sendToBox(command)
                time.sleep(0.1)
                print(f"✓ Minimum temperature resistance set to {value:.3f} Ω (no verification)")

        except Exception as e:
            print(f"✗ Error setting rtmin: {e}")
            if hasattr(self, 'statusbar'):
                self.statusbar.showMessage(f"Error setting rtmin: {e}", 5000)

    def on_vtmax_changed(self, value):
        """Handle vtmax (maximum TEC voltage) spinbox value change"""
        if not self.my_serial.is_connected():
            print("✗ Cannot set vtmax: Not connected")
            return

        try:
            command = f"vtmax {value:.6f}"
            print(f"ℹ Setting maximum TEC voltage to {value:.3f} V...")

            if self.serial_worker:
                self.serial_worker.execute_command(
                    command=command,
                    verify_command="vtmax",
                    expected_response=f"{value:.6f}"
                )
            else:
                self.my_serial.sendToBox(command)
                time.sleep(0.1)
                print(f"✓ Maximum TEC voltage set to {value:.3f} V (no verification)")

        except Exception as e:
            print(f"✗ Error setting vtmax: {e}")
            if hasattr(self, 'statusbar'):
                self.statusbar.showMessage(f"Error setting vtmax: {e}", 5000)

    def on_vtmin_changed(self, value):
        """Handle vtmin (minimum TEC voltage) spinbox value change"""
        if not self.my_serial.is_connected():
            print("✗ Cannot set vtmin: Not connected")
            return

        try:
            command = f"vtmin {value:.6f}"
            print(f"ℹ Setting minimum TEC voltage to {value:.3f} V...")

            if self.serial_worker:
                self.serial_worker.execute_command(
                    command=command,
                    verify_command="vtmin",
                    expected_response=f"{value:.6f}"
                )
            else:
                self.my_serial.sendToBox(command)
                time.sleep(0.1)
                print(f"✓ Minimum TEC voltage set to {value:.3f} V (no verification)")

        except Exception as e:
            print(f"✗ Error setting vtmin: {e}")
            if hasattr(self, 'statusbar'):
                self.statusbar.showMessage(f"Error setting vtmin: {e}", 5000)


    def on_save_settings_clicked(self):
        """Handle Save Settings button click - saves current values to config file"""
        try:
            print("ℹ Saving settings to config file...")

            # Get current values from UI
            laser_current = self.doubleSpinBox_LaserCurrent.value()
            temperature_setpoint = self.doubleSpinBox_SetTemperature.value()
            p_gain = self.doubleSpinBox_P.value()
            i_gain = self.doubleSpinBox_I.value()
            d_gain = self.doubleSpinBox_D.value()

            # Get modulation control values
            ain1_enable = 0
            ain1_curr_gain = 0.0
            ain2_enable = 0
            ain2_temp_gain = 0.0

            if hasattr(self, 'checkBox_ain1Enable'):
                ain1_enable = 1 if self.checkBox_ain1Enable.isChecked() else 0
            if hasattr(self, 'doubleSpinBox_ain1CurrGain'):
                ain1_curr_gain = self.doubleSpinBox_ain1CurrGain.value()
            if hasattr(self, 'checkBox_ain2Enable'):
                ain2_enable = 1 if self.checkBox_ain2Enable.isChecked() else 0
            if hasattr(self, 'doubleSpinBox_ain2TempGain'):
                ain2_temp_gain = self.doubleSpinBox_ain2TempGain.value()

            # Get temperature and voltage limit values
            rtmax = 15000.0
            rtmin = 5000.0
            vtmax = 2.0
            vtmin = -2.0

            if hasattr(self, 'doubleSpinBox_rtmax'):
                rtmax = self.doubleSpinBox_rtmax.value()
            if hasattr(self, 'doubleSpinBox_rtmin'):
                rtmin = self.doubleSpinBox_rtmin.value()
            if hasattr(self, 'doubleSpinBox_vtmax'):
                vtmax = self.doubleSpinBox_vtmax.value()
            if hasattr(self, 'doubleSpinBox_vtmin'):
                vtmin = self.doubleSpinBox_vtmin.value()

            # Save laser settings (including ain1 modulation)
            self.config.set_laser_config(
                ilaser=laser_current,
                ain1_enable=ain1_enable,
                ain1_curr_gain=ain1_curr_gain
            )

            # Save TEC settings (temperature, PID gains, ain2 modulation, and limits)
            self.config.set_tec_config(
                rtset=temperature_setpoint,
                pgain=p_gain,
                igain=i_gain,
                dgain=d_gain,
                ain2_enable=ain2_enable,
                ain2_temp_gain=ain2_temp_gain,
                rtmax=rtmax,
                rtmin=rtmin,
                vtmax=vtmax,
                vtmin=vtmin
            )

            print(f"✓ Settings saved: Laser={laser_current:.3f}mA, Temp={temperature_setpoint:.3f}Ω, P={p_gain:.6f}, I={i_gain:.6f}, D={d_gain:.6f}, ain1={ain1_enable}, ain1_gain={ain1_curr_gain:.6f}, ain2={ain2_enable}, ain2_gain={ain2_temp_gain:.6f}")

            if hasattr(self, 'statusbar'):
                self.statusbar.showMessage("Settings saved to config file", 3000)

        except Exception as e:
            print(f"✗ Error saving settings: {e}")
            if hasattr(self, 'statusbar'):
                self.statusbar.showMessage(f"Error saving settings: {e}", 5000)

    def on_tab_changed(self, index):
        """Handle tab change event - read device values when tabs are activated"""
        if not self.my_serial.is_connected():
            return

        try:
            # Get the name of the activated tab
            tab_name = self.tabWidget.tabText(index)
            print(f"ℹ Tab changed to: {tab_name}")

            # Only read settings if connected
            if not self.my_serial.is_connected():
                return

            # If Laser Control tab is activated, read and update settings
            # Note: Temperature controls are now in Laser tab
            if tab_name == "Laser Control" or tab_name == "Laser":
                # Delay reading to allow widgets to fully initialize
                # Check if widgets exist before trying to update them
                if hasattr(self, 'doubleSpinBox_LaserCurrent') and self.doubleSpinBox_LaserCurrent is not None:
                    QtCore.QTimer.singleShot(150, self._read_and_update_laser_current)
                if hasattr(self, 'doubleSpinBox_SetTemperature') and self.doubleSpinBox_SetTemperature is not None:
                    QtCore.QTimer.singleShot(250, self._read_and_update_temperature_setpoint)

        except Exception as e:
            print(f"✗ Error handling tab change: {e}")

    def _read_and_update_temperature_setpoint(self):
        """Read temperature setpoint and PID parameters from device and update spinboxes"""
        try:
            if not self.my_serial.is_connected():
                return

            print("ℹ Reading temperature settings from device...")

            # Pause the worker thread temporarily to avoid serial conflicts
            if self.serial_worker:
                self.serial_worker.paused = True
                time.sleep(0.2)  # Give more time for current operation to finish

            # Flush input buffer to clear any stale data from previous polling
            if hasattr(self.my_serial.box, "flushInput"):
                self.my_serial.box.flushInput()
                time.sleep(0.05)

            # Define parameters with safety checks for widget existence
            params = []

            # Only add parameters if widgets exist and are valid
            if hasattr(self, 'doubleSpinBox_SetTemperature') and self.doubleSpinBox_SetTemperature is not None:
                try:
                    self.doubleSpinBox_SetTemperature.objectName()  # Test if widget is valid
                    params.append(("rtset", self.doubleSpinBox_SetTemperature, "Ω", 3))
                except RuntimeError:
                    print("⚠ doubleSpinBox_SetTemperature has been deleted, skipping")

            if hasattr(self, 'doubleSpinBox_P') and self.doubleSpinBox_P is not None:
                try:
                    self.doubleSpinBox_P.objectName()
                    params.append(("pgain", self.doubleSpinBox_P, "", 6))
                except RuntimeError:
                    print("⚠ doubleSpinBox_P has been deleted, skipping")

            if hasattr(self, 'doubleSpinBox_I') and self.doubleSpinBox_I is not None:
                try:
                    self.doubleSpinBox_I.objectName()
                    params.append(("igain", self.doubleSpinBox_I, "", 6))
                except RuntimeError:
                    print("⚠ doubleSpinBox_I has been deleted, skipping")

            if hasattr(self, 'doubleSpinBox_D') and self.doubleSpinBox_D is not None:
                try:
                    self.doubleSpinBox_D.objectName()
                    params.append(("dgain", self.doubleSpinBox_D, "", 6))
                except RuntimeError:
                    print("⚠ doubleSpinBox_D has been deleted, skipping")

            if not params:
                print("⚠ No valid spinbox widgets found, skipping temperature settings read")
                if self.serial_worker:
                    self.serial_worker.paused = False
                return

            # Read all parameters with retry logic
            for command, spinbox, unit, decimals in params:
                success = False
                for attempt in range(3):  # Try up to 3 times
                    # Ensure 'response' exists even if an exception occurs before it's assigned
                    response = None
                    try:
                        if attempt > 0:
                            print(f"ℹ Retry {attempt} for {command}...")
                            # Flush buffer before retry
                            if hasattr(self.my_serial.box, "flushInput"):
                                self.my_serial.box.flushInput()
                            time.sleep(0.1)

                        # Send command
                        self.my_serial.sendToBox(command)
                        time.sleep(0.15)

                        # Read echo and discard
                        echo = self.my_serial.readLine()
                        time.sleep(0.1)

                        # Read actual response
                        response = self.my_serial.readLine()

                        if response:
                            value = float(response.strip())

                            # Update spinbox with safety check
                            try:
                                spinbox.blockSignals(True)
                                spinbox.setValue(value)
                                spinbox.blockSignals(False)

                                print(f"ℹ Current {command}: {value:.{decimals}f} {unit}")
                                print(f"✓ {command} spinbox updated to {value:.{decimals}f} {unit}")
                                success = True
                                break
                            except RuntimeError:
                                print(f"⚠ Spinbox for {command} was deleted, skipping update")
                                success = True  # Don't retry if widget is gone
                                break
                        else:
                            print(f"ℹ No response for {command} (attempt {attempt+1})")

                    except ValueError as e:
                        response_text = response if response is not None else 'unknown'
                        print(f"✗ Error parsing {command}: {e} (response: '{response_text}', attempt {attempt+1})")
                    except RuntimeError as e:
                        print(f"✗ Widget deleted for {command}: {e} (attempt {attempt+1})")
                        break  # Don't retry if widget is deleted
                    except Exception as e:
                        print(f"✗ Error reading {command}: {e} (attempt {attempt+1})")

                    # Small delay before retry
                    if attempt < 2:
                        time.sleep(0.1)

                if not success:
                    print(f"✗ Failed to read {command} after 3 attempts")

            # Resume the worker thread
            if self.serial_worker:
                self.serial_worker.paused = False

        except Exception as e:
            print(f"✗ Error reading temperature settings: {e}")
            import traceback
            traceback.print_exc()
            # Make sure to resume worker on error
            if self.serial_worker:
                self.serial_worker.paused = False

    def _read_and_update_laser_current(self):
        """Read laser current setpoint from device and update spinbox"""
        try:
            if not self.my_serial.is_connected():
                return

            # Check if spinbox exists before trying to read
            if not hasattr(self, 'doubleSpinBox_LaserCurrent') or self.doubleSpinBox_LaserCurrent is None:
                print("⚠ Laser current spinbox not available, skipping read")
                return

            # Verify widget is still valid
            try:
                self.doubleSpinBox_LaserCurrent.objectName()
            except RuntimeError:
                print("⚠ Laser current spinbox has been deleted, skipping read")
                return

            print("ℹ Reading laser current setpoint from device...")

            # Pause the worker thread temporarily to avoid serial conflicts
            if self.serial_worker:
                self.serial_worker.paused = True
                time.sleep(0.1)  # Let current operation finish

            # Send command to read laser current (ilaser)
            self.my_serial.sendToBox("ilaser")
            time.sleep(0.2)  # Longer delay for response

            # Read response
            response = self.my_serial.readLine()

            # Resume the worker thread
            if self.serial_worker:
                self.serial_worker.paused = False

            if response:
                try:
                    laser_current = float(response.strip())
                    print(f"ℹ Current laser current setpoint: {laser_current:.3f} mA")

                    # Update spinbox with safety check
                    try:
                        self.doubleSpinBox_LaserCurrent.blockSignals(True)
                        self.doubleSpinBox_LaserCurrent.setValue(laser_current)
                        self.doubleSpinBox_LaserCurrent.blockSignals(False)
                        print(f"✓ Laser current spinbox updated to {laser_current:.3f} mA")
                    except RuntimeError:
                        print("⚠ Laser current spinbox was deleted during update")

                except ValueError as e:
                    print(f"✗ Error parsing laser current: {e}")
            else:
                print("ℹ Could not read laser current (no response)")

        except Exception as e:
            print(f"✗ Error reading laser current: {e}")
            # Make sure to resume worker on error
            if self.serial_worker:
                self.serial_worker.paused = False

    def _debounce_call(self, handler_name, value):
        """Store the last value for handler_name and (re)start a single-shot timer.

        When the timer fires, _debounce_timeout will call the real handler with the
        last stored value. This prevents frequent rapid calls to handlers.
        """
        try:
            # Save last value
            self._debounce_values[handler_name] = value

            # If a timer already exists, restart it
            timer = self._debounce_timers.get(handler_name)
            if timer is not None:
                timer.stop()
                timer.start()
                return

            # Create a new single-shot QTimer
            timer = QtCore.QTimer(self.main_window if self.main_window is not None else None)
            timer.setSingleShot(True)
            # Use a bound lambda to capture handler_name
            timer.timeout.connect(lambda hn=handler_name: self._debounce_timeout(hn))
            self._debounce_timers[handler_name] = timer
            timer.start(self._debounce_interval_ms)
        except Exception as e:
            print(f"⚠ Error in _debounce_call for {handler_name}: {e}")

    def _debounce_timeout(self, handler_name):
        """Called when debounce timer fires; invokes the real handler with last value."""
        try:
            value = self._debounce_values.pop(handler_name, None)
            timer = self._debounce_timers.pop(handler_name, None)
            if timer is not None:
                try:
                    timer.stop()
                except Exception:
                    pass

            if value is None:
                return

            handler = getattr(self, handler_name, None)
            if handler is None:
                print(f"⚠ Debounced handler '{handler_name}' not found")
                return

            # Call the original handler with the last value
            handler(value)
        except Exception as e:
            print(f"✗ Exception in _debounce_timeout for {handler_name}: {e}")


def main():
    app = QtWidgets.QApplication(sys.argv)
    # Apply PyQt6-compatible dark theme
    app.setStyleSheet(qdarktheme.load_stylesheet())

    MainWindow = QtWidgets.QMainWindow()
    ui = MyUi()
    ui.setupUi(MainWindow)
    MainWindow.showMaximized()

    sys.exit(app.exec())


if __name__ == '__main__':
    main()
