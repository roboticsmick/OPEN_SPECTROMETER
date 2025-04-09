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
# Exit immediately if a command exits with a non-zero status.
set -e

# === Configuration ===
# Define the path for the Python virtual environment
DEFAULT_VENV_PATH="~/seabreeze_env"
# Define the desired swap file size (e.g., "1G", "2G")
SWAP_SIZE="1G"
# Define the timeout in seconds to wait for the package manager
PKG_MANAGER_TIMEOUT=120

# === Script Variables ===
ACTUAL_USER=""
ACTUAL_HOME=""
VENV_PATH="" # Will be resolved later

# === Helper Functions ===

# Function to print error messages and exit
critical_error() {
    echo "" >&2
    echo "ERROR: $1" >&2
    echo "Setup failed. Please fix the issue and run the script again." >&2
    exit 1
}

# Function to print warning messages
warning() {
    echo "" >&2
    echo "WARNING: $1" >&2
    # Decide if script should continue or prompt user - for now, just warn
    # To make it interactive again, uncomment the lines below
    # echo "Do you want to continue anyway? (y/n)"
    # read -p "Enter choice [y/n]: " continue_choice
    # if [[ "$continue_choice" != "y" && "$continue_choice" != "Y" ]]; then
    #     echo "Exiting script."
    #     exit 1
    # fi
}

# Function to check if script is run as root
check_root() {
    if [ "$(id -u)" -ne 0 ]; then
        critical_error "This script must be run with sudo or as root."
    fi
}

# Get the actual user even when script is run with sudo
get_actual_user() {
    if [ -n "$SUDO_USER" ]; then
        ACTUAL_USER="$SUDO_USER"
    else
        # If not run with sudo, check if already root, otherwise use current user
        if [ "$(id -u)" -eq 0 ]; then
             # Attempt to find a non-root user with a valid home directory / UID >= 1000
             ACTUAL_USER=$(awk -F: '($3 >= 1000) && ($7 !~ /nologin|false/) && ($6 != "") { print $1; exit }' /etc/passwd)
             if [ -z "$ACTUAL_USER" ]; then
                 critical_error "Running as root without sudo, and could not determine a standard user. Please run with 'sudo'."
             fi
             echo "Warning: Running as root without sudo. Assuming user is '$ACTUAL_USER'."
        else
             ACTUAL_USER=$(whoami)
        fi
    fi

    ACTUAL_HOME=$(getent passwd "$ACTUAL_USER" | cut -d: -f6)

    if [ -z "$ACTUAL_HOME" ] || [ ! -d "$ACTUAL_HOME" ]; then
        critical_error "Could not determine a valid home directory for user '$ACTUAL_USER'."
    fi

    # Resolve the VENV_PATH relative to the user's home directory
    VENV_PATH=$(eval echo "$DEFAULT_VENV_PATH")

    echo "Running setup for user: $ACTUAL_USER (home: $ACTUAL_HOME)"
    echo "Python virtual environment will be created at: $VENV_PATH"
}

# Check internet connectivity
check_internet() {
    echo "Checking internet connectivity..."
    if ping -c 1 8.8.8.8 >/dev/null 2>&1; then
        echo "Internet connection available."
    else
        warning "No internet connection detected. Network operations (updates, downloads) will fail."
        # Exit here if internet is strictly required
        # critical_error "Internet connection is required. Please connect and try again."
    fi
}

