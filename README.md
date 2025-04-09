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

# Function to handle critical errors
critical_error() {
    echo ""
    echo "ERROR: $1"
    echo "Setup failed. Please fix the issue and run the script again."
    exit 1
}

# Function to handle non-critical errors
warning() {
    echo ""
    echo "WARNING: $1"
    echo "Do you want to continue anyway? (y/n)"
    read -p "Enter choice [y/n]: " continue_choice
    if [ "$continue_choice" != "y" ] && [ "$continue_choice" != "Y" ]; then
        echo "Exiting script."
        exit 1
    fi
}

# Improved package manager check and fix function
handle_package_manager() {
    action=$1
    echo "Checking package manager availability before $action..."
    
    # Check for active package manager processes
    local active_processes=$(pgrep -f "unattended-upgr|apt|dpkg" | grep -v "^$$" || true)
    
    if [ -n "$active_processes" ]; then
        echo ""
        echo "Package manager is currently in use by the following processes:"
        ps -f -p $(echo "$active_processes" | tr '\n' ' ')
        echo ""
        echo "Do you want to:"
        echo "1) Wait for completion (up to 30 seconds)"
        echo "2) Force terminate these processes and proceed"
        echo "3) Exit script"
        read -p "Enter choice [1-3]: " pkg_choice
        
        case $pkg_choice in
            1)
                echo "Waiting up to 30 seconds for package manager to become available..."
                local timeout=30
                local start_time=$(date +%s)
                
                while [ -n "$(pgrep -f 'unattended-upgr|apt|dpkg' | grep -v '^$$')" ]; do
                    echo -n "."
                    sleep 2
                    local current_time=$(date +%s)
                    if (( current_time - start_time > timeout )); then
                        echo ""
                        echo "Timeout reached. Package manager is still busy."
                        echo "Do you want to force terminate package manager processes? (y/n)"
                        read -p "Enter choice [y/n]: " force_choice
                        
                        if [ "$force_choice" = "y" ] || [ "$force_choice" = "Y" ]; then
                            # Fall through to option 2
                            pkg_choice=2
                            break
                        else
                            echo "Exiting script. Please try again later."
                            exit 1
                        fi
                    fi
                done
                
                if [ "$pkg_choice" != "2" ]; then
                    echo " Package manager now available."
                    return 0
                fi
                # Fall through to option 2 if needed
                ;;
            2)
                # Force terminate processes - proceed to next section
                ;;
            3)
                echo "Exiting script. Please try again later."
                exit 1
                ;;
            *)
                echo "Invalid choice. Exiting script."
                exit 1
                ;;
        esac
        
        # Option 2 handling: Force terminate processes
        if [ "$pkg_choice" = "2" ]; then
            echo "Forcefully terminating package manager processes..."
            
            # Kill all apt/dpkg processes
            for pid in $active_processes; do
                process_name=$(ps -p $pid -o comm= 2>/dev/null || echo "unknown")
                echo "Terminating $process_name (PID: $pid)..."
                sudo kill -15 $pid 2>/dev/null || sudo kill -9 $pid 2>/dev/null
            done
            
            # Give processes time to terminate
            sleep 3
            
            # Check if any processes are still running
            active_processes=$(pgrep -f "unattended-upgr|apt|dpkg" | grep -v "^$$" || true)
            if [ -n "$active_processes" ]; then
                echo "WARNING: Some package manager processes could not be terminated:"
                ps -f -p $(echo "$active_processes" | tr '\n' ' ')
                echo "This might cause issues with package installation."
            fi
            
            # Remove lock files
            echo "Removing package manager lock files..."
            sudo rm -f /var/lib/dpkg/lock* /var/lib/apt/lists/lock /var/cache/apt/archives/lock || true
            
            # Repair package system
            echo "Repairing package system..."
            sudo dpkg --configure -a
            
            echo "Package manager should now be available."
            return 0
        fi
    else
        echo "Package manager is available."
        return 0
    fi
}

