# OPEN_SPECTROMETER

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

Install the LCD display drivers. May be missing stuff here.

```sh
wget https://files.waveshare.com/upload/b/bd/1.3inch_LCD_HAT_code.7z
7z x 1.3inch_LCD_HAT_code.7z -r -o./1.3inch_LCD_HAT_code
sudo chmod 777 -R 1.3inch_LCD_HAT_code
mv ~/pysb/1.3inch_LCD_HAT_code/1.3inch_LCD_HAT_code/python ~/pysb/lcd
```

Veiw the spectra or images using feh. 

```sh
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
```

If it is currently running you can stop it

```sh
sudo systemctl stop spectrometer.service
sudo systemctl disable spectrometer.service
```

To make changes to the service file:

```sh
sudo systemctl daemon-reload
sudo systemctl restart spectrometer.service
```

## To do:

Add a voltage output to the display to monitor the lipo batteries.