# Wait for apt/dpkg locks to be released
wait_for_apt_lock() {
    echo "Checking for package manager locks..."
    local lock_files=( "/var/lib/dpkg/lock" "/var/lib/dpkg/lock-frontend" "/var/lib/apt/lists/lock" "/var/cache/apt/archives/lock" )
    local start_time=$(date +%s)
    local current_time

    while true; do
        local locked=0
        # Check lock files using fuser
        for lock_file in "${lock_files[@]}"; do
            if sudo fuser "$lock_file" >/dev/null 2>&1; then
                echo "Package manager lock file found: $lock_file. Waiting..."
                locked=1
                break
            fi
        done

        # Also check for running apt/dpkg processes (less reliable than lock files)
        if pgrep -f "apt|dpkg" > /dev/null && [ $locked -eq 0 ]; then
             echo "Waiting for package manager processes (apt/dpkg) to finish..."
             locked=1
        fi

        if [ $locked -eq 0 ]; then
            echo "Package manager is available."
            return 0
        fi

        current_time=$(date +%s)
        if (( current_time - start_time > PKG_MANAGER_TIMEOUT )); then
            critical_error "Package manager lock persists after ${PKG_MANAGER_TIMEOUT} seconds. Please investigate manually (e.g., 'sudo fuser /var/lib/dpkg/lock*') and try again."
        fi

        sleep 5
        echo -n "."
    done
}

# Check and optionally set date/time
check_date_time() {
    echo "======================================"
    echo "Verifying System Date and Time"
    echo "Current system time: $(date)"
    echo "Is this correct? (y/N)"
    read -p "Enter choice [y/N]: " date_correct

    if [[ "$date_correct" != "y" && "$date_correct" != "Y" ]]; then
        echo "Attempting to sync time via NTP (requires internet)..."
        if ping -c 1 8.8.8.8 >/dev/null 2>&1; then
            echo "Ensuring NTP service (systemd-timesyncd) is active..."
            sudo systemctl enable systemd-timesyncd
            sudo systemctl start systemd-timesyncd
            sudo timedatectl set-ntp true

            echo "Waiting up to 30 seconds for synchronization..."
            local synced=false
            for i in {1..15}; do # Check every 2 seconds
                sleep 2
                echo -n "."
                if timedatectl status | grep -q "System clock synchronized: yes"; then
                    echo " Synchronized!"
                    synced=true
                    break
                fi
            done

            if $synced; then
                echo "Time successfully synchronized via NTP."
                echo "New system time: $(date)"
            else
                warning "Could not automatically sync time via NTP. Time might be incorrect."
                echo "You can set the time manually using: sudo timedatectl set-time 'YYYY-MM-DD HH:MM:SS'"
            fi
        else
            warning "Cannot sync time via NTP (no internet). Time might be incorrect."
            echo "You can set the time manually using: sudo timedatectl set-time 'YYYY-MM-DD HH:MM:SS'"
        fi
        # Add manual setting option here if desired, but NTP is generally preferred.
    fi
}

# Configure swap space
configure_swap() {
    echo "======================================"
    echo "Configuring Swap Space (Target: ${SWAP_SIZE})"
    local swap_needed=false
    local current_swap_total=$(grep SwapTotal /proc/meminfo | awk '{print $2}') # Value in kB
    local target_swap_kb=$(numfmt --from=iec $SWAP_SIZE | awk '{print $1/1024}')

    if [ -f /swapfile ]; then
        local current_swapfile_size=$(sudo stat -c %s /swapfile 2>/dev/null || echo 0)
        local target_swapfile_bytes=$(numfmt --from=iec $SWAP_SIZE)
        echo "Existing swap file found (/swapfile, size: $(numfmt --to=iec $current_swapfile_size))."
        if [ "$current_swapfile_size" -lt "$target_swapfile_bytes" ]; then
            echo "Swap file is smaller than target size ${SWAP_SIZE}. Recreating."
            swap_needed=true
            echo "Disabling existing swap file..."
            sudo swapoff /swapfile || true # Ignore error if not active
            sudo rm -f /swapfile
        else
             echo "Existing swap file is sufficient."
             # Ensure it's enabled and in fstab
             if ! swapon --show | grep -q /swapfile; then
                 echo "Enabling swap file..."
                 sudo swapon /swapfile
             fi
             if ! grep -q '^[[:space:]]*/swapfile[[:space:]]' /etc/fstab; then
                 echo "Adding swap file to /etc/fstab..."
                 echo "/swapfile none swap sw 0 0" | sudo tee -a /etc/fstab
             fi
        fi
    elif (( current_swap_total < target_swap_kb / 2 )); then # Heuristic: If total swap is less than half target, add the file
        echo "No /swapfile found and total swap is low. Creating swap file."
        swap_needed=true
    else
        echo "Sufficient swap space detected or /swapfile not used. Skipping creation."
        free -h # Show current memory/swap status
        return 0
    fi

    if [ "$swap_needed" = true ]; then
        echo "Allocating ${SWAP_SIZE} swap file at /swapfile (this may take a while)..."
        # Use fallocate if available (faster), otherwise use dd
        if sudo fallocate -l "${SWAP_SIZE}" /swapfile; then
            echo "Swap file allocated using fallocate."
        else
            echo "fallocate failed (maybe filesystem doesn't support it), using dd instead (slower)..."
            # Calculate count for dd (Size in MiB)
            local size_mb=$(numfmt --from=iec --to=si $SWAP_SIZE | sed 's/M//')
             if ! sudo dd if=/dev/zero of=/swapfile bs=1M count="$size_mb" status=progress; then
                 critical_error "Failed to create swap file using dd."
             fi
        fi

        echo "Setting permissions..."
        sudo chmod 600 /swapfile
        echo "Formatting swap file..."
        sudo mkswap /swapfile
        echo "Enabling swap file..."
        sudo swapon /swapfile

        # Make swap permanent if not already in fstab
        if ! grep -q '^[[:space:]]*/swapfile[[:space:]]' /etc/fstab; then
            echo "Adding swap file to /etc/fstab..."
            echo "/swapfile none swap sw 0 0" | sudo tee -a /etc/fstab
        fi
        echo "Swap space configured successfully."
    fi
    free -h # Show current memory/swap status
}

