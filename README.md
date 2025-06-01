# OPEN_SPECTROMETER

This library allows you to use the Ocean Optics ST-VIS range of spectrometers using a Raspberry Pi Zerro 2W and a LCD display to view spectra using the Seabreeze API. This allows for a very small low power package that can easily be integrated into a small handheld device for field work. 

Lots of love to the the people working on keeping the PySeabreeze API alive. This let me get the Ocena Optic Spectrometer working on an ARM device. 

## Installing Pyseabreeze on Ubuntu PC for testing the spectrometer

```sh
cd
mkdir pysb
cd pysb
python3 -m venv --system-site-packages venv
source venv/bin/activate
pip install seabreeze[pyseabreeze]
seabreeze_os_setup
```

## Raspberry Pi Spectrometer Setup with Adafruit PiTFT 2.8"

Automated setup for a Raspberry Pi Zero 2 W with Ubuntu 22.04 Server LTS, Adafruit PiTFT 2.8" display, and pyseabreeze spectrometer integration.

## Hardware Requirements

- Raspberry Pi Zero 2 W
- Adafruit PiTFT 2.8" Resistive Touchscreen (Product ID: 1601)
- High-quality microSD card (128GB Samsung PRO Plus recommended)
- DS3231 RTC module (optional but recommended)
- Ocean Optics compatible spectrometer

## Initial Setup: Flash Ubuntu 22.04 Server LTS

### 1. Install Raspberry Pi Imager

```bash
sudo apt install rpi-imager
```

### 2. Flash the Operating System

