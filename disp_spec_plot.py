import seabreeze
seabreeze.use('pyseabreeze')
import seabreeze.spectrometers as sb
from datetime import datetime
import matplotlib.pyplot as plt
from PIL import Image, ImageDraw, ImageFont
import sys
import os
sys.path.append('./lcd')  # Add the lcd directory to Python path
import time
import logging
import spidev as SPI
import ST7789
import csv

# Logging level (optional)
logging.basicConfig(level=logging.INFO)

# Initialize display
disp = ST7789.ST7789()
disp.Init()
disp.clear()
disp.bl_DutyCycle(0)  # LCD backlight off initially

# Configuration Variables
INTEGRATION_TIME_MICROS = 1000000  # 1 second
FONT_SIZE = 16  # Adjustable font size for messages

# Key pins
KEY1_PIN = disp.GPIO_KEY1_PIN  # Capture & Display
KEY2_PIN = disp.GPIO_KEY2_PIN  # Save & Turn Off
KEY3_PIN = disp.GPIO_KEY3_PIN  # Cancel & Turn Off

# Placeholder for GPS data
last_gps_latitude = None
last_gps_longitude = None

def capture_spectrum(spec):
    """Captures and returns wavelength and corrected intensities."""
    spec.integration_time_micros(INTEGRATION_TIME_MICROS)
    x = spec.wavelengths()
    y_correct = spec.intensities(correct_dark_counts=True, correct_nonlinearity=True)
    return x, y_correct

def plot_to_image(x, y):
    """Generate a plot and return it as a PIL Image object sized for 240x240."""
    ticklabelpad = plt.rcParams['xtick.major.pad']
    fig, ax = plt.subplots(figsize=(240/96, 240/96), dpi=96)
    ax.plot(x, y)
    ax.set_title(f"Integration: {INTEGRATION_TIME_MICROS}µsec", fontsize=8)
    ax.grid(True, linestyle="--", alpha=0.6)

    # Set x-axis limits to the range of your data
    ax.set_xlim(min(x), max(x))

    xticks = ax.get_xticks().tolist()
    yticks = ax.get_yticks().tolist()
    if len(xticks) > 1:
        xticks = [int(t) for t in xticks if t >= min(x) and t <= max(x)]
    if len(yticks) > 1:
        yticks = [int(t) for t in yticks[:-1]]

    ax.set_xticks(xticks)
    ax.set_yticks(yticks)
    ax.set_xticklabels([str(t) for t in xticks], fontsize=8)
    ax.set_yticklabels([str(t) for t in yticks], fontsize=8)

    fontproperties = ax.xaxis.get_label().get_fontproperties()
    ax.annotate('$\lambda$', xy=(1, 0), xytext=(4, -ticklabelpad-2),
                xycoords='axes fraction', textcoords='offset points',
                ha='left', va='top', fontproperties=fontproperties, fontsize=8)
    ax.annotate('$\Phi$', xy=(0, 1), xytext=(-5, 6),
                xycoords='axes fraction', textcoords='offset points',
                ha='right', va='center', fontproperties=fontproperties, fontsize=8)

    fig.tight_layout()
    temp_image_path = "/tmp/spectrum.png"
    plt.savefig(temp_image_path, dpi=96)
    plt.close(fig)

    image = Image.open(temp_image_path).resize((240, 240))
    return image

def save_spectrum(x, y):
    """Saves the spectrum as a PNG and CSV with UTC timestamp."""
    timestamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    output_png = f"spectrum_{timestamp}.png"
    output_csv = f"spectrum_{timestamp}.csv"

    # Save PNG
    fig, ax = plt.subplots()
    ax.plot(x, y)
    ax.set_title(f"Spectra - Integration: {INTEGRATION_TIME_MICROS}µsec")
    ax.set_xlabel("Wavelength (nm)")
    ax.set_ylabel("Intensity")
    ax.grid(True, linestyle="--", alpha=0.6)
    fig.tight_layout()
    fig.savefig(output_png, dpi=300)
    plt.close(fig)

    # Save CSV
    with open(output_csv, 'w', newline='') as csvfile:
        csvwriter = csv.writer(csvfile)
        csvwriter.writerow(["Wavelengths"] + list(x))
        csvwriter.writerow(["Intensities"] + list(y))
        csvwriter.writerow(["Timestamp"] + [timestamp])
        csvwriter.writerow(["Integration Time"] + [INTEGRATION_TIME_MICROS])
        csvwriter.writerow(["Latitude"] + [last_gps_latitude if last_gps_latitude else "N/A"])
        csvwriter.writerow(["Longitude"] + [last_gps_longitude if last_gps_longitude else "N/A"])

    return output_png, output_csv

def show_message(message_lines, duration=1):
    """Display a message on the LCD for 'duration' seconds."""
    image = Image.new("RGB", (disp.width, disp.height), "WHITE")
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", FONT_SIZE)

    y_offset = 20
    for line in message_lines:
        draw.text((10, y_offset), line, font=font, fill="BLACK")
        y_offset += FONT_SIZE + 5

    disp.ShowImage(image)
    time.sleep(duration)

# State variables
spectrometer_active = False
spectrum_displayed = False
x_data = None
y_data = None
image = None

try:
    while True:
        # Key1: Capture & Display Spectrum
        if disp.digital_read(KEY1_PIN) == 1:
            if not spectrometer_active:
                print("Activating spectrometer...")
                spec = sb.Spectrometer.from_serial_number()
                spectrometer_active = True
                disp.bl_DutyCycle(50)
            show_message(["CAPTURING SPECTRA..."], duration=1)
            disp.clear()
            x_data, y_data = capture_spectrum(spec)
            image = plot_to_image(x_data, y_data)
            disp.ShowImage(image)
            spectrum_displayed = True
            while disp.digital_read(KEY1_PIN) == 1:
                time.sleep(0.1)

        # Key2: Save & Turn Off
        if disp.digital_read(KEY2_PIN) == 1:
            if spectrometer_active and spectrum_displayed:
                print("Saving spectrum...")
                png_filename, csv_filename = save_spectrum(x_data, y_data)
                show_message(["SAVED:", f"Image: {png_filename}", f"CSV: {csv_filename}"], duration=1)
                spec.close()
                spectrometer_active = False
                spectrum_displayed = False
                disp.clear()
                disp.bl_DutyCycle(0)
            while disp.digital_read(KEY2_PIN) == 1:
                time.sleep(0.1)

        # Key3: Cancel & Turn Off
        if disp.digital_read(KEY3_PIN) == 1:
            if spectrometer_active:
                print("Canceling and turning off LCD...")
                show_message(["SPECTRA DELETED."], duration=1)
                spec.close()
                spectrometer_active = False
                spectrum_displayed = False
                disp.clear()
                disp.bl_DutyCycle(0)
            while disp.digital_read(KEY3_PIN) == 1:
                time.sleep(0.1)

        time.sleep(0.1)

except KeyboardInterrupt:
    print("Exiting program...")
    if spectrometer_active:
        spec.close()
    disp.clear()
    disp.bl_DutyCycle(0)
    disp.module_exit()
