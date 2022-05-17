#!/usr/bin/env python3
# coding=utf-8
#
# Copyright (c) 2021-2022 Antonio González

from collections import deque
from datetime import datetime
from itertools import count
import os
import sys
import time

import numpy as np
from PyQt6 import uic
from PyQt6.QtCore import pyqtSlot, QTimer
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (QDialog, QFileDialog, QColorDialog,
    QMainWindow, QMessageBox, QApplication)
import pyqtgraph as pg
from serial import Serial, SerialException
from serial.tools import list_ports

# GUI parameters
GUI_REFRESH_RATE = 100  # In milliseconds
WIN_WIDTH_SAMPLES = 500
CURVE_COLOUR = "#7fff00"
BACKGROUND_COLOUR = "black"
TEXT_COLOUR = "#707070"

# Serial parameters
BAUD_DEFAULT = 115200
# This is a subset of the standard baud rates supported by all
# platforms
# (https://pythonhosted.org/pyserial/pyserial_api.html#serial.Serial
BAUD_RATES = (9600, 19200, 38400, 57600, 115200)

# Format for saving text. Passed to numpy.savetxt
SAVE_FMT = '%.4f'


class SettingsDialog(QDialog):
    """
    Settings dialog
    """
    def __init__(self, settings, parent=None):
        super().__init__(parent)
        uic.loadUi("ui/settings_dlg.ui", self)
        self.settings = settings

        # Baud
        for (index, baud) in zip(count(), BAUD_RATES):
            self.baudComboBox.addItem(str(baud))
            if baud == settings.baud:
                self.baudComboBox.setCurrentIndex(index)

        # Port
        for (index, port) in zip(count(), settings.available_ports):
            port_str = f'{port.device} -- {port.manufacturer}'
            self.portComboBox.addItem(port_str)
            if port.device == self.settings.port:
                self.portComboBox.setCurrentIndex(index)

        # Path
        self.savePathLabel.setText(settings.save_path)

        # UI settings
        self.widthSpinBox.setValue(settings.width)
        self.firstIsXcheckBox.setChecked(settings.first_is_x)
        self.colourPushButton.setStyleSheet(
            f"background-color : {self.settings.curve_colour}")
        self.bgColourPushButton.setStyleSheet(
            f"background-color : {self.settings.background_colour}")

    @pyqtSlot()
    def on_portRefreshButton_clicked(self):
        self.portComboBox.clear()
        self.settings.scan_ports()
        for port in self.settings.available_ports:
            port_str = f'{port.device} -- {port.manufacturer}'
            self.portComboBox.addItem(port_str)

    @pyqtSlot()
    def on_savePathButton_clicked(self):
        path = QFileDialog.getExistingDirectory(
            self, caption='Select default data directory')
        if path:
            self.savePathLabel.setText(path)

    @pyqtSlot()
    def on_colourPushButton_clicked(self):
        dialog = QColorDialog(self)
        dialog.setCurrentColor(QColor(self.settings.curve_colour))
        if dialog.exec():
            self.settings.curve_colour = dialog.currentColor().name()
            self.colourPushButton.setStyleSheet(
                f"background-color : {self.settings.curve_colour}")

    @pyqtSlot()
    def on_bgColourPushButton_clicked(self):
        dialog = QColorDialog(self)
        dialog.setCurrentColor(QColor(self.settings.background_colour))
        if dialog.exec():
            self.settings.background_colour = dialog.currentColor().name()
            self.bgColourPushButton.setStyleSheet(
                f"background-color : {self.settings.background_colour}")


class Settings:
    def __init__(self):
        # Connection settings
        self.baud = BAUD_DEFAULT
        self.port = ''
        self.scan_ports()
        self.first_is_x = False
        if len(self.available_ports) > 0:
            self.port = self.available_ports[0].device

        # UI settings
        self.width = WIN_WIDTH_SAMPLES
        self.curve_colour = CURVE_COLOUR
        self.background_colour = BACKGROUND_COLOUR

        # Path for saving data
        self.save_path = os.path.expanduser("~")

    def scan_ports(self):
        self.available_ports = list_ports.comports()
        for port in self.available_ports:
            if port.manufacturer is None:
                self.available_ports.remove(port)
        self.available_ports.sort()


