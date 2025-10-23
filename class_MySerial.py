#  Copyright (c) 2019.
#  This code has been produced by Dr Zoran D. Grujic and by the knowledge found on The Internet.
#  Please feel free to ask me for permission to use my code in your own projects. It is for your own well fare!

import serial
import sys
import glob
import logging
import time
import traceback
import threading


class MySerial:
    # define start values, constants
    baud = 115200 #115200
    time_out = 0.2
    port = ""
    boxNamePrefix = "CTL200-0"  # CTL200-0 model prefix for device identification
    name = ""
    boxSettings = False  # have settings from box
    box = None  # serial port object
    status = ""
    connected = False
    last_error = ""
    connect_retries = 3
    read_timeout = 1.0  # seconds

    # CTL200-0 specific attributes
    firmware_version = ""
    serial_number = ""
    model_number = ""  # e.g., CTL200-0-B-200

    def __init__(self):
        # Setup logging with a basic configuration if not already configured
        if not logging.getLogger().hasHandlers():
            logging.basicConfig(
                level=logging.INFO,
                format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
            )

        # Thread lock to prevent concurrent serial access
        self._serial_lock = threading.Lock()

    def connect(self):
        """
        Connect to the driver with improved error handling
        Returns: True if connection was successful, False otherwise
        """
        logging.info("Attempting to connect...")
        
        if self.connected and self.box and self.box.is_open:
            logging.info("Already connected")
            self.status = "connected"
            return True

        # Disconnect first if there was a previous connection
        self.disconnect()
        
        # If port was specified, try only that port
        if self.port:
            return self._try_connect_to_port(self.port)
        
        # Otherwise try all available ports
        ports = self.serial_ports()
        logging.info(f"Found ports: {ports}")
        
        if not ports:
            self.last_error = "No serial ports available"
            self.status = "No ports found"
            logging.warning(self.last_error)
            return False
            
        for port in ports:
            if self._try_connect_to_port(port):
                return True
                
        self.last_error = "Could not connect to any available port"
        self.status = "Connection failed"
        logging.error(self.last_error)
        return False

    def _try_connect_to_port(self, port):
        """
        Try to connect to a specific port
        Returns: True if connection was successful, False otherwise
        """
        logging.info(f"Trying port {port}")
        self.status = f"Trying {port}..."
        
        for attempt in range(self.connect_retries):
            try:
                # Clear any existing connection
                if self.box and self.box.is_open:
                    self.box.close()
                
                # Connect with specified parameters
                self.box = serial.Serial(
                    port, 
                    self.baud, 
                    timeout=self.time_out, 
                    dsrdtr=False, 
                    rtscts=False,
                    parity=serial.PARITY_NONE, 
                    stopbits=serial.STOPBITS_ONE
                )
                
                # Give the serial connection time to initialize
                time.sleep(0.1)
                
                if not self.box.writable():
                    self.last_error = f"{port} is NOT writable"
                    logging.warning(self.last_error)
                    self.box.close()
                    continue
                    
                # Clear buffers
                self.box.flushInput()
                self.box.flushOutput()
                
                # Send initial query and look for response
                if self._identify_device():
                    self.port = port
                    self.status = "connected"
                    self.connected = True
                    logging.info(f"Successfully connected to {self.name} on {port}")
                    return True
                    
                # If identification failed, close the port and try again
                self.box.close()
                
            except serial.SerialException as e:
                self.last_error = f"Serial error on {port}: {str(e)}"
                logging.error(self.last_error)
                # Try to clean up
                try:
                    if self.box and self.box.is_open:
                        self.box.close()
                except:
                    pass
                
            except Exception as e:
                self.last_error = f"Error connecting to {port}: {str(e)}"
                logging.error(self.last_error)
                logging.debug(traceback.format_exc())
                # Try to clean up
                try:
                    if self.box and self.box.is_open:
                        self.box.close()
                except:
                    pass
                
            logging.info(f"Connection attempt {attempt+1} failed, retrying..." if attempt < self.connect_retries-1 else f"All {self.connect_retries} connection attempts failed")
            time.sleep(0.2)  # Short delay before retry
            
        return False

    def _identify_device(self):
        """
        Send identification request and verify device response
        Uses model command as primary identification (CTL200-0-B-200)
        Returns: True if device identification was successful, False otherwise
        """
        # Clear any existing data
        self.box.flushInput()
        self.box.flushOutput()
        
        # Give device time to settle
        time.sleep(0.1)

        # Send model query - CTL200-0 uses \r (carriage return) not \n
        try:
            self.box.write(b"model\r")
            time.sleep(0.3)  # Give device time to respond
        except Exception as e:
            logging.error(f"Error sending model command: {str(e)}")
            return False

        # Try multiple times to get a response
        max_attempts = 3
        for i in range(max_attempts):
            if i > 0:
                # Resend query on retry
                try:
                    self.box.flushInput()
                    self.box.write(b"model\r")
                    time.sleep(0.3)
                except:
                    pass

            try:
                # Read all available lines (device may echo the command first)
                responses = []
                start_time = time.time()

                while time.time() - start_time < 0.6:
                    if self.box.in_waiting > 0:
                        line = self.box.readline()
                        try:
                            decoded = line.decode("ascii").rstrip("\r\n")
                            if decoded:  # Only add non-empty lines
                                responses.append(decoded)
                                logging.info(f'Attempt {i+1}: Received: {decoded}')
                        except UnicodeDecodeError:
                            logging.warning(f"Failed to decode response: {line}")
                    else:
                        time.sleep(0.01)

                # Look for model string in responses (skip echo and prompt)
                for line in responses:
                    # Skip if it's just the echo of our command or the prompt
                    if line.lower().strip() in ["model", ">>", ">"]:
                        continue

                    # Check if response matches expected model prefix (CTL200-0)
                    if line and line.startswith(self.boxNamePrefix):
                        self.model_number = line
                        self.name = line  # Start with just the model

                        # Try to get firmware version
                        firmware = self._get_firmware_version()
                        if firmware:
                            self.firmware_version = firmware
                            self.name = f"{line} (FW: {firmware})"

                        # Try to get serial number
                        serial_num = self._get_device_serial()
                        if serial_num:
                            self.serial_number = serial_num
                            if firmware:
                                self.name = f"{line} (FW: {firmware}, S/N: {serial_num})"
                            else:
                                self.name = f"{line} (S/N: {serial_num})"

                        # Try to get userdata (may contain device name/info)
                        userdata = self._get_device_userdata()
                        if userdata:
                            if firmware and serial_num:
                                self.name = f"{line} (FW: {firmware}, S/N: {serial_num}, {userdata})"
                            elif serial_num:
                                self.name = f"{line} (S/N: {serial_num}, {userdata})"
                            else:
                                self.name = f"{line} ({userdata})"

                        return True

            except Exception as e:
                logging.error(f"Error during device identification: {str(e)}")

        logging.warning(f"No valid model response found after {max_attempts} attempts")
        return False

    def _get_firmware_version(self):
        """
        Get the firmware version
        Returns: Firmware version string or None if not available
        """
        try:
            self.box.flushInput()
            self.box.write(b"version\r")
            time.sleep(0.3)

            # Read version response (may have echo and prompt)
            responses = []
            start_time = time.time()
            while time.time() - start_time < 0.4:
                if self.box.in_waiting > 0:
                    line = self.box.readline().decode("ascii").rstrip("\r\n")
                    if line:
                        responses.append(line)
                else:
                    time.sleep(0.01)

            # Filter out echo, prompt, and empty lines
            for line in responses:
                if line.lower().strip() not in ["version", ">>", ">", ""] and line.startswith("V"):
                    logging.info(f"Firmware version: {line}")
                    return line

        except Exception as e:
            logging.warning(f"Could not get firmware version: {e}")

        return None

    def _get_device_serial(self):
        """
        Get the device serial number
        Returns: Serial number string or None if not available
        """
        try:
            self.box.flushInput()
            self.box.write(b"serial\r")
            time.sleep(0.3)

            # Read serial response (may have echo and prompt)
            responses = []
            start_time = time.time()
            while time.time() - start_time < 0.4:
                if self.box.in_waiting > 0:
                    line = self.box.readline().decode("ascii").rstrip("\r\n")
                    if line:
                        responses.append(line)
                else:
                    time.sleep(0.01)

            # Filter out echo, prompt, and empty lines
            for line in responses:
                if line.lower().strip() not in ["serial", ">>", ">", ""]:
                    logging.info(f"Device serial number: {line}")
                    return line

        except Exception as e:
            logging.warning(f"Could not get serial number: {e}")

        return None


    def _get_device_userdata(self):
        """
        Get the device userdata (user-defined identification string)
        Returns: Userdata string or None if not available
        """
        try:
            self.box.flushInput()
            self.box.write(b"userdata\r")
            time.sleep(0.3)

            # Read userdata response
            responses = []
            start_time = time.time()
            while time.time() - start_time < 0.4:
                if self.box.in_waiting > 0:
                    line = self.box.readline().decode("ascii").rstrip("\r\n")
                    if line:
                        responses.append(line)
                else:
                    time.sleep(0.01)

            # Filter out echo, prompt, and empty lines
            for line in responses:
                if line.lower().strip() not in ["userdata", ">>", ">", ""]:
                    logging.info(f"Device userdata: {line}")
                    return line

        except Exception as e:
            logging.debug(f"Could not get userdata: {e}")

        return None

    def readLine(self):
        """
        Read a line from the serial port with better error handling
        Returns: The decoded line or empty string on error
        """
        if not self.is_connected():
            self.last_error = "Cannot read: Not connected"
            logging.warning(self.last_error)
            return ""
            
        try:
            line = self.box.readline()
            try:
                return line.decode("ascii").rstrip("\r\n")
            except UnicodeDecodeError:
                self.last_error = "Failed to decode response"
                logging.warning(f"{self.last_error}: {line}")
                return ""
        except serial.SerialException as e:
            self.last_error = f"Serial error during read: {str(e)}"
            logging.error(self.last_error)
            self.connected = False
            self.status = "connection lost"
            return ""
        except Exception as e:
            self.last_error = f"Error reading from serial port: {str(e)}"
            logging.error(self.last_error)
            return ""

    def read_binary_state(self, command, timeout=0.5):
        """
        Read a binary state command (like lason or tecon) that returns only "0" or "1"
        Ignores all responses that are not "0" or "1" (echoes, prompts, errors, etc.)

        Thread-safe with lock to prevent concurrent serial access.

        Handles device behavior:
        - Device echoes the command
        - Response comes on the next line (or sometimes merged)
        - Prompt ">>" may appear before or after

        Args:
            command: The command to send (e.g., "lason" or "tecon")
            timeout: Maximum time to wait for valid response (seconds)

        Returns:
            0 or 1 if valid response found, None if no valid response
        """
        if not self.is_connected():
            self.last_error = "Cannot read: Not connected"
            logging.warning(self.last_error)
            return None

        # Use lock to prevent concurrent serial access
        with self._serial_lock:
            try:
                # Flush input buffer to clear any stale data
                self.box.flushInput()
                time.sleep(0.02)  # Small delay after flush

                # Send the command
                self.sendToBox(command)

                # Collect all response lines within timeout period
                start_time = time.time()
                responses = []

                while time.time() - start_time < timeout:
                    # Wait for data to arrive
                    if self.box.in_waiting > 0:
                        # Read raw bytes with small timeout to get complete line
                        try:
                            line = self.box.readline()
                            if line:
                                decoded = line.decode("ascii", errors='ignore').strip()
                                if decoded:
                                    responses.append(decoded)
                                    logging.debug(f"{command} received line: '{decoded}'")
                        except Exception as e:
                            logging.warning(f"Error reading line: {e}")
                            continue
                    else:
                        # If we have at least one response, wait a bit more for additional data
                        if responses:
                            time.sleep(0.05)
                            # If still no more data, we're done
                            if self.box.in_waiting == 0:
                                break
                        else:
                            time.sleep(0.02)

                # Parse responses to find valid "0" or "1"
                for line in responses:
                    # Clean up the line - remove prompts and whitespace
                    cleaned = line.replace('>>', '').replace('>', '').strip()

                    # Skip empty lines after cleaning
                    if not cleaned:
                        continue

                    # Skip exact command echo
                    if cleaned.lower() == command.lower():
                        continue

                    # IMPORTANT: Reject fragments from status command (contains spaces/multiple values)
                    # Status returns: "lason vlaser itec vtec rtact iphd ain1 ain2"
                    # Fragments look like: "603 1.58494", "9086", "1.59576"
                    if ' ' in cleaned or '.' in cleaned:
                        # If it contains space or decimal point, it's likely a status fragment
                        logging.debug(f"{command} rejecting status fragment: '{cleaned}'")
                        continue

                    # Check if response is exactly "0" or "1"
                    if cleaned == "0":
                        logging.debug(f"{command} -> 0")
                        return 0
                    elif cleaned == "1":
                        logging.debug(f"{command} -> 1")
                        return 1

                    # Log unexpected responses that don't match
                    logging.debug(f"{command} ignoring invalid response: '{cleaned}'")

                # No valid response found
                logging.warning(f"{command}: no valid response after {timeout}s (got {len(responses)} lines)")
                return None

            except Exception as e:
                self.last_error = f"Error reading {command}: {str(e)}"
                logging.error(self.last_error)
                return None

    def sendToBox(self, stri):
        """
        Send string to the box with error handling
        CTL200-0 uses \r (carriage return) as line terminator
        Returns: Number of bytes written or 0 on error
        """
        if not self.is_connected():
            self.last_error = "Cannot write: Not connected"
            logging.warning(self.last_error)
            return 0
            
        try:
            stri = (stri+"\r").encode('utf-8')
            bytes_written = self.box.write(stri)
            return bytes_written
        except serial.SerialException as e:
            self.last_error = f"Serial error during write: {str(e)}"
            logging.error(self.last_error)
            self.connected = False
            self.status = "connection lost"
            return 0
        except Exception as e:
            self.last_error = f"Error writing to serial port: {str(e)}"
            logging.error(self.last_error)
            return 0

    def is_connected(self):
        """
        Check if the connection is still valid
        Returns: True if connected, False otherwise
        """
        if not self.connected or not self.box:
            return False
            
        try:
            return self.box.is_open
        except:
            self.connected = False
            return False

    def disconnect(self):
        """
        Safely disconnect from the serial port
        """
        if self.box:
            try:
                if self.box.is_open:
                    # Flush buffers before closing
                    self.box.flushInput()
                    self.box.flushOutput()
                    self.box.close()
                    logging.info(f"Disconnected from {self.port}")
            except Exception as e:
                logging.error(f"Error during disconnect: {str(e)}")
        
        # Reset connection state
        self.connected = False
        self.status = "disconnected"

    @staticmethod
    def serial_ports():
        """ 
        Lists serial port names with improved error handling
        Returns: A list of the serial ports available on the system
        """
        try:
            if sys.platform.startswith('win'):
                ports = ['COM%s' % (i + 1) for i in range(256)]
            elif sys.platform.startswith('linux') or sys.platform.startswith('cygwin'):
                # this excludes your current terminal "/dev/tty"
                ports = glob.glob('/dev/tty[A-Za-z]*')
            elif sys.platform.startswith('darwin'):
                ports = glob.glob('/dev/tty.*')
            else:
                logging.error('Unsupported platform for serial port detection')
                return []

            result = []
            for port in ports:
                try:
                    s = serial.Serial(port)
                    s.close()
                    result.append(port)
                except (OSError, serial.SerialException):
                    pass
            return result
            
        except Exception as e:
            logging.error(f"Error enumerating serial ports: {str(e)}")
            return []
