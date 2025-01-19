#!/usr/bin/env python3
"""
SpectrometerSystem
------------------
This script manages a spectrometer and camera system with an LCD display and
button inputs. It cycles through three states:
  1) IDLE (STATE_1)       - Allows you to view WiFi info, date/time, or capture spectra.
  2) SPECTRA (STATE_2)    - Captures a spectrum, plots it, and optionally saves data.
  3) CAMERA (STATE_3)     - Allows capturing and saving a photo.

Dependencies:
  - seabreeze
  - matplotlib
  - picamera2
  - libcamera
  - PIL (Pillow)
  - OpenCV (cv2)
  - ST7789 (LCD display library)

Usage:
  python3 spectrometer_system.py

Notes:
  - Ensure you have the correct hardware connections for the LCD (ST7789).
  - The camera must be connected and libcamera must be installed.
  - Seabreeze must be installed for spectrometer operations.
"""

import logging
import sys
import os
import io
import time
import subprocess
import csv
from datetime import datetime

# Seabreeze libraries for spectrometers
import seabreeze
seabreeze.use('pyseabreeze')
import seabreeze.spectrometers as sb

# Plotting and image manipulation libraries
import matplotlib.pyplot as plt
from PIL import Image, ImageDraw, ImageFont

# Camera libraries
import cv2
from picamera2 import Picamera2, MappedArray
import libcamera

# Local LCD display library
sys.path.append('./lcd')
import ST7789

