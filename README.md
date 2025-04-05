# OPEN_SPECTROMETER

This library allows you to use the Ocean Optics ST-VIS range of spectrometers using a Raspberry Pi Zerro 2W and a LCD display to view spectra using the Seabreeze API. This allows for a very small low power package that can easily be integrated into a small handheld device for field work. 

Lots of love to the the people working on keeping the PySeabreeze API alive. This let me get the Ocena Optic Spectrometer working on an ARM device. 

## Setup Pi

```sh
sudo apt-get update
sudo apt-get upgrade
sudo apt install vim
sudo apt-get install git cmake
sudo apt install python3-RPi.GPIO
sudo apt-get install python3-numpy
sudo apt-get install python3-pil
sudo apt-get install git-all build-essential libusb-dev
sudo apt-get install p7zip-full -y
sudo apt-get install python3-matplotlib
sudo apt install libatlas-base-dev
sudo apt-get install python3-pip
sudo apt-get install python3-opencv
sudo apt install feh
sudo usermod -aG video pi
sudo usermod -aG i2c,gpio pi
```

Install Pyseabreeze.

```sh
cd
mkdir pysb
cd pysb
python3 -m venv --system-site-packages venv
source venv/bin/activate
pip install seabreeze[pyseabreeze]
seabreeze_os_setup
```

## 2.4inch RPi Display For RPi

Flash the pi with Raspberry Pi OS (32-bit)

```sh
sudo apt install rpi-imager
```

Setup parameters:
1. Raspberry Pi Device: Raspberry Pi Zeroe 2 W
2. Operating System: Ubuntu 22.04 Server LTS. If you change to a Raspberry Pi 4 I would go with the 64 bit OS.)
3. Stoage - Use a Samsung 128GB PRO Plus microSD Card or high quality SD card. Get the best one you can afford from a reputable supplier. Don't be cheap here.
4. Select $Edit Settings$
  1. Set hostname: rpi
  2.Tick Setusername and password
  3. Set username: pi
  4. Set password: spectro
  5. Tick Configure wireless LAN
  6. Enter known wifi name (I use my mobile hotspot name so I can access this easily in the field)
  7. Enter wifi password I use my mobile hotspot password so I can access this easily in the field)
  8. Set Wireless LAN country: AU
  9. Tick Set locale settings
  10. Timezone: Australia/Brisbane
  11. Keyboard Layout: US
  12. Select Services Tab
  13. Tick Enable SSH - Use password authentication
  14. Click Save
  15. Click Yes to apply OS customisation settings when you write the image to the storage device.

This will flash the OS to the SD card.

Enable your mobile phone hotspot so it can connect to the wifi.
Insert the SD card into the Raspberry Pi. 
Boot up the Raspberry Pi. 
Check you mobile phone hotspot. 
When a connection is detected, you Raspberry Pi will have internet access. Check you mobile phone hotspot connections. The Raspberry Pi should show. Click on this and you should be able to see the IP address.
Connect you laptops wifi to your mobile phone hotspot. 
From a terminal on you PC SSH into the Raspberry Pi.

```sh
ping rpi.local
# Copy IP address from ping below into <IP>
ssh -X pi@<IP>
```
Enter password: spectro

### Setting up the Raspberry Pi software for the LCD


```sh
cd
nano setup_pi.sh
```

Copy the code into the text file editor.

```bash
#!/bin/bash
# Exit on error
set -e

echo "Starting Raspberry Pi Zero 2 W setup script..."

# Increase swap size to help with memory issues
echo "Configuring additional swap space..."
sudo fallocate -l 1G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
# Make swap permanent
if ! grep -q "/swapfile" /etc/fstab; then
    echo "/swapfile swap swap defaults 0 0" | sudo tee -a /etc/fstab
fi

# Update package lists
echo "Updating package lists..."
sudo apt-get update -y

# Install dependencies one by one to manage memory usage
echo "Installing essential packages..."
sudo apt-get install -y pkg-config
sudo apt-get install -y libusb-1.0-0-dev
sudo apt-get install -y python3-pip
sudo apt-get install -y python3-dev
sudo apt-get install -y git
sudo apt-get install -y vim
sudo apt-get install -y build-essential
sudo apt-get install -y feh

# Add user to required groups
echo "Setting up user permissions..."
sudo usermod -aG video,i2c,gpio,spi pi

# Enable SPI on Ubuntu
echo "Enabling SPI interface..."
if ! grep -q "dtparam=spi=on" /boot/firmware/config.txt; then
    echo "dtparam=spi=on" | sudo tee -a /boot/firmware/config.txt
fi

# Configure inputrc for better terminal experience
echo "Setting up terminal history search with arrow keys..."
cat > ~/.inputrc << 'EOL'
# Respect default shortcuts.
$include /etc/inputrc

## arrow up
"\e[A":history-search-backward
## arrow down
"\e[B":history-search-forward
EOL

# Update pip and setuptools system-wide
echo "Updating pip and setuptools..."
sudo pip3 install --upgrade pip setuptools wheel

# Install seabreeze using system-wide pip
echo "Installing seabreeze (this may take a while)..."
sudo pip3 install seabreeze[pyseabreeze]

# Run the seabreeze setup script to create udev rules
echo "Setting up seabreeze udev rules..."
sudo seabreeze_os_setup

# Install Display HAT Mini and pygame
echo "Installing Display HAT Mini and pygame..."
sudo pip3 install displayhatmini pygame spidev RPi.GPIO

echo ""
echo "====================================="
echo "Setup complete! A reboot is required."
echo "Please run: sudo reboot"
echo "====================================="
```