# Check internet connectivity
check_internet() {
    echo "Checking internet connectivity..."
    if ping -c 1 8.8.8.8 >/dev/null 2>&1; then
        echo "Internet connection available."
        return 0
    else
        echo "No internet connection detected. Many operations will fail without internet."
        echo "Do you want to:"
        echo "1) Try to continue anyway"
        echo "2) Exit the script"
        read -p "Enter choice [1-2]: " net_choice
        
        case $net_choice in
            1)
                echo "Continuing without internet..."
                return 1
                ;;
            2)
                echo "Exiting script. Please connect to the internet and try again."
                exit 1
                ;;
            *)
                echo "Invalid choice. Exiting script."
                exit 1
                ;;
        esac
    fi
}

# Get the actual user even when script is run with sudo
get_actual_user() {
    if [ -n "$SUDO_USER" ]; then
        ACTUAL_USER="$SUDO_USER"
    else
        ACTUAL_USER=$(whoami)
    fi
    ACTUAL_HOME=$(getent passwd "$ACTUAL_USER" | cut -d: -f6)
    
    if [ -z "$ACTUAL_HOME" ]; then
        critical_error "Could not determine the actual user's home directory"
    fi
    
    echo "Setting up for user: $ACTUAL_USER (home: $ACTUAL_HOME)"
}

# Always check date with user
check_date_time() {
    echo "======================================"
    echo "IMPORTANT: Please verify the system date and time"
    echo "Current system date and time is: $(date)"
    echo "Is this correct? (y/n)"
    read -p "Enter choice [y/n]: " date_correct

    if [ "$date_correct" != "y" ] && [ "$date_correct" != "Y" ]; then
        echo "Would you like to:"
        echo "1) Set date/time automatically via NTP (requires internet)"
        echo "2) Set date/time manually"
        echo "3) Exit script to fix date/time yourself"
        read -p "Enter choice [1-3]: " dt_choice
        
        case $dt_choice in
            1)
                echo "Attempting to sync time via NTP..."
                if ! check_internet; then
                    echo "NTP sync requires internet. Switching to manual time setting."
                    dt_choice=2
                else
                    echo "First ensuring NTP service is enabled..."
                    sudo systemctl enable systemd-timesyncd
                    sudo timedatectl set-ntp true
                    
                    # Try to restart timesyncd service
                    echo "Restarting time synchronization service..."
                    sudo systemctl restart systemd-timesyncd
                    
                    # Wait up to 20 seconds for sync
                    echo "Waiting for time synchronization (this may take a moment)..."
                    local synced=false
                    for i in {1..20}; do
                        sleep 1
                        echo -n "."
                        if timedatectl status | grep -q "System clock synchronized: yes"; then
                            echo " Synchronized!"
                            synced=true
                            break
                        fi
                    done
                    
                    echo "Current system date is now: $(date)"
                    if ! $synced; then
                        echo "NTP sync timeout reached."
                    fi
                    
                    echo "Is this date correct now? (y/n)"
                    read -p "Enter choice [y/n]: " date_correct_now
                    
                    if [ "$date_correct_now" != "y" ] && [ "$date_correct_now" != "Y" ]; then
                        echo "NTP sync failed or date is still incorrect. Switching to manual time setting."
                        dt_choice=2
                    fi
                fi
                ;;
            2)
                # Fallthrough from case 1 if NTP fails
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
        
        # Handle manual time setting (case 2 or fallthrough from case 1)
        if [ "$dt_choice" = "2" ]; then
            echo "Please enter the current date in the format YYYY-MM-DD:"
            read -p "Date (YYYY-MM-DD): " manual_date
            echo "Please enter the current time in the format HH:MM:SS (24-hour):"
            read -p "Time (HH:MM:SS): " manual_time
            sudo timedatectl set-ntp false
            if ! sudo timedatectl set-time "${manual_date} ${manual_time}"; then
                critical_error "Failed to set date/time. Please ensure the format is correct."
            fi
            echo "Date and time set to: $(date)"
        fi
    fi
}