**Hardware Configuration:**
- **Device:** Raspberry Pi Zero 2 W
- **OS:** Ubuntu 22.04 Server LTS
- **Storage:** Use a high-quality microSD card (don't skimp on quality)

**Imager Settings:**
1. Click `Edit Settings` before flashing
2. **General Tab:**
   - Hostname: `rpi`
   - Username: `pi`
   - Password: `spectro`
   - Configure wireless LAN: ✓
     - SSID: Your WiFi network name (mobile hotspot recommended for field use)
     - Password: Your WiFi password
     - Wireless LAN country: `AU` (or your country)
   - Set locale settings: ✓
     - Timezone: `Australia/Brisbane` (or your timezone)
     - Keyboard layout: `US`
3. **Services Tab:**
   - Enable SSH: ✓ (Use password authentication)
4. Click `Save`
5. Confirm OS customization settings when prompted
6. Flash the image to your microSD card

## First Boot and Connection

### 1. Initial Boot
- Enable your mobile phone hotspot (if using mobile hotspot for WiFi)
- Insert the flashed microSD card into the Raspberry Pi
- Power on the Raspberry Pi
- Wait for the device to connect to your WiFi network

### 2. Find the Pi's IP Address

**Option A: Using hostname (if available)**
```bash
ping rpi.local
```

**Option B: Network scan (if hostname doesn't work)**
```bash
# Check your laptop's IP address when connected to the same network
ifconfig
# Look for your interface (e.g., wlp3s0) and note the network
# Example: inet 10.119.124.83 means network is 10.119.124.0/24

# Scan the network
nmap -sn 10.119.124.0/24
```

### 3. SSH into the Raspberry Pi
```bash
ssh pi@<IP_ADDRESS>
```
Enter password: `spectro`

**Note:** Both your laptop and Raspberry Pi must be on the same network for SSH to work.

## Adding Additional WiFi Networks (Optional)

If you need to add more WiFi networks after initial setup:

1. Insert the microSD card into your computer
2. Navigate to the boot partition and edit the network configuration:
   ```bash
   sudo nano /path/to/boot/50-cloud-init.yaml
   ```
3. Add additional networks to the configuration:
   ```yaml
   network:
       version: 2
       wifis:
           renderer: networkd
           wlan0:
               access-points:
                   existing_network:
                       password: existing_password
                   new_network:
                       password: new_password
               dhcp4: true
               optional: true
   ```
4. Save and safely eject the microSD card
5. Reinsert into the Pi and power on

## Automated Setup

### 1. Download and Run the Setup Script

```bash
# Download the setup script
wget https://raw.githubusercontent.com/yourusername/yourrepo/main/setup_ada.sh

# Make it executable
chmod +x setup_ada.sh

# Run the setup (requires sudo)
sudo ./setup_ada.sh
```

### 2. What the Setup Script Does

The automated setup script handles:

- **System Configuration:**
  - System package updates and installation
  - Swap file configuration (2GB)
  - SPI and I2C interface enabling
  - User group permissions for hardware access

- **Display Setup:**
  - Adafruit PiTFT 2.8" driver installation and configuration
  - Console cursor control permissions for application use
  - Display rotation and framebuffer configuration

- **Python Environment:**
  - Virtual environment creation (`~/pysb-app/pysb_venv/`)
  - Integrated package installation (no separate requirements.txt needed):
    - numpy, pyusb (core dependencies)
    - matplotlib, pygame, pygame-menu (UI)
    - spidev, RPi.GPIO, rpi-lgpio (hardware interfaces)
    - seabreeze[pyseabreeze] (spectrometer support)

- **Hardware Configuration:**
  - DS3231 RTC module setup (if connected)
  - Seabreeze udev rules for spectrometer access
  - Terminal and bash improvements

### 3. Reboot Required

After the setup script completes:

```bash
sudo reboot
```

**Important:** The reboot is required for display drivers, hardware interfaces, and user permissions to take full effect.

## Running Your Application

After reboot, SSH back into the Pi and run your application:

```bash
# Navigate to project directory
cd ~/pysb-app

# Activate the virtual environment
source pysb_venv/bin/activate

# Run your application
python3 main.py
```

## Project File Structure

After setup, your project directory will contain:

```
~/pysb-app/
├── pysb_venv/           # Python virtual environment
├── main.py              # Your main application (copy this file to the Pi)
├── assets/              # Fonts, images, etc. (copy this directory to the Pi)
└── lib/                 # Additional libraries (if needed)
```

## Copying Your Application Files

You'll need to copy your `main.py` and `assets/` directory to the Pi:

```bash
# From your development machine, copy files to the Pi
scp main.py pi@<PI_IP>:~/pysb-app/
scp -r assets/ pi@<PI_IP>:~/pysb-app/
```

## Testing and Verification

### Test Spectrometer Connection
```bash
cd ~/pysb-app
source pysb_venv/bin/activate
python -m seabreeze.cseabreeze_backend ListDevices
```

### Test RTC (if installed)
```bash
sudo hwclock -r
```

### Test Display Cursor Control
```bash
# Disable cursor blinking (should work without permission errors)
echo 0 > /sys/class/graphics/fbcon/cursor_blink
```

### Test SPI Interface
```bash
ls -l /dev/spidev*
```

## Troubleshooting

### Common Issues

1. **SSH Connection Failed:**
   - Ensure both devices are on the same network
   - Try using IP address instead of hostname
   - Check that SSH is enabled in the Pi configuration

2. **Display Not Working:**
   - Ensure reboot was completed after setup
   - Check that the display is properly connected
   - Verify that the PiTFT installer completed successfully

3. **Spectrometer Not Detected:**
   - Check USB connection
   - Verify udev rules: `ls /etc/udev/rules.d/*ocean*`
   - Test with: `lsusb` to see if device is detected

4. **Python Package Import Errors:**
   - Ensure virtual environment is activated
   - Reinstall specific packages: `pip install <package_name>`

### Manual Fixes

If automatic setup fails for any component, you can run individual steps manually:

```bash
cd ~/pysb-app
source pysb_venv/bin/activate

# Reinstall specific Python packages
pip install matplotlib pygame pygame-menu

# Reinstall seabreeze
pip install seabreeze[pyseabreeze]

# Reset udev rules for seabreeze
sudo seabreeze_os_setup
```

## Hardware Notes

- **Power:** Use a quality power supply (2.5A recommended for Pi Zero 2 W with display)
- **microSD:** Use Class 10 or better, avoid cheap/counterfeit cards
- **Display:** Ensure proper connection of the PiTFT ribbon cable
- **RTC:** Connect DS3231 to I2C pins (SDA to GPIO 2, SCL to GPIO 3, VCC to 3.3V, GND to GND)

## Development Tips

- Use `screen` or `tmux` for persistent SSH sessions
- The setup script adds helpful bash aliases and history search
- Log files are typically in `/var/log/` for troubleshooting
- Use `dmesg` to check for hardware detection issues

## License

This setup is designed for educational and research purposes. Please ensure compliance with all applicable licenses for the software components used.

## Raspberry Pi breakout PCB

A custom PCB was built to add a USB-C power input, Blue Robotics waterproof power switch, real time clock (RTC) and battery, leak sensor and button inputs. An I2C and UART breakout was also added just for fun.

This complete schematic and board design can be found in the PCB folder. 

![image](https://github.com/user-attachments/assets/a64ad8f9-ed21-4b6f-b43d-9462d401118d)

![image](https://github.com/user-attachments/assets/7c0f146f-47bb-43a3-b808-453892763aac)

Leak sensor is based on the Blue Robotics leak sensor and uses Blue Robotics SOS leak sensor probes.

![image](https://github.com/user-attachments/assets/3c34cd6f-63d9-44bc-a1fa-a32ff59414d8)

RTC is based on Adafruit's I2C DS3231 module.

![image](https://github.com/user-attachments/assets/7edfee91-a727-4598-ba51-139883b82f8c)

## Power Usage:

Using a 10000mAh battery pack, the Raspberry Pi and spectrometer setup for approximately 10 hours and 18 minutes under the current load conditions.

Current during spectrometer live feed: ~0.6A 
Voltage: 5.099V
Power consumption = Voltage × Current
Power consumption = 5.099V × 0.6A = 3.06W
Energy capacity = 10000mAh × 3.7V ÷ 1000 = 37Wh
Assuming a typical efficiency rate: 85%
Actual available energy = 37Wh × 0.85 = 31.45Wh
Runtime = Available energy ÷ Power consumption
Runtime = 31.45Wh ÷ 3.06W ≈ 10.3 hours



