from PyQt5.QtCore import QRunnable
from PyQt5.QtCore import pyqtSlot
import traceback
import sys
from QtWorkerSignals import WorkerSignals
from PyQt6.QtCore import QThread, pyqtSignal, QObject, QMutex, QWaitCondition
import time
import logging
from queue import Queue, Empty
from datetime import datetime


class Worker(QRunnable):
    '''
    Worker thread

    Inherits from QRunnable to handler theWorker thread setup, signals and wrap-up.

    :param callback: The function callback to run on this theWorker thread. Supplied args and
                     kwargs will be passed through to the runner.
    :type callback: function
    :param args: Arguments to pass to the callback function
    :param kwargs: Keywords to pass to the callback function

    '''

    def __init__(self, fn, *args, **kwargs):
        super(Worker, self).__init__()

        # Store constructor arguments (re-used for processing)
        self.fn = fn
        self.args = args
        self.kwargs = kwargs
        self.signals = WorkerSignals()

        # Add the callback to our kwargs
        self.kwargs['progress_callback'] = self.signals.progress

    @pyqtSlot()
    def run(self):
        '''
        Initialise the runner function with passed args, kwargs.
        '''

        # Retrieve args/kwargs here; and fire processing using them
        try:
            result = self.fn(*self.args, **self.kwargs)
        except Exception as e:
            traceback.print_exc()
            exctype, value = sys.exc_info()[:2]
            self.signals.error.emit((exctype, value, traceback.format_exc()))
        else:
            self.signals.result.emit(result)  # Return the result of the processing
        finally:
            self.signals.finished.emit()  # Done


