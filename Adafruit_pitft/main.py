#!/usr/bin/env python3
"""
main_controller.py
------------------
Main control software for the underwater spectrometer.
Provides a menu-driven interface via a Pimoroni Display HAT Mini,
controlled by onboard buttons and optional external Hall effect sensors.

Features:
- Menu navigation for settings adjustment.
- Configuration flags for optional hardware components.
- Display of system status (time, network).
- Spectrometer operations (Live view, capture, auto-integration - planned).
"""

import os
import sys
import time
import signal
import datetime
import subprocess
import threading
import logging
import io  # For in-memory plot rendering
import csv  # For future data saving
import numpy as np  # Might need later for data manipulation
import smbus2

# --- Configuration Flags ---
# Set these flags based on the hardware connected.
# If a flag is True, the code will expect the hardware to be present and attempt initialization.
# If initialization fails despite the flag being True, an error will be logged.
USE_DISPLAY_HAT = False  # Set to True if Pimoroni Display HAT Mini is connected
USE_ADAFRUIT_PITFT = (
    True  # Set to True if Adafruit PiTFT 2.8" is connected and configured
)
USE_GPIO_BUTTONS = True  # Set to True if GPIO (LCD/Hall) buttons are connected
USE_HALL_EFFECT_BUTTONS = (
    True  # Set to True to map external Hall sensors (requires USE_GPIO_BUTTONS=True)
)
USE_LEAK_SENSOR = True  # Set to True if the external leak sensor is connected (requires USE_GPIO_BUTTONS=True)
USE_SPECTROMETER = (
    True  # Set to True if the spectrometer is connected and should be used
)
USE_TEMP_SENSOR_IF_AVAILABLE = False  # Set to True if MCP9808 is connected

# Attempt to import hardware-specific libraries only if configured
# RPi_GPIO defined globally for type hinting and conditional access
RPi_GPIO_lib = None
if USE_GPIO_BUTTONS:
    try:
        import RPi.GPIO as GPIO

        RPi_GPIO_lib = GPIO  # Assign to global-like scope for use
        print("RPi.GPIO library loaded successfully.")
    except ImportError:
        print("ERROR: RPi.GPIO library not found, but USE_GPIO_BUTTONS is True.")
        print("GPIO features will be disabled.")
        USE_GPIO_BUTTONS = False  # Disable GPIO usage if library fails
    except RuntimeError as e:
        print(f"ERROR: Could not load RPi.GPIO (permissions or platform issue?): {e}")
        print("GPIO features will be disabled.")
        USE_GPIO_BUTTONS = False

DisplayHATMini_lib = None
if USE_DISPLAY_HAT:
    try:
        from displayhatmini import DisplayHATMini

        DisplayHATMini_lib = DisplayHATMini
        print("DisplayHATMini library loaded successfully.")
    except ImportError:
        print("ERROR: DisplayHATMini library not found, but USE_DISPLAY_HAT is True.")
        print("Display HAT features will be disabled.")
        USE_DISPLAY_HAT = False  # Disable display usage if library fails

# --- Disable sound display ---
os.environ["ALSA_MIXER_CARD"] = "-1"
os.environ["ALSA_MIXER_DEVICE"] = "-1"

# --- Spectrometer and Plotting Libraries (Conditional Import) ---
sb = None
plt = None
Image = None  # PIL/Pillow
Spectrometer = None  # Specific class from seabreeze
usb = None

if USE_SPECTROMETER:
    try:
        # Set backend explicitly before importing pyplot
        import matplotlib

        matplotlib.use(
            "Agg"
        )  # Use non-interactive backend suitable for rendering to buffer
        import matplotlib.pyplot as plt

        print("Matplotlib loaded successfully.")
        from PIL import Image  # Pillow for image manipulation

        print("Pillow (PIL) loaded successfully.")

        import seabreeze

        seabreeze.use("pyseabreeze")  # Or 'cseabreeze' if installed and preferred
        import seabreeze.spectrometers as sb
        from seabreeze.spectrometers import Spectrometer  # Import the class directly

        try:
            import usb.core
        except ImportError:
            print("WARNING: pyusb library not found, cannot catch specific USBError.")
            # usb will remain None

        print("Seabreeze libraries loaded successfully.")
    except ImportError as e:
        print(
            f"ERROR: Spectrometer/Plotting library missing ({e}), but USE_SPECTROMETER is True."
        )
        print("Spectrometer features will be disabled.")
        USE_SPECTROMETER = False
        sb = None
        plt = None
        Image = None
        Spectrometer = None
        usb = None  # Ensure it's None on import error
    except Exception as e:
        print(f"ERROR: Unexpected error loading Spectrometer/Plotting libraries: {e}")
        USE_SPECTROMETER = False
        sb = None
        plt = None
        Image = None
        Spectrometer = None
        usb = None  # Ensure it's None on other errors

# Import for MCP9808
current_script_dir = os.path.dirname(os.path.abspath(__file__))
lib_dir = os.path.join(current_script_dir, "lib")
# Add the lib directory itself to sys.path
# This would allow "import Adafruit_Python_MCP9808" if it were a proper package with __init__.py
if lib_dir not in sys.path:
    sys.path.insert(0, lib_dir)
    print(f"Added to sys.path: {lib_dir}")

mcp9808_module_dir = os.path.join(lib_dir, "Adafruit_Python_MCP9808")
if mcp9808_module_dir not in sys.path:
    sys.path.insert(0, mcp9808_module_dir)
    print(f"Added to sys.path for MCP9808 direct import: {mcp9808_module_dir}")

TEMP_SENSOR_I2C_BUS = 1
TEMP_SENSOR_I2C_ADDR = 0x18
MCP9808_Driver = None
if USE_TEMP_SENSOR_IF_AVAILABLE:  # Assuming you rename this flag for consistency
    try:
        # This import now works because lib/Adafruit_Python_MCP9808/ is in sys.path
        from MCP9808 import MCP9808 as MCP9808_Sensor_Class

        MCP9808_Driver = MCP9808_Sensor_Class
        print("MCP9808.py library module loaded successfully from local lib/ path.")
    except ImportError as e:
        print(
            f"ERROR: MCP9808.py not found or cannot be imported (ImportError: {e}), but USE_TEMP_SENSOR_IF_AVAILABLE is True."
        )
        print(f"Current sys.path: {sys.path}")
        print("Temperature sensor features will be disabled.")
        USE_TEMP_SENSOR_IF_AVAILABLE = False  # Update the flag
    except Exception as e:
        print(f"ERROR: Could not load MCP9808.py: {e}")
        USE_TEMP_SENSOR_IF_AVAILABLE = False  # Update the flag


# Pygame is always needed for the display buffer and event loop
try:
    import pygame
except ImportError:
    print("FATAL ERROR: Pygame library not found. Cannot run.")
    sys.exit(1)

# --- Logging Setup ---
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# --- Global Variables ---
# These are managed primarily within classes or the main function after init
g_shutdown_flag = threading.Event()  # Used to signal shutdown to threads and loops
g_leak_detected_flag = threading.Event()

# --- Disclaimer Text ---
# Use triple quotes for multi-line
DISCLAIMER_TEXT = """\
This open-source software is freely provided
for marine conservation and scientific research.

It comes with ABSOLUTELY NO WARRANTY, no
technical support, and no guarantee of accuracy.

Always verify all data before using for research
purposes. Dive in at your own risk!

"""

# --- Constants ---
# Define the base directory relative to the user's home
DATA_BASE_DIR = os.path.expanduser("~/pysb-app")
DATA_DIR = os.path.join(DATA_BASE_DIR, "spectra_data")
CSV_BASE_FILENAME = "spectra_log.csv"  # Base name for the daily CSV file

PLOT_SAVE_DIR = DATA_DIR  # Save plots in the same directory

# Lens Type Constants
LENS_TYPE_FIBER = "FIBER"
LENS_TYPE_CABLE = "CABLE"
LENS_TYPE_FIBER_CABLE = "FIBER+CABLE"
DEFAULT_LENS_TYPE = LENS_TYPE_FIBER

# Collection Mode Constants
MODE_RAW = "RAW"
MODE_RADIANCE = (
    "RADIANCE"  # Defined, but not used in AVAILABLE_COLLECTION_MODES for now
)
MODE_REFLECTANCE = "REFLECTANCE"

# Explicitly list available modes for the menu
AVAILABLE_COLLECTION_MODES = (MODE_RAW, MODE_REFLECTANCE)
DEFAULT_COLLECTION_MODE = MODE_RAW  # Default to RAW

# CSV Spectra Types (some mirror Collection Modes, some are specific)
SPECTRA_TYPE_RAW = "RAW"  # Corresponds to MODE_RAW sample
SPECTRA_TYPE_REFLECTANCE = (
    "REFLECTANCE"  # Corresponds to MODE_REFLECTANCE calculated sample
)
SPECTRA_TYPE_DARK_REF = "DARK"  # Dark reference spectrum
SPECTRA_TYPE_WHITE_REF = "WHITE"  # White reference spectrum
SPECTRA_TYPE_RAW_TARGET_FOR_REFLECTANCE = (
    "RAW_REFLECTANCE"  # Raw target used for a REFLECTANCE calculation
)

if DEFAULT_COLLECTION_MODE not in AVAILABLE_COLLECTION_MODES:
    logger.warning(
        f"Default collection mode '{DEFAULT_COLLECTION_MODE}' is not in AVAILABLE_COLLECTION_MODES. Falling back."
    )
    if AVAILABLE_COLLECTION_MODES:
        DEFAULT_COLLECTION_MODE = AVAILABLE_COLLECTION_MODES[0]
    else:
        DEFAULT_COLLECTION_MODE = MODE_RAW  # Fallback
        AVAILABLE_COLLECTION_MODES = (MODE_RAW,)  # Ensure it's a tuple


# Integration Time (ms)
DEFAULT_INTEGRATION_TIME_MS = 1000
MIN_INTEGRATION_TIME_MS = 100  # User-settable minimum in menu
MAX_INTEGRATION_TIME_MS = 6000  # User-settable maximum in menu
INTEGRATION_TIME_STEP_MS = 100

# Temperature update (ms)
TEMP_UPDATE_INTERVAL_S = 10.0  # How often to read temperature

# --- Spectrometer Hardware Constants (from user input) ---
SPECTROMETER_INTEGRATION_TIME_MIN_US = 3800  # Actual hardware minimum in microseconds
SPECTROMETER_INTEGRATION_TIME_MAX_US = (
    6000000  # Actual hardware maximum in microseconds
)
SPECTROMETER_INTEGRATION_TIME_BASE_US = (
    10  # Smallest increment hardware supports (microseconds)
)
SPECTROMETER_MAX_ADC_COUNT = 16383  # Max ADC reading (14-bit for this device)

# --- Auto-Integration Constants ---
AUTO_INTEG_TARGET_LOW_PERCENT = 80.0  # Target saturation percentage, lower bound
AUTO_INTEG_TARGET_HIGH_PERCENT = 95.0  # Target saturation percentage, upper bound
AUTO_INTEG_MAX_ITERATIONS = 20
AUTO_INTEG_PROPORTIONAL_GAIN = 0.8
AUTO_INTEG_MIN_ADJUSTMENT_US = SPECTROMETER_INTEGRATION_TIME_BASE_US * 5
AUTO_INTEG_OSCILLATION_DAMPING_FACTOR = 0.5

# Plotting Constants
USE_LIVE_SMOOTHING = True
LIVE_SMOOTHING_WINDOW_SIZE = 9
Y_AXIS_DEFAULT_MAX = 1000.0  # Ensure float for consistency
Y_AXIS_REFLECTANCE_DEFAULT_MAX = 10  # Default Y max for reflectance plots
Y_AXIS_REFLECTANCE_RESCALE_MIN_CEILING = (
    0.2  # Min Y-axis ceiling after rescale for reflectance
)
Y_AXIS_REFLECTANCE_RESCALE_MAX_CEILING = (
    50  # Max Y-axis ceiling after rescale for reflectance
)
Y_AXIS_RESCALE_FACTOR = 1.2
Y_AXIS_MIN_CEILING = 200.0  # Ensure float
Y_AXIS_MIN_CEILING_RELATIVE = 1.1

# --- GPIO Pin Definitions (BCM Mode) ---
if USE_ADAFRUIT_PITFT:  # Adafruit PiTFT 2.8" Capacitive Button GPIOs
    logger.info("Using Adafruit PiTFT Button GPIO mapping.")
    PIN_BTN_A = 27  # Often labeled as one of the 4 tactile switch spots
    PIN_BTN_B = 23
    PIN_BTN_X = 22
    PIN_BTN_Y = 17
    # These are common GPIOs used for the optional tactile switches on Adafruit PiTFTs.
    # Double-check with the specific PiTFT model if you solder them on,
    # or which header pins they correspond to if using the breakout.
else:  # Default to Pimoroni Display HAT Mini (or other if USE_DISPLAY_HAT is true)
    logger.info("Using Pimoroni Display HAT Mini Button GPIO mapping (or defaults).")
    PIN_BTN_A = 5  # Pimoroni Display HAT 'A'
    PIN_BTN_B = 6  # Pimoroni Display HAT 'B'
    PIN_BTN_X = 16  # Pimoroni Display HAT 'X'
    PIN_BTN_Y = 24  # Pimoroni Display HAT 'Y'

PIN_HALL_UP = 20
PIN_HALL_DOWN = 21
PIN_HALL_ENTER = 1
PIN_HALL_BACK = 12

PIN_LEAK = 26

# Button Logical Names (used internally)
BTN_UP = "up"
BTN_DOWN = "down"
BTN_ENTER = "enter"
BTN_BACK = "back"

# Screen dimensions
SCREEN_WIDTH = 320
SCREEN_HEIGHT = 240

# Colors
BLACK = (0, 0, 0)
WHITE = (255, 255, 255)
BLUE = (0, 0, 255)
GREEN = (0, 255, 0)
RED = (255, 0, 0)
YELLOW = (255, 255, 0)
GRAY = (128, 128, 128)
CYAN = (0, 255, 255)
MAGENTA = (255, 0, 255)

# Menu Layout
FONT_SIZE = 16
TITLE_FONT_SIZE = 22
HINT_FONT_SIZE = 15
DISCLAIMER_FONT_SIZE = 14
MENU_SPACING = 19
MENU_MARGIN_TOP = 44
MENU_MARGIN_LEFT = 12

# --- Font Filenames
TITLE_FONT_FILENAME = "ChakraPetch-Medium.ttf"
MAIN_FONT_FILENAME = "Roboto-Regular.ttf"
HINT_FONT_FILENAME = "Roboto-Regular.ttf"
SPECTRO_FONT_FILENAME = "Roboto-Regular.ttf"
SPECTRO_FONT_SIZE = 14

# Timing
DEBOUNCE_DELAY_S = 0.2
NETWORK_UPDATE_INTERVAL_S = 10.0
MAIN_LOOP_DELAY_S = 0.03
SPLASH_DURATION_S = 3.0
SPECTRO_LOOP_DELAY_S = 0.05
SPECTRO_REFRESH_OVERHEAD_S = 0.05
# Epsilon for division, to prevent division by zero
DIVISION_EPSILON = 1e-9


