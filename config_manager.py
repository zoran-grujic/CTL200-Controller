"""
Configuration Manager for CTL200-0 Laser Controller
Handles loading and saving of device settings and connection information
Uses TOML format for human-readable configuration files
"""

import toml
import os
from pathlib import Path


class ConfigManager:
    """Manages configuration file for CTL200-0 controller"""

    def __init__(self, config_file="ctl200_config.toml"):
        """
        Initialize configuration manager

        Args:
            config_file: Name of the config file (stored in app directory)
        """
        # Get the directory where the script is located
        self.app_dir = Path(__file__).parent
        self.config_file = self.app_dir / config_file

        # Default configuration
        self.default_config = {
            "connection": {
                "last_port": "",
                "baud_rate": 115200,
                "auto_connect": True
            },
            "laser": {
                # lason is intentionally excluded - laser must always start OFF for safety
                "ilaser": 0.0,
                "ilmax": 100.0,
                "ldelay": 1000.0,
                "lckon": 0,
                "lmodgain": 0.0,
                "ain1_enable": 0,
                "ain1_curr_gain": 0.0
            },
            "tec": {
                "tecon": 0,
                "tprot": 1,
                "rtset": 10000.0,
                "pgain": 0.001,
                "igain": 0.0001,
                "dgain": 0.001,
                "rtmin": 5000.0,
                "rtmax": 15000.0,
                "vtmin": -2.0,
                "vtmax": 2.0,
                "tmodgain": 0.0,
                "ain2_enable": 0,
                "ain2_temp_gain": 0.0
            },
            "device_info": {
                "model": "",
                "firmware": "",
                "serial_number": "",
                "last_connected": ""
            },
            "esp32_laser_lock": {
                "last_port": "",
                "last_connected": ""
            },
            "laser_lock_pid": {
                "p": 0.0,
                "i": 0.0,
                "d": 0.0,
                "invert_pid": False
            },
            "ui": {
                "window_maximized": True,
                "last_tab": 0
            }
        }

        self.config = self.default_config.copy()
        self.load()

    def load(self):
        """Load configuration from file"""
        try:
            if self.config_file.exists():
                with open(self.config_file, 'r') as f:
                    loaded_config = toml.load(f)
                    # Merge with defaults to ensure all keys exist
                    self._merge_config(self.config, loaded_config)
                print(f"✓ Configuration loaded from {self.config_file}")
                return True
            else:
                print(f"ℹ No config file found, using defaults")
                return False
        except Exception as e:
            print(f"✗ Error loading config: {e}")
            self.config = self.default_config.copy()
            return False

    def save(self):
        """Save configuration to file"""
        try:
            with open(self.config_file, 'w') as f:
                toml.dump(self.config, f)
            print(f"✓ Configuration saved to {self.config_file}")
            return True
        except Exception as e:
            print(f"✗ Error saving config: {e}")
            return False

    def _merge_config(self, base, updates):
        """Recursively merge updates into base config"""
        for key, value in updates.items():
            if key in base:
                if isinstance(value, dict) and isinstance(base[key], dict):
                    self._merge_config(base[key], value)
                else:
                    base[key] = value

    def get(self, section, key, default=None):
        """
        Get a configuration value

        Args:
            section: Configuration section (e.g., 'laser', 'tec', 'connection')
            key: Key within the section
            default: Default value if key not found

        Returns:
            Configuration value or default
        """
        try:
            return self.config.get(section, {}).get(key, default)
        except:
            return default

    def set(self, section, key, value):
        """
        Set a configuration value

        Args:
            section: Configuration section
            key: Key within the section
            value: Value to set
        """
        if section not in self.config:
            self.config[section] = {}
        self.config[section][key] = value

    def get_section(self, section):
        """Get an entire configuration section"""
        return self.config.get(section, {})

    def set_section(self, section, values):
        """Set an entire configuration section"""
        if isinstance(values, dict):
            self.config[section] = values

    def get_last_port(self):
        """Get the last connected port"""
        return self.config["connection"]["last_port"]

    def set_last_port(self, port):
        """Save the last connected port"""
        self.config["connection"]["last_port"] = port
        self.save()

    def get_device_info(self):
        """Get saved device information"""
        return self.config["device_info"]

    def set_device_info(self, model="", firmware="", serial_number=""):
        """Save device information"""
        import datetime
        self.config["device_info"]["model"] = model
        self.config["device_info"]["firmware"] = firmware
        self.config["device_info"]["serial_number"] = serial_number
        self.config["device_info"]["last_connected"] = datetime.datetime.now().isoformat()
        self.save()

    def get_laser_config(self):
        """Get laser configuration"""
        return self.config["laser"]

    def set_laser_config(self, **kwargs):
        """
        Set laser configuration

        Args:
            **kwargs: Laser parameters (lason, ilaser, ilmax, etc.)
        """
        for key, value in kwargs.items():
            if key in self.config["laser"]:
                self.config["laser"][key] = value
        self.save()

    def get_tec_config(self):
        """Get TEC configuration"""
        return self.config["tec"]

    def set_tec_config(self, **kwargs):
        """
        Set TEC configuration

        Args:
            **kwargs: TEC parameters (tecon, rtset, pgain, etc.)
        """
        for key, value in kwargs.items():
            if key in self.config["tec"]:
                self.config["tec"][key] = value
        self.save()

    def get_laser_lock_pid_config(self):
        """Get laser lock PID configuration"""
        return self.config["laser_lock_pid"]

    def set_laser_lock_pid_config(self, **kwargs):
        """
        Set laser lock PID configuration

        Args:
            **kwargs: Laser lock PID parameters (p, i, d, invert_pid)
        """
        for key, value in kwargs.items():
            if key in self.config["laser_lock_pid"]:
                self.config["laser_lock_pid"][key] = value
        self.save()

    def reset_to_defaults(self):
        """Reset configuration to default values"""
        self.config = self.default_config.copy()
        self.save()
        print("✓ Configuration reset to defaults")

    def export_config(self, filename):
        """Export configuration to a different file"""
        try:
            export_path = self.app_dir / filename
            with open(export_path, 'w') as f:
                toml.dump(self.config, f)
            print(f"✓ Configuration exported to {export_path}")
            return True
        except Exception as e:
            print(f"✗ Error exporting config: {e}")
            return False

    def import_config(self, filename):
        """Import configuration from a file"""
        try:
            import_path = self.app_dir / filename
            if import_path.exists():
                with open(import_path, 'r') as f:
                    imported = toml.load(f)
                    self._merge_config(self.config, imported)
                self.save()
                print(f"✓ Configuration imported from {import_path}")
                return True
            else:
                print(f"✗ Import file not found: {import_path}")
                return False
        except Exception as e:
            print(f"✗ Error importing config: {e}")
            return False


# Test/example usage
if __name__ == "__main__":
    config = ConfigManager()

    print("\nCurrent configuration:")
    print(f"Last port: {config.get_last_port()}")
    print(f"Laser config: {config.get_laser_config()}")
    print(f"TEC config: {config.get_tec_config()}")

    # Example: Set last port
    config.set_last_port("COM4")

    # Example: Update laser settings
    config.set_laser_config(ilaser=50.0, ilmax=100.0)

    # Example: Update device info
    config.set_device_info(
        model="CTL200-0-B-200",
        firmware="V0.17",
        serial_number="17095"
    )

    print("\nConfiguration saved!")