# Configure needrestart for non-interactive updates
configure_needrestart() {
    echo "======================================"
    echo "Configuring needrestart for automatic restarts..."
    if [ -f /etc/needrestart/needrestart.conf ]; then
        # Check if the line is commented out or set to interactive/list
        if grep -q -E "^\s*#?\s*\$nrconf{restart}\s*=\s*'[il]'" /etc/needrestart/needrestart.conf; then
             echo "Setting needrestart to automatic mode..."
             sudo sed -i "s:^\s*#\?\s*\$nrconf{restart}\s*=\s*'[il]':\$nrconf{restart} = 'a':" /etc/needrestart/needrestart.conf
        else
             echo "Needrestart already configured or manually set."
        fi
    else
        echo "Needrestart config file not found, creating one with automatic mode."
        echo "\$nrconf{restart} = 'a';" | sudo tee /etc/needrestart/needrestart.conf
    fi
     # Also configure apt to be non-interactive if neededrestart prompts during apt installs
     echo 'APT::Get::Assume-Yes "true";' | sudo tee /etc/apt/apt.conf.d/99assume-yes
     echo 'DPkg::Options { "--force-confdef"; "--force-confold"; }' | sudo tee /etc/apt/apt.conf.d/90local-dpkg-options
}

# Update system packages
update_system() {
    echo "======================================"
    echo "Updating System Packages"
    wait_for_apt_lock
    echo "Running apt update..."
    if ! sudo apt-get update; then
        warning "apt update failed. Package lists may be outdated."
    fi

    wait_for_apt_lock
    echo "Running apt upgrade..."
    # Use non-interactive frontend
    if ! sudo DEBIAN_FRONTEND=noninteractive apt-get upgrade -y; then
        warning "apt upgrade failed. System may not be fully up-to-date."
    fi
}

