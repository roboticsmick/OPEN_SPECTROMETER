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
2. Operating System: Raspberry Pi OS (32 Bit) (Note: I have found the 64 bit OS to be buggy with the Raspberry Pi Zero 2 W. If you change to a Raspberry Pi 4 I would go with the 64 bit OS.)
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
ssh -X pi@192.XXX.X.X
```
Enter password: spectro

### Setting up the Raspberry Pi software for the LCD


```sh
cd
nano setup_pi.sh
```

```bash
#!/bin/bash
# Update and upgrade
sudo apt-get update -y && sudo apt-get upgrade -y
# Install packages with auto-confirm
sudo apt-get install -y vim git cmake python3-RPi.GPIO python3-numpy python3-pil \
  git-all build-essential libusb-dev p7zip-full python3-matplotlib \
  libatlas-base-dev python3-pip python3-opencv feh
# Ttkbootstrap setup
pip3 install ttkbootstrap
# Add user to groups
sudo usermod -aG video pi
sudo usermod -aG i2c,gpio pi
# PySeabreeze setup - without venv
pip3 install seabreeze[pyseabreeze]
seabreeze_os_setup
# LCD screen setup
sudo touch /etc/rc.local
sudo chmod +x /etc/rc.local
git clone https://github.com/goodtft/LCD-show.git
chmod -R 755 LCD-show
cd LCD-show
sudo ./LCD24-3A+-show
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