class MainWindow(QMainWindow):
    """
    Data acquisition main window
    """
    def __init__(self):
        super().__init__()
        uic.loadUi("ui/main.ui", self)
        self._gui_refresh_rate = GUI_REFRESH_RATE
        self.playButton.setEnabled(True)
        self.stopButton.setEnabled(False)
        self.settings = Settings()
        
        # Data container
        self.data = []

        # Create a timer to update the plot at regular intervals
        self.timer = QTimer()
        self.timer.setInterval(GUI_REFRESH_RATE)
        self.timer.timeout.connect(self.update_plot)

    @pyqtSlot()
    def on_quitButton_clicked(self):
        self.stop()
        self.close()

    @pyqtSlot()
    def on_playButton_clicked(self):
        self.start()

    @pyqtSlot()
    def on_stopButton_clicked(self):
        self.stop()

    @pyqtSlot(bool)
    def on_recButton_toggled(self, checked):
        if checked:
            self.start_recording()
        else:
            self.stop_recording()

    @pyqtSlot()
    def on_settingsButton_clicked(self):
        dialog = SettingsDialog(self.settings, parent=self)
        if dialog.exec():
            self.settings.baud = int(dialog.baudComboBox.currentText())
            port_index = dialog.portComboBox.currentIndex()
            port = self.settings.available_ports[port_index]
            self.settings.port = port.device
            self.settings.save_path = dialog.savePathLabel.text()
            self.settings.width = dialog.widthSpinBox.value()
            self.settings.first_is_x = dialog.firstIsXcheckBox.isChecked()
            self.graphicsView.setBackground(
                self.settings.background_colour)

    def start(self, retry=3):
        """
        Start reading serial data
        """
        # Connect to the microcontroller and wait for data
        self.statusbar.showMessage('Connecting to µC...')
        try:
            self.serial = Serial(port=self.settings.port,
                                 baudrate=self.settings.baud,
                                 timeout=None)
        except SerialException as exc:
            self.stop()
            self.statusbar.showMessage("Serial error")
            QMessageBox.critical(self, "Serial error",
                                           exc.strerror)
            return
        time.sleep(0.1)

        retries = 0
        self.statusbar.showMessage("Waiting for data...")
        self.serial.reset_input_buffer()
        while self.serial.in_waiting < 30:
            if retries == retry:
                msg = "No serial data received."
                self.stop()
                QMessageBox.information(self, "Notice", msg)
                return
            retries += 1
            time.sleep(1)

        # Read the first line of data. When the wrong baud rate is set
        # this line will not have an end-of-line character, and that can
        # be used to alert the user. (I do not know if this will always
        # work out though.)
        #
        # Note that, according to pySerial documentation
        # (https://pythonhosted.org/pyserial/shortintro.html#readline),
        # an exception should be raised if readline() does not find an
        # end-of-line when a timeout is set. However, that does not work
        # for me: even with a timeout, readline() blocks forever when
        # the wrong baud rate is set (and thus no eol is found). These
        # lines seem to do a good job at circumventing that issue.
        self.serial.timeout = 0
        line = self.serial.readline(30)
        if not line.endswith(b'\n'):
            self.stop()
            msg = ("The serial stream is not as expected.\n" +
                   "Perhaps the wrong baud rate was set?")
            QMessageBox.critical(self, "Serial error", msg)
            return

        # If the above was successful, read the next line an use this to
        # find out how many values per line (i.e. signals) there are in
        # the data.
        line = self.serial.readline()
        line = line.decode().split()
        nsignals = len(line)

        # Initialise data containers.
        self.x = np.arange(-self.settings.width, 0)
        if self.settings.first_is_x:
            nsignals -= 1

            # First time value, convert from ms to s.
            x0 = float(line[0])/1000

            # Read another line to estimate sampling frequency
            line = self.serial.readline()
            line = line.decode().split()
            x1 = float(line[0])/1000
            fs = 1/(x1-x0)
            self.x = deque(self.x/fs, maxlen=self.settings.width)
            self._x0 = x1 + (1/fs)

        else:           
            self._x0 = 0
        self.data.clear()
        empty_array = np.empty(self.settings.width) * np.nan
        for _ in range(nsignals):
            self.data.append(deque(empty_array,
                             maxlen=self.settings.width))

        # Set up plots
        self.setup_plot(nsignals)

        # Start the timer
        self.serial.timeout = None
        self.timer.start()

        # Update gui
        self.statusbar.clearMessage()
        self.playButton.setEnabled(False)
        self.stopButton.setEnabled(True)
        self.recButton.setEnabled(True)
        self.settingsButton.setEnabled(False)

    def stop(self):
        """
        Stop reading serial data.
        """
        # Stop data acquisition.
        self.timer.stop()

        # Close serial connection
        if hasattr(self, 'serial'):
            self.serial.close()

        if self.recButton.isChecked():
            self.recButton.toggle()

        # Reset gui
        self.statusbar.clearMessage()
        self.playButton.setEnabled(True)
        self.stopButton.setEnabled(False)
        self.recButton.setEnabled(False)
        self.settingsButton.setEnabled(True)

    def start_recording(self):
        now = datetime.today()
        filename = '{:%Y-%m-%d_%H_%M_%S}.tab'.format(now)
        path = os.path.join(self.settings.save_path, filename)
        self._outfile = open(path, 'w')
        self.statusbar.showMessage(f"Recording to {path}")

    def stop_recording(self):
        self._outfile.close()
        self.statusbar.clearMessage()

    def update_plot(self):
        """
        Update the plots with incoming data

        This function runs repeatedly under a QTimer. It reads the data
        form the serial port and plots it.
        """
        if self.serial.in_waiting:
            try:
                # Read data, reshape, and convert to array
                this_data = self.serial.readlines(
                    self.serial.in_waiting)
                this_data = [
                    line.decode().split() for line in this_data]
                this_data = np.array(this_data, dtype='float')

                # Save data if requested
                if self.recButton.isChecked():
                    np.savetxt(self._outfile, this_data, fmt=SAVE_FMT)

                # Get/calculate x values
                this_data = this_data.T
                if self.settings.first_is_x:
                    x = (this_data[0]/1000) - self._x0
                    self.x.extend(x)
                    this_data = this_data[1:]
                else:
                    n = this_data.shape[1]
                    self.x += n

                # Extend the data containers and plot
                for (index, value) in enumerate(this_data):
                    self.data[index].extend(value)
                    self.curves[index].setData(self.x, self.data[index])
            
            except ValueError as error:
                print(error)
                self.timer.stop()
                # sys.exit()

    def setup_plot(self, nsignals):
        # title_fontsize = 10
        x_tick_fontsize = 10
        y_tick_fontsize = 10
        # marker_fontsize = 8
        y_tick_margin = 60

        xfont = pg.QtGui.QFont()
        yfont = pg.QtGui.QFont()
        yfont.setPointSize(y_tick_fontsize)
        xfont.setPointSize(x_tick_fontsize)

        # Format with bounding box...
        # self.layout = pg.GraphicsLayout(border=(100, 100, 100))
        # ...or no box.
        self.layout = pg.GraphicsLayout()

        self.graphicsView.setCentralItem(self.layout)
        self.plots = []
        self.curves = []

        # Create a plot for each signal and initialise a curve for
        # each plot. These curves are the ones that will be updated
        # with serial data.
        for nrow in range(nsignals):
            plot = self.layout.addPlot(row=nrow, col=0)

            # Format y-axis.
            # Add a fixed margin to the left so that the plots are
            # aligned regardless of the width of the y-ticklabels.
            yaxis = plot.axes['left']['item']
            yaxis.setWidth(y_tick_margin)
            yaxis.setTickFont(yfont)

            # Format x-axis. Do not show x-ticklabels but do retain
            # the x-axis line and the vertical grid lines.
            xaxis = plot.axes['bottom']['item']
            xaxis.setStyle(showValues=False)
            plot.showGrid(x=True, y=True)

            # Create curves.
            pen = pg.mkPen(color=self.settings.curve_colour, width=1.25)
            curve = plot.plot(pen=pen)
            self.plots.append(plot)
            self.curves.append(curve)
            # plot.setXRange(0, self.settings.width)

            # An empty label adds some space between the plots.
            # Otherwise the last plot is shorter than the rest.
            plot.setLabel('bottom', ' ', size=10)

        # Link x-axis from all plots to that of the last one.
        for plot in self.plots[:-1]:
            plot.setXLink(self.plots[-1])

        #  Show the x-axis and the x-label in the last plot.
        last_plot = self.plots[-1]
        xaxis = last_plot.axes['bottom']['item']
        xaxis.setStyle(showValues=True)
        xaxis.setTickFont(yfont)
        if self.settings.first_is_x:
            xlab = 'Time'
            xunits = 's'
        else:
            xlab = 'Samples'
            xunits = 'index'
        last_plot.setLabel('bottom', xlab, units=xunits, size=10,
            color=TEXT_COLOUR)


if __name__ == "__main__":
    app = QApplication([])
    # app.setStyle('Fusion')
    win = MainWindow()
    win.show()
    sys.exit(app.exec())
