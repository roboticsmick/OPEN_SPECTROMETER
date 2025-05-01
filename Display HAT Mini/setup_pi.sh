#!/bin/bash
# Exit immediately if a command exits with a non-zero status.
set -e

# === Configuration ===
# Define the name for the main project directory relative to the user's home
PROJECT_DIR_NAME="pysb-app"
# Define the name for the virtual environment directory *inside* the project directory
VENV_DIR_NAME="venv"
# Define the desired swap file size (e.g., "1G", "2G")
SWAP_SIZE="1G"
# Define the timeout in seconds to wait for the package manager
PKG_MANAGER_TIMEOUT=120

# === Script Variables ===
ACTUAL_USER=""
ACTUAL_HOME=""
PROJECT_DIR_PATH="" # Will be constructed later (e.g., /home/pi/pysb-app)
VENV_PATH=""        # Will be constructed later (e.g., /home/pi/pysb-app/venv)

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
        if [ "$(id -u)" -eq 0 ]; then
             ACTUAL_USER=$(awk -F: '($3 >= 1000) && ($7 !~ /nologin|false/) && ($6 != "") { print $1; exit }' /etc/passwd)
             if [ -z "$ACTUAL_USER" ]; then
                 critical_error "Running as root without sudo, and could not determine a standard user. Please run with 'sudo'."
             fi
             echo "Warning: Running as root without sudo. Assuming user is '$ACTUAL_USER'."
        else
             ACTUAL_USER=$(whoami)
             # Should have been caught by check_root()
             if [ "$(id -u)" -ne 0 ]; then
                 critical_error "This script needs root privileges. Please run with 'sudo'."
             fi
        fi
    fi

    ACTUAL_HOME=$(getent passwd "$ACTUAL_USER" | cut -d: -f6)

    if [ -z "$ACTUAL_HOME" ] || [ ! -d "$ACTUAL_HOME" ]; then
        critical_error "Could not determine a valid home directory for user '$ACTUAL_USER'."
    fi

    # Construct the absolute paths
    PROJECT_DIR_PATH="$ACTUAL_HOME/$PROJECT_DIR_NAME"
    VENV_PATH="$PROJECT_DIR_PATH/$VENV_DIR_NAME"

    echo "Running setup for user: $ACTUAL_USER (home: $ACTUAL_HOME)"
    echo "Project directory will be created at: $PROJECT_DIR_PATH"
    echo "Python virtual environment will be created at: $VENV_PATH"
}

# Check internet connectivity
check_internet() {
    echo "Checking internet connectivity..."
    if ping -c 1 8.8.8.8 >/dev/null 2>&1; then
        echo "Internet connection available."
    else
        warning "No internet connection detected. Network operations (updates, downloads) will fail."
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
        for lock_file in "${lock_files[@]}"; do
            if sudo fuser "$lock_file" >/dev/null 2>&1; then
                echo "Package manager lock file found: $lock_file. Waiting..."
                locked=1
                break
            fi
        done

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
            sudo systemctl enable systemd-timesyncd --now > /dev/null 2>&1 # Enable and start
            sudo timedatectl set-ntp true

            echo "Waiting up to 30 seconds for synchronization..."
            local synced=false
            for i in {1..15}; do
                sleep 2; echo -n "."
                if timedatectl status | grep -q "System clock synchronized: yes"; then
                    echo " Synchronized!" ; synced=true ; break
                fi
            done

            if $synced; then
                echo "Time successfully synchronized via NTP." ; echo "New system time: $(date)"
            else
                warning "Could not automatically sync time via NTP. Time might be incorrect."
                echo "You can set the time manually using: sudo timedatectl set-time 'YYYY-MM-DD HH:MM:SS'"
            fi
        else
            warning "Cannot sync time via NTP (no internet). Time might be incorrect."
            echo "You can set the time manually using: sudo timedatectl set-time 'YYYY-MM-DD HH:MM:SS'"
        fi
    fi
}

