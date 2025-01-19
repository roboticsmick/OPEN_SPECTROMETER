#!/usr/bin/env python3
"""
SpectrometerSystem
------------------
This script manages a spectrometer system with an e-ink display and button inputs.
It cycles between two states:
  1) IDLE (STATE_1)     - Shows last capture or status info
  2) SPECTRA (STATE_2)  - Captures a spectrum, plots it, and optionally saves data

Dependencies:
  - seabreeze
  - matplotlib
  - PIL (Pillow)
  - inky (e-ink display library)
"""

import logging
import sys
import os
import io
import time
import csv
from datetime import datetime

# Seabreeze libraries for spectrometers
import seabreeze
seabreeze.use('pyseabreeze')
import seabreeze.spectrometers as sb

# Plotting and image manipulation libraries
import matplotlib.pyplot as plt
from PIL import Image, ImageDraw, ImageFont

# E-ink display library
from inky.auto import auto

class SpectrometerSystem:
    """
    A class to manage the spectrometer and e-ink display.

    States
    ------
    STATE_1 = "IDLE"      # Idle: show last capture or status
    STATE_2 = "SPECTRA"   # Spectra: show live capture, save or discard
    """

    STATE_1 = "IDLE"
    STATE_2 = "SPECTRA"

    def __init__(self):
        """Initialize the SpectrometerSystem."""
        # Set up logging
        logging.basicConfig(level=logging.INFO)
        self.logger = logging.getLogger(__name__)

        # Initialize e-ink display
        try:
            self.display = auto(ask_user=True, verbose=True)
        except TypeError:
            raise TypeError("You need to update the Inky library to >= v1.1.0")

        # Get display dimensions
        self.width, self.height = self.display.resolution
        self.plot_width = int(2 * self.width / 3)  # 2/3 of screen for plot
        self.info_width = self.width - self.plot_width  # 1/3 for info

        # Set display border and rotation if needed
        self.display.set_border(self.display.BLACK)

        # Spectrometer integration time (microseconds)
        self.INTEGRATION_TIME_MICROS = 5000000  # 0.5 second

        # Font settings for on-screen text
        self.FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        self.font = ImageFont.truetype(self.FONT_PATH, 12)

        # State variables
        self.current_state = self.STATE_1
        self.spectrometer = None
        self.spectrum_data = None
        self.current_filename = None
        self.live_mode = True

    def capture_spectrum(self):
        """Capture spectrum data from the spectrometer."""
        if not self.spectrometer:
            self.spectrometer = sb.Spectrometer.from_serial_number()

        self.spectrometer.integration_time_micros(self.INTEGRATION_TIME_MICROS)
        wavelengths = self.spectrometer.wavelengths()
        intensities = self.spectrometer.intensities(
            correct_dark_counts=True,
            correct_nonlinearity=True
        )
        return wavelengths, intensities

    def plot_spectrum(self, x, y):
        """Generate spectrum plot for e-ink display."""
        # Create figure with size matching the plot area of e-ink display
        fig, ax = plt.subplots(figsize=(self.plot_width/100, self.height/100), dpi=100)
        ax.plot(x, y, 'k-', linewidth=1)  # Black line for e-ink
        ax.set_title(f"Integration: {self.INTEGRATION_TIME_MICROS/1e6:.1f}s", fontsize=8)
        ax.grid(True, linestyle="--", alpha=0.3)

        # Make the plot readable
        ax.set_xlim(min(x), max(x))
        xticks = [int(t) for t in ax.get_xticks() if min(x) <= t <= max(x)]
        yticks = [int(t) for t in ax.get_yticks()[:-1]]
        ax.set_xticks(xticks)
        ax.set_yticks(yticks)
        ax.set_xticklabels([str(t) for t in xticks], fontsize=8)
        ax.set_yticklabels([str(t) for t in yticks], fontsize=8)

        fig.tight_layout()
        
        # Save to buffer
        buf = io.BytesIO()
        plt.savefig(buf, format='png', dpi=100)
        buf.seek(0)
        plt.close(fig)

        return Image.open(buf)

    def create_display_image(self, spectrum_img=None, info_text=""):
        """Create combined image with spectrum and info area."""
        # Create blank image with display dimensions
        img = Image.new("P", (self.width, self.height), self.display.WHITE)
        draw = ImageDraw.Draw(img)

        # If we have spectrum data, paste it into left 2/3
        if spectrum_img:
            spectrum_resized = spectrum_img.resize((self.plot_width, self.height))
            img.paste(spectrum_resized, (0, 0))

        # Draw vertical divider
        draw.line((self.plot_width, 0, self.plot_width, self.height), 
                 fill=self.display.BLACK)

        # Add info text in right 1/3
        if info_text:
            # Split text into lines that fit
            words = info_text.split()
            lines = []
            current_line = []
            for word in words:
                current_line.append(word)
                if draw.textlength(" ".join(current_line), font=self.font) > self.info_width - 10:
                    current_line.pop()
                    lines.append(" ".join(current_line))
                    current_line = [word]
            if current_line:
                lines.append(" ".join(current_line))

            # Draw text lines
            y_offset = 10
            for line in lines:
                draw.text((self.plot_width + 5, y_offset), line, 
                         font=self.font, fill=self.display.BLACK)
                y_offset += 20

        return img

    def save_data(self, x, y):
        """Save spectrum data to CSV and PNG files."""
        timestamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
        base_filename = f"spectrum_{timestamp}"

        # Save CSV
        with open(f"{base_filename}.csv", 'w', newline='') as csvfile:
            csvwriter = csv.writer(csvfile)
            csvwriter.writerow(["Wavelengths"] + list(x))
            csvwriter.writerow(["Intensities"] + list(y))
            csvwriter.writerow(["Timestamp", timestamp])
            csvwriter.writerow(["Integration Time", self.INTEGRATION_TIME_MICROS])

        # Save high-res plot
        plt.figure(figsize=(10, 6))
        plt.plot(x, y, 'k-')
        plt.title(f"Spectra - Integration: {self.INTEGRATION_TIME_MICROS/1e6:.1f}s")
        plt.xlabel("Wavelength (nm)")
        plt.ylabel("Intensity")
        plt.grid(True, linestyle="--", alpha=0.6)
        plt.savefig(f"{base_filename}.png", dpi=300)
        plt.close()

        return base_filename

    def handle_state_1(self):
        """Handle IDLE state - show last capture or status."""
        # If we have previous spectrum data, show it
        if self.spectrum_data:
            x, y = self.spectrum_data
            spectrum_img = self.plot_spectrum(x, y)
            info_text = "Last Capture\n\nPress KEY1 for\nnew capture"
            display_img = self.create_display_image(spectrum_img, info_text)
        else:
            # Show status screen
            info_text = "Ready\n\nPress KEY1 to\nstart capture"
            display_img = self.create_display_image(info_text=info_text)

        self.display.set_image(display_img)
        self.display.show()

        # Check for transition to capture mode
        if self.check_button(1):  # Implement button checking based on your hardware
            self.current_state = self.STATE_2
            self.live_mode = True

    def handle_state_2(self):
        """Handle SPECTRA state with live updating."""
        if self.live_mode:
            # Capture and display live spectrum
            x, y = self.capture_spectrum()
            spectrum_img = self.plot_spectrum(x, y)
            info_text = "Live Mode\n\nKEY1: Capture\nKEY3: Cancel"
            display_img = self.create_display_image(spectrum_img, info_text)
            self.display.set_image(display_img)
            self.display.show()

            if self.check_button(1):  # Freeze
                self.spectrum_data = (x, y)
                self.live_mode = False
            elif self.check_button(3):  # Cancel
                self.current_state = self.STATE_1
                self.live_mode = True

        else:
            # Show frozen spectrum
            x, y = self.spectrum_data
            spectrum_img = self.plot_spectrum(x, y)
            info_text = "Captured\n\nKEY2: Save\nKEY3: Discard"
            display_img = self.create_display_image(spectrum_img, info_text)
            self.display.set_image(display_img)
            self.display.show()

            if self.check_button(2):  # Save
                self.current_filename = self.save_data(x, y)
                self.current_state = self.STATE_1
                self.live_mode = True
            elif self.check_button(3):  # Discard
                self.spectrum_data = None
                self.live_mode = True

    def check_button(self, button_num):
        """
        Check if a button is pressed.
        Implement this based on your specific button hardware.
        """
        # TODO: Implement button checking based on your hardware
        return False

    def run(self):
        """Main program loop."""
        try:
            while True:
                if self.current_state == self.STATE_1:
                    self.handle_state_1()
                elif self.current_state == self.STATE_2:
                    self.handle_state_2()
                time.sleep(0.1)

        except KeyboardInterrupt:
            self.logger.info("Exiting program...")
        finally:
            self.cleanup()

    def cleanup(self):
        """Clean up hardware resources."""
        if self.spectrometer:
            self.spectrometer.close()

if __name__ == "__main__":
    system = SpectrometerSystem()
    system.run()