# Configure needrestart
configure_needrestart() {
    echo "Checking needrestart configuration..."
    if [ -f /etc/needrestart/needrestart.conf ]; then
        if grep -q "#\$nrconf{restart} = 'i';" /etc/needrestart/needrestart.conf && ! grep -q "\$nrconf{restart} = 'a';" /etc/needrestart/needrestart.conf; then
            echo "Configuring needrestart to automatic mode..."
            sudo sed -i 's/#$nrconf{restart} = '"'"'i'"'"';/$nrconf{restart} = '"'"'a'"'"';/g' /etc/needrestart/needrestart.conf
        else
            echo "Needrestart already configured for automatic mode."
        fi
    else
        echo "Needrestart configuration not found. Skipping this step."
    fi
}

# Configure swap
configure_swap() {
    echo "Checking swap configuration..."
    if [ ! -f /swapfile ] || [ "$(stat -c %s /swapfile 2>/dev/null || echo 0)" -lt "1000000000" ]; then
        echo "Configuring additional swap space..."
        # Remove existing swap if too small
        if [ -f /swapfile ]; then
            sudo swapoff /swapfile 2>/dev/null || true
            sudo rm /swapfile
        fi
        echo "Allocating 1GB swap file (this may take a moment)..."
        if ! sudo fallocate -l 1G /swapfile 2>/dev/null; then
            echo "fallocate failed, trying dd method instead..."
            sudo dd if=/dev/zero of=/swapfile bs=1M count=1024
        fi
        sudo chmod 600 /swapfile
        sudo mkswap /swapfile
        sudo swapon /swapfile
        # Make swap permanent if not already in fstab
        if ! grep -q "/swapfile swap swap defaults 0 0" /etc/fstab; then
            echo "/swapfile swap swap defaults 0 0" | sudo tee -a /etc/fstab
        fi
        echo "Swap space configured successfully."
        free -h
    else
        echo "Swap already configured."
        free -h
    fi
}

# Enable SPI
enable_spi() {
    echo "Checking SPI interface configuration..."
    local config_file=""
    
    # Find the correct config file location (differences between Raspberry Pi OS and Ubuntu)
    if [ -f /boot/config.txt ]; then
        config_file="/boot/config.txt"
    elif [ -f /boot/firmware/config.txt ]; then
        config_file="/boot/firmware/config.txt"
    else
        warning "Could not find config.txt in /boot or /boot/firmware. SPI cannot be enabled."
        return
    fi
    
    if ! grep -q "^dtparam=spi=on" "$config_file"; then
        echo "Enabling SPI interface..."
        # Comment out any existing SPI setting that might be disabled
        sudo sed -i 's/^dtparam=spi=off/#dtparam=spi=off/g' "$config_file"
        # Add SPI enabled setting
        echo "dtparam=spi=on" | sudo tee -a "$config_file"
        echo "SPI interface enabled. A reboot will be required for this to take effect."
    else
        echo "SPI interface already enabled in $config_file."
    fi
}

# Configure terminal experience
configure_terminal() {
    echo "Checking terminal history configuration..."
    if [ ! -f "$ACTUAL_HOME/.inputrc" ] || ! grep -q "history-search-backward" "$ACTUAL_HOME/.inputrc"; then
        echo "Setting up terminal history search with arrow keys..."
        cat > "$ACTUAL_HOME/.inputrc" << 'EOL'
# Respect default shortcuts.
$include /etc/inputrc

## arrow up
"\e[A]:history-search-backward
## arrow down
"\e[B]:history-search-forward
EOL
        # Ensure the file is owned by the actual user
        sudo chown $ACTUAL_USER:$ACTUAL_USER "$ACTUAL_HOME/.inputrc"
        echo "Terminal history search configured."
    else
        echo "Terminal history search already configured."
    fi
}

# Improved package installation function
install_package() {
    package=$1
    echo "Installing package: $package..."
    
    # First make sure package manager is available
    handle_package_manager "installing $package"
    
    # Now try to install the package
    if ! sudo DEBIAN_FRONTEND=noninteractive apt-get install -y $package; then
        warning "Failed to install $package. Some features may not work correctly."
        return 1
    fi
    
    echo "$package installed successfully."
    return 0
}