# Install required system packages
install_system_packages() {
    echo "======================================"
    echo "Installing System Packages"
    # Define packages needed
    local packages=(
        git         # git-all is a meta-package, git is usually sufficient
        build-essential
        pkg-config
        libusb-1.0-0-dev # Specific dev package for libusb 1.0
        libudev-dev
        python3-pip     # For system pip3, primarily to install venv if needed
        python3-dev
        python3-venv    # Crucial for creating virtual environments
        vim             # User preference
        feh             # User preference
        # Add any other essential system packages here
    )

    echo "Installing: ${packages[@]}"
    wait_for_apt_lock
    if ! sudo DEBIAN_FRONTEND=noninteractive apt-get install -y "${packages[@]}"; then
        critical_error "Failed to install one or more essential system packages. Cannot continue."
    fi
    echo "System packages installed successfully."

     # Verify libusb can be found by pkg-config
    echo "Verifying libusb installation..."
    if ! pkg-config --exists libusb-1.0; then
        warning "pkg-config cannot find libusb-1.0. Seabreeze installation might fail."
        echo "Attempting to find libusb manually:"
        find /usr/lib /usr/local/lib -name 'libusb-1.0.pc' 2>/dev/null || echo "libusb-1.0.pc not found."
        sudo ldconfig -p | grep libusb-1.0 || echo "libusb-1.0 not found by ldconfig."
    else
        echo "libusb-1.0 found by pkg-config:"
        pkg-config --modversion libusb-1.0
    fi
}

# Enable SPI interface
enable_spi() {
    echo "======================================"
    echo "Enabling SPI Interface"
    local config_file=""

    # Ubuntu on Pi often uses /boot/firmware/config.txt
    if [ -f /boot/firmware/config.txt ]; then
        config_file="/boot/firmware/config.txt"
    elif [ -f /boot/config.txt ]; then # Fallback for Raspberry Pi OS style
        config_file="/boot/config.txt"
    else
        warning "Could not find config.txt in /boot/firmware or /boot. Cannot enable SPI automatically."
        return
    fi
    echo "Using config file: $config_file"

    if grep -q -E "^\s*dtparam=spi=on" "$config_file"; then
        echo "SPI interface already enabled in $config_file."
    else
        echo "Enabling SPI interface..."
        # Remove or comment out any existing spi=off line
        sudo sed -i -E 's:^\s*(dtparam=spi=off):#\1:' "$config_file"
        # Add spi=on if not present
        if ! grep -q -E "^\s*dtparam=spi=on" "$config_file"; then
            echo "dtparam=spi=on" | sudo tee -a "$config_file" > /dev/null
        fi
        echo "SPI interface enabled. A reboot is required for changes to take effect."
    fi
}

# Add user to necessary groups
setup_user_permissions() {
    echo "======================================"
    echo "Setting Up User Permissions for Hardware Access"
    local groups_to_add=("video" "i2c" "gpio" "spi" "dialout") # dialout often needed for serial/USB devices

    for group in "${groups_to_add[@]}"; do
        if getent group "$group" >/dev/null; then
            if groups "$ACTUAL_USER" | grep -q -w "$group"; then
                echo "User '$ACTUAL_USER' already in group '$group'."
            else
                echo "Adding user '$ACTUAL_USER' to group '$group'..."
                if ! sudo usermod -aG "$group" "$ACTUAL_USER"; then
                     warning "Failed to add user '$ACTUAL_USER' to group '$group'."
                else
                     echo "User added to '$group'. Changes take effect on next login/reboot."
                fi
            fi
        else
            echo "Group '$group' does not exist on this system. Skipping."
        fi
    done
}

# Configure terminal history search
configure_terminal() {
    echo "======================================"
    echo "Configuring Terminal History Search"
    local inputrc_path="$ACTUAL_HOME/.inputrc"

    if [ -f "$inputrc_path" ] && grep -q "history-search-backward" "$inputrc_path"; then
        echo "Terminal history search already configured in $inputrc_path."
    else
        echo "Setting up terminal history search (up/down arrows) in $inputrc_path..."
        # Create or overwrite the file, ensuring defaults are included first
        {
            echo '# Include system defaults'
            echo '$include /etc/inputrc'
            echo ''
            echo '# History search with arrows'
            echo '"\e[A": history-search-backward'
            echo '"\e[B": history-search-forward'
        } | sudo -u "$ACTUAL_USER" tee "$inputrc_path" > /dev/null

        # Ensure ownership is correct (tee might run as root initially)
        sudo chown "$ACTUAL_USER:$ACTUAL_USER" "$inputrc_path"
        echo "Terminal history configured. Changes take effect in new shell sessions."
    fi
}

