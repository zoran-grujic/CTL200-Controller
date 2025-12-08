# CTL200-0 Laser Controller

Advanced GUI application for controlling the **CTL200-0-B-200 Laser Diode Controller** with integrated **ESP32 Laser Lock** support.

## Features

### CTL200-0 Control
- **Laser Diode Control**: Current setting, safety limits, modulation
- **Temperature Control (TEC)**: PID-based thermoelectric cooling with RT sensor monitoring
- **Real-time Monitoring**: Temperature plot with actual and setpoint values
- **Configuration Management**: Auto-save/restore device settings via TOML config
- **Safety Features**: Laser must be manually enabled, automatic limits enforcement

### ESP32 Laser Lock Integration
- **Sweep Measurements**: DAC vs ADC sweep with configurable range (0-65535)
- **Interactive Plotting**: Zoom/pan plot to set sweep range automatically
- **Dual Plot View**: Resizable splitter showing both temperature and sweep data
- **Auto-reconnect**: Remembers last COM port for both devices

### User Interface
- **Custom Toggle Switches**: Visual laser and TEC enable/disable controls
- **Tabbed Interface**: Organized controls for Laser, Temperature, Settings
- **Debounced Inputs**: Prevents command flooding during rapid parameter changes
- **Communication Log**: Optional serial traffic monitoring for debugging

## Requirements

### Hardware
- CTL200-0-B-200 Laser Diode Controller (RS-232 serial)
- ESP32 Laser Lock device (optional, USB serial)
- Windows PC with available COM ports

### Software
- Python 3.12+
- PyQt6
- pyqtgraph
- pyserial
- toml

## Installation

```bash
# Clone repository
git clone https://github.com/yourusername/CTL200-Controller.git
cd CTL200-Controller

# Install dependencies
pip install PyQt6 pyqtgraph pyserial toml

# Run application
python app.pyw
```

## Configuration

Settings are automatically saved to `ctl200_config.toml`:

```toml
[connection]
last_port = "COM4"              # CTL200 last connected port
auto_connect = true

[esp32_laser_lock]
last_port = "COM7"              # ESP32 last connected port
last_connected = "2025-12-08T..."

[laser]
ilaser = 94.0                   # Laser current (mA)
ilmax = 100.0                   # Maximum current limit
ldelay = 1000.0                 # Laser delay (ms)
# ... other laser parameters

[tec]
rtset = 14733.0                 # Temperature setpoint (Ohms)
pgain = 0.0003                  # PID proportional gain
igain = 3e-5                    # PID integral gain
dgain = 0.0                     # PID derivative gain
# ... other TEC parameters
```

## Usage

### First Connection
1. Connect CTL200-0 via RS-232 (typically COM4)
2. Connect ESP32 Laser Lock via USB (typically COM7)
3. Launch application - both devices auto-detected
4. Ports saved for future sessions

### Laser Control
1. Navigate to **Laser Control** tab
2. Set current with `ILaser` spinbox (applies after 100ms debounce)
3. Toggle laser ON/OFF with switch (safety: always starts OFF)
4. Monitor temperature in real-time plot

### Sweep Measurements
1. Ensure ESP32 connected (status shown in console)
2. **Zoom** or **pan** the sweep plot x-axis to desired DAC range
3. Wait 500ms - sweep command automatically sent
4. New data replaces old plot (first point skipped for accuracy)
5. Adjust view and repeat as needed

### Plot Controls
- **Drag splitter** between plots to resize
- **Right-click** plots for PyQtGraph context menu (export, etc.)
- **Temperature legend** shows RT actual (blue) and RT set (red dashed)

## Architecture

### Key Components

**app.pyw**: Main application, PyQt6 GUI, device communication
- `MyUi`: Main window class, handles all UI interactions
- Serial communication via worker thread (CTL200) and timer (ESP32)
- Debounced parameter updates (100ms laser current, 500ms sweep range)

**config_manager.py**: TOML-based configuration persistence

**class_MySerial.py**: Serial port detection and management

**QtWorker.py / QtWorkerSignals.py**: Background thread for CTL200 status polling

**toggle_switch.py**: Custom PyQt6 toggle switch widget

### Communication Protocol

**CTL200-0**: ASCII commands, polled status (50ms interval)
```
> ilaser 94.0      # Set laser current
< ilaser 94.0      # Echo response
> status 20        # Poll all parameters
< lason,1,ilaser,94.0,...  # CSV response
```

**ESP32 Laser Lock**: ASCII commands, streaming sweep data
```
> whois?           # Device identification
< Laser lock by BGMAGLAB
> sweep 10000 20000  # Start sweep
< # Starting sweep
< Point,DAC_Raw,ADC_Raw
< 0,10000,1234
< 1,10050,1256
...
```

## Development

### GUI Editing
```bash
# Edit GUI with Qt Designer
python gui-edit.py

# Compile UI to Python (automatic on app start)
python gui-compile.py
```

### Adding Commands
1. Add command definition to `CTL200-0_serial_commands.txt`
2. Implement handler in `MyUi` class
3. Update config manager if parameter needs persistence

## Troubleshooting

**Device not found**:
- Check COM port availability in Device Manager
- Ensure correct baud rate (115200 for both devices)
- Try different USB ports

**Laser won't enable**:
- Safety feature: check TEC is enabled first
- Verify current limits (`ilmax`, `ldelay`)

**Temperature plot not updating**:
- Check status polling is active (should auto-start)
- Verify TEC is enabled

**Sweep plot shows ghost lines**:
- Fixed via buffer clearing - ensure latest code
- Old data cleared when range changes

**Plot won't zoom**:
- Sweep plot x-axis limited to 0-65535 (DAC range)
- Use mouse wheel or drag to zoom y-axis freely

## Safety Notes

⚠️ **Laser Safety**:
- Laser defaults to OFF on startup
- Set appropriate `ilmax` before enabling
- Always wear safety glasses when laser is ON

⚠️ **Temperature Control**:
- Set `rtmin`/`rtmax` limits before enabling TEC
- Monitor RT sensor for proper operation
- TEC thermal protection enabled by default (`tprot = 1`)

## License

MIT License - See LICENSE file

## Credits

Developed for scientific instrumentation control
- CTL200-0 Controller by [Manufacturer]
- ESP32 Laser Lock by BGMAGLAB

## Version History

**v1.0** (2025-12-08)
- Initial release
- CTL200-0 full control
- ESP32 laser lock integration
- Dual plot view with splitter
- Configuration management
- Auto-reconnect features