# Configure DS3231 RTC module
setup_rtc() {
    echo "======================================"
    echo "Setting Up DS3231 RTC Module"
    
    # Check if I2C is enabled
    local config_file=""
    if [ -f /boot/firmware/config.txt ]; then 
        config_file="/boot/firmware/config.txt"
    elif [ -f /boot/config.txt ]; then 
        config_file="/boot/config.txt"
    else 
        warning "Could not find config.txt. Cannot configure RTC automatically."
        return
    fi
    
    echo "Using config file: $config_file"
    
    # Add RTC overlay if not already present
    if grep -q "dtoverlay=i2c-rtc,ds3231" "$config_file"; then
        echo "RTC overlay already enabled in $config_file"
    else
        echo "Adding DS3231 RTC overlay to $config_file..."
        echo "" | sudo tee -a "$config_file" > /dev/null
        echo "# Enable DS3231 RTC" | sudo tee -a "$config_file" > /dev/null
        echo "dtoverlay=i2c-rtc,ds3231" | sudo tee -a "$config_file" > /dev/null
        echo "RTC overlay added. Will take effect after reboot."
    fi
    
    # Remove fake-hwclock if installed
    echo "Checking for fake-hwclock..."
    if dpkg -l | grep -q fake-hwclock; then
        echo "Removing fake-hwclock package..."
        wait_for_apt_lock
        sudo apt-get -y remove fake-hwclock
        sudo update-rc.d -f fake-hwclock remove
    else
        echo "fake-hwclock package not installed. Skipping removal."
    fi
    
    # Modify the hwclock-set file to work with hardware RTC
    echo "Configuring hwclock-set file..."
    local hwclock_set="/lib/udev/hwclock-set"
    
    if [ -f "$hwclock_set" ]; then
        # Make a backup if not already done
        if [ ! -f "${hwclock_set}.backup" ]; then
            sudo cp "$hwclock_set" "${hwclock_set}.backup"
            echo "Created backup of original hwclock-set file: ${hwclock_set}.backup"
        fi
        
        # Comment out problematic lines
        echo "Modifying hwclock-set to work with hardware RTC..."
        # Comment out the systemd condition
        sudo sed -i 's/^if \[ -e \/run\/systemd\/system \] ; then/\#if \[ -e \/run\/systemd\/system \] ; then/g' "$hwclock_set"
        sudo sed -i 's/^    exit 0/\#    exit 0/g' "$hwclock_set"
        sudo sed -i 's/^fi/\#fi/g' "$hwclock_set"
        
        # Comment out the udev condition as well
        sudo sed -i 's/^if \[ -e \/run\/udev\/hwclock-set \] ; then/\#if \[ -e \/run\/udev\/hwclock-set \] ; then/g' "$hwclock_set"
        
        echo "hwclock-set file has been configured for hardware RTC."
    else
        warning "hwclock-set file not found at $hwclock_set. Manual configuration may be needed."
    fi
    
    # Note about setting the hardware clock
    echo ""
    echo "Note: After system time is set correctly (either manually or via NTP),"
    echo "you should set the hardware RTC from the system time with:"
    echo "  sudo hwclock -w"
    echo ""
    echo "After reboot, verify RTC is working with:"
    echo "  sudo hwclock -r"
    echo "  ls -l /dev/rtc*"
    echo "  lsmod | grep rtc"
}