# Setup Python Virtual Environment and install packages
setup_python_venv() {
    echo "======================================"
    echo "Setting Up Python Virtual Environment at $VENV_PATH"

    # Create venv directory if it doesn't exist, as the user
    if [ ! -d "$VENV_PATH" ]; then
        echo "Creating virtual environment..."
        if ! sudo -u "$ACTUAL_USER" python3 -m venv "$VENV_PATH"; then
            critical_error "Failed to create Python virtual environment at '$VENV_PATH'."
        fi
        echo "Virtual environment created."
    else
        echo "Virtual environment directory already exists."
    fi

    # Define Python packages to install within the venv
    # Note: RPi.GPIO might require root for direct hardware access outside groups,
    # but installation should be in venv.
    local python_packages=(
        "wheel"             # Good practice to have wheel installed
        "setuptools --upgrade" # Ensure setuptools is up-to-date within venv
        "pip --upgrade"     # Ensure pip is up-to-date within venv
        "matplotlib"
        "pygame"
        "spidev"            # For SPI communication
        "RPi.GPIO"          # For GPIO control
        "seabreeze[pyseabreeze]" # Core requirement
        "displayhatmini"    # Specific hardware package
        # Add other required Python packages here
    )

    echo "Installing Python packages into virtual environment:"
    for package in "${python_packages[@]}"; do
        echo "Installing $package..."
        # Run pip install as the user, using the venv's pip
        # Use --no-cache-dir to potentially avoid issues with corrupted cache
        if ! sudo -u "$ACTUAL_USER" "$VENV_PATH/bin/python" -m pip install --no-cache-dir $package; then
            # Seabreeze can be tricky, provide more specific warning
            if [[ "$package" == "seabreeze[pyseabreeze]" ]]; then
                 warning "Failed to install $package. This often relates to missing 'libusb-1.0-dev' or 'libudev-dev' (check earlier steps) or pkg-config issues."
                 echo "You might need to activate the venv ('source $VENV_PATH/bin/activate') and try installing manually ('pip install $package --verbose') to diagnose."
            else
                 warning "Failed to install Python package '$package' into the virtual environment."
            fi
            # Decide whether to continue or exit based on package importance
            # if [[ "$package" == "seabreeze[pyseabreeze]" ]]; then critical_error "Seabreeze failed to install."; fi
        fi
    done

    echo "Python packages installation process finished."
    echo "Remember to activate the environment before running your scripts:"
    echo "  source $VENV_PATH/bin/activate"
    echo "Then run your python script: python your_script.py"
    echo "To deactivate: deactivate"

    # Add activation hint to .bashrc if not already present
    local bashrc_path="$ACTUAL_HOME/.bashrc"
    local activation_hint="echo 'Python venv for seabreeze available at: source ${VENV_PATH}/bin/activate'"
    if [ -f "$bashrc_path" ] && ! grep -q "$activation_hint" "$bashrc_path"; then
         echo "Adding venv activation hint to $bashrc_path..."
         echo "" | sudo -u "$ACTUAL_USER" tee -a "$bashrc_path" > /dev/null
         echo "# Hint for activating the Python virtual environment for seabreeze project" | sudo -u "$ACTUAL_USER" tee -a "$bashrc_path" > /dev/null
         echo "$activation_hint" | sudo -u "$ACTUAL_USER" tee -a "$bashrc_path" > /dev/null
         sudo chown "$ACTUAL_USER:$ACTUAL_USER" "$bashrc_path"
    fi
}