# --- Classes ---
class ButtonHandler:
    """
    Handles GPIO button inputs (Display HAT via library callback + optional Hall sensors/Leak)
    and maps Pygame key events, providing a unified button interface.
    """

    # Map Pimoroni HAT pins to our logical button names
    _DH_PIN_TO_BUTTON = {
        PIN_BTN_A: BTN_ENTER,
        PIN_BTN_B: BTN_BACK,
        PIN_BTN_X: BTN_UP,
        PIN_BTN_Y: BTN_DOWN,
    }

    def __init__(self, display_hat_obj=None):  # Pass the display_hat object
        """Initializes button states and debounce tracking."""
        self.display_hat = display_hat_obj

        self._gpio_available = USE_GPIO_BUTTONS and RPi_GPIO_lib is not None
        self._display_hat_buttons_enabled = (
            USE_DISPLAY_HAT
            and not USE_ADAFRUIT_PITFT
            and self.display_hat is not None
            and DisplayHATMini_lib is not None
        )

        self._hall_buttons_enabled = USE_HALL_EFFECT_BUTTONS and self._gpio_available
        self._leak_sensor_enabled = USE_LEAK_SENSOR and self._gpio_available

        self._button_states = {
            btn: False for btn in [BTN_UP, BTN_DOWN, BTN_ENTER, BTN_BACK]
        }
        self._state_lock = threading.Lock()

        self._last_press_time = {
            btn: 0.0 for btn in [BTN_UP, BTN_DOWN, BTN_ENTER, BTN_BACK]
        }
        self._manual_pin_to_button: dict[int, str] = {}
        self._manual_gpio_pins_used: set[int] = set()

        if self._gpio_available or self._display_hat_buttons_enabled:
            self._setup_inputs()
        else:
            logger.warning(
                "Neither GPIO nor Display HAT buttons are available/enabled. Only keyboard input will work."
            )

    def _setup_inputs(self):
        logger.info("Setting up button/sensor inputs...")
        if self._gpio_available and (
            self._hall_buttons_enabled
            or self._leak_sensor_enabled
            or not self._display_hat_buttons_enabled
        ):
            try:
                current_mode = RPi_GPIO_lib.getmode()
                if current_mode is None:
                    RPi_GPIO_lib.setmode(GPIO.BCM)
                    logger.info("  GPIO mode set to BCM.")
                elif current_mode != GPIO.BCM:
                    logger.warning(
                        f"  GPIO mode was already set to {current_mode}, attempting to change to BCM."
                    )
                    try:
                        RPi_GPIO_lib.setmode(GPIO.BCM)
                    except RuntimeError as e:
                        logger.error(
                            f"  Failed to change GPIO mode to BCM: {e}. Manual GPIO setup might fail."
                        )
                RPi_GPIO_lib.setwarnings(False)

                # Setup Hall Effect sensors
                if self._hall_buttons_enabled:
                    logger.info(
                        "  Setting up Hall Effect sensor inputs via RPi.GPIO..."
                    )
                    hall_pins = {
                        PIN_HALL_UP: BTN_UP,
                        PIN_HALL_DOWN: BTN_DOWN,
                        PIN_HALL_ENTER: BTN_ENTER,
                        PIN_HALL_BACK: BTN_BACK,
                    }
                    assert len(hall_pins) == len(
                        set(hall_pins.keys())
                    ), "Duplicate Hall Effect pin definitions"
                    for pin, name in hall_pins.items():
                        assert isinstance(
                            pin, int
                        ), f"Hall pin {pin} must be an integer"
                        if not (
                            self._display_hat_buttons_enabled
                            and pin in self._DH_PIN_TO_BUTTON
                        ):
                            RPi_GPIO_lib.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
                            RPi_GPIO_lib.add_event_detect(
                                pin,
                                GPIO.FALLING,
                                callback=self._manual_gpio_callback,
                                bouncetime=int(DEBOUNCE_DELAY_S * 1000),
                            )
                            self._manual_pin_to_button[pin] = name
                            self._manual_gpio_pins_used.add(pin)
                            logger.info(
                                f"    Mapped Hall Effect GPIO {pin} to '{name}'"
                            )
                        else:
                            logger.warning(
                                f"    Skipping manual setup for GPIO {pin} (Hall '{name}') as it's a Display HAT pin."
                            )
                else:
                    logger.info(
                        "  Hall Effect button inputs disabled or GPIO unavailable."
                    )

                # Setup Adafruit PiTFT tactile buttons
                if USE_ADAFRUIT_PITFT:
                    logger.info(
                        "  Setting up Adafruit PiTFT tactile button inputs via RPi.GPIO..."
                    )
                    pitft_pins = {
                        PIN_BTN_A: BTN_ENTER,  # A button -> Enter (GPIO 27)
                        PIN_BTN_B: BTN_BACK,  # B button -> Back (GPIO 23)
                        PIN_BTN_X: BTN_UP,  # X button -> Up (GPIO 22)
                        PIN_BTN_Y: BTN_DOWN,  # Y button -> Down (GPIO 17)
                    }

                    for pin, name in pitft_pins.items():
                        assert isinstance(
                            pin, int
                        ), f"PiTFT pin {pin} must be an integer"
                        if (
                            pin not in self._manual_gpio_pins_used
                        ):  # Avoid duplicate setup
                            RPi_GPIO_lib.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
                            RPi_GPIO_lib.add_event_detect(
                                pin,
                                GPIO.FALLING,
                                callback=self._manual_gpio_callback,
                                bouncetime=int(DEBOUNCE_DELAY_S * 1000),
                            )
                            self._manual_pin_to_button[pin] = name
                            self._manual_gpio_pins_used.add(pin)
                            logger.info(f"    Mapped PiTFT GPIO {pin} to '{name}'")
                        else:
                            logger.warning(
                                f"    Skipping PiTFT GPIO {pin} ('{name}') - already configured"
                            )
                else:
                    logger.info("  Adafruit PiTFT tactile buttons disabled.")

                # Setup leak sensor
                if self._leak_sensor_enabled:
                    assert isinstance(
                        PIN_LEAK, int
                    ), "Leak sensor pin must be an integer"
                    logger.info(
                        f"  Setting up Leak sensor input on GPIO {PIN_LEAK} via RPi.GPIO..."
                    )
                    if not (
                        self._display_hat_buttons_enabled
                        and PIN_LEAK in self._DH_PIN_TO_BUTTON
                    ):
                        RPi_GPIO_lib.setup(PIN_LEAK, GPIO.IN, pull_up_down=GPIO.PUD_UP)
                        RPi_GPIO_lib.add_event_detect(
                            PIN_LEAK,
                            GPIO.FALLING,
                            callback=self._leak_callback,
                            bouncetime=1000,
                        )
                        self._manual_gpio_pins_used.add(PIN_LEAK)
                        logger.info(
                            f"    Leak sensor event detection added on GPIO {PIN_LEAK}"
                        )
                    else:
                        logger.warning(
                            f"    Skipping manual setup for GPIO {PIN_LEAK} (Leak) as it's a Display HAT pin."
                        )
                else:
                    logger.info("  Leak sensor input disabled or GPIO unavailable.")

            except RuntimeError as e:
                logger.error(
                    f"RUNTIME ERROR setting up manual GPIO: {e}", exc_info=True
                )
                self._hall_buttons_enabled = False
                self._leak_sensor_enabled = False
                self._manual_gpio_pins_used.clear()
            except Exception as e:
                logger.error(
                    f"UNEXPECTED EXCEPTION setting up manual GPIO: {e}", exc_info=True
                )
                self._hall_buttons_enabled = False
                self._leak_sensor_enabled = False
                self._manual_gpio_pins_used.clear()

        # Display HAT button setup (if enabled)
        if self._display_hat_buttons_enabled:
            try:
                logger.info("  Registering Display HAT button callback...")
                assert self.display_hat is not None and hasattr(
                    self.display_hat, "on_button_pressed"
                ), "Display HAT object is None or lacks 'on_button_pressed'"
                self.display_hat.on_button_pressed(self._display_hat_callback)
                logger.info("  Display HAT button callback registered successfully.")
            except AssertionError as ae:
                logger.error(
                    f"Failed to register Display HAT callback prerequisite: {ae}"
                )
                self._display_hat_buttons_enabled = False
            except Exception as e:
                logger.error(
                    f"Failed to register Display HAT button callback: {e}",
                    exc_info=True,
                )
                self._display_hat_buttons_enabled = False
        else:
            logger.info("  Display HAT buttons disabled or unavailable.")

    def _display_hat_callback(self, pin: int):
        assert isinstance(
            pin, int
        ), f"Invalid pin type received in DH callback: {type(pin)}"
        button_name = self._DH_PIN_TO_BUTTON.get(pin)
        if button_name is None:
            return
        current_time = time.monotonic()
        with self._state_lock:
            last_press = self._last_press_time.get(button_name, 0.0)
            assert current_time >= last_press, "Monotonic time decreased unexpectedly"
            if (current_time - last_press) > DEBOUNCE_DELAY_S:
                self._button_states[button_name] = True
                self._last_press_time[button_name] = current_time
                logger.debug(f"Display HAT Button pressed: {button_name} (Pin {pin})")

    def _manual_gpio_callback(self, channel: int):
        assert isinstance(
            channel, int
        ), f"Invalid channel type received in manual GPIO callback: {type(channel)}"
        button_name = self._manual_pin_to_button.get(channel)
        if button_name is None:
            return
        current_time = time.monotonic()
        with self._state_lock:
            last_press = self._last_press_time.get(button_name, 0.0)
            assert current_time >= last_press, "Monotonic time decreased unexpectedly"
            if (current_time - last_press) > DEBOUNCE_DELAY_S:
                self._button_states[button_name] = True
                self._last_press_time[button_name] = current_time
                logger.debug(
                    f"Manual GPIO Button pressed: {button_name} (Pin {channel})"
                )

    def _leak_callback(self, channel: int):
        assert (
            channel == PIN_LEAK
        ), f"Leak callback triggered for unexpected channel {channel}"
        logger.critical(f"!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!")
        logger.critical(f"!!! WATER LEAK DETECTED on GPIO {channel} !!!")
        logger.critical(f"!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!")
        g_leak_detected_flag.set()

    def check_button(self, button_name: str) -> bool:
        assert (
            button_name in self._button_states
        ), f"Invalid button name requested: {button_name}"
        pressed = False
        with self._state_lock:
            if self._button_states[button_name]:
                pressed = True
                self._button_states[button_name] = False
        return pressed

    def process_pygame_events(self) -> str | None:
        assert pygame.get_init(), "Pygame not initialized when processing events"
        quit_requested = False
        try:
            events = pygame.event.get()
            for event in events:
                if event.type == pygame.QUIT:
                    logger.info("Pygame QUIT event received.")
                    quit_requested = True
                if event.type == pygame.KEYDOWN:
                    key_map = {
                        pygame.K_UP: BTN_UP,
                        pygame.K_w: BTN_UP,
                        pygame.K_DOWN: BTN_DOWN,
                        pygame.K_s: BTN_DOWN,
                        pygame.K_RETURN: BTN_ENTER,
                        pygame.K_RIGHT: BTN_ENTER,
                        pygame.K_d: BTN_ENTER,
                        pygame.K_BACKSPACE: BTN_BACK,
                        pygame.K_LEFT: BTN_BACK,
                        pygame.K_a: BTN_BACK,
                        pygame.K_ESCAPE: "QUIT",
                    }
                    button_name = key_map.get(event.key)
                    if button_name == "QUIT":
                        quit_requested = True
                        logger.info("Escape key pressed, requesting QUIT.")
                    elif button_name:
                        with self._state_lock:
                            self._button_states[button_name] = True
                        logger.debug(f"Key mapped to button press: {button_name}")
        except pygame.error as e:
            logger.error(f"Pygame error during event processing: {e}")
        except Exception as e:
            logger.error(
                f"Unexpected error during event processing: {e}", exc_info=True
            )
        return "QUIT" if quit_requested else None

    def cleanup(self):
        if self._gpio_available and self._manual_gpio_pins_used:
            logger.info(
                f"Cleaning up manually configured GPIO pins: {list(self._manual_gpio_pins_used)}"
            )
            try:
                for pin in self._manual_gpio_pins_used:
                    assert isinstance(
                        pin, int
                    ), f"Invalid pin type during cleanup: {type(pin)}"
                    try:
                        RPi_GPIO_lib.remove_event_detect(pin)
                    except RuntimeError:
                        logger.warning(
                            f"Could not remove event detect for pin {pin} during cleanup."
                        )
                RPi_GPIO_lib.cleanup(list(self._manual_gpio_pins_used))
                logger.info("Manual GPIO cleanup complete for specified pins.")
            except Exception as e:
                logger.error(f"Error during manual GPIO cleanup: {e}")
        else:
            logger.info(
                "Manual GPIO cleanup skipped (no pins manually configured or GPIO unavailable)."
            )


# --- Temperature I2C Wrapper Classes (SMBus2Device, SMBus2Wrapper ---
class SMBus2Device:
    def __init__(self, address, busnum):
        self.address = address
        self.bus = smbus2.SMBus(busnum)

    def readU16BE(self, register_address):
        word = self.bus.read_word_data(self.address, register_address)
        return ((word & 0xFF) << 8) | (word >> 8)


class SMBus2Wrapper:
    def __init__(self, busnum=1):
        self.busnum = busnum

    def get_i2c_device(self, address, **kwargs):
        bus_to_use = kwargs.get("busnum", self.busnum)
        return SMBus2Device(address, bus_to_use)


# --- TempSensorInfo Class ---
class TempSensorInfo:
    def __init__(self, sensor_instance):
        self._temperature_c: float | str | None = "N/A"  # Default to "N/A" if no sensor
        self._sensor = sensor_instance  # This can be None
        self._lock = threading.Lock()
        self._update_thread = None
        self._last_update_time = 0.0
        assert isinstance(g_shutdown_flag, threading.Event)

        if not self._sensor:
            logger.warning("TempSensorInfo initialized with no active sensor instance.")
            # No need to set self._temperature_c here, already "N/A" by default
        else:
            # If sensor is present, try an initial read to see if it works.
            # This is optional, but can give early feedback.
            # Loop will handle periodic reads.
            try:
                initial_temp = self._sensor.readTempC()
                if isinstance(initial_temp, (float, int)):
                    self._temperature_c = float(initial_temp)
                else:
                    self._temperature_c = "Init Error"
            except Exception:
                self._temperature_c = "Init Fail"

    def start_updates(self):
        if not self._sensor:  # If no sensor, the thread won't do useful work
            logger.info(
                "TempSensorInfo: No sensor instance. Update thread will not start real reads."
            )
            # We can still start a dummy thread or just let get_temperature_c return its state
            # For simplicity, if no sensor, the loop will just yield "N/A" or "Sensor Error"
            # The update loop itself will handle the _sensor being None.
            # return # Optionally, don't even start the thread if no sensor.
            # But for consistency, let's allow it to run and handle None.

        if self._update_thread and self._update_thread.is_alive():
            logger.warning("TempSensorInfo: Update thread already running.")
            return

        assert self._update_thread is None or not self._update_thread.is_alive()
        logger.info("Starting temperature sensor update thread.")
        self._update_thread = threading.Thread(
            target=self._temp_update_loop, daemon=True
        )
        self._update_thread.start()

    def stop_updates(self):
        # ... (stop_updates logic remains the same)
        if self._update_thread and self._update_thread.is_alive():
            logger.info("Waiting for temperature update thread to stop...")
            try:
                self._update_thread.join(timeout=TEMP_UPDATE_INTERVAL_S + 1.0)
                if self._update_thread.is_alive():
                    logger.warning(
                        "Temperature update thread did not terminate cleanly."
                    )
            except Exception as e:
                logger.error(f"Error joining temperature update thread: {e}")
        else:
            logger.info("Temperature update thread was not running or already stopped.")
        self._update_thread = None
        logger.info("Temperature update thread stopped.")

    def get_temperature_c(self) -> float | str | None:
        with self._lock:
            return self._temperature_c

    def _temp_update_loop(self):
        logger.info("Temperature update loop started.")
        # No early exit if self._sensor is None; loop will handle it.

        while not g_shutdown_flag.is_set():
            start_time = time.monotonic()
            current_temp_value = "N/A"  # Default for this iteration
            raw_temp = "N/A"  # Initialize raw_temp
            if self._sensor:  # Only attempt to read if a sensor object exists
                try:
                    raw_temp = self._sensor.readTempC()
                    if isinstance(raw_temp, (float, int)):
                        current_temp_value = float(raw_temp)
                    else:
                        logger.error(
                            f"TempSensorInfo: Invalid temperature data type: {type(raw_temp)}",
                            exc_info=False,
                        )
                        current_temp_value = "Type Error"
                except AttributeError:
                    logger.error(
                        "TempSensorInfo: Sensor object missing or method not found during read.",
                        exc_info=True,
                    )
                    current_temp_value = "Sensor AttrErr"  # More specific error
                    self._sensor = (
                        None  # Assume sensor is lost if attribute error persists
                    )
                except Exception as e:
                    logger.error(f"Error reading temperature: {e}", exc_info=False)
                    current_temp_value = "Read Error"
            else:  # self._sensor is None
                current_temp_value = "No Sensor"  # Or keep it "N/A"

            with self._lock:
                self._temperature_c = current_temp_value
            self._last_update_time = time.monotonic()

            elapsed_time = time.monotonic() - start_time
            wait_time = max(0, TEMP_UPDATE_INTERVAL_S - elapsed_time)
            g_shutdown_flag.wait(timeout=wait_time)
        logger.info("Temperature update loop finished.")


class NetworkInfo:
    """
    Handles retrieval of network information (WiFi SSID, IP Address).
    Runs network checks in a separate thread to avoid blocking the main UI loop.
    """

    _WLAN_IFACE = "wlan0"  # Network interface to check

    def __init__(self):
        """Initializes network info placeholders and starts the update thread."""
        self._wifi_name = "Initializing..."
        self._ip_address = "Initializing..."
        self._lock = threading.Lock()  # Protect access to shared state
        self._update_thread = None
        self._last_update_time = 0.0
        assert isinstance(
            g_shutdown_flag, threading.Event
        ), "Global shutdown flag not initialized or incorrect type"

    def start_updates(self):
        assert (
            self._update_thread is None or not self._update_thread.is_alive()
        ), "Network update thread already started"
        logger.info("Starting network info update thread.")
        self._update_thread = threading.Thread(
            target=self._network_update_loop, daemon=True
        )
        self._update_thread.start()

    def stop_updates(self):
        if self._update_thread and self._update_thread.is_alive():
            logger.info("Waiting for network info update thread to stop...")
            try:
                self._update_thread.join(timeout=NETWORK_UPDATE_INTERVAL_S + 1.0)
                if self._update_thread.is_alive():
                    logger.warning(
                        "Network update thread did not terminate cleanly after timeout."
                    )
            except Exception as e:
                logger.error(f"Error joining network update thread: {e}")
        else:
            logger.info(
                "Network info update thread was not running or already stopped."
            )
        self._update_thread = None
        logger.info("Network info update thread stopped.")

    def get_wifi_name(self) -> str:
        assert self._lock is not None, "NetworkInfo lock not initialized"
        with self._lock:
            assert isinstance(
                self._wifi_name, str
            ), "Internal wifi_name state is not a string"
            return self._wifi_name

    def get_ip_address(self) -> str:
        assert self._lock is not None, "NetworkInfo lock not initialized"
        with self._lock:
            assert isinstance(
                self._ip_address, str
            ), "Internal ip_address state is not a string"
            return self._ip_address

    def _is_interface_up(self) -> bool:
        operstate_path = f"/sys/class/net/{self._WLAN_IFACE}/operstate"
        assert isinstance(
            operstate_path, str
        ), "Generated operstate path is not a string"
        try:
            if not os.path.exists(operstate_path):
                return False
            with open(operstate_path, "r") as f:
                return f.read(10).strip().lower() == "up"
        except FileNotFoundError:
            return False
        except OSError as e:
            logger.error(
                f"OS error checking interface status for {self._WLAN_IFACE}: {e}"
            )
            return False
        except Exception as e:
            logger.error(
                f"Unexpected error checking interface status for {self._WLAN_IFACE}: {e}"
            )
            return False

    def _fetch_wifi_name(self) -> str:
        if not self._is_interface_up():
            return "Not Connected"
        try:
            result = subprocess.run(
                ["iwgetid", "-r"],
                capture_output=True,
                text=True,
                check=False,
                timeout=5.0,
            )
            assert isinstance(
                result, subprocess.CompletedProcess
            ), "subprocess.run did not return expected object"
            return (
                result.stdout.strip()
                if result.returncode == 0 and result.stdout and result.stdout.strip()
                else "Not Connected"
            )
        except FileNotFoundError:
            logger.error("'iwgetid' command not found.")
            return "Error (No iwgetid)"
        except subprocess.TimeoutExpired:
            logger.warning("'iwgetid' command timed out.")
            return "Error (Timeout)"
        except Exception as e:
            logger.error(f"Error running iwgetid: {e}")
            return "Error (Exec)"

    def _fetch_ip_address(self) -> str:
        if not self._is_interface_up():
            return "Not Connected"
        try:
            result = subprocess.run(
                ["hostname", "-I"],
                capture_output=True,
                text=True,
                check=False,
                timeout=5.0,
            )
            assert isinstance(
                result, subprocess.CompletedProcess
            ), "subprocess.run did not return expected object"
            if result.returncode == 0 and result.stdout and result.stdout.strip():
                ip_list = result.stdout.strip().split()
                if ip_list:
                    assert isinstance(ip_list[0], str)
                    return ip_list[0]
                else:
                    return "No IP"
            else:
                return "No IP"
        except FileNotFoundError:
            logger.error("'hostname' command not found.")
            return "Error (No hostname)"
        except subprocess.TimeoutExpired:
            logger.warning("'hostname -I' command timed out.")
            return "Error (Timeout)"
        except Exception as e:
            logger.error(f"Error running hostname -I: {e}")
            return "Error (Exec)"

    def _network_update_loop(self):
        logger.info("Network update loop started.")
        while not g_shutdown_flag.is_set():
            start_time = time.monotonic()
            new_wifi, new_ip = "Error", "Error"
            try:
                new_wifi, new_ip = self._fetch_wifi_name(), self._fetch_ip_address()
                assert isinstance(new_wifi, str) and isinstance(new_ip, str)
                with self._lock:
                    self._wifi_name, self._ip_address = new_wifi, new_ip
                self._last_update_time = time.monotonic()
            except Exception as e:
                logger.error(f"Error in network update loop: {e}", exc_info=True)
                with self._lock:
                    self._wifi_name, self._ip_address = str(new_wifi), str(new_ip)
            elapsed_time = time.monotonic() - start_time
            wait_time = max(0, NETWORK_UPDATE_INTERVAL_S - elapsed_time)
            assert (
                isinstance(wait_time, (float, int)) and wait_time >= 0
            ), f"Invalid wait time calculated: {wait_time}"
            g_shutdown_flag.wait(timeout=wait_time)
        logger.info("Network update loop finished.")


