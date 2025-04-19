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
import RPi.GPIO # Placeholder for type hinting
import io         # For in-memory plot rendering
import csv        # For future data saving
import numpy as np # Might need later for data manipulation

# --- Configuration Flags ---
# Set these flags based on the hardware connected.
# If a flag is True, the code will expect the hardware to be present and attempt initialization.
# If initialization fails despite the flag being True, an error will be logged.
USE_DISPLAY_HAT = True       # Set to True if Pimoroni Display HAT Mini is connected
USE_GPIO_BUTTONS = True      # Set to True if GPIO (LCD/Hall) buttons are connected
USE_HALL_EFFECT_BUTTONS = False # Set to True to map external Hall sensors (requires USE_GPIO_BUTTONS=True)
USE_LEAK_SENSOR = False        # Set to True if the external leak sensor is connected (requires USE_GPIO_BUTTONS=True)
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
# <<< NEW: Add pyusb import if needed for USBError catch >>>
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

        # <<< NEW: Import usb.core specifically for catching USBError >>>
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
CSV_FILENAME = os.path.join(DATA_DIR, "spectra_log.csv")
PLOT_SAVE_DIR = DATA_DIR # Save plots in the same directory

# Integration Time (ms)
INTEGRATION_TIME_SCALE_FACTOR = 10.0 # Factor to multiply calculated microseconds by
DEFAULT_INTEGRATION_TIME_MS = 500
MIN_INTEGRATION_TIME_MS = 100
MAX_INTEGRATION_TIME_MS = 6000 # Increased max based on spectrometer
INTEGRATION_TIME_STEP_MS = 100

# Plotting Constants
LIVE_SMOOTHING_WINDOW_SIZE = 9
Y_AXIS_DEFAULT_MAX = 1000
Y_AXIS_RESCALE_FACTOR = 1.2
Y_AXIS_MIN_CEILING = 60
Y_AXIS_MIN_CEILING_RELATIVE = 1.1 
INTEGRATION_TIME_SCALE_FACTOR = 10.0 

# GPIO Pin Definitions (BCM Mode)
# --- Display HAT Mini Buttons (Corrected based on Pimoroni library standard) ---
PIN_DH_A = 5   # Was 16. Physical Button A (maps to Enter/Right logic) -> GPIO 5
PIN_DH_B = 6   # Was 6. Physical Button B (maps to Back/Left logic) -> GPIO 6
PIN_DH_X = 16  # Was 12. Physical Button X (maps to Up logic) -> GPIO 16
PIN_DH_Y = 24  # Was 13. Physical Button Y (maps to Down logic) -> GPIO 24

# --- External Hall Effect Sensor Pins (Check these carefully for your wiring) ---
# Ensure these DO NOT conflict with DH pins 5, 6, 16, 24 if both are used
PIN_HALL_UP = 18     # Mirrors DH X (Up) -> Check if 18 is okay
PIN_HALL_DOWN = 23   # Mirrors DH Y (Down) -> Check if 23 is okay
PIN_HALL_ENTER = 20  # Mirrors DH A (Enter/Right) -> Check if 20 is okay
PIN_HALL_BACK = 8    # Mirrors DH B (Back/Left) -> Check if 8 is okay

# --- Leak Sensor Pin ---
PIN_LEAK = 21 # Changed from 26 in original code to match user request

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
FONT_SIZE = 18
TITLE_FONT_SIZE = 24
HINT_FONT_SIZE = 14
DISCLAIMER_FONT_SIZE = 14
MENU_SPACING = 26
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