# Setup seabreeze udev rules
setup_seabreeze_udev() {
    echo "======================================"
    echo "Setting Up Seabreeze udev Rules"
    local rules_file="/etc/udev/rules.d/10-oceanoptics.rules"
    local seabreeze_setup_cmd="$VENV_PATH/bin/seabreeze_os_setup"

    if [ -f "$rules_file" ]; then
        echo "Seabreeze udev rules file already exists ($rules_file). Skipping automatic setup."
        echo "If you have connection problems, you may need to remove this file and re-run the script,"
        echo "or manually run: sudo $seabreeze_setup_cmd"
        return
    fi

    if [ ! -x "$seabreeze_setup_cmd" ]; then
        warning "seabreeze_os_setup command not found or not executable in the venv ($seabreeze_setup_cmd)."
        echo "Cannot automatically set up udev rules. Ocean Optics devices might require manual udev rule configuration."
        echo "You might need to find the rules file in the seabreeze source or documentation."
        return
    fi

    echo "Running seabreeze_os_setup to install udev rules..."
    if ! sudo "$seabreeze_setup_cmd"; then
        warning "Failed to execute seabreeze_os_setup. udev rules may not be correctly installed."
    else
        echo "Seabreeze udev rules should be installed. Reloading udev rules..."
        sudo udevadm control --reload-rules
        sudo udevadm trigger
        echo "udev rules reloaded. Device permissions should apply on plug-in."
    fi
}

# Verify installations
verify_setup() {
    echo "======================================"
    echo "Verifying Setup (Basic Checks)"

    # 1. Verify SPI device
    echo "Checking for SPI device nodes..."
    if ls /dev/spidev* >/dev/null 2>&1; then
        echo "SPI devices found:"
        ls -l /dev/spidev*
    else
        echo "SPI device nodes (/dev/spidev*) not found. This is expected until after a reboot."
    fi

    # 2. Verify Python packages in venv
    echo "Checking Python package imports within the virtual environment..."
    local python_check_packages=("matplotlib" "pygame" "spidev" "RPi.GPIO" "seabreeze")
    local failed_imports=()
    for package in "${python_check_packages[@]}"; do
        if sudo -u "$ACTUAL_USER" "$VENV_PATH/bin/python" -c "import $package" >/dev/null 2>&1; then
            echo "  - $package: OK"
        else
            echo "  - $package: FAILED to import"
            failed_imports+=("$package")
        fi
    done

    if [ ${#failed_imports[@]} -gt 0 ]; then
        warning "Some Python packages failed to import: ${failed_imports[*]}"
        echo "This could be due to installation issues or because a reboot is required for hardware access groups (like spi, gpio) to take effect."
    else
        echo "All checked Python packages imported successfully within the venv."
    fi
}


# === Main Script Logic ===
main() {
    echo "====================================="
    echo "Starting Raspberry Pi Zero 2 W Setup Script"
    echo "Target OS: Ubuntu 22.04 LTS Server"
    echo "====================================="

    check_root
    get_actual_user

    check_date_time
    check_internet # Warns only

    # --- System Configuration ---
    configure_swap
    configure_needrestart # Do before apt operations

    # --- System Updates & Packages ---
    update_system
    install_system_packages # Installs build tools, python3-venv, libusb-dev etc.

    # --- Hardware & Permissions ---
    enable_spi
    setup_user_permissions # Add user to spi, gpio, etc.

    # --- User Environment ---
    configure_terminal

    # --- Python Environment & Packages ---
    setup_python_venv # Creates venv and installs python packages inside it

    # --- Application Specific Setup ---
    setup_seabreeze_udev # Needs venv to be setup first

    # --- Verification ---
    verify_setup

    echo ""
    echo "====================================="
    echo "Setup script finished!"
    echo ""
    echo "IMPORTANT RECOMMENDATIONS:"
    echo "1. A REBOOT is strongly recommended for all changes (groups, SPI, swap, kernel updates) to fully apply:"
    echo "   sudo reboot"
    echo ""
    echo "2. After rebooting, ACTIVATE the Python virtual environment before running your code:"
    echo "   source $VENV_PATH/bin/activate"
    echo ""
    echo "3. Test your core Python imports again within the activated environment:"
    echo "   python -c 'import matplotlib; import pygame; import spidev; import RPi.GPIO; import seabreeze; print(\"Core packages imported successfully!\")'"
    echo ""
    echo "4. To check seabreeze device detection (after activating venv & plugging in device):"
    echo "   python -m seabreeze.cseabreeze_backend ListDevices"
    echo "====================================="
}

# Execute the main function
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




