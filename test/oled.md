
## Raspberry Pi OLED SSH display

This script will output the Raspberry Pi IP address on bootup via an I2C OLED (dimensions: 128x32 pixels). Our Raspberry Pi is installed in a waterproof acrylic case, so the OLED display allows us to SSH into the Raspberry Pi to download data easily.

```sh
cd
sudo apt-get update
sudo apt-get install python3-venv
python3 -m venv oled-env
cd oled-env
source bin/activate
pip3 install adafruit-circuitpython-ssd1306 pillow psutil
vim display_ip.py
```

Update the username and password for your own system. 

```py
import time
import board
import busio
import adafruit_ssd1306
import psutil
import socket
from PIL import Image, ImageDraw, ImageFont
import subprocess

# I2C setup - explicitly specify bus 1
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
font = ImageFont.truetype("/usr/share/fonts/opentype/cantarell/Cantarell-Regular.otf", 8)

def get_ip_address():
    # Get the IP address from network interfaces
    for iface_name, iface_addrs in psutil.net_if_addrs().items():
        for addr in iface_addrs:
            if addr.family == socket.AF_INET and not addr.address.startswith("127."):
                return addr.address
    return "No IP"

def get_network_name():
    try:
        # Run iwgetid to get the network SSID
        ssid = subprocess.check_output(["iwgetid", "-r"]).decode("utf-8").strip()
        return ssid if ssid else "No WiFi"
    except subprocess.CalledProcessError:
        return "No WiFi"

def display_info():
    while True:
        # Get the IP address and network name
        ip_address = get_ip_address()
        network_name = get_network_name()

        # Clear the drawing area
        draw.rectangle((0, 0, oled.width, oled.height), outline=0, fill=0)

        # Draw the IP address on the first line
        draw.text((0, 0), f"IP: {ip_address}", font=font, fill=255)

        # Draw the network name (SSID) on the second line
        draw.text((0, 10), f"WiFi: {network_name}", font=font, fill=255)

        # Draw the user and password on the third line
        draw.text((0, 20), "USER: pi PASS: logic", font=font, fill=255)

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
```

### Run at ssh display bootup

```sh
cd
crontab -e
```

```bash
@reboot /bin/bash -c "source /home/pi/oled-env/bin/activate && /home/pi/oled-env/bin/python /home/pi/oled-env/display_ip.py"
```

### Turn off display at shutdown

```sh
cd
vim /home/pi/oled-env/shutdown_oled.py
```

```py
#!/home/pi/oled-env/bin/python3

import board
import busio
import adafruit_ssd1306

# I2C setup - explicitly specify bus 1
i2c = busio.I2C(board.SCL, board.SDA)

# OLED display setup (width and height for your display)
WIDTH = 128
HEIGHT = 32
oled = adafruit_ssd1306.SSD1306_I2C(WIDTH, HEIGHT, i2c, addr=0x3C)

# Clear the display and turn it off
oled.fill(0)
oled.show()
oled.poweroff()
```

```sh
sudo vim /etc/systemd/system/oled-shutdown.service
```

```bash
[Unit]
Description=Turn off OLED display on shutdown
DefaultDependencies=no
Before=shutdown.target reboot.target halt.target
Conflicts=reboot.target halt.target

[Service]
Type=oneshot
ExecStart=/home/pi/oled-env/bin/python3 /home/pi/oled-env/shutdown_oled.py
RemainAfterExit=yes

[Install]
WantedBy=halt.target reboot.target shutdown.target
```

```sh
sudo systemctl daemon-reload
sudo systemctl enable oled-shutdown.service
```

```sh
sudo poweroff
```
