import seabreeze
seabreeze.use('pyseabreeze')
import seabreeze.spectrometers as sb
from datetime import datetime
import matplotlib.pyplot as plt
from PIL import Image, ImageDraw, ImageFont
import sys
import os
import time
import subprocess
import logging
from picamera2 import Picamera2, MappedArray
import cv2
import libcamera
import csv
sys.path.append('./lcd')
import ST7789

class SpectrometerSystem:
    # System states
    STATE_1 = "IDLE"
    STATE_2 = "SPECTRA"
    STATE_3 = "CAMERA"

    def __init__(self):
        # Initialize logging
        logging.basicConfig(level=logging.INFO)
        self.logger = logging.getLogger(__name__)

        # Initialize display
        self.disp = ST7789.ST7789()
        self.disp.Init()
        self.disp.clear()
        self.disp.bl_DutyCycle(0)
        
        # Initialize state variables before display setup
        self.current_state = self.STATE_1
        self.spectrometer = None
        self.camera = None
        self.spectrum_data = None
        self.current_image = None
        self.current_filename = None

        # Key pins
        self.KEY1_PIN = self.disp.GPIO_KEY1_PIN
        self.KEY2_PIN = self.disp.GPIO_KEY2_PIN
        self.KEY3_PIN = self.disp.GPIO_KEY3_PIN

        # Configuration
        self.INTEGRATION_TIME_MICROS = 1000000  # 1 second
        self.FONT_SIZE = 16
        self.FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

        # State variables
        self.current_state = self.STATE_1
        self.spectrometer = None
        self.camera = None
        self.spectrum_data = None
        self.current_image = None
        self.current_filename = None

        # Initialize camera
        self._setup_camera()

    def _setup_camera(self):
        """Initialize the camera with configured settings."""
        try:
            self.camera = Picamera2()
            config = self.camera.create_preview_configuration(
                main={"size": (240, 240)},
                transform=libcamera.Transform(hflip=1, vflip=1)  # Flip image if needed
            )
            self.camera.configure(config)
            self.camera.pre_callback = self._apply_timestamp
            self.logger.info("Camera setup completed successfully")
        except Exception as e:
            self.logger.error(f"Camera setup failed: {str(e)}")
            self.camera = None

    def _apply_timestamp(self, request):
        """Apply timestamp overlay to camera preview."""
        timestamp = time.strftime("%Y-%m-%d %X")
        with MappedArray(request, "main") as m:
            cv2.putText(m.array, timestamp, (10, 30), cv2.FONT_HERSHEY_SIMPLEX,
                       0.6, (0, 255, 0), 1)

    def show_message(self, message_lines, duration=6):
        """Display message on LCD screen."""
        self.disp.bl_DutyCycle(50)
        image = Image.new("RGB", (self.disp.width, self.disp.height), "WHITE")
        draw = ImageDraw.Draw(image)
        font = ImageFont.truetype(self.FONT_PATH, self.FONT_SIZE)

        y_offset = 20
        for line in message_lines:
            draw.text((10, y_offset), line, font=font, fill="BLACK")
            y_offset += self.FONT_SIZE + 5

        self.disp.ShowImage(image)
        time.sleep(duration)
        self.disp.clear()
        self.disp.bl_DutyCycle(0)

    def get_wifi_info(self):
        """Get WiFi connection information."""
        ssid = subprocess.getoutput("iwgetid -r").strip() or "not connected"
        ip_addr = subprocess.getoutput("hostname -I").strip() or "not connected"
        wifi_password = "spectro" if ssid != "not connected" else "not connected"
        return ssid, ip_addr, wifi_password

    def get_datetime_info(self):
        """Get current date and time information."""
        date_str = time.strftime("%d %b %Y")
        time_str = time.strftime("%H:%M:%S")
        tz_str = "Australia/Brisbane (AEST, +1000)"
        return date_str, time_str, tz_str

    def capture_spectrum(self):
        """Capture spectrum data."""
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
        """Generate spectrum plot as image."""
        fig, ax = plt.subplots(figsize=(240/96, 240/96), dpi=96)
        ax.plot(x, y)
        ax.set_title(f"Integration: {self.INTEGRATION_TIME_MICROS}µsec", fontsize=8)
        ax.grid(True, linestyle="--", alpha=0.6)
        
        # Configure axes
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
        
        return Image.open("/tmp/spectrum.png").resize((240, 240))

    def save_data(self, x, y, image=None):
        """Save spectrum data and optional image."""
        timestamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
        base_filename = f"spectrum_{timestamp}"
        
        # Save spectrum data
        with open(f"{base_filename}.csv", 'w', newline='') as csvfile:
            csvwriter = csv.writer(csvfile)
            csvwriter.writerow(["Wavelengths"] + list(x))
            csvwriter.writerow(["Intensities"] + list(y))
            csvwriter.writerow(["Timestamp", timestamp])
            csvwriter.writerow(["Integration Time", self.INTEGRATION_TIME_MICROS])

        # Save spectrum plot
        plt.figure()
        plt.plot(x, y)
        plt.title(f"Spectra - Integration: {self.INTEGRATION_TIME_MICROS}µsec")
        plt.xlabel("Wavelength (nm)")
        plt.ylabel("Intensity")
        plt.grid(True, linestyle="--", alpha=0.6)
        plt.savefig(f"{base_filename}.png", dpi=300)
        plt.close()

        # Save camera image if provided
        if image is not None:
            cv2.imwrite(f"{base_filename}_photo.jpg", image)

        return base_filename

    def handle_state_1(self):
        """Handle IDLE state operations."""
        if self.disp.digital_read(self.KEY1_PIN) == 1:  # Capture spectra
            self.show_message(["CAPTURING SPECTRA..."], 1)
            self.current_state = self.STATE_2
            while self.disp.digital_read(self.KEY1_PIN) == 1:
                time.sleep(0.1)

        elif self.disp.digital_read(self.KEY2_PIN) == 1:  # Show WiFi info
            ssid, ip_addr, password = self.get_wifi_info()
            self.show_message([
                f"WiFi: {ssid}",
                f"IP: {ip_addr}",
                f"Pass: {password}"
            ])
            while self.disp.digital_read(self.KEY2_PIN) == 1:
                time.sleep(0.1)

        elif self.disp.digital_read(self.KEY3_PIN) == 1:  # Show date/time
            date_str, time_str, tz_str = self.get_datetime_info()
            self.show_message([
                f"Date: {date_str}",
                f"Time: {time_str}",
                f"TZ: {tz_str}"
            ])
            while self.disp.digital_read(self.KEY3_PIN) == 1:
                time.sleep(0.1)

    def handle_state_2(self):
        """Handle SPECTRA state operations."""
        if not self.spectrum_data:
            x, y = self.capture_spectrum()
            self.spectrum_data = (x, y)
            image = self.plot_spectrum(x, y)
            self.disp.bl_DutyCycle(50)
            self.disp.ShowImage(image)

        if self.disp.digital_read(self.KEY2_PIN) == 1:  # Save and go to camera
            self.current_filename = self.save_data(*self.spectrum_data)
            self.current_state = self.STATE_3
            self.camera.start()
            while self.disp.digital_read(self.KEY2_PIN) == 1:
                time.sleep(0.1)

        elif self.disp.digital_read(self.KEY3_PIN) == 1:  # Reject and return
            self.spectrum_data = None
            self.current_state = self.STATE_1
            self.disp.clear()
            self.disp.bl_DutyCycle(0)
            while self.disp.digital_read(self.KEY3_PIN) == 1:
                time.sleep(0.1)

    def handle_state_3(self):
        """Handle CAMERA state operations."""
        try:
            # Show live preview if no image is captured
            if self.current_image is None:
                frame = self.camera.capture_array()
                image = Image.fromarray(frame)
                self.disp.ShowImage(image)

            if self.disp.digital_read(self.KEY1_PIN) == 1:  # Capture photo
                self.logger.info("Capturing photo...")
                self.current_image = self.camera.capture_array()
                image = Image.fromarray(self.current_image)
                self.disp.ShowImage(image)
                self.logger.info("Photo captured and displayed")
                while self.disp.digital_read(self.KEY1_PIN) == 1:
                    time.sleep(0.1)

            elif self.disp.digital_read(self.KEY2_PIN) == 1:  # Save and return
                if self.current_image is not None:
                    self.save_data(*self.spectrum_data, self.current_image)
                    self.camera.stop()
                    self.current_state = self.STATE_1
                    self.spectrum_data = None
                    self.current_image = None
                    self.current_filename = None
                    self.disp.clear()
                    self.disp.bl_DutyCycle(0)
                while self.disp.digital_read(self.KEY2_PIN) == 1:
                    time.sleep(0.1)

            elif self.disp.digital_read(self.KEY3_PIN) == 1:  # Restart camera
                self.current_image = None
                while self.disp.digital_read(self.KEY3_PIN) == 1:
                    time.sleep(0.1)

        except Exception as e:
            self.logger.error(f"Error in camera handling: {str(e)}")
            # Reset state on error
            self.current_image = None
            self.current_state = self.STATE_1

    def run(self):
        """Main program loop."""
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
        """Clean up system resources."""
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
