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
    raw_data_received = pyqtSignal(str)  # Emits raw received data without formatting

    def __init__(self, serial_device):
        super().__init__()
        self.serial_device = serial_device
        self.running = False
        self.poll_interval = 0.05  # 50ms for faster updates

        # Interval for reading ilaser separately (seconds). Reading ilaser every status poll is expensive,
        # so read it less often (e.g., 5 times per second) to keep status polls fast.
        self.ilaser_interval = 0.2  # 200ms = 5 updates per second
        self._last_ilaser_read = 0.0

        # Interval for reading TEC enable state separately (seconds)
        self.tecon_interval = 1.0
        self._last_tecon_read = 0.0

        # Command queue for user commands
        self.command_queue = Queue()

        # Synchronization primitives
        self.mutex = QMutex()
        self.paused = False
        self.executing_command = False

        # Disable detailed logging by default (it severely impacts polling rate)
        # Can be enabled via UI toggle for debugging
        self.detailed_logging = False

        # Connection loss detection
        self.no_response_count = 0
        self.no_response_threshold = 10  # Trigger connection lost after 10 consecutive no responses

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
            cycle_start = time.time()
            try:
                # Check if paused (e.g., during tab change reads)
                if self.paused:
                    time.sleep(max(0.005, min(0.05, self.poll_interval)))  # Short sleep while paused
                    continue

                # Check if there are pending user commands
                if not self.command_queue.empty():
                    self._execute_queued_commands()
                    continue

                if not self.serial_device.is_connected():
                    self.error_occurred.emit("Device not connected")
                    # wait the poll interval before retrying
                    remaining = self.poll_interval - (time.time() - cycle_start)
                    if remaining > 0:
                        time.sleep(remaining)
                    continue

                # Wait for any pending response from previous status request
                self._wait_for_pending_response()

                # Flush input buffer to clear any stale data
                if hasattr(self.serial_device.box, 'flushInput'):
                    self.serial_device.box.flushInput()

                # Send status command to get all values at once
                self._log_communication('TX', 'status')
                self.serial_device.sendToBox("status")
                # very small settle delay to allow device to start responding
                time.sleep(max(0.002, min(0.01, self.poll_interval/10)))

                # Read response, filtering out echo and prompt
                # choose a read timeout based on poll_interval (with sensible bounds)
                response = self._read_response("status", timeout=None)

                if response:
                    self._log_communication('RX', response)

                    # Reset no response counter on successful response
                    self.no_response_count = 0

                    # Parse the status response
                    status_data = self._parse_status_response(response)

                    if status_data:
                         # Emit parsed status immediately (fast path)
                        self.status_updated.emit(status_data)

                        # Now optionally read ilaser/tecon periodically without delaying the status emission
                        now = time.time()

                        # Read laser current separately, but only every ilaser_interval seconds
                        if now - self._last_ilaser_read >= self.ilaser_interval:
                            try:
                                ilaser = self._read_laser_current()
                                if ilaser is not None:
                                    # Emit incremental update containing only the ilaser field
                                    self.status_updated.emit({'ilaser': ilaser})
                                self._last_ilaser_read = now
                            except Exception as e:
                                logging.warning(f"Error reading ilaser: {e}")

                        # Read tecon (TEC enable) only periodically to avoid blocking every poll
                        if now - self._last_tecon_read >= self.tecon_interval:
                            try:
                                tecon = self._read_tec_state()
                                if tecon is not None:
                                    self.status_updated.emit({'tecon': tecon})
                                self._last_tecon_read = now
                            except Exception as e:
                                logging.warning(f"Error reading tecon: {e}")
                else:
                    logging.warning("No response from status command")
                    self.no_response_count += 1

                    # Check if we've exceeded the threshold for connection loss
                    if self.no_response_count >= self.no_response_threshold:
                        logging.error(f"Connection lost: {self.no_response_count} consecutive failed status commands")
                        # Close the serial port
                        try:
                            if hasattr(self.serial_device, 'disconnect'):
                                self.serial_device.disconnect()
                            elif hasattr(self.serial_device, 'box') and self.serial_device.box:
                                self.serial_device.box.close()
                        except Exception as e:
                            logging.error(f"Error closing serial port: {e}")

                        # Emit error signal to trigger reconnection prompt
                        self.error_occurred.emit("Device not connected")
                        # Reset counter to prevent repeated errors
                        self.no_response_count = 0
                        # Stop polling
                        self.running = False

            except Exception as e:
                error_msg = f"Error reading device status: {str(e)}"
                logging.error(error_msg)
                self.error_occurred.emit(error_msg)

            # Sleep only the remaining time to match poll_interval (break into small increments)
            try:
                elapsed = time.time() - cycle_start
                remaining = self.poll_interval - elapsed
                while self.running and remaining > 0:
                    step = min(0.005, remaining)
                    time.sleep(step)
                    remaining -= step
            except Exception:
                pass

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

        # Conservative short wait: keep this small so polling isn't stalled by lingering data
        max_wait = max(0.005, min(0.02, self.poll_interval))
        start_time = time.time()

        while time.time() - start_time < max_wait:
            try:
                if self.serial_device.box.in_waiting == 0:
                    return
            except Exception:
                return
            time.sleep(0.002)

    def _is_numeric(self, s):
        """
        Check if a string represents a valid number (int or float)
        Returns: True if numeric, False otherwise
        """
        try:
            float(s)
            return True
        except (ValueError, TypeError):
            return False

    def _read_response(self, command, timeout=None):
        """
        Read response from device, filtering out command echo and prompt
        Returns: The actual response data or None
        """
        try:
            # Determine effective timeout: if timeout provided use it, otherwise derive from poll_interval
            if timeout is None:
                # Conservative, tight timeout: scale with poll_interval but keep small caps to avoid long stalls
                timeout = min(0.12, max(0.01, self.poll_interval * 1.2))

            start_time = time.time()
            buffer = bytearray()

            # If underlying pyserial box is available, prefer non-blocking reads from it
            box = getattr(self.serial_device, 'box', None)

            # Track when we last received data to implement idle detection
            last_data_time = start_time
            line_terminator_found = False

            # For status command, we expect 8 space-separated values
            expected_status_fields = 8 if command == "status" else 0

            while time.time() - start_time < timeout:
                if box is not None:
                    try:
                        in_wait = box.in_waiting
                    except Exception:
                        in_wait = 0

                    if in_wait > 0:
                        # read all available bytes at once
                        chunk = box.read(in_wait)
                        if chunk:
                            buffer.extend(chunk)
                            last_data_time = time.time()
                            # Check if we have a line terminator
                            if b"\n" in buffer or b"\r" in buffer:
                                line_terminator_found = True
                            # Small delay to allow more data to accumulate in buffer
                            time.sleep(0.003)
                        else:
                            time.sleep(0.002)
                    else:
                        # Check if response is complete
                        if line_terminator_found:
                            # For status command, verify we have enough fields
                            if expected_status_fields > 0:
                                try:
                                    text = buffer.decode('ascii', errors='ignore')
                                    # Count space-separated fields in the buffer
                                    fields = text.strip().split()
                                    if len(fields) >= expected_status_fields:
                                        # We have complete data
                                        break
                                except:
                                    pass

                            # For other commands or if we've waited long enough, break
                            if (time.time() - last_data_time) > 0.025:
                                break
                        # nothing available yet
                        time.sleep(0.002)
                else:
                    # Fallback to serial_device.readLine (may block internally)
                    line = self.serial_device.readLine()
                    if line:
                        # append the returned text (with a newline) so parsing is unified
                        try:
                            buffer.extend((line + "\n").encode('ascii'))
                        except Exception:
                            buffer.extend((line + "\n").encode('utf-8', errors='ignore'))
                    else:
                        time.sleep(0.002)

            if not buffer:
                return None

            # Decode buffer
            try:
                text = buffer.decode('ascii', errors='ignore')
            except Exception:
                text = buffer.decode('utf-8', errors='ignore')

            # Emit raw data (unformatted, just with newlines preserved)
            if text and self.detailed_logging:
                self.raw_data_received.emit(text)

            # Normalize and split into lines; preserve order
            lines = [ln.strip() for ln in text.replace('\r', '\n').split('\n') if ln.strip()]

            # Return first non-echo, non-prompt line
            cmd_lower = (command or "").strip().lower()
            for ln in lines:
                if not ln:
                    continue
                ln_stripped = ln.strip()
                ln_lower = ln_stripped.lower()

                # Skip prompt lines
                if ln_stripped in ['>>', '>']:
                    continue

                # Skip exact echo or simple variations (case-insensitive)
                if ln_lower == cmd_lower:
                    continue

                # Skip if the line starts with the command followed by space (e.g. "status ok" echo)
                if cmd_lower and ln_lower.startswith(cmd_lower + ' '):
                    # if there's more after the command, that may be real data; only skip if it's pure echo
                    # e.g., "status" (echo) vs "status 1 2 3" (unlikely). We'll treat a line that is exactly
                    # the command or starts with command followed by nothing but whitespace as echo; otherwise accept.
                    suffix = ln_stripped[len(command):].strip()
                    if not suffix:
                        continue

                # For commands expecting single values (ilaser, vlaser, etc.), reject multi-value responses
                # This prevents status command fragments from polluting the response
                if command in ['ilaser', 'vlaser', 'itec', 'vtec', 'rtact', 'iphd', 'ain1', 'ain2']:
                    # These commands should return a single numeric value
                    # If we see multiple space-separated values, it's likely a status fragment
                    parts = ln_stripped.split()
                    if len(parts) > 1:
                        logging.debug(f"Rejecting multi-value response for {command}: '{ln_stripped}'")
                        continue

                    # Also validate it looks like a number (for numeric commands)
                    if parts and not self._is_numeric(parts[0]):
                        logging.debug(f"Rejecting non-numeric response for {command}: '{ln_stripped}'")
                        continue

                # If we get here, treat ln as the real response
                return ln_stripped

            return None

        except Exception as e:
            logging.error(f"Error reading response: {e}")
            return None

    def _parse_status_response(self, response):
        """
        Parse the status command response
        Format: lason vlaser itec vtec rtact iphd ain1 ain2
        Accepts incomplete packets with at least 6 values (ain1/ain2 optional)
        Validates ain1/ain2 have full precision (at least 5 decimal places)
        Returns: dict with parsed values or None if parsing failed
        """
        try:
            # Split the response into individual values
            values = response.strip().split()

            # Accept responses with at least 6 values (core data)
            # ain1 and ain2 are optional and will be set to None if missing or incomplete
            if len(values) >= 6:
                # Helper function to validate analog input precision
                def parse_analog_input(value_str):
                    """Parse analog input only if it has full precision (5+ decimal places)"""
                    try:
                        # Check if the value has a decimal point and enough digits after it
                        if '.' in value_str:
                            decimal_part = value_str.split('.')[1]
                            # Only accept if we have at least 5 decimal places (full precision)
                            if len(decimal_part) >= 5:
                                return float(value_str)
                        # If no decimal point or fewer than 5 decimal places, reject
                        return None
                    except:
                        return None

                status_dict = {
                    'lason': int(values[0]),           # Laser enable (0 or 1)
                    'vlaser': float(values[1]),        # Laser voltage (V)
                    # ilaser is NOT in status command - read separately, don't include placeholder
                    'itec': float(values[2]) * 1000,   # TEC current (mA)
                    'vtec': float(values[3]),          # TEC voltage (V)
                    'rtact': float(values[4]),         # Thermistor resistance (Ω)
                    'iphd': float(values[5]),          # Photodiode current (mA)
                    'ain1': parse_analog_input(values[6]) if len(values) > 6 else None,  # Analog input 1 (V) - requires full precision
                    'ain2': parse_analog_input(values[7]) if len(values) > 7 else None,  # Analog input 2 (V) - requires full precision
                }

                # Log if we got an incomplete packet or truncated analog inputs (for debugging)
                if len(values) < 8:
                    logging.debug(f"Incomplete status packet ({len(values)}/8 values)")
                elif status_dict['ain1'] is None and len(values) > 6:
                    logging.debug(f"ain1 rejected (insufficient precision): {values[6]}")
                elif status_dict['ain2'] is None and len(values) > 7:
                    logging.debug(f"ain2 rejected (insufficient precision): {values[7]}")

                return status_dict
            else:
                logging.warning(f"Unexpected status response format ({len(values)} values, need at least 6): {response}")
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
            self._log_communication('TX', 'tecon')

            # Use the new read_binary_state method which properly filters responses
            tec_state = self.serial_device.read_binary_state("tecon")

            if tec_state is not None:
                self._log_communication('RX', str(tec_state))
                return tec_state
            else:
                logging.debug("No valid response from tecon command")
                return None

        except Exception as e:
            logging.warning(f"Error reading TEC state: {e}")
            return None
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
