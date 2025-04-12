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
# Integration Time (ms)
DEFAULT_INTEGRATION_TIME_MS = 1000
MIN_INTEGRATION_TIME_MS = 100
MAX_INTEGRATION_TIME_MS = 6000 # Increased max based on spectrometer
INTEGRATION_TIME_STEP_MS = 100

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

# Menu Layout
FONT_SIZE = 18
TITLE_FONT_SIZE = 20
HINT_FONT_SIZE = 14
DISCLAIMER_FONT_SIZE = 14
MENU_SPACING = 26
MENU_MARGIN_TOP = 40
MENU_MARGIN_LEFT = 12

# --- Font Filenames 
TITLE_FONT_FILENAME = 'ChakraPetch-Medium.ttf'
MAIN_FONT_FILENAME = 'Roboto-Regular.ttf'
HINT_FONT_FILENAME = 'Roboto-Regular.ttf'

# Timing
DEBOUNCE_DELAY_S = 0.2  # Debounce time for buttons
NETWORK_UPDATE_INTERVAL_S = 10.0 # How often to check network status
MAIN_LOOP_DELAY_S = 0.03 # Target ~30 FPS
SPLASH_DURATION_S = 6.0 #

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
                RPi_GPIO_lib.setmode(GPIO.BCM)
                RPi_GPIO_lib.setwarnings(False) # Suppress channel already in use warnings if needed
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
                    # Assertion: Ensure pins are distinct (basic check)
                    assert len(hall_pins) == len(set(hall_pins.keys())), "Duplicate Hall Effect pin definitions"

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
            logger.warning(f"Display HAT callback received for unmapped pin: {pin}")
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
            logger.warning(f"Manual GPIO callback received for unmapped channel: {channel}")
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
                for pin in self._manual_gpio_pins_used:
                     # Assertion: Check pin is int
                     assert isinstance(pin, int), f"Invalid pin type during cleanup: {type(pin)}"
                     RPi_GPIO_lib.remove_event_detect(pin) # Remove detection first
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
             # Check return value
            if result.returncode == 0 and result.stdout and result.stdout.strip():
                # Return the first IP address if multiple are listed
                ip_list = result.stdout.strip().split()
                if ip_list: return ip_list[0]
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
        # Loop bound by external flag
        logger.info("Network update loop started.")
        while not g_shutdown_flag.is_set():
            start_time = time.monotonic()
            new_wifi = "Error"
            new_ip = "Error"
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
                 # Ensure state reflects error if update fails mid-way
                 with self._lock:
                      self._wifi_name = new_wifi # Keep potentially partial update
                      self._ip_address = new_ip
                 # Continue loop despite error

            # Calculate remaining time and wait
            elapsed_time = time.monotonic() - start_time
            wait_time = max(0, NETWORK_UPDATE_INTERVAL_S - elapsed_time)
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
        assert screen is not None, "Pygame screen object is required"
        assert button_handler is not None, "ButtonHandler object is required"
        assert network_info is not None, "NetworkInfo object is required"

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
             self._calculate_value_offset() # Calculate alignment if fonts loaded

    def _load_fonts(self):
        """Loads fonts from the assets folder. Uses global constants for filenames."""
        try:
            # Check Pygame font module initialization
            if not pygame.font.get_init():
                logger.info("Initializing Pygame font module.")
                pygame.font.init()

            logger.info("Loading fonts from assets folder...")

            # --- Get the absolute path to the script's directory ---
            script_dir = os.path.dirname(os.path.abspath(__file__))
            assets_dir = os.path.join(script_dir, 'assets')
            # Assertion: Check assets dir exists? Maybe too strict, handle missing files below.
            # assert os.path.isdir(assets_dir), f"Assets directory not found: {assets_dir}"

            # --- Define paths using centralized constants --- <<< MODIFIED
            title_font_path = os.path.join(assets_dir, TITLE_FONT_FILENAME)
            main_font_path = os.path.join(assets_dir, MAIN_FONT_FILENAME)
            hint_font_path = os.path.join(assets_dir, HINT_FONT_FILENAME)

            # --- Load fonts with error handling ---
            try:
                self.title_font = pygame.font.Font(title_font_path, TITLE_FONT_SIZE)
                logger.info(f"Loaded title font: {title_font_path}")
            except pygame.error as e: # Catch specific Pygame errors
                logger.error(f"Failed to load title font '{title_font_path}' using Pygame: {e}. Using fallback.")
                self.title_font = pygame.font.SysFont(None, TITLE_FONT_SIZE) # Fallback
            except FileNotFoundError:
                logger.error(f"Title font file not found: '{title_font_path}'. Using fallback.")
                self.title_font = pygame.font.SysFont(None, TITLE_FONT_SIZE)
            except Exception as e: # Catch other potential errors
                logger.error(f"Unexpected error loading title font '{title_font_path}': {e}. Using fallback.", exc_info=True)
                self.title_font = pygame.font.SysFont(None, TITLE_FONT_SIZE)

            # Repeat for main font
            try:
                self.font = pygame.font.Font(main_font_path, FONT_SIZE)
                logger.info(f"Loaded main font: {main_font_path}")
            except pygame.error as e:
                logger.error(f"Failed to load main font '{main_font_path}' using Pygame: {e}. Using fallback.")
                self.font = pygame.font.SysFont(None, FONT_SIZE)
            except FileNotFoundError:
                logger.error(f"Main font file not found: '{main_font_path}'. Using fallback.")
                self.font = pygame.font.SysFont(None, FONT_SIZE)
            except Exception as e:
                logger.error(f"Unexpected error loading main font '{main_font_path}': {e}. Using fallback.", exc_info=True)
                self.font = pygame.font.SysFont(None, FONT_SIZE)

            # Repeat for hint font
            try:
                self.hint_font = pygame.font.Font(hint_font_path, HINT_FONT_SIZE)
                logger.info(f"Loaded hint font: {hint_font_path}")
            except pygame.error as e:
                logger.error(f"Failed to load hint font '{hint_font_path}' using Pygame: {e}. Using fallback.")
                self.hint_font = pygame.font.SysFont(None, HINT_FONT_SIZE)
            except FileNotFoundError:
                logger.error(f"Hint font file not found: '{hint_font_path}'. Using fallback.")
                self.hint_font = pygame.font.SysFont(None, HINT_FONT_SIZE)
            except Exception as e:
                logger.error(f"Unexpected error loading hint font '{hint_font_path}': {e}. Using fallback.", exc_info=True)
                self.hint_font = pygame.font.SysFont(None, HINT_FONT_SIZE)

            # Final check if *essential* fonts are usable
            if not self.font: # Main font is essential for menu items
                 logger.critical("Essential main font failed to load, even with fallbacks. Cannot continue reliably.")
                 # Decide if this is critical: raise RuntimeError("Essential fonts failed to load")
                 # For now, log critical and proceed, drawing might fail later.

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
            # Loop has fixed upper bound based on menu items
            for item_text, _ in self._menu_items:
                 prefix = label_prefixes.get(item_text)
                 if prefix: # Only consider items with a defined prefix
                      # Calculate width using the loaded font
                      label_width = self.font.size(prefix)[0]
                      max_label_width = max(max_label_width, label_width)

            # Add a small gap after the longest label for visual separation
            label_gap = 8 # Adjusted gap
            self._value_start_offset_x = max_label_width + label_gap
            logger.info(f"Calculated value start offset X: {self._value_start_offset_x} (based on max label width {max_label_width})")

        except Exception as e:
            logger.error(f"Failed to calculate value start offset: {e}. Using default fallback {self._value_start_offset_x}.")
            # Keep the default fallback value set in __init__

    # --- Helper to get the time to display/use ---
    def _get_current_app_display_time(self) -> datetime.datetime:
        """Calculates the current time including the user-defined offset."""
        # Use timezone-naive datetime objects for simplicity here
        # Be aware of potential issues if system time transitions DST while app is running
        # For simple offset, naive should be okay.
        # Assertion: Ensure offset is timedelta
        assert isinstance(self._time_offset, datetime.timedelta), "Time offset is not a timedelta object"
        try:
            return datetime.datetime.now() + self._time_offset
        except OverflowError:
            logger.warning("Time offset resulted in datetime overflow. Resetting offset.")
            self._time_offset = datetime.timedelta(0)
            return datetime.datetime.now()

    # --- Public Methods ---

    def handle_input(self) -> str | None:
        """
        Processes button inputs based on the current menu state (navigation or editing).
        Returns "QUIT" to signal application exit, "CAPTURE" to start capture, or None otherwise.
        """
        # 1. Process Pygame events first (catches window close, escape key)
        pygame_event_result = self.button_handler.process_pygame_events()
        if pygame_event_result == "QUIT":
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

        # 3. Process actions returned by input handlers
        if action == "EXIT_EDIT_SAVE":
            self._is_editing = False
            self._editing_field = None
            if self._datetime_being_edited is not None: # Only commit if a datetime was edited
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

    def get_timestamp_datetime(self) -> datetime.datetime:
        """Returns a datetime object representing the current app time (System + Offset)."""
        # Useful for getting the time to embed in filenames etc.
        return self._get_current_app_display_time()


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

        if self.button_handler.check_button(BTN_UP):
            self._navigate_menu(-1)
        elif self.button_handler.check_button(BTN_DOWN):
            self._navigate_menu(1)
        elif self.button_handler.check_button(BTN_ENTER):
            return self._select_menu_item() # This might start editing or trigger capture
        elif self.button_handler.check_button(BTN_BACK):
            # Optional: Implement Back action in main menu (e.g., go to a parent menu if exists, or quit?)
            logger.info("BACK pressed in main menu (no action defined).")
            # return "QUIT" # Example: Uncomment to make BACK exit the app from main menu
            pass
        return None # No external action required

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

        if item_text == self.MENU_ITEM_CAPTURE:
            if USE_SPECTROMETER:
                logger.info("Triggering spectrometer capture.")
                return "START_CAPTURE" # Signal action to main loop
            else:
                logger.warning("Capture Spectra selected, but USE_SPECTROMETER is False.")
                # Optionally show a brief message on screen?
                return None

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
            return None # Stay in menu, now in edit mode

        # --- Read-only items (WIFI, IP) ---
        elif item_text in [self.MENU_ITEM_WIFI, self.MENU_ITEM_IP]:
             logger.info(f"Selected read-only item: {item_text}")
             # Optionally: Could force a refresh of network info here?
             # Or display more details on a sub-screen?
             return None # No action on select for these items

        # --- Fallback ---
        else:
             logger.warning(f"Selected menu item '{item_text}' with unknown type/action: {edit_type}")
             return None


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
            self._integration_time_ms = max(MIN_INTEGRATION_TIME_MS, min(new_val, MAX_INTEGRATION_TIME_MS))
            logger.debug(f"Integration time adjusted to {self._integration_time_ms} ms")

        elif edit_type == self.EDIT_TYPE_DATE:
             # Assertion: Ensure datetime object exists for editing
             assert self._datetime_being_edited is not None, "Cannot adjust Date, _datetime_being_edited is None"
             self._change_date_field(delta) # Delegate to date change helper

        elif edit_type == self.EDIT_TYPE_TIME:
             # Assertion: Ensure datetime object exists for editing
             assert self._datetime_being_edited is not None, "Cannot adjust Time, _datetime_being_edited is None"
             self._change_time_field(delta) # Delegate to time change helper

        return None # Adjustment handled internally, stay in edit mode


    def _handle_edit_next_field(self, edit_type: int) -> str | None:
        """ Moves to the next editable field, or returns 'EXIT_EDIT_SAVE' if done. """
        # Assertion: Check state
        assert self._is_editing
        # Assertion: Check edit type validity
        assert edit_type in [self.EDIT_TYPE_INTEGRATION, self.EDIT_TYPE_DATE, self.EDIT_TYPE_TIME], f"Invalid edit type for next field: {edit_type}"

        if edit_type == self.EDIT_TYPE_INTEGRATION:
            logger.debug("Finished editing Integration Time.")
            return "EXIT_EDIT_SAVE" # No fields, Enter saves/exits

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
                return "EXIT_EDIT_SAVE" # Finished all date fields

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
                return "EXIT_EDIT_SAVE" # Finished all time fields

        return None # Stay in edit mode, moved to next field


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
                _, max_days = calendar.monthrange(year, month)
                day += delta
                 # Wrap day
                if day > max_days: day = 1
                elif day < 1: day = max_days
            except ValueError:
                # Handle cases like Feb 30th attempt during month change
                logger.warning(f"Invalid intermediate date ({year}-{month}) for day calculation. Clamping day.")
                # A simple approach is to clamp to 1 or max_days (e.g., 31),
                # but get_safe_datetime will do the final validation.
                # We might set day to 1 here if delta was positive, or a reasonable max if negative.
                # Let's rely on get_safe_datetime below to handle the final validation.
                # Calculate new day without wrapping first for get_safe_datetime
                day += delta
                # Clamp day crudely here just to avoid *huge* numbers?
                day = max(1, min(day, 31))


        # Attempt to create the new temporary datetime using the helper
        # This handles invalid combinations like Feb 30th gracefully (returns None)
        new_datetime = get_safe_datetime(year, month, day, hour, minute, second)

        # Check return value
        if new_datetime:
            self._datetime_being_edited = new_datetime # Update the temporary object
            logger.debug(f"Temporary Date being edited is now: {self._datetime_being_edited.strftime('%Y-%m-%d')}")
        else:
            # This case indicates the adjustment resulted in an invalid date (e.g., Feb 30).
            # The temporary date is *not* updated, effectively ignoring the invalid change.
            logger.warning(f"Date field change resulted in invalid date. Change ignored.")

    def _change_time_field(self, delta: int):
        """ Increments/decrements the current time field of the temporary _datetime_being_edited. """
         # Assertions: Check state and parameters
        assert self._datetime_being_edited is not None, "Cannot change time field, _datetime_being_edited is None"
        assert self._editing_field in [self.FIELD_HOUR, self.FIELD_MINUTE], f"Invalid time field '{self._editing_field}' for adjustment" # Removed SECOND
        assert delta in [-1, 1], f"Invalid delta value: {delta}"

        # Use timedelta for safer time manipulation, handles wrapping automatically
        if self._editing_field == self.FIELD_HOUR:
            time_delta = datetime.timedelta(hours=delta)
        elif self._editing_field == self.FIELD_MINUTE:
            time_delta = datetime.timedelta(minutes=delta)
        # Removed seconds
        # elif self._editing_field == self.FIELD_SECOND:
        #     time_delta = datetime.timedelta(seconds=delta)
        else:
             logger.error(f"Logic error: _change_time_field called with invalid field '{self._editing_field}'")
             return # Should not happen due to assertion

        logger.debug(f"Attempting to change temporary Time field '{self._editing_field}' by {delta} hours/mins")

        try:
            self._datetime_being_edited += time_delta
            logger.debug(f"Temporary Time being edited is now: {self._datetime_being_edited.strftime('%H:%M:%S')}") # Log with seconds for clarity
        except OverflowError:
             logger.warning(f"Time field change resulted in datetime overflow. Change ignored.")
             # Datetime object remains unchanged


    def _commit_time_offset_changes(self):
        """ Calculates and stores the new time offset based on the final edited datetime. """
        # Assertion: Check state
        assert self._datetime_being_edited is not None, "Commit called but no datetime was being edited."

        try:
            # Final desired absolute time from the editor
            final_edited_time = self._datetime_being_edited
            # Current system time (at the moment of commit)
            current_system_time = datetime.datetime.now()

            # Calculate the difference (Offset = TargetTime - SystemTime)
            new_offset = final_edited_time - current_system_time

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


    # --- Private Drawing Methods ---
    def _draw_title(self):
        """Draws the main title."""
        # Assertion: Should have valid font here (checked in draw)
        assert self.title_font, "Title font not loaded"
        try:
            title_text = self.title_font.render("OPEN SPECTRO MENU", True, YELLOW)
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

        # Loop has fixed upper bound
        for i, (item_text, edit_type) in enumerate(self._menu_items):
            try:
                is_selected = (i == self._current_selection_idx)
                is_being_edited = (is_selected and self._is_editing)

                # --- Determine which datetime object to use for formatting ---
                datetime_for_formatting = datetime_to_display_default
                if is_being_edited and edit_type in [self.EDIT_TYPE_DATE, self.EDIT_TYPE_TIME]:
                    # Assertion: Must have the temp object if editing Date/Time
                    assert self._datetime_being_edited is not None, "Editing Date/Time but _datetime_being_edited is None"
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


                # --- Determine Color ---
                color = WHITE
                # Special coloring for network status
                is_network_item = item_text in [self.MENU_ITEM_WIFI, self.MENU_ITEM_IP]
                is_connected = not ("Not Connected" in value_text or "Error" in value_text or "No IP" in value_text)

                if is_selected:
                    color = YELLOW # Highlight selected item
                elif is_network_item and not is_connected:
                    color = GRAY # Dim disconnected network info


                # --- Render and Blit Label (Aligned Left) ---
                label_surface = self.font.render(label_text, True, color)
                self.screen.blit(label_surface, (MENU_MARGIN_LEFT, y_position))

                # --- Render and Blit Value (Aligned at calculated offset) ---
                if value_text: # Only blit value if it exists
                    value_surface = self.font.render(value_text, True, color)
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


    def _draw_editing_highlight(self, y_pos: int, edit_type: int, label_str: str, value_str: str):
        """ Draws highlight rectangle around the specific field being edited. """
        # Assertion: Font must be loaded 
        assert self.font is not None, "Cannot draw highlight without main font."

        # Base X position where values start (includes margin + offset)
        value_start_x = MENU_MARGIN_LEFT + self._value_start_offset_x

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
                assert self._datetime_being_edited is not None and self._editing_field is not None
                # Use the currently edited datetime to format the value string reliably
                formatted_date = self._datetime_being_edited.strftime('%Y-%m-%d')
                if self._editing_field == self.FIELD_YEAR:   field_str, offset_str = formatted_date[0:4], ""
                elif self._editing_field == self.FIELD_MONTH: field_str, offset_str = formatted_date[5:7], formatted_date[0:5] # "YYYY-"
                elif self._editing_field == self.FIELD_DAY:   field_str, offset_str = formatted_date[8:10], formatted_date[0:8] # "YYYY-MM-"
                else: return # Should not happen due to assertions elsewhere
            elif edit_type == self.EDIT_TYPE_TIME:
                # Assertion: Must have datetime object and field 
                assert self._datetime_being_edited is not None and self._editing_field is not None
                # Use the currently edited datetime (HH:MM format)
                formatted_time = self._datetime_being_edited.strftime('%H:%M')
                if self._editing_field == self.FIELD_HOUR:   field_str, offset_str = formatted_time[0:2], ""
                elif self._editing_field == self.FIELD_MINUTE: field_str, offset_str = formatted_time[3:5], formatted_time[0:3] # "HH:"
                else: return # Should not happen
            else:
                 return # Not an editable type with fields

            # Calculate widths based *only* on the relevant text segments
            field_width = self.font.size(field_str)[0] if field_str else 0
            offset_within_value_width = self.font.size(offset_str)[0] if offset_str else 0

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

        except pygame.error as e:
             logger.error(f"Pygame error calculating highlight size: {e}")
             return # Abort drawing highlight if calculation fails
        except Exception as e:
             logger.error(f"Unexpected error calculating highlight: {e}", exc_info=True)
             return

        # Draw the rectangle if successfully calculated
        if highlight_rect:
            pygame.draw.rect(self.screen, RED, highlight_rect, 1) # 1px thick border


    def _draw_hints(self):
        """Draws contextual hints at the bottom."""
        # Assertion: Font must be loaded 
        assert self.hint_font is not None, "Hint font object is not available"
        hint_text = ""
        if self._is_editing:
            # Hints specific to editing mode
            hint_text = "UP/DN: Adjust | ENT: Next/Save | BCK: Cancel"
        else:
            # Hints for navigation mode
            hint_text = "UP/DN: Navigate | ENT: Select/Edit | BCK: Back" # Clarify Back action if any
        try:
            hint_surface = self.hint_font.render(hint_text, True, YELLOW)
            # Position hints at the bottom-left
            hint_rect = hint_surface.get_rect(left=MENU_MARGIN_LEFT, bottom=SCREEN_HEIGHT - 10)
            self.screen.blit(hint_surface, hint_rect)
        except pygame.error as e:
             logger.error(f"Pygame error rendering hints: {e}")
        except Exception as e:
             logger.error(f"Unexpected error rendering hints: {e}", exc_info=True)

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

        # Check file existence before loading 
        if not os.path.isfile(image_path):
             logger.error(f"Splash screen image not found at: {image_path}")
             time.sleep(min(duration_s, 2.0)) # Wait a short time even if image missing
             return # Skip rest of splash if image missing

        # Load the image
        splash_image_raw = pygame.image.load(image_path)
        logger.info(f"Loaded splash screen image: {image_path}")

        # --- CONDITIONAL CONVERT ---
        # Only convert if NOT using the dummy driver (i.e., if a real video mode is set)
        is_dummy_driver = os.environ.get('SDL_VIDEODRIVER') == 'dummy'

        if not is_dummy_driver and pygame.display.get_init() and pygame.display.get_surface():
            # We have a real display mode, attempt conversion for performance
            try:
                logger.debug("Attempting splash image conversion for standard display.")
                splash_image_final = splash_image_raw.convert()
                # If using alpha transparency: splash_image_final = splash_image_raw.convert_alpha()
            except pygame.error as convert_error:
                logger.warning(f"pygame.Surface.convert() failed even for standard display: {convert_error}. Using raw surface.")
                splash_image_final = splash_image_raw # Use raw as fallback
        else:
            # Using dummy driver OR no display mode set, use the raw loaded surface
            logger.debug("Skipping splash image conversion (using dummy driver or no video mode).")
            splash_image_final = splash_image_raw # Use the raw loaded image directly
        # --- END CONDITIONAL CONVERT ---

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

            # Center the splash image on the screen
            splash_rect.center = screen_rect.center

            # Draw the image
            screen.blit(splash_image_final, splash_rect) # Blit the final surface

            # Update the physical display using the helper
            update_hardware_display(screen, display_hat_obj)

            # Wait for the specified duration (respecting shutdown flag)
            wait_interval = 0.1 # Check flag every 100ms
            num_intervals = int(duration_s / wait_interval)
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
        # disclaimer_font: Pygame Font object for the main text.  # <<< Removed docstring
        hint_font: Pygame Font object for the hint text.        # <<< Kept docstring
    """
    # Assertions for parameters
    assert screen is not None, "Screen surface required for disclaimer"
    assert button_handler is not None, "ButtonHandler required for disclaimer acknowledgement"
    assert hint_font is not None, "Hint font object is required"             

    logger.info("Displaying disclaimer screen...")

    # --- Load Disclaimer Font Locally --- <<< NEW SECTION
    disclaimer_font = None
    try:
        if not pygame.font.get_init(): pygame.font.init() # Ensure font module is ready

        script_dir = os.path.dirname(os.path.abspath(__file__))
        assets_dir = os.path.join(script_dir, 'assets')
        # Use the same main font file, but with the specific disclaimer size
        font_path = os.path.join(assets_dir, MAIN_FONT_FILENAME)

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

    except Exception as e:
        logger.error(f"Error during disclaimer font loading setup: {e}", exc_info=True)
        # Attempt SysFont as last resort if setup failed
        try:
            disclaimer_font = pygame.font.SysFont(None, DISCLAIMER_FONT_SIZE)
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
        # ... (rest of the text preparation logic remains the same) ...
        lines = DISCLAIMER_TEXT.splitlines()
        rendered_lines = []
        max_width = 0
        total_height = 0
        line_spacing = 4 # Vertical space between lines

        assert isinstance(lines, list), "Disclaimer text did not split into lines correctly"
        for line in lines:
            if line.strip():
                # Use the *locally loaded* disclaimer_font
                line_surface = disclaimer_font.render(line, True, WHITE) # <<< Use local font
                rendered_lines.append(line_surface)
                max_width = max(max_width, line_surface.get_width())
                total_height += line_surface.get_height() + line_spacing
            else:
                rendered_lines.append(None)
                # Use local font height for spacing calculation
                total_height += (disclaimer_font.get_height() // 2) + line_spacing # <<< Use local font

        if total_height > 0: total_height -= line_spacing

        # Prepare hint text
        hint_text = "Press Enter or Back to continue..."
        # Use the *passed-in* hint_font
        hint_surface = hint_font.render(hint_text, True, YELLOW) # <<< Use passed hint_font
        hint_height = hint_surface.get_height()
        total_height += hint_height + 10 # Add space for hint + padding

        # Calculate starting Y position
        start_y = max(10, (screen.get_height() - total_height) // 2)

        # --- Draw Static Content ---
        screen.fill(BLACK)
        current_y = start_y
        for surface in rendered_lines:
            if surface: # Rendered line
                line_rect = surface.get_rect(centerx=screen.get_width() // 2, top=current_y)
                screen.blit(surface, line_rect)
                current_y += surface.get_height() + line_spacing
            else: # Blank line spacing
                 # Use local font height
                current_y += (disclaimer_font.get_height() // 2) + line_spacing # <<< Use local font

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
    # ... (Acknowledgement loop remains the same) ...
    logger.info("Waiting for user acknowledgement on disclaimer screen...")
    acknowledged = False
    while not acknowledged and not g_shutdown_flag.is_set():
        quit_signal = button_handler.process_pygame_events()
        if quit_signal == "QUIT":
             logger.warning("QUIT signal received during disclaimer.")
             g_shutdown_flag.set()
             continue
        if button_handler.check_button(BTN_ENTER) or button_handler.check_button(BTN_BACK):
            acknowledged = True
            logger.info("Disclaimer acknowledged by user via button.")
        pygame.time.wait(50)

    if not acknowledged:
         logger.warning("Exited disclaimer wait due to shutdown signal.")
    else:
         logger.info("Disclaimer screen finished.")


# --- Spectrometer Placeholder Screen ---
def show_capture_placeholder(screen: pygame.Surface, display_hat_obj):
     """Displays a placeholder message for the capture screen."""
     # Assertions for parameters
     assert screen is not None, "Screen surface required for placeholder"
     logger.info("Displaying Capture Spectra placeholder screen.")
     placeholder_font = None
     try:
          # Use a slightly larger system font for the placeholder
          placeholder_font = pygame.font.SysFont(None, 30)
     except Exception as e:
          logger.error(f"Failed to load font for placeholder: {e}")
          # Fallback: try to continue without text? Or just return?
          return

     if placeholder_font:
         try:
             text_surface = placeholder_font.render("Capture Mode (TBD)", True, WHITE)
             text_rect = text_surface.get_rect(center=screen.get_rect().center)

             screen.fill(BLUE) # Use a different background color?
             screen.blit(text_surface, text_rect)

             # Use the standard display update helper
             update_hardware_display(screen, display_hat_obj)
             logger.debug("Placeholder screen drawn.")

         except pygame.error as e:
              logger.error(f"Pygame error rendering capture placeholder: {e}")
         except Exception as e:
              logger.error(f"Unexpected error rendering capture placeholder: {e}", exc_info=True)
     else:
          # If font failed, maybe just show blank screen?
          screen.fill(BLUE)
          update_hardware_display(screen, display_hat_obj)


     # Wait a moment (respecting shutdown flag) then allow return
     wait_interval = 0.1
     num_intervals = int(2.0 / wait_interval) # Wait approx 2 seconds
     # Loop bound by fixed count
     for _ in range(num_intervals):
         if g_shutdown_flag.is_set():
              logger.info("Shutdown requested during placeholder display.")
              break
         # Check for button presses to allow early exit from placeholder?
         # button_handler.process_pygame_events() # Process events to catch QUIT
         # if button_handler.check_button(BTN_BACK): break # Allow BACK to exit early
         time.sleep(wait_interval)

     logger.info("Returning from placeholder screen.")

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
    main_clock = None

    try:
        # --- Initialize Pygame and Display FIRST ---
        logger.info("Initializing Pygame and display...")
        try:
             pygame.init()
             # Initialize clock here too
             main_clock = pygame.time.Clock()
        except pygame.error as e:
             logger.critical(f"FATAL: Pygame initialization failed: {e}", exc_info=True)
             raise RuntimeError("Pygame init failed") from e

        if USE_DISPLAY_HAT and DisplayHATMini_lib:
            try:
                # Setup dummy video driver for HAT mode BEFORE display init/surface creation
                os.environ['SDL_VIDEODRIVER'] = 'dummy'
                pygame.display.init() # Need display module init even for dummy driver
                screen = pygame.Surface((SCREEN_WIDTH, SCREEN_HEIGHT)) # Create buffer surface
                # Attempt to create DisplayHATMini instance
                display_hat = DisplayHATMini_lib(screen) # Pass buffer if needed by constructor, else None
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
                # Screen creation attempt moved below
        else:
            # Not using HAT or library failed to import, setup standard window
            logger.info("Configured for standard Pygame window (Display HAT disabled or unavailable).")
            # No dummy driver needed
            if not pygame.display.get_init(): pygame.display.init() # Ensure display is initialized

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
        # Assign display_hat to menu_system *after* menu_system is created if active
        if display_hat_active:
            menu_system.display_hat = display_hat

        # Assertion: Ensure essential components initialized 
        assert network_info is not None, "NetworkInfo failed to initialize"
        assert button_handler is not None, "ButtonHandler failed to initialize"
        assert menu_system is not None, "MenuSystem failed to initialize"
        assert menu_system.font is not None, "MenuSystem failed to load essential font"

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
        # Loop bound by external flag
        while not g_shutdown_flag.is_set():
            # 1. Handle Inputs -> Get Action
            menu_action = menu_system.handle_input()

            # 2. Process Actions
            if menu_action == "QUIT":
                logger.info("QUIT action received from menu system. Signaling shutdown.")
                g_shutdown_flag.set()
                continue # Skip drawing, exit loop on next check
            elif menu_action == "CAPTURE":
                 # Show the placeholder screen (or future real capture screen)
                 show_capture_placeholder(screen, display_hat if display_hat_active else None)
                 # After placeholder returns, loop continues back to menu drawing
                 continue

            # 3. Draw Current State (Menu)
            menu_system.draw() # Draws the menu screen updates display

            # 4. Tick Clock (Control Framerate/CPU Usage)
            # Assertion: Clock must be initialized
            assert main_clock is not None, "Main clock not initialized"
            main_clock.tick(1.0 / MAIN_LOOP_DELAY_S) # Target FPS

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

        # Cleanup application logic (if needed)
        if menu_system:
            logger.debug("Cleaning up menu system...")
            try: menu_system.cleanup()
            except Exception as e: logger.error(f"Error cleaning up menu system: {e}")

        # Cleanup hardware interfaces (GPIO)
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