# Configure swap space
configure_swap() {
    echo "======================================"
    echo "Configuring Swap Space (Target: ${SWAP_SIZE})"
    local swap_needed=false
    local current_swap_total=$(grep SwapTotal /proc/meminfo | awk '{print $2}')
    local target_swap_kb=$(numfmt --from=iec $SWAP_SIZE | awk '{print $1/1024}')

    if [ -f /swapfile ]; then
        local current_swapfile_size=$(sudo stat -c %s /swapfile 2>/dev/null || echo 0)
        local target_swapfile_bytes=$(numfmt --from=iec $SWAP_SIZE)
        echo "Existing swap file found (/swapfile, size: $(numfmt --to=iec $current_swapfile_size))."
        if [ "$current_swapfile_size" -lt "$target_swapfile_bytes" ]; then
            echo "Swap file is smaller than target size ${SWAP_SIZE}. Recreating."
            swap_needed=true
            echo "Disabling existing swap file..."
            sudo swapoff /swapfile || true
            sudo rm -f /swapfile
        else
             echo "Existing swap file is sufficient."
             if ! swapon --show | grep -q /swapfile; then sudo swapon /swapfile; fi
             if ! grep -q '^[[:space:]]*/swapfile[[:space:]]' /etc/fstab; then
                 echo "/swapfile none swap sw 0 0" | sudo tee -a /etc/fstab > /dev/null
             fi
        fi
    elif (( current_swap_total < target_swap_kb / 2 )); then
        echo "No /swapfile found and total swap is low. Creating swap file."
        swap_needed=true
    else
        echo "Sufficient swap space detected or /swapfile not used. Skipping creation." ; free -h ; return 0
    fi

    if [ "$swap_needed" = true ]; then
        echo "Allocating ${SWAP_SIZE} swap file at /swapfile (this may take a while)..."
        if sudo fallocate -l "${SWAP_SIZE}" /swapfile; then
            echo "Swap file allocated using fallocate."
        else
            echo "fallocate failed, using dd instead (slower)..."
            local size_mb=$(numfmt --from=iec --to=si $SWAP_SIZE | sed 's/M//')
             if ! sudo dd if=/dev/zero of=/swapfile bs=1M count="$size_mb" status=progress; then
                 critical_error "Failed to create swap file using dd."
             fi
        fi
        sudo chmod 600 /swapfile ; sudo mkswap /swapfile ; sudo swapon /swapfile
        if ! grep -q '^[[:space:]]*/swapfile[[:space:]]' /etc/fstab; then
            echo "/swapfile none swap sw 0 0" | sudo tee -a /etc/fstab > /dev/null
        fi
        echo "Swap space configured successfully."
    fi
    free -h
}

# Configure needrestart for non-interactive updates
configure_needrestart() {
    echo "======================================"
    echo "Configuring needrestart for automatic restarts..."
    if [ -f /etc/needrestart/needrestart.conf ]; then
        if grep -q -E "^\s*#?\s*\$nrconf{restart}\s*=\s*'[il]'" /etc/needrestart/needrestart.conf; then
             echo "Setting needrestart to automatic mode..."
             sudo sed -i "s:^\s*#\?\s*\$nrconf{restart}\s*=\s*'[il]':\$nrconf{restart} = 'a':" /etc/needrestart/needrestart.conf
        else
             echo "Needrestart already configured or manually set."
        fi
    else
        echo "Needrestart config file not found, creating one with automatic mode."
        echo "\$nrconf{restart} = 'a';" | sudo tee /etc/needrestart/needrestart.conf > /dev/null
    fi
     echo 'APT::Get::Assume-Yes "true";' | sudo tee /etc/apt/apt.conf.d/99assume-yes > /dev/null
     echo 'DPkg::Options { "--force-confdef"; "--force-confold"; }' | sudo tee /etc/apt/apt.conf.d/90local-dpkg-options > /dev/null
}

# Update system packages
update_system() {
    echo "======================================"
    echo "Updating System Packages"
    wait_for_apt_lock
    echo "Running apt update..."
    if ! sudo apt-get update; then warning "apt update failed."; fi

    wait_for_apt_lock
    echo "Running apt upgrade..."
    if ! sudo DEBIAN_FRONTEND=noninteractive apt-get upgrade -y; then warning "apt upgrade failed."; fi
}

# Install required system packages
install_system_packages() {
    echo "======================================"
    echo "Installing System Packages"
    local packages=( git git-all build-essential pkg-config libusb-dev libudev-dev
                     python3-pip python3-dev python3-venv vim feh screen wireless-tools i2c-tools )
    echo "Installing: ${packages[@]}"
    wait_for_apt_lock
    if ! sudo DEBIAN_FRONTEND=noninteractive apt-get install -y "${packages[@]}"; then
        critical_error "Failed to install one or more essential system packages."
    fi
    echo "System packages installed successfully."
}


