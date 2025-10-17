import sys
import qdarkgraystyle
import numpy as np
import serial.tools.list_ports

# Configure PyQtGraph to use PyQt6 BEFORE importing it
import os

os.environ['PYQTGRAPH_QT_LIB'] = 'PyQt6'

import pyqtgraph as pg
from PyQt6 import QtCore, QtWidgets
from PyQt6.QtCore import QThreadPool

# Import the generated UI class
from gui import Ui_MainWindow
# Import MySerial class
from class_MySerial import MySerial


class MyUi(Ui_MainWindow):
    """
    Minimal UI template with just two tabs:
    1. Serial selection tab
    2. Simple plot tab
    """

    def __init__(self):
        super(MyUi, self).__init__()
        self.threadpool = QThreadPool()
        print("Multithreading with maximum %d threads" % self.threadpool.maxThreadCount())

        # For serial communication
        self.my_serial = MySerial()  # Use MySerial class instead of direct serial

        # Create the plot widget instance after proper setup
        self.plotWindow = None
        self.arbPlot = None

    def setupUi(self, MainWindow):
        # Call the parent setupUi method
        super(MyUi, self).setupUi(MainWindow)

        # Create pyqtgraph widgets after UI setup
        self.plotWindow = pg.GraphicsLayoutWidget()
        PG_layout = pg.GraphicsLayout()
        self.arbPlot = PG_layout.addPlot()
        self.arbPlot.setLabel('bottom', "time (ms)")
        self.arbPlot.setLabel('left', "S (V)")
        self.plotWindow.setCentralItem(PG_layout)

        # Replace the plot placeholder
        try:
            # Instead of using layout manipulation, just clear and add
            plot_container = self.tab_plot.layout()
            if plot_container is None:
                plot_container = QtWidgets.QVBoxLayout(self.tab_plot)

            # Remove all widgets from the layout
            while plot_container.count():
                item = plot_container.takeAt(0)
                if item.widget():
                    item.widget().hide()
                    item.widget().deleteLater()

            # Add our plot widget
            plot_container.addWidget(self.plotWindow)
        except Exception as e:
            print(f"Error setting up plot: {e}")

        # Connect buttons and initialize
        try:
            self.pushButton_RefreshPorts.clicked.connect(self.refresh_serial_ports)
        except AttributeError:
            print("Warning: pushButton_RefreshPorts not found in the UI file")

        try:
            self.pushButton_Connect.clicked.connect(self.connect_serial)
        except AttributeError:
            print("Warning: pushButton_Connect not found in the UI file")

        # Set tab index to first tab
        self.tabWidget.setCurrentIndex(0)

        # Refresh serial ports initially
        self.refresh_serial_ports()

        # Plot some example data
        self.plot_example_data()

    def refresh_serial_ports(self):
        """Refresh the list of available serial ports"""
        try:
            self.comboBox_serial.clear()
        except AttributeError:
            print("Warning: comboBox_serial not found in the UI file")
            return

        # Use MySerial's method to get available ports
        ports = self.my_serial.serial_ports()
        
        for port in ports:
            self.comboBox_serial.addItem(f"{port}")

        if not ports:
            self.comboBox_serial.addItem("No serial ports found")

    def connect_serial(self):
        """Connect to the selected serial port using MySerial class"""
        if not hasattr(self, 'comboBox_serial'):
            print("Warning: comboBox_serial not found in the UI file")
            return

        port_text = self.comboBox_serial.currentText()
        if port_text == "No serial ports found":
            return

        # Extract port name (it's already just the port name with our new implementation)
        port_name = port_text

        # Set the port in MySerial and connect
        self.my_serial.port = port_name
        
        if hasattr(self, 'label_status'):
            try:
                success = self.my_serial.connect()
                if success:
                    self.label_status.setText(f"Connected to {self.my_serial.name}")
                else:
                    self.label_status.setText(f"Failed to connect to {port_name}")
            except Exception as e:
                self.label_status.setText(f"Error: {str(e)}")
        else:
            print(f"Would connect to {port_name}")

    def plot_example_data(self):
        """Plot example data in the arbPlot"""
        # Generate some example data - sine wave
        t = np.linspace(0, 100, 1000)  # 100ms timespan
        signal = np.sin(2 * np.pi * 0.05 * t) * np.exp(-t / 50)  # Damped sine wave

        self.arbPlot.clear()
        self.arbPlot.plot(t, signal, pen='w')


def main():
    app = QtWidgets.QApplication(sys.argv)
    app.setStyleSheet(qdarkgraystyle.load_stylesheet())

    MainWindow = QtWidgets.QMainWindow()
    ui = MyUi()
    ui.setupUi(MainWindow)
    MainWindow.showMaximized()

    # PyQt5 to PyQt6 change: exec_() -> exec()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