```sh
chmod +x setup_pi.sh
./setup_pi.sh
```

## Wave share 1.3inch LCD and Raspberry Pi Global Shutter Camera version.

This script manages a spectrometer and camera system with an LCD display and button inputs. 

It cycles through three states:
  1) IDLE (STATE_1)       - Allows you to view WiFi info, date/time, or capture spectra.
  2) SPECTRA (STATE_2)    - Captures a spectrum, plots it, and optionally saves data.
  3) CAMERA (STATE_3)     - Allows capturing and saving a photo.

![20250115_160832](https://github.com/user-attachments/assets/246d29bb-95cf-4c4b-8ddd-c75c00e7c21f)
![20250115_161037](https://github.com/user-attachments/assets/fea788cb-c896-4345-8df4-738d69ec9b1e)
![20250115_161008](https://github.com/user-attachments/assets/9e8c0267-01de-4b0c-9a80-7ce6980ef3a4)
![20250115_160840](https://github.com/user-attachments/assets/db950c03-0ba2-4d37-b61a-911c44a8f0be)
![20250115_161206](https://github.com/user-attachments/assets/ecc62726-94f8-45a0-b5f6-1ab269198f1b)

### Install LCD driver

Install the LCD display drivers. May be missing stuff here as I didn't document it as I got it working. My bad.

```sh
wget https://files.waveshare.com/upload/b/bd/1.3inch_LCD_HAT_code.7z
7z x 1.3inch_LCD_HAT_code.7z -r -o./1.3inch_LCD_HAT_code
sudo chmod 777 -R 1.3inch_LCD_HAT_code
mv ~/pysb/1.3inch_LCD_HAT_code/1.3inch_LCD_HAT_code/python ~/pysb/lcd
```

### Running script:

```sh
cd /home/pi/pysb
source venv/bin/activate
cd 1_3_INCH_WAVESHARE_LCD_PI_GLOBAL_SHUTTER_CAM
python3 disp_spec_plot.py
```

## Veiwing the saved spectra and camera images via using feh. 

```sh
ssh -X 
sudo apt install feh
feh spectrum_20241212102529.png --auto-zoom --scale-down -g 600x600 -
```

## Run disp_spec_plot.py at startup.


```sh
cd pysb
vim run_spectrometer.sh
```

```bash
#!/bin/bash

# Navigate to the correct directory
cd /home/pi/pysb

# Activate the virtual environment
source venv/bin/activate

# Run the Python script
python3 disp_spec_plot.py
```

```sh
chmod +x /home/pi/pysb/run_spectrometer.sh
chmod +x /home/pi/pysb/disp_spec_plot.py
sudo nano /etc/systemd/system/spectrometer.service
```

```bash
[Unit]
Description=Spectrometer System Service
After=network.target

[Service]
Type=simple
User=pi
Group=pi
WorkingDirectory=/home/pi/pysb
ExecStart=/home/pi/pysb/run_spectrometer.sh
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```sh
# Reload systemd to recognize the new service
sudo systemctl daemon-reload
# Enable the service to start at boot
sudo systemctl enable spectrometer.service
# Start the service now
sudo systemctl start spectrometer.service
# Check the status
sudo systemctl status spectrometer.service
```

To stop it at boot

```sh
sudo systemctl disable spectrometer.service
```

If it is currently running you can stop it

```sh
sudo systemctl stop spectrometer.service
```

To make changes to the service file:

```sh
sudo systemctl daemon-reload
sudo systemctl restart spectrometer.service
```

## To do:

Add a voltage output to the display to monitor the lipo batteries.