# Enable SPI interface
enable_spi() {
    echo "======================================"
    echo "Enabling SPI Interface"
    local config_file=""
    if [ -f /boot/firmware/config.txt ]; then config_file="/boot/firmware/config.txt"
    elif [ -f /boot/config.txt ]; then config_file="/boot/config.txt"
    else warning "Could not find config.txt. Cannot enable SPI automatically." ; return ; fi
    echo "Using config file: $config_file"

    if grep -q -E "^\s*dtparam=spi=on" "$config_file"; then
        echo "SPI interface already enabled."
    else
        echo "Enabling SPI interface..."
        sudo sed -i -E 's:^\s*(dtparam=spi=off):#\1:' "$config_file"
        if ! grep -q -E "^\s*dtparam=spi=on" "$config_file"; then
            echo "dtparam=spi=on" | sudo tee -a "$config_file" > /dev/null
        fi
        echo "SPI interface enabled. Reboot required."
    fi
}

# Add user to necessary groups
setup_user_permissions() {
    echo "======================================"
    echo "Setting Up User Permissions for Hardware Access"
    local groups_to_add=("video" "i2c" "gpio" "spi" "dialout")
    for group in "${groups_to_add[@]}"; do
        if getent group "$group" >/dev/null; then
            if groups "$ACTUAL_USER" | grep -q -w "$group"; then
                echo "User '$ACTUAL_USER' already in group '$group'."
            else
                echo "Adding user '$ACTUAL_USER' to group '$group'..."
                if ! sudo usermod -aG "$group" "$ACTUAL_USER"; then
                     warning "Failed to add user '$ACTUAL_USER' to group '$group'."
                else echo "User added to '$group'. Changes take effect on next login/reboot."; fi
            fi
        else echo "Group '$group' does not exist. Skipping."; fi
    done
}

# Configure terminal history search
configure_terminal() {
    echo "======================================"
    echo "Configuring Terminal History Search"
    local inputrc_path="$ACTUAL_HOME/.inputrc"
    if [ -f "$inputrc_path" ] && grep -q "history-search-backward" "$inputrc_path"; then
        echo "Terminal history search already configured."
    else
        echo "Setting up terminal history search (up/down arrows) in $inputrc_path..."
        {
            echo '$include /etc/inputrc'
            echo '"\e[A": history-search-backward'
            echo '"\e[B": history-search-forward'
        } | sudo -u "$ACTUAL_USER" tee "$inputrc_path" > /dev/null
        sudo chown "$ACTUAL_USER:$ACTUAL_USER" "$inputrc_path"
        echo "Terminal history configured. Takes effect in new shells."
    fi
}

# Setup Python Virtual Environment and install packages
setup_python_venv() {
    echo "======================================"
    echo "Setting Up Python Environment in $PROJECT_DIR_PATH"

    # Create the main project directory first, owned by the user
    echo "Ensuring project directory exists: $PROJECT_DIR_PATH"
    if ! sudo -u "$ACTUAL_USER" mkdir -p "$PROJECT_DIR_PATH"; then
        critical_error "Failed to create project directory '$PROJECT_DIR_PATH'. Check permissions for '$ACTUAL_USER' in '$ACTUAL_HOME'."
    fi

    # Now create the virtual environment inside the project directory
    echo "Checking/Creating virtual environment at: $VENV_PATH"
    if [ ! -d "$VENV_PATH" ]; then
        echo "Creating virtual environment..."
        if ! sudo -u "$ACTUAL_USER" python3 -m venv "$VENV_PATH"; then
            critical_error "Failed to create Python virtual environment at '$VENV_PATH'."
        fi
        echo "Virtual environment created."
    else
        echo "Virtual environment directory already exists."
    fi

    # Define Python packages - Note: seabreeze is handled separately
    local python_packages=(
        "wheel" "setuptools --upgrade" "pip --upgrade"
        "matplotlib" "pygame" "pygame-menu" "spidev" "RPi.GPIO"
        "displayhatmini"
        "rpi-lgpio"
    )

    echo "Installing Python packages into virtual environment..."
    # Use the python/pip from the venv, run as the user
    local venv_python="$VENV_PATH/bin/python"
    for package in "${python_packages[@]}"; do
        echo "Installing $package..."
        if ! sudo -u "$ACTUAL_USER" "$venv_python" -m pip install --no-cache-dir $package; then
            warning "Failed to install Python package '$package'."
        fi
    done

    echo "Python packages installation process finished."
    echo "To use the environment:"
    echo "  cd $PROJECT_DIR_PATH"
    echo "  source $VENV_DIR_NAME/bin/activate"
    echo "Then run your python scripts."

    # Add activation hint to .bashrc
    local bashrc_path="$ACTUAL_HOME/.bashrc"
    local activation_hint="echo 'Project env available: cd ${PROJECT_DIR_PATH} && source ${VENV_DIR_NAME}/bin/activate'"
    if [ -f "$bashrc_path" ] && ! grep -Fq "$PROJECT_DIR_PATH" "$bashrc_path"; then # Simple check if path mentioned
         echo "Adding project activation hint to $bashrc_path..."
         { echo ""; echo "# Hint for activating the Python virtual environment for ${PROJECT_DIR_NAME}"; echo "$activation_hint"; } | sudo -u "$ACTUAL_USER" tee -a "$bashrc_path" > /dev/null
         sudo chown "$ACTUAL_USER:$ACTUAL_USER" "$bashrc_path"
    fi
}

