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
- Placeholder for spectrometer operations.

Adherence to Safety Principles (NASA Guideline Inspired):
- Rule 1 (Simple Control Flow): Avoids goto, setjmp, recursion. Uses simple loops/conditionals.
- Rule 2 (Loop Bounds): Main loop runs indefinitely until shutdown signal; internal loops are bounded. Network thread runs until shutdown.
- Rule 3 (Dynamic Memory): Minimized after init. Fonts loaded once. Pygame/PIL allocations occur but are managed. String formatting used.
- Rule 4 (Function Length): Functions broken down for clarity and length limits.
- Rule 5 (Assertions): Assertions added to check preconditions, postconditions, and invariants. Aiming for >= 2 per function where meaningful.
- Rule 6 (Scope): Data declared at the narrowest practical scope. Constants are global.
- Rule 7 (Return/Parameter Checks): Assertions check parameters. Return values from critical libs handled via exceptions or checks where possible.
- Rule 8 (Preprocessor): N/A (Python). Comments and docstrings used for clarity.
- Rule 9 (Pointers): N/A (Python references). Callbacks used simply for GPIO/threading.
- Rule 10 (Warnings): Assumes linter usage (e.g., pylint/flake8) during development.
"""

import os
import sys
import time
import signal
import datetime
import subprocess
import threading
import logging

# --- Configuration Flags ---
# Set these flags based on the hardware connected.
# If a flag is True, the code will expect the hardware to be present and attempt initialization.
# If initialization fails despite the flag being True, an error will be logged.
USE_DISPLAY_HAT = True       # Set to True if Pimoroni Display HAT Mini is connected
USE_GPIO_BUTTONS = True      # Set to True if GPIO (LCD/Hall) buttons are connected
USE_HALL_EFFECT_BUTTONS = False # Set to True to map external Hall sensors (requires USE_GPIO_BUTTONS=True)
USE_LEAK_SENSOR = False        # Set to True if the external leak sensor is connected (requires USE_GPIO_BUTTONS=True)
USE_SPECTROMETER = False       # Set to True if the spectrometer is connected and should be used (Feature Not Implemented Yet)

# Attempt to import hardware-specific libraries only if configured
RPi_GPIO = None
if USE_GPIO_BUTTONS:
    try:
        import RPi.GPIO as GPIO
        RPi_GPIO = GPIO # Assign to global-like scope for use
        print("RPi.GPIO library loaded successfully.")
    except ImportError:
        print("ERROR: RPi.GPIO library not found, but USE_GPIO_BUTTONS is True.")
        print("GPIO features will be disabled.")
        USE_GPIO_BUTTONS = False # Disable GPIO usage if library fails
    except RuntimeError:
        print("ERROR: Could not load RPi.GPIO (permissions or platform issue?).")
        print("GPIO features will be disabled.")
        USE_GPIO_BUTTONS = False

DisplayHATMini = None
if USE_DISPLAY_HAT:
    try:
        from displayhatmini import DisplayHATMini
        print("DisplayHATMini library loaded successfully.")
    except ImportError:
        print("ERROR: DisplayHATMini library not found, but USE_DISPLAY_HAT is True.")
        print("Display HAT features will be disabled.")
        USE_DISPLAY_HAT = False # Disable display usage if library fails

# Conditional import for spectrometer (placeholder)
if USE_SPECTROMETER:
    try:
        # import seabreeze etc. here in the future
        print("Spectrometer libraries would be loaded here.")
        pass
    except ImportError:
        print("ERROR: Spectrometer library (e.g., seabreeze) not found, but USE_SPECTROMETER is True.")
        USE_SPECTROMETER = False

# Pygame is always needed for the display buffer and event loop
try:
    import pygame
except ImportError:
    print("FATAL ERROR: Pygame library not found. Cannot run.")
    sys.exit(1)

# --- Constants ---
# Integration Time (ms)
DEFAULT_INTEGRATION_TIME_MS = 1000
MIN_INTEGRATION_TIME_MS = 100
MAX_INTEGRATION_TIME_MS = 60000 # Increased max based on typical spectrometer needs
INTEGRATION_TIME_STEP_MS = 100

# GPIO Pin Definitions (BCM Mode)
# --- Display HAT Mini Buttons (Corrected based on Pimoroni library standard) ---
PIN_DH_A = 5   # Was 16. Physical Button A (often maps to Enter/Right logic) -> GPIO 5
PIN_DH_B = 6   # Was 6. Physical Button B (often maps to Back/Left logic) -> GPIO 6
PIN_DH_X = 16  # Was 12. Physical Button X (often maps to Up logic) -> GPIO 16
PIN_DH_Y = 24  # Was 13. Physical Button Y (often maps to Down logic) -> GPIO 24

# --- External Hall Effect Sensor Pins (Check these carefully for your wiring) ---
# Ensure these DO NOT conflict with DH pins 5, 6, 16, 24 if both are used
PIN_HALL_UP = 18     # Mirrors DH X (Up) -> Check if 18 is okay
PIN_HALL_DOWN = 23   # Mirrors DH Y (Down) -> Check if 23 is okay
PIN_HALL_ENTER = 20  # Mirrors DH A (Enter/Right) -> Check if 20 is okay
PIN_HALL_BACK = 8    # Mirrors DH B (Back/Left) -> Check if 8 is okay

# Leak Sensor Pin
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

# Menu Layout
FONT_SIZE = 16
TITLE_FONT_SIZE = 22
MENU_SPACING = 30
MENU_MARGIN_TOP = 50
MENU_MARGIN_LEFT = 20

# Timing
DEBOUNCE_DELAY_S = 0.2  # Debounce time for buttons
NETWORK_UPDATE_INTERVAL_S = 10.0 # How often to check network status
MAIN_LOOP_DELAY_S = 0.03 # Target ~30 FPS

# --- Logging Setup ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- Global Variables ---
# These are managed primarily within classes or the main function after init
g_shutdown_flag = threading.Event() # Used to signal shutdown to threads and loops

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
        # Clamp month and day to valid ranges first to avoid some errors
        month = max(1, min(12, month))
        # Day clamping needs month/year context, handled by datetime constructor
        new_dt = datetime.datetime(year, month, day, hour, minute, second)
        return new_dt
    except ValueError as e:
        logger.warning(f"Invalid date/time combination attempted: {year}-{month}-{day} {hour}:{minute}:{second}. Error: {e}")
        return None

# --- Classes ---

class ButtonHandler:
    """
    Handles GPIO button inputs (Display HAT via library callback + optional Hall sensors/Leak)
    and maps Pygame key events, providing a unified button interface.
    Adheres to NASA Guideline 5: Uses assertions for parameter checks.
    Adheres to NASA Guideline 7: Checks parameters.
    """

    # Map Pimoroni HAT pins to our logical button names
    _DH_PIN_TO_BUTTON = {
        PIN_DH_A: BTN_ENTER, # 16
        PIN_DH_B: BTN_BACK,   # 6
        PIN_DH_X: BTN_UP,     # 12
        PIN_DH_Y: BTN_DOWN    # 13
    }

    def __init__(self, display_hat_obj=None): # Pass the display_hat object
        """Initializes button states and debounce tracking."""
        self.display_hat = display_hat_obj # Store the display_hat object

        # Determine operational status based on global flags and library availability
        self._gpio_available = USE_GPIO_BUTTONS and RPi_GPIO is not None
        self._display_hat_buttons_enabled = USE_DISPLAY_HAT and self.display_hat is not None
        self._hall_buttons_enabled = USE_HALL_EFFECT_BUTTONS and self._gpio_available
        self._leak_sensor_enabled = USE_LEAK_SENSOR and self._gpio_available

        # Button states are True if pressed *since last check*
        self._button_states = {
            BTN_UP: False,
            BTN_DOWN: False,
            BTN_ENTER: False,
            BTN_BACK: False
        }
        self._state_lock = threading.Lock()

        self._last_press_time = {
            BTN_UP: 0.0,
            BTN_DOWN: 0.0,
            BTN_ENTER: 0.0,
            BTN_BACK: 0.0
        }
        # Map *only manually configured* GPIO pins to logical button names
        self._manual_pin_to_button = {}

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
                # Clean up previous state just in case
                # RPi_GPIO.cleanup() # Use with caution if other things use GPIO
                RPi_GPIO.setmode(GPIO.BCM)
                RPi_GPIO.setwarnings(False)
                logger.info("  GPIO mode set to BCM.")

                # --- Hall Effect Buttons (Manual GPIO Setup) ---
                if self._hall_buttons_enabled:
                    logger.info("  Setting up Hall Effect sensor inputs via RPi.GPIO...")
                    hall_pins = {
                        PIN_HALL_UP: BTN_UP,
                        PIN_HALL_DOWN: BTN_DOWN,
                        PIN_HALL_ENTER: BTN_ENTER,
                        PIN_HALL_BACK: BTN_BACK,
                    }
                    for pin, name in hall_pins.items():
                        # Check for conflict with Display HAT pins (though we won't manually configure HAT pins now)
                        if pin in self._DH_PIN_TO_BUTTON:
                             logger.warning(f"  GPIO Pin {pin} for Hall sensor '{name}' is also used by Display HAT!")
                        RPi_GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
                        RPi_GPIO.add_event_detect(pin, GPIO.FALLING, callback=self._manual_gpio_callback, bouncetime=int(DEBOUNCE_DELAY_S * 1000))
                        self._manual_pin_to_button[pin] = name
                        logger.info(f"    Mapped Manual GPIO {pin} (Hall) to '{name}'")
                else:
                    logger.info("  Hall Effect button inputs disabled or GPIO unavailable.")

                # --- Leak Sensor (Manual GPIO Setup) ---
                if self._leak_sensor_enabled:
                    logger.info(f"  Setting up Leak sensor input on GPIO {PIN_LEAK} via RPi.GPIO...")
                    RPi_GPIO.setup(PIN_LEAK, GPIO.IN, pull_up_down=GPIO.PUD_UP)
                    RPi_GPIO.add_event_detect(PIN_LEAK, GPIO.FALLING, callback=self._leak_callback, bouncetime=1000)
                else:
                    logger.info("  Leak sensor input disabled or GPIO unavailable.")

            except RuntimeError as e: # Catch the specific error
                 logger.error(f"CRITICAL RUNTIME ERROR setting up manual GPIO: {e}", exc_info=True)
                 logger.error("Manual GPIO functionality (Hall/Leak) FAILED. Check permissions, pin conflicts, and hardware.")
                 # Decide how to handle this - maybe disable Hall/Leak flags?
                 self._hall_buttons_enabled = False
                 self._leak_sensor_enabled = False
            except Exception as e:
                 logger.error(f"CRITICAL EXCEPTION setting up manual GPIO: {e}", exc_info=True)
                 self._hall_buttons_enabled = False
                 self._leak_sensor_enabled = False


        # --- Display HAT Button Setup (using Library Callback) ---
        if self._display_hat_buttons_enabled:
            try:
                logger.info("  Registering Display HAT button callback...")
                # Make sure the library's internal GPIO setup doesn't conflict
                # The library likely handles setmode/warnings internally.
                # The key is to use *its* callback mechanism.
                self.display_hat.on_button_pressed(self._display_hat_callback)
                logger.info("  Display HAT button callback registered successfully.")
            except Exception as e:
                logger.error(f"Failed to register Display HAT button callback: {e}", exc_info=True)
                self._display_hat_buttons_enabled = False # Mark as failed
        else:
            logger.info("  Display HAT buttons disabled or unavailable.")


    def _display_hat_callback(self, pin):
        """
        Internal callback for Display HAT button press events (triggered by the library).
        Handles debouncing logic internally based on last press time.
        """
        button_name = self._DH_PIN_TO_BUTTON.get(pin)
        if button_name is None:
            logger.warning(f"Display HAT callback for unknown pin: {pin}")
            return

        # Check if the button is actually pressed (library might send press/release)
        # We only care about the press event (FALLING edge equivalent)
        # The library's on_button_pressed usually triggers on press-down.
        # We'll rely on our own debounce timer here.

        current_time = time.monotonic()
        with self._state_lock:
             last_press = self._last_press_time.get(button_name, 0.0)
             if (current_time - last_press) > DEBOUNCE_DELAY_S:
                 self._button_states[button_name] = True
                 self._last_press_time[button_name] = current_time
                 logger.debug(f"Display HAT Button pressed: {button_name} (Pin {pin})")
             # else: logger.debug(f"Display HAT Button bounce ignored: {button_name}")


    def _manual_gpio_callback(self, channel):
        """
        Internal callback for manually configured GPIO events (Hall sensors).
        Handles debouncing.
        """
        button_name = self._manual_pin_to_button.get(channel)
        if button_name is None:
            logger.warning(f"Manual GPIO callback for unknown channel: {channel}")
            return

        current_time = time.monotonic()
        with self._state_lock:
             last_press = self._last_press_time.get(button_name, 0.0)
             if (current_time - last_press) > DEBOUNCE_DELAY_S:
                 self._button_states[button_name] = True
                 self._last_press_time[button_name] = current_time
                 logger.debug(f"Manual GPIO Button pressed: {button_name} (Pin {channel})")
             # else: logger.debug(f"Manual GPIO Button bounce ignored: {button_name}")

    def _leak_callback(self, channel):
        """Callback function for leak detection GPIO event."""
        logger.critical(f"!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!")
        logger.critical(f"!!! WATER LEAK DETECTED on GPIO {channel} !!!")
        logger.critical(f"!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!")
        # Add persistent flag / immediate action here in future

    def check_button(self, button_name):
        """
        Checks if a specific button was pressed since the last check.
        Resets the button state after checking. Returns True if pressed, False otherwise.
        """
        assert button_name in self._button_states, f"Invalid button name requested: {button_name}"
        pressed = False
        with self._state_lock:
            if self._button_states[button_name]:
                pressed = True
                self._button_states[button_name] = False
        return pressed

    def process_pygame_events(self):
        """ Processes Pygame events, mapping keys to button states. """
        # ... (this method remains the same) ...
        quit_requested = False
        events = pygame.event.get() # Get all events since last call

        for event in events:
            if event.type == pygame.QUIT:
                logger.info("Pygame QUIT event received.")
                quit_requested = True
                # Don't break, process other events too if needed

            if event.type == pygame.KEYDOWN:
                key_map = {
                    pygame.K_UP: BTN_UP,
                    pygame.K_DOWN: BTN_DOWN,
                    pygame.K_RETURN: BTN_ENTER,
                    pygame.K_RIGHT: BTN_ENTER,
                    pygame.K_BACKSPACE: BTN_BACK,
                    pygame.K_LEFT: BTN_BACK,
                    pygame.K_ESCAPE: "QUIT" # Map escape key to quit
                }
                button_name = key_map.get(event.key)

                if button_name == "QUIT":
                    logger.info("Escape key pressed, requesting QUIT.")
                    quit_requested = True
                elif button_name:
                     # Simulate immediate press for keys, no debounce needed here
                     with self._state_lock:
                         self._button_states[button_name] = True
                     logger.debug(f"Key mapped to button press: {button_name}")
                # else: logger.debug(f"Unmapped key pressed: {event.key}") # Optional debug

        return "QUIT" if quit_requested else None


    def cleanup(self):
        """Cleans up GPIO resources if they were used *manually*."""
        # We only need to clean up GPIO if we *manually* set it up.
        # The Display HAT library should manage its own resources.
        # Check if manual setup was attempted and potentially succeeded partially
        if self._gpio_available and (self._hall_buttons_enabled or self._leak_sensor_enabled):
            try:
                logger.info("Cleaning up manually configured GPIO pins...")
                # Selectively clean up only the pins we added events for?
                # Or just call general cleanup? General cleanup is safer if unsure.
                RPi_GPIO.cleanup()
                logger.info("Manual GPIO cleanup complete.")
            except Exception as e:
                logger.error(f"Error during manual GPIO cleanup: {e}")
        else:
            logger.info("Manual GPIO cleanup skipped (not used or unavailable).")


class NetworkInfo:
    """
    Handles retrieval of network information (WiFi SSID, IP Address).
    Runs network checks in a separate thread to avoid blocking the main UI loop.
    Adheres to NASA Guideline 5: Uses assertions (implicitly via usage).
    Adheres to NASA Guideline 7: Uses try/except for external process calls.
    """
    _WLAN_IFACE = "wlan0" # Network interface to check

    def __init__(self):
        """Initializes network info placeholders and starts the update thread."""
        self._wifi_name = "Initializing..."
        self._ip_address = "Initializing..."
        self._lock = threading.Lock() # Protect access to shared state
        self._update_thread = None
        self._last_update_time = 0.0

        # Assertion: Ensure global shutdown flag exists
        assert isinstance(g_shutdown_flag, threading.Event), "Global shutdown flag not initialized"

    def start_updates(self):
        """Starts the background thread to periodically update network info."""
        # Assertion: Ensure thread is not already running
        assert self._update_thread is None or not self._update_thread.is_alive(), "Network update thread already started"

        logger.info("Starting network info update thread.")
        self._update_thread = threading.Thread(target=self._network_update_loop, daemon=True)
        self._update_thread.start()

    def stop_updates(self):
        """Signals the update thread to stop and waits for it to join."""
        # Assertion: None needed directly. Relies on thread state.
        logger.info("Stopping network info update thread.")
        # Signal is done via global g_shutdown_flag checked in the loop
        if self._update_thread and self._update_thread.is_alive():
            try:
                self._update_thread.join(timeout=NETWORK_UPDATE_INTERVAL_S + 1.0) # Wait a bit longer than interval
                if self._update_thread.is_alive():
                     logger.warning("Network update thread did not terminate cleanly.")
            except Exception as e:
                logger.error(f"Error joining network update thread: {e}")
        self._update_thread = None # Clear the thread object
        logger.info("Network info update thread stopped.")

    def get_wifi_name(self):
        """Returns the last known WiFi SSID."""
        # Assertion: None needed. Returns internal state.
        with self._lock:
            return self._wifi_name

    def get_ip_address(self):
        """Returns the last known IP address."""
        # Assertion: None needed. Returns internal state.
        with self._lock:
            return self._ip_address

    def _is_interface_up(self):
        """Checks if the WLAN interface is operationally 'up'."""
        # Assertion: None needed. Checks file system.
        operstate_path = f"/sys/class/net/{self._WLAN_IFACE}/operstate"
        try:
            with open(operstate_path, 'r') as f:
                status = f.read().strip().lower()
                return status == 'up'
        except FileNotFoundError:
            # logger.debug(f"Network interface '{self._WLAN_IFACE}' not found.")
            return False
        except Exception as e:
            logger.error(f"Error checking interface status for {self._WLAN_IFACE}: {e}")
            return False

    def _fetch_wifi_name(self):
        """Uses 'iwgetid' to get the current SSID."""
        # Assertion: None needed. Calls external process.
        # Adheres to NASA Guideline 7: Checks return code and output.
        if not self._is_interface_up():
            return "Not Connected"
        try:
            # Use timeout to prevent indefinite blocking
            result = subprocess.run(
                ['iwgetid', '-r'],
                capture_output=True, text=True, check=False, timeout=5.0
            )
            if result.returncode == 0 and result.stdout.strip():
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
            return "Error"

    def _fetch_ip_address(self):
        """Uses 'hostname -I' to get the IP address."""
        # Assertion: None needed. Calls external process.
        # Adheres to NASA Guideline 7: Checks return code and output.
        if not self._is_interface_up():
            return "Not Connected"
        try:
             # Use timeout to prevent indefinite blocking
            result = subprocess.run(
                ['hostname', '-I'],
                capture_output=True, text=True, check=False, timeout=5.0
            )
            if result.returncode == 0 and result.stdout.strip():
                # Return the first IP address if multiple are listed
                return result.stdout.strip().split()[0]
            else:
                # logger.debug(f"hostname -I failed or returned empty: code={result.returncode}, stdout='{result.stdout.strip()}'")
                return "No IP" # Interface up, but no IP yet
        except FileNotFoundError:
             logger.error("'hostname' command not found. Cannot get IP address.")
             return "Error (No hostname)"
        except subprocess.TimeoutExpired:
             logger.warning("'hostname -I' command timed out.")
             return "Error (Timeout)"
        except Exception as e:
            logger.error(f"Error running hostname -I: {e}")
            return "Error"

    def _network_update_loop(self):
        """Periodically updates network info until shutdown is signaled."""
        # Assertion: None needed for loop control itself.
        logger.info("Network update loop started.")
        while not g_shutdown_flag.is_set():
            try:
                # Fetch new data
                new_wifi = self._fetch_wifi_name()
                new_ip = self._fetch_ip_address()

                # Update shared state safely
                with self._lock:
                    self._wifi_name = new_wifi
                    self._ip_address = new_ip

                self._last_update_time = time.monotonic()
                # logger.debug(f"Network info updated: WiFi='{new_wifi}', IP='{new_ip}'")

            except Exception as e:
                 logger.error(f"Error in network update loop: {e}", exc_info=True)
                 # Continue loop despite error, maybe back off delay?

            # Wait for the next update interval or shutdown signal
            g_shutdown_flag.wait(timeout=NETWORK_UPDATE_INTERVAL_S)

        logger.info("Network update loop finished.")


class MenuSystem:
    """
    Manages the main menu UI, state, and interactions.
    Adheres to NASA Guideline 4: Functions broken down.
    Adheres to NASA Guideline 5: Uses assertions.
    Adheres to NASA Guideline 6: Scopes data appropriately.
    Adheres to NASA Guideline 7: Checks parameters, handles returns.
    """
    # Define menu items and their corresponding actions or edit types
    MENU_ITEM_CAPTURE = "Capture Spectra"
    MENU_ITEM_INTEGRATION = "Integration Time"
    MENU_ITEM_DATE = "Date"
    MENU_ITEM_TIME = "Time"
    MENU_ITEM_WIFI = "WiFi"
    MENU_ITEM_IP = "IP Address"

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
    FIELD_SECOND = 'second'


    def __init__(self, screen, button_handler, network_info):
        assert screen is not None, "Pygame screen object is required"
        assert button_handler is not None, "ButtonHandler object is required"
        assert network_info is not None, "NetworkInfo object is required"

        self.screen = screen
        self.button_handler = button_handler
        self.network_info = network_info
        self.display_hat = None

        # --- Application State ---
        self._integration_time_ms = DEFAULT_INTEGRATION_TIME_MS
        # --- NEW: Application's internal time reference ---
        # Initialized from system time at startup. Can be adjusted by user.
        self._app_datetime_ref = datetime.datetime.now()
        # Store original state only to allow discarding edits within a session
        self._original_datetime_on_edit_start = None
        # Store time offset relative to system clock
        self._time_offset = datetime.timedelta(0) # Initialise with zero offset

        # --- Menu Structure ---
        self._menu_items = [
            (self.MENU_ITEM_CAPTURE, self.EDIT_TYPE_NONE),
            (self.MENU_ITEM_INTEGRATION, self.EDIT_TYPE_INTEGRATION),
            (self.MENU_ITEM_DATE, self.EDIT_TYPE_DATE),
            (self.MENU_ITEM_TIME, self.EDIT_TYPE_TIME),
            (self.MENU_ITEM_WIFI, self.EDIT_TYPE_NONE),
            (self.MENU_ITEM_IP, self.EDIT_TYPE_NONE),
        ]
        self._current_selection_idx = 0

        # --- Editing State ---
        self._is_editing = False
        self._editing_field = None
        self._datetime_being_edited = None
        self._original_offset_on_edit_start = None

        # --- Font Initialization ---
        try:
            pygame.font.init()
            # Use a common font if possible, provide fallback
            try:
                 # Try a specific common font first
                 self.font = pygame.font.Font("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", FONT_SIZE)
                 self.title_font = pygame.font.Font("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", TITLE_FONT_SIZE)
            except: # Broad except for font loading issues
                 try: # Try default pygame font
                      self.font = pygame.font.Font(pygame.font.get_default_font(), FONT_SIZE)
                      self.title_font = pygame.font.Font(pygame.font.get_default_font(), TITLE_FONT_SIZE)
                 except: # Fallback to SysFont
                      self.font = pygame.font.SysFont(None, FONT_SIZE)
                      self.title_font = pygame.font.SysFont(None, TITLE_FONT_SIZE)
            logger.info("Fonts initialized.")
        except Exception as e:
            logger.error(f"Failed to initialize Pygame fonts: {e}", exc_info=True)
            self.font = None
            self.title_font = None
            # Consider raising RuntimeError here if fonts are essential

    # --- Helper to get the time to display/use ---
    def _get_current_app_display_time(self):
        """Calculates the current time including the user-defined offset."""
        # Use timezone-naive datetime objects for simplicity here
        # Be aware of potential issues if system time transitions DST while app is running
        # For simple offset, naive should be okay.
        return datetime.datetime.now() + self._time_offset
    
    # --- Public Methods ---

    def handle_input(self):
        pygame_event_result = self.button_handler.process_pygame_events()
        if pygame_event_result == "QUIT": return "QUIT"

        action = None
        if self._is_editing: action = self._handle_editing_input()
        else: action = self._handle_navigation_input()

        # Handle actions
        if action == "EXIT_EDIT_SAVE":
            self._is_editing = False
            self._editing_field = None
            self._commit_time_offset_changes() # New commit function name
            self._datetime_being_edited = None # Clear temporary edit object
            self._original_offset_on_edit_start = None
            return None
        elif action == "EXIT_EDIT_DISCARD":
            self._is_editing = False
            self._editing_field = None
            # Restore the original offset
            if self._original_offset_on_edit_start is not None:
                self._time_offset = self._original_offset_on_edit_start
                logger.info("Exited editing mode, time offset changes discarded.")
            else:
                logger.warning("Exited editing mode via BACK, but no original offset found to revert to.")
            self._datetime_being_edited = None # Clear temporary edit object
            self._original_offset_on_edit_start = None
            return None
        elif action == "START_CAPTURE": return "CAPTURE"
        elif action == "QUIT": return "QUIT"
        else: return None

    def draw(self):
        assert self.font and self.title_font, "Fonts were not loaded successfully"
        self.screen.fill(BLACK)
        self._draw_title()
        self._draw_menu_items() # Will use offset calculation
        self._draw_hints()
        self._update_hardware_display()

    def get_timestamp_datetime(self):
        """Returns a datetime object representing the current app time (System + Offset)."""
        # Useful for getting the time to embed in filenames etc.
        return self._get_current_app_display_time()

    # --- Private Input Handling Methods ---

    def _handle_navigation_input(self):
        # ... (Remains unchanged) ...
        assert not self._is_editing
        if self.button_handler.check_button(BTN_UP): self._navigate_menu(-1)
        elif self.button_handler.check_button(BTN_DOWN): self._navigate_menu(1)
        elif self.button_handler.check_button(BTN_ENTER): return self._select_menu_item()
        elif self.button_handler.check_button(BTN_BACK): logger.info("BACK pressed in main menu (no action).")
        return None

    def _handle_editing_input(self):
        # ... (Remains unchanged - calls adjust or next_field) ...
        assert self._is_editing
        edit_type = self._menu_items[self._current_selection_idx][1]
        action = None
        if self.button_handler.check_button(BTN_UP): action = self._handle_edit_adjust(edit_type, 1)
        elif self.button_handler.check_button(BTN_DOWN): action = self._handle_edit_adjust(edit_type, -1)
        elif self.button_handler.check_button(BTN_ENTER): action = self._handle_edit_next_field(edit_type)
        elif self.button_handler.check_button(BTN_BACK): action = "EXIT_EDIT_DISCARD" # Discard offset changes
        return action

    def _navigate_menu(self, direction):
        # ... (Remains unchanged) ...
        assert direction in [-1, 1]
        num_items = len(self._menu_items)
        assert num_items > 0
        self._current_selection_idx = (self._current_selection_idx + direction) % num_items
        logger.debug(f"Menu navigated. New selection index: {self._current_selection_idx}, Item: {self._menu_items[self._current_selection_idx][0]}")
    
    def _select_menu_item(self):
        # ... (Capture logic unchanged) ...
        item_text, edit_type = self._menu_items[self._current_selection_idx]
        logger.info(f"Menu item selected: {item_text}")

        if item_text == self.MENU_ITEM_CAPTURE:
            if USE_SPECTROMETER: return "START_CAPTURE"
            else: logger.warning("Capture Spectra selected, but USE_SPECTROMETER is False."); return None

        # --- Start Editing ---
        elif edit_type in [self.EDIT_TYPE_INTEGRATION, self.EDIT_TYPE_DATE, self.EDIT_TYPE_TIME]:
            self._is_editing = True
            # Store current offset for potential discard
            self._original_offset_on_edit_start = self._time_offset
            # Initialize the temporary datetime object based on current app time
            self._datetime_being_edited = self._get_current_app_display_time()

            if edit_type == self.EDIT_TYPE_INTEGRATION:
                self._editing_field = None
                logger.info(f"Starting to edit Integration Time (Current: {self._integration_time_ms} ms).")
            elif edit_type == self.EDIT_TYPE_DATE:
                self._editing_field = self.FIELD_YEAR
                logger.info(f"Starting to edit Date. Initial edit value: {self._datetime_being_edited.strftime('%Y-%m-%d')}")
            elif edit_type == self.EDIT_TYPE_TIME:
                self._editing_field = self.FIELD_HOUR
                logger.info(f"Starting to edit Time. Initial edit value: {self._datetime_being_edited.strftime('%H:%M')}")
        # ... (Read-only items) ...
        return None

    def _handle_edit_adjust(self, edit_type, delta):
        """ Adjusts integration time OR the temporary _datetime_being_edited. """
        assert self._is_editing, "Adjust called when not editing"
        assert delta in [-1, 1], "Invalid adjustment delta"

        if edit_type == self.EDIT_TYPE_INTEGRATION:
            # ... (Integration time logic unchanged) ...
            if delta > 0: self._integration_time_ms = min(self._integration_time_ms + INTEGRATION_TIME_STEP_MS, MAX_INTEGRATION_TIME_MS)
            else: self._integration_time_ms = max(self._integration_time_ms - INTEGRATION_TIME_STEP_MS, MIN_INTEGRATION_TIME_MS)
            logger.debug(f"Integration time adjusted to {self._integration_time_ms} ms")
        elif edit_type == self.EDIT_TYPE_DATE:
             # Modify the temporary absolute datetime
             self._change_date_field(delta)
        elif edit_type == self.EDIT_TYPE_TIME:
             # Modify the temporary absolute datetime
             self._change_time_field(delta)
        return None

    def _handle_edit_next_field(self, edit_type):
        # ... (Logic remains the same, returns EXIT_EDIT_SAVE on finish) ...
        assert self._is_editing
        if edit_type == self.EDIT_TYPE_INTEGRATION: return "EXIT_EDIT_SAVE"
        elif edit_type == self.EDIT_TYPE_DATE:
            if self._editing_field == self.FIELD_YEAR: self._editing_field = self.FIELD_MONTH; logger.debug("Editing next field: Month")
            elif self._editing_field == self.FIELD_MONTH: self._editing_field = self.FIELD_DAY; logger.debug("Editing next field: Day")
            elif self._editing_field == self.FIELD_DAY: logger.debug("Finished editing Date fields."); return "EXIT_EDIT_SAVE"
        elif edit_type == self.EDIT_TYPE_TIME:
            if self._editing_field == self.FIELD_HOUR: self._editing_field = self.FIELD_MINUTE; logger.debug("Editing next field: Minute")
            elif self._editing_field == self.FIELD_MINUTE: logger.debug("Finished editing Time fields."); return "EXIT_EDIT_SAVE"
        return None


    # --- Private Date/Time Manipulation ---

    def _change_date_field(self, delta):
        """ Increments/decrements the date field of the temporary _datetime_being_edited. """
        assert self._datetime_being_edited is not None, "Cannot change date field, not in edit mode"
        assert self._editing_field in [self.FIELD_YEAR, self.FIELD_MONTH, self.FIELD_DAY]

        current_dt = self._datetime_being_edited # Operate on the temporary object
        year, month, day = current_dt.year, current_dt.month, current_dt.day
        hour, minute, second = current_dt.hour, current_dt.minute, current_dt.second # Preserve time

        logger.debug(f"Attempting to change temporary Date field '{self._editing_field}' by {delta}")
        # ... (Year/Month/Day adjustment logic - applied to local vars year, month, day) ...
        if self._editing_field == self.FIELD_YEAR:
            year += delta
            year = max(1970, min(2100, year))
        elif self._editing_field == self.FIELD_MONTH:
            month += delta
            if month > 12: month = 1
            if month < 1: month = 12
        elif self._editing_field == self.FIELD_DAY:
            import calendar
            try: _, max_days = calendar.monthrange(year, month); day += delta
            except ValueError: logger.warning(f"Invalid date ({year}-{month}) for day calc"); max_days=31 # Fallback
            if day > max_days: day = 1
            if day < 1: day = max_days

        # Attempt to create the new temporary date
        new_datetime = get_safe_datetime(year, month, day, hour, minute, second)
        if new_datetime:
            self._datetime_being_edited = new_datetime # Update the temporary object
            logger.debug(f"Temporary Date being edited is now: {self._datetime_being_edited.strftime('%Y-%m-%d')}")
        else:
            logger.warning(f"Date field change resulted in invalid date. Temporary date not updated.")

    def _change_time_field(self, delta):
        """ Increments/decrements the time field of the temporary _datetime_being_edited. """
        assert self._datetime_being_edited is not None, "Cannot change time field, not in edit mode"
        assert self._editing_field in [self.FIELD_HOUR, self.FIELD_MINUTE]

        current_dt = self._datetime_being_edited # Operate on the temporary object
        hour, minute = current_dt.hour, current_dt.minute
        # Preserve date and seconds
        year, month, day, second = current_dt.year, current_dt.month, current_dt.day, current_dt.second

        logger.debug(f"Attempting to change temporary Time field '{self._editing_field}' by {delta}")

        if self._editing_field == self.FIELD_HOUR: hour = (hour + delta) % 24
        elif self._editing_field == self.FIELD_MINUTE: minute = (minute + delta) % 60

        # Update the temporary datetime object using replace
        self._datetime_being_edited = self._datetime_being_edited.replace(hour=hour, minute=minute)
        logger.debug(f"Temporary Time being edited is now: {self._datetime_being_edited.strftime('%H:%M')}")

    def _commit_time_offset_changes(self):
        """ Calculates and stores the new time offset based on the final edited datetime. """
        if self._datetime_being_edited is not None:
            # Final desired absolute time
            final_edited_time = self._datetime_being_edited
            # Current system time
            current_system_time = datetime.datetime.now()

            # Calculate the difference
            new_offset = final_edited_time - current_system_time

            # Store the new offset
            self._time_offset = new_offset

            logger.info(f"Time offset update finalized.")
            logger.info(f"Final edited time: {final_edited_time.strftime('%Y-%m-%d %H:%M:%S.%f')}")
            logger.info(f"System time at commit: {current_system_time.strftime('%Y-%m-%d %H:%M:%S.%f')}")
            logger.info(f"New time offset stored: {self._time_offset}")
        else:
            logger.warning("Commit called but no datetime was being edited.")


    # --- Private Drawing Methods ---        
    def _draw_menu_items(self):
        """ Draws the menu, using the calculated display time OR the value being edited. """
        y_position = MENU_MARGIN_TOP
        # Calculate the time to show when *not* editing this specific line
        datetime_to_display_default = self._get_current_app_display_time()

        for i, (item_text, edit_type) in enumerate(self._menu_items):
            display_text = ""
            is_selected = (i == self._current_selection_idx)
            is_being_edited = (is_selected and self._is_editing)

            # --- Determine which datetime object to use for formatting ---
            datetime_for_formatting = None
            if is_being_edited and edit_type in [self.EDIT_TYPE_DATE, self.EDIT_TYPE_TIME]:
                # If editing Date/Time, use the temporary value being changed
                # Ensure it's not None (shouldn't be if _is_editing is True for these types)
                assert self._datetime_being_edited is not None, "In edit mode for Date/Time but _datetime_being_edited is None"
                datetime_for_formatting = self._datetime_being_edited
            else:
                # Otherwise, use the calculated (now + offset) time
                datetime_for_formatting = datetime_to_display_default

            # --- Generate display strings using the selected datetime object ---
            if item_text == self.MENU_ITEM_INTEGRATION:
                 display_text = f"Integration: {self._integration_time_ms} ms" # Integration is simple value
            elif item_text == self.MENU_ITEM_DATE:
                 display_text = f"Date: {datetime_for_formatting.strftime('%Y-%m-%d')}"
            elif item_text == self.MENU_ITEM_TIME:
                 display_text = f"Time: {datetime_for_formatting.strftime('%H:%M')}" # No seconds
            elif item_text == self.MENU_ITEM_WIFI:
                 display_text = f"WiFi: {self.network_info.get_wifi_name()}"
            elif item_text == self.MENU_ITEM_IP:
                 display_text = f"IP: {self.network_info.get_ip_address()}"
            else: display_text = item_text # e.g., "Capture Spectra"

            # --- Determine color ---
            color = WHITE
            is_connected = "Not Connected" not in display_text and "Error" not in display_text
            if is_selected: color = YELLOW if self._is_editing else GREEN
            elif (item_text == self.MENU_ITEM_WIFI or item_text == self.MENU_ITEM_IP) and not is_connected: color = GRAY

            # --- Render and Blit ---
            text_surface = self.font.render(display_text, True, color)
            self.screen.blit(text_surface, (MENU_MARGIN_LEFT, y_position))

            # --- Draw Editing Highlight ---
            # Highlight logic remains the same, it needs the string representation
            # of the value being edited for positioning.
            if is_being_edited and edit_type in [self.EDIT_TYPE_DATE, self.EDIT_TYPE_TIME]:
                 # We still need the string based on _datetime_being_edited for the highlight function
                 if self._datetime_being_edited:
                     if edit_type == self.EDIT_TYPE_DATE:
                          highlight_text_source = f"Date: {self._datetime_being_edited.strftime('%Y-%m-%d')}"
                     elif edit_type == self.EDIT_TYPE_TIME:
                          highlight_text_source = f"Time: {self._datetime_being_edited.strftime('%H:%M')}"
                     self._draw_editing_highlight(highlight_text_source, y_position)
                 else:
                      logger.warning("Attempting to draw highlight but _datetime_being_edited is None")

            y_position += MENU_SPACING


    def _draw_title(self):
        """Draws the main title."""
        # Assertion: Should have valid font here (checked in draw)
        assert self.title_font, "Title font not loaded"
        title_text = self.title_font.render("Underwater Spectrometer", True, WHITE)
        # Center the title horizontally, place it near the top
        title_rect = title_text.get_rect(centerx=SCREEN_WIDTH // 2, top=10)
        self.screen.blit(title_text, title_rect)

    def _draw_editing_highlight(self, full_text_being_edited, y_pos):
        """ Draws highlight based on the string representation of the value being edited. """
        # This function's logic remains the same - it works on the passed string
        # ... (Calculations based on full_text_being_edited) ...
        assert self._editing_field is not None
        assert self.font
        try: prefix_width = self.font.size(full_text_being_edited.split(':')[0] + ': ')[0]; value_start_x = MENU_MARGIN_LEFT + prefix_width
        except Exception as e: logger.error(f"Highlight prefix error: {e}"); return

        highlight_rect = None; value_str = ""; field_str = ""; offset_str = ""
        try:
            edit_type = self._menu_items[self._current_selection_idx][1]
            # Use the passed string directly for parsing highlight position
            value_part = full_text_being_edited.split(': ')[1]

            if edit_type == self.EDIT_TYPE_DATE:
                if self._editing_field == self.FIELD_YEAR: field_str, offset_str = value_part[0:4], ""
                elif self._editing_field == self.FIELD_MONTH: field_str, offset_str = value_part[5:7], value_part[0:5]
                elif self._editing_field == self.FIELD_DAY: field_str, offset_str = value_part[8:10], value_part[0:8]
                else: return
            elif edit_type == self.EDIT_TYPE_TIME: # Format is HH:MM
                if self._editing_field == self.FIELD_HOUR: field_str, offset_str = value_part[0:2], ""
                elif self._editing_field == self.FIELD_MINUTE: field_str, offset_str = value_part[3:5], value_part[0:3]
                else: return

            field_width = self.font.size(field_str)[0]; offset_width = self.font.size(offset_str)[0]
            highlight_x = value_start_x + offset_width
            highlight_rect = pygame.Rect(highlight_x - 1, y_pos - 1, field_width + 2, FONT_SIZE + 2)
        except Exception as e: logger.error(f"Highlight calc error: {e}"); return

        if highlight_rect: pygame.draw.rect(self.screen, RED, highlight_rect, 1)

    def _draw_hints(self):
        """ Draws context-sensitive control hints at the bottom. (Updated) """
        hint_text = ""
        if self._is_editing:
            # New editing hints
             hint_text = "UP/DN: Adjust | ENT: Next/Save | BCK: Cancel"
        else:
            # Navigation hints remain the same
            hint_text = "UP/DN: Navigate | ENT: Select/Edit | BCK: Back"

        hint_surface = self.font.render(hint_text, True, YELLOW)
        hint_rect = hint_surface.get_rect(left=MENU_MARGIN_LEFT, bottom=SCREEN_HEIGHT - 10)
        self.screen.blit(hint_surface, hint_rect)

    def _update_hardware_display(self):
        """Updates the physical display (Pimoroni or standard Pygame window)."""
        if USE_DISPLAY_HAT and self.display_hat:
            try:
                # Rotate buffer for Display HAT Mini orientation
                rotated_surface = pygame.transform.rotate(self.screen, 180)
                # Convert to 16-bit format expected by display
                pixelbytes = rotated_surface.convert(16, 0).get_buffer()
                # Perform byteswap needed for ST7789
                pixelbytes_swapped = bytearray(pixelbytes)
                pixelbytes_swapped[0::2], pixelbytes_swapped[1::2] = pixelbytes_swapped[1::2], pixelbytes_swapped[0::2]

                # Send data to display in chunks
                # Assertion: Ensure display_hat object and st7789 interface exist
                assert hasattr(self.display_hat, 'st7789'), "Display HAT object missing st7789 interface"
                self.display_hat.st7789.set_window()
                chunk_size = 4096
                for i in range(0, len(pixelbytes_swapped), chunk_size):
                    self.display_hat.st7789.data(pixelbytes_swapped[i:i + chunk_size])

            except Exception as e:
                logger.error(f"Error updating Display HAT Mini: {e}", exc_info=False) # Avoid excessive logging
                # Consider fallback or marking display as failed?
        else:
            # Standard Pygame window update
            try:
                 pygame.display.flip()
            except Exception as e:
                 logger.error(f"Error updating Pygame display: {e}", exc_info=True)
                 # If display fails, maybe trigger shutdown?


# --- Spectrometer Placeholder Screen ---
# TODO: Implement this screen properly later

def show_capture_placeholder(screen, display_hat):
     """Displays a placeholder message for the capture screen."""
     logger.info("Displaying Capture Spectra placeholder screen.")
     font = pygame.font.SysFont(None, 30)
     text = font.render("Capture Mode (Not Implemented)", True, WHITE)
     screen.fill(BLACK)
     screen_rect = screen.get_rect()
     text_rect = text.get_rect(center=screen_rect.center)
     screen.blit(text, text_rect)

     # Update display
     if USE_DISPLAY_HAT and display_hat:
         # Re-use the hardware update logic (maybe refactor _update_hardware_display into a utility?)
         try:
            rotated_surface = pygame.transform.rotate(screen, 180)
            pixelbytes = rotated_surface.convert(16, 0).get_buffer()
            pixelbytes_swapped = bytearray(pixelbytes)
            pixelbytes_swapped[0::2], pixelbytes_swapped[1::2] = pixelbytes_swapped[1::2], pixelbytes_swapped[0::2]
            display_hat.st7789.set_window()
            chunk_size = 4096
            for i in range(0, len(pixelbytes_swapped), chunk_size):
                display_hat.st7789.data(pixelbytes_swapped[i:i + chunk_size])
         except Exception as e:
            logger.error(f"Error updating Display HAT Mini for placeholder: {e}")
     else:
         pygame.display.flip()

     # Wait a moment then allow return (or handle input here for placeholder)
     time.sleep(2.0)
     logger.info("Returning from placeholder screen.")


# --- Signal Handling ---

def setup_signal_handlers(button_handler, network_info):
    """Sets up signal handlers for graceful shutdown."""
    # Assertion: Ensure handlers are provided
    assert button_handler is not None, "Button handler required for cleanup"
    assert network_info is not None, "Network info required for cleanup"

    def signal_handler(sig, frame):
        logger.warning(f"Received signal {sig}. Initiating graceful shutdown...")
        g_shutdown_flag.set() # Signal threads and loops to stop
        # Note: Cleanup is now primarily handled in the main finally block

    signal.signal(signal.SIGINT, signal_handler)  # Ctrl+C
    signal.signal(signal.SIGTERM, signal_handler) # kill command
    logger.info("Signal handlers set up for SIGINT and SIGTERM.")


def main():
    """Main application entry point."""
    logger.info("============================================")
    logger.info("   Underwater Spectrometer Controller Start ")
    logger.info("============================================")
    logger.info(f"Configuration: DisplayHAT={USE_DISPLAY_HAT}, GPIO={USE_GPIO_BUTTONS}, HallSensors={USE_HALL_EFFECT_BUTTONS}, LeakSensor={USE_LEAK_SENSOR}, Spectrometer={USE_SPECTROMETER}")

    display_hat_operational = USE_DISPLAY_HAT
    # Note: ButtonHandler now manages its internal GPIO/HAT status

    display_hat = None
    screen = None
    button_handler = None
    network_info = None
    menu_system = None
    main_clock = pygame.time.Clock()

    try:
        # --- Initialize Display ---
        logger.info("Initializing display...")
        pygame.init()

        if USE_DISPLAY_HAT:
            try:
                os.environ['SDL_VIDEODRIVER'] = 'dummy'
                pygame.display.init()
                screen = pygame.Surface((SCREEN_WIDTH, SCREEN_HEIGHT))
                display_hat = DisplayHATMini(None) # <<< Initialize HAT first
                logger.info("DisplayHATMini initialized successfully.")
            except Exception as e:
                logger.error(f"Failed to initialize DisplayHATMini: {e}", exc_info=True)
                logger.error("Falling back to standard Pygame window (if possible).")
                display_hat_operational = False
                display_hat = None # <<< Ensure display_hat is None if init failed
                os.environ.pop('SDL_VIDEODRIVER', None)
                if pygame.display.get_init(): pygame.display.quit()
                pygame.display.init()
                try:
                    screen = pygame.display.set_mode((SCREEN_WIDTH, SCREEN_HEIGHT))
                    pygame.display.set_caption("Spectrometer Menu")
                    logger.info("Initialized standard Pygame display window as fallback.")
                except Exception as fallback_e:
                    logger.critical(f"FATAL: Failed to initialize fallback Pygame display: {fallback_e}", exc_info=True)
                    raise RuntimeError("Display initialization failed") from fallback_e

        if not display_hat_operational:
             if screen is None and pygame.display.get_init():
                try:
                    screen = pygame.display.set_mode((SCREEN_WIDTH, SCREEN_HEIGHT))
                    pygame.display.set_caption("Spectrometer Menu")
                    logger.info("Initialized standard Pygame display window.")
                except Exception as std_e:
                     logger.critical(f"FATAL: Failed to initialize standard Pygame display: {std_e}", exc_info=True)
                     raise RuntimeError("Display initialization failed") from std_e

        assert screen is not None, "Failed to create Pygame screen surface"

        # --- Initialize Core Components ---
        logger.info("Initializing core components...")
        network_info = NetworkInfo()
        # <<< Pass the initialized display_hat object (or None) to ButtonHandler
        button_handler = ButtonHandler(display_hat if display_hat_operational else None)

        menu_system = MenuSystem(screen, button_handler, network_info)
        if display_hat_operational and display_hat:
            menu_system.display_hat = display_hat # Pass to MenuSystem for drawing

        # --- Setup Signal Handling & Start Background Tasks ---
        setup_signal_handlers(button_handler, network_info)
        network_info.start_updates()

        # --- Main Loop ---
        logger.info("Starting main application loop...")
        while not g_shutdown_flag.is_set():
            menu_action = menu_system.handle_input()

            if menu_action == "QUIT":
                logger.info("QUIT action received from menu system.")
                g_shutdown_flag.set()
                continue
            elif menu_action == "CAPTURE":
                 show_capture_placeholder(screen, display_hat if display_hat_operational else None)
                 continue

            menu_system.draw()
            main_clock.tick(1.0 / MAIN_LOOP_DELAY_S)

    except Exception as e:
        logger.critical(f"FATAL ERROR in main function: {e}", exc_info=True)
        g_shutdown_flag.set()

    finally:
        # --- Cleanup Resources ---
        logger.warning("Initiating final cleanup...")
        if network_info: network_info.stop_updates()
        if menu_system: menu_system.cleanup()
        # Display HAT cleanup might be handled by library or GPIO cleanup
        if button_handler: button_handler.cleanup() # Cleanup manual GPIO
        if pygame.get_init():
             logger.info("Quitting Pygame...")
             pygame.quit()
             logger.info("Pygame quit.")
        logger.info("============================================")
        logger.info("   Application Finished.")
        logger.info("============================================")


if __name__ == "__main__":
    # Ensure iwgetid is installed before running
    # sudo apt update && sudo apt install wireless-tools
    main()