class SerialWorker(QObject):
    """
    Worker class for handling serial communication in a separate thread.
    Reads device status every 200ms and emits signals with the data.
    Supports pausing for user commands with proper synchronization.
    """

    # Signals to emit data to the main GUI thread
    status_updated = pyqtSignal(dict)  # Emits dict with all status values
    error_occurred = pyqtSignal(str)   # Emits error messages
    command_completed = pyqtSignal(str, bool, str)  # command, success, response
    communication_log = pyqtSignal(str, str, str)  # timestamp, direction, message

    def __init__(self, serial_device):
        super().__init__()
        self.serial_device = serial_device
        self.running = False
        self.poll_interval = 0.05  # 50ms for faster updates

        # Command queue for user commands
        self.command_queue = Queue()

        # Synchronization primitives
        self.mutex = QMutex()
        self.paused = False
        self.executing_command = False

        # Enable detailed logging
        self.detailed_logging = True

    def _log_communication(self, direction, message):
        """
        Log communication with timestamp
        direction: 'TX' for transmitted, 'RX' for received
        """
        if self.detailed_logging:
            timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
            self.communication_log.emit(timestamp, direction, message)

    def start_polling(self):
        """Start the polling loop"""
        self.running = True
        self.poll_device()

    def stop_polling(self):
        """Stop the polling loop"""
        self.running = False

    def execute_command(self, command, verify_command=None, expected_response=None):
        """
        Queue a user command to be executed.
        The polling loop will pause, execute the command, and resume.

        Args:
            command: Command string to send (e.g., "lason 1")
            verify_command: Optional command to verify result (e.g., "lason")
            expected_response: Expected response for verification (e.g., "1")
        """
        self.command_queue.put({
            'command': command,
            'verify_command': verify_command,
            'expected_response': expected_response
        })

    def poll_device(self):
        """
        Main polling loop - reads device status every 200ms
        Pauses when user commands are queued
        """
        while self.running:
            try:
                # Check if paused (e.g., during tab change reads)
                if self.paused:
                    time.sleep(0.05)  # Short sleep while paused
                    continue

                # Check if there are pending user commands
                if not self.command_queue.empty():
                    self._execute_queued_commands()
                    continue

                if not self.serial_device.is_connected():
                    self.error_occurred.emit("Device not connected")
                    time.sleep(self.poll_interval)
                    continue

                # Wait for any pending response from previous status request
                self._wait_for_pending_response()

                # Flush input buffer to clear any stale data
                if hasattr(self.serial_device.box, 'flushInput'):
                    self.serial_device.box.flushInput()

                # Send status command to get all values at once
                self._log_communication('TX', 'status')
                self.serial_device.sendToBox("status")
                time.sleep(0.1)

                # Read response, filtering out echo and prompt
                response = self._read_response("status")

                if response:
                    self._log_communication('RX', response)

                    # Parse the status response
                    status_data = self._parse_status_response(response)

                    if status_data:
                        # Read laser current separately
                        ilaser = self._read_laser_current()
                        if ilaser is not None:
                            status_data['ilaser'] = ilaser

                        # Emit the parsed data
                        self.status_updated.emit(status_data)
                    else:
                        logging.warning(f"Failed to parse status: {response}")
                else:
                    logging.warning("No response from status command")

            except Exception as e:
                error_msg = f"Error reading device status: {str(e)}"
                logging.error(error_msg)
                self.error_occurred.emit(error_msg)

            # Wait for next poll interval
            time.sleep(self.poll_interval)

    def _execute_queued_commands(self):
        """
        Execute all queued user commands before resuming status polling
        """
        while not self.command_queue.empty():
            try:
                cmd_info = self.command_queue.get_nowait()
            except Empty:
                break

            self.mutex.lock()
            self.executing_command = True
            self.mutex.unlock()

            try:
                # Wait for any pending status response to complete
                self._wait_for_pending_response()

                # Clear buffers
                if hasattr(self.serial_device.box, 'flushInput'):
                    self.serial_device.box.flushInput()
                if hasattr(self.serial_device.box, 'flushOutput'):
                    self.serial_device.box.flushOutput()

                time.sleep(0.05)

                # Execute the command
                logging.info(f"Executing user command: {cmd_info['command']}")
                self._log_communication('TX', f"[USER] {cmd_info['command']}")
                self.serial_device.sendToBox(cmd_info['command'])
                time.sleep(0.1)

                # Read the response (if any)
                response = self._read_response(cmd_info['command'])
                if response:
                    self._log_communication('RX', response)
                logging.info(f"Command response: {response}")

                # Verify if requested
                success = True
                if cmd_info.get('verify_command'):
                    time.sleep(0.05)

                    # Clear buffer before verification
                    if hasattr(self.serial_device.box, 'flushInput'):
                        self.serial_device.box.flushInput()

                    self._log_communication('TX', f"[VERIFY] {cmd_info['verify_command']}")
                    self.serial_device.sendToBox(cmd_info['verify_command'])
                    time.sleep(0.1)

                    verify_response = self._read_response(cmd_info['verify_command'])
                    if verify_response:
                        self._log_communication('RX', verify_response)
                    logging.info(f"Verification response: {verify_response}")

                    if cmd_info.get('expected_response'):
                        success = (verify_response == cmd_info['expected_response'])
                        if not success:
                            logging.warning(
                                f"Verification failed: expected '{cmd_info['expected_response']}', "
                                f"got '{verify_response}'"
                            )

                # Emit completion signal
                self.command_completed.emit(
                    cmd_info['command'],
                    success,
                    response if response else ""
                )

            except Exception as e:
                error_msg = f"Error executing command '{cmd_info['command']}': {str(e)}"
                logging.error(error_msg)
                self.error_occurred.emit(error_msg)
                self.command_completed.emit(cmd_info['command'], False, str(e))

            finally:
                self.mutex.lock()
                self.executing_command = False
                self.mutex.unlock()

            # Small delay between commands
            time.sleep(0.05)

    def _wait_for_pending_response(self):
        """
        Wait for any pending serial data to be received
        This ensures we don't have overlapping requests
        """
        if not hasattr(self.serial_device.box, 'in_waiting'):
            return

        max_wait = 0.3  # Maximum wait time in seconds
        start_time = time.time()

        while time.time() - start_time < max_wait:
            if self.serial_device.box.in_waiting == 0:
                return
            time.sleep(0.01)

    def _read_response(self, command):
        """
        Read response from device, filtering out command echo and prompt
        Returns: The actual response data or None
        """
        try:
            start_time = time.time()
            responses = []

            # Read all available lines within timeout
            while time.time() - start_time < 0.3:
                if self.serial_device.box.in_waiting > 0:
                    line = self.serial_device.readLine()
                    if line:
                        responses.append(line)
                else:
                    time.sleep(0.01)

            # Filter out command echo and prompt
            for line in responses:
                cleaned = line.strip()
                # Skip empty lines, command echoes, and prompts
                if cleaned and cleaned not in [command, ">>", ">"]:
                    return cleaned

            return None

        except Exception as e:
            logging.error(f"Error reading response: {e}")
            return None

    def _parse_status_response(self, response):
        """
        Parse the status command response
        Format: lason vlaser itec vtec rtact iphd ain1 ain2
        Returns: dict with parsed values or None if parsing failed
        """
        try:
            # Split the response into individual values
            values = response.strip().split()

            if len(values) >= 8:
                status_dict = {
                    'lason': int(values[0]),           # Laser enable (0 or 1)
                    'vlaser': float(values[1]),        # Laser voltage (V)
                    'ilaser': 0.0,                     # Placeholder, will be read separately
                    'itec': float(values[2]) * 1000,   # TEC current (mA)
                    'vtec': float(values[3]),          # TEC voltage (V)
                    'rtact': float(values[4]),         # Thermistor resistance (Ω)
                    'iphd': float(values[5]),          # Photodiode current (mA)
                    'ain1': float(values[6]),          # Analog input 1 (V)
                    'ain2': float(values[7]),          # Analog input 2 (V)
                }

                # Read TEC state separately (not included in status command)
                tecon = self._read_tec_state()
                if tecon is not None:
                    status_dict['tecon'] = tecon

                return status_dict
            else:
                logging.warning(f"Unexpected status response format: {response}")
                return None

        except (ValueError, IndexError) as e:
            logging.error(f"Error parsing status response '{response}': {e}")
            return None

    def _read_tec_state(self):
        """
        Read TEC enable state separately
        Returns: TEC state (0 or 1) or None on error
        """
        try:
            # Flush input buffer
            if hasattr(self.serial_device.box, 'flushInput'):
                self.serial_device.box.flushInput()

            self._log_communication('TX', 'tecon')
            self.serial_device.sendToBox("tecon")
            time.sleep(0.1)

            # Read response with filtering
            response = self._read_response("tecon")

            if response:
                self._log_communication('RX', response)
                # Try to convert to int
                return int(response)
            else:
                logging.debug("No response from tecon command")
                return 0

        except ValueError as e:
            logging.warning(f"Error parsing TEC state: {e}")
            return 0
        except Exception as e:
            logging.warning(f"Error reading TEC state: {e}")
            return 0

    def _read_laser_current(self):
        """
        Read laser current separately (not included in status command)
        Returns: Laser current in mA or None on error
        """
        try:
            # Flush input buffer
            if hasattr(self.serial_device.box, 'flushInput'):
                self.serial_device.box.flushInput()

            self._log_communication('TX', 'ilaser')
            self.serial_device.sendToBox("ilaser")
            time.sleep(0.1)

            # Read response with filtering
            response = self._read_response("ilaser")

            if response:
                self._log_communication('RX', response)
                # Try to convert to float
                return float(response)
            else:
                logging.debug("No response from ilaser command")
                return 0.0

        except ValueError as e:
            logging.warning(f"Error parsing laser current: {e}")
            return 0.0
        except Exception as e:
            logging.warning(f"Error reading laser current: {e}")
            return 0.0