# Install seabreeze separately (last step of Python package installation)
install_seabreeze() {
    echo "======================================"
    echo "Installing Seabreeze (Special Handling)"
    local venv_python="$VENV_PATH/bin/python"
    local venv_pip="$VENV_PATH/bin/pip"
    
    echo "Installing seabreeze[pyseabreeze] package (this may take a while)..."
    echo "Note: This step can take several minutes, especially on Raspberry Pi Zero 2 W."
    
    if ! sudo -u "$ACTUAL_USER" "$venv_pip" install --no-cache-dir seabreeze[pyseabreeze]; then
        warning "Failed to install seabreeze[pyseabreeze] package."
        echo "You may need to install it manually after setup:"
        echo "  1. cd $PROJECT_DIR_PATH"
        echo "  2. source $VENV_DIR_NAME/bin/activate"
        echo "  3. pip install seabreeze[pyseabreeze]"
    else
        echo "Seabreeze installation successful."
    fi
}

# Setup seabreeze udev rules
setup_seabreeze_udev() {
    echo "======================================"
    echo "Setting Up Seabreeze udev Rules"
    local rules_file="/etc/udev/rules.d/10-oceanoptics.rules"
    # Use the venv's command path
    local seabreeze_setup_cmd="$VENV_PATH/bin/seabreeze_os_setup"

    if [ -f "$rules_file" ]; then
        echo "Seabreeze udev rules file already exists. Skipping setup."
        echo "If needed, remove $rules_file and re-run, or run manually:"
        echo "sudo $seabreeze_setup_cmd"
        return
    fi

    if [ ! -x "$seabreeze_setup_cmd" ]; then
        warning "seabreeze_os_setup not found or not executable in venv ($seabreeze_setup_cmd)."
        echo "Cannot set up udev rules automatically. Manual setup may be needed."
        return
    fi

    echo "Running seabreeze_os_setup to install udev rules..."
    if ! sudo "$seabreeze_setup_cmd"; then
        warning "Failed to execute seabreeze_os_setup."
        echo "You may need to run it manually after setup:"
        echo "  1. cd $PROJECT_DIR_PATH"
        echo "  2. source $VENV_DIR_NAME/bin/activate" 
        echo "  3. sudo seabreeze_os_setup"
    else
        echo "Seabreeze udev rules installed. Reloading udev rules..."
        sudo udevadm control --reload-rules && sudo udevadm trigger
        echo "udev rules reloaded."
    fi
}

