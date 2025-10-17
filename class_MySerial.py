#  Copyright (c) 2019.
#  This code has been produced by Dr Zoran D. Grujic and by the knowledge found on The Internet.
#  Please feel free to ask me for permission to use my code in your own projects. It is for your own well fare!

import serial
import sys
import glob
import logging
import time
import traceback


class MySerial:
    # define start values, constants
    baud = 115200 #115200
    time_out = 0.2
    port = ""
    boxNamePrefix = "PIXI click driver"
    name = ""
    boxSettings = False  # have settings from box
    box = None  # serial port object
    status = ""
    connected = False
    last_error = ""
    connect_retries = 3
    read_timeout = 1.0  # seconds

    def __init__(self):
        # Setup logging with a basic configuration if not already configured
        if not logging.getLogger().hasHandlers():
            logging.basicConfig(
                level=logging.INFO,
                format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
            )

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
        Returns: True if device identification was successful, False otherwise
        """
        # Send empty command first to clear any partial commands
        self.sendToBox("")
        time.sleep(0.01)
        
        # Clear any existing data
        self.box.flushInput()
        self.box.flushOutput()
        
        # Send identification query
        self.sendToBox("whois?")
        
        # Try multiple times to get a response
        max_attempts = 10
        for i in range(max_attempts):
            if i % 5 == 0 and i > 0:
                self.sendToBox("whois?")  # Resend query every few attempts
                
            try:
                # Wait for response with timeout
                start_time = time.time()
                while self.box.in_waiting == 0:
                    if time.time() - start_time > self.read_timeout:
                        logging.warning(f"Timeout waiting for response on attempt {i+1}")
                        break
                    time.sleep(0.01)
                
                if self.box.in_waiting > 0:
                    line = self.readLine()
                    logging.info(f'Attempt {i+1}: Response: {line}')
                    
                    # Check if response matches expected device prefix
                    if line and line.startswith(self.boxNamePrefix):
                        self.name = line
                        return True
            except Exception as e:
                logging.error(f"Error during device identification: {str(e)}")
                
        return False

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

    def sendToBox(self, stri):
        """
        Send string to the box with error handling
        Returns: Number of bytes written or 0 on error
        """
        if not self.is_connected():
            self.last_error = "Cannot write: Not connected"
            logging.warning(self.last_error)
            return 0
            
        try:
            stri = (stri+"\n").encode('utf-8')
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