# --- Logging Setup ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- Global Variables ---
# These are managed primarily within classes or the main function after init
g_shutdown_flag = threading.Event() # Used to signal shutdown to threads and loops

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
                         RPi_GPIO_lib.add_event_detect(PIN_LEAK, GPIO.FALLING, callback=self._leak_callback, bouncetime=1000)
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
        # Future: Trigger visual alert, log persistent flag, attempt safe shutdown?
        # Consider setting g_shutdown_flag here for immediate shutdown?
        # g_shutdown_flag.set() # Uncomment for emergency stop on leak

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
    MENU_ITEM_DATE = "DATE"
    MENU_ITEM_TIME = "TIME"
    MENU_ITEM_WIFI = "WIFI"
    MENU_ITEM_IP = "IP"

    EDIT_TYPE_NONE = 0
    EDIT_TYPE_INTEGRATION = 1
    EDIT_TYPE_DATE = 2
    EDIT_TYPE_TIME = 3

    # Fields for date/time editing
    FIELD_YEAR = 'year'
    FIELD_MONTH = 'month'
    FIELD_DAY = 'day'
    FIELD_HOUR = 'hour'
    FIELD_MINUTE = 'minute'
    # FIELD_SECOND = 'second' # Seconds editing removed for simplicity

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
        # Store offset from system time, not an absolute editable time
        self._time_offset = datetime.timedelta(0)
        # Store offset before editing starts, for discard/revert
        self._original_offset_on_edit_start: datetime.timedelta | None = None
        # Store the absolute datetime being manipulated *during* edits
        self._datetime_being_edited: datetime.datetime | None = None

        # --- Menu Structure ---
        # Use tuples for immutability where appropriate
        self._menu_items = (
            (self.MENU_ITEM_CAPTURE, self.EDIT_TYPE_NONE),
            (self.MENU_ITEM_INTEGRATION, self.EDIT_TYPE_INTEGRATION),
            (self.MENU_ITEM_DATE, self.EDIT_TYPE_DATE),
            (self.MENU_ITEM_TIME, self.EDIT_TYPE_TIME),
            (self.MENU_ITEM_WIFI, self.EDIT_TYPE_NONE),
            (self.MENU_ITEM_IP, self.EDIT_TYPE_NONE),
        )
        self._current_selection_idx = 0
        # Assertion: Ensure menu items exist
        assert len(self._menu_items) > 0, "Menu items list cannot be empty"

        # --- Editing State ---
        self._is_editing = False
        self._editing_field: str | None = None # e.g. FIELD_YEAR

        # --- Font Initialization ---
        self.font: pygame.font.Font | None = None
        self.title_font: pygame.font.Font | None = None
        self.hint_font: pygame.font.Font | None = None
        self._value_start_offset_x = 120 # Default/fallback value alignment X pos

        self._load_fonts() # Encapsulate font loading
        if self.font:
             # Assertion: Check font loaded successfully
             assert self.font is not None, "Main font should be loaded before calculating offset"
             self._calculate_value_offset() # Calculate alignment if fonts loaded
        else:
             logger.error("Main font failed to load; cannot calculate value offset.")


    def _load_fonts(self):
        """Loads fonts from the assets folder. Uses global constants for filenames."""
        try:
            # Check Pygame font module initialization
            if not pygame.font.get_init():
                logger.info("Initializing Pygame font module.")
                pygame.font.init()
            # Assertion: Pygame font module must be initialized
            assert pygame.font.get_init(), "Pygame font module failed to initialize"


            logger.info("Loading fonts from assets folder...")

            # --- Get the absolute path to the script's directory ---
            script_dir = os.path.dirname(os.path.abspath(__file__))
            assets_dir = os.path.join(script_dir, 'assets')
            # Assertion: Check assets dir exists? Maybe too strict, handle missing files below.
            # assert os.path.isdir(assets_dir), f"Assets directory not found: {assets_dir}"

            # --- Define paths using centralized constants ---
            title_font_path = os.path.join(assets_dir, TITLE_FONT_FILENAME)
            main_font_path = os.path.join(assets_dir, MAIN_FONT_FILENAME)
            hint_font_path = os.path.join(assets_dir, HINT_FONT_FILENAME)

            # Assertion: Check paths are strings
            assert isinstance(title_font_path, str), "Title font path is not a string"
            assert isinstance(main_font_path, str), "Main font path is not a string"
            assert isinstance(hint_font_path, str), "Hint font path is not a string"

            # --- Load fonts with error handling ---
            try:
                # Check file exists before loading
                if not os.path.isfile(title_font_path):
                    logger.error(f"Title font file not found: '{title_font_path}'. Using fallback.")
                    self.title_font = pygame.font.SysFont(None, TITLE_FONT_SIZE)
                else:
                    self.title_font = pygame.font.Font(title_font_path, TITLE_FONT_SIZE)
                    logger.info(f"Loaded title font: {title_font_path}")
            except pygame.error as e: # Catch specific Pygame errors
                logger.error(f"Failed to load title font '{title_font_path}' using Pygame: {e}. Using fallback.")
                self.title_font = pygame.font.SysFont(None, TITLE_FONT_SIZE) # Fallback
            except Exception as e: # Catch other potential errors
                logger.error(f"Unexpected error loading title font '{title_font_path}': {e}. Using fallback.", exc_info=True)
                self.title_font = pygame.font.SysFont(None, TITLE_FONT_SIZE)

            # Repeat for main font
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

            # Repeat for hint font
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

            # Final check if *essential* fonts are usable
            if not self.font: # Main font is essential for menu items
                 logger.critical("Essential main font failed to load, even with fallbacks. Menu will likely fail.")
                 # raise RuntimeError("Essential fonts failed to load") # Can be too harsh
            # Assertion: Ensure fonts are Font objects or None after loading
            assert isinstance(self.title_font, (pygame.font.Font, type(None))), "Title font is invalid type"
            assert isinstance(self.font, (pygame.font.Font, type(None))), "Main font is invalid type"
            assert isinstance(self.hint_font, (pygame.font.Font, type(None))), "Hint font is invalid type"


        except Exception as e:
            logger.critical(f"Critical error during Pygame font initialization/loading: {e}", exc_info=True)
            # Ensure fonts are None if init fails badly
            self.font = None
            self.title_font = None
            self.hint_font = None
            # Optional: raise RuntimeError("Font initialization failed")

    def _calculate_value_offset(self):
        """Calculates the X offset for aligned value display based on label widths."""
        # Assertion: Must have main font loaded
        assert self.font is not None, "Cannot calculate value offset without main font."
        try:
            max_label_width = 0
            # Define the prefixes used for labels that have values next to them
            # These should match exactly how they are rendered in _draw_menu_items
            label_prefixes = {
                self.MENU_ITEM_INTEGRATION: "INTEGRATION:",
                self.MENU_ITEM_DATE: "DATE:",
                self.MENU_ITEM_TIME: "TIME:",
                self.MENU_ITEM_WIFI: "WIFI:",
                self.MENU_ITEM_IP: "IP:"
            }

            # Iterate through menu items to find the longest relevant label prefix
            # Loop has fixed upper bound based on menu items (tuple length)
            for item_text, _ in self._menu_items:
                 prefix = label_prefixes.get(item_text)
                 if prefix: # Only consider items with a defined prefix
                      # Assertion: Check prefix is string
                      assert isinstance(prefix, str), f"Label prefix for {item_text} is not a string"
                      # Calculate width using the loaded font
                      label_width = self.font.size(prefix)[0]
                      max_label_width = max(max_label_width, label_width)

            # Add a small gap after the longest label for visual separation
            label_gap = 8 # Adjusted gap
            # Assertion: Ensure calculated offset is numeric
            assert isinstance(max_label_width, (int, float)), "Max label width calculation failed"
            self._value_start_offset_x = int(max_label_width + label_gap) # Ensure integer
            logger.info(f"Calculated value start offset X: {self._value_start_offset_x} (based on max label width {max_label_width})")

        except Exception as e:
            logger.error(f"Failed to calculate value start offset: {e}. Using default fallback {self._value_start_offset_x}.")
            # Keep the default fallback value set in __init__
            self._value_start_offset_x = 120 # Reset to default

    # --- Helper to get the time to display/use ---
    def _get_current_app_display_time(self) -> datetime.datetime:
        """Calculates the current time including the user-defined offset."""
        # Use timezone-naive datetime objects for simplicity here
        # Be aware of potential issues if system time transitions DST while app is running
        # For simple offset, naive should be okay.
        # Assertion: Ensure offset is timedelta
        assert isinstance(self._time_offset, datetime.timedelta), "Time offset is not a timedelta object"
        try:
            now = datetime.datetime.now()
            # Assertion: Check 'now' is datetime object
            assert isinstance(now, datetime.datetime), "datetime.now() returned unexpected type"
            app_time = now + self._time_offset
            # Assertion: Check 'app_time' is datetime object
            assert isinstance(app_time, datetime.datetime), "Time calculation resulted in unexpected type"
            return app_time
        except OverflowError:
            logger.warning("Time offset resulted in datetime overflow. Resetting offset.")
            self._time_offset = datetime.timedelta(0)
            # Assertion: Ensure offset is reset correctly
            assert self._time_offset == datetime.timedelta(0), "Offset reset failed"
            return datetime.datetime.now()

    # --- Public Methods ---

    def get_integration_time_ms(self) -> int:
        """Returns the currently configured integration time in milliseconds."""
        # Assertion: Ensure return value is int
        assert isinstance(self._integration_time_ms, int), "Internal integration time is not int"
        return self._integration_time_ms

    def get_timestamp_datetime(self) -> datetime.datetime:
        """Returns a datetime object representing the current app time (System + Offset)."""
        # Useful for getting the time to embed in filenames etc.
        dt = self._get_current_app_display_time()
        # Assertion: Ensure return value is datetime
        assert isinstance(dt, datetime.datetime), "get_current_app_display_time returned invalid type"
        return dt

    def handle_input(self) -> str | None:
        """
        Processes button inputs based on the current menu state (navigation or editing).
        Returns "QUIT" to signal application exit, "CAPTURE" to start capture, or None otherwise.
        """
        # 1. Process Pygame events first (catches window close, escape key)
        pygame_event_result = self.button_handler.process_pygame_events()
        if pygame_event_result == "QUIT":
            # Assertion: Check return value validity
            assert pygame_event_result == "QUIT", "process_pygame_events returned unexpected value"
            return "QUIT" # Exit signal

        # 2. Handle button presses based on state
        action = None
        if self._is_editing:
            # Assertion: Editing field must be set if editing (except for integration time)
            item_text, edit_type = self._menu_items[self._current_selection_idx]
            assert self._editing_field is not None or edit_type == self.EDIT_TYPE_INTEGRATION, \
                   f"Inconsistent editing state: _is_editing=True but _editing_field is None for type {edit_type}"
            action = self._handle_editing_input()
        else:
            # Assertion: Should not be editing
            assert not self._is_editing and self._editing_field is None, "Navigation input called while editing state is inconsistent"
            action = self._handle_navigation_input()

        # Assertion: Check action type (str or None)
        assert isinstance(action, (str, type(None))), "Input handler returned unexpected type"


        # 3. Process actions returned by input handlers
        if action == "EXIT_EDIT_SAVE":
            self._is_editing = False
            self._editing_field = None
            if self._datetime_being_edited is not None: # Only commit if a datetime was edited
                 # Assertion: Check object type before commit
                 assert isinstance(self._datetime_being_edited, datetime.datetime), "Cannot commit invalid datetime object"
                 self._commit_time_offset_changes()
            # Clear temporary edit state regardless
            self._datetime_being_edited = None
            self._original_offset_on_edit_start = None
            logger.info("Exited editing mode, changes saved (if any).")
            return None # Stay in menu
        elif action == "EXIT_EDIT_DISCARD":
            self._is_editing = False
            self._editing_field = None
            # Restore the original offset if it was saved
            if self._original_offset_on_edit_start is not None:
                # Assertion: Check original offset type
                assert isinstance(self._original_offset_on_edit_start, datetime.timedelta), "Cannot restore invalid offset type"
                self._time_offset = self._original_offset_on_edit_start
                logger.info("Exited editing mode, time offset changes discarded.")
            else:
                # This case shouldn't happen if logic is correct, but log if it does
                logger.warning("Exited editing mode via BACK, but no original offset found to revert to.")
            # Clear temporary edit state
            self._datetime_being_edited = None
            self._original_offset_on_edit_start = None
            return None # Stay in menu
        elif action == "START_CAPTURE":
            logger.info("Capture action triggered.")
            return "CAPTURE" # Signal to main loop
        elif action == "QUIT":
            # This shouldn't be returned directly by handlers anymore, but handle defensively
            logger.warning("QUIT action returned unexpectedly from input handler.")
            return "QUIT"
        else:
            # No action needed or action handled internally (e.g., field change)
            return None

    def draw(self):
        """Draws the complete menu screen."""
        # Assertions for essential resources
        assert self.font, "Main font not loaded, cannot draw menu items."
        assert self.title_font, "Title font not loaded, cannot draw title."
        assert self.hint_font, "Hint font not loaded, cannot draw hints."
        assert self.screen is not None, "Screen surface not available for drawing."

        try:
            # Clear screen
            self.screen.fill(BLACK)

            # Draw components
            self._draw_title()
            self._draw_menu_items() # Handles alignment and editing highlight
            self._draw_hints()

            # Update the physical display
            # Assertion: Ensure display_hat (if used) is assigned
            # update_hardware_display handles None display_hat object gracefully
            update_hardware_display(self.screen, self.display_hat)

        except pygame.error as e:
             logger.error(f"Pygame error during drawing: {e}", exc_info=True)
             # Attempt to recover or just skip frame? For now, log and continue.
        except Exception as e:
             logger.error(f"Unexpected error during drawing: {e}", exc_info=True)


    def cleanup(self):
        """Performs any cleanup needed by the menu system."""
        # Currently, MenuSystem primarily uses resources managed elsewhere (Pygame fonts, screen)
        # Pygame fonts are managed by pygame.font module, cleanup happens on pygame.quit()
        logger.info("MenuSystem cleanup completed (no specific actions needed).")
        pass

    # --- Private Input Handling Methods ---

    def _handle_navigation_input(self) -> str | None:
        """ Handles UP/DOWN/ENTER/BACK when in navigation mode. """
        # Assertion: Check state
        assert not self._is_editing, "Navigation input called while editing"

        action = None # Default return
        if self.button_handler.check_button(BTN_UP):
            self._navigate_menu(-1)
        elif self.button_handler.check_button(BTN_DOWN):
            self._navigate_menu(1)
        elif self.button_handler.check_button(BTN_ENTER):
            action = self._select_menu_item() # This might start editing or trigger capture
        elif self.button_handler.check_button(BTN_BACK):
            # Optional: Implement Back action in main menu (e.g., go to a parent menu if exists, or quit?)
            logger.info("BACK pressed in main menu (no action defined).")
            # action = "QUIT" # Example: Uncomment to make BACK exit the app from main menu
            pass

        # Assertion: Check action type
        assert isinstance(action, (str, type(None))), "Navigation input returning invalid type"
        return action # No external action required unless selecting item

    def _handle_editing_input(self) -> str | None:
        """ Handles UP/DOWN/ENTER/BACK when editing a value. """
        # Assertion: Check state
        assert self._is_editing, "Editing input called while not editing"

        # Get the edit type of the currently selected item
        # Assertion: Check index bounds
        assert 0 <= self._current_selection_idx < len(self._menu_items), "Invalid menu selection index"
        item_text, edit_type = self._menu_items[self._current_selection_idx]

        action = None # Action to return (e.g., exit states)

        # UP/DOWN adjust the current field's value
        if self.button_handler.check_button(BTN_UP):
            self._handle_edit_adjust(edit_type, 1) # Increment/Increase
        elif self.button_handler.check_button(BTN_DOWN):
             self._handle_edit_adjust(edit_type, -1) # Decrement/Decrease

        # ENTER cycles to the next field or confirms/exits (SAVE)
        elif self.button_handler.check_button(BTN_ENTER):
            action = self._handle_edit_next_field(edit_type) # Returns "EXIT_EDIT_SAVE" on finish

        # BACK exits edit mode WITHOUT saving (DISCARD)
        elif self.button_handler.check_button(BTN_BACK):
            action = "EXIT_EDIT_DISCARD" # Signal discard

        # Assertion: Check action type
        assert isinstance(action, (str, type(None))), "Editing input returning invalid type"
        return action # Returns None if just adjusting/changing field, or exit action


    def _navigate_menu(self, direction: int):
        """Updates the current menu selection index, wrapping around."""
        # Assertion: Check direction validity
        assert direction in [-1, 1], f"Invalid navigation direction: {direction}"
        num_items = len(self._menu_items)
        # Assertion: Ensure num_items is positive before modulo
        assert num_items > 0, "Menu has no items"

        self._current_selection_idx = (self._current_selection_idx + direction) % num_items
        # Assertion: Ensure index remains valid after calculation
        assert 0 <= self._current_selection_idx < num_items, "Menu index out of bounds after navigation"
        logger.debug(f"Menu navigated. New selection index: {self._current_selection_idx}, Item: {self._menu_items[self._current_selection_idx][0]}")

    def _select_menu_item(self) -> str | None:
        """ Handles the ENTER action in navigation mode (Starts editing or action). """
        # Assertion: Check index bounds
        assert 0 <= self._current_selection_idx < len(self._menu_items), "Invalid menu selection index"
        item_text, edit_type = self._menu_items[self._current_selection_idx]
        logger.info(f"Menu item selected: {item_text}")

        action_result = None # Default return

        if item_text == self.MENU_ITEM_CAPTURE:
            if USE_SPECTROMETER:
                logger.info("Triggering spectrometer capture screen.")
                action_result = "START_CAPTURE" # Signal action to main loop
            else:
                logger.warning("Capture Spectra selected, but USE_SPECTROMETER is False.")
                # Optionally show a brief message on screen?
                action_result = None

        # --- Start Editing ---
        elif edit_type in [self.EDIT_TYPE_INTEGRATION, self.EDIT_TYPE_DATE, self.EDIT_TYPE_TIME]:
            logger.info(f"Entering edit mode for: {item_text}")
            self._is_editing = True
            # Store current offset for potential discard/revert
            self._original_offset_on_edit_start = self._time_offset
            # Initialize the temporary absolute datetime object based on current *apparent* app time
            self._datetime_being_edited = self._get_current_app_display_time()
            # Assertion: Ensure datetime object created
            assert self._datetime_being_edited is not None, "Failed to get current app time for editing"

            # Set the initial field to edit
            if edit_type == self.EDIT_TYPE_INTEGRATION:
                self._editing_field = None # Integration time doesn't use fields
                logger.debug(f"Starting edit: Integration Time (Current: {self._integration_time_ms} ms)")
            elif edit_type == self.EDIT_TYPE_DATE:
                self._editing_field = self.FIELD_YEAR # Start with Year
                logger.debug(f"Starting edit: Date (Initial: {self._datetime_being_edited.strftime('%Y-%m-%d')}, Field: Year)")
            elif edit_type == self.EDIT_TYPE_TIME:
                self._editing_field = self.FIELD_HOUR # Start with Hour
                logger.debug(f"Starting edit: Time (Initial: {self._datetime_being_edited.strftime('%H:%M')}, Field: Hour)")
            action_result = None # Stay in menu, now in edit mode

        # --- Read-only items (WIFI, IP) ---
        elif item_text in [self.MENU_ITEM_WIFI, self.MENU_ITEM_IP]:
             logger.info(f"Selected read-only item: {item_text}")
             # Optionally: Could force a refresh of network info here?
             # Or display more details on a sub-screen?
             action_result = None # No action on select for these items

        # --- Fallback ---
        else:
             logger.warning(f"Selected menu item '{item_text}' with unknown type/action: {edit_type}")
             action_result = None

        # Assertion: Check return type
        assert isinstance(action_result, (str, type(None))), "Select menu item returning invalid type"
        return action_result


    def _handle_edit_adjust(self, edit_type: int, delta: int):
        """ Adjusts the value of the currently edited field (Integration, Date, or Time). """
        # Assertions: Check state and parameters
        assert self._is_editing, "Adjust called when not editing"
        assert delta in [-1, 1], f"Invalid adjustment delta: {delta}"
        assert edit_type in [self.EDIT_TYPE_INTEGRATION, self.EDIT_TYPE_DATE, self.EDIT_TYPE_TIME], f"Invalid edit type for adjustment: {edit_type}"

        if edit_type == self.EDIT_TYPE_INTEGRATION:
            # Adjust integration time directly
            current_val = self._integration_time_ms
            step = INTEGRATION_TIME_STEP_MS
            new_val = current_val + delta * step
            # Clamp within defined limits
            clamped_val = max(MIN_INTEGRATION_TIME_MS, min(new_val, MAX_INTEGRATION_TIME_MS))
            # Assertion: Check clamped value is within bounds
            assert MIN_INTEGRATION_TIME_MS <= clamped_val <= MAX_INTEGRATION_TIME_MS, "Integration time out of bounds after clamp"
            self._integration_time_ms = clamped_val
            logger.debug(f"Integration time adjusted to {self._integration_time_ms} ms")

        elif edit_type == self.EDIT_TYPE_DATE:
             # Assertion: Ensure datetime object exists for editing
             assert self._datetime_being_edited is not None, "Cannot adjust Date, _datetime_being_edited is None"
             self._change_date_field(delta) # Delegate to date change helper

        elif edit_type == self.EDIT_TYPE_TIME:
             # Assertion: Ensure datetime object exists for editing
             assert self._datetime_being_edited is not None, "Cannot adjust Time, _datetime_being_edited is None"
             self._change_time_field(delta) # Delegate to time change helper

        # No explicit return value needed, side effect is changing internal state


    def _handle_edit_next_field(self, edit_type: int) -> str | None:
        """ Moves to the next editable field, or returns 'EXIT_EDIT_SAVE' if done. """
        # Assertion: Check state
        assert self._is_editing, "Next field called when not editing"
        # Assertion: Check edit type validity
        assert edit_type in [self.EDIT_TYPE_INTEGRATION, self.EDIT_TYPE_DATE, self.EDIT_TYPE_TIME], f"Invalid edit type for next field: {edit_type}"

        action_result = None # Default return

        if edit_type == self.EDIT_TYPE_INTEGRATION:
            logger.debug("Finished editing Integration Time.")
            action_result = "EXIT_EDIT_SAVE" # No fields, Enter saves/exits

        elif edit_type == self.EDIT_TYPE_DATE:
            # Assertion: Check current field is valid for Date
            assert self._editing_field in [self.FIELD_YEAR, self.FIELD_MONTH, self.FIELD_DAY], f"Invalid date field '{self._editing_field}'"
            if self._editing_field == self.FIELD_YEAR:
                self._editing_field = self.FIELD_MONTH
                logger.debug("Editing next field: Month")
            elif self._editing_field == self.FIELD_MONTH:
                self._editing_field = self.FIELD_DAY
                logger.debug("Editing next field: Day")
            elif self._editing_field == self.FIELD_DAY:
                logger.debug("Finished editing Date fields.")
                action_result = "EXIT_EDIT_SAVE" # Finished all date fields

        elif edit_type == self.EDIT_TYPE_TIME:
            # Assertion: Check current field is valid for Time
            assert self._editing_field in [self.FIELD_HOUR, self.FIELD_MINUTE], f"Invalid time field '{self._editing_field}'"
            if self._editing_field == self.FIELD_HOUR:
                self._editing_field = self.FIELD_MINUTE
                logger.debug("Editing next field: Minute")
            elif self._editing_field == self.FIELD_MINUTE:
                # Removed seconds editing
                # self._editing_field = self.FIELD_SECOND; logger.debug("Editing next field: Second")
                # elif self._editing_field == self.FIELD_SECOND:
                logger.debug("Finished editing Time fields.")
                action_result = "EXIT_EDIT_SAVE" # Finished all time fields

        # Assertion: Check return type
        assert isinstance(action_result, (str, type(None))), "Next field returning invalid type"
        return action_result # Stay in edit mode (return None) or exit (return string)


    # --- Private Date/Time Manipulation ---

    def _change_date_field(self, delta: int):
        """ Increments/decrements the current date field of the temporary _datetime_being_edited. """
        # Assertions: Check state and parameters
        assert self._datetime_being_edited is not None, "Cannot change date field, _datetime_being_edited is None"
        assert self._editing_field in [self.FIELD_YEAR, self.FIELD_MONTH, self.FIELD_DAY], f"Invalid date field '{self._editing_field}' for adjustment"
        assert delta in [-1, 1], f"Invalid delta value: {delta}"

        # Operate on a copy or use components to create a new date safely
        current_dt = self._datetime_being_edited
        year, month, day = current_dt.year, current_dt.month, current_dt.day
        # Preserve time components
        hour, minute, second = current_dt.hour, current_dt.minute, current_dt.second
        # Assertions: Check component types
        assert all(isinstance(v, int) for v in [year, month, day, hour, minute, second]), "Date/time components are not integers"

        logger.debug(f"Attempting to change temporary Date field '{self._editing_field}' by {delta} from {year}-{month:02d}-{day:02d}")

        # Calculate new component values with wrapping/clamping
        if self._editing_field == self.FIELD_YEAR:
            year += delta
            year = max(1970, min(2100, year)) # Clamp year reasonably
        elif self._editing_field == self.FIELD_MONTH:
            month += delta
            # Wrap month
            if month > 12: month = 1
            elif month < 1: month = 12
        elif self._editing_field == self.FIELD_DAY:
            # Calculate max days for the *potentially changed* month and year
            import calendar
            try:
                # Use the potentially updated year/month
                # Assertion: Check year/month validity before monthrange
                assert 1 <= month <= 12, "Invalid month for calendar.monthrange"
                _, max_days = calendar.monthrange(year, month)
                day += delta
                 # Wrap day
                if day > max_days: day = 1
                elif day < 1: day = max_days
            except ValueError:
                # Handle cases like Feb 30th attempt during month change
                logger.warning(f"Invalid intermediate date ({year}-{month}) for day calculation. Clamping day.")
                # Calculate new day without wrapping first for get_safe_datetime
                day += delta
                # Clamp day crudely here just to avoid *huge* numbers?
                day = max(1, min(day, 31))


        # Attempt to create the new temporary datetime using the helper
        # This handles invalid combinations like Feb 30th gracefully (returns None)
        new_datetime = get_safe_datetime(year, month, day, hour, minute, second)

        # Check return value
        if new_datetime:
            # Assertion: Check new datetime is valid object
            assert isinstance(new_datetime, datetime.datetime), "get_safe_datetime returned invalid type"
            self._datetime_being_edited = new_datetime # Update the temporary object
            logger.debug(f"Temporary Date being edited is now: {self._datetime_being_edited.strftime('%Y-%m-%d')}")
        else:
            # This case indicates the adjustment resulted in an invalid date (e.g., Feb 30).
            # The temporary date is *not* updated, effectively ignoring the invalid change.
            logger.warning(f"Date field change resulted in invalid date. Change ignored.")
            # Assertion: Ensure temporary datetime object remains valid
            assert isinstance(self._datetime_being_edited, datetime.datetime), "Temporary datetime became invalid"


    def _change_time_field(self, delta: int):
        """ Increments/decrements the current time field of the temporary _datetime_being_edited. """
         # Assertions: Check state and parameters
        assert self._datetime_being_edited is not None, "Cannot change time field, _datetime_being_edited is None"
        assert self._editing_field in [self.FIELD_HOUR, self.FIELD_MINUTE], f"Invalid time field '{self._editing_field}' for adjustment" # Removed SECOND
        assert delta in [-1, 1], f"Invalid delta value: {delta}"

        # Use timedelta for safer time manipulation, handles wrapping automatically
        time_delta = datetime.timedelta(0) # Initialize
        if self._editing_field == self.FIELD_HOUR:
            time_delta = datetime.timedelta(hours=delta)
        elif self._editing_field == self.FIELD_MINUTE:
            time_delta = datetime.timedelta(minutes=delta)
        # Removed seconds
        # elif self._editing_field == self.FIELD_SECOND:
        #     time_delta = datetime.timedelta(seconds=delta)
        else:
             # This case should not be reachable due to the assertion above
             logger.error(f"Logic error: _change_time_field called with invalid field '{self._editing_field}'")
             return

        # Assertion: Check time_delta is timedelta object
        assert isinstance(time_delta, datetime.timedelta), "Failed to create valid timedelta"

        logger.debug(f"Attempting to change temporary Time field '{self._editing_field}' by {delta} hours/mins")

        try:
            new_datetime = self._datetime_being_edited + time_delta
            # Assertion: Ensure result is datetime
            assert isinstance(new_datetime, datetime.datetime), "Time delta calculation resulted in invalid type"
            self._datetime_being_edited = new_datetime
            logger.debug(f"Temporary Time being edited is now: {self._datetime_being_edited.strftime('%H:%M:%S')}") # Log with seconds for clarity
        except OverflowError:
             logger.warning(f"Time field change resulted in datetime overflow. Change ignored.")
             # Datetime object remains unchanged
             # Assertion: Ensure temporary datetime object remains valid
             assert isinstance(self._datetime_being_edited, datetime.datetime), "Temporary datetime became invalid after overflow attempt"


    def _commit_time_offset_changes(self):
        """ Calculates and stores the new time offset based on the final edited datetime. """
        # Assertion: Check state
        assert self._datetime_being_edited is not None, "Commit called but no datetime was being edited."

        try:
            # Final desired absolute time from the editor
            final_edited_time = self._datetime_being_edited
            # Current system time (at the moment of commit)
            current_system_time = datetime.datetime.now()
            # Assertions: Check types before calculation
            assert isinstance(final_edited_time, datetime.datetime), "Final edited time is invalid type"
            assert isinstance(current_system_time, datetime.datetime), "Current system time is invalid type"


            # Calculate the difference (Offset = TargetTime - SystemTime)
            new_offset = final_edited_time - current_system_time
            # Assertion: Ensure offset is timedelta
            assert isinstance(new_offset, datetime.timedelta), "Offset calculation resulted in invalid type"

            # Store the new offset
            self._time_offset = new_offset

            logger.info(f"Time offset update finalized.")
            logger.info(f"  Final edited time: {final_edited_time.strftime('%Y-%m-%d %H:%M:%S')}")
            logger.info(f"  System time at commit: {current_system_time.strftime('%Y-%m-%d %H:%M:%S')}")
            logger.info(f"  New time offset stored: {self._time_offset}")
            # Log seconds for offset calculation even if not edited/displayed

        except Exception as e:
             logger.error(f"Error calculating or storing time offset: {e}", exc_info=True)
             # Keep the old offset if calculation fails?
             logger.warning("Time offset commit failed. Previous offset retained.")
             # Assertion: Ensure offset remains a valid timedelta
             assert isinstance(self._time_offset, datetime.timedelta), "Time offset became invalid after commit failure"


    # --- Private Drawing Methods ---
    def _draw_title(self):
        """Draws the main title."""
        # Assertion: Should have valid font here (checked in draw)
        assert self.title_font, "Title font not loaded"
        try:
            title_text = self.title_font.render("OPEN SPECTRO MENU", True, YELLOW)
            # Assertion: Ensure render result is a Surface
            assert isinstance(title_text, pygame.Surface), "Title render failed"
            # Center the title horizontally, place it near the top
            title_rect = title_text.get_rect(centerx=SCREEN_WIDTH // 2, top=10)
            self.screen.blit(title_text, title_rect)
        except pygame.error as e:
             logger.error(f"Pygame error rendering title: {e}")
        except Exception as e:
             logger.error(f"Unexpected error rendering title: {e}", exc_info=True)


    def _draw_menu_items(self):
        """ Draws the menu items, aligning values and handling highlight/edit states. """
        # Assertion: Font must be loaded
        assert self.font is not None, "Cannot draw menu items without main font."
        y_position = MENU_MARGIN_TOP
        # Get the base time to display (may be overridden if editing)
        datetime_to_display_default = self._get_current_app_display_time()
        # Assertion: Check default datetime is valid
        assert isinstance(datetime_to_display_default, datetime.datetime), "Default display time is invalid"

        # Loop has fixed upper bound (length of _menu_items tuple)
        for i, (item_text, edit_type) in enumerate(self._menu_items):
            try:
                is_selected = (i == self._current_selection_idx)
                is_being_edited = (is_selected and self._is_editing)
                # Assertions: Check loop variables are valid types
                assert isinstance(item_text, str), f"Menu item text at index {i} is not string"
                assert isinstance(edit_type, int), f"Menu item edit type at index {i} is not int"
                assert isinstance(is_selected, bool), "is_selected flag is not bool"
                assert isinstance(is_being_edited, bool), "is_being_edited flag is not bool"

                # --- Determine which datetime object to use for formatting ---
                datetime_for_formatting = datetime_to_display_default
                if is_being_edited and edit_type in [self.EDIT_TYPE_DATE, self.EDIT_TYPE_TIME]:
                    # Assertion: Must have the temp object if editing Date/Time
                    assert self._datetime_being_edited is not None, "Editing Date/Time but _datetime_being_edited is None"
                    # Assertion: Check temp object type
                    assert isinstance(self._datetime_being_edited, datetime.datetime), "Temporary datetime object is invalid type"
                    datetime_for_formatting = self._datetime_being_edited

                # --- Generate Label and Value Strings ---
                label_text = item_text # Default for items without separate values
                value_text = ""
                prefix = "" # Used for alignment calculation

                # Map items to their labels and how to get their values
                if item_text == self.MENU_ITEM_INTEGRATION:
                    prefix = "INTEGRATION:"
                    label_text = prefix
                    value_text = f"{self._integration_time_ms} ms"
                elif item_text == self.MENU_ITEM_DATE:
                    prefix = "DATE:"
                    label_text = prefix
                    value_text = f"{datetime_for_formatting.strftime('%Y-%m-%d')}"
                elif item_text == self.MENU_ITEM_TIME:
                    prefix = "TIME:"
                    label_text = prefix
                    value_text = f"{datetime_for_formatting.strftime('%H:%M')}" # Display HH:MM only
                elif item_text == self.MENU_ITEM_WIFI:
                    prefix = "WIFI:"
                    label_text = prefix
                    value_text = self.network_info.get_wifi_name() # Fetch current value
                elif item_text == self.MENU_ITEM_IP:
                    prefix = "IP:"
                    label_text = prefix
                    value_text = self.network_info.get_ip_address() # Fetch current value
                # else: label_text = item_text, value_text = "" (e.g., for CAPTURE)

                # Assertions: Check generated text types
                assert isinstance(label_text, str), "Generated label text is not string"
                assert isinstance(value_text, str), "Generated value text is not string"


                # --- Determine Color ---
                color = WHITE
                # Special coloring for network status
                is_network_item = item_text in [self.MENU_ITEM_WIFI, self.MENU_ITEM_IP]
                is_connected = not ("Not Connected" in value_text or "Error" in value_text or "No IP" in value_text)
                # Assertions: Check flag types
                assert isinstance(is_network_item, bool), "is_network_item flag is not bool"
                assert isinstance(is_connected, bool), "is_connected flag is not bool"

                if is_selected:
                    color = YELLOW # Highlight selected item
                elif is_network_item and not is_connected:
                    color = GRAY # Dim disconnected network info
                # Assertion: Check color is tuple
                assert isinstance(color, tuple), "Color is not a tuple"


                # --- Render and Blit Label (Aligned Left) ---
                label_surface = self.font.render(label_text, True, color)
                # Assertion: Check render result
                assert isinstance(label_surface, pygame.Surface), f"Label render failed for '{label_text}'"
                self.screen.blit(label_surface, (MENU_MARGIN_LEFT, y_position))

                # --- Render and Blit Value (Aligned at calculated offset) ---
                if value_text: # Only blit value if it exists
                    value_surface = self.font.render(value_text, True, color)
                    # Assertion: Check render result
                    assert isinstance(value_surface, pygame.Surface), f"Value render failed for '{value_text}'"
                    # Use the calculated offset for the value's starting position
                    value_pos_x = MENU_MARGIN_LEFT + self._value_start_offset_x
                    self.screen.blit(value_surface, (value_pos_x, y_position))

                # --- Draw Editing Highlight ---
                if is_being_edited and edit_type in [self.EDIT_TYPE_INTEGRATION, self.EDIT_TYPE_DATE, self.EDIT_TYPE_TIME]:
                    self._draw_editing_highlight(y_position, edit_type, label_text, value_text)

            except pygame.error as e:
                logger.error(f"Pygame error rendering menu item '{item_text}': {e}")
                # Draw placeholder or skip? Skip for now.
            except Exception as e:
                logger.error(f"Unexpected error rendering menu item '{item_text}': {e}", exc_info=True)

            # Move to next line position
            y_position += MENU_SPACING
            # Assertion: Ensure y_position remains numeric
            assert isinstance(y_position, int), "y_position is not integer"


    def _draw_editing_highlight(self, y_pos: int, edit_type: int, label_str: str, value_str: str):
        """ Draws highlight rectangle around the specific field being edited. """
        # Assertion: Font must be loaded
        assert self.font is not None, "Cannot draw highlight without main font."
        # Assertions: Check input parameters
        assert isinstance(y_pos, int), "y_pos must be integer"
        assert isinstance(edit_type, int), "edit_type must be integer"
        assert isinstance(label_str, str), "label_str must be string"
        assert isinstance(value_str, str), "value_str must be string"


        # Base X position where values start (includes margin + offset)
        value_start_x = MENU_MARGIN_LEFT + self._value_start_offset_x
        # Assertion: Check offset calculation is valid
        assert isinstance(value_start_x, int), "value_start_x calculation failed"

        highlight_rect = None
        try:
            # Determine the text segment corresponding to the specific field being edited
            field_str = ""  # The text part (e.g., "2024", "08", "15", "1000")
            offset_str = "" # The text *before* the field within the value string (e.g., "YYYY-", "YYYY-MM-")

            if edit_type == self.EDIT_TYPE_INTEGRATION:
                 # Value is like "1000 ms". Highlight the number part.
                 field_str = str(self._integration_time_ms) # The number itself
                 offset_str = "" # No prefix within the value part
            elif edit_type == self.EDIT_TYPE_DATE:
                # Assertion: Must have datetime object and field
                assert self._datetime_being_edited is not None and self._editing_field is not None, "Missing state for date highlight"
                # Use the currently edited datetime to format the value string reliably
                formatted_date = self._datetime_being_edited.strftime('%Y-%m-%d')
                if self._editing_field == self.FIELD_YEAR:   field_str, offset_str = formatted_date[0:4], ""
                elif self._editing_field == self.FIELD_MONTH: field_str, offset_str = formatted_date[5:7], formatted_date[0:5] # "YYYY-"
                elif self._editing_field == self.FIELD_DAY:   field_str, offset_str = formatted_date[8:10], formatted_date[0:8] # "YYYY-MM-"
                else:
                     # Should not happen due to assertions elsewhere
                     logger.error(f"Invalid editing field '{self._editing_field}' for date highlight")
                     return
            elif edit_type == self.EDIT_TYPE_TIME:
                # Assertion: Must have datetime object and field
                assert self._datetime_being_edited is not None and self._editing_field is not None, "Missing state for time highlight"
                # Use the currently edited datetime (HH:MM format)
                formatted_time = self._datetime_being_edited.strftime('%H:%M')
                if self._editing_field == self.FIELD_HOUR:   field_str, offset_str = formatted_time[0:2], ""
                elif self._editing_field == self.FIELD_MINUTE: field_str, offset_str = formatted_time[3:5], formatted_time[0:3] # "HH:"
                else:
                    # Should not happen
                     logger.error(f"Invalid editing field '{self._editing_field}' for time highlight")
                     return
            else:
                 logger.warning(f"Highlight requested for non-field edit type {edit_type}")
                 return # Not an editable type with fields

            # Assertions: Check string types after calculation
            assert isinstance(field_str, str), "field_str is not string"
            assert isinstance(offset_str, str), "offset_str is not string"

            # Calculate widths based *only* on the relevant text segments
            field_width = self.font.size(field_str)[0] if field_str else 0
            offset_within_value_width = self.font.size(offset_str)[0] if offset_str else 0
            # Assertions: Check widths are numeric
            assert isinstance(field_width, int), "field_width calculation failed"
            assert isinstance(offset_within_value_width, int), "offset_within_value_width calculation failed"


            # Calculate final X position for the highlight rectangle
            highlight_x = value_start_x + offset_within_value_width

            # Define the rectangle (add small padding)
            padding = 1
            highlight_rect = pygame.Rect(
                highlight_x - padding,
                y_pos - padding,
                field_width + 2 * padding,
                FONT_SIZE + 2 * padding # Use font size for height
            )
            # Assertion: Check rect created
            assert isinstance(highlight_rect, pygame.Rect), "Failed to create highlight Rect"

        except pygame.error as e:
             logger.error(f"Pygame error calculating highlight size: {e}")
             return # Abort drawing highlight if calculation fails
        except Exception as e:
             logger.error(f"Unexpected error calculating highlight: {e}", exc_info=True)
             return

        # Draw the rectangle if successfully calculated
        if highlight_rect:
            # Assertion: Check screen exists
            assert self.screen is not None, "Screen is None, cannot draw highlight"
            pygame.draw.rect(self.screen, BLUE, highlight_rect, 1) # 1px thick border


    def _draw_hints(self):
        """Draws contextual hints at the bottom."""
        # Assertion: Font must be loaded
        assert self.hint_font is not None, "Hint font object is not available"
        hint_text = ""
        if self._is_editing:
            # Hints specific to editing mode
            hint_text = "X/Y: Adjust | A: Next/Save | B: Cancel"
        else:
            # Hints for navigation mode
            hint_text = "X/Y: Navigate | A: Select/Edit | B: Back" # Clarify Back action if any
        # Assertion: Check hint text is string
        assert isinstance(hint_text, str), "Generated hint text is not string"
        try:
            hint_surface = self.hint_font.render(hint_text, True, YELLOW)
            # Assertion: Check render result
            assert isinstance(hint_surface, pygame.Surface), "Hint render failed"
            # Position hints at the bottom-left
            hint_rect = hint_surface.get_rect(left=MENU_MARGIN_LEFT, bottom=SCREEN_HEIGHT - 10)
            # Assertion: Check screen exists
            assert self.screen is not None, "Screen is None, cannot draw hints"
            self.screen.blit(hint_surface, hint_rect)
        except pygame.error as e:
             logger.error(f"Pygame error rendering hints: {e}")
        except Exception as e:
             logger.error(f"Unexpected error rendering hints: {e}", exc_info=True)

class SpectrometerScreen:
    """
    Handles the spectrometer live view, capture, saving, and state management.
    Includes adjustable Y-axis scaling, integration time scaling workaround,
    and a simplified state-based calibration workflow.
    """
    # Internal state flags
    STATE_LIVE_VIEW = "live"           # Normal live view of raw counts
    STATE_FROZEN_VIEW = "frozen"         # Frozen OOI scan view
    STATE_CALIBRATE = "calibrate"       # Mode active, showing live raw, expecting calib type selection
    STATE_WHITE_REF_SETUP = "white_setup" # Live relative view after capturing white ref
    STATE_DARK_CAPTURE = "dark_capture"   # Live raw view for dark capture setup

    def __init__(self, screen: pygame.Surface, button_handler: ButtonHandler, menu_system: MenuSystem, display_hat_obj):
        self.screen = screen
        self.button_handler = button_handler
        self.menu_system = menu_system
        self.display_hat = display_hat_obj

        self.spectrometer: Spectrometer | None = None
        self.wavelengths = None
        self._initialize_spectrometer_device()

        self.plot_fig = None
        self.plot_ax = None
        self.plot_line = None
        self.plot_buffer = None
        self._initialize_plot()

        self.overlay_font = None
        self._load_overlay_font()

        self.is_active = False
        self._current_state = self.STATE_LIVE_VIEW
        self._last_integration_time_ms = 0

        self._frozen_intensities = None # For OOI freeze
        self._frozen_wavelengths = None
        self._frozen_timestamp: datetime.datetime | None = None
        self._frozen_integration_time_ms: int | None = None

        self._current_y_max: float = float(Y_AXIS_DEFAULT_MAX)

        self._stored_white_reference: np.ndarray | None = None # Stores captured white ref
        self._white_ref_capture_timestamp: datetime.datetime | None = None
        self._white_ref_integration_ms: int | None = None

    def _initialize_spectrometer_device(self):
            """Finds the first available spectrometer device and stores the object."""
            logger.info("Looking for spectrometer devices...")
            if not USE_SPECTROMETER or sb is None or Spectrometer is None:
                logger.warning("Spectrometer use disabled or libraries not loaded.")
                return
            try:
                devices = sb.list_devices()
                if not devices: logger.error("No spectrometer devices found."); self.spectrometer = None; return
                self.spectrometer = Spectrometer.from_serial_number(devices[0].serial_number)
                if self.spectrometer is None: logger.error("Failed to create Spectrometer instance."); return
                if not hasattr(self.spectrometer, '_dev'): logger.error("Spectrometer missing '_dev'.")
                self.wavelengths = self.spectrometer.wavelengths()
                if self.wavelengths is None or len(self.wavelengths) == 0: logger.error("Failed to get wavelengths."); self.spectrometer = None; return

                logger.info(f"Spectrometer device object created: {devices[0]}")
                logger.info(f"  Model: {self.spectrometer.model}")
                logger.info(f"  Serial: {self.spectrometer.serial_number}")
                logger.info(f"  Wavelength range: {self.wavelengths[0]:.1f} nm to {self.wavelengths[-1]:.1f} nm ({len(self.wavelengths)} points)")
                try: # Get limits
                    limits_tuple = self.spectrometer.integration_time_micros_limits
                    if isinstance(limits_tuple, tuple) and len(limits_tuple) == 2:
                        min_integ, max_integ = limits_tuple
                        logger.info(f"  Limits (reported): {min_integ / 1000:.1f}ms - {max_integ / 1000:.1f}ms")
                    else: logger.warning(f"  Limits unexpected format: {type(limits_tuple)}")
                except AttributeError: logger.warning("  Limits attribute not found.")
                except Exception as e_int: logger.warning(f"  Could not query limits: {e_int}")
            except Exception as e: logger.error(f"Error initializing device: {e}", exc_info=True); self.spectrometer = None

    def _initialize_plot(self):
            """Initializes the matplotlib figure and axes for plotting with desired styling."""
            if plt is None: logger.error("Matplotlib unavailable."); return
            logger.debug("Initializing plot...")
            try:
                plot_width_px = SCREEN_WIDTH; plot_height_px = SCREEN_HEIGHT - 40; dpi = 96
                figsize_inches = (plot_width_px / dpi, plot_height_px / dpi)
                self.plot_fig, self.plot_ax = plt.subplots(figsize=figsize_inches, dpi=dpi)
                if not self.plot_fig or not self.plot_ax: raise RuntimeError("subplots failed")
                (self.plot_line,) = self.plot_ax.plot([], [], linewidth=1, color='cyan')
                if not self.plot_line: raise RuntimeError("plot failed")
                # Styling
                self.plot_ax.grid(True, linestyle=":", alpha=0.6, color='gray')
                self.plot_ax.tick_params(axis='both', which='major', labelsize=9, colors='white')
                self.plot_ax.set_xlabel("Wavelength (nm)", fontsize=10, color='white')
                self.plot_ax.set_ylabel("Intensity", fontsize=10, color='white') # Initial label
                self.plot_fig.patch.set_facecolor('black'); self.plot_ax.set_facecolor('black')
                self.plot_ax.spines['top'].set_color('gray'); self.plot_ax.spines['bottom'].set_color('gray')
                self.plot_ax.spines['left'].set_color('gray'); self.plot_ax.spines['right'].set_color('gray')
                self.plot_fig.tight_layout(pad=0.5)
                logger.debug("Plot initialized with styling.")
            except Exception as e:
                logger.error(f"Failed plot init: {e}", exc_info=True)
                if self.plot_fig and plt and plt.fignum_exists(self.plot_fig.number): plt.close(self.plot_fig)
                self.plot_fig = self.plot_ax = self.plot_line = None

    def _load_overlay_font(self):
        """Loads the font used for text overlays."""
        if not pygame.font.get_init(): pygame.font.init()
        try:
            script_dir = os.path.dirname(os.path.abspath(__file__))
            assets_dir = os.path.join(script_dir, 'assets')
            font_path = os.path.join(assets_dir, SPECTRO_FONT_FILENAME)
            if not os.path.isfile(font_path):
                logger.warning(f"Overlay font file not found: '{font_path}'. Using fallback.")
                self.overlay_font = pygame.font.SysFont(None, SPECTRO_FONT_SIZE)
            else:
                self.overlay_font = pygame.font.Font(font_path, SPECTRO_FONT_SIZE)
            if self.overlay_font is None: raise RuntimeError("Font loading failed")
            logger.info(f"Loaded overlay font (size {SPECTRO_FONT_SIZE})")
        except Exception as e:
            logger.error(f"Failed loading overlay font: {e}", exc_info=True)
            try: self.overlay_font = pygame.font.SysFont(None, SPECTRO_FONT_SIZE)
            except Exception: logger.critical("CRITICAL: Could not load any overlay font.")

    def activate(self):
        """Called when switching to this screen. Tries to open the device connection."""
        logger.info("Activating Spectrometer Screen.")
        self.is_active = True
        self._current_state = self.STATE_LIVE_VIEW # Start in normal live view
        self._frozen_intensities = None; self._frozen_wavelengths = None
        self._frozen_timestamp = None; self._frozen_integration_time_ms = None
        self._stored_white_reference = None # Clear white ref on activate
        self._white_ref_capture_timestamp = None
        self._white_ref_integration_ms = None
        self._current_y_max = float(Y_AXIS_DEFAULT_MAX) # Reset Y-axis max
        logger.debug(f"Y-axis max reset to default: {self._current_y_max}")

        if not USE_SPECTROMETER: logger.warning("Spectrometer use is disabled."); return
        if self.spectrometer is None or not hasattr(self.spectrometer, '_dev'):
            logger.error("Spectrometer device object invalid. Cannot activate."); return

        try: # Try to open/configure
            if not self.spectrometer._dev.is_open:
                logger.info(f"Opening connection: {self.spectrometer.serial_number}")
                self.spectrometer.open()
                logger.info("Connection opened.")
                try: # Set initial integration time
                    self._last_integration_time_ms = self.menu_system.get_integration_time_ms()
                    integration_micros_scaled = int((self._last_integration_time_ms * 1000) * INTEGRATION_TIME_SCALE_FACTOR)
                    logger.debug(f"ACTIVATE: Sending scaled integration time: {integration_micros_scaled} µs (target {self._last_integration_time_ms} ms)")
                    self.spectrometer.integration_time_micros(integration_micros_scaled)
                    logger.info(f"Initial integration time set (target: {self._last_integration_time_ms} ms)")
                except Exception as e_menu: logger.error(f"Failed init integration: {e_menu}"); self._last_integration_time_ms = DEFAULT_INTEGRATION_TIME_MS
            else: # Already open
                logger.info("Connection already open.")
                try: # Sync integration time
                     current_menu_integ = self.menu_system.get_integration_time_ms()
                     if current_menu_integ != self._last_integration_time_ms:
                          integration_micros_scaled = int((current_menu_integ * 1000) * INTEGRATION_TIME_SCALE_FACTOR)
                          logger.debug(f"ACTIVATE (Sync): Sending scaled integ time: {integration_micros_scaled} µs (target {current_menu_integ} ms)")
                          self.spectrometer.integration_time_micros(integration_micros_scaled)
                          self._last_integration_time_ms = current_menu_integ
                          logger.info(f"Synced integration time (target: {current_menu_integ} ms)")
                except Exception as e_sync: logger.warning(f"Could not sync integ time: {e_sync}")
        except usb.core.USBError as e_usb: logger.error(f"USB Error opening: [{getattr(e_usb, 'errno', 'N/A')}] {e_usb.strerror if hasattr(e_usb, 'strerror') else str(e_usb)}")
        except AttributeError as e_attr: logger.error(f"Attribute error on activate: {e_attr}")
        except Exception as e: logger.error(f"Unexpected error activating: {e}", exc_info=True)

    def deactivate(self):
        """Called when switching away from this screen."""
        logger.info("Deactivating Spectrometer Screen.")
        self.is_active = False
        # Clear all temporary data on deactivate
        self._frozen_intensities = None; self._frozen_wavelengths = None
        self._frozen_timestamp = None; self._frozen_integration_time_ms = None
        self._stored_white_reference = None
        self._white_ref_capture_timestamp = None
        self._white_ref_integration_ms = None
        self._current_state = self.STATE_LIVE_VIEW # Ensure state is reset

    def handle_input(self) -> str | None:
        """Processes button inputs for the spectrometer screen based on state."""
        pygame_event_result = self.button_handler.process_pygame_events()
        if pygame_event_result == "QUIT": return "QUIT"

        action_result = None
        spec_ready = (self.spectrometer and hasattr(self.spectrometer, '_dev') and self.spectrometer._dev.is_open)

        current_state = self._current_state # Local copy for checks

        # --- State: Live View (Normal OOI Mode) ---
        if current_state == self.STATE_LIVE_VIEW:
            if self.button_handler.check_button(BTN_ENTER): # A: Freeze OOI
                if spec_ready: self._handle_freeze_capture()
                else: logger.warning("Freeze ignored: Spectrometer not ready.")
            elif self.button_handler.check_button(BTN_BACK): # B: Back to Main Menu
                action_result = "BACK_TO_MENU"
            elif self.button_handler.check_button(BTN_DOWN): # Y: Rescale Y-Axis (Raw)
                if spec_ready: self._rescale_y_axis(relative=False)
                else: logger.warning("Rescale ignored: Spectrometer not ready.")
            elif self.button_handler.check_button(BTN_UP): # X: Enter Calibrate Mode
                logger.info("Entering Calibrate mode.")
                self._current_state = self.STATE_CALIBRATE
                # Plot continues showing live raw data

        # --- State: Calibrate (Shows Live Raw Plot, overlay indicates options) ---
        elif current_state == self.STATE_CALIBRATE:
            if self.button_handler.check_button(BTN_ENTER): # A: Enter White Ref Setup
                if spec_ready:
                    logger.info("Entering White Reference Setup mode.")
                    success = self._capture_and_store_white_ref() # Capture initial white reference
                    if success:
                        self._current_state = self.STATE_WHITE_REF_SETUP
                        self._current_y_max = 2.0 # Reset Y for relative view
                    else:
                        logger.error("Failed initial white ref capture. Staying in Calibrate mode.")
                        # Optional: Flash an error message?
                else: logger.warning("White Ref setup ignored: Spectrometer not ready.")
            elif self.button_handler.check_button(BTN_UP): # X: Enter Dark Capture Setup
                if spec_ready:
                    logger.info("Entering Dark Capture Setup mode.")
                    self._current_state = self.STATE_DARK_CAPTURE
                    self._current_y_max = float(Y_AXIS_DEFAULT_MAX) # Reset Y axis for dark view
                else: logger.warning("Dark Capture setup ignored: Spectrometer not ready.")
            elif self.button_handler.check_button(BTN_BACK): # B: Back to Live View
                logger.info("Exiting Calibrate mode.")
                self._current_state = self.STATE_LIVE_VIEW
            # Y button (BTN_DOWN) maybe could trigger rescale here too if desired?
            # elif self.button_handler.check_button(BTN_DOWN): # Y: Rescale Y-Axis (Raw)
            #     if spec_ready: self._rescale_y_axis(relative=False)
            #     else: logger.warning("Rescale ignored.")

        # --- State: White Reference Setup (Live Relative View) ---
        elif current_state == self.STATE_WHITE_REF_SETUP:
            if self.button_handler.check_button(BTN_ENTER): # A: Save Stored White Ref
                if self._stored_white_reference is not None:
                     logger.info("Saving stored White Reference...")
                     self._save_calib_data("WHITE", self._stored_white_reference,
                                           self._white_ref_capture_timestamp, self._white_ref_integration_ms)
                     self._stored_white_reference = None
                     self._current_state = self.STATE_LIVE_VIEW # Return to live view
                else: logger.error("Cannot save White Ref: No reference stored.")
            elif self.button_handler.check_button(BTN_BACK): # B: Cancel and Back to Live View
                logger.info("Cancelling White Reference setup.")
                self._stored_white_reference = None
                self._current_state = self.STATE_LIVE_VIEW

        # --- State: Dark Capture Setup (Live Raw View) ---
        elif current_state == self.STATE_DARK_CAPTURE:
            if self.button_handler.check_button(BTN_ENTER): # A: Capture and Save Dark
                if spec_ready:
                     logger.info("Capturing and saving Dark scan...")
                     self._capture_and_save_calib("DARK")
                     self._current_state = self.STATE_LIVE_VIEW # Return to live view after capture
                else: logger.warning("Dark Capture ignored: Spectrometer not ready.")
            elif self.button_handler.check_button(BTN_BACK): # B: Cancel and Back to Live View
                logger.info("Cancelling Dark Capture setup.")
                self._current_state = self.STATE_LIVE_VIEW

        # --- State: Frozen View (OOI) ---
        elif current_state == self.STATE_FROZEN_VIEW:
            if self.button_handler.check_button(BTN_ENTER): # A: Save OOI
                 self._handle_save_ooi()
            elif self.button_handler.check_button(BTN_BACK): # B: Discard OOI
                 self._handle_discard_frozen()

        return action_result

    def _capture_and_store_white_ref(self) -> bool:
        """Captures current spectrum for white ref, stores it internally. Returns success."""
        if not (self.spectrometer and hasattr(self.spectrometer, '_dev') and self.spectrometer._dev.is_open and self.wavelengths is not None):
            logger.error("Cannot capture white ref: Spectrometer not ready."); return False
        if np is None: logger.error("NumPy unavailable."); return False

        logger.info("Capturing White Reference spectrum...")
        try:
            current_integration_time_ms = self.menu_system.get_integration_time_ms()
            integration_micros_scaled = int((current_integration_time_ms * 1000) * INTEGRATION_TIME_SCALE_FACTOR)
            if current_integration_time_ms != self._last_integration_time_ms:
                 logger.debug(f"WHITE_REF_STORE: Sending scaled integ time: {integration_micros_scaled} µs (target {current_integration_time_ms} ms)")
                 self.spectrometer.integration_time_micros(integration_micros_scaled)
                 self._last_integration_time_ms = current_integration_time_ms

            intensities = self.spectrometer.intensities(correct_dark_counts=True, correct_nonlinearity=True)
            timestamp = self.menu_system.get_timestamp_datetime()

            if intensities is not None and len(intensities) == len(self.wavelengths):
                max_val = np.max(intensities)
                spec_max_count = getattr(self.spectrometer, 'spectrum_max_value', 65535)
                if max_val >= spec_max_count * 0.98:
                    logger.error(f"White reference saturated (max={max_val:.0f}). Reduce integration time and try again.")
                    # Consider adding a visual indicator/message here
                    return False # Do not store saturated reference

                self._stored_white_reference = np.array(intensities)
                min_value_for_division = 1e-6 # Avoid division by zero
                self._stored_white_reference[self._stored_white_reference <= min_value_for_division] = min_value_for_division
                self._white_ref_capture_timestamp = timestamp
                self._white_ref_integration_ms = current_integration_time_ms
                logger.info("White Reference stored internally.")
                return True
            else:
                logger.error("Failed to capture valid intensities for white reference.")
                return False
        except Exception as e:
            logger.error(f"Error capturing initial white reference: {e}", exc_info=True)
            self._stored_white_reference = None # Ensure it's None on error
            return False

    def _capture_and_save_calib(self, spectra_type: str):
        """Captures current spectrum and immediately saves it as DARK."""
        if spectra_type != "DARK":
            logger.error(f"Invalid type '{spectra_type}' for immediate capture/save.")
            return
        if not (self.spectrometer and hasattr(self.spectrometer, '_dev') and self.spectrometer._dev.is_open and self.wavelengths is not None):
             logger.error(f"Cannot capture {spectra_type}: Spectrometer not ready.")
             return

        logger.info(f"Capturing {spectra_type} scan...")
        try:
            current_integration_time_ms = self.menu_system.get_integration_time_ms()
            integration_micros_scaled = int((current_integration_time_ms * 1000) * INTEGRATION_TIME_SCALE_FACTOR)
            if current_integration_time_ms != self._last_integration_time_ms:
                 logger.debug(f"CAPTURE_{spectra_type}: Sending scaled integ time: {integration_micros_scaled} µs (target {current_integration_time_ms} ms)")
                 self.spectrometer.integration_time_micros(integration_micros_scaled)
                 self._last_integration_time_ms = current_integration_time_ms

            intensities = self.spectrometer.intensities(correct_dark_counts=True, correct_nonlinearity=True)
            timestamp = self.menu_system.get_timestamp_datetime()

            if intensities is not None and len(intensities) == len(self.wavelengths):
                save_success = self._save_data( # Call the general save function
                    intensities=intensities, wavelengths=self.wavelengths,
                    timestamp=timestamp, integration_ms=current_integration_time_ms, # Use original MS
                    spectra_type=spectra_type, save_plot=False # No plot for calib
                )
                if save_success: logger.info(f"{spectra_type} scan captured and saved.")
                else: logger.error(f"Failed to save {spectra_type} scan.")
            else: logger.error(f"Failed capture for {spectra_type} scan.")
        except Exception as e: logger.error(f"Error capturing/saving {spectra_type}: {e}", exc_info=True)

    def _save_calib_data(self, spectra_type: str, intensities: np.ndarray,
                         timestamp: datetime.datetime, integration_ms: int):
        """Saves calibration data (specifically the stored White Ref)"""
        if spectra_type != "WHITE":
            logger.error(f"Invalid type '{spectra_type}' for saving calib data.")
            return
        if not all([intensities is not None, self.wavelengths is not None,
                    timestamp is not None, integration_ms is not None]):
            logger.error(f"Cannot save {spectra_type}: Missing required data.")
            return

        logger.info(f"Saving {spectra_type} reference...")
        save_success = self._save_data( # Call the general save function
            intensities=intensities, wavelengths=self.wavelengths,
            timestamp=timestamp, integration_ms=integration_ms, # Use original MS
            spectra_type=spectra_type, save_plot=False # No plot for calib
        )
        if save_success: logger.info(f"{spectra_type} reference saved successfully.")
        else: logger.error(f"Failed to save {spectra_type} reference.")

    def _handle_freeze_capture(self):
        """Captures the current OOI spectrum data and freezes the display state."""
        if not (self.spectrometer and hasattr(self.spectrometer, '_dev') and self.spectrometer._dev.is_open and self.wavelengths is not None):
             logger.error("Cannot freeze: Spectrometer not ready."); return
        logger.info("Freezing current OOI spectrum...")
        try:
            current_integration_time_ms = self.menu_system.get_integration_time_ms()
            integration_micros_scaled = int((current_integration_time_ms * 1000) * INTEGRATION_TIME_SCALE_FACTOR)
            if current_integration_time_ms != self._last_integration_time_ms:
                 self.spectrometer.integration_time_micros(integration_micros_scaled)
                 self._last_integration_time_ms = current_integration_time_ms

            intensities = self.spectrometer.intensities(correct_dark_counts=True, correct_nonlinearity=True)
            if intensities is not None and len(intensities) == len(self.wavelengths):
                self._frozen_intensities = intensities
                self._frozen_wavelengths = self.wavelengths
                self._frozen_timestamp = self.menu_system.get_timestamp_datetime()
                self._frozen_integration_time_ms = current_integration_time_ms # Store original MS
                self._current_state = self.STATE_FROZEN_VIEW
                logger.info(f"OOI Spectrum frozen (target integ: {self._frozen_integration_time_ms} ms)")
            else: logger.error("Failed to capture valid OOI intensities.")
        except Exception as e: logger.error(f"Error freezing OOI: {e}", exc_info=True)

    def _handle_save_ooi(self):
        """Saves the currently frozen OOI spectrum data, then returns to live view."""
        if not all([self._frozen_intensities is not None, self._frozen_wavelengths is not None,
                    self._frozen_timestamp is not None, self._frozen_integration_time_ms is not None]):
             logger.error("Cannot save OOI: Missing frozen data components."); self._handle_discard_frozen(); return

        logger.info("Attempting to save frozen OOI spectrum...")
        save_success = self._save_data( # Call general save function
            intensities=self._frozen_intensities, wavelengths=self._frozen_wavelengths,
            timestamp=self._frozen_timestamp, integration_ms=self._frozen_integration_time_ms,
            spectra_type="OOI", save_plot=True # Save plot for OOI
        )
        if save_success: logger.info("Frozen OOI spectrum saved successfully.")
        else: logger.error("Failed to save frozen OOI spectrum.")
        self._handle_discard_frozen() # Clear state and return

    def _handle_discard_frozen(self):
        """Discards the currently frozen spectrum data and returns to live view."""
        logger.info("Discarding frozen spectrum.")
        self._frozen_intensities = None; self._frozen_wavelengths = None
        self._frozen_timestamp = None; self._frozen_integration_time_ms = None
        self._current_state = self.STATE_LIVE_VIEW # Always return to normal live view
        logger.info("Returned to live view (discarded).")

    def _save_data(self, intensities: np.ndarray, wavelengths: np.ndarray,
                   timestamp: datetime.datetime, integration_ms: int,
                   spectra_type: str, save_plot: bool = True) -> bool:
        """Saves spectrum data (OOI, DARK, or WHITE) to CSV. Optionally saves plot."""
        if intensities is None or wavelengths is None or timestamp is None or integration_ms is None or not spectra_type:
            logger.error(f"Cannot save data: Invalid params for type '{spectra_type}'.")
            return False

        timestamp_str = timestamp.strftime("%Y-%m-%d-%H-%M-%S")
        logger.debug(f"Saving data (Type: {spectra_type})...")
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
            with open(CSV_FILENAME, 'a', newline='') as csvfile:
                csvwriter = csv.writer(csvfile)
                file_exists = os.path.isfile(CSV_FILENAME) and os.path.getsize(CSV_FILENAME) > 0
                if not file_exists:
                    header = ["timestamp", "spectra_type", "integration_time_ms", "scans_to_average"] + \
                             [f"{wl:.2f}" for wl in wavelengths]
                    csvwriter.writerow(header)
                data_row = [timestamp_str, spectra_type, integration_ms, 1] + \
                           [f"{inten:.4f}" for inten in intensities]
                csvwriter.writerow(data_row)

            if save_plot: # Only save plot if flag is True
                if plt is None: logger.warning("Plot save skipped: Matplotlib unavailable.")
                else:
                    plot_filename_base = f"spectrum_{spectra_type}_{timestamp_str}"
                    plot_filepath_png = os.path.join(PLOT_SAVE_DIR, f"{plot_filename_base}.png")
                    logger.debug(f"Saving plot: {plot_filepath_png}")
                    save_fig, save_ax = None, None
                    try:
                        save_fig, save_ax = plt.subplots(figsize=(8, 6))
                        if not save_fig or not save_ax: raise RuntimeError("Failed plot creation")
                        save_ax.plot(wavelengths, intensities)
                        save_ax.set_title(f"Spectrum ({spectra_type}) - {timestamp_str}\nIntegration: {integration_ms} ms, Scans: 1", fontsize=10)
                        save_ax.set_xlabel("Wavelength (nm)"); save_ax.set_ylabel("Intensity")
                        save_ax.grid(True, linestyle="--", alpha=0.7); save_fig.tight_layout()
                        save_fig.savefig(plot_filepath_png, dpi=150)
                        logger.debug("Plot image saved.")
                    finally:
                        if save_fig and plt.fignum_exists(save_fig.number): plt.close(save_fig)
            else: logger.debug(f"Plot saving skipped for type: {spectra_type}")
            logger.info(f"Data saved for type: {spectra_type}.")
            return True
        except Exception as e: logger.error(f"Error saving data: {e}", exc_info=True); return False

    def _rescale_y_axis(self, relative: bool = False):
        """Captures spectrum, calculates new Y max, updates state. Handles relative."""
        if np is None: logger.error("NumPy unavailable."); return
        if not (self.spectrometer and hasattr(self.spectrometer, '_dev') and self.spectrometer._dev.is_open):
             logger.warning("Spectrometer not ready."); return
        logger.info(f"Rescaling Y-axis (relative={relative})...")
        try:
            current_integration_time_ms = self.menu_system.get_integration_time_ms()
            integration_micros_scaled = int((current_integration_time_ms * 1000) * INTEGRATION_TIME_SCALE_FACTOR)
            if current_integration_time_ms != self._last_integration_time_ms:
                 logger.debug(f"RESCALE: Sending scaled integ time: {integration_micros_scaled} µs (target {current_integration_time_ms} ms)")
                 self.spectrometer.integration_time_micros(integration_micros_scaled)
                 self._last_integration_time_ms = current_integration_time_ms

            intensities = self.spectrometer.intensities(correct_dark_counts=True, correct_nonlinearity=True)
            if intensities is not None and len(intensities) > 0:
                 data_to_scale = intensities
                 if relative:
                      if self._stored_white_reference is not None and len(intensities) == len(self._stored_white_reference):
                           data_to_scale = intensities / self._stored_white_reference
                      else: logger.warning("Cannot rescale relative: White ref missing/invalid."); return

                 max_val = np.max(data_to_scale)
                 min_ceiling = Y_AXIS_MIN_CEILING_RELATIVE if relative else Y_AXIS_MIN_CEILING
                 new_y_max = max(min_ceiling, max_val * Y_AXIS_RESCALE_FACTOR)
                 self._current_y_max = float(new_y_max)
                 logger.info(f"Y-axis max rescaled to: {self._current_y_max:.2f} (based on max val: {max_val:.2f})")
            else: logger.warning("Failed rescaling: No valid intensities.")
        except usb.core.USBError as e_usb: logger.error(f"USB error during rescale: {e_usb}")
        except AttributeError as e_attr: logger.error(f"Attribute error during rescale: {e_attr}")
        except Exception as e: logger.error(f"Error rescaling Y-axis: {e}", exc_info=True)

    def _capture_and_plot(self) -> pygame.Surface | None:
        """Captures/uses data, applies smoothing, calculates relative if needed, plots."""
        if not (self.plot_fig and self.plot_ax and self.plot_line and Image):
             logger.warning("Plotting components not ready."); return None
        # Check numpy availability, warn if needed but proceed
        if np is None and self._current_state in [self.STATE_LIVE_VIEW, self.STATE_WHITE_REF_SETUP, self.STATE_DARK_CAPTURE]:
             logger.warning("NumPy unavailable for smoothing/relative calc.")

        plot_w = None; plot_i_to_display = None; y_label = "Intensity"

        try:
            # --- Get Live Data if in a live state ---
            current_state = self._current_state # Cache state
            if current_state in [self.STATE_LIVE_VIEW, self.STATE_CALIBRATE, self.STATE_WHITE_REF_SETUP, self.STATE_DARK_CAPTURE]:
                if not (self.spectrometer and hasattr(self.spectrometer, '_dev') and self.spectrometer._dev.is_open and self.wavelengths is not None):
                     return None # Not ready

                current_integration_time_ms = self.menu_system.get_integration_time_ms()
                if current_integration_time_ms != self._last_integration_time_ms:
                     integration_micros_scaled = int((current_integration_time_ms * 1000) * INTEGRATION_TIME_SCALE_FACTOR)
                     # logger.debug(f"PLOT ({self._current_state}): Sending scaled integ time: {integration_micros_scaled} µs (target {current_integration_time_ms} ms)") # Can be noisy
                     self.spectrometer.integration_time_micros(integration_micros_scaled)
                     self._last_integration_time_ms = current_integration_time_ms

                raw_i = self.spectrometer.intensities(correct_dark_counts=True, correct_nonlinearity=True)
                if raw_i is None or len(raw_i) != len(self.wavelengths):
                     logger.warning("Failed live capture for plot."); return None
                plot_w = self.wavelengths

                # --- Process based on state ---
                if self._current_state == self.STATE_WHITE_REF_SETUP:
                     y_label = "Relative Reflectance"
                     if self._stored_white_reference is not None and np is not None and len(raw_i) == len(self._stored_white_reference):
                          plot_i_to_display = raw_i / self._stored_white_reference
                     else:
                          logger.warning("White ref invalid for relative plot, showing raw.")
                          plot_i_to_display = raw_i
                else: # STATE_LIVE_VIEW or STATE_DARK_CAPTURE
                     y_label = "Intensity (Counts)"
                     if np is not None and LIVE_SMOOTHING_WINDOW_SIZE > 1 and isinstance(raw_i, np.ndarray):
                         try: # Apply smoothing
                             window_size = LIVE_SMOOTHING_WINDOW_SIZE
                             weights = np.ones(window_size) / window_size
                             plot_i_to_display = np.convolve(raw_i, weights, mode='same')
                         except Exception as smooth_err:
                             logger.error(f"Smoothing error: {smooth_err}. Using raw."); plot_i_to_display = raw_i
                     else: plot_i_to_display = raw_i 

            elif self._current_state == self.STATE_FROZEN_VIEW: # Use frozen (raw) data
                if not (self._frozen_intensities is not None and self._frozen_wavelengths is not None):
                     logger.error("Frozen data missing for plot."); self._handle_discard_frozen(); return None
                plot_w = self._frozen_wavelengths
                plot_i_to_display = self._frozen_intensities
                y_label = "Intensity (Frozen)"
            else: return None

            # --- Update Plot Data & Axes ---
            if plot_w is None or plot_i_to_display is None: return None
            self.plot_line.set_data(plot_w, plot_i_to_display)
            current_y_limit = self._current_y_max
            if self._current_state == self.STATE_WHITE_REF_SETUP:
                 current_y_limit = max(Y_AXIS_MIN_CEILING_RELATIVE, self._current_y_max)
            self.plot_ax.set_ylabel(y_label, fontsize=10, color='white')
            self.plot_ax.set_ylim(0, current_y_limit)
            self.plot_ax.set_xlim(min(plot_w), max(plot_w))

            # --- Render Plot to Buffer & Convert ---
            try:
                 buf = io.BytesIO()
                 self.plot_fig.savefig(buf, format='png', dpi=self.plot_fig.dpi, bbox_inches='tight', pad_inches=0.05)
                 buf.seek(0)
                 if buf.getbuffer().nbytes == 0: raise RuntimeError("Empty plot buffer")
                 plot_surface = pygame.image.load(buf, "png")
                 buf.close()
                 if plot_surface is None: # Check if load itself failed (unlikely here)
                     raise RuntimeError("pygame.image.load failed")
                 return plot_surface
            except Exception as render_err:
                 logger.error(f"Error rendering plot to surface: {render_err}", exc_info=True)
                 if 'buf' in locals() and hasattr(buf, 'closed') and not buf.closed: buf.close()
                 return None

        # --- Exception Handling ---
        except usb.core.USBError as e_usb: logger.error(f"USB error in plot: {e_usb}"); return None
        except AttributeError as e_attr: logger.error(f"Attribute error in plot: {e_attr}"); return None
        except Exception as e: logger.error(f"General Plot error: {e}", exc_info=True); return None
    
    
    def _draw_overlays(self):
        """Draws status text overlays on the screen."""
        if not self.overlay_font: return
        display_integration_time_ms = DEFAULT_INTEGRATION_TIME_MS
        try:
             current_state = self._current_state
             if current_state == self.STATE_FROZEN_VIEW and self._frozen_integration_time_ms is not None:
                  display_integration_time_ms = self._frozen_integration_time_ms
             elif current_state != self.STATE_FROZEN_VIEW:
                  display_integration_time_ms = self.menu_system.get_integration_time_ms()
        except Exception as e: logger.warning(f"Could not get integration time for overlay: {e}")

        try:
            # Integ Time
            integ_text = f"Integ: {display_integration_time_ms} ms"; integ_surf = self.overlay_font.render(integ_text, True, YELLOW)
            self.screen.blit(integ_surf, (5, 5))
            # Mode
            state_text = f"Mode: {self._current_state.upper()}"; state_color = YELLOW
            if self._current_state == self.STATE_FROZEN_VIEW: state_color = BLUE
            elif self._current_state == self.STATE_CALIBRATE: state_color = GREEN
            elif self._current_state == self.STATE_WHITE_REF_SETUP: state_color = CYAN
            elif self._current_state == self.STATE_DARK_CAPTURE: state_color = RED
            state_surf = self.overlay_font.render(state_text, True, state_color)
            state_rect = state_surf.get_rect(right=SCREEN_WIDTH - 5, top=5)
            self.screen.blit(state_surf, state_rect)

            # Hints
            hint_text = "";
            if self._current_state == self.STATE_LIVE_VIEW: hint_text = "A:Freeze OOI | X:Calib | Y:Rescale | B:Menu"
            elif self._current_state == self.STATE_FROZEN_VIEW: hint_text = "A: Save OOI | B: Discard"
            elif self._current_state == self.STATE_CALIBRATE: hint_text = "A: White Setup | X: Dark Setup | B: Back Live"
            elif self._current_state == self.STATE_WHITE_REF_SETUP: hint_text = "Aim@White -> A: Save Ref | B: Cancel Live"
            elif self._current_state == self.STATE_DARK_CAPTURE: hint_text = "Cap On -> A: Save Dark | B: Cancel Live"

            if hint_text:
                hint_surf = self.overlay_font.render(hint_text, True, YELLOW)
                hint_rect = hint_surf.get_rect(centerx=SCREEN_WIDTH // 2, bottom=SCREEN_HEIGHT - 5)
                self.screen.blit(hint_surf, hint_rect) 
        except Exception as e: logger.error(f"Error rendering overlays: {e}", exc_info=True)

    # <<< Fixed draw method >>>
    def draw(self):
        """Draws the spectrometer screen based on the current state."""
        if self.screen is None: return
        spectrometer_ready = (USE_SPECTROMETER and self.spectrometer and hasattr(self.spectrometer, '_dev') and self.spectrometer._dev.is_open)
        self.screen.fill(BLACK)

        # --- Draw Plot or Error Message ---
        if not spectrometer_ready:
             if self.overlay_font: # Show error message
                 err_text = "Spectrometer Not Ready";
                 if not USE_SPECTROMETER: err_text = "Spectrometer Disabled"
                 elif self.spectrometer is None: err_text = "Not Found"
                 elif not hasattr(self.spectrometer, '_dev'): err_text = "Backend Err"
                 elif not self.spectrometer._dev.is_open: err_text = "Connect Err"
                 err_surf = self.overlay_font.render(err_text, True, RED)
                 err_rect = err_surf.get_rect(center=self.screen.get_rect().center); self.screen.blit(err_surf, err_rect)
        else: # Spectrometer Ready: Draw plot for relevant states
            plot_surface = self._capture_and_plot() # Handles drawing based on state
            if plot_surface:
                 plot_rect = plot_surface.get_rect(centerx=SCREEN_WIDTH // 2, top=20); plot_rect.clamp_ip(self.screen.get_rect()); self.screen.blit(plot_surface, plot_rect)
            else: # Draw placeholder if plot failed
                 if self.overlay_font: status_text = "Capturing..." if self._current_state != self.STATE_FROZEN_VIEW else "Plot Error"; status_surf = self.overlay_font.render(status_text, True, GRAY); status_rect = status_surf.get_rect(center=self.screen.get_rect().center); self.screen.blit(status_surf, status_rect)

        # --- Draw Overlays (Hints, Status) for all states ---
        self._draw_overlays() 
        update_hardware_display(self.screen, self.display_hat) # Update physical screen


    def run_loop(self) -> str:
        """Runs the main loop for the Spectrometer screen."""
        logger.info(f"Starting Spectrometer screen loop (State: {self._current_state}).")
        while self.is_active and not g_shutdown_flag.is_set():
            action = self.handle_input() # Handle input based on state
            if action == "QUIT": self.deactivate(); return "QUIT"
            if action == "BACK_TO_MENU": self.deactivate(); return "BACK"
            self.draw() # Draw the current state
            wait_time_ms = int(SPECTRO_LOOP_DELAY_S * 1000)
            try: # Adjust wait based on integration time only in live states
                current_integ_ms = 0
                if self._current_state in [self.STATE_LIVE_VIEW, self.STATE_WHITE_REF_SETUP, self.STATE_DARK_CAPTURE]: # Use self.
                     current_integ_ms = self.menu_system.get_integration_time_ms()
                if current_integ_ms > 0:
                    integration_seconds = current_integ_ms / 1000.0
                    target_wait_s = integration_seconds + SPECTRO_REFRESH_OVERHEAD_S # Use constant
                    wait_time_ms = int(max(SPECTRO_LOOP_DELAY_S, target_wait_s) * 1000)
            except Exception: pass
            pygame.time.wait(wait_time_ms)
        return "QUIT" if g_shutdown_flag.is_set() else "BACK"

    def cleanup(self):
        """Cleans up spectrometer connection and plotting resources."""
        logger.info("Cleaning up SpectrometerScreen resources...")
        if self.spectrometer:
            try:
                if hasattr(self.spectrometer, '_dev') and self.spectrometer._dev.is_open:
                     self.spectrometer.close(); logger.info("Spectrometer closed.")
                else: logger.debug("Spectrometer already closed/invalid.")
            except Exception as e: logger.error(f"Error closing spectrometer: {e}", exc_info=True)
        self.spectrometer = None
        if self.plot_fig and plt and plt.fignum_exists(self.plot_fig.number):
            try: plt.close(self.plot_fig); logger.info("Plot figure closed.")
            except Exception as e: logger.error(f"Error closing plot figure: {e}", exc_info=True)
        self.plot_fig = self.plot_ax = self.plot_line = None     

# --- Splash Screen Function ---
def show_splash_screen(screen: pygame.Surface, display_hat_obj, duration_s: float):
    """
    Displays the splash screen image for a specified duration.
    Args:
        screen: The Pygame Surface to draw on.
        display_hat_obj: The initialized DisplayHATMini object, or None.
        duration_s: How long to display the splash screen in seconds.
    """
    # Assertions for parameters
    assert screen is not None, "Screen surface required for splash screen"
    assert isinstance(duration_s, (int, float)) and duration_s >= 0, "Splash duration must be a non-negative number"

    logger.info(f"Displaying splash screen for {duration_s:.1f} seconds...")
    splash_image_final = None # Renamed variable for the surface to blit
    try:
        # Construct path to image robustly
        script_dir = os.path.dirname(os.path.abspath(__file__))
        assets_dir = os.path.join(script_dir, 'assets')
        image_path = os.path.join(assets_dir, 'pysb-app.png') # Consider making filename a constant
        # Assertion: Check path is string
        assert isinstance(image_path, str), "Splash image path is not string"

        # Check file existence before loading
        if not os.path.isfile(image_path):
             logger.error(f"Splash screen image not found at: {image_path}")
             time.sleep(min(duration_s, 2.0)) # Wait a short time even if image missing
             return # Skip rest of splash if image missing

        # Load the image
        splash_image_raw = pygame.image.load(image_path)
        # Assertion: Check loaded image type
        assert isinstance(splash_image_raw, pygame.Surface), "Splash image load failed"
        logger.info(f"Loaded splash screen image: {image_path}")

        # Only convert if NOT using the dummy driver (i.e., if a real video mode is set)
        is_dummy_driver = os.environ.get('SDL_VIDEODRIVER') == 'dummy'
        # Assertion: Check driver type is string or None
        assert isinstance(is_dummy_driver, bool), "is_dummy_driver check failed"

        if not is_dummy_driver and pygame.display.get_init() and pygame.display.get_surface():
            # We have a real display mode, attempt conversion for performance
            try:
                logger.debug("Attempting splash image conversion for standard display.")
                splash_image_final = splash_image_raw.convert()
                # Assertion: Check conversion result
                assert isinstance(splash_image_final, pygame.Surface), "Splash image convert failed"
                # If using alpha transparency: splash_image_final = splash_image_raw.convert_alpha()
            except pygame.error as convert_error:
                logger.warning(f"pygame.Surface.convert() failed even for standard display: {convert_error}. Using raw surface.")
                splash_image_final = splash_image_raw # Use raw as fallback
        else:
            # Using dummy driver OR no display mode set, use the raw loaded surface
            logger.debug("Skipping splash image conversion (using dummy driver or no video mode).")
            splash_image_final = splash_image_raw # Use the raw loaded image directly
        # --- END CONDITIONAL CONVERT ---

        # Assertion: Check final image surface exists
        assert splash_image_final is not None, "Final splash image surface is None"


    except pygame.error as e:
        logger.error(f"Pygame error loading splash screen image: {e}", exc_info=True)
        time.sleep(min(duration_s, 2.0))
        return # Skip splash on load error
    except FileNotFoundError: # Should be caught by isfile check, but belt-and-suspenders
        logger.error(f"Splash screen image file not found (exception): {image_path}")
        time.sleep(min(duration_s, 2.0))
        return
    except Exception as e:
        logger.error(f"An unexpected error occurred loading splash screen: {e}", exc_info=True)
        time.sleep(min(duration_s, 2.0))
        return # Skip splash on error

    # --- Proceed only if splash_image_final was successfully assigned ---
    if splash_image_final:
        try:
            # Clear screen (optional, depends on desired effect)
            screen.fill(BLACK)

            # Get image dimensions and screen dimensions
            splash_rect = splash_image_final.get_rect()
            screen_rect = screen.get_rect()
            # Assertions: Check rect types
            assert isinstance(splash_rect, pygame.Rect), "Splash rect calculation failed"
            assert isinstance(screen_rect, pygame.Rect), "Screen rect calculation failed"

            # Center the splash image on the screen
            splash_rect.center = screen_rect.center

            # Draw the image
            screen.blit(splash_image_final, splash_rect) # Blit the final surface

            # Update the physical display using the helper
            update_hardware_display(screen, display_hat_obj)

            # Wait for the specified duration (respecting shutdown flag)
            wait_interval = 0.1 # Check flag every 100ms
            # Assertion: Check interval is numeric
            assert isinstance(wait_interval, float), "Wait interval is not float"
            num_intervals = int(duration_s / wait_interval)
            # Assertion: Check loop bound is int
            assert isinstance(num_intervals, int), "Splash loop interval calculation failed"
            # Loop is bounded by num_intervals
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
    hint_font: pygame.font.Font
    ):
    """
    Displays a disclaimer message and waits for user acknowledgement using ButtonHandler.
    Args:
        screen: The Pygame Surface to draw on.
        display_hat_obj: The initialized DisplayHATMini object, or None.
        button_handler: The initialized ButtonHandler object.
        # disclaimer_font: Pygame Font object for the main text. # <<< Removed docstring
        hint_font: Pygame Font object for the hint text.       # <<< Kept docstring
    """
    # Assertions for parameters
    assert screen is not None, "Screen surface required for disclaimer"
    assert button_handler is not None, "ButtonHandler required for disclaimer acknowledgement"
    assert hint_font is not None, "Hint font object is required for disclaimer"
    assert isinstance(hint_font, pygame.font.Font), "Hint font is not a valid Font object"

    logger.info("Displaying disclaimer screen...")

    # --- Load Disclaimer Font Locally ---
    disclaimer_font = None
    try:
        if not pygame.font.get_init(): pygame.font.init() # Ensure font module is ready
        # Assertion: Font module must be ready
        assert pygame.font.get_init(), "Pygame font module not ready for disclaimer font load"

        script_dir = os.path.dirname(os.path.abspath(__file__))
        assets_dir = os.path.join(script_dir, 'assets')
        # Use the same main font file, but with the specific disclaimer size
        font_path = os.path.join(assets_dir, MAIN_FONT_FILENAME)
        # Assertion: Check path is string
        assert isinstance(font_path, str), "Disclaimer font path is not string"

        if not os.path.isfile(font_path):
            logger.error(f"Disclaimer font file not found: {font_path}. Using fallback.")
            disclaimer_font = pygame.font.SysFont(None, DISCLAIMER_FONT_SIZE)
        else:
            try:
                disclaimer_font = pygame.font.Font(font_path, DISCLAIMER_FONT_SIZE)
                logger.info(f"Loaded disclaimer font: {font_path} (Size: {DISCLAIMER_FONT_SIZE})")
            except pygame.error as e:
                logger.error(f"Failed to load disclaimer font '{font_path}' size {DISCLAIMER_FONT_SIZE}: {e}. Using fallback.")
                disclaimer_font = pygame.font.SysFont(None, DISCLAIMER_FONT_SIZE)
            except Exception as e:
                 logger.error(f"Unexpected error loading disclaimer font '{font_path}': {e}. Using fallback.", exc_info=True)
                 disclaimer_font = pygame.font.SysFont(None, DISCLAIMER_FONT_SIZE)

        # Assertion: Check font object created (or SysFont fallback)
        assert disclaimer_font is not None, "Disclaimer font failed to load even with fallback"

    except Exception as e:
        logger.error(f"Error during disclaimer font loading setup: {e}", exc_info=True)
        # Attempt SysFont as last resort if setup failed
        try:
            disclaimer_font = pygame.font.SysFont(None, DISCLAIMER_FONT_SIZE)
            assert disclaimer_font is not None, "SysFont fallback failed for disclaimer"
        except Exception as e_sys:
             logger.critical(f"FATAL: Could not load any font for disclaimer: {e_sys}")
             return # Cannot proceed without any font

    # Check if font loading succeeded (either TTF or SysFont fallback)
    if not disclaimer_font:
        logger.error("Failed to load any font for disclaimer text. Cannot display.")
        return
    # --- End Font Loading Section ---

    # --- Prepare Text ---
    try:
        lines = DISCLAIMER_TEXT.splitlines()
        rendered_lines = []
        max_width = 0
        total_height = 0
        line_spacing = 4 # Vertical space between lines
        # Assertion: Check lines is list
        assert isinstance(lines, list), "Disclaimer text did not split into lines correctly"
        # Assertion: Ensure line spacing is int
        assert isinstance(line_spacing, int), "Line spacing must be integer"

        # Loop bounded by number of lines in disclaimer text (fixed)
        for line in lines:
            # Assertion: Check line is string
            assert isinstance(line, str), "Line in disclaimer is not string"
            if line.strip():
                # Use the *locally loaded* disclaimer_font
                line_surface = disclaimer_font.render(line, True, WHITE) # <<< Use local font
                # Assertion: Check render result
                assert isinstance(line_surface, pygame.Surface), f"Disclaimer line render failed for '{line}'"
                rendered_lines.append(line_surface)
                max_width = max(max_width, line_surface.get_width())
                total_height += line_surface.get_height() + line_spacing
            else:
                rendered_lines.append(None)
                # Use local font height for spacing calculation
                total_height += (disclaimer_font.get_height() // 2) + line_spacing # <<< Use local font
            # Assertion: Check total height calculation
            assert isinstance(total_height, int), "Disclaimer total height calculation failed"

        if total_height > 0: total_height -= line_spacing

        # Prepare hint text
        hint_text = "Press A or B to continue..."
        # Use the *passed-in* hint_font
        hint_surface = hint_font.render(hint_text, True, YELLOW) # <<< Use passed hint_font
        # Assertion: Check hint render result
        assert isinstance(hint_surface, pygame.Surface), "Disclaimer hint render failed"
        hint_height = hint_surface.get_height()
        total_height += hint_height + 10 # Add space for hint + padding
        # Assertion: Check total height again
        assert isinstance(total_height, int), "Disclaimer total height calculation failed after hint"

        # Calculate starting Y position
        start_y = max(10, (screen.get_height() - total_height) // 2)
        # Assertion: Check start_y is int
        assert isinstance(start_y, int), "Disclaimer start_y calculation failed"


        # --- Draw Static Content ---
        screen.fill(BLACK)
        current_y = start_y
        # Loop bounded by number of rendered lines (fixed)
        for surface in rendered_lines:
            if surface: # Rendered line
                # Assertion: Check surface is valid
                assert isinstance(surface, pygame.Surface), "Invalid surface in rendered_lines"
                line_rect = surface.get_rect(centerx=screen.get_width() // 2, top=current_y)
                screen.blit(surface, line_rect)
                current_y += surface.get_height() + line_spacing
            else: # Blank line spacing
                 # Use local font height
                current_y += (disclaimer_font.get_height() // 2) + line_spacing # <<< Use local font
            # Assertion: Check current_y update
            assert isinstance(current_y, int), "Disclaimer current_y update failed"

        # Draw hint at the bottom
        hint_rect = hint_surface.get_rect(centerx=screen.get_width() // 2, top=current_y + 10)
        screen.blit(hint_surface, hint_rect)

        # --- Update Display Once ---
        update_hardware_display(screen, display_hat_obj)

    except pygame.error as e:
         logger.error(f"Pygame error preparing or drawing disclaimer: {e}", exc_info=True)
         return
    except Exception as e:
         logger.error(f"Unexpected error preparing or drawing disclaimer: {e}", exc_info=True)
         return

    # --- Wait for Acknowledgement Loop ---
    logger.info("Waiting for user acknowledgement on disclaimer screen...")
    acknowledged = False
    # Loop bound by external flag g_shutdown_flag or user action
    while not acknowledged and not g_shutdown_flag.is_set():
        # Assertion: Loop condition variables must be bool
        assert isinstance(acknowledged, bool), "acknowledged flag is not bool"
        assert isinstance(g_shutdown_flag.is_set(), bool), "g_shutdown_flag state is not bool"

        quit_signal = button_handler.process_pygame_events()
        if quit_signal == "QUIT":
             # Assertion: Check signal value
             assert quit_signal == "QUIT", "Invalid quit signal received"
             logger.warning("QUIT signal received during disclaimer.")
             g_shutdown_flag.set()
             continue
        if button_handler.check_button(BTN_ENTER) or button_handler.check_button(BTN_BACK):
            acknowledged = True
            logger.info("Disclaimer acknowledged by user via button.")
        pygame.time.wait(50) # Small delay to prevent busy-waiting

    if not acknowledged:
         logger.warning("Exited disclaimer wait due to shutdown signal.")
    else:
         logger.info("Disclaimer screen finished.")

# --- Signal Handling ---
def setup_signal_handlers(button_handler: ButtonHandler, network_info: NetworkInfo):
    """Sets up signal handlers for graceful shutdown."""
    # Assertion: Ensure handlers are provided
    assert button_handler is not None, "Button handler required for signal handler setup"
    assert network_info is not None, "Network info required for signal handler setup"

    def signal_handler(sig, frame):
        # Keep handler very simple: set flag and log.
        # Avoid complex cleanup logic here; do it in `finally`.
        if not g_shutdown_flag.is_set(): # Prevent multiple logs if signal received twice
            logger.warning(f"Received signal {sig}. Initiating graceful shutdown...")
            g_shutdown_flag.set() # Signal threads and loops to stop
        else:
             logger.debug(f"Signal {sig} received again, shutdown already in progress.")


    # Attempt to register handlers
    try:
        signal.signal(signal.SIGINT, signal_handler)  # Ctrl+C
        signal.signal(signal.SIGTERM, signal_handler) # kill command
        logger.info("Signal handlers set up for SIGINT and SIGTERM.")
    except ValueError as e:
         # This can happen if not run in the main thread
         logger.error(f"Failed to set signal handlers: {e}. Shutdown via Ctrl+C might not be clean.")
    except Exception as e:
         logger.error(f"Unexpected error setting signal handlers: {e}", exc_info=True)

# --- Helper Functions ---
def get_safe_datetime(year, month, day, hour=0, minute=0, second=0):
    """
    Attempts to create a datetime object, handling potential ValueErrors.
    Returns the new datetime object or None if invalid.
    """
    # Assertions added for parameter types
    assert isinstance(year, int), "Year must be an integer"
    assert isinstance(month, int), "Month must be an integer"
    assert isinstance(day, int), "Day must be an integer"
    assert isinstance(hour, int), "Hour must be an integer"
    assert isinstance(minute, int), "Minute must be an integer"
    assert isinstance(second, int), "Second must be an integer"

    try:
        # Clamp month first to avoid some direct errors
        month = max(1, min(12, month))
        # Day clamping requires month/year context, datetime constructor handles it well
        # Year clamping can also be done if desired: year = max(1970, min(2100, year))
        new_dt = datetime.datetime(year, month, day, hour, minute, second)
        return new_dt
    except ValueError as e:
        logger.warning(f"Invalid date/time combination attempted: {year}-{month}-{day} {hour}:{minute}:{second}. Error: {e}")
        return None # Return value checked by caller


def update_hardware_display(screen: pygame.Surface, display_hat_obj):
    """
    Updates the physical display (Pimoroni or standard Pygame window).
    Args:
        screen: The Pygame Surface to display.
        display_hat_obj: The initialized DisplayHATMini object, or None.
    """
    # Assertions for required parameters
    assert screen is not None, "Screen surface cannot be None for display update"
    # display_hat_obj can be None, checked below

    if USE_DISPLAY_HAT and display_hat_obj: # Check flag AND object validity
        try:
            # Ensure display_hat_obj is the correct type or has the required method
            assert hasattr(display_hat_obj, 'st7789'), "Display HAT object missing st7789 interface"
            assert hasattr(display_hat_obj.st7789, 'set_window'), "Display HAT st7789 missing set_window method"
            assert hasattr(display_hat_obj.st7789, 'data'), "Display HAT st7789 missing data method"

            # Rotation and byte swapping for ST7789
            rotated_surface = pygame.transform.rotate(screen, 180)
            pixelbytes = rotated_surface.convert(16, 0).get_buffer()
            pixelbytes_swapped = bytearray(pixelbytes)
            pixelbytes_swapped[0::2], pixelbytes_swapped[1::2] = pixelbytes_swapped[1::2], pixelbytes_swapped[0::2]

            display_hat_obj.st7789.set_window()
            chunk_size = 4096 # Send data in chunks
            # Ensure loop range is valid even for empty byte array
            for i in range(0, len(pixelbytes_swapped), chunk_size):
                # Assertion: Check loop bounds implicitly via range
                display_hat_obj.st7789.data(pixelbytes_swapped[i:i + chunk_size])
        except AttributeError as ae:
             logger.error(f"Display HAT object missing expected attribute/method: {ae}", exc_info=False)
        except Exception as e:
            # Log specific error but don't crash the whole app if display fails
            logger.error(f"Error updating Display HAT Mini: {e}", exc_info=False)
            # Potentially disable further HAT updates?
    else:
        # Standard Pygame window update
        try:
             # Check if pygame display is initialized and a surface exists
             if pygame.display.get_init() and pygame.display.get_surface():
                  pygame.display.flip()
             # else: pass # No standard display window to flip
        except pygame.error as e:
             logger.error(f"Error updating Pygame display: {e}", exc_info=True)
        except Exception as e: # Catch broader errors just in case
             logger.error(f"Unexpected error updating Pygame display: {e}", exc_info=True)

# --- Main Application ---
def main():
    """Main application entry point."""
    logger.info("=" * 44)
    logger.info("   Underwater Spectrometer Controller Start ")
    logger.info("=" * 44)
    logger.info(f"Configuration: DisplayHAT={USE_DISPLAY_HAT}, GPIO={USE_GPIO_BUTTONS}, HallSensors={USE_HALL_EFFECT_BUTTONS}, LeakSensor={USE_LEAK_SENSOR}, Spectrometer={USE_SPECTROMETER}")

    display_hat_active = False # Track if HAT *object* was successfully created
    # Note: ButtonHandler manages its internal GPIO/HAT status

    # --- Initialize variables to None ---
    display_hat = None
    screen = None
    button_handler = None
    network_info = None
    menu_system = None
    spectrometer_screen = None # <<< NEW: Add variable for the new screen
    main_clock = None # Clock can still be useful for main menu loop timing

    try:
        # --- Initialize Pygame and Display FIRST ---
        logger.info("Initializing Pygame and display...")
        try:
             pygame.init()
             # Assertion: Check Pygame initialized
             assert pygame.get_init(), "Pygame initialization failed"
             # Initialize clock here too
             main_clock = pygame.time.Clock()
             # Assertion: Check clock created
             assert main_clock is not None, "Pygame clock initialization failed"
        except pygame.error as e:
             logger.critical(f"FATAL: Pygame initialization failed: {e}", exc_info=True)
             raise RuntimeError("Pygame init failed") from e

        if USE_DISPLAY_HAT and DisplayHATMini_lib:
            try:
                # Setup dummy video driver for HAT mode BEFORE display init/surface creation
                os.environ['SDL_VIDEODRIVER'] = 'dummy'
                pygame.display.init() # Need display module init even for dummy driver
                # Assertion: Check display module initialized
                assert pygame.display.get_init(), "Pygame display module failed to initialize (dummy)"
                screen = pygame.Surface((SCREEN_WIDTH, SCREEN_HEIGHT)) # Create buffer surface
                # Assertion: Check screen created
                assert screen is not None, "Failed to create screen buffer for HAT"
                # Attempt to create DisplayHATMini instance
                display_hat = DisplayHATMini_lib(screen) # Pass buffer if needed by constructor, else None
                # Assertion: Check HAT object created
                assert display_hat is not None, "DisplayHATMini object creation failed"
                display_hat_active = True # Mark as active
                logger.info("DisplayHATMini initialized successfully with dummy driver.")
            except Exception as e:
                logger.error(f"Failed to initialize DisplayHATMini: {e}", exc_info=True)
                logger.warning("Falling back to standard Pygame window (if possible).")
                display_hat_active = False
                display_hat = None # Ensure display_hat is None if init failed
                # Clean up dummy driver env var if fallback needed
                os.environ.pop('SDL_VIDEODRIVER', None)
                # Re-init display for standard window
                if pygame.display.get_init(): pygame.display.quit()
                pygame.display.init()
                # Assertion: Check display module re-initialized
                assert pygame.display.get_init(), "Pygame display module failed to re-initialize (fallback)"
                # Screen creation attempt moved below
        else:
            # Not using HAT or library failed to import, setup standard window
            logger.info("Configured for standard Pygame window (Display HAT disabled or unavailable).")
            # No dummy driver needed
            if not pygame.display.get_init(): pygame.display.init() # Ensure display is initialized
            # Assertion: Check display module initialized
            assert pygame.display.get_init(), "Pygame display module failed to initialize (standard)"

        # --- Create Screen Surface (Standard Window or Fallback) ---
        if screen is None: # If not created by HAT init
            try:
                screen = pygame.display.set_mode((SCREEN_WIDTH, SCREEN_HEIGHT))
                pygame.display.set_caption("Spectrometer Menu")
                logger.info("Initialized standard Pygame display window.")
            except pygame.error as e:
                logger.critical(f"FATAL: Failed to create Pygame screen surface: {e}", exc_info=True)
                raise RuntimeError("Display surface creation failed") from e

        # Assertion: Screen surface must exist now
        assert screen is not None, "Failed to create Pygame screen surface"

        # --- Initialize Core Components SECOND (Before Startup Screens) ---
        logger.info("Initializing core components...")
        network_info = NetworkInfo()
        # Pass the initialized display_hat object only if it's active
        button_handler = ButtonHandler(display_hat if display_hat_active else None)
        menu_system = MenuSystem(screen, button_handler, network_info)

        # <<< NEW: Initialize SpectrometerScreen >>>
        if USE_SPECTROMETER:
             # Pass dependencies needed by SpectrometerScreen
             spectrometer_screen = SpectrometerScreen(
                 screen,
                 button_handler,
                 menu_system, # Pass menu system to get integration time etc.
                 display_hat if display_hat_active else None
             )
             # Assertion: Check if screen initialization failed internally (e.g., no device)
             # SpectrometerScreen constructor handles logging if device fails.
             # We can proceed even if spectrometer init failed, it will show error on screen.
             assert spectrometer_screen is not None, "SpectrometerScreen object creation failed unexpectedly"

        # Assign display_hat to menu_system *after* menu_system is created if active
        if display_hat_active:
            # Assertion: Check menu_system exists before assignment
            assert menu_system is not None, "MenuSystem not created before display_hat assignment"
            menu_system.display_hat = display_hat

        # Assertion: Ensure essential components initialized
        assert network_info is not None, "NetworkInfo failed to initialize"
        assert button_handler is not None, "ButtonHandler failed to initialize"
        assert menu_system is not None, "MenuSystem failed to initialize"
        assert menu_system.font is not None, "MenuSystem failed to load essential font"
        # Assertion: Check spectrometer screen initialized if configured
        if USE_SPECTROMETER:
            assert spectrometer_screen is not None, "SpectrometerScreen failed to initialize when USE_SPECTROMETER is True"

        # --- Show Splash Screen ---
        show_splash_screen(screen, display_hat if display_hat_active else None, SPLASH_DURATION_S)

        # --- Show Disclaimer Screen (Pass button_handler and hint font) ---
        if not g_shutdown_flag.is_set():
             # Assertion: Ensure hint_font exists before passing
             assert menu_system.hint_font is not None, "Hint font not loaded before disclaimer call"
             show_disclaimer_screen(screen,
                                   display_hat if display_hat_active else None,
                                   button_handler,
                                   # menu_system.font,      # REMOVED main font arg
                                   menu_system.hint_font) # Pass only hint font

        # --- Setup Signal Handling & Start Background Tasks ---
        # Check shutdown flag again in case user quit during disclaimer
        if g_shutdown_flag.is_set():
            logger.warning("Shutdown requested during startup screens. Exiting early.")
            raise SystemExit("Shutdown during startup") # Use SystemExit for cleaner exit path

        logger.info("Setting up signal handlers and starting background tasks...")
        setup_signal_handlers(button_handler, network_info) # Pass initialized handlers
        network_info.start_updates() # Start network thread


        # --- Main Loop ---
        logger.info("Starting main application loop...")
        current_screen = "MENU" # Track which screen is active: "MENU" or "SPECTROMETER"
        # Loop bound by external flag g_shutdown_flag
        while not g_shutdown_flag.is_set():
            # Assertion: Check flag state is bool
            assert isinstance(g_shutdown_flag.is_set(), bool), "g_shutdown_flag state is not bool"

            if current_screen == "MENU":
                # --- Handle Menu ---
                menu_action = menu_system.handle_input()
                # ... (keep existing action processing for "QUIT", "CAPTURE") ...

                if menu_action == "CAPTURE":
                    if USE_SPECTROMETER and spectrometer_screen:
                        logger.info("Switching to Spectrometer screen...")
                        spectrometer_screen.activate() # Activate the screen
                        current_screen = "SPECTROMETER"
                        # Skip drawing menu this iteration
                        continue
                    else:
                        # ... (handle unavailable spectrometer) ...
                        pass

                # Draw Menu Screen (only if not switching)
                menu_system.draw()
                # Tick Clock
                assert main_clock is not None, "Main clock not initialized"
                main_clock.tick(1.0 / MAIN_LOOP_DELAY_S) # Target FPS for menu

            elif current_screen == "SPECTROMETER":
                # --- Handle Spectrometer Screen ---
                assert USE_SPECTROMETER and spectrometer_screen is not None, "In SPECTROMETER state but screen not available"

                # run_loop now handles input, drawing, timing, and internal state changes
                spectro_status = spectrometer_screen.run_loop() # Returns "BACK" or "QUIT"

                if spectro_status == "QUIT":
                    # Quit signal was handled internally by run_loop setting g_shutdown_flag
                    logger.info("Spectrometer screen signaled QUIT.")
                    # Loop will terminate on next check
                    continue
                elif spectro_status == "BACK":
                    # User pressed Back or screen deactivated normally
                    logger.info("Returning to Menu screen...")
                    current_screen = "MENU"
                    # spectrometer_screen.deactivate() was called by run_loop
                    continue

            else:
                logger.error(f"FATAL: Unknown screen state '{current_screen}'")
                g_shutdown_flag.set() # Unknown state, force shutdown
                
             
    except SystemExit as e: # Catch specific exit reasons like shutdown during startup
        logger.warning(f"Exiting due to SystemExit: {e}")
    except RuntimeError as e: # Catch configuration/initialization errors
        logger.critical(f"RUNTIME ERROR: {e}", exc_info=True)
        g_shutdown_flag.set() # Ensure flag is set on critical errors
    except KeyboardInterrupt: # Handle Ctrl+C if signal handler failed
         logger.warning("KeyboardInterrupt caught directly. Initiating shutdown...")
         g_shutdown_flag.set()
    except Exception as e:
        logger.critical(f"FATAL UNHANDLED EXCEPTION in main function: {e}", exc_info=True)
        g_shutdown_flag.set() # Ensure flag is set on unexpected errors

    finally:
        # --- Cleanup Resources ---
        # This block executes regardless of how the try block exited (normal, exception, SystemExit)
        logger.warning("Initiating final cleanup...")

        # Stop background threads first
        if network_info:
            logger.debug("Stopping network info...")
            try: network_info.stop_updates()
            except Exception as e: logger.error(f"Error stopping network info: {e}")

        # Cleanup application logic (screens)
        if menu_system:
            logger.debug("Cleaning up menu system...")
            try: menu_system.cleanup()
            except Exception as e: logger.error(f"Error cleaning up menu system: {e}")
        # <<< NEW: Cleanup Spectrometer Screen >>>
        if spectrometer_screen:
            logger.debug("Cleaning up spectrometer screen...")
            try: spectrometer_screen.cleanup()
            except Exception as e: logger.error(f"Error cleaning up spectrometer screen: {e}")


        # Cleanup hardware interfaces (GPIO) - ButtonHandler manages its own
        if button_handler:
            logger.debug("Cleaning up button handler / GPIO...")
            try: button_handler.cleanup()
            except Exception as e: logger.error(f"Error cleaning up button handler / GPIO: {e}")

        # Cleanup Display HAT resources if active?
        # Typically, the library might not require explicit cleanup, or it's tied to Pygame exit.
        # Check Pimoroni library docs if specific cleanup is needed for display_hat object.
        # if display_hat_active and display_hat:
        #     try: display_hat.cleanup() # Hypothetical cleanup method
        #     except Exception as e: logger.error(f"Error cleaning up Display HAT: {e}")

        # Quit Pygame last
        if pygame.get_init():
             logger.info("Quitting Pygame...")
             try: pygame.quit()
             except Exception as e: logger.error(f"Error quitting Pygame: {e}")
             logger.info("Pygame quit.")
        else:
             logger.info("Pygame not initialized, skipping quit.")

        logger.info("=" * 44)
        logger.info("   Application Finished.")
        logger.info("=" * 44)

if __name__ == "__main__":
    # Keep __main__ block simple: just call main()
    main()
