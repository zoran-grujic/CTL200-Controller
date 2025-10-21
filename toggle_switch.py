"""
Animated Toggle Switch Widget for PyQt6
Android/iOS style switch button with smooth animations
"""

from PyQt6.QtWidgets import QCheckBox
from PyQt6.QtCore import (QPropertyAnimation, QEasingCurve, QRectF,
                          Qt, pyqtProperty, QSize)
from PyQt6.QtGui import QPainter, QColor, QPen, QBrush
from PyQt6.QtWidgets import QAbstractButton


class AnimatedToggle(QCheckBox):
    """
    Animated toggle switch that looks like Android/iOS switches

    Features:
    - Smooth sliding animation
    - Customizable colors
    - Hover effects
    - Works as a QCheckBox (can use isChecked(), toggled signal, etc.)
    """

    def __init__(self, parent=None,
                 bar_color_on=QColor(0, 150, 0),      # Green when ON
                 bar_color_off=QColor(100, 100, 100), # Gray when OFF
                 handle_color=QColor(255, 255, 255),   # White handle
                 pulse_on_color=QColor(0, 200, 0)):    # Bright green pulse
        super().__init__(parent)

        # Colors
        self._bar_color_on = bar_color_on
        self._bar_color_off = bar_color_off
        self._handle_color = handle_color
        self._pulse_on_color = pulse_on_color

        # Dimensions
        self._bar_width = 60
        self._bar_height = 28
        self._handle_radius = 22

        # Animation properties
        self._handle_position = 0
        self._pulse_radius = 0
        self._animation_enabled = True  # Track if animation should run

        # Setup animations
        self._animation = QPropertyAnimation(self, b"handle_position", self)
        self._animation.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self._animation.setDuration(200)  # 200ms animation

        # Connect signals
        self.stateChanged.connect(self._on_state_changed)

        # Set initial size
        self.setFixedSize(QSize(self._bar_width, self._bar_height))

    def _on_state_changed(self, state):
        """Handle state change and animate"""
        self._animation.stop()
        if state == Qt.CheckState.Checked.value:
            self._animation.setStartValue(self._handle_position)
            self._animation.setEndValue(self._bar_width - self._handle_radius)
        else:
            self._animation.setStartValue(self._handle_position)
            self._animation.setEndValue(0)

        if self._animation_enabled:
            self._animation.start()
        else:
            # Jump immediately without animation
            self._handle_position = self._animation.endValue()
            self.update()

    def setChecked(self, checked):
        """Override setChecked to handle initial position properly"""
        # Store signals blocked state
        was_blocked = self.signalsBlocked()

        # Call parent setChecked
        super().setChecked(checked)

        # If signals were blocked, manually update handle position since _on_state_changed won't be called
        if was_blocked:
            if checked:
                self._handle_position = self._bar_width - self._handle_radius
            else:
                self._handle_position = 0
            self.update()

    @pyqtProperty(int)
    def handle_position(self):
        return self._handle_position

    @handle_position.setter
    def handle_position(self, pos):
        self._handle_position = pos
        self.update()

    def paintEvent(self, event):
        """Custom painting for the toggle switch"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Calculate dimensions
        bar_rect = QRectF(0, (self._bar_height - 20) / 2,
                         self._bar_width, 20)
        handle_x = self._handle_position + (self._handle_radius / 2)
        handle_y = self._bar_height / 2

        # Draw the bar (background track)
        if self.isChecked():
            bar_brush = QBrush(self._bar_color_on)
        else:
            bar_brush = QBrush(self._bar_color_off)

        painter.setBrush(bar_brush)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(bar_rect, 10, 10)

        # Draw glow effect when ON
        if self.isChecked() and self._pulse_radius > 0:
            glow_color = QColor(self._pulse_on_color)
            glow_color.setAlpha(50)
            painter.setBrush(QBrush(glow_color))
            painter.drawEllipse(QRectF(handle_x - self._pulse_radius,
                                      handle_y - self._pulse_radius,
                                      self._pulse_radius * 2,
                                      self._pulse_radius * 2))

        # Draw the handle (sliding circle)
        handle_brush = QBrush(self._handle_color)
        painter.setBrush(handle_brush)

        # Add subtle shadow
        shadow_color = QColor(0, 0, 0, 30)
        painter.setPen(QPen(shadow_color, 2))
        painter.drawEllipse(QRectF(handle_x - (self._handle_radius / 2) + 1,
                                   handle_y - (self._handle_radius / 2) + 1,
                                   self._handle_radius - 2,
                                   self._handle_radius - 2))

        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QRectF(handle_x - (self._handle_radius / 2),
                                   handle_y - (self._handle_radius / 2),
                                   self._handle_radius,
                                   self._handle_radius))

    def hitButton(self, pos):
        """Override to make entire widget clickable"""
        return self.contentsRect().contains(pos)

    def sizeHint(self):
        """Return the recommended size"""
        return QSize(self._bar_width, self._bar_height)


class LaserToggle(AnimatedToggle):
    """
    Specialized toggle for laser control with danger colors
    RED when OFF (danger - will turn on)
    GREEN when ON (safe - already on)
    """

    def __init__(self, parent=None):
        super().__init__(
            parent=parent,
            bar_color_on=QColor(76, 175, 80),    # Material Green
            bar_color_off=QColor(200, 50, 50),   # Red (danger)
            handle_color=QColor(255, 255, 255),
            pulse_on_color=QColor(100, 255, 100)
        )


class TECToggle(AnimatedToggle):
    """
    Specialized toggle for TEC control - identical appearance to Laser toggle
    RED when OFF (danger - inactive)
    GREEN when ON (safe - active)
    """

    def __init__(self, parent=None):
        super().__init__(
            parent=parent,
            bar_color_on=QColor(76, 175, 80),    # Material Green (same as Laser)
            bar_color_off=QColor(200, 50, 50),   # Red (same as Laser)
            handle_color=QColor(255, 255, 255),
            pulse_on_color=QColor(100, 255, 100)
        )


# Test/Demo
if __name__ == "__main__":
    from PyQt6.QtWidgets import QApplication, QWidget, QVBoxLayout, QLabel
    import sys

    app = QApplication(sys.argv)

    window = QWidget()
    layout = QVBoxLayout()

    # Generic toggle
    toggle1 = AnimatedToggle()
    layout.addWidget(QLabel("Generic Toggle:"))
    layout.addWidget(toggle1)

    # Laser toggle
    laser_toggle = LaserToggle()
    layout.addWidget(QLabel("\nLaser Control (Red=OFF, Green=ON):"))
    layout.addWidget(laser_toggle)
    laser_toggle.toggled.connect(lambda state: print(f"Laser: {'ON' if state else 'OFF'}"))

    # TEC toggle
    tec_toggle = TECToggle()
    layout.addWidget(QLabel("\nTEC Control (Gray=OFF, Orange=ON):"))
    layout.addWidget(tec_toggle)
    tec_toggle.toggled.connect(lambda state: print(f"TEC: {'ON' if state else 'OFF'}"))

    layout.addStretch()
    window.setLayout(layout)
    window.setWindowTitle("Animated Toggle Switches")
    window.show()

    sys.exit(app.exec())