# Install required packages
install_packages() {
    echo "Updating package lists..."
    
    # Make sure we can update
    handle_package_manager "updating package lists"
    
    if ! sudo apt-get update -y; then
        warning "Failed to update package lists. Some packages may not install correctly."
    fi
    
    echo "Installing recommended packages..."
    recommended_packages=(
      "git-all" 
      "build-essential" 
      "libusb-dev"
    )
    
    for pkg in "${recommended_packages[@]}"; do
        install_package "$pkg"
    done
    
    echo "Installing additional helpful packages..."
    additional_packages=(
      "pkg-config" 
      "libusb-1.0-0-dev" 
      "libusb-1.0-0" 
      "libudev-dev" 
      "python3-pip" 
      "python3-dev" 
      "python3-setuptools" 
      "python3-wheel" 
      "vim" 
      "feh"
    )
    
    for pkg in "${additional_packages[@]}"; do
        install_package "$pkg"
    done
    
    # Verify libusb installation
    echo "Verifying libusb installation..."
    if ! pkg-config --list-all | grep -q libusb; then
        warning "libusb not found by pkg-config. This may cause issues with pyseabreeze installation."
        echo "libusb installation details:"
        sudo find /usr -name "*libusb*" 2>/dev/null
        sudo ldconfig -p | grep libusb
    else
        echo "libusb found by pkg-config:"
        pkg-config --list-all | grep libusb
    fi
}

# Add user to groups
setup_user_permissions() {
    echo "Setting up user permissions..."
    groups_to_add=("video" "i2c" "gpio" "spi")
    
    for group in "${groups_to_add[@]}"; do
        if getent group $group >/dev/null; then
            if ! groups $ACTUAL_USER | grep -q "\b$group\b"; then
                sudo usermod -aG $group $ACTUAL_USER
                echo "Added user to $group group."
            else
                echo "User already in $group group."
            fi
        else
            echo "Group $group does not exist. Skipping."
        fi
    done
}

# Update Python tools
update_python_tools() {
    echo "Updating pip and setuptools..."
    
    # First upgrade pip for the system (needed for some dependencies)
    if ! sudo pip3 install --upgrade pip setuptools wheel; then
        warning "Failed to upgrade system pip. Will continue with user pip upgrade."
    fi
    
    # Now upgrade pip for the user
    if ! sudo -u $ACTUAL_USER pip3 install --user --upgrade pip setuptools wheel; then
        warning "Failed to upgrade user pip. Some Python packages may not install correctly."
    fi
}

# Install Python packages
install_python_packages() {
    echo "Installing Python packages for user $ACTUAL_USER..."
    
    # Install basic packages first
    echo "Installing basic Python packages..."
    basic_packages=("matplotlib" "pygame" "spidev" "RPi.GPIO")
    for package in "${basic_packages[@]}"; do
        echo "Installing $package..."
        if ! sudo -u $ACTUAL_USER pip3 install --user $package; then
            warning "Failed to install $package. Some functionality may not work correctly."
        fi
    done
    
    # Special handling for seabreeze due to libusb dependency
    echo "Installing seabreeze package with special handling..."
    
    # First ensure all required system dependencies are installed 
    handle_package_manager "installing libusb dependencies"
    if ! sudo apt-get install -y libusb-1.0-0-dev libusb-1.0-0 libudev-dev; then
        warning "Failed to install libusb dependencies. Seabreeze may not work correctly."
    fi
    
    # Export PKG_CONFIG_PATH to help find libusb
    export PKG_CONFIG_PATH="/usr/lib/arm-linux-gnueabihf/pkgconfig:/usr/lib/pkgconfig:/usr/share/pkgconfig"
    
    # Try installing seabreeze with detailed output
    echo "Installing seabreeze with verbose output to diagnose any issues..."
    if ! sudo -u $ACTUAL_USER pip3 install --user --verbose seabreeze[pyseabreeze]; then
        echo "First attempt to install seabreeze failed, trying alternative approach..."
        
        # Try with specific build flags
        if ! sudo -u $ACTUAL_USER CFLAGS="-I/usr/include/libusb-1.0" pip3 install --user --verbose seabreeze[pyseabreeze]; then
            warning "Failed to install seabreeze. Ocean Optics devices may not work correctly."
        fi
    fi
    
    # Try to install displayhatmini
    echo "Installing displayhatmini..."
    if ! sudo -u $ACTUAL_USER pip3 install --user displayhatmini; then
        warning "Failed to install displayhatmini. Display HAT Mini may not work correctly."
    fi
    
    # Add the user's local bin to their PATH if not already there
    if ! grep -q 'PATH="\$HOME/.local/bin:\$PATH"' "$ACTUAL_HOME/.bashrc"; then
        echo 'PATH="$HOME/.local/bin:$PATH"' | sudo tee -a "$ACTUAL_HOME/.bashrc"
        echo "Added user's local bin directory to PATH."
    fi
}