class MenuSystem:
    """
    Manages the main menu UI, state, and interactions.
    """

    MENU_ITEM_CAPTURE = "LOG SPECTRA"
    MENU_ITEM_INTEGRATION = "INTEGRATION TIME"
    MENU_ITEM_COLLECTION_MODE = "COLLECTION MODE"
    MENU_ITEM_LENS_TYPE = "LENS TYPE"
    MENU_ITEM_DATE = "DATE"
    MENU_ITEM_TIME = "TIME"
    MENU_ITEM_WIFI = "WIFI"
    MENU_ITEM_TEMPERATURE = "TEMPERATURE"
    MENU_ITEM_IP = "IP"

    EDIT_TYPE_NONE = 0
    EDIT_TYPE_INTEGRATION = 1
    EDIT_TYPE_DATE = 2
    EDIT_TYPE_TIME = 3
    EDIT_TYPE_COLLECTION_MODE = 4
    EDIT_TYPE_LENS_TYPE = 5

    FIELD_YEAR, FIELD_MONTH, FIELD_DAY = "year", "month", "day"
    FIELD_HOUR, FIELD_MINUTE = "hour", "minute"

    COLLECTION_MODES = AVAILABLE_COLLECTION_MODES
    LENS_TYPES = (LENS_TYPE_FIBER, LENS_TYPE_CABLE, LENS_TYPE_FIBER_CABLE)

    def __init__(
        self,
        screen: pygame.Surface,
        button_handler: ButtonHandler,
        network_info: NetworkInfo,
        temp_sensor_info: TempSensorInfo,
    ):
        assert (
            screen and button_handler and network_info and temp_sensor_info
        ), "MenuSystem dependencies missing"

        self.screen = screen
        self.button_handler = button_handler
        self.network_info = network_info
        self.temp_sensor_info = temp_sensor_info
        self.display_hat = None
        self._integration_time_ms = DEFAULT_INTEGRATION_TIME_MS
        try:
            self._collection_mode_idx = self.COLLECTION_MODES.index(
                DEFAULT_COLLECTION_MODE
            )
        except ValueError:
            logger.warning(
                f"Default mode '{DEFAULT_COLLECTION_MODE}' not in {self.COLLECTION_MODES}. Defaulting."
            )
            self._collection_mode_idx = 0
        self._collection_mode = self.COLLECTION_MODES[self._collection_mode_idx]
        try:
            self._lens_type_idx = self.LENS_TYPES.index(DEFAULT_LENS_TYPE)
        except ValueError:
            logger.warning(
                f"Default lens '{DEFAULT_LENS_TYPE}' not in {self.LENS_TYPES}. Defaulting."
            )
            self._lens_type_idx = 0
        self._lens_type = self.LENS_TYPES[self._lens_type_idx]

        self._time_offset = datetime.timedelta(0)
        self._original_offset_on_edit_start: datetime.timedelta | None = None
        self._datetime_being_edited: datetime.datetime | None = None
        self._menu_items_base = (  # Base items
            (self.MENU_ITEM_CAPTURE, self.EDIT_TYPE_NONE),
            (self.MENU_ITEM_INTEGRATION, self.EDIT_TYPE_INTEGRATION),
            (self.MENU_ITEM_COLLECTION_MODE, self.EDIT_TYPE_COLLECTION_MODE),
            (self.MENU_ITEM_LENS_TYPE, self.EDIT_TYPE_LENS_TYPE),
            (self.MENU_ITEM_DATE, self.EDIT_TYPE_DATE),
            (self.MENU_ITEM_TIME, self.EDIT_TYPE_TIME),
        )
        self._menu_items_temp = (
            (self.MENU_ITEM_TEMPERATURE, self.EDIT_TYPE_NONE),
        )  # Temp
        self._menu_items_net = (  # Network items
            (self.MENU_ITEM_WIFI, self.EDIT_TYPE_NONE),
            (self.MENU_ITEM_IP, self.EDIT_TYPE_NONE),
        )
        self._menu_items = (
            self._menu_items_base + self._menu_items_temp + self._menu_items_net
        )
        self._current_selection_idx, self._is_editing, self._editing_field = (
            0,
            False,
            None,
        )
        self.font, self.title_font, self.hint_font = None, None, None
        self._value_start_offset_x = 120
        self._load_fonts()
        if self.font:
            self._calculate_value_offset()
        else:
            logger.error("Main font failed to load; cannot calculate value offset.")

    def _load_fonts(self):
        """Loads fonts from the assets folder. Uses global constants for filenames."""
        try:
            if not pygame.font.get_init():
                pygame.font.init()
                logger.info("Initializing Pygame font module.")
            assert pygame.font.get_init(), "Pygame font module failed to initialize"

            logger.info("Loading fonts from assets folder...")
            # Corrected assignment: define s_dir first
            script_dir = os.path.dirname(os.path.abspath(__file__))
            assets_dir = os.path.join(script_dir, "assets")  # Now use script_dir

            paths = {
                "title": os.path.join(assets_dir, TITLE_FONT_FILENAME),
                "main": os.path.join(assets_dir, MAIN_FONT_FILENAME),
                "hint": os.path.join(assets_dir, HINT_FONT_FILENAME),
            }
            sizes = {
                "title": TITLE_FONT_SIZE,
                "main": FONT_SIZE,
                "hint": HINT_FONT_SIZE,
            }
            fonts_loaded: dict[str, pygame.font.Font | None] = {
                "title": None,
                "main": None,
                "hint": None,
            }  # Explicitly type

            # Loop is bounded by the number of entries in paths (fixed at 3)
            for (
                name,
                path_str,
            ) in paths.items():  # Renamed path to path_str to avoid conflict
                assert isinstance(path_str, str), f"{name} font path is not a string"
                font_size = sizes[name]
                assert (
                    isinstance(font_size, int) and font_size > 0
                ), f"Invalid font size for {name}"
                try:
                    if not os.path.isfile(path_str):
                        logger.error(
                            f"{name.capitalize()} font file not found: '{path_str}'. Using Pygame SysFont fallback."
                        )
                        fonts_loaded[name] = pygame.font.SysFont(None, font_size)
                    else:
                        fonts_loaded[name] = pygame.font.Font(path_str, font_size)
                        logger.info(
                            f"Loaded {name} font: {path_str} (Size: {font_size})"
                        )

                    if (
                        fonts_loaded[name] is None
                    ):  # Should not happen with SysFont fallback, but good check
                        raise RuntimeError(
                            f"Font loading returned None for {name} even after SysFont fallback attempt."
                        )

                except (
                    pygame.error
                ) as e_pygame:  # Catch Pygame-specific font loading errors
                    logger.error(
                        f"Pygame error loading {name} font '{path_str}' (Size: {font_size}): {e_pygame}. Using SysFont fallback.",
                        exc_info=True,
                    )
                    try:
                        fonts_loaded[name] = pygame.font.SysFont(None, font_size)
                        if fonts_loaded[name] is None:
                            raise RuntimeError("SysFont fallback also returned None.")
                    except Exception as e_sysfont_fallback:
                        logger.critical(
                            f"CRITICAL: SysFont fallback also failed for {name} font: {e_sysfont_fallback}"
                        )
                        fonts_loaded[name] = None  # Ensure it's None
                except RuntimeError as e_rt:  # Catch our explicit RuntimeError
                    logger.error(f"Runtime error for {name} font: {e_rt}")
                    fonts_loaded[name] = None
                except Exception as e_general:  # Catch any other unexpected errors
                    logger.error(
                        f"Unexpected error loading {name} font '{path_str}': {e_general}. Using SysFont fallback.",
                        exc_info=True,
                    )
                    try:
                        fonts_loaded[name] = pygame.font.SysFont(None, font_size)
                        if fonts_loaded[name] is None:
                            raise RuntimeError(
                                "SysFont fallback also returned None after general error."
                            )
                    except Exception as e_sysfont_fallback_gen:
                        logger.critical(
                            f"CRITICAL: SysFont fallback also failed for {name} font after general error: {e_sysfont_fallback_gen}"
                        )
                        fonts_loaded[name] = None

            self.title_font = fonts_loaded["title"]
            self.font = fonts_loaded["main"]
            self.hint_font = fonts_loaded["hint"]

            if not self.font:  # Critical check for the main font
                logger.critical(
                    "Essential main font (self.font) failed to load, even with fallbacks. Application may not display correctly."
                )
            # Assertions on final types (can be None if loading failed critically)
            assert isinstance(
                self.title_font, (pygame.font.Font, type(None))
            ), "Title font has invalid type post-load"
            assert isinstance(
                self.font, (pygame.font.Font, type(None))
            ), "Main font has invalid type post-load"
            assert isinstance(
                self.hint_font, (pygame.font.Font, type(None))
            ), "Hint font has invalid type post-load"

        except AssertionError as ae:  # Catch assertion errors within this function
            logger.critical(f"AssertionError during font loading: {ae}", exc_info=True)
            self.font = self.title_font = self.hint_font = (
                None  # Ensure all are None on failure
            )
        except (
            Exception
        ) as e:  # Catch any other top-level errors during font init/setup
            logger.critical(
                f"Critical error during Pygame font initialization/loading setup: {e}",
                exc_info=True,
            )
            self.font = self.title_font = self.hint_font = None

    def _calculate_value_offset(self):
        assert self.font is not None, "Cannot calculate value offset without main font."
        try:
            max_w = 0
            prefixes = {
                self.MENU_ITEM_INTEGRATION: "INTEGRATION:",
                self.MENU_ITEM_COLLECTION_MODE: "MODE:",
                self.MENU_ITEM_LENS_TYPE: "LENS TYPE:",
                self.MENU_ITEM_DATE: "DATE:",
                self.MENU_ITEM_TIME: "TIME:",
                self.MENU_ITEM_TIME: "TIME:",
                self.MENU_ITEM_TEMPERATURE: "TEMP:",
                self.MENU_ITEM_WIFI: "WIFI:",
                self.MENU_ITEM_IP: "IP:",
            }
            for item, _ in self._menu_items:
                if p := prefixes.get(item):
                    max_w = max(max_w, self.font.size(p)[0])
            self._value_start_offset_x = int(max_w + 8)
            logger.info(
                f"Calculated value start offset X: {self._value_start_offset_x} (max label width {max_w})"
            )
        except Exception as e:
            logger.error(
                f"Failed to calculate value offset: {e}. Using fallback {self._value_start_offset_x}."
            )

    def _get_current_app_display_time(self) -> datetime.datetime:
        assert isinstance(self._time_offset, datetime.timedelta)
        try:
            return datetime.datetime.now() + self._time_offset
        except OverflowError:
            logger.warning("Time offset overflow. Resetting.")
            self._time_offset = datetime.timedelta(0)
            return datetime.datetime.now()

    def get_integration_time_ms(self) -> int:
        assert isinstance(self._integration_time_ms, int)
        return self._integration_time_ms

    def get_timestamp_datetime(self) -> datetime.datetime:
        return self._get_current_app_display_time()

    def get_collection_mode(self) -> str:
        assert self._collection_mode in self.COLLECTION_MODES
        return self._collection_mode

    def get_lens_type(self) -> str:
        assert self._lens_type in self.LENS_TYPES
        return self._lens_type

    # --- New Method ---
    def set_integration_time_ms(self, new_time_ms: int):
        """
        Sets the integration time. Called by SpectrometerScreen after auto-integration.
        Clamps value to defined min/max and aligns to step.
        """
        assert isinstance(
            new_time_ms, int
        ), f"New integration time must be int, got {type(new_time_ms)}"
        logger.info(
            f"MenuSystem: Attempting to set integration time to {new_time_ms} ms."
        )

        clamped_time_ms = max(
            MIN_INTEGRATION_TIME_MS, min(new_time_ms, MAX_INTEGRATION_TIME_MS)
        )
        if clamped_time_ms != new_time_ms:
            logger.warning(
                f"MenuSystem: Requested integration time {new_time_ms} ms was clamped to {clamped_time_ms} ms."
            )

        # Align to menu step size
        aligned_time_ms = (
            round(clamped_time_ms / INTEGRATION_TIME_STEP_MS) * INTEGRATION_TIME_STEP_MS
        )
        if aligned_time_ms != clamped_time_ms:
            logger.info(
                f"MenuSystem: Clamped time {clamped_time_ms} ms aligned to step size, resulting in {aligned_time_ms} ms."
            )

        self._integration_time_ms = int(aligned_time_ms)
        logger.info(
            f"MenuSystem: Integration time successfully set to {self._integration_time_ms} ms."
        )

    def handle_input(self) -> str | None:
        if (pg_evt_res := self.button_handler.process_pygame_events()) == "QUIT":
            return "QUIT"
        action = (
            self._handle_editing_input()
            if self._is_editing
            else self._handle_navigation_input()
        )
        if action == "EXIT_EDIT_SAVE":
            self._is_editing, self._editing_field = False, None
            if self._datetime_being_edited:
                self._commit_time_offset_changes()
            self._datetime_being_edited, self._original_offset_on_edit_start = (
                None,
                None,
            )
            logger.info("Exited editing mode, changes saved.")
            return None
        elif action == "EXIT_EDIT_DISCARD":
            self._is_editing, self._editing_field = False, None
            if self._original_offset_on_edit_start:
                self._time_offset = self._original_offset_on_edit_start
                logger.info("Exited editing, time offset changes discarded.")
            self._datetime_being_edited, self._original_offset_on_edit_start = (
                None,
                None,
            )
            logger.info("Exited editing mode (Discard).")
            return None
        elif action == "START_CAPTURE":
            logger.info("Capture action triggered.")
            return "START_CAPTURE"
        return None

    def draw(self):
        assert (
            self.font and self.title_font and self.hint_font and self.screen
        ), "Drawing dependencies missing."
        try:
            self.screen.fill(BLACK)
            self._draw_title()
            self._draw_menu_items()
            self._draw_hints()
            update_hardware_display(self.screen, self.display_hat)
        except Exception as e:
            logger.error(f"Error during menu drawing: {e}", exc_info=True)

    def cleanup(self):
        logger.info("MenuSystem cleanup completed.")
        pass

    def _handle_navigation_input(self) -> str | None:
        assert not self._is_editing
        action = None
        if self.button_handler.check_button(BTN_UP):
            self._navigate_menu(-1)
        elif self.button_handler.check_button(BTN_DOWN):
            self._navigate_menu(1)
        elif self.button_handler.check_button(BTN_ENTER):
            action = self._select_menu_item()
        elif self.button_handler.check_button(BTN_BACK):
            logger.info("BACK pressed in main menu.")
        return action

    def _handle_editing_input(self) -> str | None:
        assert self._is_editing
        item_text, edit_type = self._menu_items[self._current_selection_idx]
        action = None
        if self.button_handler.check_button(BTN_UP):
            self._handle_edit_adjust(edit_type, 1)
        elif self.button_handler.check_button(BTN_DOWN):
            self._handle_edit_adjust(edit_type, -1)
        elif self.button_handler.check_button(BTN_ENTER):
            action = self._handle_edit_next_field(edit_type)
        elif self.button_handler.check_button(BTN_BACK):
            action = "EXIT_EDIT_DISCARD"
        return action

    def _navigate_menu(self, direction: int):
        assert direction in [-1, 1]
        num_items = len(self._menu_items)
        assert num_items > 0
        self._current_selection_idx = (
            self._current_selection_idx + direction
        ) % num_items
        logger.debug(
            f"Menu navigated. New selection: {self._menu_items[self._current_selection_idx][0]}"
        )

    def _select_menu_item(self) -> str | None:
        item_text, edit_type = self._menu_items[self._current_selection_idx]
        logger.info(f"Menu item selected: {item_text}")
        if item_text == self.MENU_ITEM_CAPTURE:
            if USE_SPECTROMETER:
                return "START_CAPTURE"
            else:
                logger.warning("Capture selected, but USE_SPECTROMETER is False.")
                return None
        elif edit_type in [
            self.EDIT_TYPE_INTEGRATION,
            self.EDIT_TYPE_COLLECTION_MODE,
            self.EDIT_TYPE_LENS_TYPE,
            self.EDIT_TYPE_DATE,
            self.EDIT_TYPE_TIME,
        ]:
            self._is_editing = True
            if edit_type in [self.EDIT_TYPE_DATE, self.EDIT_TYPE_TIME]:
                self._original_offset_on_edit_start = self._time_offset
                self._datetime_being_edited = self._get_current_app_display_time()
            else:
                self._original_offset_on_edit_start = self._datetime_being_edited = None
            field_map = {
                self.EDIT_TYPE_DATE: self.FIELD_YEAR,
                self.EDIT_TYPE_TIME: self.FIELD_HOUR,
            }
            self._editing_field = field_map.get(edit_type)
            logger.info(
                f"Entering edit mode for: {item_text}"
                + (f" (Field: {self._editing_field})" if self._editing_field else "")
            )
            return None
        return None

    def _handle_edit_adjust(self, edit_type: int, delta: int):
        assert self._is_editing and delta in [-1, 1]
        if edit_type == self.EDIT_TYPE_INTEGRATION:
            new_val = self._integration_time_ms + delta * INTEGRATION_TIME_STEP_MS
            self._integration_time_ms = max(
                MIN_INTEGRATION_TIME_MS, min(new_val, MAX_INTEGRATION_TIME_MS)
            )
            logger.debug(f"Integration time adjusted to {self._integration_time_ms} ms")
        elif edit_type == self.EDIT_TYPE_COLLECTION_MODE:
            self._collection_mode_idx = (self._collection_mode_idx + delta) % len(
                self.COLLECTION_MODES
            )
            self._collection_mode = self.COLLECTION_MODES[self._collection_mode_idx]
            logger.debug(f"Collection mode changed to: {self._collection_mode}")
        elif edit_type == self.EDIT_TYPE_LENS_TYPE:
            self._lens_type_idx = (self._lens_type_idx + delta) % len(self.LENS_TYPES)
            self._lens_type = self.LENS_TYPES[self._lens_type_idx]
            logger.debug(f"Lens type changed to: {self._lens_type}")
        elif edit_type == self.EDIT_TYPE_DATE:
            assert self._datetime_being_edited
            self._change_date_field(delta)
        elif edit_type == self.EDIT_TYPE_TIME:
            assert self._datetime_being_edited
            self._change_time_field(delta)

    def _handle_edit_next_field(self, edit_type: int) -> str | None:
        assert self._is_editing
        if edit_type in [
            self.EDIT_TYPE_INTEGRATION,
            self.EDIT_TYPE_COLLECTION_MODE,
            self.EDIT_TYPE_LENS_TYPE,
        ]:
            return "EXIT_EDIT_SAVE"
        elif edit_type == self.EDIT_TYPE_DATE:
            assert self._editing_field in [
                self.FIELD_YEAR,
                self.FIELD_MONTH,
                self.FIELD_DAY,
            ]
            if self._editing_field == self.FIELD_YEAR:
                self._editing_field = self.FIELD_MONTH
            elif self._editing_field == self.FIELD_MONTH:
                self._editing_field = self.FIELD_DAY
            elif self._editing_field == self.FIELD_DAY:
                return "EXIT_EDIT_SAVE"
        elif edit_type == self.EDIT_TYPE_TIME:
            assert self._editing_field in [self.FIELD_HOUR, self.FIELD_MINUTE]
            if self._editing_field == self.FIELD_HOUR:
                self._editing_field = self.FIELD_MINUTE
            elif self._editing_field == self.FIELD_MINUTE:
                return "EXIT_EDIT_SAVE"
        return None

    def _change_date_field(self, delta: int):
        assert (
            self._datetime_being_edited
            and self._editing_field
            in [self.FIELD_YEAR, self.FIELD_MONTH, self.FIELD_DAY]
            and delta in [-1, 1]
        )
        dt, y, m, d = (
            self._datetime_being_edited,
            *self._datetime_being_edited.timetuple()[:3],
        )
        if self._editing_field == self.FIELD_YEAR:
            y = max(1970, min(2100, y + delta))
        elif self._editing_field == self.FIELD_MONTH:
            m = (m - 1 + delta + 12) % 12 + 1
        elif self._editing_field == self.FIELD_DAY:
            import calendar

            max_d = calendar.monthrange(y, m)[1]
            d = (d - 1 + delta + max_d) % max_d + 1
        if new_dt := get_safe_datetime(y, m, d, dt.hour, dt.minute, dt.second):
            self._datetime_being_edited = new_dt
            logger.debug(
                f"Date field '{self._editing_field}' changed. New temp date: {new_dt:%Y-%m-%d}"
            )
        else:
            logger.warning("Date field change resulted in invalid date.")

    def _change_time_field(self, delta: int):
        assert (
            self._datetime_being_edited
            and self._editing_field in [self.FIELD_HOUR, self.FIELD_MINUTE]
            and delta in [-1, 1]
        )
        td = datetime.timedelta(
            hours=delta if self._editing_field == self.FIELD_HOUR else 0,
            minutes=delta if self._editing_field == self.FIELD_MINUTE else 0,
        )
        try:
            self._datetime_being_edited += td
            logger.debug(
                f"Time field '{self._editing_field}' changed. New temp time: {self._datetime_being_edited:%H:%M}"
            )
        except OverflowError:
            logger.warning("Time field change overflowed.")

    def _commit_time_offset_changes(self):
        assert self._datetime_being_edited
        try:
            self._time_offset = self._datetime_being_edited - datetime.datetime.now()
            logger.info(
                f"Time offset updated. Final: {self._datetime_being_edited:%Y-%m-%d %H:%M:%S}, Offset: {self._time_offset}"
            )
        except Exception as e:
            logger.error(f"Error committing time offset: {e}", exc_info=True)

    def _draw_title(self):
        assert self.title_font
        surf = self.title_font.render("OPEN SPECTRO MENU", True, YELLOW)
        self.screen.blit(surf, surf.get_rect(centerx=SCREEN_WIDTH // 2, top=10))

    def _draw_menu_items(self):
        assert self.font
        y = MENU_MARGIN_TOP
        dt_disp = self._get_current_app_display_time()
        for i, (item, edit_type) in enumerate(self._menu_items):
            try:
                sel, edit = (i == self._current_selection_idx), (
                    i == self._current_selection_idx and self._is_editing
                )
                dt_fmt = (
                    self._datetime_being_edited
                    if edit
                    and edit_type in [self.EDIT_TYPE_DATE, self.EDIT_TYPE_TIME]
                    and self._datetime_being_edited
                    else dt_disp
                )
                lbl, val = item, ""
                if item == self.MENU_ITEM_INTEGRATION:
                    lbl, val = "INTEGRATION:", f"{self._integration_time_ms} ms"
                elif item == self.MENU_ITEM_COLLECTION_MODE:
                    lbl, val = "MODE:", self._collection_mode
                elif item == self.MENU_ITEM_LENS_TYPE:
                    lbl, val = "LENS TYPE:", self._lens_type
                elif item == self.MENU_ITEM_DATE:
                    lbl, val = "DATE:", f"{dt_fmt:%Y-%m-%d}"
                elif item == self.MENU_ITEM_TIME:
                    lbl, val = "TIME:", f"{dt_fmt:%H:%M}"
                elif item == self.MENU_ITEM_TEMPERATURE:
                    lbl, val_temp = "TEMP:", "N/A"
                    if self.temp_sensor_info:
                        temp_reading = self.temp_sensor_info.get_temperature_c()
                        if isinstance(temp_reading, float):
                            val_temp = f"{temp_reading:.1f} C"
                        elif isinstance(temp_reading, str):
                            val_temp = temp_reading
                    val = val_temp
                elif item == self.MENU_ITEM_WIFI:
                    lbl, val = "WIFI:", self.network_info.get_wifi_name()
                elif item == self.MENU_ITEM_IP:
                    lbl, val = "IP:", self.network_info.get_ip_address()

                color = (
                    YELLOW
                    if sel
                    else (
                        GRAY
                        if item in [self.MENU_ITEM_WIFI, self.MENU_ITEM_IP]
                        and ("Not Connected" in val or "Error" in val or "No IP" in val)
                        else WHITE
                    )
                )
                self.screen.blit(
                    self.font.render(lbl, True, color), (MENU_MARGIN_LEFT, y)
                )
                if val:
                    self.screen.blit(
                        self.font.render(val, True, color),
                        (MENU_MARGIN_LEFT + self._value_start_offset_x, y),
                    )
                if edit and edit_type in [
                    self.EDIT_TYPE_INTEGRATION,
                    self.EDIT_TYPE_COLLECTION_MODE,
                    self.EDIT_TYPE_LENS_TYPE,
                    self.EDIT_TYPE_DATE,
                    self.EDIT_TYPE_TIME,
                ]:
                    self._draw_editing_highlight(y, edit_type, lbl, val)
            except Exception as e:
                logger.error(f"Error rendering menu item '{item}': {e}", exc_info=True)
            y += MENU_SPACING

    def _draw_editing_highlight(
        self, y_pos: int, edit_type: int, label_str: str, value_str: str
    ):
        assert self.font
        val_start_x = MENU_MARGIN_LEFT + self._value_start_offset_x
        rect = None
        try:
            f_str, off_str = "", ""
            if edit_type == self.EDIT_TYPE_INTEGRATION:
                f_str = str(self._integration_time_ms)
            elif edit_type == self.EDIT_TYPE_COLLECTION_MODE:
                f_str = self._collection_mode
            elif edit_type == self.EDIT_TYPE_LENS_TYPE:
                f_str = self._lens_type
            elif edit_type == self.EDIT_TYPE_DATE:
                assert self._datetime_being_edited and self._editing_field
                fmt_d = self._datetime_being_edited.strftime("%Y-%m-%d")
                if self._editing_field == self.FIELD_YEAR:
                    f_str, off_str = fmt_d[0:4], ""
                elif self._editing_field == self.FIELD_MONTH:
                    f_str, off_str = fmt_d[5:7], fmt_d[0:5]
                elif self._editing_field == self.FIELD_DAY:
                    f_str, off_str = fmt_d[8:10], fmt_d[0:8]
            elif edit_type == self.EDIT_TYPE_TIME:
                assert self._datetime_being_edited and self._editing_field
                fmt_t = self._datetime_being_edited.strftime("%H:%M")
                if self._editing_field == self.FIELD_HOUR:
                    f_str, off_str = fmt_t[0:2], ""
                elif self._editing_field == self.FIELD_MINUTE:
                    f_str, off_str = fmt_t[3:5], fmt_t[0:3]
            if f_str:
                f_w, off_w = self.font.size(f_str)[0], self.font.size(off_str)[0]
                pad = 1
                rect = pygame.Rect(
                    val_start_x + off_w - pad,
                    y_pos - pad,
                    f_w + 2 * pad,
                    FONT_SIZE + 2 * pad,
                )
        except Exception as e:
            logger.error(f"Error calculating highlight: {e}", exc_info=True)
            return
        if rect:
            pygame.draw.rect(self.screen, BLUE, rect, 1)

    def _draw_hints(self):
        assert self.hint_font
        hint = (
            "X/Y: Adjust | A: Next/Save | B: Cancel"
            if self._is_editing
            else "X/Y: Navigate | A: Select/Edit | B: Back"
        )
        surf = self.hint_font.render(hint, True, YELLOW)
        self.screen.blit(
            surf, surf.get_rect(left=MENU_MARGIN_LEFT, bottom=SCREEN_HEIGHT - 10)
        )


class SpectrometerScreen:
    """
    Handles the spectrometer live view, capture, saving, and state management.
    Calibration (Dark/White/Auto-Integration) follows a setup-run-confirm/save model.
    """

    # --- Internal State Flags ---
    STATE_LIVE_VIEW = "live_view"
    STATE_CALIBRATE = "calibrate_menu"
    STATE_DARK_CAPTURE_SETUP = "dark_setup"
    STATE_WHITE_CAPTURE_SETUP = "white_setup"
    STATE_FROZEN_VIEW = "frozen_view"
    STATE_AUTO_INTEG_SETUP = "auto_integ_setup"
    STATE_AUTO_INTEG_RUNNING = "auto_integ_running"
    STATE_AUTO_INTEG_CONFIRM = "auto_integ_confirm"

    # --- Constants for Frozen Capture Types ---
    FROZEN_TYPE_OOI = "OOI"
    FROZEN_TYPE_DARK = "DARK"
    FROZEN_TYPE_WHITE = "WHITE"
    FROZEN_TYPE_AUTO_INTEG_RESULT = "AUTO_INTEG_RESULT"

    def __init__(
        self,
        screen: pygame.Surface,
        button_handler: ButtonHandler,
        menu_system: MenuSystem,
        display_hat_obj,
        temp_sensor_info: TempSensorInfo,
    ):
        assert (
            screen and button_handler and menu_system and temp_sensor_info
        ), "SpectrometerScreen dependencies missing"
        self.screen = screen
        self.button_handler = button_handler
        self.menu_system = menu_system
        self.display_hat = display_hat_obj
        self.temp_sensor_info = temp_sensor_info

        self.spectrometer: Spectrometer | None = None
        self.wavelengths: np.ndarray | None = None

        self._hw_min_integration_us: int = SPECTROMETER_INTEGRATION_TIME_MIN_US
        self._hw_max_integration_us: int = SPECTROMETER_INTEGRATION_TIME_MAX_US
        self._hw_max_intensity_adc: int = SPECTROMETER_MAX_ADC_COUNT
        self._hw_integration_time_increment_us: int = (
            SPECTROMETER_INTEGRATION_TIME_BASE_US
        )

        self._auto_integ_target_low_counts: float = self._hw_max_intensity_adc * (
            AUTO_INTEG_TARGET_LOW_PERCENT / 100.0
        )
        self._auto_integ_target_high_counts: float = self._hw_max_intensity_adc * (
            AUTO_INTEG_TARGET_HIGH_PERCENT / 100.0
        )

        if USE_SPECTROMETER:  # Only initialise if config flag is True
            self._initialize_spectrometer_device()
        else:
            logger.info(
                "SpectrometerScreen: USE_SPECTROMETER is False, skipping device initialization."
            )

        self.plot_fig, self.plot_ax, self.plot_line = None, None, None
        if (
            USE_SPECTROMETER
        ):  # Plotting is only relevant if spectrometer is intended to be used
            self._initialize_plot()

        self.overlay_font: pygame.font.Font | None = None
        self._load_overlay_font()  # Font is always needed for status messages

        self.is_active = False
        self._current_state = self.STATE_LIVE_VIEW
        self._last_integration_time_ms = 0

        self._frozen_intensities: np.ndarray | None = None
        self._frozen_wavelengths: np.ndarray | None = None
        self._frozen_timestamp: datetime.datetime | None = None
        self._frozen_integration_ms: int | None = None
        self._frozen_capture_type: str | None = None
        self._frozen_sample_collection_mode: str | None = None

        self._current_y_max: float = float(Y_AXIS_DEFAULT_MAX)
        self._scans_today_count: int = 0

        self._auto_integ_optimizing: bool = False
        self._current_auto_integ_us: int = 0
        self._pending_auto_integ_ms: int | None = None
        self._auto_integ_iteration_count: int = 0
        self._auto_integ_status_msg: str = ""
        self._last_peak_adc_value: float = 0.0
        self._previous_integ_adjustment_direction: int = 0

        self._dark_reference_intensities: np.ndarray | None = None
        self._dark_reference_integration_ms: int | None = None
        self._white_reference_intensities: np.ndarray | None = None
        self._white_reference_integration_ms: int | None = None
        self._raw_target_intensities_for_reflectance: np.ndarray | None = None

        # DATA_DIR creation for scan counts needs to happen even if spec is not ready,
        # as _scans_today_count is initialized based on existing files.
        # This is okay, as it doesn't imply saving new spectra yet.
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
        except OSError as e:
            logger.error(f"Could not create base data directory {DATA_DIR}: {e}")
        except Exception as e_mkdir:
            logger.error(f"Unexpected error creating data dir {DATA_DIR}: {e_mkdir}")

    def _initialize_spectrometer_device(self):
        # This method is only called if USE_SPECTROMETER is True
        logger.info("SpectrometerScreen: Initializing spectrometer device...")
        if sb is None or Spectrometer is None:  # Check if libraries loaded
            logger.error(
                "SpectrometerScreen: Seabreeze libraries not loaded. Cannot initialize device."
            )
            self.spectrometer = None
            return
        try:
            devices = sb.list_devices()
            if not devices:
                logger.error("SpectrometerScreen: No spectrometer devices found.")
                self.spectrometer = None
                return

            # Attempt to connect to the first device
            self.spectrometer = Spectrometer.from_serial_number(
                devices[0].serial_number
            )
            if not self.spectrometer or not hasattr(self.spectrometer, "_dev"):
                logger.error(
                    "SpectrometerScreen: Failed to create Spectrometer instance or missing backend (_dev)."
                )
                self.spectrometer = None
                return

            self.wavelengths = self.spectrometer.wavelengths()
            if self.wavelengths is None or len(self.wavelengths) == 0:
                logger.error(
                    "SpectrometerScreen: Failed to get wavelengths from device."
                )
                self.spectrometer.close()  # Close if wavelengths fail
                self.spectrometer = None
                return

            assert isinstance(self.spectrometer, Spectrometer)
            assert (
                isinstance(self.wavelengths, np.ndarray) and self.wavelengths.size > 0
            )

            logger.info(
                f"Spectrometer device: {devices[0]}, Model: {self.spectrometer.model}, Serial: {self.spectrometer.serial_number}"
            )
            logger.info(
                f"  Wavelengths: {self.wavelengths[0]:.1f} to {self.wavelengths[-1]:.1f} nm ({len(self.wavelengths)} points)"
            )

            # Query and update hardware limits
            try:
                min_us, max_us = self.spectrometer.integration_time_micros_limits
                self._hw_min_integration_us = int(min_us)
                self._hw_max_integration_us = int(max_us)
                logger.info(
                    f"  Device reported integration limits: {self._hw_min_integration_us} µs - {self._hw_max_integration_us} µs."
                )
            except (AttributeError, TypeError, ValueError) as e_limits:
                logger.warning(
                    f"  Could not query device integration limits ({e_limits}). Using configured defaults: {self._hw_min_integration_us} µs - {self._hw_max_integration_us} µs."
                )

            try:  # Try to get max intensity (ADC max)
                # Seabreeze API doesn't have a standard way for max_intensity for all devices.
                # pyseabreeze specific backends might store it, e.g., `self.spectrometer._dev.max_intensity`
                # For now, we rely on the configured SPECTROMETER_MAX_ADC_COUNT
                # If a device-specific way is found, it can be used to update self._hw_max_intensity_adc here.
                # Example: if hasattr(self.spectrometer._dev, 'max_intensity'): self._hw_max_intensity_adc = self.spectrometer._dev.max_intensity
                logger.info(
                    f"  Using configured max ADC count: {self._hw_max_intensity_adc}."
                )
            except Exception as e_max_adc:
                logger.warning(
                    f"  Could not determine device max ADC count ({e_max_adc}). Using configured: {self._hw_max_intensity_adc}"
                )

            # Recalculate auto-integration target counts based on potentially updated _hw_max_intensity_adc
            self._auto_integ_target_low_counts = float(
                self._hw_max_intensity_adc * (AUTO_INTEG_TARGET_LOW_PERCENT / 100.0)
            )
            self._auto_integ_target_high_counts = float(
                self._hw_max_intensity_adc * (AUTO_INTEG_TARGET_HIGH_PERCENT / 100.0)
            )
            logger.info(
                f"  Auto-integration target ADC range: {self._auto_integ_target_low_counts:.0f} - {self._auto_integ_target_high_counts:.0f}"
            )

            # Check for integration time increment (step size)
            # This is not a standard Seabreeze API feature. Some devices might have it.
            # For now, using configured SPECTROMETER_INTEGRATION_TIME_BASE_US for _hw_integration_time_increment_us
            logger.info(
                f"  Using configured integration time base/increment: {self._hw_integration_time_increment_us} µs."
            )

        except sb.SeaBreezeError as e_sb:
            logger.error(f"SeaBreezeError initializing device: {e_sb}", exc_info=True)
            self.spectrometer = None
        except Exception as e:
            logger.error(f"Unexpected error initializing device: {e}", exc_info=True)
            self.spectrometer = None

    def _is_spectrometer_ready(self) -> bool:
        """Helper to check if the spectrometer hardware is truly ready for operations."""
        if not USE_SPECTROMETER:  # Global config check first
            return False
        if self.spectrometer is None:
            return False
        dev_proxy = getattr(self.spectrometer, "_dev", None)
        if dev_proxy is None or not hasattr(dev_proxy, "is_open"):
            return False
        # Wavelengths must also be loaded for most operations
        return (
            dev_proxy.is_open
            and self.wavelengths is not None
            and self.wavelengths.size > 0
        )

    def _initialize_plot(self):
        if plt is None:
            logger.error("Matplotlib unavailable. Cannot init plot.")
            return
        logger.debug("Initializing Matplotlib plot for SpectrometerScreen...")
        try:
            plot_w_px, plot_h_px = SCREEN_WIDTH, SCREEN_HEIGHT - 45
            dpi = float(self.screen.get_width() / 3.33) if self.screen else 96.0
            fig_w_in, fig_h_in = plot_w_px / dpi, plot_h_px / dpi
            assert fig_w_in > 0 and fig_h_in > 0
            self.plot_fig, self.plot_ax = plt.subplots(
                figsize=(fig_w_in, fig_h_in), dpi=dpi
            )
            if not self.plot_fig or not self.plot_ax:
                raise RuntimeError("plt.subplots failed.")
            (self.plot_line,) = self.plot_ax.plot([], [], linewidth=1.0, color="cyan")
            if not self.plot_line:
                raise RuntimeError("plot_ax.plot failed.")
            self.plot_ax.grid(True, linestyle=":", alpha=0.6, color="gray")
            self.plot_ax.tick_params(
                axis="both", which="major", labelsize=8, colors="white"
            )
            self.plot_ax.set_xlabel("Wavelength (nm)", fontsize=9, color="white")
            self.plot_ax.set_ylabel("Intensity", fontsize=9, color="white")
            self.plot_fig.patch.set_facecolor("black")
            self.plot_ax.set_facecolor("black")
            for spine in ["top", "bottom", "left", "right"]:
                self.plot_ax.spines[spine].set_color("gray")
            self.plot_fig.tight_layout(pad=0.3)
            logger.debug("Matplotlib plot initialized successfully.")
        except Exception as e:
            logger.error(f"Failed to initialize Matplotlib plot: {e}", exc_info=True)
            if self.plot_fig and plt and plt.fignum_exists(self.plot_fig.number):
                plt.close(self.plot_fig)
            self.plot_fig = self.plot_ax = self.plot_line = None

    def _load_overlay_font(self):
        if not pygame.font.get_init():
            pygame.font.init()
            logger.info("Pygame font module initialized for overlay_font.")
        assert pygame.font.get_init(), "Pygame font module failed to initialize."
        try:
            font_path = os.path.join(
                os.path.dirname(os.path.abspath(__file__)),
                "assets",
                SPECTRO_FONT_FILENAME,
            )
            if not os.path.isfile(font_path):
                logger.warning(
                    f"Overlay font file not found: '{font_path}'. Using SysFont."
                )
                self.overlay_font = pygame.font.SysFont(None, SPECTRO_FONT_SIZE)
            else:
                self.overlay_font = pygame.font.Font(font_path, SPECTRO_FONT_SIZE)
            if not self.overlay_font:
                raise RuntimeError("Font loading returned None.")
            logger.info(
                f"Loaded overlay font: {SPECTRO_FONT_FILENAME} (Size: {SPECTRO_FONT_SIZE})"
            )
        except Exception as e:
            logger.error(f"Error loading overlay font: {e}", exc_info=True)
            self.overlay_font = (
                pygame.font.SysFont(None, SPECTRO_FONT_SIZE)
                if pygame.font.get_init()
                else None
            )

    def _clear_frozen_data(self):
        self._frozen_intensities = self._frozen_wavelengths = self._frozen_timestamp = (
            None
        )
        self._frozen_integration_ms = self._frozen_capture_type = (
            self._frozen_sample_collection_mode
        ) = None
        logger.debug("Cleared all frozen spectrum data.")

    def _cancel_auto_integration(self):
        """Resets all auto-integration related state variables."""
        logger.debug("Cancelling and resetting auto-integration variables.")
        self._auto_integ_optimizing = False
        self._current_auto_integ_us = 0
        self._pending_auto_integ_ms = None
        self._auto_integ_iteration_count = 0
        self._auto_integ_status_msg = ""
        self._last_peak_adc_value = 0.0
        self._previous_integ_adjustment_direction = 0
        # Clear any spectrum data that might have been stored for confirm state display
        if self._frozen_capture_type == self.FROZEN_TYPE_AUTO_INTEG_RESULT:
            self._clear_frozen_data()

    def _are_references_valid_for_reflectance(
        self,
    ) -> tuple[bool, str]:  # ADDED THIS MISSING METHOD
        """Checks if stored Dark and White references are suitable for calculating live reflectance."""
        assert (
            self.menu_system is not None
        ), "MenuSystem not available for reference validation"
        # Wavelengths check is implicitly handled by checking array lengths against self.wavelengths if needed
        # For now, we assume self.wavelengths is valid if spectrometer is initialized.

        current_integ_ms = self.menu_system.get_integration_time_ms()
        # Default hint part, specific messages will be built by _draw_overlays
        # This method just signals validity and a general reason type.

        dark_ok = False
        if (
            self._dark_reference_intensities is not None
            and self.wavelengths is not None
            and len(self._dark_reference_intensities) == len(self.wavelengths)
            and self._dark_reference_integration_ms is not None
        ):
            dark_ok = True

        white_ok = False
        if (
            self._white_reference_intensities is not None
            and self.wavelengths is not None
            and len(self._white_reference_intensities) == len(self.wavelengths)
            and self._white_reference_integration_ms is not None
        ):
            white_ok = True

        if not dark_ok and not white_ok:
            return False, "No Dark/White refs"
        if not dark_ok:
            return False, "No Dark ref"
        if not white_ok:
            return False, "No White ref"

        # Both references exist, now check integration times
        dark_integ_ok = self._dark_reference_integration_ms == current_integ_ms
        white_integ_ok = self._white_reference_integration_ms == current_integ_ms

        if not dark_integ_ok and not white_integ_ok:
            return False, "Integ mismatch D&W"
        if not dark_integ_ok:
            return False, "Integ mismatch Dark"
        if not white_integ_ok:
            return False, "Integ mismatch White"

        return True, ""  # All good

    def activate(self):
        logger.info("Activating Spectrometer Screen.")
        self.is_active = True
        self._current_state = self.STATE_LIVE_VIEW
        self._clear_frozen_data()
        self._cancel_auto_integration()

        current_menu_mode = self.menu_system.get_collection_mode()
        if (
            self._current_state == self.STATE_LIVE_VIEW
            and current_menu_mode == MODE_REFLECTANCE
        ):
            self._current_y_max = float(
                Y_AXIS_REFLECTANCE_DEFAULT_MAX
            )  # Default for reflectance
        else:
            self._current_y_max = float(Y_AXIS_DEFAULT_MAX)
        logger.debug(
            f"Activate: Y-axis max set to: {self._current_y_max} for state {self._current_state}, mode {current_menu_mode}"
        )

        # Initialize scans_today_count (this part is fine as it reads existing logs)
        # It does not attempt to save anything yet.
        try:
            dt_now = self.menu_system.get_timestamp_datetime()
            date_str = dt_now.strftime("%Y-%m-%d")
            daily_data_dir = os.path.join(DATA_DIR, date_str)
            # No need to os.makedirs here in activate if _save_data handles it.
            # Or, if you want the folder created on entering the screen:
            # if self._is_spectrometer_ready(): # Only make dir if we might save
            #     try: os.makedirs(daily_data_dir, exist_ok=True)
            #     except OSError as e_dir: logger.error(f"Could not create data directory {daily_data_dir}: {e_dir}")

            csv_path = os.path.join(daily_data_dir, f"{date_str}_{CSV_BASE_FILENAME}")
            count = 0
            if os.path.isfile(csv_path):  # Check if file exists before trying to open
                try:
                    with open(csv_path, "r", newline="") as f:
                        reader = csv.reader(f)
                        next(reader, None)  # Skip header
                        for row in reader:
                            if len(row) > 1 and row[1] in [
                                SPECTRA_TYPE_RAW,
                                SPECTRA_TYPE_REFLECTANCE,
                            ]:
                                count += 1
                    logger.info(
                        f"Found {count} existing OOI scans in today's log: {csv_path}"
                    )
                except (
                    FileNotFoundError
                ):  # Should be caught by isfile, but good practice
                    logger.info(
                        f"Log file {csv_path} not found when counting scans (race condition?). Scan count 0."
                    )
                except StopIteration:  # Empty file
                    logger.info(f"Log file {csv_path} is empty or has no header.")
                except Exception as e_scan_read:
                    logger.error(
                        f"Error reading scan count from {csv_path}: {e_scan_read}",
                        exc_info=True,
                    )
            else:
                logger.info(f"No existing log for today at {csv_path}. Scan count 0.")
            self._scans_today_count = count
        except Exception as e_scan_init:
            logger.error(
                f"Error initializing scans_today_count: {e_scan_init}", exc_info=True
            )
            self._scans_today_count = 0
        logger.info(f"Scans today initialized to: {self._scans_today_count}")

        # Attempt to open and configure spectrometer if it was initialized
        if self.spectrometer:  # self.spectrometer is the hardware object from __init__
            try:
                dev_proxy = getattr(self.spectrometer, "_dev", None)
                if (
                    dev_proxy
                    and hasattr(dev_proxy, "is_open")
                    and not dev_proxy.is_open
                ):
                    logger.info(
                        f"Opening spectrometer connection: {self.spectrometer.serial_number}"
                    )
                    self.spectrometer.open()
                    logger.info("Spectrometer connection opened.")
                elif dev_proxy and hasattr(dev_proxy, "is_open") and dev_proxy.is_open:
                    logger.info("Spectrometer connection already open.")
                # else: # Spectrometer object exists but no _dev or is_open
                #     logger.warning("Spectrometer object present but seems malformed. Cannot open/configure.")
                #     return # Cannot proceed with device config

                # Sync integration time only if fully ready (includes being open)
                if self._is_spectrometer_ready():
                    current_menu_integ_ms = self.menu_system.get_integration_time_ms()
                    integ_us = int(current_menu_integ_ms * 1000)
                    integ_us_clamped = max(
                        self._hw_min_integration_us,
                        min(integ_us, self._hw_max_integration_us),
                    )
                    logger.debug(
                        f"ACTIVATE: Setting integration time to {integ_us_clamped} µs (target {current_menu_integ_ms} ms)"
                    )
                    self.spectrometer.integration_time_micros(integ_us_clamped)
                    self._last_integration_time_ms = current_menu_integ_ms
                    logger.info(
                        f"Initial/Synced integration time set to target: {current_menu_integ_ms} ms (actual: {integ_us_clamped / 1000.0} ms)."
                    )
                else:
                    logger.warning(
                        "Spectrometer not fully ready during activate, cannot set integration time on device."
                    )
            except Exception as e:
                logger.error(
                    f"Error during spectrometer activation/configuration: {e}",
                    exc_info=True,
                )
        else:
            logger.warning(
                "SpectrometerScreen.activate: No spectrometer hardware object (self.spectrometer is None). Operations will be limited."
            )

    def deactivate(self):
        logger.info("Deactivating Spectrometer Screen.")
        self.is_active = False
        self._clear_frozen_data()
        self._cancel_auto_integration()
        self._current_state = self.STATE_LIVE_VIEW

    def _start_auto_integration_setup(self):
        """Prepares for auto-integration process."""
        logger.info("Starting Auto-Integration Setup.")
        self._cancel_auto_integration()  # Reset all auto-integ variables
        assert (
            self.menu_system is not None
        ), "MenuSystem not available for auto-integ setup"
        current_menu_integ_ms = self.menu_system.get_integration_time_ms()
        self._current_auto_integ_us = int(current_menu_integ_ms * 1000)
        # Clamp initial test integration time to hardware limits
        self._current_auto_integ_us = max(
            self._hw_min_integration_us,
            min(self._current_auto_integ_us, self._hw_max_integration_us),
        )
        self._auto_integ_status_msg = "Aim at white ref, then Start"
        self._current_state = self.STATE_AUTO_INTEG_SETUP
        self._current_y_max = float(Y_AXIS_DEFAULT_MAX)  # Reset Y-axis for raw view
        logger.debug(
            f"Auto-integ setup: Initial test integ set to {self._current_auto_integ_us} µs."
        )

    def handle_input(self) -> str | None:
        assert self.button_handler is not None
        if (pg_evt_res := self.button_handler.process_pygame_events()) == "QUIT":
            return "QUIT"

        action_result: str | None = None
        # Use our robust check for spectrometer readiness
        spectrometer_can_operate = self._is_spectrometer_ready()

        state = self._current_state
        current_menu_mode = self.menu_system.get_collection_mode()

        if state == self.STATE_LIVE_VIEW:
            if self.button_handler.check_button(BTN_ENTER):  # A: Freeze Sample
                if spectrometer_can_operate:
                    if current_menu_mode == MODE_REFLECTANCE:
                        valid_refs, _ = self._are_references_valid_for_reflectance()
                        if valid_refs:
                            self._perform_freeze_capture(self.FROZEN_TYPE_OOI)
                        else:
                            logger.warning(
                                "Freeze Sample in REFLECTANCE mode ignored: References invalid."
                            )
                    else:  # MODE_RAW or other modes
                        self._perform_freeze_capture(self.FROZEN_TYPE_OOI)
                else:
                    logger.warning(
                        "Freeze Sample ignored: Spectrometer not ready or not configured."
                    )
            elif self.button_handler.check_button(BTN_UP):  # X: Calib Menu
                self._current_state = self.STATE_CALIBRATE
                self._current_y_max = float(Y_AXIS_DEFAULT_MAX)
                logger.debug(
                    f"Calib Entry: Y-axis set to {self._current_y_max} for raw display."
                )
            elif self.button_handler.check_button(BTN_DOWN):  # Y: Rescale
                if spectrometer_can_operate:
                    self._rescale_y_axis(relative=False)
                else:
                    logger.warning(
                        "Rescale ignored: Spectrometer not ready or not configured."
                    )
            elif self.button_handler.check_button(BTN_BACK):
                action_result = "BACK_TO_MENU"

        elif state == self.STATE_CALIBRATE:
            if self.button_handler.check_button(BTN_ENTER):  # A: White Setup
                self._current_state = self.STATE_WHITE_CAPTURE_SETUP
                self._current_y_max = float(Y_AXIS_DEFAULT_MAX)
                logger.debug(
                    f"Entering White Setup: Y-axis set to {self._current_y_max}"
                )
            elif self.button_handler.check_button(BTN_UP):  # X: Dark Setup
                self._current_state = self.STATE_DARK_CAPTURE_SETUP
                self._current_y_max = float(Y_AXIS_DEFAULT_MAX)
                logger.debug(
                    f"Entering Dark Setup: Y-axis set to {self._current_y_max}"
                )
            elif self.button_handler.check_button(BTN_DOWN):  # Y: Auto-Integ Setup
                if spectrometer_can_operate:
                    self._start_auto_integration_setup()
                else:
                    logger.warning(
                        "Auto-Integ Setup ignored: Spectrometer not ready or not configured."
                    )
            elif self.button_handler.check_button(BTN_BACK):
                self._current_state = self.STATE_LIVE_VIEW
                # Adjust Y-axis if returning to live reflectance mode and refs are valid
                if current_menu_mode == MODE_REFLECTANCE:
                    valid_refs, _ = self._are_references_valid_for_reflectance()
                    self._current_y_max = (
                        float(Y_AXIS_REFLECTANCE_DEFAULT_MAX)
                        if valid_refs
                        else float(Y_AXIS_DEFAULT_MAX)
                    )
                else:  # Raw mode
                    self._current_y_max = float(Y_AXIS_DEFAULT_MAX)

        elif state == self.STATE_DARK_CAPTURE_SETUP:
            if self.button_handler.check_button(BTN_ENTER):  # A: Freeze Dark
                if spectrometer_can_operate:
                    self._perform_freeze_capture(self.FROZEN_TYPE_DARK)
                else:
                    logger.warning(
                        "Freeze Dark ignored: Spectrometer not ready or not configured."
                    )
            elif self.button_handler.check_button(BTN_BACK):
                self._current_state = self.STATE_CALIBRATE

        elif state == self.STATE_WHITE_CAPTURE_SETUP:
            if self.button_handler.check_button(BTN_ENTER):  # A: Freeze White
                if spectrometer_can_operate:
                    self._perform_freeze_capture(self.FROZEN_TYPE_WHITE)
                else:
                    logger.warning(
                        "Freeze White ignored: Spectrometer not ready or not configured."
                    )
            elif self.button_handler.check_button(BTN_DOWN):  # Y: Rescale
                if spectrometer_can_operate:
                    self._rescale_y_axis(relative=False)
                else:
                    logger.warning(
                        "Rescale White Setup ignored: Spectrometer not ready or not configured."
                    )
            elif self.button_handler.check_button(BTN_BACK):
                self._current_state = self.STATE_CALIBRATE

        elif state == self.STATE_FROZEN_VIEW:
            # _frozen_capture_type is only set if a capture was successful
            assert self._frozen_capture_type is not None
            if self.button_handler.check_button(BTN_ENTER):  # A: Save
                self._perform_save_frozen_data()
            elif self.button_handler.check_button(BTN_BACK):  # B: Discard
                self._perform_discard_frozen_data()

        elif state == self.STATE_AUTO_INTEG_SETUP:
            if self.button_handler.check_button(BTN_ENTER):  # A: Start Auto-Integ
                if spectrometer_can_operate:
                    logger.info("Starting Auto-Integration RUNNING state.")
                    self._auto_integ_optimizing = True
                    self._auto_integ_iteration_count = 0
                    self._auto_integ_status_msg = "Running iteration 1..."
                    self._current_state = self.STATE_AUTO_INTEG_RUNNING
                else:
                    logger.warning(
                        "Start Auto-Integ ignored: Spectrometer not ready or not configured."
                    )
            elif self.button_handler.check_button(BTN_BACK):
                self._cancel_auto_integration()
                self._current_state = self.STATE_CALIBRATE

        # STATE_AUTO_INTEG_RUNNING and STATE_AUTO_INTEG_CONFIRM are only reachable if spectrometer_can_operate was true
        # So, their internal logic doesn't need extra checks for this.
        elif state == self.STATE_AUTO_INTEG_RUNNING:
            if self.button_handler.check_button(BTN_BACK):  # B: Cancel Auto
                logger.info("Auto-Integration cancelled by user during RUNNING state.")
                self._cancel_auto_integration()
                self._current_state = self.STATE_CALIBRATE

        elif state == self.STATE_AUTO_INTEG_CONFIRM:
            if self.button_handler.check_button(BTN_ENTER):  # A: Apply
                self._apply_auto_integration_result()
            elif self.button_handler.check_button(BTN_BACK):  # B: Discard/Back
                logger.info(
                    "Auto-Integration result discarded by user. Returning to Calibrate Menu."
                )
                self._cancel_auto_integration()
                self._current_state = self.STATE_CALIBRATE
        else:
            logger.error(f"Unhandled input state in SpectrometerScreen: {state}")
            self._current_state = self.STATE_LIVE_VIEW  # Fallback

        return action_result

    def _run_auto_integration_step(self):
        """Performs one iteration of the auto-integration algorithm."""
        assert (
            self.spectrometer
            and hasattr(self.spectrometer, "_dev")
            and self.spectrometer._dev.is_open
        ), "Spectrometer not ready for auto-integ step."
        assert (
            self._auto_integ_optimizing
        ), "Auto-integ step called when not optimizing."
        assert np is not None, "NumPy (np) is required for auto-integration."

        # --- Helper to transition to CONFIRM state with proper Y-axis scaling ---
        def _transition_to_confirm(status_msg: str, pending_ms: int):
            self._auto_integ_status_msg = status_msg
            self._pending_auto_integ_ms = pending_ms
            self._auto_integ_optimizing = False  # Stop further iterations

            # Scale Y-axis for the confirm state based on the captured frozen spectrum
            if (
                self._frozen_intensities is not None
                and len(self._frozen_intensities) > 0
            ):
                final_max_peak = np.max(
                    self._frozen_intensities
                )  # Intensities are from current/last capture
                # THIS IS WHERE _current_y_max IS SET FOR THE CONFIRM SCREEN
                self._current_y_max = max(
                    float(Y_AXIS_MIN_CEILING),
                    float(final_max_peak * Y_AXIS_RESCALE_FACTOR),
                )
                self._current_y_max = min(
                    self._current_y_max,
                    float(self._hw_max_intensity_adc * Y_AXIS_RESCALE_FACTOR),
                )
                logger.debug(
                    f"Auto-Integ (Confirm): Y-max set to {self._current_y_max:.1f} for peak {final_max_peak:.1f}"
                )
            else:  # Fallback if no valid frozen intensities (e.g., initial error before any capture)
                self._current_y_max = float(Y_AXIS_DEFAULT_MAX)
                logger.warning(
                    "Auto-Integ (Confirm): No frozen intensities to scale Y-axis, using default."
                )

            self._current_state = self.STATE_AUTO_INTEG_CONFIRM
            logger.info(
                f"Auto-Integ: {self._auto_integ_status_msg} Proposed Integ: {self._pending_auto_integ_ms} ms."
            )

        # --- End of helper ---

        if self._auto_integ_iteration_count >= AUTO_INTEG_MAX_ITERATIONS:
            # Note: _frozen_intensities should hold the spectrum from the *last successful iteration*
            # before hitting max iterations. The helper will use this.
            _transition_to_confirm(
                f"Max iterations ({AUTO_INTEG_MAX_ITERATIONS}) reached.",
                int(
                    round(self._current_auto_integ_us / 1000.0)
                ),  # Use the last attempted integration time
            )
            return

        self._auto_integ_iteration_count += 1
        # Update status message here, before potential early exit if capture fails
        current_iter_msg = f"Running iter {self._auto_integ_iteration_count}/{AUTO_INTEG_MAX_ITERATIONS}..."
        self._auto_integ_status_msg = (
            current_iter_msg  # Keep this concise for the top status line
        )
        logger.debug(
            f"Auto-Integ Step {self._auto_integ_iteration_count}: Current test integ {self._current_auto_integ_us} µs. {current_iter_msg}"
        )

        max_peak_adc = 0.0
        try:
            clamped_current_us = max(
                self._hw_min_integration_us,
                min(self._current_auto_integ_us, self._hw_max_integration_us),
            )
            if clamped_current_us != self._current_auto_integ_us:
                logger.debug(
                    f"Auto-Integ: Clamped test integ from {self._current_auto_integ_us} to {clamped_current_us} µs for device."
                )

            self.spectrometer.integration_time_micros(clamped_current_us)

            intensities = self.spectrometer.intensities(
                correct_dark_counts=True, correct_nonlinearity=True
            )
            assert (
                intensities is not None
            ), "Spectrometer returned None for intensities."
            if len(intensities) > 0:
                max_peak_adc = np.max(intensities)
            else:
                logger.warning("Auto-Integ: Empty intensities array received.")
                max_peak_adc = 0.0

            # Store this spectrum and its integration time. This will be used by _transition_to_confirm.
            self._frozen_intensities = (
                intensities.copy() if intensities is not None else None
            )
            self._frozen_wavelengths = (
                self.wavelengths.copy() if self.wavelengths is not None else None
            )
            self._frozen_capture_type = self.FROZEN_TYPE_AUTO_INTEG_RESULT
            self._frozen_integration_ms = int(round(clamped_current_us / 1000.0))

        except (sb.SeaBreezeError, usb.core.USBError, AttributeError, AssertionError, RuntimeError) as e:  # type: ignore
            logger.error(
                f"Auto-Integ: Error during spectrum capture: {e}", exc_info=True
            )
            _transition_to_confirm(
                "Capture Error. Aborting.",
                int(round(self._current_auto_integ_us / 1000.0)),
            )
            return

        self._last_peak_adc_value = max_peak_adc
        logger.debug(
            f"Auto-Integ Step {self._auto_integ_iteration_count}: Max peak ADC {max_peak_adc:.1f} with {clamped_current_us} µs."
        )

        if (
            self._auto_integ_target_low_counts
            <= max_peak_adc
            <= self._auto_integ_target_high_counts
        ):
            _transition_to_confirm(
                f"Optimal found: {max_peak_adc:.0f} counts.",
                int(round(clamped_current_us / 1000.0)),
            )
            return
        if (
            clamped_current_us <= self._hw_min_integration_us
            and max_peak_adc > self._auto_integ_target_high_counts
        ):
            _transition_to_confirm(
                f"Saturated at min integ ({self._hw_min_integration_us / 1000.0:.1f} ms).",
                int(round(self._hw_min_integration_us / 1000.0)),
            )
            return
        if (
            clamped_current_us >= self._hw_max_integration_us
            and max_peak_adc < self._auto_integ_target_low_counts
        ):
            _transition_to_confirm(
                f"Too dim at max integ ({self._hw_max_integration_us / 1000.0:.1f} ms).",
                int(round(self._hw_max_integration_us / 1000.0)),
            )
            return

        # Algorithm to calculate next integration time
        target_adc = (
            self._auto_integ_target_low_counts + self._auto_integ_target_high_counts
        ) / 2.0
        effective_max_peak_adc = max_peak_adc if max_peak_adc > 1.0 else 1.0
        adjustment_ratio = target_adc / effective_max_peak_adc
        ideal_next_integ_us = clamped_current_us * adjustment_ratio
        change_us = ideal_next_integ_us - clamped_current_us
        damped_change_us = change_us * AUTO_INTEG_PROPORTIONAL_GAIN

        current_adjustment_direction = (
            1
            if damped_change_us > self._hw_integration_time_increment_us / 2.0
            else (
                -1
                if damped_change_us < -self._hw_integration_time_increment_us / 2.0
                else 0
            )
        )

        if (
            current_adjustment_direction != 0
            and self._previous_integ_adjustment_direction != 0
            and current_adjustment_direction
            == -self._previous_integ_adjustment_direction
        ):
            damped_change_us *= AUTO_INTEG_OSCILLATION_DAMPING_FACTOR
            logger.debug(
                f"Auto-Integ: Oscillation detected. Damping change to {damped_change_us:.0f} µs."
            )

        if abs(damped_change_us) < AUTO_INTEG_MIN_ADJUSTMENT_US:
            min_adj = AUTO_INTEG_MIN_ADJUSTMENT_US
            if max_peak_adc < self._auto_integ_target_low_counts:
                damped_change_us = min_adj
            elif max_peak_adc > self._auto_integ_target_high_counts:
                damped_change_us = -min_adj
            logger.debug(
                f"Auto-Integ: Applying min adjustment of {damped_change_us:.0f} µs."
            )

        new_test_integ_us = clamped_current_us + damped_change_us
        new_test_integ_us = max(
            self._hw_min_integration_us,
            min(new_test_integ_us, self._hw_max_integration_us),
        )
        if self._hw_integration_time_increment_us > 0:
            new_test_integ_us = (
                round(new_test_integ_us / self._hw_integration_time_increment_us)
                * self._hw_integration_time_increment_us
            )
        new_test_integ_us = int(new_test_integ_us)

        if new_test_integ_us == clamped_current_us and not (
            self._auto_integ_target_low_counts
            <= max_peak_adc
            <= self._auto_integ_target_high_counts
        ):
            _transition_to_confirm(
                "Algorithm stalled. No change.", int(round(clamped_current_us / 1000.0))
            )
            return

        self._current_auto_integ_us = new_test_integ_us
        self._previous_integ_adjustment_direction = current_adjustment_direction
        # Update status for the next iteration (will be shown by _draw_overlays if still in RUNNING state)
        self._auto_integ_status_msg = (
            f"Peak:{max_peak_adc:.0f} Next:{self._current_auto_integ_us / 1000.0:.1f}ms"
        )
        logger.debug(
            f"Auto-Integ: Next test integration time: {self._current_auto_integ_us} µs. Status: {self._auto_integ_status_msg}"
        )

    def _apply_auto_integration_result(self):
        """Applies the confirmed auto-integration time to the MenuSystem."""
        logger.info("Applying auto-integration result.")
        assert (
            self.menu_system is not None
        ), "MenuSystem not available to apply auto-integ result."
        if self._pending_auto_integ_ms is not None:
            logger.info(
                f"Setting integration time in menu to: {self._pending_auto_integ_ms} ms."
            )
            self.menu_system.set_integration_time_ms(self._pending_auto_integ_ms)
            # Update _last_integration_time_ms to reflect the newly set value (which might be clamped/aligned by MenuSystem)
            self._last_integration_time_ms = self.menu_system.get_integration_time_ms()
            logger.info(
                f"Auto-integration successful. New active integration time: {self._last_integration_time_ms} ms."
            )
        else:
            logger.warning("No pending auto-integration time to apply.")

        self._cancel_auto_integration()  # Clean up auto-integration state variables
        self._current_state = self.STATE_LIVE_VIEW
        logger.info("Returned to Live View after auto-integration.")

    def _perform_freeze_capture(
        self, capture_type: str
    ):  # MODIFIED - Corrected constant usage
        assert (
            self.menu_system is not None
            and self.spectrometer is not None
            and self.wavelengths is not None
        )
        assert capture_type in [
            self.FROZEN_TYPE_OOI,
            self.FROZEN_TYPE_DARK,
            self.FROZEN_TYPE_WHITE,
        ], f"Invalid capture_type '{capture_type}'"

        dev_proxy = getattr(self.spectrometer, "_dev", None)
        if not (dev_proxy and hasattr(dev_proxy, "is_open") and dev_proxy.is_open):
            logger.error(f"Cannot freeze {capture_type}: Spectrometer not ready.")
            return

        logger.info(f"Attempting to freeze spectrum for type: {capture_type}...")
        try:
            current_menu_integ_ms = self.menu_system.get_integration_time_ms()
            current_menu_mode = self.menu_system.get_collection_mode()
            assert isinstance(current_menu_integ_ms, int) and current_menu_integ_ms > 0

            if current_menu_integ_ms != self._last_integration_time_ms:
                integ_us = int(current_menu_integ_ms * 1000)
                integ_us_clamped = max(
                    self._hw_min_integration_us,
                    min(integ_us, self._hw_max_integration_us),
                )
                logger.debug(
                    f"FREEZE ({capture_type}): Setting integ to {integ_us_clamped} µs (target {current_menu_integ_ms} ms)"
                )
                self.spectrometer.integration_time_micros(integ_us_clamped)
            self._last_integration_time_ms = (
                current_menu_integ_ms  # Capture with this integration time
            )

            raw_intensities_capture = self.spectrometer.intensities(
                correct_dark_counts=True, correct_nonlinearity=True
            )
            assert raw_intensities_capture is not None and len(
                raw_intensities_capture
            ) == len(self.wavelengths)

            self._clear_frozen_data()

            if capture_type == self.FROZEN_TYPE_OOI:
                self._frozen_sample_collection_mode = current_menu_mode
                if current_menu_mode == MODE_REFLECTANCE:
                    valid_refs, _ = self._are_references_valid_for_reflectance()
                    assert (
                        valid_refs
                    ), "Freeze Reflectance called with invalid references - input handling should prevent this."
                    assert (
                        self._dark_reference_intensities is not None
                        and self._white_reference_intensities is not None
                    )

                    self._raw_target_intensities_for_reflectance = (
                        raw_intensities_capture.copy()
                    )

                    numerator = (
                        self._raw_target_intensities_for_reflectance
                        - self._dark_reference_intensities
                    )
                    denominator = (
                        self._white_reference_intensities
                        - self._dark_reference_intensities
                    )

                    reflectance_values = np.full_like(
                        self._raw_target_intensities_for_reflectance, 0.0
                    )
                    valid_indices = np.where(np.abs(denominator) > DIVISION_EPSILON)
                    reflectance_values[valid_indices] = (
                        numerator[valid_indices] / denominator[valid_indices]
                    )

                    # Use Y_AXIS_REFLECTANCE_RESCALE_MAX_CEILING for clipping the stored frozen data too
                    self._frozen_intensities = np.clip(
                        reflectance_values, 0.0, Y_AXIS_REFLECTANCE_RESCALE_MAX_CEILING
                    )
                else:
                    self._frozen_intensities = raw_intensities_capture
            else:
                self._frozen_intensities = raw_intensities_capture

            self._frozen_wavelengths = self.wavelengths.copy()
            self._frozen_timestamp = self.menu_system.get_timestamp_datetime()
            self._frozen_integration_ms = self._last_integration_time_ms
            self._frozen_capture_type = capture_type

            display_mode_log = (
                self._frozen_sample_collection_mode
                if capture_type == self.FROZEN_TYPE_OOI
                else capture_type
            )
            logger.info(
                f"{display_mode_log} spectrum frozen (Integ: {self._frozen_integration_ms} ms)."
            )
            self._current_state = self.STATE_FROZEN_VIEW

        except Exception as e:
            logger.error(f"Error freezing {capture_type} spectrum: {e}", exc_info=True)

    def _perform_save_frozen_data(self):
        assert (
            self._frozen_capture_type
            and self._frozen_intensities is not None
            and self._frozen_wavelengths is not None
            and self._frozen_timestamp
            and self._frozen_integration_ms is not None
        ), "Frozen data assertion failed before saving."

        spectra_type_csv = ""
        # Determine the spectra type string for the CSV based on the capture type
        if self._frozen_capture_type == self.FROZEN_TYPE_OOI:
            assert (
                self._frozen_sample_collection_mode is not None
            ), "Frozen sample collection mode missing for OOI type."
            spectra_type_csv = self._frozen_sample_collection_mode.upper()
        elif self._frozen_capture_type in [
            self.FROZEN_TYPE_DARK,
            self.FROZEN_TYPE_WHITE,
        ]:
            spectra_type_csv = self._frozen_capture_type  # e.g., "DARK" or "WHITE"
        else:
            logger.error(
                f"Unknown frozen_capture_type: {self._frozen_capture_type}. Cannot save data."
            )
            self._perform_discard_frozen_data()  # Discard invalid data
            return

        logger.info(f"Attempting to save frozen data as {spectra_type_csv}...")

        # Determine if a plot should be saved (only for OOI/Sample captures, not for Dark/White refs by default here)
        should_save_plot = self._frozen_capture_type == self.FROZEN_TYPE_OOI

        save_success = self._save_data(
            intensities=self._frozen_intensities,
            wavelengths=self._frozen_wavelengths,
            timestamp=self._frozen_timestamp,
            integration_ms=self._frozen_integration_ms,
            spectra_type=spectra_type_csv,
            save_plot=should_save_plot,
        )

        if save_success:
            logger.info(
                f"Frozen {self._frozen_capture_type} (saved to CSV as {spectra_type_csv}) successful."
            )

            # --- Crucial Fix: Update internal references if a Dark or White spectrum was saved ---
            if self._frozen_capture_type == self.FROZEN_TYPE_DARK:
                assert (
                    self._frozen_intensities is not None
                    and self._frozen_integration_ms is not None
                ), "Dark frozen data became None before internal update."
                self._dark_reference_intensities = self._frozen_intensities.copy()
                self._dark_reference_integration_ms = self._frozen_integration_ms
                logger.info(
                    f"Internal Dark reference updated. Integ: {self._dark_reference_integration_ms} ms, {len(self._dark_reference_intensities)} points."
                )
            elif self._frozen_capture_type == self.FROZEN_TYPE_WHITE:
                assert (
                    self._frozen_intensities is not None
                    and self._frozen_integration_ms is not None
                ), "White frozen data became None before internal update."
                self._white_reference_intensities = self._frozen_intensities.copy()
                self._white_reference_integration_ms = self._frozen_integration_ms
                logger.info(
                    f"Internal White reference updated. Integ: {self._white_reference_integration_ms} ms, {len(self._white_reference_intensities)} points."
                )
            # --- End of Fix ---

            # Specific handling for saving raw target data when a REFLECTANCE OOI spectrum is saved
            if (
                self._frozen_capture_type == self.FROZEN_TYPE_OOI
                and self._frozen_sample_collection_mode == MODE_REFLECTANCE
                and self._raw_target_intensities_for_reflectance is not None
            ):
                logger.info(
                    f"Saving associated RAW_REFLECTANCE target for the Reflectance OOI spectrum..."
                )
                # Save the raw target spectrum that was used to calculate this reflectance value
                # Note: save_plot is False for this auxiliary raw data.
                # Scan count is typically incremented by the main REFLECTANCE save, not for this raw component.
                # However, current _save_data increments for all. This is fine if consistent.
                raw_target_save_success = self._save_data(
                    intensities=self._raw_target_intensities_for_reflectance,
                    wavelengths=self._frozen_wavelengths,  # Wavelengths are the same
                    timestamp=self._frozen_timestamp,  # Timestamp is the same
                    integration_ms=self._frozen_integration_ms,  # Integration time is the same
                    spectra_type=SPECTRA_TYPE_RAW_TARGET_FOR_REFLECTANCE,  # Specific type for this raw data
                    save_plot=False,  # Typically no plot for this auxiliary raw data
                )
                if raw_target_save_success:
                    logger.info("RAW_REFLECTANCE target spectrum saved successfully.")
                else:
                    logger.error("Failed to save RAW_REFLECTANCE target spectrum.")
                self._raw_target_intensities_for_reflectance = (
                    None  # Clear after saving
                )

        else:
            logger.error(
                f"Failed to save frozen {self._frozen_capture_type} (intended as {spectra_type_csv})."
            )

        # State transition logic after save attempt
        if self._frozen_capture_type in [
            self.FROZEN_TYPE_OOI,
            self.FROZEN_TYPE_DARK,
            self.FROZEN_TYPE_WHITE,
        ]:
            self._current_state = (
                self.STATE_LIVE_VIEW
            )  # Always return to live view after any save
            if self._frozen_capture_type != self.FROZEN_TYPE_OOI:  # i.e., DARK or WHITE
                logger.info(
                    f"{self._frozen_capture_type} reference saved action complete. Returning to main live view."
                )
        else:
            # This case should ideally not be reached if all frozen_capture_types are handled above
            logger.warning(
                f"Unhandled frozen_capture_type '{self._frozen_capture_type}' for state transition after save. Defaulting to LIVE_VIEW."
            )
            self._current_state = self.STATE_LIVE_VIEW

        self._clear_frozen_data()  # Clear all temporary frozen data variables
        logger.info(
            f"Returned to state: {self._current_state} after processing frozen data save."
        )

    def _perform_discard_frozen_data(self):
        assert self._frozen_capture_type is not None
        logger.info(f"Discarding frozen {self._frozen_capture_type} spectrum.")
        original_frozen_type = self._frozen_capture_type
        self._clear_frozen_data()  # Clears type, so use original_frozen_type for logic

        if original_frozen_type == self.FROZEN_TYPE_OOI:
            self._current_state = self.STATE_LIVE_VIEW
        elif original_frozen_type == self.FROZEN_TYPE_DARK:
            self._current_state = self.STATE_DARK_CAPTURE_SETUP
        elif original_frozen_type == self.FROZEN_TYPE_WHITE:
            self._current_state = self.STATE_WHITE_CAPTURE_SETUP
        elif (
            original_frozen_type == self.FROZEN_TYPE_AUTO_INTEG_RESULT
        ):  # Discarding from auto-integ confirm
            self._cancel_auto_integration()  # Full reset of auto-integ vars
            self._current_state = self.STATE_CALIBRATE  # Go back to calib menu
            logger.info(
                "Frozen auto-integ result discarded. Returning to Calibrate Menu."
            )
        else:
            logger.error(
                f"Unknown frozen_type '{original_frozen_type}' during discard."
            )
            self._current_state = self.STATE_LIVE_VIEW
        logger.info(f"Returned to state: {self._current_state} after discarding.")

    def _save_data(
        self,
        intensities: np.ndarray,
        wavelengths: np.ndarray,
        timestamp: datetime.datetime,
        integration_ms: int,
        spectra_type: str,
        save_plot: bool = True,
    ) -> bool:
        # This method is only called if _perform_freeze_capture was successful,
        # which implies the spectrometer was ready at the time of capture.
        # No need for an additional _is_spectrometer_ready() check here.

        assert (
            intensities is not None
            and wavelengths is not None
            and timestamp
            and spectra_type
            and self.menu_system
        ), "_save_data called with invalid parameters"

        # Ensure we only save actual measurement types, not internal/temporary ones.
        valid_spectra_types_for_save = [
            SPECTRA_TYPE_RAW,
            SPECTRA_TYPE_REFLECTANCE,
            SPECTRA_TYPE_DARK_REF,
            SPECTRA_TYPE_WHITE_REF,
            SPECTRA_TYPE_RAW_TARGET_FOR_REFLECTANCE,
        ]
        if spectra_type not in valid_spectra_types_for_save:
            logger.warning(
                f"_save_data called with non-standard spectra_type: '{spectra_type}'. Data will not be saved."
            )
            return False

        logger.info(f"Preparing to save data of type: {spectra_type}")

        daily_folder = os.path.join(DATA_DIR, timestamp.strftime("%Y-%m-%d"))
        try:
            os.makedirs(
                daily_folder, exist_ok=True
            )  # Create folder if it doesn't exist
            logger.debug(f"Ensured daily data folder exists: {daily_folder}")
        except OSError as e_mkdir:
            logger.error(f"Could not create data directory {daily_folder}: {e_mkdir}")
            return False
        except Exception as e_mkdir_general:  # Catch any other makedirs error
            logger.error(
                f"Unexpected error creating data directory {daily_folder}: {e_mkdir_general}"
            )
            return False

        csv_path = os.path.join(
            daily_folder, f"{timestamp.strftime('%Y-%m-%d')}_{CSV_BASE_FILENAME}"
        )
        ts_utc_str = timestamp.strftime("%Y-%m-%dT%H:%M:%SZ")
        lens_str = self.menu_system.get_lens_type()
        assert isinstance(lens_str, str)

        current_temp_c_val_for_csv = ""  # Initialize to empty string for CSV

        if self.temp_sensor_info:
            temp_reading = (
                self.temp_sensor_info.get_temperature_c()
            )  # float, str, or None
            if isinstance(temp_reading, float):
                current_temp_c_val_for_csv = f"{temp_reading:.2f}"

        temp_value_for_log_display = (
            current_temp_c_val_for_csv
            if current_temp_c_val_for_csv
            else "MISSING/ERROR"
        )

        logger.debug(
            f"Saving data (Type: {spectra_type}, Lens: {lens_str}, Temp CSV: '{current_temp_c_val_for_csv}', Temp Log: '{temp_value_for_log_display}') to CSV: {csv_path}"
        )  # Log both values for clarity during debugging

        try:
            hdr_needed = not (
                os.path.isfile(csv_path) and os.path.getsize(csv_path) > 0
            )
            with open(
                csv_path, "a", newline=""
            ) as csvf:  # newline='' is important for csv
                writer = csv.writer(csvf)
                header_row = [
                    "timestamp_utc",
                    "spectra_type",
                    "lens_type",
                    "integration_time_ms",
                    "temperature_c",  # Column name
                ]
                header_row.extend(
                    [f"{float(wl):.2f}" for wl in wavelengths]
                )  # Assuming wl can be float()

                if hdr_needed:
                    writer.writerow(header_row)

                data_row = [
                    ts_utc_str,
                    spectra_type,
                    lens_str,
                    integration_ms,
                    current_temp_c_val_for_csv,  # Writes "" or formatted float
                ]
                data_row.extend(
                    [f"{float(i):.4f}" for i in intensities]
                )  # Assuming i can be float()
                writer.writerow(data_row)

            # Only increment scan count for "OOI" type captures (RAW or REFLECTANCE)
            # Dark/White refs or RAW_TARGET_FOR_REFLECTANCE should not increment the main scan count.
            if spectra_type in [SPECTRA_TYPE_RAW, SPECTRA_TYPE_REFLECTANCE]:
                self._scans_today_count += 1
                logger.info(
                    f"Scan count today incremented to: {self._scans_today_count}"
                )
            else:
                logger.info(
                    f"Reference/Auxiliary data type '{spectra_type}' saved, scan count not incremented."
                )

            if (
                save_plot and plt and Image
            ):  # save_plot is usually True for OOI, False for refs
                plot_ts_local = timestamp.strftime("%Y-%m-%d-%H%M%S")
                # Use PLOT_SAVE_DIR (which is DATA_DIR) and then daily_folder
                plot_file = os.path.join(
                    daily_folder,
                    f"spectrum_{spectra_type}_{lens_str}_{plot_ts_local}.png",
                )
                logger.debug(f"Attempting to save plot: {plot_file}")
                fig_temp, ax_temp = None, None
                try:
                    fig_temp, ax_temp = plt.subplots(figsize=(8, 6))
                    if not fig_temp or not ax_temp:
                        raise RuntimeError("Failed temp fig/axes for plot save.")
                    ax_temp.plot(wavelengths, intensities)
                    title_scan_count = (
                        self._scans_today_count
                        if spectra_type in [SPECTRA_TYPE_RAW, SPECTRA_TYPE_REFLECTANCE]
                        else "Ref"
                    )
                    ax_temp.set_title(
                        f"Spectrum ({spectra_type}) - {plot_ts_local}\nLens: {lens_str}, Integ: {integration_ms} ms, Scan#: {title_scan_count}",
                        fontsize=10,
                    )
                    ax_temp.set_xlabel("Wavelength (nm)")
                    ax_temp.set_ylabel(
                        "Intensity"
                        if spectra_type != SPECTRA_TYPE_REFLECTANCE
                        else "Reflectance"
                    )
                    ax_temp.grid(True, linestyle="--", alpha=0.7)
                    fig_temp.tight_layout()
                    fig_temp.savefig(plot_file, dpi=150)
                    logger.info(f"Plot image saved: {plot_file}")
                except Exception as e_plot:
                    logger.error(
                        f"Error saving plot {plot_file}: {e_plot}", exc_info=True
                    )
                finally:
                    if fig_temp and plt and plt.fignum_exists(fig_temp.number):
                        plt.close(fig_temp)
            return True
        except Exception as e_csv:
            logger.error(f"Error saving data to CSV {csv_path}: {e_csv}", exc_info=True)
            return False

    def _rescale_y_axis(self, relative: bool = False):
        assert (
            self.menu_system is not None
            and np is not None
            and self.spectrometer is not None
        )
        dev_proxy = getattr(self.spectrometer, "_dev", None)
        if not (dev_proxy and hasattr(dev_proxy, "is_open") and dev_proxy.is_open):
            logger.warning("Spectrometer not ready for Y-axis rescale.")
            return

        logger.info(f"Attempting to rescale Y-axis...")
        try:
            current_menu_integ_ms = self.menu_system.get_integration_time_ms()
            current_menu_mode = self.menu_system.get_collection_mode()
            assert isinstance(current_menu_integ_ms, int) and current_menu_integ_ms > 0

            if current_menu_integ_ms != self._last_integration_time_ms:
                integ_us = int(current_menu_integ_ms * 1000)
                integ_us_clamped = max(
                    self._hw_min_integration_us,
                    min(integ_us, self._hw_max_integration_us),
                )
                logger.debug(
                    f"RESCALE_Y: Setting integ to {integ_us_clamped} µs (target {current_menu_integ_ms} ms)"
                )
                self.spectrometer.integration_time_micros(integ_us_clamped)
            self._last_integration_time_ms = current_menu_integ_ms

            intensities_for_rescale_raw = self.spectrometer.intensities(
                correct_dark_counts=True, correct_nonlinearity=True
            )
            assert intensities_for_rescale_raw is not None

            max_val_for_scaling = 0.0
            is_reflectance_plot_for_rescale = False

            if current_menu_mode == MODE_REFLECTANCE:
                valid_refs, _ = self._are_references_valid_for_reflectance()
                if valid_refs:
                    assert (
                        self._dark_reference_intensities is not None
                        and self._white_reference_intensities is not None
                    )
                    is_reflectance_plot_for_rescale = True
                    numerator = (
                        intensities_for_rescale_raw - self._dark_reference_intensities
                    )
                    denominator = (
                        self._white_reference_intensities
                        - self._dark_reference_intensities
                    )
                    reflectance_values = np.full_like(intensities_for_rescale_raw, 0.0)
                    valid_indices = np.where(np.abs(denominator) > DIVISION_EPSILON)
                    reflectance_values[valid_indices] = (
                        numerator[valid_indices] / denominator[valid_indices]
                    )

                    if len(reflectance_values) > 0:
                        max_val_for_scaling = np.max(reflectance_values)
                    else:
                        logger.warning("Empty reflectance values for rescale.")
                        return
                else:
                    if len(intensities_for_rescale_raw) > 0:
                        max_val_for_scaling = np.max(intensities_for_rescale_raw)
                    else:
                        logger.warning(
                            "Empty raw intensities for rescale (reflectance mode, bad refs)."
                        )
                        return
            else:
                if len(intensities_for_rescale_raw) > 0:
                    max_val_for_scaling = np.max(intensities_for_rescale_raw)
                else:
                    logger.warning("Empty raw intensities for rescale (raw mode).")
                    return

            if is_reflectance_plot_for_rescale:
                new_y_max = max(
                    float(Y_AXIS_REFLECTANCE_RESCALE_MIN_CEILING),
                    float(max_val_for_scaling * Y_AXIS_RESCALE_FACTOR),
                )
                new_y_max = min(
                    new_y_max, float(Y_AXIS_REFLECTANCE_RESCALE_MAX_CEILING)
                )
            else:
                new_y_max = max(
                    float(Y_AXIS_MIN_CEILING),
                    float(max_val_for_scaling * Y_AXIS_RESCALE_FACTOR),
                )
                new_y_max = min(
                    new_y_max, float(self._hw_max_intensity_adc * Y_AXIS_RESCALE_FACTOR)
                )

            self._current_y_max = new_y_max
            logger.info(
                f"Y-axis max rescaled to: {self._current_y_max:.2f} (peak val: {max_val_for_scaling:.2f}, mode: {'Reflectance' if is_reflectance_plot_for_rescale else 'Raw'})"
            )

        except Exception as e:
            logger.error(f"Error rescaling Y-axis: {e}", exc_info=True)

    def _capture_and_plot(self) -> pygame.Surface | None:
        # Initial check for plotting library
        if not (self.plot_fig and self.plot_ax and self.plot_line and plt and Image):
            logger.warning(
                "_capture_and_plot: Plotting components not initialized or Matplotlib/Pillow missing."
            )
            return None  # Cannot plot

        spectrometer_can_operate = self._is_spectrometer_ready()
        state = self._current_state
        current_menu_mode = self.menu_system.get_collection_mode()

        plot_wl: np.ndarray | None = None
        plot_inten: np.ndarray | None = None
        y_label_str = "Intensity"

        # --- Handle states that display FROZEN data first ---
        is_frozen_plot_state = state == self.STATE_FROZEN_VIEW or (
            state == self.STATE_AUTO_INTEG_CONFIRM
            and self._frozen_capture_type == self.FROZEN_TYPE_AUTO_INTEG_RESULT
        )

        if is_frozen_plot_state:
            if (
                self._frozen_intensities is not None
                and self._frozen_wavelengths is not None
                and self._frozen_integration_ms is not None
            ):
                plot_wl, plot_inten = self._frozen_wavelengths, self._frozen_intensities
                if state == self.STATE_FROZEN_VIEW:
                    assert self._frozen_capture_type is not None
                    if self._frozen_capture_type == self.FROZEN_TYPE_OOI:
                        assert self._frozen_sample_collection_mode is not None
                        y_label_str = f"{self._frozen_sample_collection_mode.capitalize()} (Frozen)"
                    else:  # DARK, WHITE
                        y_label_str = (
                            f"{self._frozen_capture_type.capitalize()} (Frozen)"
                        )
                elif state == self.STATE_AUTO_INTEG_CONFIRM:
                    y_label_str = f"Raw Final ({self._frozen_integration_ms}ms)"
            else:
                logger.error(
                    "Frozen data missing for plot in a frozen state. Discarding stale state."
                )
                self._perform_discard_frozen_data()  # Go back to a valid non-frozen state
                return None  # No plot this frame
        # --- Handle LIVE data capture and plotting ---
        elif spectrometer_can_operate:
            # This block executes for live_view, calibrate, dark_setup, white_setup, auto_integ_setup, auto_integ_running
            # if the spectrometer is ready.
            try:
                current_menu_integ_ms = self.menu_system.get_integration_time_ms()
                integ_time_for_capture_us = (
                    self._current_auto_integ_us
                    if state == self.STATE_AUTO_INTEG_RUNNING
                    else int(current_menu_integ_ms * 1000)
                )

                if (
                    current_menu_integ_ms != self._last_integration_time_ms
                    and state != self.STATE_AUTO_INTEG_RUNNING
                ):
                    self._last_integration_time_ms = current_menu_integ_ms

                integ_us_clamped = max(
                    self._hw_min_integration_us,
                    min(integ_time_for_capture_us, self._hw_max_integration_us),
                )
                self.spectrometer.integration_time_micros(integ_us_clamped)

                raw_inten_capture = self.spectrometer.intensities(
                    correct_dark_counts=True, correct_nonlinearity=True
                )

                if raw_inten_capture is None or len(raw_inten_capture) != len(
                    self.wavelengths
                ):
                    logger.warning(
                        f"Failed live capture or length mismatch in state {state}."
                    )
                    return None  # No plot this frame

                plot_wl = (
                    self.wavelengths
                )  # Wavelengths are from the ready spectrometer

                # Determine plot_inten and y_label based on state and mode
                is_calibration_raw_display_state = state in [
                    self.STATE_CALIBRATE,
                    self.STATE_DARK_CAPTURE_SETUP,
                    self.STATE_WHITE_CAPTURE_SETUP,
                    self.STATE_AUTO_INTEG_SETUP,
                ]

                if (
                    is_calibration_raw_display_state
                    or state == self.STATE_AUTO_INTEG_RUNNING
                ):
                    plot_inten = raw_inten_capture
                    y_label_str = "Intensity (Counts)"
                    if state == self.STATE_AUTO_INTEG_RUNNING:
                        y_label_str = f"Raw Auto ({integ_us_clamped/1000.0:.1f}ms)"
                    # Ensure Y-axis is suitable for raw counts
                    if self._current_y_max < Y_AXIS_MIN_CEILING * 0.9 or (
                        current_menu_mode == MODE_REFLECTANCE
                        and self._current_y_max
                        <= Y_AXIS_REFLECTANCE_RESCALE_MAX_CEILING * 1.1
                    ):
                        self._current_y_max = Y_AXIS_DEFAULT_MAX
                elif state == self.STATE_LIVE_VIEW:
                    if current_menu_mode == MODE_REFLECTANCE:
                        valid_refs, _ = self._are_references_valid_for_reflectance()
                        if valid_refs:
                            assert (
                                self._dark_reference_intensities is not None
                                and self._white_reference_intensities is not None
                            )
                            numerator = (
                                raw_inten_capture - self._dark_reference_intensities
                            )
                            denominator = (
                                self._white_reference_intensities
                                - self._dark_reference_intensities
                            )
                            reflectance_values = np.full_like(raw_inten_capture, 0.0)
                            valid_indices = np.where(np.abs(denominator) > DIVISION_EPSILON)  # type: ignore
                            reflectance_values[valid_indices] = (
                                numerator[valid_indices] / denominator[valid_indices]
                            )
                            plot_inten = np.clip(
                                reflectance_values,
                                0.0,
                                Y_AXIS_REFLECTANCE_RESCALE_MAX_CEILING,
                            )
                            y_label_str = "Reflectance"
                            if (
                                self._current_y_max
                                > Y_AXIS_REFLECTANCE_RESCALE_MAX_CEILING * 1.1
                            ):
                                self._current_y_max = Y_AXIS_REFLECTANCE_DEFAULT_MAX
                        else:  # Reflectance mode but refs not valid for current settings
                            plot_inten = raw_inten_capture  # Show raw data
                            y_label_str = "Intensity (Ref Mode - No Valid Refs)"
                            if self._current_y_max < Y_AXIS_MIN_CEILING * 0.9:
                                self._current_y_max = Y_AXIS_DEFAULT_MAX
                    else:  # MODE_RAW in LIVE_VIEW
                        plot_inten = raw_inten_capture
                        y_label_str = "Intensity (Counts)"
                        if self._current_y_max < Y_AXIS_MIN_CEILING * 0.9:
                            self._current_y_max = Y_AXIS_DEFAULT_MAX
                else:  # Should not be reached if states are handled, fallback
                    plot_inten = raw_inten_capture
                    y_label_str = f"Intensity ({state})"

            except (
                sb.SeaBreezeError,
                (usb.core.USBError if usb else OSError),
                AttributeError,
                AssertionError,
                RuntimeError,
            ) as e_capture:
                logger.error(
                    f"Error during live data capture for plot: {e_capture}",
                    exc_info=False,
                )
                return None  # No plot this frame
        else:
            # Spectrometer is NOT ready, and we are NOT in a frozen data display state.
            # This means we are in a live view or setup state, but hardware is unavailable.
            logger.debug(
                "_capture_and_plot: Spectrometer not ready, and not in a frozen display state. No data to plot."
            )
            return None  # No data to plot. The draw() method will handle showing "Not Ready".

        # --- Common Plot Rendering Logic (if plot_wl and plot_inten have data) ---
        if (
            plot_wl is None
            or plot_inten is None
            or len(plot_wl) == 0
            or len(plot_inten) == 0
        ):
            logger.debug(
                "_capture_and_plot: No valid wavelength or intensity data to render."
            )
            return None

        try:
            display_intensities = plot_inten
            # Smoothing logic (as before)
            if (
                USE_LIVE_SMOOTHING
                and LIVE_SMOOTHING_WINDOW_SIZE > 1
                and np
                and isinstance(plot_inten, np.ndarray)
                and plot_inten.size >= LIVE_SMOOTHING_WINDOW_SIZE
            ):
                win_size = LIVE_SMOOTHING_WINDOW_SIZE + (
                    1 if LIVE_SMOOTHING_WINDOW_SIZE % 2 == 0 else 0
                )
                if win_size <= len(plot_inten) and win_size >= 3:
                    weights = np.ones(win_size) / float(win_size)
                    display_intensities = np.convolve(plot_inten, weights, mode="same")  # type: ignore

            self.plot_line.set_data(plot_wl, display_intensities)
            self.plot_ax.set_ylabel(y_label_str, fontsize=9, color="white")
            self.plot_ax.set_xlim(min(plot_wl), max(plot_wl))

            if state == self.STATE_AUTO_INTEG_RUNNING and len(display_intensities) > 0:
                current_max_val = np.max(display_intensities)
                dynamic_y_max = max(
                    float(Y_AXIS_MIN_CEILING),
                    float(current_max_val * Y_AXIS_RESCALE_FACTOR),
                )
                dynamic_y_max = min(
                    dynamic_y_max,
                    float(self._hw_max_intensity_adc * Y_AXIS_RESCALE_FACTOR),
                )
                self.plot_ax.set_ylim(0, dynamic_y_max)
            else:
                self.plot_ax.set_ylim(0, self._current_y_max)

            plot_buffer = io.BytesIO()
            self.plot_fig.savefig(
                plot_buffer,
                format="png",
                dpi=self.plot_fig.get_dpi(),
                bbox_inches="tight",
                pad_inches=0.05,
            )
            plot_buffer.seek(0)
            if plot_buffer.getbuffer().nbytes == 0:
                raise RuntimeError("Plot buffer is empty after savefig.")
            plot_surface = pygame.image.load(plot_buffer, "png")
            if plot_surface is None:
                raise RuntimeError("pygame.image.load returned None from buffer.")
            plot_buffer.close()
            return plot_surface

        except Exception as render_err:
            logger.error(f"Error rendering plot data: {render_err}", exc_info=True)
            if "plot_buffer" in locals() and plot_buffer:
                plot_buffer.close()
            return None

    def _draw_overlays(self):  # MODIFIED for 6 hint states
        if not self.overlay_font or not self.menu_system or not self.screen:
            logger.warning("Overlay dependencies missing in _draw_overlays.")
            return

        state = self._current_state
        current_menu_mode = self.menu_system.get_collection_mode()
        current_menu_integ_ms = self.menu_system.get_integration_time_ms()
        disp_integ_ms = DEFAULT_INTEGRATION_TIME_MS

        try:
            if (
                state == self.STATE_FROZEN_VIEW
                and self._frozen_integration_ms is not None
            ):
                disp_integ_ms = self._frozen_integration_ms
            elif state == self.STATE_AUTO_INTEG_RUNNING:
                disp_integ_ms = int(round(self._current_auto_integ_us / 1000.0))
            elif (
                state == self.STATE_AUTO_INTEG_CONFIRM
                and self._pending_auto_integ_ms is not None
            ):
                disp_integ_ms = self._pending_auto_integ_ms
            else:  # For live states, use current menu integration
                disp_integ_ms = current_menu_integ_ms
        except Exception as e_integ_disp:
            logger.warning(
                f"Could not get integration time for overlay: {e_integ_disp}"
            )

        try:
            top_y_pos = 5
            left_x_pos_start = 5
            right_margin = 5
            text_spacing = 10
            current_x_pos = left_x_pos_start

            integ_text_str = f"Integ: {disp_integ_ms} ms"
            integ_surf = self.overlay_font.render(integ_text_str, True, YELLOW)
            self.screen.blit(integ_surf, (current_x_pos, top_y_pos))
            current_x_pos += integ_surf.get_width() + text_spacing

            scans_text_str = f"Scans: {self._scans_today_count}"
            scans_surf = self.overlay_font.render(scans_text_str, True, YELLOW)
            self.screen.blit(scans_surf, (current_x_pos, top_y_pos))

            mode_txt_l1, mode_color_l1, hint_txt = "", YELLOW, ""

            if state == self.STATE_LIVE_VIEW:
                mode_txt_l1 = f"Mode: {current_menu_mode.upper()}"
                mode_color_l1 = YELLOW

                if current_menu_mode == MODE_REFLECTANCE:
                    valid_refs_overall, reason_code = (
                        self._are_references_valid_for_reflectance()
                    )

                    if not valid_refs_overall:
                        hint_base = "-> X:Calib | B:Menu"
                        # Build hint based on reason_code from _are_references_valid_for_reflectance
                        if reason_code == "No Dark/White refs":
                            hint_txt = "No Dark/White refs " + hint_base
                        elif reason_code == "No Dark ref":
                            hint_txt = "No Dark ref " + hint_base
                        elif reason_code == "No White ref":
                            hint_txt = "No White ref " + hint_base
                        elif reason_code == "Integ mismatch D&W":
                            hint_txt = "Integ mismatch D&W " + hint_base
                        elif reason_code == "Integ mismatch Dark":
                            hint_txt = "Integ mismatch Dark " + hint_base
                        elif reason_code == "Integ mismatch White":
                            hint_txt = "Integ mismatch White " + hint_base
                        else:
                            hint_txt = "Ref Problem " + hint_base  # Fallback
                    else:  # Reflectance mode, refs are valid
                        hint_txt = "A:Freeze | X:Calib | Y:Rescale | B:Menu"
                else:  # e.g., MODE_RAW
                    hint_txt = "A:Freeze | X:Calib | Y:Rescale | B:Menu"

            elif state == self.STATE_FROZEN_VIEW:
                mode_txt_l1 = "Mode: REVIEW"
                mode_color_l1 = BLUE
                hint_txt = "A:Save Frozen | B:Discard Frozen"

            elif state == self.STATE_CALIBRATE:
                mode_txt_l1 = "CALIBRATION MENU"
                mode_color_l1 = GREEN
                hint_txt = "A:White | X:Dark | Y:Auto | B:Back"
            elif state == self.STATE_DARK_CAPTURE_SETUP:
                mode_txt_l1 = "Mode: DARK SETUP"
                mode_color_l1 = RED
                hint_txt = "A:Freeze Dark | B:Back (Calib)"
            elif state == self.STATE_WHITE_CAPTURE_SETUP:
                mode_txt_l1 = "Mode: WHITE SETUP"
                mode_color_l1 = CYAN
                hint_txt = "A:Freeze White | Y:Rescale | B:Back (Calib)"
            elif state == self.STATE_AUTO_INTEG_SETUP:
                mode_txt_l1 = "AUTO INTEG SETUP"
                mode_color_l1 = MAGENTA
                hint_txt = "Aim White Ref -> A:Start | B:Back (Calib)"
            elif state == self.STATE_AUTO_INTEG_RUNNING:
                mode_txt_l1 = f"AUTO RUN iter:{self._auto_integ_iteration_count}"
                mode_color_l1 = MAGENTA
                hint_txt = "B:Cancel Auto-Integration"
            elif state == self.STATE_AUTO_INTEG_CONFIRM:
                mode_txt_l1 = "AUTO INTEG CONFIRM"
                mode_color_l1 = MAGENTA
                hint_txt = "A:Apply Result | B:Back (Calib)"
            else:
                mode_txt_l1 = f"Mode: {state.upper()} (ERROR)"  # Should not happen with corrected handle_input
                logger.error(f"Overlay: Unhandled state '{state}' for mode text.")

            if mode_txt_l1:
                mode_surf_l1 = self.overlay_font.render(
                    mode_txt_l1, True, mode_color_l1
                )
                mode_rect = mode_surf_l1.get_rect(
                    right=SCREEN_WIDTH - right_margin, top=top_y_pos
                )
                scan_text_width_plus_spacing = (
                    scans_surf.get_width() + text_spacing if scans_surf else 0
                )

                # Check for potential overlap with scan count if mode text is very long
                # (though it's generally shorter now for error states)
                # A more robust placement would be to ensure mode_rect.left > current_x_pos + scan_text_width_plus_spacing
                # For simplicity, this right-aligns it if it's too long to fit after scans.
                if (
                    mode_rect.left < current_x_pos + scan_text_width_plus_spacing
                ):  # Heuristic check
                    mode_rect.right = (
                        SCREEN_WIDTH - right_margin
                    )  # Keep it right aligned
                else:
                    # If it fits, try to place it after scans, but still prefer right alignment
                    # This can be tricky. Let's keep it simple: right align it.
                    pass  # mode_rect is already right-aligned by default above

                self.screen.blit(mode_surf_l1, mode_rect)

            if hint_txt:
                hint_surf = self.overlay_font.render(hint_txt, True, YELLOW)
                self.screen.blit(
                    hint_surf,
                    hint_surf.get_rect(
                        centerx=SCREEN_WIDTH // 2, bottom=SCREEN_HEIGHT - 5
                    ),
                )

        except pygame.error as e_render_overlay_final:
            logger.error(
                f"Pygame error rendering overlays: {e_render_overlay_final}",
                exc_info=True,
            )
        except AssertionError as e_assert_overlay_final:
            logger.error(
                f"AssertionError rendering overlays: {e_assert_overlay_final}",
                exc_info=True,
            )
        except Exception as e_overlay_final:
            logger.error(
                f"Unexpected error rendering overlays: {e_overlay_final}", exc_info=True
            )

    def draw(self):
        if self.screen is None:
            logger.error("Screen object None in SpectrometerScreen.draw.")
            return
        self.screen.fill(BLACK)

        spectrometer_can_operate = self._is_spectrometer_ready()  # True if HW is ok
        plot_surface = None

        # Determine if we are in a state that should display previously frozen data
        is_frozen_data_display_state = (
            self._current_state == self.STATE_FROZEN_VIEW
            or (
                self._current_state == self.STATE_AUTO_INTEG_CONFIRM
                and self._frozen_capture_type == self.FROZEN_TYPE_AUTO_INTEG_RESULT
                and self._frozen_intensities is not None
            )
        )

        if spectrometer_can_operate or is_frozen_data_display_state:
            # Attempt to capture and plot if spectrometer is ready OR if we have frozen data to show
            if self.plot_fig:  # Check if plot components are initialized
                plot_surface = self._capture_and_plot()
            else:
                logger.warning("Plotting components not initialized, cannot draw plot.")

        if plot_surface:
            plot_rect = plot_surface.get_rect(
                centerx=SCREEN_WIDTH // 2, top=25
            )  # Position of plot
            plot_rect.clamp_ip(
                self.screen.get_rect()
            )  # Ensure it's within screen bounds
            self.screen.blit(plot_surface, plot_rect)
        else:
            # No plot surface available (either error, or spectrometer not ready and no frozen data)
            if self.overlay_font:
                status_txt = (
                    "Plot Error"  # Default if plot_surface is None for unknown reason
                )
                if not USE_SPECTROMETER:  # Configured off
                    status_txt = "Spectrometer Disabled"
                elif not self.spectrometer:  # Failed to init Spectrometer object
                    status_txt = "Spectrometer Not Found"
                elif not spectrometer_can_operate and not is_frozen_data_display_state:
                    # Hardware not ready and no frozen data to show (e.g. live view with no device)
                    status_txt = "Spectrometer Not Ready"
                elif self.plot_fig is None:
                    status_txt = "Plotting System Error"

                status_surf = self.overlay_font.render(
                    status_txt,
                    True,
                    RED if "Not Ready" in status_txt or "Error" in status_txt else GRAY,
                )
                text_rect = status_surf.get_rect(
                    center=(SCREEN_WIDTH // 2, SCREEN_HEIGHT // 2)
                )
                self.screen.blit(status_surf, text_rect)

        self._draw_overlays()  # Draw status text, hints, etc.
        update_hardware_display(self.screen, self.display_hat)  # Actual screen update

    def run_loop(self) -> str:
        logger.info(
            f"Starting Spectrometer screen loop (Initial State: {self._current_state})."
        )
        assert self.menu_system is not None
        while self.is_active and not g_shutdown_flag.is_set():
            action = self.handle_input()
            if action == "QUIT":
                self.deactivate()
                return "QUIT"
            if action == "BACK_TO_MENU":
                self.deactivate()
                return "BACK"

            if (
                self._current_state == self.STATE_AUTO_INTEG_RUNNING
                and self._auto_integ_optimizing
            ):
                self._run_auto_integration_step()  # This might change state

            self.draw()

            wait_ms = int(SPECTRO_LOOP_DELAY_S * 1000)
            try:
                # Dynamic wait time based on integration time for live/setup states
                # Exclude frozen, calibrate menu, and confirm states from dynamic wait, as they don't continuously capture
                if self._current_state not in [
                    self.STATE_FROZEN_VIEW,
                    self.STATE_CALIBRATE,
                    self.STATE_AUTO_INTEG_CONFIRM,
                ]:
                    integ_ms_for_wait = 0
                    if self._current_state == self.STATE_AUTO_INTEG_RUNNING:
                        integ_ms_for_wait = int(
                            round(self._current_auto_integ_us / 1000.0)
                        )
                    else:  # live_view, dark_setup, white_setup, auto_integ_setup
                        integ_ms_for_wait = self.menu_system.get_integration_time_ms()

                    assert isinstance(integ_ms_for_wait, int) and integ_ms_for_wait >= 0
                    if integ_ms_for_wait > 0:
                        target_wait_s = (
                            integ_ms_for_wait / 1000.0
                        ) + SPECTRO_REFRESH_OVERHEAD_S
                        wait_ms = int(max(SPECTRO_LOOP_DELAY_S, target_wait_s) * 1000)
            except Exception as e_wait:
                logger.warning(
                    f"Error calculating dynamic wait time: {e_wait}. Using default."
                )
            assert (
                isinstance(wait_ms, int) and wait_ms >= 0
            ), f"Invalid wait_ms: {wait_ms}"
            pygame.time.wait(wait_ms)

        if self.is_active:
            self.deactivate()
        logger.info("Spectrometer screen loop finished.")
        return "QUIT" if g_shutdown_flag.is_set() else "BACK"

    def cleanup(self):
        logger.info("Cleaning up SpectrometerScreen resources...")
        if self.spectrometer:
            try:
                dev_proxy = getattr(self.spectrometer, "_dev", None)
                if dev_proxy and hasattr(dev_proxy, "is_open") and dev_proxy.is_open:
                    self.spectrometer.close()
                    logger.info(
                        f"Spectrometer {self.spectrometer.serial_number} closed."
                    )
            except Exception as e:
                logger.error(f"Error closing spectrometer: {e}", exc_info=True)
        self.spectrometer = None
        if self.plot_fig and plt and plt.fignum_exists(self.plot_fig.number):
            try:
                plt.close(self.plot_fig)
                logger.info("Matplotlib plot figure closed.")
            except Exception as e:
                logger.error(f"Error closing Matplotlib plot: {e}", exc_info=True)
        self.plot_fig = self.plot_ax = self.plot_line = None
        logger.info("SpectrometerScreen cleanup complete.")


# --- Splash Screen Function ---
def show_splash_screen(screen: pygame.Surface, display_hat_obj, duration_s: float):
    assert screen and isinstance(duration_s, (int, float)) and duration_s >= 0
    logger.info(f"Displaying splash screen for {duration_s:.1f} seconds...")
    img_final = None
    try:
        img_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "assets", "pysb-app.png"
        )
        if not os.path.isfile(img_path):
            logger.error(f"Splash image not found: {img_path}")
            time.sleep(min(duration_s, 2.0))
            return
        img_raw = pygame.image.load(img_path)
        assert isinstance(img_raw, pygame.Surface)
        is_dummy = os.environ.get("SDL_VIDEODRIVER") == "dummy"
        if not is_dummy and pygame.display.get_init() and pygame.display.get_surface():
            try:
                img_final = img_raw.convert()
                assert isinstance(img_final, pygame.Surface)
            except pygame.error as e_conv:
                logger.warning(f"Splash convert failed: {e_conv}. Using raw.")
                img_final = img_raw
        else:
            img_final = img_raw
    except Exception as e:
        logger.error(f"Error loading splash: {e}", exc_info=True)
        time.sleep(min(duration_s, 2.0))
        return

    if img_final:
        try:
            screen.fill(BLACK)
            img_rect = img_final.get_rect(center=screen.get_rect().center)
            screen.blit(img_final, img_rect)
            update_hardware_display(screen, display_hat_obj)
            wait_interval, num_intervals = 0.1, int(duration_s / 0.1)
            for _ in range(num_intervals):
                if g_shutdown_flag.is_set():
                    logger.info("Shutdown during splash.")
                    break
                time.sleep(wait_interval)
            logger.info("Splash screen finished.")
        except Exception as e:
            logger.error(f"Error displaying splash: {e}", exc_info=True)


# --- Disclaimer Screen Function ---
def show_disclaimer_screen(
    screen: pygame.Surface,
    display_hat_obj,
    button_handler: ButtonHandler,
    hint_font: pygame.font.Font,
):
    assert (
        screen
        and button_handler
        and hint_font
        and isinstance(hint_font, pygame.font.Font)
    )
    logger.info("Displaying disclaimer screen...")
    disc_font = None
    try:
        if not pygame.font.get_init():
            pygame.font.init()
        font_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "assets", MAIN_FONT_FILENAME
        )
        if not os.path.isfile(font_path):
            disc_font = pygame.font.SysFont(None, DISCLAIMER_FONT_SIZE)
            logger.error(f"Disclaimer font not found: {font_path}. Fallback.")
        else:
            try:
                disc_font = pygame.font.Font(font_path, DISCLAIMER_FONT_SIZE)
                logger.info(f"Loaded disclaimer font: {font_path}")
            except pygame.error as e_fload:
                disc_font = pygame.font.SysFont(None, DISCLAIMER_FONT_SIZE)
                logger.error(f"Failed font '{font_path}': {e_fload}. Fallback.")
        assert disc_font
    except Exception as e:
        logger.error(f"Error loading disclaimer font: {e}", exc_info=True)
        disc_font = (
            pygame.font.SysFont(None, DISCLAIMER_FONT_SIZE)
            if pygame.font.get_init()
            else None
        )
    if not disc_font:
        logger.error("No font for disclaimer.")
        return

    try:
        lines, rendered, max_w, total_h, l_space = (
            DISCLAIMER_TEXT.splitlines(),
            [],
            0,
            0,
            4,
        )
        for line_txt in lines:
            if line_txt.strip():
                surf = disc_font.render(line_txt, True, WHITE)
                rendered.append(surf)
                max_w, total_h = (
                    max(max_w, surf.get_width()),
                    total_h + surf.get_height() + l_space,
                )
            else:
                rendered.append(None)
                total_h += (disc_font.get_height() // 2) + l_space
        if total_h > 0:
            total_h -= l_space
        hint_surf = hint_font.render("Press A or B to continue...", True, YELLOW)
        total_h += hint_surf.get_height() + 10
        start_y = max(10, (screen.get_height() - total_h) // 2)
        screen.fill(BLACK)
        current_y = start_y
        for surf in rendered:
            if surf:
                screen.blit(
                    surf, surf.get_rect(centerx=screen.get_width() // 2, top=current_y)
                )
                current_y += surf.get_height() + l_space
            else:
                current_y += (disc_font.get_height() // 2) + l_space
        screen.blit(
            hint_surf,
            hint_surf.get_rect(centerx=screen.get_width() // 2, top=current_y + 10),
        )
        update_hardware_display(screen, display_hat_obj)
    except Exception as e:
        logger.error(f"Error drawing disclaimer: {e}", exc_info=True)
        return

    logger.info("Waiting for disclaimer acknowledgement...")
    acknowledged = False
    while not acknowledged and not g_shutdown_flag.is_set():
        if button_handler.process_pygame_events() == "QUIT":
            g_shutdown_flag.set()
            logger.warning("QUIT during disclaimer.")
            continue
        if button_handler.check_button(BTN_ENTER) or button_handler.check_button(
            BTN_BACK
        ):
            acknowledged = True
            logger.info("Disclaimer acknowledged.")
        pygame.time.wait(50)
    if not acknowledged:
        logger.warning("Exited disclaimer due to shutdown.")
    else:
        logger.info("Disclaimer screen finished.")


# --- Leak Warning Screen Function ---
def show_leak_warning_screen(
    screen: pygame.Surface, display_hat_obj, button_handler: ButtonHandler
):
    assert screen and button_handler
    logger.critical("Displaying LEAK WARNING screen!")
    font_l, font_s = None, None
    try:
        if not pygame.font.get_init():
            pygame.font.init()
        font_l, font_s = pygame.font.SysFont(None, 60), pygame.font.SysFont(None, 24)
        assert font_l and font_s
    except Exception as e:
        logger.error(f"Could not load fonts for leak warning: {e}")

    cx, cy = screen.get_width() // 2, screen.get_height() // 2
    last_blink, show_txt = time.monotonic(), True
    while g_leak_detected_flag.is_set() and not g_shutdown_flag.is_set():
        if button_handler.process_pygame_events() == "QUIT":
            g_shutdown_flag.set()
            break
        screen.fill(RED)
        if time.monotonic() - last_blink > 0.5:
            show_txt = not show_txt
            last_blink = time.monotonic()
        if show_txt and font_l and font_s:
            try:
                texts = [
                    ("! LEAK !", font_l, -30),
                    ("WATER DETECTED!", font_s, 20),
                    ("Press ANY btn to shutdown.", font_s, 50),
                ]
                for content, font, y_off in texts:
                    surf = font.render(content, True, YELLOW, RED)
                    screen.blit(surf, surf.get_rect(center=(cx, cy + y_off)))
            except Exception as e_render:
                logger.error(f"Error rendering leak text: {e_render}")
        update_hardware_display(screen, display_hat_obj)
        for btn_name in [BTN_UP, BTN_DOWN, BTN_ENTER, BTN_BACK]:
            if button_handler.check_button(btn_name):
                logger.warning(
                    f"Leak warning acknowledged by {btn_name}. Shutting down."
                )
                g_shutdown_flag.set()
                break
        if g_shutdown_flag.is_set():
            break
        pygame.time.wait(100)
    logger.info("Exiting leak warning screen.")
    return "QUIT"


# --- Signal Handling ---
def setup_signal_handlers(button_handler: ButtonHandler, network_info: NetworkInfo):
    assert button_handler and network_info

    def handler(sig, frame):
        if not g_shutdown_flag.is_set():
            logger.warning(f"Signal {sig}. Initiating shutdown...")
            g_shutdown_flag.set()
        else:
            logger.debug(f"Signal {sig} again, shutdown in progress.")

    try:
        signal.signal(signal.SIGINT, handler)
        signal.signal(signal.SIGTERM, handler)
        logger.info("Signal handlers set.")
    except Exception as e:
        logger.error(f"Failed to set signal handlers: {e}", exc_info=True)


# --- Helper Functions ---
def get_safe_datetime(year, month, day, hour=0, minute=0, second=0):
    assert all(isinstance(v, int) for v in [year, month, day, hour, minute, second])
    try:
        return datetime.datetime(
            year, max(1, min(12, month)), day, hour, minute, second
        )
    except ValueError as e:
        logger.warning(
            f"Invalid date/time: Y{year}-M{month}-D{day} H{hour}:M{minute}:S{second}. {e}"
        )
        return None


def update_hardware_display(
    screen: pygame.Surface, display_hat_obj
):  # display_hat_obj can be None
    assert screen is not None
    if USE_ADAFRUIT_PITFT:
        try:
            # Write pygame surface directly to framebuffer
            raw = pygame.image.tostring(screen, "RGB")

            # Convert RGB888 to RGB565 for the display
            rgb565_data = bytearray()
            for i in range(0, len(raw), 3):
                if i + 2 < len(raw):
                    r = raw[i] >> 3  # 5 bits
                    g = raw[i + 1] >> 2  # 6 bits
                    b = raw[i + 2] >> 3  # 5 bits
                    rgb565 = (r << 11) | (g << 5) | b
                    # Little endian for the display
                    rgb565_data.extend(rgb565.to_bytes(2, byteorder="little"))

            # Write to framebuffer
            with open("/dev/fb1", "wb") as fb:
                fb.write(rgb565_data)

        except Exception as e:
            logger.error(
                f"Error updating Adafruit PiTFT framebuffer: {e}", exc_info=True
            )
    elif USE_DISPLAY_HAT and display_hat_obj:  # This is the Pimoroni specific path
        try:
            # --- This is your existing Pimoroni DisplayHATMini logic ---
            assert (
                hasattr(display_hat_obj, "st7789")
                and hasattr(display_hat_obj.st7789, "set_window")
                and hasattr(display_hat_obj.st7789, "data")
            )
            # The Pimoroni HAT needs 180 deg rotation often
            rotated_surf = pygame.transform.rotate(screen, 180)

            # Convert to 16-bit format (RGB565) that ST7789 expects
            # The format 16,0 usually means RGB565.
            # Check if the surface needs conversion or if it's already in a compatible format.
            # If screen is already 16-bit, convert might not be needed or might be different.
            # For ST7789, RGB565 is common.
            pixel_data_pygame_format = rotated_surf.convert(16, 0).get_buffer()

            # Byte swap for ST7789 (MSB first for 16-bit color)
            # Pygame's buffer might be LSB first depending on system/pygame version.
            # This byte swap is typical for direct ST7789 interaction.
            px_bytes_swapped = bytearray(pixel_data_pygame_format)
            for i in range(0, len(px_bytes_swapped), 2):
                px_bytes_swapped[i], px_bytes_swapped[i + 1] = (
                    px_bytes_swapped[i + 1],
                    px_bytes_swapped[i],
                )

            display_hat_obj.st7789.set_window()  # Prepare ST7789 for data

            # Send data in chunks
            chunk_size = 4096
            for i in range(0, len(px_bytes_swapped), chunk_size):
                display_hat_obj.st7789.data(px_bytes_swapped[i : i + chunk_size])
            # --- End of Pimoroni Logic ---
        except Exception as e:
            logger.error(
                f"Error updating Display HAT Mini: {e}", exc_info=False
            )  # exc_info=False if too verbose
    else:  # Fallback for standard Pygame window (e.g., development on PC)
        try:
            if pygame.display.get_init() and pygame.display.get_surface():
                pygame.display.flip()
        except Exception as e:
            logger.error(
                f"Error updating Adafruit PiTFT (pygame.display.flip): {e}",
                exc_info=True,
            )


# --- Main Application ---
def main():
    logger.info(
        "=" * 44 + "\n   Underwater Spectrometer Controller Start \n" + "=" * 44
    )
    # Use USE_TEMP_SENSOR_IF_AVAILABLE for clarity in the log
    logger.info(
        f"Config: DH={USE_DISPLAY_HAT}, AdafruitTFT={USE_ADAFRUIT_PITFT}, GPIO={USE_GPIO_BUTTONS}, Hall={USE_HALL_EFFECT_BUTTONS}, Leak={USE_LEAK_SENSOR}, Spec={USE_SPECTROMETER}, TempSensorAttempt={USE_TEMP_SENSOR_IF_AVAILABLE}"
    )

    # Initialize display-related variables
    display_hat_active = False
    display_hat = None
    screen = None

    # Initialize hardware control objects
    mcp9808_physical_sensor = None  #
    temp_sensor_info = None
    net_info = None
    btn_handler = None
    menu_sys = None
    spec_screen = None
    clock = None
    spectrometer_hardware_ok = False

    try:
        # --- Pygame and Display Setup ---
        if USE_ADAFRUIT_PITFT:
            logger.info(
                "Configuring Pygame for Adafruit PiTFT (dummy SDL_VIDEODRIVER for manual fb write)..."
            )
            os.environ["SDL_VIDEODRIVER"] = "dummy"
            # Disable console cursor on fb1
            try:
                with open("/sys/class/graphics/fbcon/cursor_blink", "w") as f:
                    f.write("0")
                logger.info("Console cursor blink disabled")
            except Exception as e:
                logger.warning(f"Could not disable cursor blink: {e}")

            try:
                # Hide cursor on the framebuffer
                os.system("echo 0 > /sys/class/vtconsole/vtcon1/bind 2>/dev/null")
                logger.info("Console unbound from fb1")
            except Exception as e:
                logger.warning(f"Could not unbind console: {e}")

            pygame.init()
            assert pygame.get_init(), "Pygame (core) init failed"
            screen = pygame.Surface((SCREEN_WIDTH, SCREEN_HEIGHT))
            pygame.mouse.set_visible(False)
            logger.info(
                "Adafruit PiTFT: Pygame surface created for manual framebuffer writing."
            )
        elif USE_DISPLAY_HAT and DisplayHATMini_lib:
            if "SDL_VIDEODRIVER" not in os.environ:
                os.environ["SDL_VIDEODRIVER"] = "dummy"
            pygame.init()
            assert pygame.get_init(), "Pygame (core) init failed"
            screen = pygame.Surface((SCREEN_WIDTH, SCREEN_HEIGHT))
            display_hat = DisplayHATMini_lib(screen)
            display_hat_active = True
            logger.info(
                "DisplayHATMini initialized (likely with dummy SDL for Pygame surface)."
            )
        else:
            logger.info("Initializing standard Pygame display window...")
            if (
                "SDL_VIDEODRIVER" in os.environ
                and os.environ["SDL_VIDEODRIVER"] == "dummy"
            ):
                del os.environ["SDL_VIDEODRIVER"]
            pygame.init()
            assert pygame.get_init(), "Pygame (core) init failed"
            screen = pygame.display.set_mode((SCREEN_WIDTH, SCREEN_HEIGHT))
            pygame.display.set_caption("Spectrometer Menu")
            logger.info("Standard Pygame display window initialized.")

        assert screen, "Pygame screen/surface was not created."
        clock = pygame.time.Clock()
        assert clock, "Pygame clock failed to initialize."

        # --- Temperature Sensor Initialization ---
        if USE_TEMP_SENSOR_IF_AVAILABLE:
            if MCP9808_Driver:
                try:
                    logger.info(
                        "Attempting to initialize temperature sensor MCP9808..."
                    )
                    i2c_custom_wrapper = SMBus2Wrapper(busnum=TEMP_SENSOR_I2C_BUS)
                    mcp9808_physical_sensor_candidate = MCP9808_Driver(
                        address=TEMP_SENSOR_I2C_ADDR, i2c=i2c_custom_wrapper
                    )
                    if mcp9808_physical_sensor_candidate.begin():
                        mcp9808_physical_sensor = mcp9808_physical_sensor_candidate
                        logger.info(
                            "MCP9808 temperature sensor initialized successfully and detected."
                        )
                    else:
                        logger.warning(
                            "MCP9808 sensor begin() returned False. Sensor likely not connected or faulty."
                        )
                except Exception as e_temp_init:
                    logger.error(
                        f"Error initializing MCP9808 sensor hardware: {e_temp_init}",
                        exc_info=True,
                    )
            else:
                logger.warning(
                    "MCP9808_Driver class not available. Cannot attempt temperature sensor init."
                )
        else:
            logger.info(
                "Temperature sensor usage is disabled by USE_TEMP_SENSOR_IF_AVAILABLE=False."
            )

        temp_sensor_info = TempSensorInfo(mcp9808_physical_sensor)  # Always created

        # --- Initialize Core Components ---
        logger.info("Initializing core components...")
        net_info = NetworkInfo()
        btn_handler = ButtonHandler(display_hat if display_hat_active else None)
        menu_sys = MenuSystem(screen, btn_handler, net_info, temp_sensor_info)

        if USE_SPECTROMETER:  # Config flag says we want to use it
            # Instantiate SpectrometerScreen regardless of hardware status at this point.
            # It will manage its own state based on actual device connection.
            spec_screen = SpectrometerScreen(
                screen,
                btn_handler,
                menu_sys,
                display_hat if display_hat_active else None,
                temp_sensor_info,
            )
            assert spec_screen, "SpectrometerScreen failed to initialize"
            if display_hat_active:
                assert menu_sys  # menu_sys should exist at this point
                menu_sys.display_hat = display_hat  # Keep this for Pimoroni
                if (
                    spec_screen
                ):  # spec_screen might be None if USE_SPECTROMETER is False
                    spec_screen.display_hat = display_hat  # Keep this for Pimoroni

            # Check if the spectrometer hardware was actually initialized successfully within SpectrometerScreen
            # This assumes SpectrometerScreen._initialize_spectrometer_device() sets self.spectrometer
            if spec_screen.spectrometer and hasattr(
                spec_screen.spectrometer, "_dev"
            ):  # A basic check
                spectrometer_hardware_ok = True
                logger.info(
                    "Spectrometer hardware appears to be initialized within SpectrometerScreen."
                )
            else:
                logger.warning(
                    "Spectrometer hardware FAILED to initialize within SpectrometerScreen, or not found. Spectrometer operations will be limited."
                )
                spectrometer_hardware_ok = False  # Explicitly false
        else:
            logger.info("Spectrometer usage disabled by USE_SPECTROMETER=False config.")

        if not menu_sys.font:
            logger.critical(
                "Main font failed to load. UI will be impaired. Attempting to continue..."
            )
            # Application might be unusable but try to proceed for debugging.

        # --- Startup Screens (Splash, Disclaimer) ---
        show_splash_screen(
            screen, display_hat if display_hat_active else None, SPLASH_DURATION_S
        )
        if not g_shutdown_flag.is_set():
            if menu_sys.hint_font:
                show_disclaimer_screen(
                    screen,
                    display_hat if display_hat_active else None,
                    btn_handler,
                    menu_sys.hint_font,
                )
            else:
                logger.warning(
                    "Hint font not loaded, skipping disclaimer screen text render, but pausing."
                )
                time.sleep(3)
        if g_shutdown_flag.is_set():
            raise SystemExit("Shutdown during startup phase.")

        # --- Start Background Tasks ---
        logger.info("Setting up signal handlers and starting background tasks...")
        setup_signal_handlers(btn_handler, net_info)  # Assuming this is defined
        net_info.start_updates()
        temp_sensor_info.start_updates()  # TempSensorInfo handles if sensor is None

        # --- Main Application Loop ---
        logger.info("Starting main application loop...")
        current_scr_state = "MENU"
        while not g_shutdown_flag.is_set():
            # Handle global events like leak first
            if g_leak_detected_flag.is_set():
                logger.critical("Leak detected! Switching to leak warning.")
                leak_action = show_leak_warning_screen(
                    screen, display_hat if display_hat_active else None, btn_handler
                )
                # show_leak_warning_screen() itself is a loop.
                # It will set g_shutdown_flag if a button is pressed.
                if leak_action == "QUIT" or g_shutdown_flag.is_set():
                    if not g_shutdown_flag.is_set():  # Ensure flag is set
                        g_shutdown_flag.set()
                    logger.warning(
                        "Leak warning resulted in QUIT or shutdown flag set."
                    )
                    break  # Exit main loop to cleanup and exit
                # If leak somehow clears without shutdown, loop continues.

            # State-specific logic:
            if current_scr_state == "MENU":
                menu_action = (
                    menu_sys.handle_input()
                )  # Processes Pygame events, sets button states

                if menu_action == "QUIT":
                    g_shutdown_flag.set()
                    logger.info("Menu signaled QUIT.")
                elif menu_action == "START_CAPTURE":
                    if spec_screen:  # Check if spectrometer screen object exists
                        spec_screen.activate()  # Prepare spectrometer screen
                        current_scr_state = "SPECTROMETER"
                        logger.info("Transitioning to Spectrometer screen.")
                        if not spectrometer_hardware_ok:
                            logger.warning(
                                "Spectrometer hardware not ready, operations limited."
                            )
                        # The Spectrometer screen's loop will run in the next main loop iteration.
                    else:
                        logger.warning(
                            "START_CAPTURE selected, but SpectrometerScreen not configured (USE_SPECTROMETER=False)."
                        )
                        # Stay in MENU state, menu will be drawn below.

                # If still in MENU state (or returned to it) and not shutting down, draw the menu.
                # This ensures the menu is drawn every frame it's active.
                if current_scr_state == "MENU" and not g_shutdown_flag.is_set():
                    menu_sys.draw()  # This method calls update_hardware_display

            elif current_scr_state == "SPECTROMETER":
                assert (
                    spec_screen
                ), "In SPECTROMETER state, but spec_screen is None. This should not happen."

                # spec_screen.run_loop() is a blocking loop that handles its own input, drawing, and state.
                # It will only return "BACK" (to menu) or "QUIT".
                spec_status = spec_screen.run_loop()

                if spec_status == "QUIT":
                    logger.info("Spectrometer screen signaled QUIT.")
                    g_shutdown_flag.set()
                elif spec_status == "BACK":
                    logger.info("Returning to Menu from Spectrometer screen.")
                    current_scr_state = "MENU"
                    # The next main loop iteration will handle menu input and draw the menu.

            else:  # Should not be reached if states are managed correctly
                logger.error(f"FATAL: Unknown screen state '{current_scr_state}'")
                g_shutdown_flag.set()  # Force shutdown on unknown state

            # Regulate loop speed if not shutting down
            if not g_shutdown_flag.is_set():
                clock.tick(1.0 / MAIN_LOOP_DELAY_S)

    except SystemExit as e:
        logger.warning(f"Exiting due to SystemExit: {e}")
    except RuntimeError as e:
        logger.critical(f"RUNTIME ERROR in main: {e}", exc_info=True)
        g_shutdown_flag.set()
    except KeyboardInterrupt:
        logger.warning("KeyboardInterrupt. Initiating shutdown...")
        g_shutdown_flag.set()
    except Exception as e:
        logger.critical(f"FATAL UNHANDLED EXCEPTION in main: {e}", exc_info=True)
        g_shutdown_flag.set()
    finally:
        logger.warning("Initiating final cleanup...")
        if net_info:
            try:
                net_info.stop_updates()
            except Exception as e_ni:
                logger.error(f"Error stopping net_info: {e_ni}")
        if temp_sensor_info:
            try:
                temp_sensor_info.stop_updates()
            except Exception as e_ts:
                logger.error(f"Error stopping temp_sensor_info: {e_ts}")
        if spec_screen:
            try:
                spec_screen.cleanup()
            except Exception as e_ss:
                logger.error(f"Error cleaning spec_screen: {e_ss}")
        if menu_sys:
            try:
                menu_sys.cleanup()
            except Exception as e_ms:
                logger.error(f"Error cleaning menu_sys: {e_ms}")
        if btn_handler:
            try:
                btn_handler.cleanup()
            except Exception as e_bh:
                logger.error(f"Error cleaning btn_handler: {e_bh}")

        # Pygame quit should be robust
        if pygame.get_init():
            try:
                pygame.quit()
                logger.info("Pygame quit successfully.")
            except Exception as e_pq:
                logger.error(f"Error during pygame.quit(): {e_pq}")
        else:
            logger.info("Pygame not initialized, skipping quit.")

        logger.info("=" * 44 + "\n   Application Finished.\n" + "=" * 44)


if __name__ == "__main__":
    main()
