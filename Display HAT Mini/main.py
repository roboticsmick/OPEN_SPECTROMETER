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
- Spectrometer operations (Live view, capture - planned).
"""

import os
import sys
import time
import signal
import datetime
import subprocess
import threading
import logging
import RPi.GPIO
import io         # For in-memory plot rendering
import csv        # For future data saving
import numpy as np # Might need later for data manipulation

# --- Configuration Flags ---
# Set these flags based on the hardware connected.
# If a flag is True, the code will expect the hardware to be present and attempt initialization.
# If initialization fails despite the flag being True, an error will be logged.
USE_DISPLAY_HAT = True       # Set to True if Pimoroni Display HAT Mini is connected
USE_GPIO_BUTTONS = True      # Set to True if GPIO (LCD/Hall) buttons are connected
USE_HALL_EFFECT_BUTTONS = True # Set to True to map external Hall sensors (requires USE_GPIO_BUTTONS=True)
USE_LEAK_SENSOR = True        # Set to True if the external leak sensor is connected (requires USE_GPIO_BUTTONS=True)
USE_SPECTROMETER = True       # Set to True if the spectrometer is connected and should be used

# Attempt to import hardware-specific libraries only if configured
# RPi_GPIO defined globally for type hinting and conditional access
RPi_GPIO_lib = None
if USE_GPIO_BUTTONS:
    try:
        import RPi.GPIO as GPIO
        RPi_GPIO_lib = GPIO # Assign to global-like scope for use
        print("RPi.GPIO library loaded successfully.")
    except ImportError:
        print("ERROR: RPi.GPIO library not found, but USE_GPIO_BUTTONS is True.")
        print("GPIO features will be disabled.")
        USE_GPIO_BUTTONS = False # Disable GPIO usage if library fails
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
        USE_DISPLAY_HAT = False # Disable display usage if library fails

# --- Spectrometer and Plotting Libraries (Conditional Import) ---
sb = None
plt = None
Image = None # PIL/Pillow
Spectrometer = None # Specific class from seabreeze
usb = None

if USE_SPECTROMETER:
    try:
        # Set backend explicitly before importing pyplot
        import matplotlib
        matplotlib.use('Agg') # Use non-interactive backend suitable for rendering to buffer
        import matplotlib.pyplot as plt
        print("Matplotlib loaded successfully.")
        from PIL import Image # Pillow for image manipulation
        print("Pillow (PIL) loaded successfully.")

        import seabreeze
        seabreeze.use('pyseabreeze') # Or 'cseabreeze' if installed and preferred
        import seabreeze.spectrometers as sb
        from seabreeze.spectrometers import Spectrometer # Import the class directly

        try:
            import usb.core
        except ImportError:
            print("WARNING: pyusb library not found, cannot catch specific USBError.")
            # usb will remain None

        print("Seabreeze libraries loaded successfully.")
    except ImportError as e:
        print(f"ERROR: Spectrometer/Plotting library missing ({e}), but USE_SPECTROMETER is True.")
        print("Spectrometer features will be disabled.")
        USE_SPECTROMETER = False
        sb = None
        plt = None
        Image = None
        Spectrometer = None
        usb = None # Ensure it's None on import error
    except Exception as e:
        print(f"ERROR: Unexpected error loading Spectrometer/Plotting libraries: {e}")
        USE_SPECTROMETER = False
        sb = None
        plt = None
        Image = None
        Spectrometer = None
        usb = None # Ensure it's None on other errors


# Pygame is always needed for the display buffer and event loop
try:
    import pygame
except ImportError:
    print("FATAL ERROR: Pygame library not found. Cannot run.")
    sys.exit(1)

# --- Logging Setup ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- Global Variables ---
# These are managed primarily within classes or the main function after init
g_shutdown_flag = threading.Event() # Used to signal shutdown to threads and loops
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
# CSV_FILENAME = os.path.join(DATA_DIR, "spectra_log.csv") # Original single file
# Modified to use daily CSV files as per App Overview for SpectrometerScreen._save_data logic
# The CSV_FILENAME constant here might be a legacy if SpectrometerScreen handles its own daily naming.
# For now, keeping it as SpectrometerScreen seems to implement its own daily log path.
# If a global default log is needed, this should be clarified.
# The Application Overview states: "Saves to daily CSV: YYYY-MM-DD_log.csv" under SpectrometerScreen._save_data.
# This CSV_FILENAME global constant might be unused if all saving is via SpectrometerScreen.
# Re-evaluating: SpectrometerScreen currently uses a hardcoded "spectra_log.csv" within its daily folder.
# For consistency and to allow a configurable *base name*, let's use this global.
CSV_BASE_FILENAME = "spectra_log.csv" # Base name for the daily CSV file

PLOT_SAVE_DIR = DATA_DIR # Save plots in the same directory

# Lens Type Constants
LENS_TYPE_FIBER = "FIBER"
LENS_TYPE_CABLE = "CABLE"
LENS_TYPE_FIBER_CABLE = "FIBER+CABLE"
DEFAULT_LENS_TYPE = LENS_TYPE_FIBER

# Collection Mode Constants
MODE_RAW = "RAW"
MODE_RADIANCE = "RADIANCE" # Defined, but not used in AVAILABLE_COLLECTION_MODES for now
MODE_REFLECTANCE = "REFLECTANCE"

# Explicitly list available modes for the menu
AVAILABLE_COLLECTION_MODES = (MODE_RAW, MODE_REFLECTANCE)
# AVAILABLE_COLLECTION_MODES = (MODE_RAW, MODE_REFLECTANCE, MODE_RADIANCE) # Future
DEFAULT_COLLECTION_MODE = MODE_RAW # Default to RAW

# Ensure default is valid, fallback if not (though it should be with current setup)
if DEFAULT_COLLECTION_MODE not in AVAILABLE_COLLECTION_MODES:
    logger.warning(f"Default collection mode '{DEFAULT_COLLECTION_MODE}' is not in AVAILABLE_COLLECTION_MODES. Falling back.")
    if AVAILABLE_COLLECTION_MODES: # Check if list is not empty
        DEFAULT_COLLECTION_MODE = AVAILABLE_COLLECTION_MODES[0]
    else: # Should not happen, but as a very safe fallback
        DEFAULT_COLLECTION_MODE = MODE_RAW # Fallback to raw if list somehow empty
        AVAILABLE_COLLECTION_MODES = (MODE_RAW,)



# Integration Time (ms)
INTEGRATION_TIME_SCALE_FACTOR = 10.0 # Factor to multiply calculated microseconds by
DEFAULT_INTEGRATION_TIME_MS = 500
MIN_INTEGRATION_TIME_MS = 100
MAX_INTEGRATION_TIME_MS = 6000 # Increased max based on spectrometer
INTEGRATION_TIME_STEP_MS = 100

# Plotting Constants
USE_LIVE_SMOOTHING = True # Flag to enable/disable smoothing
LIVE_SMOOTHING_WINDOW_SIZE = 9
Y_AXIS_DEFAULT_MAX = 1000
Y_AXIS_RESCALE_FACTOR = 1.2
Y_AXIS_MIN_CEILING = 60
Y_AXIS_MIN_CEILING_RELATIVE = 1.1
# INTEGRATION_TIME_SCALE_FACTOR = 10.0 # Already defined above

# GPIO Pin Definitions (BCM Mode)
# --- Display HAT Mini Buttons (Corrected based on Pimoroni library standard) ---
PIN_DH_A = 5   # Was 16. Physical Button A (maps to Enter/Right logic) -> GPIO 5
PIN_DH_B = 6   # Was 6. Physical Button B (maps to Back/Left logic) -> GPIO 6
PIN_DH_X = 16  # Was 12. Physical Button X (maps to Up logic) -> GPIO 16
PIN_DH_Y = 24  # Was 13. Physical Button Y (maps to Down logic) -> GPIO 24

# --- External Hall Effect Sensor Pins (Check these carefully for your wiring) ---
PIN_HALL_UP = 20     # Mirrors DH X (Up)
PIN_HALL_DOWN = 21   # Mirrors DH Y (Down)
PIN_HALL_ENTER = 1  # Mirrors DH A (Enter/Right)
PIN_HALL_BACK = 12    # Mirrors DH B (Back/Left)

# --- Leak Sensor Pin ---
PIN_LEAK = 26 # Changed from 26 in original code to match user request

# Button Logical Names (used internally)
BTN_UP = 'up'
BTN_DOWN = 'down'
BTN_ENTER = 'enter'
BTN_BACK = 'back'

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

# Menu Layout
FONT_SIZE = 16
TITLE_FONT_SIZE = 22
HINT_FONT_SIZE = 15
DISCLAIMER_FONT_SIZE = 14
MENU_SPACING = 19
MENU_MARGIN_TOP = 44
MENU_MARGIN_LEFT = 12

# --- Font Filenames
TITLE_FONT_FILENAME = 'ChakraPetch-Medium.ttf'
MAIN_FONT_FILENAME = 'Roboto-Regular.ttf'
HINT_FONT_FILENAME = 'Roboto-Regular.ttf'
SPECTRO_FONT_FILENAME = 'Roboto-Regular.ttf'
SPECTRO_FONT_SIZE = 14

# Timing
DEBOUNCE_DELAY_S = 0.2  # Debounce time for buttons
NETWORK_UPDATE_INTERVAL_S = 10.0 # How often to check network status
MAIN_LOOP_DELAY_S = 0.03 # Target ~30 FPS
SPLASH_DURATION_S = 3.0 # Change to 6 for final
SPECTRO_LOOP_DELAY_S = 0.05 # Target ~20 FPS for spectrometer screen (adjust as needed)
SPECTRO_REFRESH_OVERHEAD_S = 0.05 # Add 50ms buffer to integration time for refresh delay

# --- Classes ---
class ButtonHandler:
    """
    Handles GPIO button inputs (Display HAT via library callback + optional Hall sensors/Leak)
    and maps Pygame key events, providing a unified button interface.
    """
    # Map Pimoroni HAT pins to our logical button names
    _DH_PIN_TO_BUTTON = {
        PIN_DH_A: BTN_ENTER,
        PIN_DH_B: BTN_BACK,
        PIN_DH_X: BTN_UP,
        PIN_DH_Y: BTN_DOWN
    }

    def __init__(self, display_hat_obj=None): # Pass the display_hat object
        """Initializes button states and debounce tracking."""
        # display_hat_obj can be None if USE_DISPLAY_HAT is False or init failed
        self.display_hat = display_hat_obj # Store the display_hat object

        # Determine operational status based on global flags and library availability
        self._gpio_available = USE_GPIO_BUTTONS and RPi_GPIO_lib is not None
        self._display_hat_buttons_enabled = USE_DISPLAY_HAT and self.display_hat is not None and DisplayHATMini_lib is not None
        self._hall_buttons_enabled = USE_HALL_EFFECT_BUTTONS and self._gpio_available
        self._leak_sensor_enabled = USE_LEAK_SENSOR and self._gpio_available

        # Button states are True if pressed *since last check*
        self._button_states = { btn: False for btn in [BTN_UP, BTN_DOWN, BTN_ENTER, BTN_BACK] }
        self._state_lock = threading.Lock() # Protect access from multiple threads (GPIO callbacks)

        self._last_press_time = { btn: 0.0 for btn in [BTN_UP, BTN_DOWN, BTN_ENTER, BTN_BACK] }

        # Map *only manually configured* GPIO pins to logical button names
        self._manual_pin_to_button: dict[int, str] = {}

        # Keep track of pins we manually set up for cleanup
        self._manual_gpio_pins_used: set[int] = set()

        if self._gpio_available or self._display_hat_buttons_enabled:
             self._setup_inputs()
        else:
            logger.warning("Neither GPIO nor Display HAT buttons are available/enabled. Only keyboard input will work.")

    def _setup_inputs(self):
        """Sets up GPIO for manual inputs and registers Display HAT callbacks."""
        logger.info("Setting up button/sensor inputs...")

        # --- GPIO Setup (Only if needed for Hall/Leak or if HAT is absent but GPIO enabled) ---
        if self._gpio_available and (self._hall_buttons_enabled or self._leak_sensor_enabled or not self._display_hat_buttons_enabled):
            try:
                # Avoid global cleanup if possible, but ensure mode is correct
                # Ensure mode is set only once if possible, or handle potential re-set warnings
                current_mode = RPi_GPIO_lib.getmode()
                if current_mode is None: # If mode not set yet
                    RPi_GPIO_lib.setmode(GPIO.BCM)
                    logger.info("  GPIO mode set to BCM.")
                elif current_mode != GPIO.BCM:
                    logger.warning(f"  GPIO mode was already set to {current_mode}, attempting to change to BCM.")
                    # RPi.GPIO might raise an error or warning if changing mode
                    try:
                         RPi_GPIO_lib.setmode(GPIO.BCM)
                    except RuntimeError as e:
                         logger.error(f"  Failed to change GPIO mode to BCM: {e}. Manual GPIO setup might fail.")
                         # Proceed cautiously, subsequent setup might fail
                # else: logger.debug("  GPIO mode already BCM.")

                RPi_GPIO_lib.setwarnings(False) # Suppress channel already in use warnings if needed

                # --- Hall Effect Buttons (Manual GPIO Setup) ---
                if self._hall_buttons_enabled:
                    logger.info("  Setting up Hall Effect sensor inputs via RPi.GPIO...")
                    hall_pins = {
                        PIN_HALL_UP: BTN_UP,
                        PIN_HALL_DOWN: BTN_DOWN,
                        PIN_HALL_ENTER: BTN_ENTER,
                        PIN_HALL_BACK: BTN_BACK,
                    }
                    # Assertion: Ensure pins are distinct (basic check)
                    assert len(hall_pins) == len(set(hall_pins.keys())), "Duplicate Hall Effect pin definitions"

                    # Loop bounded by number of hall pins (fixed)
                    for pin, name in hall_pins.items():
                         # Assertion: Check pin is integer
                         assert isinstance(pin, int), f"Hall pin {pin} must be an integer"
                         if pin in self._DH_PIN_TO_BUTTON:
                             logger.warning(f"  GPIO Pin {pin} for Hall sensor '{name}' conflicts with a Display HAT button pin!")
                         # Only setup pins not already potentially managed by HAT library if HAT is present
                         # (Though HAT library *should* handle its own pins exclusively)
                         if not (self._display_hat_buttons_enabled and pin in self._DH_PIN_TO_BUTTON):
                             RPi_GPIO_lib.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
                             RPi_GPIO_lib.add_event_detect(pin, GPIO.FALLING, callback=self._manual_gpio_callback, bouncetime=int(DEBOUNCE_DELAY_S * 1000))
                             self._manual_pin_to_button[pin] = name
                             self._manual_gpio_pins_used.add(pin) # Track for cleanup
                             logger.info(f"    Mapped Manual GPIO {pin} (Hall) to '{name}'")
                         else:
                             logger.warning(f"    Skipping manual setup for GPIO {pin} (Hall '{name}') as it's a Display HAT pin.")
                else:
                    logger.info("  Hall Effect button inputs disabled or GPIO unavailable.")

                # --- Leak Sensor (Manual GPIO Setup) ---
                if self._leak_sensor_enabled:
                    # Assertion: Check pin type
                    assert isinstance(PIN_LEAK, int), "Leak sensor pin must be an integer"
                    logger.info(f"  Setting up Leak sensor input on GPIO {PIN_LEAK} via RPi.GPIO...")
                    if PIN_LEAK in self._DH_PIN_TO_BUTTON:
                        logger.warning(f"  GPIO Pin {PIN_LEAK} for Leak sensor conflicts with a Display HAT button pin!")

                    if not (self._display_hat_buttons_enabled and PIN_LEAK in self._DH_PIN_TO_BUTTON):
                         RPi_GPIO_lib.setup(PIN_LEAK, GPIO.IN, pull_up_down=GPIO.PUD_UP)
                         # Use a longer bouncetime for leak sensor? Depends on sensor characteristics.
                         RPi_GPIO_lib.add_event_detect(PIN_LEAK, GPIO.FALLING, callback=self._leak_callback, bouncetime=1000) # g_leak_detected_flag is set here
                         self._manual_gpio_pins_used.add(PIN_LEAK) # Track for cleanup
                         logger.info(f"    Leak sensor event detection added on GPIO {PIN_LEAK}")
                    else:
                         logger.warning(f"    Skipping manual setup for GPIO {PIN_LEAK} (Leak) as it's a Display HAT pin.")
                else:
                    logger.info("  Leak sensor input disabled or GPIO unavailable.")

            except RuntimeError as e: # Catch specific GPIO errors
                 logger.error(f"RUNTIME ERROR setting up manual GPIO: {e}", exc_info=True)
                 logger.error("Manual GPIO setup FAILED. Check permissions, pin conflicts, and hardware.")
                 # Disable features reliant on manual GPIO if setup fails critically
                 self._hall_buttons_enabled = False
                 self._leak_sensor_enabled = False
                 self._manual_gpio_pins_used.clear() # Clear tracked pins as setup failed
            except Exception as e:
                 logger.error(f"UNEXPECTED EXCEPTION setting up manual GPIO: {e}", exc_info=True)
                 self._hall_buttons_enabled = False
                 self._leak_sensor_enabled = False
                 self._manual_gpio_pins_used.clear()

        # --- Display HAT Button Setup (using Library Callback) ---
        if self._display_hat_buttons_enabled:
            try:
                logger.info("  Registering Display HAT button callback...")
                # Assertion: Ensure the display_hat object has the required method
                assert self.display_hat is not None and hasattr(self.display_hat, 'on_button_pressed'), "Display HAT object is None or lacks 'on_button_pressed'"
                self.display_hat.on_button_pressed(self._display_hat_callback)
                logger.info("  Display HAT button callback registered successfully.")
            except AssertionError as ae:
                logger.error(f"Failed to register Display HAT callback prerequisite: {ae}")
                self._display_hat_buttons_enabled = False
            except Exception as e:
                logger.error(f"Failed to register Display HAT button callback: {e}", exc_info=True)
                self._display_hat_buttons_enabled = False # Mark as failed
        else:
            logger.info("  Display HAT buttons disabled or unavailable.")


    def _display_hat_callback(self, pin: int):
        """
        Internal callback for Display HAT button press events (triggered by the library).
        Handles debouncing logic internally based on last press time.
        """
        # Assertion: Ensure pin is int
        assert isinstance(pin, int), f"Invalid pin type received in DH callback: {type(pin)}"
        button_name = self._DH_PIN_TO_BUTTON.get(pin)
        if button_name is None:
            # logger.warning(f"Display HAT callback received for unmapped pin: {pin}") # Can be noisy
            return

        current_time = time.monotonic()
        # Lock to prevent race conditions with check_button or other callbacks
        with self._state_lock:
             last_press = self._last_press_time.get(button_name, 0.0)
             # Assertion: Check time calculation is valid
             assert current_time >= last_press, "Monotonic time decreased unexpectedly"
             if (current_time - last_press) > DEBOUNCE_DELAY_S:
                 self._button_states[button_name] = True
                 self._last_press_time[button_name] = current_time
                 logger.debug(f"Display HAT Button pressed: {button_name} (Pin {pin})")
             # else: logger.debug(f"Display HAT Button bounce ignored: {button_name}")


    def _manual_gpio_callback(self, channel: int):
        """
        Internal callback for manually configured GPIO events (Hall sensors).
        Handles debouncing.
        """
        # Assertion: Ensure channel is int
        assert isinstance(channel, int), f"Invalid channel type received in manual GPIO callback: {type(channel)}"
        button_name = self._manual_pin_to_button.get(channel)
        if button_name is None:
            # logger.warning(f"Manual GPIO callback received for unmapped channel: {channel}") # Can be noisy
            return

        current_time = time.monotonic()
        with self._state_lock:
             last_press = self._last_press_time.get(button_name, 0.0)
             # Assertion: Check time calculation is valid
             assert current_time >= last_press, "Monotonic time decreased unexpectedly"
             if (current_time - last_press) > DEBOUNCE_DELAY_S:
                 self._button_states[button_name] = True
                 self._last_press_time[button_name] = current_time
                 logger.debug(f"Manual GPIO Button pressed: {button_name} (Pin {channel})")
             # else: logger.debug(f"Manual GPIO Button bounce ignored: {button_name}")

    def _leak_callback(self, channel: int):
        """Callback function for leak detection GPIO event."""
        # Assertion: Check channel matches expected leak pin
        assert channel == PIN_LEAK, f"Leak callback triggered for unexpected channel {channel}"
        logger.critical(f"!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!")
        logger.critical(f"!!! WATER LEAK DETECTED on GPIO {channel} !!!")
        logger.critical(f"!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!")
        g_leak_detected_flag.set() # Set the global flag for main loop to handle
        # Optional: Immediately set g_shutdown_flag for emergency stop, but this might bypass some cleanup.
        # g_shutdown_flag.set()


    def check_button(self, button_name: str) -> bool:
        """
        Checks if a specific button was pressed since the last check.
        Resets the button state after checking. Returns True if pressed, False otherwise.
        """
        # Assertion: Check button name validity
        assert button_name in self._button_states, f"Invalid button name requested: {button_name}"
        pressed = False
        with self._state_lock:
            if self._button_states[button_name]:
                pressed = True
                self._button_states[button_name] = False # Consume the press event
        return pressed

    def process_pygame_events(self) -> str | None:
        """
        Processes Pygame events, mapping keys to button states.
        Returns "QUIT" if quit is requested, None otherwise.
        """
        # Assertion: Check Pygame is initialized
        assert pygame.get_init(), "Pygame not initialized when processing events"
        quit_requested = False
        try:
            # Get all events since last call. Limit loop iterations?
            # Pygame's internal event queue has limits, so this loop is implicitly bounded.
            events = pygame.event.get()

            # Loop is bounded by number of events in queue (finite)
            for event in events:
                if event.type == pygame.QUIT:
                    logger.info("Pygame QUIT event received.")
                    quit_requested = True
                    # Don't break, process other events in this batch if needed

                if event.type == pygame.KEYDOWN:
                    # Using a dict for mapping is clear and efficient
                    key_map = {
                        pygame.K_UP: BTN_UP,
                        pygame.K_w: BTN_UP, # Add WASD?
                        pygame.K_DOWN: BTN_DOWN,
                        pygame.K_s: BTN_DOWN,
                        pygame.K_RETURN: BTN_ENTER,
                        pygame.K_RIGHT: BTN_ENTER,
                        pygame.K_d: BTN_ENTER,
                        pygame.K_BACKSPACE: BTN_BACK,
                        pygame.K_LEFT: BTN_BACK,
                        pygame.K_a: BTN_BACK,
                        pygame.K_ESCAPE: "QUIT" # Map escape key to quit
                    }
                    button_name = key_map.get(event.key)

                    if button_name == "QUIT":
                        logger.info("Escape key pressed, requesting QUIT.")
                        quit_requested = True
                    elif button_name:
                         # Simulate immediate press for keys, no debounce needed here
                         # Lock needed as main loop reads this state
                         with self._state_lock:
                             self._button_states[button_name] = True
                         logger.debug(f"Key mapped to button press: {button_name}")
                    # else: logger.debug(f"Unmapped key pressed: {event.key}") # Optional debug

        except pygame.error as e:
             logger.error(f"Pygame error during event processing: {e}")
             # Decide if this is critical. Maybe request quit?
             # quit_requested = True
        except Exception as e:
             logger.error(f"Unexpected error during event processing: {e}", exc_info=True)
             # quit_requested = True


        return "QUIT" if quit_requested else None


    def cleanup(self):
        """Cleans up GPIO resources *only* for pins manually configured by this class."""
        if self._gpio_available and self._manual_gpio_pins_used:
            logger.info(f"Cleaning up manually configured GPIO pins: {list(self._manual_gpio_pins_used)}")
            try:
                # Clean up only the specific pins we added event detection to
                # Loop bounded by number of tracked pins (finite)
                for pin in self._manual_gpio_pins_used:
                     # Assertion: Check pin is int
                     assert isinstance(pin, int), f"Invalid pin type during cleanup: {type(pin)}"
                     # Attempt removal even if setup failed partially
                     try:
                         RPi_GPIO_lib.remove_event_detect(pin) # Remove detection first
                     except RuntimeError: # May occur if pin wasn't set up correctly
                         logger.warning(f"Could not remove event detect for pin {pin} during cleanup.")
                # Now cleanup the pins themselves
                RPi_GPIO_lib.cleanup(list(self._manual_gpio_pins_used))
                logger.info("Manual GPIO cleanup complete for specified pins.")
            except Exception as e:
                logger.error(f"Error during manual GPIO cleanup: {e}")
                # Fallback to general cleanup might be needed if specific cleanup fails
                # try: RPi_GPIO_lib.cleanup() except Exception as e2: logger.error(f"General GPIO cleanup failed: {e2}")
        else:
            logger.info("Manual GPIO cleanup skipped (no pins manually configured or GPIO unavailable).")
        # Note: Display HAT library resources are assumed to be managed by the library itself or Pygame exit

class NetworkInfo:
    """
    Handles retrieval of network information (WiFi SSID, IP Address).
    Runs network checks in a separate thread to avoid blocking the main UI loop.
    """
    _WLAN_IFACE = "wlan0" # Network interface to check

    def __init__(self):
        """Initializes network info placeholders and starts the update thread."""
        self._wifi_name = "Initializing..."
        self._ip_address = "Initializing..."
        self._lock = threading.Lock() # Protect access to shared state
        self._update_thread = None
        self._last_update_time = 0.0

        # Assertion: Ensure global shutdown flag exists and is correct type
        assert isinstance(g_shutdown_flag, threading.Event), "Global shutdown flag not initialized or incorrect type"

    def start_updates(self):
        """Starts the background thread to periodically update network info."""
        # Assertion: Ensure thread is not already running
        assert self._update_thread is None or not self._update_thread.is_alive(), "Network update thread already started"

        logger.info("Starting network info update thread.")
        self._update_thread = threading.Thread(target=self._network_update_loop, daemon=True)
        self._update_thread.start()

    def stop_updates(self):
        """Signals the update thread to stop and waits for it to join."""
        # Assertion: Check thread exists before joining
        if self._update_thread and self._update_thread.is_alive():
            logger.info("Waiting for network info update thread to stop...")
            # Signal is done via global g_shutdown_flag checked in the loop
            try:
                # Wait slightly longer than interval for thread to notice flag and exit
                self._update_thread.join(timeout=NETWORK_UPDATE_INTERVAL_S + 1.0)
                if self._update_thread.is_alive():
                     logger.warning("Network update thread did not terminate cleanly after timeout.")
            except Exception as e:
                logger.error(f"Error joining network update thread: {e}")
        else:
            logger.info("Network info update thread was not running or already stopped.")
        self._update_thread = None # Clear the thread object
        logger.info("Network info update thread stopped.")

    def get_wifi_name(self) -> str:
        """Returns the last known WiFi SSID."""
        # Assertion: Lock object must exist
        assert self._lock is not None, "NetworkInfo lock not initialized"
        with self._lock:
            # Assertion: Ensure return type is string
            assert isinstance(self._wifi_name, str), "Internal wifi_name state is not a string"
            return self._wifi_name

    def get_ip_address(self) -> str:
        """Returns the last known IP address."""
        # Assertion: Lock object must exist
        assert self._lock is not None, "NetworkInfo lock not initialized"
        with self._lock:
             # Assertion: Ensure return type is string
            assert isinstance(self._ip_address, str), "Internal ip_address state is not a string"
            return self._ip_address

    def _is_interface_up(self) -> bool:
        """Checks if the WLAN interface is operationally 'up'."""
        operstate_path = f"/sys/class/net/{self._WLAN_IFACE}/operstate"
        # Assertion: Ensure path is string
        assert isinstance(operstate_path, str), "Generated operstate path is not a string"
        try:
            if not os.path.exists(operstate_path): return False # Handle file not existing
            with open(operstate_path, 'r') as f:
                status = f.read(10).strip().lower() # Read limited bytes
                return status == 'up'
        except FileNotFoundError:
            # This is expected if interface doesn't exist, not an error
            # logger.debug(f"Network interface '{self._WLAN_IFACE}' not found.")
            return False
        except OSError as e: # Catch file system errors
            logger.error(f"OS error checking interface status for {self._WLAN_IFACE}: {e}")
            return False
        except Exception as e: # Catch other unexpected errors
            logger.error(f"Unexpected error checking interface status for {self._WLAN_IFACE}: {e}")
            return False

    def _fetch_wifi_name(self) -> str:
        """Uses 'iwgetid' to get the current SSID."""
        # Checks return code and output.
        if not self._is_interface_up():
            return "Not Connected"
        try:
            # Use timeout to prevent indefinite blocking
            command = ['iwgetid', '-r']
            result = subprocess.run(
                command,
                capture_output=True, text=True, check=False, timeout=5.0
            )
            # Assertion: Check result object type (subprocess.CompletedProcess)
            assert isinstance(result, subprocess.CompletedProcess), "subprocess.run did not return expected object"

            # Check return value
            if result.returncode == 0 and result.stdout and result.stdout.strip():
                return result.stdout.strip()
            else:
                # logger.debug(f"iwgetid failed or returned empty: code={result.returncode}, stdout='{result.stdout.strip()}'")
                return "Not Connected" # Treat failure or no SSID as not connected
        except FileNotFoundError:
             logger.error("'iwgetid' command not found. Cannot get WiFi name.")
             return "Error (No iwgetid)"
        except subprocess.TimeoutExpired:
             logger.warning("'iwgetid' command timed out.")
             return "Error (Timeout)"
        except Exception as e:
            logger.error(f"Error running iwgetid: {e}")
            return "Error (Exec)"

    def _fetch_ip_address(self) -> str:
        """Uses 'hostname -I' to get the IP address."""
        # Checks return code and output.
        if not self._is_interface_up():
            return "Not Connected"
        try:
             # Use timeout to prevent indefinite blocking
            command = ['hostname', '-I']
            result = subprocess.run(
                command,
                capture_output=True, text=True, check=False, timeout=5.0
            )
             # Assertion: Check result object type (subprocess.CompletedProcess)
            assert isinstance(result, subprocess.CompletedProcess), "subprocess.run did not return expected object"

             # Check return value
            if result.returncode == 0 and result.stdout and result.stdout.strip():
                # Return the first IP address if multiple are listed
                ip_list = result.stdout.strip().split()
                if ip_list:
                     # Assertion: Check first element is string
                     assert isinstance(ip_list[0], str), "IP address list element is not string"
                     return ip_list[0]
                else: return "No IP" # Command succeeded but no output?
            else:
                # logger.debug(f"hostname -I failed or returned empty: code={result.returncode}, stdout='{result.stdout.strip()}'")
                return "No IP" # Interface up, but no IP yet (e.g., DHCP ongoing)
        except FileNotFoundError:
             logger.error("'hostname' command not found. Cannot get IP address.")
             return "Error (No hostname)"
        except subprocess.TimeoutExpired:
             logger.warning("'hostname -I' command timed out.")
             return "Error (Timeout)"
        except Exception as e:
            logger.error(f"Error running hostname -I: {e}")
            return "Error (Exec)"

    def _network_update_loop(self):
        """Periodically updates network info until shutdown is signaled."""
        # Loop bound by external flag g_shutdown_flag
        logger.info("Network update loop started.")
        while not g_shutdown_flag.is_set():
            start_time = time.monotonic()
            new_wifi = "Error"
            new_ip = "Error"
            try:
                # Fetch new data
                new_wifi = self._fetch_wifi_name()
                new_ip = self._fetch_ip_address()

                # Assertion: Ensure fetched values are strings
                assert isinstance(new_wifi, str), "Fetched WiFi name is not string"
                assert isinstance(new_ip, str), "Fetched IP address is not string"

                # Update shared state safely
                with self._lock:
                    self._wifi_name = new_wifi
                    self._ip_address = new_ip

                self._last_update_time = time.monotonic()
                # logger.debug(f"Network info updated: WiFi='{new_wifi}', IP='{new_ip}'")

            except Exception as e:
                 logger.error(f"Error in network update loop: {e}", exc_info=True)
                 # Ensure state reflects error if update fails mid-way
                 with self._lock:
                      # Ensure values remain strings even on error path
                      self._wifi_name = str(new_wifi)
                      self._ip_address = str(new_ip)
                 # Continue loop despite error

            # Calculate remaining time and wait
            elapsed_time = time.monotonic() - start_time
            wait_time = max(0, NETWORK_UPDATE_INTERVAL_S - elapsed_time)
            # Assertion: Ensure wait time is non-negative float or int
            assert isinstance(wait_time, (float, int)) and wait_time >= 0, f"Invalid wait time calculated: {wait_time}"
            # Use wait_for to be responsive to the shutdown flag
            g_shutdown_flag.wait(timeout=wait_time)

        logger.info("Network update loop finished.")

class MenuSystem:
    """
    Manages the main menu UI, state, and interactions.
    """
    # Define menu items and their corresponding actions or edit types
    MENU_ITEM_CAPTURE = "LOG SPECTRA"
    MENU_ITEM_INTEGRATION = "INTEGRATION TIME"
    MENU_ITEM_COLLECTION_MODE = "COLLECTION MODE"
    MENU_ITEM_LENS_TYPE = "LENS TYPE"
    MENU_ITEM_DATE = "DATE"
    MENU_ITEM_TIME = "TIME"
    MENU_ITEM_WIFI = "WIFI"
    MENU_ITEM_IP = "IP"

    EDIT_TYPE_NONE = 0
    EDIT_TYPE_INTEGRATION = 1
    EDIT_TYPE_DATE = 2
    EDIT_TYPE_TIME = 3
    EDIT_TYPE_COLLECTION_MODE = 4
    EDIT_TYPE_LENS_TYPE = 5

    # Fields for date/time editing
    FIELD_YEAR = 'year'
    FIELD_MONTH = 'month'
    FIELD_DAY = 'day'
    FIELD_HOUR = 'hour'
    FIELD_MINUTE = 'minute'

    # Use the global AVAILABLE_COLLECTION_MODES to define modes for this menu instance
    COLLECTION_MODES = AVAILABLE_COLLECTION_MODES

    LENS_TYPES = (LENS_TYPE_FIBER, LENS_TYPE_CABLE, LENS_TYPE_FIBER_CABLE)

    def __init__(self, screen: pygame.Surface, button_handler: ButtonHandler, network_info: NetworkInfo):
        """
        Initializes the menu system with display, input, and network info sources.
        """
        # Assertions for required dependencies
        assert screen is not None, "Pygame screen object is required for MenuSystem"
        assert button_handler is not None, "ButtonHandler object is required for MenuSystem"
        assert network_info is not None, "NetworkInfo object is required for MenuSystem"

        self.screen = screen
        self.button_handler = button_handler
        self.network_info = network_info
        self.display_hat = None  # To be set externally if available

        # --- Application State ---
        self._integration_time_ms = DEFAULT_INTEGRATION_TIME_MS

        # Collection Mode State
        # self.COLLECTION_MODES is now (MODE_RAW, MODE_REFLECTANCE) due to class attribute
        assert len(self.COLLECTION_MODES) > 0, "COLLECTION_MODES cannot be empty for MenuSystem"
        try:
             self._collection_mode_idx = self.COLLECTION_MODES.index(DEFAULT_COLLECTION_MODE)
        except ValueError:
             logger.warning(f"Default collection mode '{DEFAULT_COLLECTION_MODE}' not found in this MenuSystem's modes list '{self.COLLECTION_MODES}'. Defaulting to index 0.")
             self._collection_mode_idx = 0
        self._collection_mode = self.COLLECTION_MODES[self._collection_mode_idx]
        assert 0 <= self._collection_mode_idx < len(self.COLLECTION_MODES), "Initial collection mode index out of bounds"
        assert isinstance(self._collection_mode, str) and self._collection_mode in self.COLLECTION_MODES, "Initial collection mode is invalid"

        # Lens Type State
        try:
            self._lens_type_idx = self.LENS_TYPES.index(DEFAULT_LENS_TYPE)
        except ValueError:
            logger.warning(f"Default lens type '{DEFAULT_LENS_TYPE}' not found in types list. Defaulting to index 0.")
            self._lens_type_idx = 0
        self._lens_type = self.LENS_TYPES[self._lens_type_idx]
        assert 0 <= self._lens_type_idx < len(self.LENS_TYPES), "Initial lens type index out of bounds"
        assert isinstance(self._lens_type, str) and self._lens_type in self.LENS_TYPES, "Initial lens type is invalid"

        # Store offset from system time, not an absolute editable time
        self._time_offset = datetime.timedelta(0)
        self._original_offset_on_edit_start: datetime.timedelta | None = None
        self._datetime_being_edited: datetime.datetime | None = None

        # --- Menu Structure ---
        self._menu_items = (
            (self.MENU_ITEM_CAPTURE, self.EDIT_TYPE_NONE),
            (self.MENU_ITEM_INTEGRATION, self.EDIT_TYPE_INTEGRATION),
            (self.MENU_ITEM_COLLECTION_MODE, self.EDIT_TYPE_COLLECTION_MODE),
            (self.MENU_ITEM_LENS_TYPE, self.EDIT_TYPE_LENS_TYPE),
            (self.MENU_ITEM_DATE, self.EDIT_TYPE_DATE),
            (self.MENU_ITEM_TIME, self.EDIT_TYPE_TIME),
            (self.MENU_ITEM_WIFI, self.EDIT_TYPE_NONE),
            (self.MENU_ITEM_IP, self.EDIT_TYPE_NONE),
        )
        self._current_selection_idx = 0
        assert len(self._menu_items) > 0, "Menu items list cannot be empty"

        # --- Editing State ---
        self._is_editing = False
        self._editing_field: str | None = None

        # --- Font Initialization ---
        self.font: pygame.font.Font | None = None
        self.title_font: pygame.font.Font | None = None
        self.hint_font: pygame.font.Font | None = None
        self._value_start_offset_x = 120 # Default fallback

        self._load_fonts()
        if self.font:
             assert self.font is not None, "Main font should be loaded before calculating offset"
             self._calculate_value_offset()
        else:
             logger.error("Main font failed to load; cannot calculate value offset for menu items.")


    def _load_fonts(self):
        """Loads fonts from the assets folder. Uses global constants for filenames."""
        try:
            if not pygame.font.get_init():
                logger.info("Initializing Pygame font module.")
                pygame.font.init()
            assert pygame.font.get_init(), "Pygame font module failed to initialize"

            logger.info("Loading fonts from assets folder...")
            script_dir = os.path.dirname(os.path.abspath(__file__))
            assets_dir = os.path.join(script_dir, 'assets')

            title_font_path = os.path.join(assets_dir, TITLE_FONT_FILENAME)
            main_font_path = os.path.join(assets_dir, MAIN_FONT_FILENAME)
            hint_font_path = os.path.join(assets_dir, HINT_FONT_FILENAME)

            assert isinstance(title_font_path, str), "Title font path is not a string"
            assert isinstance(main_font_path, str), "Main font path is not a string"
            assert isinstance(hint_font_path, str), "Hint font path is not a string"

            try:
                if not os.path.isfile(title_font_path):
                    logger.error(f"Title font file not found: '{title_font_path}'. Using fallback.")
                    self.title_font = pygame.font.SysFont(None, TITLE_FONT_SIZE)
                else:
                    self.title_font = pygame.font.Font(title_font_path, TITLE_FONT_SIZE)
                    logger.info(f"Loaded title font: {title_font_path}")
            except pygame.error as e:
                logger.error(f"Failed to load title font '{title_font_path}' using Pygame: {e}. Using fallback.")
                self.title_font = pygame.font.SysFont(None, TITLE_FONT_SIZE)
            except Exception as e:
                logger.error(f"Unexpected error loading title font '{title_font_path}': {e}. Using fallback.", exc_info=True)
                self.title_font = pygame.font.SysFont(None, TITLE_FONT_SIZE)

            try:
                if not os.path.isfile(main_font_path):
                     logger.error(f"Main font file not found: '{main_font_path}'. Using fallback.")
                     self.font = pygame.font.SysFont(None, FONT_SIZE)
                else:
                    self.font = pygame.font.Font(main_font_path, FONT_SIZE)
                    logger.info(f"Loaded main font: {main_font_path}")
            except pygame.error as e:
                logger.error(f"Failed to load main font '{main_font_path}' using Pygame: {e}. Using fallback.")
                self.font = pygame.font.SysFont(None, FONT_SIZE)
            except Exception as e:
                logger.error(f"Unexpected error loading main font '{main_font_path}': {e}. Using fallback.", exc_info=True)
                self.font = pygame.font.SysFont(None, FONT_SIZE)

            try:
                if not os.path.isfile(hint_font_path):
                     logger.error(f"Hint font file not found: '{hint_font_path}'. Using fallback.")
                     self.hint_font = pygame.font.SysFont(None, HINT_FONT_SIZE)
                else:
                    self.hint_font = pygame.font.Font(hint_font_path, HINT_FONT_SIZE)
                    logger.info(f"Loaded hint font: {hint_font_path}")
            except pygame.error as e:
                logger.error(f"Failed to load hint font '{hint_font_path}' using Pygame: {e}. Using fallback.")
                self.hint_font = pygame.font.SysFont(None, HINT_FONT_SIZE)
            except Exception as e:
                logger.error(f"Unexpected error loading hint font '{hint_font_path}': {e}. Using fallback.", exc_info=True)
                self.hint_font = pygame.font.SysFont(None, HINT_FONT_SIZE)

            if not self.font:
                 logger.critical("Essential main font failed to load, even with fallbacks. Menu will likely fail.")
            assert isinstance(self.title_font, (pygame.font.Font, type(None))), "Title font is invalid type"
            assert isinstance(self.font, (pygame.font.Font, type(None))), "Main font is invalid type"
            assert isinstance(self.hint_font, (pygame.font.Font, type(None))), "Hint font is invalid type"

        except Exception as e:
            logger.critical(f"Critical error during Pygame font initialization/loading: {e}", exc_info=True)
            self.font = None
            self.title_font = None
            self.hint_font = None

    def _calculate_value_offset(self):
        """Calculates the X offset for aligned value display based on label widths."""
        assert self.font is not None, "Cannot calculate value offset without main font."
        try:
            max_label_width = 0
            label_prefixes = {
                self.MENU_ITEM_INTEGRATION: "INTEGRATION:",
                self.MENU_ITEM_COLLECTION_MODE: "MODE:",
                self.MENU_ITEM_LENS_TYPE: "LENS TYPE:", # <<< NEW
                self.MENU_ITEM_DATE: "DATE:",
                self.MENU_ITEM_TIME: "TIME:",
                self.MENU_ITEM_WIFI: "WIFI:",
                self.MENU_ITEM_IP: "IP:"
            }

            for item_text, _ in self._menu_items:
                 prefix = label_prefixes.get(item_text)
                 if prefix:
                      assert isinstance(prefix, str), f"Label prefix for {item_text} is not a string"
                      label_width = self.font.size(prefix)[0]
                      max_label_width = max(max_label_width, label_width)

            label_gap = 8
            assert isinstance(max_label_width, (int, float)), "Max label width calculation failed"
            self._value_start_offset_x = int(max_label_width + label_gap)
            logger.info(f"Calculated value start offset X: {self._value_start_offset_x} (based on max label width {max_label_width})")

        except Exception as e:
            logger.error(f"Failed to calculate value start offset: {e}. Using default fallback {self._value_start_offset_x}.")
            self._value_start_offset_x = 120

    def _get_current_app_display_time(self) -> datetime.datetime:
        """Calculates the current time including the user-defined offset."""
        assert isinstance(self._time_offset, datetime.timedelta), "Time offset is not a timedelta object"
        try:
            now = datetime.datetime.now()
            assert isinstance(now, datetime.datetime), "datetime.now() returned unexpected type"
            app_time = now + self._time_offset
            assert isinstance(app_time, datetime.datetime), "Time calculation resulted in unexpected type"
            return app_time
        except OverflowError:
            logger.warning("Time offset resulted in datetime overflow. Resetting offset.")
            self._time_offset = datetime.timedelta(0)
            assert self._time_offset == datetime.timedelta(0), "Offset reset failed"
            return datetime.datetime.now()

    def get_integration_time_ms(self) -> int:
        """Returns the currently configured integration time in milliseconds."""
        assert isinstance(self._integration_time_ms, int), "Internal integration time is not int"
        return self._integration_time_ms

    def get_timestamp_datetime(self) -> datetime.datetime:
        """Returns a datetime object representing the current app time (System + Offset)."""
        dt = self._get_current_app_display_time()
        assert isinstance(dt, datetime.datetime), "get_current_app_display_time returned invalid type"
        return dt

    def get_collection_mode(self) -> str:
        """Returns the currently selected collection mode string."""
        assert self._collection_mode in self.COLLECTION_MODES, "Internal collection mode state is invalid"
        return self._collection_mode

    def get_lens_type(self) -> str: # <<< NEW
        """Returns the currently selected lens type string."""
        assert self._lens_type in self.LENS_TYPES, "Internal lens type state is invalid"
        return self._lens_type

    def handle_input(self) -> str | None:
        """
        Processes button inputs based on the current menu state (navigation or editing).
        Returns "QUIT" to signal application exit, "CAPTURE" to start capture, or None otherwise.
        """
        pygame_event_result = self.button_handler.process_pygame_events()
        if pygame_event_result == "QUIT":
            assert pygame_event_result == "QUIT", "process_pygame_events returned unexpected value"
            return "QUIT"

        action = None
        if self._is_editing:
            assert 0 <= self._current_selection_idx < len(self._menu_items), "Invalid index for editing state check"
            item_text, edit_type = self._menu_items[self._current_selection_idx]
            # Lens type and collection mode also don't use _editing_field
            assert (self._editing_field is not None or edit_type not in [self.EDIT_TYPE_DATE, self.EDIT_TYPE_TIME]), \
                   f"Editing state inconsistent: _editing_field is None for type {edit_type}"
            action = self._handle_editing_input()
        else:
            assert not self._is_editing and self._editing_field is None, "Navigation input called while editing state is inconsistent"
            action = self._handle_navigation_input()

        assert isinstance(action, (str, type(None))), "Input handler returned unexpected type"

        if action == "EXIT_EDIT_SAVE":
            self._is_editing = False
            self._editing_field = None
            if self._datetime_being_edited is not None:
                 assert isinstance(self._datetime_being_edited, datetime.datetime), "Cannot commit invalid datetime object"
                 self._commit_time_offset_changes()
            self._datetime_being_edited = None
            self._original_offset_on_edit_start = None
            logger.info("Exited editing mode, changes saved (if any).")
            return None
        elif action == "EXIT_EDIT_DISCARD":
            self._is_editing = False
            self._editing_field = None
            if self._original_offset_on_edit_start is not None:
                assert isinstance(self._original_offset_on_edit_start, datetime.timedelta), "Cannot restore invalid offset type"
                self._time_offset = self._original_offset_on_edit_start
                logger.info("Exited editing mode, time offset changes discarded.")
            else:
                logger.debug("Exited editing mode via BACK (discard), no original offset to revert (expected for Integ/Mode/Lens).")
            self._datetime_being_edited = None
            self._original_offset_on_edit_start = None
            logger.info("Exited editing mode (Discard).")
            return None
        elif action == "START_CAPTURE":
            logger.info("Capture action triggered.")
            return "START_CAPTURE"
        elif action == "QUIT":
            logger.warning("QUIT action returned unexpectedly from input handler.")
            return "QUIT"
        else:
            return None

    def draw(self):
        """Draws the complete menu screen."""
        assert self.font, "Main font not loaded, cannot draw menu items."
        assert self.title_font, "Title font not loaded, cannot draw title."
        assert self.hint_font, "Hint font not loaded, cannot draw hints."
        assert self.screen is not None, "Screen surface not available for drawing."

        try:
            self.screen.fill(BLACK)
            self._draw_title()
            self._draw_menu_items()
            self._draw_hints()
            update_hardware_display(self.screen, self.display_hat)
        except pygame.error as e:
             logger.error(f"Pygame error during drawing: {e}", exc_info=True)
        except Exception as e:
             logger.error(f"Unexpected error during drawing: {e}", exc_info=True)

    def cleanup(self):
        """Performs any cleanup needed by the menu system."""
        logger.info("MenuSystem cleanup completed (no specific actions needed).")
        pass

    def _handle_navigation_input(self) -> str | None:
        """ Handles UP/DOWN/ENTER/BACK when in navigation mode. """
        assert not self._is_editing, "Navigation input called while editing"
        action = None
        if self.button_handler.check_button(BTN_UP):
            self._navigate_menu(-1)
        elif self.button_handler.check_button(BTN_DOWN):
            self._navigate_menu(1)
        elif self.button_handler.check_button(BTN_ENTER):
            action = self._select_menu_item()
        elif self.button_handler.check_button(BTN_BACK):
            logger.info("BACK pressed in main menu (no action defined).")
            pass
        assert isinstance(action, (str, type(None))), "Navigation input returning invalid type"
        return action

    def _handle_editing_input(self) -> str | None:
        """ Handles UP/DOWN/ENTER/BACK when editing a value. """
        assert self._is_editing, "Editing input called while not editing"
        assert 0 <= self._current_selection_idx < len(self._menu_items), "Invalid menu selection index"
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

        assert isinstance(action, (str, type(None))), "Editing input returning invalid type"
        return action

    def _navigate_menu(self, direction: int):
        """Updates the current menu selection index, wrapping around."""
        assert direction in [-1, 1], f"Invalid navigation direction: {direction}"
        num_items = len(self._menu_items)
        assert num_items > 0, "Menu has no items"
        self._current_selection_idx = (self._current_selection_idx + direction) % num_items
        assert 0 <= self._current_selection_idx < num_items, "Menu index out of bounds after navigation"
        logger.debug(f"Menu navigated. New selection index: {self._current_selection_idx}, Item: {self._menu_items[self._current_selection_idx][0]}")

    def _select_menu_item(self) -> str | None:
        """ Handles the ENTER action in navigation mode (Starts editing or action). """
        assert 0 <= self._current_selection_idx < len(self._menu_items), "Invalid menu selection index"
        item_text, edit_type = self._menu_items[self._current_selection_idx]
        logger.info(f"Menu item selected: {item_text}")
        action_result = None

        if item_text == self.MENU_ITEM_CAPTURE:
            if USE_SPECTROMETER:
                logger.info("Triggering spectrometer capture screen.")
                action_result = "START_CAPTURE"
            else:
                logger.warning("Capture Spectra selected, but USE_SPECTROMETER is False.")
                action_result = None
        # <<< MODIFIED: Include LENS_TYPE in editable items >>>
        elif edit_type in [self.EDIT_TYPE_INTEGRATION, self.EDIT_TYPE_COLLECTION_MODE, self.EDIT_TYPE_LENS_TYPE, self.EDIT_TYPE_DATE, self.EDIT_TYPE_TIME]:
            logger.info(f"Entering edit mode for: {item_text}")
            self._is_editing = True

            if edit_type in [self.EDIT_TYPE_DATE, self.EDIT_TYPE_TIME]:
                self._original_offset_on_edit_start = self._time_offset
                self._datetime_being_edited = self._get_current_app_display_time()
                assert self._datetime_being_edited is not None, "Failed to get current app time for editing"
            else:
                 self._original_offset_on_edit_start = None
                 self._datetime_being_edited = None

            if edit_type == self.EDIT_TYPE_DATE:
                self._editing_field = self.FIELD_YEAR
                logger.debug(f"Starting edit: Date (Initial: {self._datetime_being_edited.strftime('%Y-%m-%d')}, Field: Year)")
            elif edit_type == self.EDIT_TYPE_TIME:
                self._editing_field = self.FIELD_HOUR
                logger.debug(f"Starting edit: Time (Initial: {self._datetime_being_edited.strftime('%H:%M')}, Field: Hour)")
            elif edit_type == self.EDIT_TYPE_INTEGRATION:
                 self._editing_field = None
                 logger.debug(f"Starting edit: Integration Time (Current: {self._integration_time_ms} ms)")
            elif edit_type == self.EDIT_TYPE_COLLECTION_MODE:
                 self._editing_field = None
                 logger.debug(f"Starting edit: Collection Mode (Current: {self._collection_mode})")
            elif edit_type == self.EDIT_TYPE_LENS_TYPE: # <<< NEW
                 self._editing_field = None
                 logger.debug(f"Starting edit: Lens Type (Current: {self._lens_type})")
            action_result = None
        elif item_text in [self.MENU_ITEM_WIFI, self.MENU_ITEM_IP]:
             logger.info(f"Selected read-only item: {item_text}")
             action_result = None
        else:
             logger.warning(f"Selected menu item '{item_text}' with unknown type/action: {edit_type}")
             action_result = None
        assert isinstance(action_result, (str, type(None))), "Select menu item returning invalid type"
        return action_result

    def _handle_edit_adjust(self, edit_type: int, delta: int):
        """ Adjusts the value of the currently edited item. """
        assert self._is_editing, "Adjust called when not editing"
        assert delta in [-1, 1], f"Invalid adjustment delta: {delta}"
        # <<< MODIFIED: Include LENS_TYPE in valid edit types >>>
        assert edit_type in [self.EDIT_TYPE_INTEGRATION, self.EDIT_TYPE_COLLECTION_MODE, self.EDIT_TYPE_LENS_TYPE, self.EDIT_TYPE_DATE, self.EDIT_TYPE_TIME], f"Invalid edit type for adjustment: {edit_type}"

        if edit_type == self.EDIT_TYPE_INTEGRATION:
            current_val = self._integration_time_ms
            step = INTEGRATION_TIME_STEP_MS
            new_val = current_val + delta * step
            clamped_val = max(MIN_INTEGRATION_TIME_MS, min(new_val, MAX_INTEGRATION_TIME_MS))
            assert MIN_INTEGRATION_TIME_MS <= clamped_val <= MAX_INTEGRATION_TIME_MS, "Integration time out of bounds after clamp"
            self._integration_time_ms = clamped_val
            logger.debug(f"Integration time adjusted to {self._integration_time_ms} ms")
        elif edit_type == self.EDIT_TYPE_COLLECTION_MODE:
             num_modes = len(self.COLLECTION_MODES)
             assert num_modes > 0, "Collection modes list is empty"
             new_idx = (self._collection_mode_idx + delta) % num_modes
             assert 0 <= new_idx < num_modes, "Calculated collection mode index is out of bounds"
             self._collection_mode_idx = new_idx
             self._collection_mode = self.COLLECTION_MODES[new_idx]
             logger.debug(f"Collection mode changed to: {self._collection_mode}")
        elif edit_type == self.EDIT_TYPE_LENS_TYPE: # <<< NEW
             num_types = len(self.LENS_TYPES)
             assert num_types > 0, "Lens types list is empty"
             new_idx = (self._lens_type_idx + delta) % num_types
             assert 0 <= new_idx < num_types, "Calculated lens type index is out of bounds"
             self._lens_type_idx = new_idx
             self._lens_type = self.LENS_TYPES[new_idx]
             logger.debug(f"Lens type changed to: {self._lens_type}")
        elif edit_type == self.EDIT_TYPE_DATE:
             assert self._datetime_being_edited is not None, "Cannot adjust Date, _datetime_being_edited is None"
             self._change_date_field(delta)
        elif edit_type == self.EDIT_TYPE_TIME:
             assert self._datetime_being_edited is not None, "Cannot adjust Time, _datetime_being_edited is None"
             self._change_time_field(delta)

    def _handle_edit_next_field(self, edit_type: int) -> str | None:
        """ Moves to the next editable field, or returns 'EXIT_EDIT_SAVE' if done. """
        assert self._is_editing
        # <<< MODIFIED: Include LENS_TYPE in valid edit types >>>
        assert edit_type in [self.EDIT_TYPE_INTEGRATION, self.EDIT_TYPE_COLLECTION_MODE, self.EDIT_TYPE_LENS_TYPE, self.EDIT_TYPE_DATE, self.EDIT_TYPE_TIME], f"Invalid edit type for next field: {edit_type}"
        action_result = None

        if edit_type == self.EDIT_TYPE_INTEGRATION:
            logger.debug("Finished editing Integration Time.")
            action_result = "EXIT_EDIT_SAVE"
        elif edit_type == self.EDIT_TYPE_COLLECTION_MODE:
            logger.debug("Finished editing Collection Mode.")
            action_result = "EXIT_EDIT_SAVE"
        elif edit_type == self.EDIT_TYPE_LENS_TYPE: # <<< NEW
            logger.debug("Finished editing Lens Type.")
            action_result = "EXIT_EDIT_SAVE"
        elif edit_type == self.EDIT_TYPE_DATE:
            assert self._editing_field in [self.FIELD_YEAR, self.FIELD_MONTH, self.FIELD_DAY], f"Invalid date field '{self._editing_field}'"
            if self._editing_field == self.FIELD_YEAR:
                self._editing_field = self.FIELD_MONTH
                logger.debug("Editing next field: Month")
            elif self._editing_field == self.FIELD_MONTH:
                self._editing_field = self.FIELD_DAY
                logger.debug("Editing next field: Day")
            elif self._editing_field == self.FIELD_DAY:
                logger.debug("Finished editing Date fields.")
                action_result = "EXIT_EDIT_SAVE"
        elif edit_type == self.EDIT_TYPE_TIME:
            assert self._editing_field in [self.FIELD_HOUR, self.FIELD_MINUTE], f"Invalid time field '{self._editing_field}'"
            if self._editing_field == self.FIELD_HOUR:
                self._editing_field = self.FIELD_MINUTE
                logger.debug("Editing next field: Minute")
            elif self._editing_field == self.FIELD_MINUTE:
                logger.debug("Finished editing Time fields.")
                action_result = "EXIT_EDIT_SAVE"
        assert isinstance(action_result, (str, type(None))), "Next field returning invalid type"
        return action_result

    def _change_date_field(self, delta: int):
        """ Increments/decrements the current date field of the temporary _datetime_being_edited. """
        assert self._datetime_being_edited is not None, "Cannot change date field, _datetime_being_edited is None"
        assert self._editing_field in [self.FIELD_YEAR, self.FIELD_MONTH, self.FIELD_DAY], f"Invalid date field '{self._editing_field}' for adjustment"
        assert delta in [-1, 1], f"Invalid delta value: {delta}"

        current_dt = self._datetime_being_edited
        year, month, day = current_dt.year, current_dt.month, current_dt.day
        hour, minute, second = current_dt.hour, current_dt.minute, current_dt.second
        assert all(isinstance(v, int) for v in [year, month, day, hour, minute, second]), "Date/time components are not integers"
        logger.debug(f"Attempting to change temporary Date field '{self._editing_field}' by {delta} from {year}-{month:02d}-{day:02d}")

        if self._editing_field == self.FIELD_YEAR:
            year += delta
            year = max(1970, min(2100, year))
        elif self._editing_field == self.FIELD_MONTH:
            month += delta
            if month > 12: month = 1
            elif month < 1: month = 12
        elif self._editing_field == self.FIELD_DAY:
            import calendar
            try:
                assert 1 <= month <= 12, "Invalid month for calendar.monthrange"
                _, max_days = calendar.monthrange(year, month)
                day += delta
                if day > max_days: day = 1
                elif day < 1: day = max_days
            except ValueError:
                logger.warning(f"Invalid intermediate date ({year}-{month}) for day calculation. Clamping day.")
                day += delta
                day = max(1, min(day, 31))

        new_datetime = get_safe_datetime(year, month, day, hour, minute, second)
        if new_datetime:
            assert isinstance(new_datetime, datetime.datetime), "get_safe_datetime returned invalid type"
            self._datetime_being_edited = new_datetime
            logger.debug(f"Temporary Date being edited is now: {self._datetime_being_edited.strftime('%Y-%m-%d')}")
        else:
            logger.warning(f"Date field change resulted in invalid date. Change ignored.")
            assert isinstance(self._datetime_being_edited, datetime.datetime), "Temporary datetime became invalid"

    def _change_time_field(self, delta: int):
        """ Increments/decrements the current time field of the temporary _datetime_being_edited. """
        assert self._datetime_being_edited is not None, "Cannot change time field, _datetime_being_edited is None"
        assert self._editing_field in [self.FIELD_HOUR, self.FIELD_MINUTE], f"Invalid time field '{self._editing_field}' for adjustment"
        assert delta in [-1, 1], f"Invalid delta value: {delta}"
        time_delta = datetime.timedelta(0)
        if self._editing_field == self.FIELD_HOUR:
            time_delta = datetime.timedelta(hours=delta)
        elif self._editing_field == self.FIELD_MINUTE:
            time_delta = datetime.timedelta(minutes=delta)
        else:
             logger.error(f"Logic error: _change_time_field called with invalid field '{self._editing_field}'")
             return
        assert isinstance(time_delta, datetime.timedelta), "Failed to create valid timedelta"
        logger.debug(f"Attempting to change temporary Time field '{self._editing_field}' by {delta} hours/mins")
        try:
            new_datetime = self._datetime_being_edited + time_delta
            assert isinstance(new_datetime, datetime.datetime), "Time delta calculation resulted in invalid type"
            self._datetime_being_edited = new_datetime
            logger.debug(f"Temporary Time being edited is now: {self._datetime_being_edited.strftime('%H:%M:%S')}")
        except OverflowError:
             logger.warning(f"Time field change resulted in datetime overflow. Change ignored.")
             assert isinstance(self._datetime_being_edited, datetime.datetime), "Temporary datetime became invalid after overflow attempt"

    def _commit_time_offset_changes(self):
        """ Calculates and stores the new time offset based on the final edited datetime. """
        assert self._datetime_being_edited is not None, "Commit called but no datetime was being edited."
        try:
            final_edited_time = self._datetime_being_edited
            current_system_time = datetime.datetime.now()
            assert isinstance(final_edited_time, datetime.datetime), "Final edited time is invalid type"
            assert isinstance(current_system_time, datetime.datetime), "Current system time is invalid type"
            new_offset = final_edited_time - current_system_time
            assert isinstance(new_offset, datetime.timedelta), "Offset calculation resulted in invalid type"
            self._time_offset = new_offset
            logger.info(f"Time offset update finalized.")
            logger.info(f"  Final edited time: {final_edited_time.strftime('%Y-%m-%d %H:%M:%S')}")
            logger.info(f"  System time at commit: {current_system_time.strftime('%Y-%m-%d %H:%M:%S')}")
            logger.info(f"  New time offset stored: {self._time_offset}")
        except Exception as e:
             logger.error(f"Error calculating or storing time offset: {e}", exc_info=True)
             logger.warning("Time offset commit failed. Previous offset retained.")
             assert isinstance(self._time_offset, datetime.timedelta), "Time offset became invalid after commit failure"

    def _draw_title(self):
        """Draws the main title."""
        assert self.title_font, "Title font not loaded"
        try:
            title_text = self.title_font.render("OPEN SPECTRO MENU", True, YELLOW)
            assert isinstance(title_text, pygame.Surface), "Title render failed"
            title_rect = title_text.get_rect(centerx=SCREEN_WIDTH // 2, top=10)
            self.screen.blit(title_text, title_rect)
        except pygame.error as e:
             logger.error(f"Pygame error rendering title: {e}")
        except Exception as e:
             logger.error(f"Unexpected error rendering title: {e}", exc_info=True)

    def _draw_menu_items(self):
        """ Draws the menu items, aligning values and handling highlight/edit states. """
        assert self.font is not None, "Cannot draw menu items without main font."
        y_position = MENU_MARGIN_TOP
        datetime_to_display_default = self._get_current_app_display_time()
        assert isinstance(datetime_to_display_default, datetime.datetime), "Default display time is invalid"

        for i, (item_text, edit_type) in enumerate(self._menu_items):
            try:
                is_selected = (i == self._current_selection_idx)
                is_being_edited = (is_selected and self._is_editing)
                assert isinstance(item_text, str), f"Menu item text at index {i} is not string"
                assert isinstance(edit_type, int), f"Menu item edit type at index {i} is not int"
                assert isinstance(is_selected, bool), "is_selected flag is not bool"
                assert isinstance(is_being_edited, bool), "is_being_edited flag is not bool"

                datetime_for_formatting = datetime_to_display_default
                if is_being_edited and edit_type in [self.EDIT_TYPE_DATE, self.EDIT_TYPE_TIME]:
                    assert self._datetime_being_edited is not None, "Editing Date/Time but _datetime_being_edited is None"
                    assert isinstance(self._datetime_being_edited, datetime.datetime), "Temporary datetime object is invalid type"
                    datetime_for_formatting = self._datetime_being_edited

                label_text = item_text
                value_text = ""
                prefix = "" # Used for alignment, though not directly drawn

                if item_text == self.MENU_ITEM_INTEGRATION:
                    prefix = "INTEGRATION:" # For _calculate_value_offset
                    label_text = prefix      # Text to draw
                    value_text = f"{self._integration_time_ms} ms"
                elif item_text == self.MENU_ITEM_COLLECTION_MODE:
                    prefix = "MODE:"
                    label_text = prefix
                    value_text = self._collection_mode
                elif item_text == self.MENU_ITEM_LENS_TYPE: # <<< NEW
                    prefix = "LENS TYPE:"
                    label_text = prefix
                    value_text = self._lens_type
                elif item_text == self.MENU_ITEM_DATE:
                    prefix = "DATE:"
                    label_text = prefix
                    value_text = f"{datetime_for_formatting.strftime('%Y-%m-%d')}"
                elif item_text == self.MENU_ITEM_TIME:
                    prefix = "TIME:"
                    label_text = prefix
                    value_text = f"{datetime_for_formatting.strftime('%H:%M')}"
                elif item_text == self.MENU_ITEM_WIFI:
                    prefix = "WIFI:"
                    label_text = prefix
                    value_text = self.network_info.get_wifi_name()
                elif item_text == self.MENU_ITEM_IP:
                    prefix = "IP:"
                    label_text = prefix
                    value_text = self.network_info.get_ip_address()

                assert isinstance(label_text, str), "Generated label text is not string"
                assert isinstance(value_text, str), "Generated value text is not string"

                color = WHITE
                is_network_item = item_text in [self.MENU_ITEM_WIFI, self.MENU_ITEM_IP]
                is_connected = not ("Not Connected" in value_text or "Error" in value_text or "No IP" in value_text)
                assert isinstance(is_network_item, bool), "is_network_item flag is not bool"
                assert isinstance(is_connected, bool), "is_connected flag is not bool"

                if is_selected:
                    color = YELLOW
                elif is_network_item and not is_connected:
                    color = GRAY
                assert isinstance(color, tuple), "Color is not a tuple"

                label_surface = self.font.render(label_text, True, color)
                assert isinstance(label_surface, pygame.Surface), f"Label render failed for '{label_text}'"
                self.screen.blit(label_surface, (MENU_MARGIN_LEFT, y_position))

                if value_text:
                    value_surface = self.font.render(value_text, True, color)
                    assert isinstance(value_surface, pygame.Surface), f"Value render failed for '{value_text}'"
                    value_pos_x = MENU_MARGIN_LEFT + self._value_start_offset_x
                    self.screen.blit(value_surface, (value_pos_x, y_position))

                # <<< MODIFIED: Include LENS_TYPE highlight >>>
                if is_being_edited and edit_type in [self.EDIT_TYPE_INTEGRATION, self.EDIT_TYPE_COLLECTION_MODE, self.EDIT_TYPE_LENS_TYPE, self.EDIT_TYPE_DATE, self.EDIT_TYPE_TIME]:
                    self._draw_editing_highlight(y_position, edit_type, label_text, value_text)

            except pygame.error as e:
                logger.error(f"Pygame error rendering menu item '{item_text}': {e}")
            except Exception as e:
                logger.error(f"Unexpected error rendering menu item '{item_text}': {e}", exc_info=True)

            y_position += MENU_SPACING
            assert isinstance(y_position, int), "y_position is not integer"

    def _draw_editing_highlight(self, y_pos: int, edit_type: int, label_str: str, value_str: str):
        """ Draws highlight rectangle around the specific field or value being edited. """
        assert self.font is not None, "Cannot draw highlight without main font."
        assert isinstance(y_pos, int), "y_pos must be integer"
        assert isinstance(edit_type, int), "edit_type must be integer"
        assert isinstance(label_str, str), "label_str must be string"
        assert isinstance(value_str, str), "value_str must be string"

        value_start_x = MENU_MARGIN_LEFT + self._value_start_offset_x
        assert isinstance(value_start_x, int), "value_start_x calculation failed"

        highlight_rect = None
        try:
            field_str = ""
            offset_str = ""

            if edit_type == self.EDIT_TYPE_INTEGRATION:
                 field_str = str(self._integration_time_ms)
                 offset_str = ""
            elif edit_type == self.EDIT_TYPE_COLLECTION_MODE:
                 field_str = self._collection_mode
                 offset_str = ""
            elif edit_type == self.EDIT_TYPE_LENS_TYPE: # <<< NEW
                 field_str = self._lens_type
                 offset_str = ""
            elif edit_type == self.EDIT_TYPE_DATE:
                assert self._datetime_being_edited is not None and self._editing_field is not None, "Missing state for date highlight"
                formatted_date = self._datetime_being_edited.strftime('%Y-%m-%d')
                if self._editing_field == self.FIELD_YEAR:   field_str, offset_str = formatted_date[0:4], ""
                elif self._editing_field == self.FIELD_MONTH: field_str, offset_str = formatted_date[5:7], formatted_date[0:5]
                elif self._editing_field == self.FIELD_DAY:   field_str, offset_str = formatted_date[8:10], formatted_date[0:8]
                else: logger.error(f"Invalid editing field '{self._editing_field}' for date highlight"); return
            elif edit_type == self.EDIT_TYPE_TIME:
                assert self._datetime_being_edited is not None and self._editing_field is not None, "Missing state for time highlight"
                formatted_time = self._datetime_being_edited.strftime('%H:%M')
                if self._editing_field == self.FIELD_HOUR:   field_str, offset_str = formatted_time[0:2], ""
                elif self._editing_field == self.FIELD_MINUTE: field_str, offset_str = formatted_time[3:5], formatted_time[0:3]
                else: logger.error(f"Invalid editing field '{self._editing_field}' for time highlight"); return
            else: logger.warning(f"Highlight requested for unknown edit type {edit_type}"); return

            assert isinstance(field_str, str), "field_str is not string"
            assert isinstance(offset_str, str), "offset_str is not string"

            field_width = self.font.size(field_str)[0] if field_str else 0
            offset_within_value_width = self.font.size(offset_str)[0] if offset_str else 0
            assert isinstance(field_width, int), "field_width calculation failed"
            assert isinstance(offset_within_value_width, int), "offset_within_value_width calculation failed"

            highlight_x = value_start_x + offset_within_value_width
            padding = 1
            highlight_rect = pygame.Rect(
                highlight_x - padding,
                y_pos - padding,
                field_width + 2 * padding,
                FONT_SIZE + 2 * padding
            )
            assert isinstance(highlight_rect, pygame.Rect), "Failed to create highlight Rect"

        except pygame.error as e:
             logger.error(f"Pygame error calculating highlight size: {e}"); return
        except Exception as e:
             logger.error(f"Unexpected error calculating highlight: {e}", exc_info=True); return

        if highlight_rect:
            assert self.screen is not None, "Screen is None, cannot draw highlight"
            pygame.draw.rect(self.screen, BLUE, highlight_rect, 1)

    def _draw_hints(self):
        """Draws contextual hints at the bottom."""
        assert self.hint_font is not None, "Hint font object is not available"
        hint_text = ""
        if self._is_editing:
            hint_text = "X/Y: Adjust | A: Next/Save | B: Cancel"
        else:
            hint_text = "X/Y: Navigate | A: Select/Edit | B: Back"
        assert isinstance(hint_text, str), "Generated hint text is not string"
        try:
            hint_surface = self.hint_font.render(hint_text, True, YELLOW)
            assert isinstance(hint_surface, pygame.Surface), "Hint render failed"
            hint_rect = hint_surface.get_rect(left=MENU_MARGIN_LEFT, bottom=SCREEN_HEIGHT - 10)
            assert self.screen is not None, "Screen is None, cannot draw hints"
            self.screen.blit(hint_surface, hint_rect)
        except pygame.error as e:
             logger.error(f"Pygame error rendering hints: {e}")
        except Exception as e:
             logger.error(f"Unexpected error rendering hints: {e}", exc_info=True)

class SpectrometerScreen:
    """
    Handles the spectrometer live view, capture, saving, and state management.
    Calibration (Dark/White) now follows a freeze-review-save model similar to sample capture.
    """
    # --- Internal State Flags ---
    STATE_LIVE_VIEW = "live_view" # Main live view (Raw/Reflectance as per menu)
    STATE_CALIBRATE = "calibrate_menu" # Intermediate menu to choose Dark/White setup

    STATE_DARK_CAPTURE_SETUP = "dark_setup" # Live raw view, preparing for Dark capture
    STATE_WHITE_CAPTURE_SETUP = "white_setup" # Live raw view, preparing for White capture

    # Generic frozen state for OOI, Dark, or White captures
    STATE_FROZEN_VIEW = "frozen_view"

    # --- Constants for Frozen Capture Types ---
    FROZEN_TYPE_OOI = "OOI" # Represents a sample capture (Raw or Reflectance)
    FROZEN_TYPE_DARK = "DARK"
    FROZEN_TYPE_WHITE = "WHITE"


    def __init__(self, screen: pygame.Surface, button_handler: ButtonHandler, menu_system: MenuSystem, display_hat_obj):
        # Assertions for parameters
        assert screen is not None, "Screen object is required for SpectrometerScreen"
        assert button_handler is not None, "ButtonHandler object is required for SpectrometerScreen"
        assert menu_system is not None, "MenuSystem object is required for SpectrometerScreen"

        self.screen = screen
        self.button_handler = button_handler
        self.menu_system = menu_system
        self.display_hat = display_hat_obj

        self.spectrometer: Spectrometer | None = None
        self.wavelengths: np.ndarray | None = None
        self._initialize_spectrometer_device()

        self.plot_fig: plt.Figure | None = None
        self.plot_ax: plt.Axes | None = None
        self.plot_line: plt.Line2D | None = None
        self._initialize_plot()

        self.overlay_font: pygame.font.Font | None = None
        self._load_overlay_font()

        self.is_active = False
        self._current_state = self.STATE_LIVE_VIEW # Initial state
        self._last_integration_time_ms = 0

        # --- Frozen Data Storage (used by OOI, Dark, and White captures) ---
        self._frozen_intensities: np.ndarray | None = None
        self._frozen_wavelengths: np.ndarray | None = None
        self._frozen_timestamp: datetime.datetime | None = None
        self._frozen_integration_ms: int | None = None
        self._frozen_capture_type: str | None = None # Stores FROZEN_TYPE_OOI, _DARK, or _WHITE
        self._frozen_sample_collection_mode: str | None = None # Specific for OOI: "RAW" or "REFLECTANCE"

        self._current_y_max: float = float(Y_AXIS_DEFAULT_MAX)
        self._scans_today_count: int = 0

        try:
            os.makedirs(DATA_DIR, exist_ok=True)
        except OSError as e:
            logger.error(f"Could not create base data directory {DATA_DIR} on SpectrometerScreen init: {e}")
        except Exception as e_mkdir:
            logger.error(f"Unexpected error creating data directory {DATA_DIR}: {e_mkdir}")

    def _initialize_spectrometer_device(self):
            """Finds the first available spectrometer device and stores the object."""
            logger.info("Looking for spectrometer devices...")
            if not USE_SPECTROMETER or sb is None or Spectrometer is None:
                logger.warning("Spectrometer use disabled or libraries not loaded.")
                self.spectrometer = None # Ensure it's None
                return
            try:
                devices = sb.list_devices()
                if not devices:
                    logger.error("No spectrometer devices found.")
                    self.spectrometer = None
                    return

                self.spectrometer = Spectrometer.from_serial_number(devices[0].serial_number)
                if self.spectrometer is None:
                    logger.error("Failed to create Spectrometer instance (returned None).")
                    return

                if not hasattr(self.spectrometer, '_dev'):
                    logger.error("Spectrometer object initialized but missing '_dev' backend attribute (pyseabreeze).")
                    self.spectrometer = None
                    return

                self.wavelengths = self.spectrometer.wavelengths()
                if self.wavelengths is None or len(self.wavelengths) == 0:
                    logger.error("Failed to get wavelengths from spectrometer.")
                    self.spectrometer = None
                    return

                assert isinstance(self.spectrometer, Spectrometer), "Spectrometer object is not of expected type."
                assert isinstance(self.wavelengths, np.ndarray) and self.wavelengths.size > 0, "Wavelengths are not a valid numpy array."

                logger.info(f"Spectrometer device object created: {devices[0]}")
                logger.info(f"  Model: {self.spectrometer.model}")
                logger.info(f"  Serial: {self.spectrometer.serial_number}")
                logger.info(f"  Wavelength range: {self.wavelengths[0]:.1f} nm to {self.wavelengths[-1]:.1f} nm ({len(self.wavelengths)} points)")

                try:
                    limits_tuple = self.spectrometer.integration_time_micros_limits
                    if isinstance(limits_tuple, tuple) and len(limits_tuple) == 2:
                        min_integ, max_integ = limits_tuple
                        logger.info(f"  Integration time limits (reported): {min_integ / 1000.0:.1f}ms - {max_integ / 1000.0:.1f}ms")
                    else:
                        logger.warning(f"  Integration time limits attribute has unexpected format: {type(limits_tuple)}")
                except AttributeError:
                    logger.warning("  Integration time limits attribute ('integration_time_micros_limits') not found on spectrometer object.")
                except Exception as e_int_limits:
                    logger.warning(f"  Could not query integration time limits: {e_int_limits}")

            except sb.SeaBreezeError as e_sb:
                logger.error(f"SeaBreezeError initializing spectrometer device: {e_sb}", exc_info=True)
                self.spectrometer = None
            except Exception as e:
                logger.error(f"Unexpected error initializing spectrometer device: {e}", exc_info=True)
                self.spectrometer = None

    def _initialize_plot(self):
            """Initializes the matplotlib figure and axes for plotting with desired styling."""
            if plt is None:
                logger.error("Matplotlib (plt) is unavailable. Cannot initialize plot.")
                return
            logger.debug("Initializing Matplotlib plot for SpectrometerScreen...")
            try:
                plot_width_px = SCREEN_WIDTH
                plot_height_px = SCREEN_HEIGHT - 45
                dpi = float(self.screen.get_width() / 3.33) if self.screen else 96.0

                figsize_inches_w = plot_width_px / dpi
                figsize_inches_h = plot_height_px / dpi
                assert figsize_inches_w > 0 and figsize_inches_h > 0, "Calculated figsize must be positive."

                self.plot_fig, self.plot_ax = plt.subplots(figsize=(figsize_inches_w, figsize_inches_h), dpi=dpi)

                if not self.plot_fig or not self.plot_ax:
                    raise RuntimeError("plt.subplots failed to return figure and/or axes.")

                (self.plot_line,) = self.plot_ax.plot([], [], linewidth=1.0, color='cyan')
                if not self.plot_line:
                    raise RuntimeError("plot_ax.plot failed to return a line object.")

                self.plot_ax.grid(True, linestyle=":", alpha=0.6, color='gray')
                self.plot_ax.tick_params(axis='both', which='major', labelsize=8, colors='white')
                self.plot_ax.set_xlabel("Wavelength (nm)", fontsize=9, color='white')
                self.plot_ax.set_ylabel("Intensity", fontsize=9, color='white')
                self.plot_fig.patch.set_facecolor('black')
                self.plot_ax.set_facecolor('black')

                spines_to_color = ['top', 'bottom', 'left', 'right']
                for spine_key in spines_to_color:
                    self.plot_ax.spines[spine_key].set_color('gray')

                self.plot_fig.tight_layout(pad=0.3)
                logger.debug("Matplotlib plot initialized successfully with styling.")

            except RuntimeError as e_rt:
                 logger.error(f"Runtime error during plot initialization: {e_rt}", exc_info=True)
                 if self.plot_fig and plt and plt.fignum_exists(self.plot_fig.number): plt.close(self.plot_fig)
                 self.plot_fig = self.plot_ax = self.plot_line = None
            except Exception as e:
                logger.error(f"Failed to initialize Matplotlib plot: {e}", exc_info=True)
                if self.plot_fig and plt and plt.fignum_exists(self.plot_fig.number): plt.close(self.plot_fig)
                self.plot_fig = self.plot_ax = self.plot_line = None

    def _load_overlay_font(self):
        """Loads the font used for text overlays."""
        if not pygame.font.get_init():
            logger.info("Pygame font module not initialized. Initializing now for overlay_font.")
            pygame.font.init()
        assert pygame.font.get_init(), "Pygame font module failed to initialize."
        try:
            script_dir = os.path.dirname(os.path.abspath(__file__))
            assets_dir = os.path.join(script_dir, 'assets')
            font_path = os.path.join(assets_dir, SPECTRO_FONT_FILENAME)
            assert isinstance(font_path, str), "Overlay font path is not a string"

            if not os.path.isfile(font_path):
                logger.warning(f"Overlay font file not found: '{font_path}'. Using Pygame SysFont fallback.")
                self.overlay_font = pygame.font.SysFont(None, SPECTRO_FONT_SIZE)
            else:
                self.overlay_font = pygame.font.Font(font_path, SPECTRO_FONT_SIZE)

            if self.overlay_font is None:
                raise RuntimeError("Font loading returned None even after attempting fallback.")
            logger.info(f"Loaded overlay font: {SPECTRO_FONT_FILENAME} (Size: {SPECTRO_FONT_SIZE})")
        except RuntimeError as e_rt:
            logger.error(f"Runtime error loading overlay font: {e_rt}", exc_info=True)
            self.overlay_font = None
        except pygame.error as e_pygame:
            logger.error(f"Pygame error loading overlay font: {e_pygame}. Attempting SysFont.", exc_info=True)
            try:
                self.overlay_font = pygame.font.SysFont(None, SPECTRO_FONT_SIZE)
                if self.overlay_font is None: raise RuntimeError("SysFont fallback also returned None.")
            except Exception as e_sysfont:
                logger.critical(f"CRITICAL: Could not load any overlay font, SysFont fallback failed: {e_sysfont}")
                self.overlay_font = None
        except Exception as e:
            logger.error(f"Unexpected error loading overlay font: {e}", exc_info=True)
            self.overlay_font = None

    def _clear_frozen_data(self):
        """Clears all temporarily stored frozen spectrum data."""
        self._frozen_intensities = None
        self._frozen_wavelengths = None
        self._frozen_timestamp = None
        self._frozen_integration_ms = None
        self._frozen_capture_type = None
        self._frozen_sample_collection_mode = None
        logger.debug("Cleared all frozen spectrum data.")

    def activate(self):
        """Called when switching to this screen. Tries to open device and init scan count."""
        logger.info("Activating Spectrometer Screen.")
        self.is_active = True
        self._current_state = self.STATE_LIVE_VIEW # Always start in main live view
        self._clear_frozen_data() # Ensure no stale frozen data
        self._current_y_max = float(Y_AXIS_DEFAULT_MAX)
        logger.debug(f"Y-axis max reset to default: {self._current_y_max}")

        assert self.menu_system is not None, "MenuSystem is None during SpectrometerScreen activation."
        try:
            current_app_datetime = self.menu_system.get_timestamp_datetime()
            today_date_str = current_app_datetime.strftime("%Y-%m-%d")
            daily_folder_path = os.path.join(DATA_DIR, today_date_str)
            csv_filename_dated = f"{today_date_str}_{CSV_BASE_FILENAME}"
            csv_filepath = os.path.join(daily_folder_path, csv_filename_dated)
            assert isinstance(daily_folder_path, str) and isinstance(csv_filename_dated, str), "CSV path components are not strings."

            current_scan_count = 0
            if os.path.isfile(csv_filepath):
                line_count_for_day = 0
                try:
                    with open(csv_filepath, 'r', newline='') as f_check:
                        reader = csv.reader(f_check)
                        header = next(reader, None)
                        if header:
                            for _row in reader:
                                line_count_for_day += 1
                    current_scan_count = line_count_for_day
                    logger.info(f"Found {current_scan_count} existing scans in today's log: {csv_filepath}")
                except StopIteration:
                    logger.info(f"Log file {csv_filepath} exists but is empty or has only a header. Scan count is 0.")
                    current_scan_count = 0
                except csv.Error as e_csv_read:
                    logger.error(f"CSVError reading existing log file {csv_filepath} for scan count: {e_csv_read}. Count may be inaccurate.")
                    current_scan_count = 0
                except Exception as e_file_read:
                    logger.error(f"Error reading existing log file {csv_filepath} for scan count: {e_file_read}. Count may be inaccurate.")
                    current_scan_count = 0
            else:
                logger.info(f"No existing log file for today found at {csv_filepath}. Scan count starts at 0.")
            self._scans_today_count = current_scan_count
        except Exception as e_scan_count_init:
            logger.error(f"Error initializing 'scans today' count: {e_scan_count_init}. Defaulting to 0.")
            self._scans_today_count = 0
        logger.info(f"Scans today initialized to: {self._scans_today_count}")

        if not USE_SPECTROMETER:
            logger.warning("Spectrometer use is disabled in configuration.")
            return
        if self.spectrometer is None or not hasattr(self.spectrometer, '_dev'):
            logger.error("Spectrometer device object is invalid or not initialized. Cannot activate.")
            return

        try:
            device_proxy = getattr(self.spectrometer, '_dev', None)
            if device_proxy is None or not hasattr(device_proxy, 'is_open'):
                logger.error("Spectrometer device proxy or 'is_open' attribute not found. Cannot check/open connection.")
                return

            if not device_proxy.is_open:
                logger.info(f"Opening spectrometer connection: {self.spectrometer.serial_number}")
                self.spectrometer.open()
                logger.info("Spectrometer connection opened.")
                self._last_integration_time_ms = self.menu_system.get_integration_time_ms()
                integration_micros_scaled = int((self._last_integration_time_ms * 1000) * INTEGRATION_TIME_SCALE_FACTOR)
                logger.debug(f"ACTIVATE: Sending scaled integration time: {integration_micros_scaled} µs (target {self._last_integration_time_ms} ms)")
                self.spectrometer.integration_time_micros(integration_micros_scaled)
                logger.info(f"Initial integration time set to target: {self._last_integration_time_ms} ms")
            else:
                logger.info("Spectrometer connection already open.")
                current_menu_integ_ms = self.menu_system.get_integration_time_ms()
                if current_menu_integ_ms != self._last_integration_time_ms:
                    integration_micros_scaled = int((current_menu_integ_ms * 1000) * INTEGRATION_TIME_SCALE_FACTOR)
                    logger.debug(f"ACTIVATE (Sync): Sending scaled integration time: {integration_micros_scaled} µs (target {current_menu_integ_ms} ms)")
                    self.spectrometer.integration_time_micros(integration_micros_scaled)
                    self._last_integration_time_ms = current_menu_integ_ms
                    logger.info(f"Synced integration time to target: {current_menu_integ_ms} ms")
        except sb.SeaBreezeError as e_sb_open:
            logger.error(f"SeaBreezeError during spectrometer activation/open: {e_sb_open}", exc_info=True)
        except usb.core.USBError as e_usb:
            logger.error(f"USB Error opening spectrometer: [{getattr(e_usb, 'errno', 'N/A')}] {getattr(e_usb, 'strerror', str(e_usb))}", exc_info=True)
        except AttributeError as e_attr:
            logger.error(f"Attribute error during spectrometer activation: {e_attr}", exc_info=True)
        except Exception as e:
            logger.error(f"Unexpected error activating spectrometer: {e}", exc_info=True)

    def deactivate(self):
        """Called when switching away from this screen."""
        logger.info("Deactivating Spectrometer Screen.")
        self.is_active = False
        self._clear_frozen_data()
        self._current_state = self.STATE_LIVE_VIEW # Reset state for next activation
        # _scans_today_count persists for the duration of the app being open.

    def handle_input(self) -> str | None:
        """Processes button inputs for the spectrometer screen based on state."""
        assert self.button_handler is not None, "ButtonHandler is None in SpectrometerScreen.handle_input"
        pygame_event_result = self.button_handler.process_pygame_events()
        if pygame_event_result == "QUIT":
            logger.info("QUIT signal from Pygame events received in SpectrometerScreen.")
            return "QUIT"

        action_result: str | None = None
        device_proxy = getattr(self.spectrometer, '_dev', None)
        spec_ready = (
            self.spectrometer is not None and
            device_proxy is not None and
            hasattr(device_proxy, 'is_open') and
            device_proxy.is_open
        )
        current_state_local = self._current_state

        if current_state_local == self.STATE_LIVE_VIEW:
            if self.button_handler.check_button(BTN_ENTER): # A: Freeze Sample (OOI)
                if spec_ready: self._perform_freeze_capture(self.FROZEN_TYPE_OOI)
                else: logger.warning("Freeze Sample ignored: Spectrometer not ready.")
            elif self.button_handler.check_button(BTN_UP): # X: Enter Calibrate Menu
                logger.info("Entering Calibrate Menu.")
                self._current_state = self.STATE_CALIBRATE
            elif self.button_handler.check_button(BTN_DOWN): # Y: Rescale Y-Axis
                if spec_ready: self._rescale_y_axis(relative=False) # Standard rescale for live view
                else: logger.warning("Rescale ignored: Spectrometer not ready.")
            elif self.button_handler.check_button(BTN_BACK): # B: Back to Main Menu
                action_result = "BACK_TO_MENU"

        elif current_state_local == self.STATE_CALIBRATE: # Calibration type selection menu
            if self.button_handler.check_button(BTN_UP): # X: Dark Capture Setup
                logger.info("Selected Dark Capture Setup.")
                self._current_state = self.STATE_DARK_CAPTURE_SETUP
                self._current_y_max = float(Y_AXIS_DEFAULT_MAX) # Reset Y for raw dark counts
            elif self.button_handler.check_button(BTN_ENTER): # A: White Capture Setup
                logger.info("Selected White Capture Setup.")
                self._current_state = self.STATE_WHITE_CAPTURE_SETUP
                self._current_y_max = float(Y_AXIS_DEFAULT_MAX) # Reset Y for raw white counts
            elif self.button_handler.check_button(BTN_BACK): # B: Back to Live View
                logger.info("Exiting Calibrate Menu to Live View.")
                self._current_state = self.STATE_LIVE_VIEW

        elif current_state_local == self.STATE_DARK_CAPTURE_SETUP:
            if self.button_handler.check_button(BTN_ENTER): # A: Freeze Dark
                if spec_ready: self._perform_freeze_capture(self.FROZEN_TYPE_DARK)
                else: logger.warning("Freeze Dark ignored: Spectrometer not ready.")
            elif self.button_handler.check_button(BTN_BACK): # B: Back to Calibrate Menu
                self._current_state = self.STATE_CALIBRATE
            # No Y rescale for Dark setup by design

        elif current_state_local == self.STATE_WHITE_CAPTURE_SETUP:
            if self.button_handler.check_button(BTN_ENTER): # A: Freeze White
                if spec_ready: self._perform_freeze_capture(self.FROZEN_TYPE_WHITE)
                else: logger.warning("Freeze White ignored: Spectrometer not ready.")
            elif self.button_handler.check_button(BTN_DOWN): # Y: Rescale Y-Axis for White Setup
                if spec_ready: self._rescale_y_axis(relative=False) # Standard rescale for white setup
                else: logger.warning("Rescale White Setup ignored: Spectrometer not ready.")
            elif self.button_handler.check_button(BTN_BACK): # B: Back to Calibrate Menu
                self._current_state = self.STATE_CALIBRATE

        elif current_state_local == self.STATE_FROZEN_VIEW:
            assert self._frozen_capture_type is not None, "In FROZEN_VIEW but _frozen_capture_type is None."
            if self.button_handler.check_button(BTN_ENTER): # A: Save Frozen Data
                self._perform_save_frozen_data()
                # State change to previous setup state or live view is handled by _perform_save_frozen_data
            elif self.button_handler.check_button(BTN_BACK): # B: Discard Frozen Data
                self._perform_discard_frozen_data()
                # State change to previous setup state or live view is handled by _perform_discard_frozen_data
        else:
            logger.error(f"Unhandled input state in SpectrometerScreen: {current_state_local}")
            self._current_state = self.STATE_LIVE_VIEW # Fallback to a known safe state

        assert isinstance(action_result, (str, type(None))), f"handle_input returned invalid type: {type(action_result)}"
        return action_result

    def _perform_freeze_capture(self, capture_type: str):
        """Captures the current spectrum and freezes it for review."""
        assert self.menu_system is not None, "MenuSystem is None in _perform_freeze_capture"
        assert capture_type in [self.FROZEN_TYPE_OOI, self.FROZEN_TYPE_DARK, self.FROZEN_TYPE_WHITE], \
               f"Invalid capture_type '{capture_type}' for freeze."

        device_proxy = getattr(self.spectrometer, '_dev', None)
        if not (self.spectrometer and device_proxy and hasattr(device_proxy, 'is_open') and device_proxy.is_open and self.wavelengths is not None):
             logger.error(f"Cannot freeze {capture_type}: Spectrometer not ready or wavelengths missing.")
             return

        logger.info(f"Attempting to freeze spectrum for type: {capture_type}...")
        try:
            current_integration_time_ms = self.menu_system.get_integration_time_ms()
            assert isinstance(current_integration_time_ms, int) and current_integration_time_ms > 0, \
                   f"Invalid integration time from menu: {current_integration_time_ms}"

            if current_integration_time_ms != self._last_integration_time_ms:
                 integration_micros_scaled = int((current_integration_time_ms * 1000) * INTEGRATION_TIME_SCALE_FACTOR)
                 logger.debug(f"FREEZE ({capture_type}): Setting integration time to {integration_micros_scaled} µs (target {current_integration_time_ms} ms)")
                 self.spectrometer.integration_time_micros(integration_micros_scaled)
                 self._last_integration_time_ms = current_integration_time_ms

            # For all freeze types (OOI, DARK, WHITE), we capture raw, corrected intensities.
            # If OOI is Reflectance, the _save_data or _capture_and_plot will handle processing.
            intensities_captured = self.spectrometer.intensities(correct_dark_counts=True, correct_nonlinearity=True)
            assert intensities_captured is not None, "Received None for intensities from spectrometer."

            if len(intensities_captured) == len(self.wavelengths):
                self._clear_frozen_data() # Clear any previous frozen data first
                self._frozen_intensities = intensities_captured
                self._frozen_wavelengths = self.wavelengths
                self._frozen_timestamp = self.menu_system.get_timestamp_datetime()
                self._frozen_integration_ms = current_integration_time_ms
                self._frozen_capture_type = capture_type

                if capture_type == self.FROZEN_TYPE_OOI:
                    self._frozen_sample_collection_mode = self.menu_system.get_collection_mode()
                    logger.info(f"Sample spectrum frozen (Mode: {self._frozen_sample_collection_mode}, Integ: {self._frozen_integration_ms} ms). Type: {self._frozen_capture_type}")
                else: # DARK or WHITE
                    logger.info(f"{capture_type} spectrum frozen (Integ: {self._frozen_integration_ms} ms). Type: {self._frozen_capture_type}")

                self._current_state = self.STATE_FROZEN_VIEW
            else:
                logger.error(f"Failed to capture valid intensities for {capture_type} freeze. Length mismatch.")
        except sb.SeaBreezeError as e_sb_freeze:
            logger.error(f"SeaBreezeError during {capture_type} freeze capture: {e_sb_freeze}", exc_info=True)
        except Exception as e:
            logger.error(f"Unexpected error freezing {capture_type} spectrum: {e}", exc_info=True)

    def _perform_save_frozen_data(self):
        """Saves the currently frozen spectrum data, then returns to the appropriate previous state."""
        assert self._frozen_capture_type is not None, "Cannot save, _frozen_capture_type is None."
        assert self._frozen_intensities is not None, "Cannot save, _frozen_intensities is None."
        assert self._frozen_wavelengths is not None, "Cannot save, _frozen_wavelengths is None."
        assert self._frozen_timestamp is not None, "Cannot save, _frozen_timestamp is None."
        assert self._frozen_integration_ms is not None, "Cannot save, _frozen_integration_ms is None."

        spectra_type_for_csv = ""
        intensities_to_save = self._frozen_intensities

        if self._frozen_capture_type == self.FROZEN_TYPE_OOI:
            assert self._frozen_sample_collection_mode is not None, "_frozen_sample_collection_mode is None for OOI save."
            spectra_type_for_csv = self._frozen_sample_collection_mode.upper()
            logger.info(f"Preparing to save frozen OOI sample as type: {spectra_type_for_csv}")
            if self._frozen_sample_collection_mode == MODE_REFLECTANCE:
                logger.warning(f"Saving OOI as REFLECTANCE: This example saves the frozen raw-like data under REFLECTANCE type. Full on-the-fly calculation from stored references is not implemented here.")
        elif self._frozen_capture_type == self.FROZEN_TYPE_DARK:
            spectra_type_for_csv = "DARK"
        elif self._frozen_capture_type == self.FROZEN_TYPE_WHITE:
            spectra_type_for_csv = "WHITE"
        else:
            logger.error(f"Unknown _frozen_capture_type: {self._frozen_capture_type}. Cannot determine spectra_type for CSV.")
            self._perform_discard_frozen_data() # Discard and return to appropriate state
            return

        logger.info(f"Attempting to save frozen data as {spectra_type_for_csv}...")
        save_success = self._save_data(
            intensities=intensities_to_save,
            wavelengths=self._frozen_wavelengths,
            timestamp=self._frozen_timestamp,
            integration_ms=self._frozen_integration_ms,
            spectra_type=spectra_type_for_csv,
            save_plot=(self._frozen_capture_type == self.FROZEN_TYPE_OOI)
        )

        if save_success:
            logger.info(f"Frozen {self._frozen_capture_type} (saved as {spectra_type_for_csv}) spectrum saved successfully.")
        else:
            logger.error(f"Failed to save frozen {self._frozen_capture_type} (intended as {spectra_type_for_csv}) spectrum.")

        if self._frozen_capture_type == self.FROZEN_TYPE_OOI:
            self._current_state = self.STATE_LIVE_VIEW
        elif self._frozen_capture_type == self.FROZEN_TYPE_DARK:
            # After saving DARK, go back to main LIVE VIEW
            self._current_state = self.STATE_LIVE_VIEW
            logger.info("Dark reference saved. Returning to main live view.")
        elif self._frozen_capture_type == self.FROZEN_TYPE_WHITE:
            # After saving WHITE, go back to main LIVE VIEW
            self._current_state = self.STATE_LIVE_VIEW
            logger.info("White reference saved. Returning to main live view.")
        else: # Should not be reached due to earlier check, but as a safeguard
            logger.error(f"Unexpected frozen_capture_type '{self._frozen_capture_type}' after save. Defaulting to LIVE_VIEW.")
            self._current_state = self.STATE_LIVE_VIEW


        self._clear_frozen_data()
        logger.info(f"Returned to state: {self._current_state} after saving frozen data.")

    def _perform_discard_frozen_data(self):
        """Discards the currently frozen spectrum data and returns to the appropriate previous state."""
        assert self._frozen_capture_type is not None, "Cannot discard, _frozen_capture_type is None."
        logger.info(f"Discarding frozen {self._frozen_capture_type} spectrum.")

        # <<< MODIFIED State Transition Logic for DISCARDING >>>
        if self._frozen_capture_type == self.FROZEN_TYPE_OOI:
            self._current_state = self.STATE_LIVE_VIEW
        elif self._frozen_capture_type == self.FROZEN_TYPE_DARK:
            # After discarding DARK, go back to DARK SETUP screen
            self._current_state = self.STATE_DARK_CAPTURE_SETUP
            logger.info("Frozen Dark reference discarded. Returning to Dark Capture Setup.")
        elif self._frozen_capture_type == self.FROZEN_TYPE_WHITE:
            # After discarding WHITE, go back to WHITE SETUP screen
            self._current_state = self.STATE_WHITE_CAPTURE_SETUP
            logger.info("Frozen White reference discarded. Returning to White Capture Setup.")
        else:
            logger.error(f"Unknown _frozen_capture_type during discard: {self._frozen_capture_type}. Returning to LIVE_VIEW.")
            self._current_state = self.STATE_LIVE_VIEW

        self._clear_frozen_data()
        logger.info(f"Returned to state: {self._current_state} after discarding frozen data.")

    def _save_data(self, intensities: np.ndarray, wavelengths: np.ndarray,
                   timestamp: datetime.datetime, integration_ms: int,
                   spectra_type: str, save_plot: bool = True) -> bool:
        """
        Saves spectrum data to daily CSV. Optionally saves plot.
        CSV columns: timestamp_utc, spectra_type, lens_type, integration_time_ms, w1, w2, ...
        """
        # Assertions for parameters
        assert intensities is not None, "Intensities are None in _save_data."
        assert wavelengths is not None, "Wavelengths are None in _save_data."
        assert timestamp is not None, "Timestamp is None in _save_data."
        assert integration_ms is not None, "Integration_ms is None in _save_data."
        assert spectra_type, "spectra_type is empty in _save_data."
        assert isinstance(intensities, np.ndarray), "Intensities must be numpy array."
        assert isinstance(wavelengths, np.ndarray), "Wavelengths must be numpy array."
        assert isinstance(timestamp, datetime.datetime), "Timestamp must be datetime object."
        assert isinstance(integration_ms, int), "Integration_ms must be int."
        assert self.menu_system is not None, "MenuSystem is None in _save_data."

        # --- Create Daily Folder ---
        daily_folder_path = os.path.join(DATA_DIR, timestamp.strftime("%Y-%m-%d"))
        try:
            os.makedirs(daily_folder_path, exist_ok=True)
        except OSError as e:
            logger.error(f"Could not create daily data directory {daily_folder_path}: {e}")
            return False
        except Exception as e_mkdir:
            logger.error(f"Unexpected error creating daily directory {daily_folder_path}: {e_mkdir}")
            return False

        # --- Prepare CSV ---
        csv_filename_dated = f"{timestamp.strftime('%Y-%m-%d')}_{CSV_BASE_FILENAME}"
        csv_filepath = os.path.join(daily_folder_path, csv_filename_dated)
        assert isinstance(csv_filepath, str), "Generated CSV filepath is not a string."

        timestamp_str_utc = timestamp.strftime("%Y-%m-%dT%H:%M:%SZ")
        lens_type_str = self.menu_system.get_lens_type()
        assert isinstance(lens_type_str, str) and lens_type_str in MenuSystem.LENS_TYPES, \
               f"Invalid lens type '{lens_type_str}' from menu_system. Valid types: {MenuSystem.LENS_TYPES}"

        logger.debug(f"Saving data (Type: {spectra_type}, Lens: {lens_type_str}) to {csv_filepath}")
        try:
            write_header = not (os.path.isfile(csv_filepath) and os.path.getsize(csv_filepath) > 0)
            with open(csv_filepath, 'a', newline='') as csvfile:
                csvwriter = csv.writer(csvfile)
                if write_header:
                    header = ["timestamp_utc", "spectra_type", "lens_type", "integration_time_ms"] + \
                             [f"{wl:.2f}" for wl in wavelengths]
                    csvwriter.writerow(header)
                data_row = [timestamp_str_utc, spectra_type, lens_type_str, integration_ms] + \
                           [f"{inten:.4f}" for inten in intensities]
                csvwriter.writerow(data_row)

            self._scans_today_count += 1
            logger.info(f"Scan count for today incremented to: {self._scans_today_count}")

            if save_plot:
                if plt is None or Image is None:
                    logger.warning("Plot save skipped: Matplotlib or Pillow unavailable.")
                else:
                    plot_timestamp_str_local = timestamp.strftime("%Y-%m-%d-%H%M%S")
                    plot_filename_base = f"spectrum_{spectra_type}_{lens_type_str}_{plot_timestamp_str_local}"
                    plot_filepath_png = os.path.join(daily_folder_path, f"{plot_filename_base}.png")
                    logger.debug(f"Attempting to save plot: {plot_filepath_png}")
                    save_fig_temp, save_ax_temp = None, None
                    try:
                        save_fig_temp, save_ax_temp = plt.subplots(figsize=(8, 6))
                        if not save_fig_temp or not save_ax_temp:
                            raise RuntimeError("Failed to create temporary figure/axes for saving plot.")
                        save_ax_temp.plot(wavelengths, intensities)
                        title_str = (f"Spectrum ({spectra_type}) - {plot_timestamp_str_local}\n"
                                     f"Lens: {lens_type_str}, Integ: {integration_ms} ms, Scans Today: {self._scans_today_count}")
                        save_ax_temp.set_title(title_str, fontsize=10)
                        save_ax_temp.set_xlabel("Wavelength (nm)")
                        save_ax_temp.set_ylabel("Intensity")
                        save_ax_temp.grid(True, linestyle="--", alpha=0.7)
                        save_fig_temp.tight_layout()
                        save_fig_temp.savefig(plot_filepath_png, dpi=150)
                        logger.info(f"Plot image saved: {plot_filepath_png}")
                    except Exception as e_plot_save:
                        logger.error(f"Error saving plot image to {plot_filepath_png}: {e_plot_save}", exc_info=True)
                    finally:
                        if save_fig_temp and plt and plt.fignum_exists(save_fig_temp.number):
                            plt.close(save_fig_temp)
            else:
                logger.debug(f"Plot saving skipped for spectra_type: {spectra_type}")
            logger.info(f"Data successfully saved for type: {spectra_type} (Lens: {lens_type_str}).")
            return True
        except csv.Error as e_csv:
            logger.error(f"CSVError saving data to {csv_filepath}: {e_csv}", exc_info=True)
        except IOError as e_io:
            logger.error(f"IOError saving data to {csv_filepath}: {e_io}", exc_info=True)
        except Exception as e:
            logger.error(f"Unexpected error saving data to {csv_filepath}: {e}", exc_info=True)
        return False

    def _rescale_y_axis(self, relative: bool = False): # 'relative' param is currently not used by callers
        """Captures spectrum, calculates new Y max for raw count display."""
        assert self.menu_system is not None, "MenuSystem is None in _rescale_y_axis"
        if np is None:
            logger.error("NumPy (np) is unavailable. Cannot rescale Y-axis.")
            return

        device_proxy = getattr(self.spectrometer, '_dev', None)
        if not (self.spectrometer and device_proxy and hasattr(device_proxy, 'is_open') and device_proxy.is_open):
             logger.warning("Spectrometer not ready for Y-axis rescale.")
             return

        logger.info(f"Attempting to rescale Y-axis for raw count display...")
        try:
            current_integration_time_ms = self.menu_system.get_integration_time_ms()
            assert isinstance(current_integration_time_ms, int) and current_integration_time_ms > 0, \
                   f"Invalid integration time from menu: {current_integration_time_ms}"

            if current_integration_time_ms != self._last_integration_time_ms:
                 integration_micros_scaled = int((current_integration_time_ms * 1000) * INTEGRATION_TIME_SCALE_FACTOR)
                 logger.debug(f"RESCALE_Y: Setting integration time to {integration_micros_scaled} µs (target {current_integration_time_ms} ms)")
                 self.spectrometer.integration_time_micros(integration_micros_scaled)
                 self._last_integration_time_ms = current_integration_time_ms

            intensities = self.spectrometer.intensities(correct_dark_counts=True, correct_nonlinearity=True)
            assert intensities is not None, "Received None for intensities from spectrometer for rescale."

            if len(intensities) > 0:
                 max_val = np.max(intensities) # Rescale based on raw intensities
                 new_y_max = max(float(Y_AXIS_MIN_CEILING), float(max_val * Y_AXIS_RESCALE_FACTOR))
                 self._current_y_max = new_y_max
                 logger.info(f"Y-axis max rescaled to: {self._current_y_max:.2f} (based on max_val: {max_val:.2f})")
            else:
                 logger.warning("Failed Y-axis rescaling: No valid intensities captured.")
        except sb.SeaBreezeError as e_sb_rescale:
            logger.error(f"SeaBreezeError during Y-axis rescale: {e_sb_rescale}", exc_info=True)
        except usb.core.USBError as e_usb:
            logger.error(f"USB error during Y-axis rescale: {e_usb}", exc_info=True)
        except AttributeError as e_attr:
            logger.error(f"Attribute error during Y-axis rescale: {e_attr}", exc_info=True)
        except Exception as e:
            logger.error(f"Unexpected error rescaling Y-axis: {e}", exc_info=True)

    def _capture_and_plot(self) -> pygame.Surface | None:
        """Captures/uses data, applies smoothing, plots to an in-memory Pygame surface."""
        assert self.plot_fig and self.plot_ax and self.plot_line, "Plotting components not initialized."
        assert Image is not None, "Pillow (Image) is not available for plot rendering."
        assert self.menu_system is not None, "MenuSystem is None in _capture_and_plot."

        if np is None and self._current_state not in [self.STATE_FROZEN_VIEW]: # NumPy needed for live smoothing
             logger.warning("NumPy (np) is unavailable. Live smoothing will be skipped.")

        plot_wavelengths_local: np.ndarray | None = None
        plot_intensities_to_display: np.ndarray | None = None
        y_axis_label_str = "Intensity"
        device_proxy = getattr(self.spectrometer, '_dev', None)
        current_internal_state = self._current_state

        try:
            if current_internal_state == self.STATE_FROZEN_VIEW:
                if not (self._frozen_intensities is not None and self._frozen_wavelengths is not None):
                     logger.error("Frozen data or wavelengths missing for plot. Discarding.")
                     self._perform_discard_frozen_data() # Clears and sets state
                     return None
                plot_wavelengths_local = self._frozen_wavelengths
                plot_intensities_to_display = self._frozen_intensities # This is always raw-like data
                
                # Determine label for frozen view based on what was frozen
                frozen_type_label = self._frozen_capture_type or "UNKNOWN"
                if self._frozen_capture_type == self.FROZEN_TYPE_OOI:
                    frozen_type_label = self._frozen_sample_collection_mode or "SAMPLE"
                y_axis_label_str = f"Intensity ({frozen_type_label.upper()} Frozen)"

            elif current_internal_state in [self.STATE_LIVE_VIEW, self.STATE_DARK_CAPTURE_SETUP, self.STATE_WHITE_CAPTURE_SETUP, self.STATE_CALIBRATE]:
                # All these states show a live, raw (potentially smoothed) spectrum
                if not (self.spectrometer and device_proxy and hasattr(device_proxy, 'is_open') and device_proxy.is_open and self.wavelengths is not None):
                     logger.debug(f"Spectrometer not ready for live plot in state: {current_internal_state}.")
                     return None

                current_integration_time_ms = self.menu_system.get_integration_time_ms()
                if current_integration_time_ms != self._last_integration_time_ms:
                     integration_micros_scaled = int((current_integration_time_ms * 1000) * INTEGRATION_TIME_SCALE_FACTOR)
                     self.spectrometer.integration_time_micros(integration_micros_scaled)
                     self._last_integration_time_ms = current_integration_time_ms

                raw_intensities_capture = self.spectrometer.intensities(correct_dark_counts=True, correct_nonlinearity=True)
                if raw_intensities_capture is None or len(raw_intensities_capture) != len(self.wavelengths):
                     logger.warning(f"Failed live capture or length mismatch in state {current_internal_state}.")
                     return None
                plot_wavelengths_local = self.wavelengths
                
                # Determine Y-axis label based on actual collection mode for LIVE_VIEW
                if current_internal_state == self.STATE_LIVE_VIEW:
                    collection_mode = self.menu_system.get_collection_mode()
                    if collection_mode == MODE_REFLECTANCE:
                        # Here, if we were to display actual live reflectance, we'd calculate it.
                        # The current structure is to always show raw-like counts for simplicity in plot.
                        # The *saved* data for REFLECTANCE OOI would be calculated at save time if implemented.
                        y_axis_label_str = "Reflectance (Calculated)" # Placeholder if calc was done
                        # For now, still plot raw for live reflectance view, label indicates aspiration.
                        # plot_intensities_to_display = calculate_live_reflectance(...)
                        plot_intensities_to_display = raw_intensities_capture # Actually plotting raw
                        y_axis_label_str = "Intensity (Reflect)" # More accurate to what's plotted
                    else: # MODE_RAW
                        y_axis_label_str = "Intensity (Counts)"
                        plot_intensities_to_display = raw_intensities_capture
                else: # DARK_SETUP, WHITE_SETUP, CALIBRATE_MENU
                    y_axis_label_str = "Intensity (Counts)"
                    plot_intensities_to_display = raw_intensities_capture

                # Apply smoothing if configured, NumPy available, and data is ndarray
                if np is not None and USE_LIVE_SMOOTHING and LIVE_SMOOTHING_WINDOW_SIZE > 1 and isinstance(plot_intensities_to_display, np.ndarray):
                    try:
                        window_s = LIVE_SMOOTHING_WINDOW_SIZE
                        if window_s % 2 == 0: window_s += 1
                        if window_s > len(plot_intensities_to_display): window_s = len(plot_intensities_to_display)
                        if window_s >= 3 :
                            weights = np.ones(window_s) / float(window_s)
                            plot_intensities_to_display = np.convolve(plot_intensities_to_display, weights, mode='same')
                    except Exception as smooth_err:
                        logger.error(f"Error during live smoothing: {smooth_err}. Using unsmoothed data.")
            else:
                 logger.error(f"Unknown plot state: {current_internal_state}. Cannot capture/plot.")
                 return None

            if plot_wavelengths_local is None or plot_intensities_to_display is None:
                logger.debug("No data available to plot (wavelengths or intensities are None).")
                return None

            self.plot_line.set_data(plot_wavelengths_local, plot_intensities_to_display)
            self.plot_ax.set_ylabel(y_axis_label_str, fontsize=9, color='white')
            self.plot_ax.set_ylim(0, self._current_y_max) # Always use current Y max
            self.plot_ax.set_xlim(min(plot_wavelengths_local), max(plot_wavelengths_local))

            plot_buffer = None
            try:
                 plot_buffer = io.BytesIO()
                 self.plot_fig.savefig(plot_buffer, format='png', dpi=self.plot_fig.get_dpi(), bbox_inches='tight', pad_inches=0.05)
                 plot_buffer.seek(0)
                 if plot_buffer.getbuffer().nbytes == 0:
                     raise RuntimeError("Plot buffer is empty after savefig.")
                 plot_surface = pygame.image.load(plot_buffer, "png")
                 if plot_surface is None:
                     raise RuntimeError("pygame.image.load returned None from buffer.")
                 return plot_surface
            except RuntimeError as e_render_rt:
                 logger.error(f"Runtime error rendering plot to Pygame surface: {e_render_rt}", exc_info=False) # Less verbose for frequent errors
                 return None
            except Exception as render_err:
                 logger.error(f"Unexpected error rendering plot to Pygame surface: {render_err}", exc_info=True)
                 return None
            finally:
                if plot_buffer: plot_buffer.close()

        except sb.SeaBreezeError as e_sb_plot:
            logger.error(f"SeaBreezeError in _capture_and_plot: {e_sb_plot}", exc_info=False)
            return None
        except usb.core.USBError as e_usb_plot:
            logger.error(f"USBError in _capture_and_plot: {e_usb_plot}", exc_info=False)
            return None
        except AttributeError as e_attr_plot:
            logger.error(f"AttributeError in _capture_and_plot: {e_attr_plot}", exc_info=True)
            return None
        except Exception as e_general_plot:
            logger.error(f"General unhandled error in _capture_and_plot: {e_general_plot}", exc_info=True)
            return None

    def _draw_overlays(self):
        """Draws status text overlays on the screen."""
        if not self.overlay_font: logger.warning("Overlay font not available."); return
        if self.menu_system is None: logger.error("MenuSystem not available."); return
        if self.screen is None: logger.error("Screen object is None."); return

        display_integration_time_ms = DEFAULT_INTEGRATION_TIME_MS
        current_internal_state_local = self._current_state # Cache

        try:
             if current_internal_state_local == self.STATE_FROZEN_VIEW and self._frozen_integration_ms is not None:
                  display_integration_time_ms = self._frozen_integration_ms
             elif current_internal_state_local != self.STATE_FROZEN_VIEW : # All other live/setup states
                  display_integration_time_ms = self.menu_system.get_integration_time_ms()
        except Exception as e_integ_get:
            logger.warning(f"Could not get integration time for overlay: {e_integ_get}")

        try:
            top_left_y_pos = 5; current_x_pos = 5; text_spacing = 10

            integ_text_str = f"Integ: {display_integration_time_ms} ms"
            integ_surf = self.overlay_font.render(integ_text_str, True, YELLOW)
            assert isinstance(integ_surf, pygame.Surface), "Integ text render failed"
            self.screen.blit(integ_surf, (current_x_pos, top_left_y_pos))
            current_x_pos += integ_surf.get_width() + text_spacing

            scans_text_str = f"Scans: {self._scans_today_count}"
            scans_surf = self.overlay_font.render(scans_text_str, True, YELLOW)
            assert isinstance(scans_surf, pygame.Surface), "Scans text render failed"
            self.screen.blit(scans_surf, (current_x_pos, top_left_y_pos))

            state_text_to_render = ""; state_color = YELLOW
            if current_internal_state_local == self.STATE_LIVE_VIEW:
                collection_mode_str = self.menu_system.get_collection_mode()
                state_text_to_render = f"Mode: {collection_mode_str.upper()}"
            elif current_internal_state_local == self.STATE_FROZEN_VIEW:
                frozen_type_disp = self._frozen_capture_type or "FROZEN"
                if self._frozen_capture_type == self.FROZEN_TYPE_OOI:
                    frozen_type_disp = self._frozen_sample_collection_mode or "SAMPLE"
                state_text_to_render = f"Mode: {frozen_type_disp.upper()} (FROZEN)"
                state_color = BLUE
            elif current_internal_state_local == self.STATE_CALIBRATE:
                state_text_to_render = "CALIBRATION MENU"
                state_color = GREEN
            elif current_internal_state_local == self.STATE_DARK_CAPTURE_SETUP:
                state_text_to_render = "Mode: DARK SETUP"
                state_color = RED
            elif current_internal_state_local == self.STATE_WHITE_CAPTURE_SETUP:
                state_text_to_render = "Mode: WHITE SETUP"
                state_color = CYAN
            else:
                state_text_to_render = f"Mode: {current_internal_state_local.upper()} (ERROR)"
                logger.error(f"Overlay: Unhandled state '{current_internal_state_local}' for mode text.")

            assert isinstance(state_text_to_render, str) and state_text_to_render, "State text invalid"
            state_surf = self.overlay_font.render(state_text_to_render, True, state_color)
            assert isinstance(state_surf, pygame.Surface), "State text render failed"
            state_rect = state_surf.get_rect(right=SCREEN_WIDTH - 5, top=top_left_y_pos)
            self.screen.blit(state_surf, state_rect)

            hint_text_str = ""
            if current_internal_state_local == self.STATE_LIVE_VIEW:
                hint_text_str = "A:Freeze | X:Calib Menu | Y:Rescale | B:Main Menu"
            elif current_internal_state_local == self.STATE_FROZEN_VIEW:
                hint_text_str = "A:Save Frozen | B:Discard Frozen"
            elif current_internal_state_local == self.STATE_CALIBRATE:
                hint_text_str = "A:White Setup | X:Dark Setup | B:Back (Live)"
            elif current_internal_state_local == self.STATE_DARK_CAPTURE_SETUP:
                hint_text_str = "A:Freeze Dark | B:Back (Calib Menu)"
            elif current_internal_state_local == self.STATE_WHITE_CAPTURE_SETUP:
                hint_text_str = "A:Freeze White | Y:Rescale | B:Back (Calib Menu)"

            if hint_text_str:
                hint_surf = self.overlay_font.render(hint_text_str, True, YELLOW)
                assert isinstance(hint_surf, pygame.Surface), "Hint text render failed"
                hint_rect = hint_surf.get_rect(centerx=SCREEN_WIDTH // 2, bottom=SCREEN_HEIGHT - 5)
                self.screen.blit(hint_surf, hint_rect)

        except pygame.error as e_render:
             logger.error(f"Pygame error rendering overlays: {e_render}", exc_info=True)
        except AssertionError as e_assert:
            logger.error(f"AssertionError rendering overlays: {e_assert}", exc_info=True)
        except Exception as e:
            logger.error(f"Unexpected error rendering overlays: {e}", exc_info=True)

    def draw(self):
        """Draws the spectrometer screen based on the current state."""
        if self.screen is None: logger.error("Screen object is None in SpectrometerScreen.draw."); return

        device_proxy = getattr(self.spectrometer, '_dev', None)
        spectrometer_can_plot_live = (
            USE_SPECTROMETER and self.spectrometer is not None and
            device_proxy and hasattr(device_proxy, 'is_open') and device_proxy.is_open
        )
        self.screen.fill(BLACK)

        if not spectrometer_can_plot_live and self._current_state != self.STATE_FROZEN_VIEW:
             if self.overlay_font:
                 err_text_str = "Spectrometer Not Ready"
                 if not USE_SPECTROMETER: err_text_str = "Spectrometer Disabled"
                 elif self.spectrometer is None: err_text_str = "Not Found"
                 elif not device_proxy or not hasattr(device_proxy, 'is_open'): err_text_str = "Backend Err"
                 elif not device_proxy.is_open : err_text_str = "Connect Err"
                 else: err_text_str = "Init Issue"
                 err_surf = self.overlay_font.render(err_text_str, True, RED)
                 assert isinstance(err_surf, pygame.Surface), "Error text render failed"
                 err_rect = err_surf.get_rect(center=self.screen.get_rect().center)
                 self.screen.blit(err_surf, err_rect)
        else:
            plot_surface = self._capture_and_plot()
            if plot_surface:
                 assert isinstance(plot_surface, pygame.Surface), "plot_surface invalid"
                 plot_rect = plot_surface.get_rect(centerx=SCREEN_WIDTH // 2, top=25)
                 plot_rect.clamp_ip(self.screen.get_rect())
                 self.screen.blit(plot_surface, plot_rect)
            else:
                 if self.overlay_font:
                     status_text_str = "Plot Error"
                     if self._current_state != self.STATE_FROZEN_VIEW and spectrometer_can_plot_live:
                         status_text_str = "Capturing..."
                     elif self._current_state != self.STATE_FROZEN_VIEW and not spectrometer_can_plot_live:
                         status_text_str = "Device Issue"
                     status_surf = self.overlay_font.render(status_text_str, True, GRAY)
                     assert isinstance(status_surf, pygame.Surface), "Status text render failed"
                     status_rect = status_surf.get_rect(center=self.screen.get_rect().center)
                     self.screen.blit(status_surf, status_rect)
        self._draw_overlays()
        update_hardware_display(self.screen, self.display_hat)

    def run_loop(self) -> str:
        """Runs the main loop for the Spectrometer screen."""
        logger.info(f"Starting Spectrometer screen loop (Initial State: {self._current_state}).")
        assert self.menu_system is not None, "MenuSystem is None at start of SpectrometerScreen.run_loop"

        while self.is_active and not g_shutdown_flag.is_set():
            action = self.handle_input()
            if action == "QUIT":
                logger.info("SpectrometerScreen.handle_input signaled QUIT.")
                self.deactivate(); return "QUIT"
            if action == "BACK_TO_MENU":
                logger.info("SpectrometerScreen.handle_input signaled BACK_TO_MENU.")
                self.deactivate(); return "BACK"

            self.draw()
            wait_time_ms = int(SPECTRO_LOOP_DELAY_S * 1000)
            try:
                if self._current_state not in [self.STATE_FROZEN_VIEW, self.STATE_CALIBRATE]: # Dynamic wait for live/setup
                     current_integ_ms_for_wait = self.menu_system.get_integration_time_ms()
                     assert isinstance(current_integ_ms_for_wait, int) and current_integ_ms_for_wait >= 0, \
                            f"Invalid integration time for wait calc: {current_integ_ms_for_wait}"
                     if current_integ_ms_for_wait > 0:
                         target_wait_s = (current_integ_ms_for_wait / 1000.0) + SPECTRO_REFRESH_OVERHEAD_S
                         wait_time_ms = int(max(SPECTRO_LOOP_DELAY_S, target_wait_s) * 1000)
            except Exception as e_wait_calc:
                logger.warning(f"Error calculating dynamic wait time: {e_wait_calc}. Using default.")
            assert isinstance(wait_time_ms, int) and wait_time_ms >= 0, f"Invalid wait_time_ms: {wait_time_ms}"
            pygame.time.wait(wait_time_ms)

        if self.is_active: self.deactivate()
        logger.info("Spectrometer screen loop finished.")
        return "QUIT" if g_shutdown_flag.is_set() else "BACK"

    def cleanup(self):
        """Cleans up spectrometer connection and plotting resources."""
        logger.info("Cleaning up SpectrometerScreen resources...")
        if self.spectrometer:
            try:
                device_proxy = getattr(self.spectrometer, '_dev', None)
                if device_proxy and hasattr(device_proxy, 'is_open') and device_proxy.is_open:
                     logger.info(f"Closing spectrometer connection: {self.spectrometer.serial_number}")
                     self.spectrometer.close()
                     logger.info("Spectrometer connection closed.")
                else:
                     logger.debug("Spectrometer already closed, invalid, or proxy not found during cleanup.")
            except sb.SeaBreezeError as e_sb_close:
                logger.error(f"SeaBreezeError closing spectrometer: {e_sb_close}", exc_info=True)
            except Exception as e:
                logger.error(f"Unexpected error closing spectrometer: {e}", exc_info=True)
        self.spectrometer = None

        if self.plot_fig and plt and plt.fignum_exists(self.plot_fig.number):
            try:
                plt.close(self.plot_fig)
                logger.info("Matplotlib plot figure closed.")
            except Exception as e_plot_close:
                logger.error(f"Error closing Matplotlib plot figure: {e_plot_close}", exc_info=True)
        self.plot_fig = self.plot_ax = self.plot_line = None
        logger.info("SpectrometerScreen cleanup complete.")
# --- Splash Screen Function ---
def show_splash_screen(screen: pygame.Surface, display_hat_obj, duration_s: float):
    """
    Displays the splash screen image for a specified duration.
    Args:
        screen: The Pygame Surface to draw on.
        display_hat_obj: The initialized DisplayHATMini object, or None.
        duration_s: How long to display the splash screen in seconds.
    """
    assert screen is not None, "Screen surface required for splash screen"
    assert isinstance(duration_s, (int, float)) and duration_s >= 0, "Splash duration must be a non-negative number"

    logger.info(f"Displaying splash screen for {duration_s:.1f} seconds...")
    splash_image_final = None
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        assets_dir = os.path.join(script_dir, 'assets')
        image_path = os.path.join(assets_dir, 'pysb-app.png')
        assert isinstance(image_path, str), "Splash image path is not string"

        if not os.path.isfile(image_path):
             logger.error(f"Splash screen image not found at: {image_path}")
             time.sleep(min(duration_s, 2.0))
             return

        splash_image_raw = pygame.image.load(image_path)
        assert isinstance(splash_image_raw, pygame.Surface), "Splash image load failed"
        logger.info(f"Loaded splash screen image: {image_path}")

        is_dummy_driver = os.environ.get('SDL_VIDEODRIVER') == 'dummy'
        # is_dummy_driver is inherently bool after comparison, no direct assert needed for its type
        # but can assert the result of os.environ.get if desired (str or None)

        if not is_dummy_driver and pygame.display.get_init() and pygame.display.get_surface():
            try:
                logger.debug("Attempting splash image conversion for standard display.")
                splash_image_final = splash_image_raw.convert()
                assert isinstance(splash_image_final, pygame.Surface), "Splash image convert failed"
            except pygame.error as convert_error:
                logger.warning(f"pygame.Surface.convert() failed: {convert_error}. Using raw surface.")
                splash_image_final = splash_image_raw
        else:
            logger.debug("Skipping splash image conversion (dummy driver or no video mode).")
            splash_image_final = splash_image_raw
        assert splash_image_final is not None, "Final splash image surface is None"

    except pygame.error as e:
        logger.error(f"Pygame error loading splash screen image: {e}", exc_info=True)
        time.sleep(min(duration_s, 2.0))
        return
    except FileNotFoundError:
        logger.error(f"Splash screen image file not found (exception): {image_path}")
        time.sleep(min(duration_s, 2.0))
        return
    except Exception as e:
        logger.error(f"An unexpected error occurred loading splash screen: {e}", exc_info=True)
        time.sleep(min(duration_s, 2.0))
        return

    if splash_image_final:
        try:
            screen.fill(BLACK)
            splash_rect = splash_image_final.get_rect()
            screen_rect = screen.get_rect()
            assert isinstance(splash_rect, pygame.Rect), "Splash rect calculation failed"
            assert isinstance(screen_rect, pygame.Rect), "Screen rect calculation failed"
            splash_rect.center = screen_rect.center
            screen.blit(splash_image_final, splash_rect)
            update_hardware_display(screen, display_hat_obj)

            wait_interval = 0.1
            assert isinstance(wait_interval, float), "Wait interval is not float"
            num_intervals = int(duration_s / wait_interval)
            assert isinstance(num_intervals, int), "Splash loop interval calculation failed"
            for _ in range(num_intervals):
                 if g_shutdown_flag.is_set():
                      logger.info("Shutdown requested during splash screen.")
                      break
                 time.sleep(wait_interval)
            logger.info("Splash screen finished.")
        except pygame.error as e:
             logger.error(f"Pygame error displaying splash screen: {e}", exc_info=True)
        except Exception as e:
             logger.error(f"Error displaying splash screen: {e}", exc_info=True)

# --- Disclaimer Screen Function ---
def show_disclaimer_screen(
    screen: pygame.Surface,
    display_hat_obj,
    button_handler: ButtonHandler,
    hint_font: pygame.font.Font # Only hint_font is needed
    ):
    """
    Displays a disclaimer message and waits for user acknowledgement.
    """
    assert screen is not None, "Screen surface required for disclaimer"
    assert button_handler is not None, "ButtonHandler required for disclaimer"
    assert hint_font is not None and isinstance(hint_font, pygame.font.Font), "Valid hint font object is required"

    logger.info("Displaying disclaimer screen...")
    disclaimer_font = None
    try:
        if not pygame.font.get_init(): pygame.font.init()
        assert pygame.font.get_init(), "Pygame font module not ready for disclaimer"
        script_dir = os.path.dirname(os.path.abspath(__file__))
        assets_dir = os.path.join(script_dir, 'assets')
        font_path = os.path.join(assets_dir, MAIN_FONT_FILENAME) # Use main font file for disclaimer
        assert isinstance(font_path, str), "Disclaimer font path is not string"

        if not os.path.isfile(font_path):
            logger.error(f"Disclaimer font file not found: {font_path}. Using fallback.")
            disclaimer_font = pygame.font.SysFont(None, DISCLAIMER_FONT_SIZE)
        else:
            try:
                disclaimer_font = pygame.font.Font(font_path, DISCLAIMER_FONT_SIZE)
                logger.info(f"Loaded disclaimer font: {font_path} (Size: {DISCLAIMER_FONT_SIZE})")
            except pygame.error as e:
                logger.error(f"Failed to load font '{font_path}' size {DISCLAIMER_FONT_SIZE}: {e}. Using fallback.")
                disclaimer_font = pygame.font.SysFont(None, DISCLAIMER_FONT_SIZE)
        assert disclaimer_font is not None, "Disclaimer font failed to load even with fallback"
    except Exception as e:
        logger.error(f"Error loading disclaimer font: {e}", exc_info=True)
        try: disclaimer_font = pygame.font.SysFont(None, DISCLAIMER_FONT_SIZE)
        except Exception as e_sys: logger.critical(f"FATAL: Could not load any font for disclaimer: {e_sys}"); return

    if not disclaimer_font: logger.error("No font for disclaimer. Cannot display."); return

    try:
        lines = DISCLAIMER_TEXT.splitlines()
        rendered_lines = []
        max_width = 0; total_height = 0; line_spacing = 4
        assert isinstance(lines, list), "Disclaimer text did not split correctly"
        assert isinstance(line_spacing, int), "Line spacing must be int"

        for line_text in lines:
            assert isinstance(line_text, str), "Line in disclaimer is not string"
            if line_text.strip():
                line_surface = disclaimer_font.render(line_text, True, WHITE)
                assert isinstance(line_surface, pygame.Surface), f"Disclaimer line render failed: '{line_text}'"
                rendered_lines.append(line_surface)
                max_width = max(max_width, line_surface.get_width())
                total_height += line_surface.get_height() + line_spacing
            else: # Blank line
                rendered_lines.append(None)
                total_height += (disclaimer_font.get_height() // 2) + line_spacing
            assert isinstance(total_height, int), "Disclaimer total height calc failed"
        if total_height > 0: total_height -= line_spacing # Remove last spacing

        hint_text_str = "Press A or B to continue..."
        hint_surface = hint_font.render(hint_text_str, True, YELLOW) # Use passed hint_font
        assert isinstance(hint_surface, pygame.Surface), "Disclaimer hint render failed"
        total_height += hint_surface.get_height() + 10 # Space for hint
        assert isinstance(total_height, int), "Disclaimer total height calc failed after hint"

        start_y = max(10, (screen.get_height() - total_height) // 2)
        assert isinstance(start_y, int), "Disclaimer start_y calc failed"

        screen.fill(BLACK)
        current_y = start_y
        for surface in rendered_lines:
            if surface:
                assert isinstance(surface, pygame.Surface), "Invalid surface in rendered_lines"
                line_rect = surface.get_rect(centerx=screen.get_width() // 2, top=current_y)
                screen.blit(surface, line_rect)
                current_y += surface.get_height() + line_spacing
            else: # Blank line spacing
                current_y += (disclaimer_font.get_height() // 2) + line_spacing
            assert isinstance(current_y, int), "Disclaimer current_y update failed"

        hint_rect = hint_surface.get_rect(centerx=screen.get_width() // 2, top=current_y + 10)
        screen.blit(hint_surface, hint_rect)
        update_hardware_display(screen, display_hat_obj)

    except pygame.error as e: logger.error(f"Pygame error preparing/drawing disclaimer: {e}", exc_info=True); return
    except Exception as e: logger.error(f"Unexpected error preparing/drawing disclaimer: {e}", exc_info=True); return

    logger.info("Waiting for user acknowledgement on disclaimer screen...")
    acknowledged = False
    while not acknowledged and not g_shutdown_flag.is_set():
        assert isinstance(acknowledged, bool), "acknowledged flag is not bool"
        # g_shutdown_flag.is_set() is bool
        quit_signal = button_handler.process_pygame_events()
        if quit_signal == "QUIT":
             assert quit_signal == "QUIT", "Invalid quit signal received"
             logger.warning("QUIT signal received during disclaimer.")
             g_shutdown_flag.set() # Propagate quit
             continue # Loop will terminate
        if button_handler.check_button(BTN_ENTER) or button_handler.check_button(BTN_BACK):
            acknowledged = True
            logger.info("Disclaimer acknowledged by user.")
        pygame.time.wait(50) # Small delay

    if not acknowledged: logger.warning("Exited disclaimer wait due to shutdown signal.")
    else: logger.info("Disclaimer screen finished.")

# --- Signal Handling ---
def setup_signal_handlers(button_handler: ButtonHandler, network_info: NetworkInfo):
    """Sets up signal handlers for graceful shutdown."""
    assert button_handler is not None, "Button handler required for signal handler setup"
    assert network_info is not None, "Network info required for signal handler setup"

    def signal_handler(sig, frame):
        if not g_shutdown_flag.is_set():
            logger.warning(f"Received signal {sig}. Initiating graceful shutdown...")
            g_shutdown_flag.set()
        else:
             logger.debug(f"Signal {sig} received again, shutdown already in progress.")

    try:
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
        logger.info("Signal handlers set up for SIGINT and SIGTERM.")
    except ValueError as e:
         logger.error(f"Failed to set signal handlers: {e}. Shutdown via Ctrl+C might not be clean.")
    except Exception as e:
         logger.error(f"Unexpected error setting signal handlers: {e}", exc_info=True)

# --- Helper Functions ---
def get_safe_datetime(year, month, day, hour=0, minute=0, second=0):
    """
    Attempts to create a datetime object, handling potential ValueErrors.
    Returns the new datetime object or None if invalid.
    """
    assert isinstance(year, int), "Year must be an integer"
    assert isinstance(month, int), "Month must be an integer"
    assert isinstance(day, int), "Day must be an integer"
    assert isinstance(hour, int), "Hour must be an integer"
    assert isinstance(minute, int), "Minute must be an integer"
    assert isinstance(second, int), "Second must be an integer"
    try:
        month_clamped = max(1, min(12, month)) # Clamp month for safety before creating datetime
        # Datetime constructor handles day clamping based on month/year
        new_dt = datetime.datetime(year, month_clamped, day, hour, minute, second)
        return new_dt
    except ValueError as e: # Catches invalid day for month/year, etc.
        logger.warning(f"Invalid date/time combination: Y{year}-M{month}-D{day} H{hour}:M{minute}:S{second}. Error: {e}")
        return None

def show_leak_warning_screen(screen: pygame.Surface, display_hat_obj, button_handler: ButtonHandler):
    assert screen is not None and button_handler is not None
    logger.critical("Displaying LEAK WARNING screen!")
    leak_font_large, leak_font_small = None, None
    try:
        if not pygame.font.get_init(): pygame.font.init()
        leak_font_large = pygame.font.SysFont(None, 60); leak_font_small = pygame.font.SysFont(None, 24)
        assert leak_font_large and leak_font_small, "Failed font load for leak warning"
    except Exception as e: logger.error(f"Could not load fonts for leak warning: {e}")

    screen_center_x, screen_center_y = screen.get_width() // 2, screen.get_height() // 2
    last_blink_time, show_text_blink = time.monotonic(), True # Renamed show_text to avoid conflict

    # Loop while leak detected and shutdown not otherwise signaled
    while g_leak_detected_flag.is_set() and not g_shutdown_flag.is_set():
        if button_handler.process_pygame_events() == "QUIT":
            g_shutdown_flag.set() # Propagate QUIT signal
            break # Exit loop immediately

        screen.fill(RED) # Constant red background

        # Blinking text logic
        if time.monotonic() - last_blink_time > 0.5:
            show_text_blink = not show_text_blink
            last_blink_time = time.monotonic()

        if show_text_blink and leak_font_large and leak_font_small: # Ensure fonts are loaded
            try:
                texts_to_render = [("! LEAK !", leak_font_large, -30),
                                   ("WATER DETECTED!", leak_font_small, 20),
                                   ("Press ANY btn to shutdown.", leak_font_small, 50)]
                # Bounded loop by number of texts
                for txt_content, font_obj, y_offset in texts_to_render:
                    surf = font_obj.render(txt_content, True, YELLOW, RED) # Yellow on Red
                    rect = surf.get_rect(center=(screen_center_x, screen_center_y + y_offset))
                    screen.blit(surf, rect)
            except Exception as e_render: logger.error(f"Error rendering leak text: {e_render}")

        update_hardware_display(screen, display_hat_obj)

        # Check for button press to acknowledge and initiate shutdown
        # Bounded loop (fixed number of buttons to check)
        for btn_name_check in [BTN_UP, BTN_DOWN, BTN_ENTER, BTN_BACK]:
            if button_handler.check_button(btn_name_check):
                logger.warning(f"Leak warning acknowledged by {btn_name_check}. Shutting down application.")
                g_shutdown_flag.set() # Signal application shutdown
                break # Exit button check loop
        if g_shutdown_flag.is_set(): break # Exit outer while loop if shutdown initiated

        pygame.time.wait(100) # Brief pause

    logger.info("Exiting leak warning screen (shutdown likely initiated).")
    return "QUIT" # Always signal QUIT to main loop from here


def update_hardware_display(screen: pygame.Surface, display_hat_obj):
    """
    Updates the physical display (Pimoroni or standard Pygame window).
    Args:
        screen: The Pygame Surface to display.
        display_hat_obj: The initialized DisplayHATMini object, or None.
    """
    assert screen is not None, "Screen surface cannot be None for display update"

    if USE_DISPLAY_HAT and display_hat_obj:
        try:
            assert hasattr(display_hat_obj, 'st7789'), "Display HAT object missing st7789 interface"
            assert hasattr(display_hat_obj.st7789, 'set_window'), "st7789 missing set_window"
            assert hasattr(display_hat_obj.st7789, 'data'), "st7789 missing data method"

            rotated_surface = pygame.transform.rotate(screen, 180)
            # Ensure the surface is in a format suitable for ST7789 (RGB565)
            # Pygame's convert(16, 0) might not be exactly RGB565 depending on system/pygame build
            # For ST7789, an explicit conversion to RGB565 byte order is often needed if `convert` isn't perfect.
            # The provided code attempts byte swapping, which is common for this.
            pixelbytes_raw = rotated_surface.convert(16, 0).get_buffer() # Get raw buffer
            pixelbytes_swapped = bytearray(pixelbytes_raw) # Modifiable copy

            # Perform byte swapping for ST7789 (big-endian to little-endian for 16-bit words, or vice-versa)
            # Loop is bounded by length of pixelbytes_swapped
            for i in range(0, len(pixelbytes_swapped), 2):
                pixelbytes_swapped[i], pixelbytes_swapped[i+1] = pixelbytes_swapped[i+1], pixelbytes_swapped[i]

            display_hat_obj.st7789.set_window()
            chunk_size = 4096
            # Loop is bounded by length of pixelbytes_swapped / chunk_size
            for i in range(0, len(pixelbytes_swapped), chunk_size):
                display_hat_obj.st7789.data(pixelbytes_swapped[i:i + chunk_size])
        except AttributeError as ae:
             logger.error(f"Display HAT update failed: Missing attribute/method - {ae}", exc_info=False)
        except Exception as e:
            logger.error(f"Error updating Display HAT Mini: {e}", exc_info=False) # Log exc_info=False for less noise if frequent
    else:
        try:
             if pygame.display.get_init() and pygame.display.get_surface():
                  pygame.display.flip()
        except pygame.error as e:
             logger.error(f"Error updating Pygame display (flip): {e}", exc_info=True)
        except Exception as e:
             logger.error(f"Unexpected error updating Pygame display: {e}", exc_info=True)

# --- Main Application ---
def main():
    """Main application entry point."""
    logger.info("=" * 44)
    logger.info("   Underwater Spectrometer Controller Start ")
    logger.info("=" * 44)
    logger.info(f"Config: DisplayHAT={USE_DISPLAY_HAT}, GPIO={USE_GPIO_BUTTONS}, HallSensors={USE_HALL_EFFECT_BUTTONS}, LeakSensor={USE_LEAK_SENSOR}, Spectrometer={USE_SPECTROMETER}")

    display_hat_active = False
    display_hat = None
    screen = None
    button_handler = None
    network_info = None
    menu_system = None
    spectrometer_screen = None
    main_clock = None

    try:
        logger.info("Initializing Pygame and display...")
        try:
             pygame.init()
             assert pygame.get_init(), "Pygame initialization failed"
             main_clock = pygame.time.Clock()
             assert main_clock is not None, "Pygame clock initialization failed"
        except pygame.error as e:
             logger.critical(f"FATAL: Pygame initialization failed: {e}", exc_info=True)
             raise RuntimeError("Pygame init failed") from e

        if USE_DISPLAY_HAT and DisplayHATMini_lib:
            try:
                os.environ['SDL_VIDEODRIVER'] = 'dummy'
                pygame.display.init()
                assert pygame.display.get_init(), "Pygame display module failed (dummy)"
                screen = pygame.Surface((SCREEN_WIDTH, SCREEN_HEIGHT))
                assert screen is not None, "Failed to create screen buffer for HAT"
                display_hat = DisplayHATMini_lib(screen)
                assert display_hat is not None, "DisplayHATMini object creation failed"
                display_hat_active = True
                logger.info("DisplayHATMini initialized with dummy driver.")
            except Exception as e:
                logger.error(f"Failed to initialize DisplayHATMini: {e}", exc_info=True)
                logger.warning("Falling back to standard Pygame window.")
                display_hat_active = False; display_hat = None
                os.environ.pop('SDL_VIDEODRIVER', None)
                if pygame.display.get_init(): pygame.display.quit()
                pygame.display.init()
                assert pygame.display.get_init(), "Pygame display module failed (fallback)"
        else:
            logger.info("Configured for standard Pygame window.")
            if not pygame.display.get_init(): pygame.display.init()
            assert pygame.display.get_init(), "Pygame display module failed (standard)"

        if screen is None:
            try:
                screen = pygame.display.set_mode((SCREEN_WIDTH, SCREEN_HEIGHT))
                pygame.display.set_caption("Spectrometer Menu")
                logger.info("Initialized standard Pygame display window.")
            except pygame.error as e:
                logger.critical(f"FATAL: Failed to create Pygame screen: {e}", exc_info=True)
                raise RuntimeError("Display surface creation failed") from e
        assert screen is not None, "Failed to create Pygame screen surface"

        logger.info("Initializing core components...")
        network_info = NetworkInfo()
        button_handler = ButtonHandler(display_hat if display_hat_active else None)
        menu_system = MenuSystem(screen, button_handler, network_info)

        if USE_SPECTROMETER:
             spectrometer_screen = SpectrometerScreen(
                 screen, button_handler, menu_system,
                 display_hat if display_hat_active else None
             )
             assert spectrometer_screen is not None, "SpectrometerScreen creation failed"

        if display_hat_active:
            assert menu_system is not None, "MenuSystem not created for display_hat assign"
            menu_system.display_hat = display_hat # For menu to update HAT
            # SpectrometerScreen already received display_hat in its constructor

        assert network_info is not None, "NetworkInfo failed init"
        assert button_handler is not None, "ButtonHandler failed init"
        assert menu_system is not None and menu_system.font is not None, "MenuSystem or its font failed init"
        if USE_SPECTROMETER:
            assert spectrometer_screen is not None, "SpectrometerScreen failed init when configured"

        show_splash_screen(screen, display_hat if display_hat_active else None, SPLASH_DURATION_S)

        if not g_shutdown_flag.is_set():
             assert menu_system.hint_font is not None, "Hint font not loaded for disclaimer"
             show_disclaimer_screen(screen,
                                   display_hat if display_hat_active else None,
                                   button_handler,
                                   menu_system.hint_font)

        if g_shutdown_flag.is_set():
            logger.warning("Shutdown requested during startup screens. Exiting.")
            raise SystemExit("Shutdown during startup")

        logger.info("Setting up signal handlers and starting background tasks...")
        setup_signal_handlers(button_handler, network_info)
        network_info.start_updates()

        logger.info("Starting main application loop...")
        current_screen_state = "MENU" # "MENU" or "SPECTROMETER"
        # Loop bound by global shutdown flag
        while not g_shutdown_flag.is_set():
            assert isinstance(g_shutdown_flag.is_set(), bool), "g_shutdown_flag state is not bool"

            # --- Global Leak Check ---
            if g_leak_detected_flag.is_set():
                logger.critical("Leak detected flag is set! Switching to leak warning screen.")
                leak_screen_action = show_leak_warning_screen(screen, display_hat if display_hat_active else None, button_handler)
                if leak_screen_action == "QUIT" or g_shutdown_flag.is_set():
                    logger.warning("Leak warning screen signaled QUIT or shutdown flag was set. Terminating.")
                    if not g_shutdown_flag.is_set(): g_shutdown_flag.set() # Ensure it's set
                    break # Exit main loop

            if current_screen_state == "MENU":
                menu_action = menu_system.handle_input()
                if menu_action == "QUIT":
                    logger.info("Menu signaled QUIT.")
                    g_shutdown_flag.set()
                elif menu_action == "START_CAPTURE":
                    if USE_SPECTROMETER and spectrometer_screen:
                        logger.info("Switching to Spectrometer screen...")
                        spectrometer_screen.activate()
                        current_screen_state = "SPECTROMETER"
                        continue # Skip menu draw this iteration
                    else:
                        logger.warning("START_CAPTURE requested, but spectrometer not available/configured.")
                        # Stay in menu, perhaps show a message on screen if desired
                if not g_shutdown_flag.is_set(): # Only draw if not shutting down
                    menu_system.draw()
                assert main_clock is not None, "Main clock not initialized for menu tick"
                main_clock.tick(1.0 / MAIN_LOOP_DELAY_S)

            elif current_screen_state == "SPECTROMETER":
                assert USE_SPECTROMETER and spectrometer_screen is not None, "Spectrometer state without screen"
                spectro_status = spectrometer_screen.run_loop() # Handles its own input, draw, timing
                if spectro_status == "QUIT":
                    logger.info("Spectrometer screen signaled QUIT.")
                    if not g_shutdown_flag.is_set(): g_shutdown_flag.set() # Ensure shutdown
                elif spectro_status == "BACK":
                    logger.info("Returning to Menu screen from Spectrometer.")
                    current_screen_state = "MENU"
                # run_loop ensures spectrometer_screen.deactivate() is called
            else:
                logger.error(f"FATAL: Unknown screen state '{current_screen_state}'")
                g_shutdown_flag.set()

    except SystemExit as e:
        logger.warning(f"Exiting due to SystemExit: {e}")
    except RuntimeError as e:
        logger.critical(f"RUNTIME ERROR: {e}", exc_info=True)
        g_shutdown_flag.set()
    except KeyboardInterrupt:
         logger.warning("KeyboardInterrupt caught. Initiating shutdown...")
         g_shutdown_flag.set()
    except Exception as e:
        logger.critical(f"FATAL UNHANDLED EXCEPTION in main: {e}", exc_info=True)
        g_shutdown_flag.set()
    finally:
        logger.warning("Initiating final cleanup...")
        if network_info:
            logger.debug("Stopping network info...")
            try: network_info.stop_updates()
            except Exception as e_ni_stop: logger.error(f"Error stopping network_info: {e_ni_stop}")
        if spectrometer_screen:
            logger.debug("Cleaning up spectrometer screen...")
            try: spectrometer_screen.cleanup()
            except Exception as e_spec_clean: logger.error(f"Error cleaning spectrometer_screen: {e_spec_clean}")
        if menu_system:
            logger.debug("Cleaning up menu system...")
            try: menu_system.cleanup()
            except Exception as e_menu_clean: logger.error(f"Error cleaning menu_system: {e_menu_clean}")
        if button_handler:
            logger.debug("Cleaning up button handler / GPIO...")
            try: button_handler.cleanup()
            except Exception as e_btn_clean: logger.error(f"Error cleaning button_handler: {e_btn_clean}")
        if pygame.get_init():
             logger.info("Quitting Pygame...")
             try: pygame.quit()
             except Exception as e_pq_quit: logger.error(f"Error quitting Pygame: {e_pq_quit}")
             logger.info("Pygame quit.")
        else:
             logger.info("Pygame not initialized, skipping quit.")
        logger.info("=" * 44); logger.info("   Application Finished."); logger.info("=" * 44)

if __name__ == "__main__":
    main()
