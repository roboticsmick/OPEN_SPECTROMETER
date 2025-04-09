# OPEN_SPECTROMETER

This library allows you to use the Ocean Optics ST-VIS range of spectrometers using a Raspberry Pi Zerro 2W and a LCD display to view spectra using the Seabreeze API. This allows for a very small low power package that can easily be integrated into a small handheld device for field work. 

Lots of love to the the people working on keeping the PySeabreeze API alive. This let me get the Ocena Optic Spectrometer working on an ARM device. 

## Installing Pyseabreeze on PC

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
2. Operating System: Ubuntu 22.04 Server LTS (https://ubuntu.com/tutorials/how-to-install-ubuntu-on-your-raspberry-pi#1-overview)
3. Stoage - Use a Samsung 128GB PRO Plus microSD Card or high quality SD card. Get the best one you can afford from a reputable supplier. Don't be cheap here.
4. Select $Edit Settings$
  1. Set hostname: $rpi$
  2.Tick $Set username and password$
  3. Set username: $pi$
  4. Set password: $spectro$
  5. Tick $Configure wireless LAN$
  6. Enter known wifi name (I use my mobile hotspot name so I can access this easily in the field)
  7. Enter wifi password I use my mobile hotspot password so I can access this easily in the field)
  8. Set Wireless LAN country: $AU$
  9. Tick $Set locale settings$
  10. Timezone: $Australia/Brisbane$
  11. Keyboard Layout: $US$
  12. Select Services Tab
  13. Tick $Enable SSH - Use password authentication$
  14. Click Save
  15. Click Yes to apply OS customisation settings when you write the image to the storage device.

This will flash the OS to the SD card.

Enable your mobile phone hotspot so it can connect to the wifi.
Insert the SD card into the Raspberry Pi. 
Boot up the Raspberry Pi.
*Note: When first booting *
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

# Check and correct date/time
echo "Checking system date and time..."
current_year=$(date +"%Y")
if [ "$current_year" -lt "2024" ]; then
    echo "======================================"
    echo "ERROR: System date appears to be wrong"
    echo "Current system date is: $(date)"
    echo "======================================"
    echo "Would you like to:"
    echo "1) Set date/time automatically via NTP (requires internet)"
    echo "2) Set date/time manually"
    echo "3) Exit script to fix date/time yourself"
    read -p "Enter choice [1-3]: " dt_choice
    
    case $dt_choice in
        1)
            echo "Attempting to sync time via NTP..."
            sudo timedatectl set-ntp true
            # Give NTP a moment to sync
            sleep 5
            sudo systemctl restart systemd-timesyncd
            sleep 2
            echo "Current system date is now: $(date)"
            current_year=$(date +"%Y")
            if [ "$current_year" -lt "2024" ]; then
                echo "NTP sync failed or not connected to internet."
                echo "Please ensure internet connectivity or set time manually."
                exit 1
            fi
            ;;
        2)
            echo "Please enter the current date in the format YYYY-MM-DD:"
            read -p "Date (YYYY-MM-DD): " manual_date
            echo "Please enter the current time in the format HH:MM:SS (24-hour):"
            read -p "Time (HH:MM:SS): " manual_time
            sudo timedatectl set-ntp false
            sudo timedatectl set-time "${manual_date} ${manual_time}"
            echo "Date and time set to: $(date)"
            ;;
        3)
            echo "Exiting script. Please fix the date/time and run the script again."
            echo "You can set the date/time with: sudo timedatectl set-time \"YYYY-MM-DD HH:MM:SS\""
            exit 1
            ;;
        *)
            echo "Invalid choice. Exiting."
            exit 1
            ;;
    esac
fi

# Configure needrestart to automatic mode (no prompts) if not already done
echo "Checking needrestart configuration..."
if grep -q "#\$nrconf{restart} = 'i';" /etc/needrestart/needrestart.conf && ! grep -q "\$nrconf{restart} = 'a';" /etc/needrestart/needrestart.conf; then
    echo "Configuring needrestart to automatic mode..."
    sudo sed -i 's/#$nrconf{restart} = '"'"'i'"'"';/$nrconf{restart} = '"'"'a'"'"';/g' /etc/needrestart/needrestart.conf
else
    echo "Needrestart already configured for automatic mode."
fi

# Increase swap size to help with memory issues if not already done
echo "Checking swap configuration..."
if [ ! -f /swapfile ] || [ "$(stat -c %s /swapfile)" -lt "1000000000" ]; then
    echo "Configuring additional swap space..."
    # Remove existing swap if too small
    if [ -f /swapfile ]; then
        sudo swapoff /swapfile
        sudo rm /swapfile
    fi
    sudo fallocate -l 1G /swapfile
    sudo chmod 600 /swapfile
    sudo mkswap /swapfile
    sudo swapon /swapfile
    # Make swap permanent if not already in fstab
    if ! grep -q "/swapfile swap swap defaults 0 0" /etc/fstab; then
        echo "/swapfile swap swap defaults 0 0" | sudo tee -a /etc/fstab
    fi
else
    echo "Swap already configured."
fi

# Enable SPI on Ubuntu if not already enabled
echo "Checking SPI interface configuration..."
if ! grep -q "dtparam=spi=on" /boot/firmware/config.txt; then
    echo "Enabling SPI interface..."
    echo "dtparam=spi=on" | sudo tee -a /boot/firmware/config.txt
else
    echo "SPI interface already enabled."
fi

# Configure inputrc for better terminal experience if not already done
echo "Checking terminal history configuration..."
if [ ! -f ~/.inputrc ] || ! grep -q "history-search-backward" ~/.inputrc; then
    echo "Setting up terminal history search with arrow keys..."
    cat > ~/.inputrc << 'EOL'
# Respect default shortcuts.
$include /etc/inputrc

## arrow up
"\e[A":history-search-backward
## arrow down
"\e[B":history-search-forward
EOL
else
    echo "Terminal history search already configured."
fi

# Update package lists
echo "Updating package lists..."
sudo apt-get update -y

# Install dependencies one by one to manage memory usage
echo "Installing essential packages..."
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y pkg-config
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y libusb-1.0-0-dev
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y python3-pip
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y python3-dev
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y git
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y vim
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y build-essential
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y feh

# Add user to required groups if not already added
echo "Setting up user permissions..."
for group in video i2c gpio spi; do
    if ! groups pi | grep -q "\b$group\b"; then
        sudo usermod -aG $group pi
        echo "Added user to $group group."
    else
        echo "User already in $group group."
    fi
done

# Update pip and setuptools system-wide
echo "Updating pip and setuptools..."
sudo pip3 install --upgrade pip setuptools wheel

# Install Python packages
echo "Installing Python packages..."
sudo pip3 install matplotlib
sudo pip3 install seabreeze[pyseabreeze]
sudo pip3 install displayhatmini pygame spidev RPi.GPIO

# Check if udev rules for seabreeze already exist
echo "Checking seabreeze udev rules..."
if [ ! -f /etc/udev/rules.d/10-oceanoptics.rules ] || ! grep -q "Ocean Optics" /etc/udev/rules.d/10-oceanoptics.rules; then
    echo "Setting up seabreeze udev rules..."
    sudo seabreeze_os_setup
else
    echo "Seabreeze udev rules already configured."
fi

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




