import time
import board
import busio
import adafruit_ssd1306
import psutil
import socket
from PIL import Image, ImageDraw, ImageFont

# I2C setup
i2c = busio.I2C(board.SCL, board.SDA)

# OLED display setup (width and height for your display)
WIDTH = 128
HEIGHT = 32
oled = adafruit_ssd1306.SSD1306_I2C(WIDTH, HEIGHT, i2c, addr=0x3C)

# Clear the OLED display
oled.fill(0)
oled.show()

# Create a new image with 1-bit color for drawing
image = Image.new("1", (oled.width, oled.height))
draw = ImageDraw.Draw(image)

# Load a default font
font = ImageFont.truetype("/usr/share/fonts/opentype/cantarell/Cantarell-Regular.otf", 10)

def get_ip_address():
    # Get the IP address from network interfaces
    for iface_name, iface_addrs in psutil.net_if_addrs().items():
        for addr in iface_addrs:
            if addr.family == socket.AF_INET and not addr.address.startswith("127."):
                return addr.address
    return "No IP"

def display_info():
    while True:
        # Get the IP address
        ip_address = get_ip_address()

        # Clear the drawing area
        draw.rectangle((0, 0, oled.width, oled.height), outline=0, fill=0)

        # Draw the IP address on the first line
        draw.text((0, 0), f"SSH IP: ssh pi@{ip_address}", font=font, fill=255)

        # Set the default user and password
        user_info = "USER: pi PASSWORD: password"

        # Draw the user and password on the second line
        draw.text((0, 16), user_info, font=font, fill=255)

        # Display image on the OLED
        oled.image(image)
        oled.show()

        # If the IP address is found, exit the loop
        if ip_address != "No IP":
            break

        # Wait for a few seconds before retrying
        time.sleep(5)

if __name__ == "__main__":
    display_info()