# Verify installations
verify_setup() {
    echo "======================================"
    echo "Verifying Setup (Basic Checks)"

    echo "Checking for SPI device nodes..."
    if ls /dev/spidev* >/dev/null 2>&1; then
        echo "SPI devices found:" ; ls -l /dev/spidev*
    else echo "SPI device nodes (/dev/spidev*) not found (expected until reboot)." ; fi

    echo "Checking Python package imports within the virtual environment ($VENV_PATH)..."
    local python_check_packages=("matplotlib" "pygame" "pygame_menu" "spidev" "RPi.GPIO")
    local failed_imports=()
    local venv_python="$VENV_PATH/bin/python"
    if [ ! -x "$venv_python" ]; then
        warning "Could not find Python interpreter in venv ($venv_python). Skipping package check."
        return
    fi
    for package in "${python_check_packages[@]}"; do
        if sudo -u "$ACTUAL_USER" "$venv_python" -c "import $package" >/dev/null 2>&1; then
            echo "  - $package: OK"
        else
            echo "  - $package: FAILED to import" ; failed_imports+=("$package")
        fi
    done

    # Special check for seabreeze (which may not be installed yet)
    echo "Checking seabreeze (optional at this point):"
    if sudo -u "$ACTUAL_USER" "$venv_python" -c "import seabreeze" >/dev/null 2>&1; then
        echo "  - seabreeze: OK"
    else
        echo "  - seabreeze: NOT IMPORTED (may need manual installation)"
        echo "    Follow the steps in the 'Next Steps' section below"
    fi

    if [ ${#failed_imports[@]} -gt 0 ]; then
        warning "Some Python packages failed to import: ${failed_imports[*]}"
        echo "May require reboot (for hardware group permissions) or installation check."
    else echo "All checked Python packages imported successfully within the venv." ; fi
}


# === Main Script Logic ===
main() {
    echo "====================================="
    echo "Starting Raspberry Pi Zero 2 W Setup Script"
    echo "Target OS: Ubuntu 22.04 LTS Server"
    echo "Project Dir: ~/$PROJECT_DIR_NAME, Venv: $VENV_DIR_NAME"
    echo "====================================="

    check_root
    get_actual_user # Sets ACTUAL_USER, ACTUAL_HOME, PROJECT_DIR_PATH, VENV_PATH

    check_date_time
    check_internet

    configure_swap
    configure_needrestart

    update_system
    install_system_packages

    enable_spi
    setup_rtc       # Add this line to call the new RTC setup function
    setup_user_permissions

    configure_terminal

    setup_python_venv # Creates project dir and venv inside
    
    # Special handling for seabreeze
    install_seabreeze
    setup_seabreeze_udev # Uses command from venv

    verify_setup # Checks venv imports

    # Set hardware RTC from system time (if the system time was set correctly)
    echo "Setting hardware RTC from system time..."
    sudo hwclock -w
    echo "Hardware RTC set to: $(sudo hwclock -r)"

    echo ""
    echo "====================================="
    echo "Setup script finished!"
    echo ""
    echo "IMPORTANT RECOMMENDATIONS:"
    echo "1. A REBOOT is strongly recommended:"
    echo "   sudo reboot"
    echo ""
    echo "2. After rebooting, navigate to your project directory and ACTIVATE the environment:"
    echo "   cd $PROJECT_DIR_PATH"
    echo "   source $VENV_DIR_NAME/bin/activate"
    echo ""
    echo "3. If seabreeze installation failed, install it manually after reboot:"
    echo "   cd $PROJECT_DIR_PATH"
    echo "   source $VENV_DIR_NAME/bin/activate"
    echo "   pip install seabreeze[pyseabreeze]"
    echo "   sudo seabreeze_os_setup"
    echo ""
    echo "4. Test core imports again within the activated environment:"
    echo "   python -c 'import matplotlib, pygame, pygame_menu, spidev, RPi.GPIO; print(\"OK\")'"
    echo ""
    echo "5. Place your Python scripts inside '$PROJECT_DIR_PATH'."
    echo "6. Check seabreeze device detection (activate venv, plug in device):"
    echo "   python -m seabreeze.cseabreeze_backend ListDevices"
    echo ""
    echo "7. Verify RTC is working after reboot:"
    echo "   sudo hwclock -r"
    echo "   ls -l /dev/rtc*"
    echo "====================================="
}
# Execute the main function
main