# Setup seabreeze udev rules
setup_seabreeze() {
    echo "Checking seabreeze udev rules..."
    if [ ! -f /etc/udev/rules.d/10-oceanoptics.rules ] || ! grep -q "Ocean Optics" /etc/udev/rules.d/10-oceanoptics.rules; then
        echo "Setting up seabreeze udev rules..."
        # Run seabreeze setup command with the actual user
        if which seabreeze_os_setup >/dev/null 2>&1; then
            if ! sudo seabreeze_os_setup; then
                warning "Failed to set up seabreeze udev rules. Ocean Optics devices may not work correctly."
            fi
        elif [ -f "$ACTUAL_HOME/.local/bin/seabreeze_os_setup" ]; then
            if ! sudo "$ACTUAL_HOME/.local/bin/seabreeze_os_setup"; then
                warning "Failed to set up seabreeze udev rules. Ocean Optics devices may not work correctly."
            fi
        else
            warning "seabreeze_os_setup command not found. Ocean Optics devices may not work correctly."
        fi
    else
        echo "Seabreeze udev rules already configured."
    fi
}

# Verify SPI is properly configured
verify_spi() {
    echo "Verifying SPI configuration..."
    if ! ls -l /dev/spidev* 2>/dev/null; then
        echo "SPI devices not found. This is normal if you haven't rebooted yet."
        echo "After rebooting, you should see devices at /dev/spidev*"
    else
        echo "SPI devices found:"
        ls -l /dev/spidev*
    fi
}

# Verify Python packages installed correctly
verify_python_packages() {
    echo "Verifying Python package installation..."
    
    # Check each package separately to identify specific issues
    packages=("matplotlib" "seabreeze" "pygame" "spidev" "RPi.GPIO")
    missing=()
    
    for package in "${packages[@]}"; do
        if ! sudo -u $ACTUAL_USER python3 -c "import $package" >/dev/null 2>&1; then
            missing+=("$package")
        fi
    done
    
    if [ ${#missing[@]} -gt 0 ]; then
        warning "The following Python packages could not be imported: ${missing[*]}"
        echo "You may need to reboot for some packages to work properly."
        echo "After reboot, you can verify packages with:"
        for package in "${missing[@]}"; do
            echo "python3 -c 'import $package'"
        done
    else
        echo "All Python packages verified successfully."
    fi
}

# Main function to run the script
main() {
    echo "====================================="
    echo "Raspberry Pi Zero 2W Setup Script"
    echo "====================================="
    
    # Get actual user information
    get_actual_user
    
    # Check date/time first
    check_date_time
    
    # Check internet connectivity
    check_internet
    
    # Configure system settings
    configure_needrestart
    configure_swap
    enable_spi
    configure_terminal
    
    # Install and configure software
    install_packages
    setup_user_permissions
    update_python_tools
    install_python_packages
    setup_seabreeze
    
    # Verify configuration
    verify_spi
    verify_python_packages
    
    echo ""
    echo "====================================="
    echo "Setup complete! A reboot is required for all changes to take effect."
    echo "Please run: sudo reboot"
    echo "====================================="
    echo ""
    echo "After reboot, test your setup with:"
    echo "python3 -c 'import matplotlib; import seabreeze; import pygame; print(\"All packages working!\")'"
    echo "====================================="
}

# Run the main function
main
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