class SpectrometerSystem:
    """
    A class to manage the spectrometer, camera, and LCD display.

    Attributes
    ----------
    current_state : str
        Tracks the current state of the system among STATE_1, STATE_2, STATE_3.
    spectrometer : sb.Spectrometer or None
        Reference to the Ocean Optics spectrometer object.
    camera : Picamera2 or None
        Reference to the Raspberry Pi camera object.
    spectrum_data : tuple or None
        Holds captured wavelength (x) and intensity (y) arrays after a capture.
    current_image : numpy.ndarray or None
        Stores the last captured camera image.
    current_filename : str or None
        Used to store a filename base for saving data and images.
    disp : ST7789.ST7789
        Object controlling the LCD display.
    logger : logging.Logger
        Logger instance for system messages.

    States
    ------
    STATE_1 = "IDLE"
    STATE_2 = "SPECTRA"
    STATE_3 = "CAMERA"
    """

    # Define system states for readability
    STATE_1 = "IDLE"      # Idle: show WiFi, date/time, capture new spectra
    STATE_2 = "SPECTRA"   # Spectra: show last capture, optionally save or discard
    STATE_3 = "CAMERA"    # Camera: preview or save camera images

    def __init__(self):
        """
        Initialize the SpectrometerSystem.

        - Sets up logging.
        - Initializes the LCD display and clears it.
        - Configures hardware pins for buttons.
        - Sets initial system state and placeholders for data.
        - Prepares the camera configuration.
        """
        # Set up logging
        logging.basicConfig(level=logging.INFO)
        self.logger = logging.getLogger(__name__)

        # Initialize display
        self.disp = ST7789.ST7789()
        self.disp.Init()
        self.disp.clear()
        self.disp.bl_DutyCycle(0)

        # Key pins on the display for button inputs
        self.KEY1_PIN = self.disp.GPIO_KEY1_PIN
        self.KEY2_PIN = self.disp.GPIO_KEY2_PIN
        self.KEY3_PIN = self.disp.GPIO_KEY3_PIN

        # Spectrometer integration time (microseconds)
        self.INTEGRATION_TIME_MICROS = 5000000  # 0.5 second

        # Font settings for on-screen text
        self.FONT_SIZE = 16
        self.FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

        # State variables
        self.current_state = self.STATE_1
        self.spectrometer = None
        self.camera = None
        self.spectrum_data = None
        self.current_image = None
        self.current_filename = None

        # Set up camera
        self._setup_camera()

    def _setup_camera(self):
        """
        Configure and set up the Raspberry Pi camera.

        - Sets up a still configuration with a flip transform.
        - Attaches a callback to apply a timestamp overlay to the preview stream.
        """
        try:
            self.camera = Picamera2()

            # Create a still configuration at full resolution
            still_config = self.camera.create_still_configuration(
                main={"size": (1456, 1088)},  # Full resolution for saving
                transform=libcamera.Transform(hflip=1, vflip=1)  # Adjust orientation
            )

            # Configure camera
            self.camera.configure(still_config)

            # Optional: set up timestamp overlay
            self.camera.pre_callback = self._apply_timestamp

            self.logger.info("Camera setup completed successfully")
        except Exception as e:
            self.logger.error(f"Camera setup failed: {str(e)}")
            self.camera = None

    def _apply_timestamp(self, request):
        """
        Callback to apply a timestamp overlay to the camera preview.

        Parameters
        ----------
        request : libcamera.Request
            Contains the image data to be modified.
        """
        timestamp = time.strftime("%Y-%m-%d %X")
        with MappedArray(request, "main") as m:
            cv2.putText(
                m.array,
                timestamp,
                (10, 30),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.6,
                (0, 255, 0),
                1
            )

    def show_message(self, message_lines, duration=6):
        """
        Display a list of message lines on the LCD for a set duration.

        Parameters
        ----------
        message_lines : list of str
            Text lines to display on the screen.
        duration : int, optional
            Seconds to display the message before clearing, by default 6.
        """
        self.disp.bl_DutyCycle(50)
        image = Image.new("RGB", (self.disp.width, self.disp.height), "WHITE")
        draw = ImageDraw.Draw(image)
        font = ImageFont.truetype(self.FONT_PATH, self.FONT_SIZE)

        # Print each line with a small vertical offset
        y_offset = 20
        for line in message_lines:
            draw.text((10, y_offset), line, font=font, fill="BLACK")
            y_offset += self.FONT_SIZE + 5

        self.disp.ShowImage(image)
        time.sleep(duration)
        self.disp.clear()
        self.disp.bl_DutyCycle(0)

    def get_wifi_info(self):
        """
        Retrieve WiFi and SSH information.

        Returns
        -------
        tuple
            (ssid, ip_addr, ssh_password)
        """
        ssid = subprocess.getoutput("iwgetid -r").strip() or "not connected"
        ip_addr = subprocess.getoutput("hostname -I").strip() or "not connected"
        ssh_password = "spectro" if ssid != "not connected" else "not connected"
        return ssid, ip_addr, ssh_password

    def get_datetime_info(self):
        """
        Get current date, time, and time zone information dynamically.

        Returns
        -------
        tuple
            (date_str, time_str, tz_str_line1, tz_str_line2)
        """
        # Current date and time
        date_str = time.strftime("%d %b %Y")
        time_str = time.strftime("%H:%M:%S")
        
        # Time zone details
        tz_name = time.tzname[0]  # Local timezone name
        tz_offset_hours = -time.timezone // 3600  # Offset in hours
        tz_offset = f"{'+' if tz_offset_hours >= 0 else ''}{tz_offset_hours:02d}00"  # Format +hhmm

        # Full time zone string
        tz_str = f"{tz_name} (UTC{tz_offset})"

        # Split into two lines if necessary
        if len(tz_str) > 20:  # Assuming 20 characters fit on one line
            tz_str_line1 = tz_str[:20]
            tz_str_line2 = tz_str[20:]
        else:
            tz_str_line1 = tz_str
            tz_str_line2 = ""

        return date_str, time_str, tz_str_line1, tz_str_line2

    def capture_spectrum(self):
        """
        Capture the spectrum data from the attached spectrometer.

        Returns
        -------
        tuple
            (wavelengths, intensities)
        """
        if not self.spectrometer:
            # Create spectrometer object from first discovered device
            self.spectrometer = sb.Spectrometer.from_serial_number()

        # Set integration time
        self.spectrometer.integration_time_micros(self.INTEGRATION_TIME_MICROS)

        # Get wavelength and intensity arrays
        wavelengths = self.spectrometer.wavelengths()
        intensities = self.spectrometer.intensities(
            correct_dark_counts=True,
            correct_nonlinearity=True
        )
        return wavelengths, intensities

    def plot_spectrum(self, x, y):
        """
        Generate a small spectrum plot and return it as a PIL Image.

        Parameters
        ----------
        x : array-like
            Wavelengths in nm.
        y : array-like
            Intensities.

        Returns
        -------
        PIL.Image
            A 240x240 image of the spectrum plot.
        """
        fig, ax = plt.subplots(figsize=(240/96, 240/96), dpi=96)
        ax.plot(x, y)
        ax.set_title(f"Integration: {self.INTEGRATION_TIME_MICROS}µsec", fontsize=8)
        ax.grid(True, linestyle="--", alpha=0.6)

        # Make the plot readable on the small screen
        ax.set_xlim(min(x), max(x))
        xticks = [int(t) for t in ax.get_xticks() if min(x) <= t <= max(x)]
        yticks = [int(t) for t in ax.get_yticks()[:-1]]
        ax.set_xticks(xticks)
        ax.set_yticks(yticks)
        ax.set_xticklabels([str(t) for t in xticks], fontsize=8)
        ax.set_yticklabels([str(t) for t in yticks], fontsize=8)

        fig.tight_layout()
        plt.savefig("/tmp/spectrum.png", dpi=96)
        plt.close(fig)

        # Resize to the 240x240 LCD resolution
        return Image.open("/tmp/spectrum.png").resize((240, 240))

    def save_data(self, x, y, image=None):
        """
        Save the spectrum data (CSV and PNG plot) and optionally a camera image.

        Parameters
        ----------
        x : array-like
            Wavelength data.
        y : array-like
            Intensity data.
        image : numpy.ndarray, optional
            If provided, a full-resolution camera image to save.

        Returns
        -------
        str
            The base filename used for the saved files.
        """
        timestamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
        base_filename = f"spectrum_{timestamp}"

        # Save CSV of the spectrum data
        with open(f"{base_filename}.csv", 'w', newline='') as csvfile:
            csvwriter = csv.writer(csvfile)
            csvwriter.writerow(["Wavelengths"] + list(x))
            csvwriter.writerow(["Intensities"] + list(y))
            csvwriter.writerow(["Timestamp", timestamp])
            csvwriter.writerow(["Integration Time", self.INTEGRATION_TIME_MICROS])

        # Save a higher-resolution spectrum plot
        plt.figure()
        plt.plot(x, y)
        plt.title(f"Spectra - Integration: {self.INTEGRATION_TIME_MICROS}µsec")
        plt.xlabel("Wavelength (nm)")
        plt.ylabel("Intensity")
        plt.grid(True, linestyle="--", alpha=0.6)
        plt.savefig(f"{base_filename}.png", dpi=300)
        plt.close()

        # Optionally save the photo
        if image is not None:
            cv2.imwrite(f"{base_filename}_photo.jpg", image)

        return base_filename

    def handle_state_1(self):
        """
        Handle the IDLE state.

        - KEY1: Capture spectra -> move to SPECTRA state.
        - KEY2: Show WiFi info.
        - KEY3: Show date/time info.
        """
        # If KEY1 is pressed, transition to capturing spectra
        if self.disp.digital_read(self.KEY1_PIN) == 1:
            self.show_message([
                "Starting ST-VIS-25",
                "Button 1: Capture",
                "Button 2: Save",
                "Button 3: Discard"
            ], duration=3)
            self.current_state = self.STATE_2
            # Wait until button is released to avoid accidental repeated triggers
            while self.disp.digital_read(self.KEY1_PIN) == 1:
                time.sleep(0.1)

        # If KEY2 is pressed, show WiFi info
        elif self.disp.digital_read(self.KEY2_PIN) == 1:
            ssid, ip_addr, password = self.get_wifi_info()
            self.show_message([
                f"WiFi: {ssid}",
                f"IP: {ip_addr}",
                f"ssh pwd: {password}"
            ])
            while self.disp.digital_read(self.KEY2_PIN) == 1:
                time.sleep(0.1)

        # If KEY3 is pressed, show date/time info
        elif self.disp.digital_read(self.KEY3_PIN) == 1:
            date_str, time_str, tz_str_line1, tz_str_line2 = self.get_datetime_info()
            self.show_message([
                f"Date: {date_str}",
                f"Time: {time_str}",
                f"TZ: {tz_str_line1}",
                tz_str_line2  # Empty string if no overflow
            ])
            while self.disp.digital_read(self.KEY3_PIN) == 1:
                time.sleep(0.1)

    def handle_state_2(self):
        """
        Handle SPECTRA state with live-updating spectra.

        In live mode (live_mode = True):
        - Continuously capture a new spectrum and update the plot.
        - If Button 1 is pressed, freeze (capture) the current spectrum (live_mode = False).
        - If Button 3 is pressed in live mode, return to IDLE (discarding everything).

        In frozen mode (live_mode = False):
        - Display the frozen spectrum.
        - If Button 2 is pressed, save data and transition to CAMERA (STATE_3).
        - If Button 3 is pressed, discard and go back to live mode.
        """

        # Create the figure and line object once if not done yet
        if not hasattr(self, 'fig'):
            self.fig, self.ax = plt.subplots(figsize=(240/96, 240/96), dpi=96)
            (self.line,) = self.ax.plot([], [], linewidth=1)
            self.ax.set_title("Live Spectrum", fontsize=8)
            self.ax.grid(True, linestyle="--", alpha=0.6)

        # Ensure we have a live_mode attribute
        if not hasattr(self, 'live_mode'):
            self.live_mode = True  # default to live streaming

        # LIVE MODE
        if self.live_mode:
            # 1. Continuously show live spectra until Button 1 or Button 3 is pressed
            x, y = self.capture_spectrum()  # One capture per call
            # Update line data
            self.line.set_xdata(x)
            self.line.set_ydata(y)
            self.ax.relim()
            self.ax.autoscale_view()

            # Render to in-memory buffer
            buf = io.BytesIO()
            self.fig.savefig(buf, format='png', dpi=96)
            buf.seek(0)

            # Convert buffer to PIL Image
            img_pil = Image.open(buf).resize((240, 240))
            buf.close()

            # Display
            self.disp.bl_DutyCycle(50)
            self.disp.ShowImage(img_pil)

            # Check for Button 1 -> freeze
            if self.disp.digital_read(self.KEY1_PIN) == 1:
                # Freeze the current data
                self.logger.info("Freezing spectrum...")
                self.spectrum_data = (x, y) 
                self.live_mode = False
                while self.disp.digital_read(self.KEY1_PIN) == 1:
                    time.sleep(0.1)
                return  # Wait for next loop iteration

            # Check for Button 3 -> return to IDLE if in live mode
            elif self.disp.digital_read(self.KEY3_PIN) == 1:
                self.logger.info("Returning to IDLE from live mode...")
                self.spectrum_data = None
                self.live_mode = True
                self.current_state = self.STATE_1
                self.disp.clear()
                self.disp.bl_DutyCycle(0)
                while self.disp.digital_read(self.KEY3_PIN) == 1:
                    time.sleep(0.1)
                return

        # FROZEN MODE
        else:
            # We already have self.spectrum_data from the freeze
            if self.spectrum_data:
                x, y = self.spectrum_data
                # Re-plot the frozen data
                self.line.set_xdata(x)
                self.line.set_ydata(y)
                self.ax.relim()
                self.ax.autoscale_view()

                buf = io.BytesIO()
                self.fig.savefig(buf, format='png', dpi=96)
                buf.seek(0)

                img_pil = Image.open(buf).resize((240, 240))
                buf.close()

                self.disp.bl_DutyCycle(50)
                self.disp.ShowImage(img_pil)

                # Button 2 -> Save data and go to CAMERA (State 3)
                if self.disp.digital_read(self.KEY2_PIN) == 1:
                    self.logger.info("Saving captured spectrum and moving to CAMERA...")
                    self.current_filename = self.save_data(x, y)
                    self.logger.info(f"Spectrum saved: {self.current_filename}")
                    self.current_state = self.STATE_3
                    while self.disp.digital_read(self.KEY2_PIN) == 1:
                        time.sleep(0.1)
                    return

                # Button 3 -> Discard and go back to live mode
                elif self.disp.digital_read(self.KEY3_PIN) == 1:
                    self.logger.info("Discarding frozen spectrum; returning to live mode...")
                    self.spectrum_data = None
                    self.live_mode = True
                    self.disp.clear()
                    # Keep backlight on while we jump back into live mode
                    self.disp.bl_DutyCycle(50)
                    while self.disp.digital_read(self.KEY3_PIN) == 1:
                        time.sleep(0.1)
                    return

    def handle_state_3(self):
        """
        Handle the CAMERA state.

        - Shows live preview if no current_image is stored.
        - KEY1: Capture a photo preview (stores it in current_image).
        - If a photo is stored:
          - KEY2: Save the photo, return to IDLE.
          - KEY3: Discard the photo, return to live preview.
        """
        try:
            # If camera is not already running, start it
            if self.camera:
                try:
                    self.camera.start()
                except Exception as e:
                    self.logger.error(f"Error trying to start camera: {e}")

            # No captured photo yet, so show live preview
            if self.current_image is None and self.camera is not None:
                frame = self.camera.capture_array("main")
                frame_resized = cv2.resize(frame, (240, 240), interpolation=cv2.INTER_AREA)
                preview_image = Image.fromarray(frame_resized)
                self.disp.ShowImage(preview_image)

                # KEY1 pressed: Capture photo for preview
                if self.disp.digital_read(self.KEY1_PIN) == 1:
                    self.logger.info("Capturing photo for preview...")
                    captured_frame = self.camera.capture_array("main")
                    self.current_image = captured_frame
                    preview_frame = cv2.resize(captured_frame, (240, 240), interpolation=cv2.INTER_AREA)
                    preview_image = Image.fromarray(frame_resized)
                    self.disp.ShowImage(preview_image)

                    while self.disp.digital_read(self.KEY1_PIN) == 1:
                        time.sleep(0.1)

            # Photo is captured and displayed
            else:
                # KEY2: Save the captured photo and return to IDLE
                if self.disp.digital_read(self.KEY2_PIN) == 1:
                    self.logger.info("Saving captured photo...")
                    if not self.current_filename:
                        self.current_filename = f"spectrum_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"

                    full_image_filename = f"{self.current_filename}_photo.jpg"
                    image_to_save = cv2.cvtColor(self.current_image, cv2.COLOR_RGB2BGR)
                    cv2.imwrite(full_image_filename, image_to_save)
                    self.logger.info(f"Full resolution photo saved: {full_image_filename}")

                    # Clean up and revert to IDLE
                    if self.camera:
                        self.camera.stop()
                    self.current_state = self.STATE_1
                    self.spectrum_data = None
                    self.current_image = None
                    self.current_filename = None
                    self.disp.clear()
                    self.disp.bl_DutyCycle(0)

                    while self.disp.digital_read(self.KEY2_PIN) == 1:
                        time.sleep(0.1)

                # KEY3: Discard the photo and return to live preview
                elif self.disp.digital_read(self.KEY3_PIN) == 1:
                    self.logger.info("Discarding photo and returning to preview...")
                    self.current_image = None  # Clear stored image

                    while self.disp.digital_read(self.KEY3_PIN) == 1:
                        time.sleep(0.1)

        except Exception as e:
            self.logger.error(f"Error in camera handling: {str(e)}")
            # Reset state on error
            self.current_image = None
            self.current_state = self.STATE_1

    def run(self):
        """
        Main program loop. Monitors button presses and handles state transitions.
        """
        try:
            while True:
                if self.current_state == self.STATE_1:
                    self.handle_state_1()
                elif self.current_state == self.STATE_2:
                    self.handle_state_2()
                elif self.current_state == self.STATE_3:
                    self.handle_state_3()
                time.sleep(0.1)

        except KeyboardInterrupt:
            self.logger.info("Exiting program...")

        finally:
            self.cleanup()

    def cleanup(self):
        """
        Clean up hardware and close resources before exiting.
        """
        if self.spectrometer:
            self.spectrometer.close()
        if self.camera:
            self.camera.stop()
        self.disp.clear()
        self.disp.bl_DutyCycle(0)
        self.disp.module_exit()

if __name__ == "__main__":
    system = SpectrometerSystem()
    system.run()
