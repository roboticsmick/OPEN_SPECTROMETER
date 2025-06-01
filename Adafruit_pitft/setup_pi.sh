#!/bin/bash
# Exit immediately if a command exits with a non-zero status.
set -e

# === Configuration ===
PROJECT_DIR_NAME="pysb-app"
VENV_DIR_NAME="pysb_venv"
SWAP_SIZE="2G"
PKG_MANAGER_TIMEOUT=180

# === Script Variables ===
ACTUAL_USER=""
ACTUAL_HOME=""
PROJECT_DIR_PATH=""
VENV_PATH=""

# === Python Package Requirements (Integrated) ===
PYTHON_PACKAGES=(
    "numpy"
    "pyusb"
    "matplotlib"
    "pygame"
    "pygame-menu"
    "spidev"
    "RPi.GPIO"
    "rpi-lgpio"
    "wheel"
    "setuptools"
    # Note: seabreeze[pyseabreeze] is handled separately due to compilation requirements
    # Note: displayhatmini removed for Adafruit PiTFT compatibility
)

# === Helper Functions ===
critical_error() {
    echo "" >&2; echo "ERROR: $1" >&2; echo "Setup failed." >&2; exit 1;
}
warning() {
    echo "" >&2; echo "WARNING: $1" >&2;
}
info() {
    echo "[INFO] $1"
}
check_root() {
    if [ "$(id -u)" -ne 0 ]; then critical_error "This script must be run with sudo or as root."; fi
}
get_actual_user() {
    if [ -n "$SUDO_USER" ]; then ACTUAL_USER="$SUDO_USER"; else
        if [ "$(id -u)" -eq 0 ]; then
             ACTUAL_USER=$(awk -F: '($3 >= 1000) && ($7 !~ /nologin|false/) && ($6 != "") { print $1; exit }' /etc/passwd)
             if [ -z "$ACTUAL_USER" ]; then critical_error "Running as root, could not determine standard user."; fi
             info "Running as root. Assuming user: '$ACTUAL_USER'."
        else ACTUAL_USER=$(whoami); if [ "$(id -u)" -ne 0 ]; then critical_error "Needs root. Run with 'sudo'."; fi; fi
    fi
    ACTUAL_HOME=$(getent passwd "$ACTUAL_USER" | cut -d: -f6)
    if [ -z "$ACTUAL_HOME" ] || [ ! -d "$ACTUAL_HOME" ]; then critical_error "No valid home dir for '$ACTUAL_USER'."; fi
    PROJECT_DIR_PATH="$ACTUAL_HOME/$PROJECT_DIR_NAME"; VENV_PATH="$PROJECT_DIR_PATH/$VENV_DIR_NAME"
    info "User: $ACTUAL_USER (home: $ACTUAL_HOME)"; info "Project dir: $PROJECT_DIR_PATH"; info "Venv: $VENV_PATH"
}
check_internet() {
    info "Checking internet..."; if ping -c 1 8.8.8.8 >/dev/null 2>&1; then info "Internet OK."; else warning "No internet."; fi
}
wait_for_apt_lock() {
    info "Checking package manager locks..."; local locks=( "/var/lib/dpkg/lock*" "/var/lib/apt/lists/lock" "/var/cache/apt/archives/lock" ); local start=$(date +%s)
    while true; do local locked=0
        for lock in "${locks[@]}"; do if sudo fuser $lock >/dev/null 2>&1; then info "Lock: $lock. Waiting..."; locked=1; break; fi; done
        if pgrep -f "apt|dpkg" > /dev/null && [ $locked -eq 0 ]; then info "Waiting for apt/dpkg processes..."; locked=1; fi
        if [ $locked -eq 0 ]; then info "Package manager available."; return 0; fi
        if (( $(date +%s) - start > PKG_MANAGER_TIMEOUT )); then critical_error "Lock timeout. Investigate manually."; fi
        sleep 5; echo -n "."; done
}
check_date_time() {
    info "======================================"; info "Verifying System Date and Time"
    info "Current: $(date)"; read -p "Correct? (y/N): " choice
    if [[ "$choice" != "y" && "$choice" != "Y" ]]; then info "Syncing time via NTP..."
        if ping -c 1 8.8.8.8 >/dev/null 2>&1; then
            sudo systemctl enable systemd-timesyncd --now > /dev/null 2>&1; sudo timedatectl set-ntp true
            info "Waiting for sync (30s max)..."; local synced=false
            for i in {1..15}; do sleep 2; echo -n "."; if timedatectl status | grep -q "System clock synchronized: yes"; then echo " Synced!"; synced=true; break; fi; done
            if $synced; then info "Time synced. New: $(date)"; else warning "Could not sync time."; fi
        else warning "No internet. Cannot sync time."; fi
        info "Manual: sudo timedatectl set-time 'YYYY-MM-DD HH:MM:SS'"
    fi
}
setup_rtc() {
    info "======================================"; info "Setting Up DS3231 RTC Module"
    local cfg="/boot/firmware/config.txt"; if [ ! -f $cfg ]; then cfg="/boot/config.txt"; fi
    if [ ! -f $cfg ]; then warning "config.txt not found. Cannot auto-config RTC."; return; fi
    info "Using config: $cfg"
    if grep -q "dtoverlay=i2c-rtc,ds3231" "$cfg"; then info "RTC overlay already enabled."; else
        info "Adding DS3231 RTC overlay to $cfg..."; echo -e "\n# Enable DS3231 RTC\ndtoverlay=i2c-rtc,ds3231" | sudo tee -a "$cfg" > /dev/null
        info "RTC overlay added. Effective after reboot."
    fi
    if dpkg -l | grep -q fake-hwclock; then info "Removing fake-hwclock..."; wait_for_apt_lock; sudo apt-get -y remove fake-hwclock; sudo update-rc.d -f fake-hwclock remove; else info "fake-hwclock not installed."; fi
    local hwset="/lib/udev/hwclock-set"; if [ -f "$hwset" ]; then
        if [ ! -f "${hwset}.backup_pysb" ]; then sudo cp "$hwset" "${hwset}.backup_pysb"; info "Backed up hwclock-set to ${hwset}.backup_pysb"; fi
        info "Modifying hwclock-set..."; sudo sed -i -E 's/^if \[ -e \/run\/(systemd\/system|udev\/hwclock-set) \] ; then/#&/' "$hwset"; sudo sed -i -E 's/^    exit 0/#&/' "$hwset"; sudo sed -i -E 's/^fi/#&/' "$hwset"
        info "hwclock-set configured."; else warning "hwclock-set not found. Manual config may be needed."; fi
    echo -e "\nNote: After system time is correct, run: sudo hwclock -w\nAfter reboot, verify with: sudo hwclock -r\n"
}
configure_swap() {
    info "======================================"; info "Configuring Swap (Target: ${SWAP_SIZE})"
    local current_total_kb=$(grep SwapTotal /proc/meminfo | awk '{print $2}')
    local target_bytes=$(numfmt --from=iec "$SWAP_SIZE")
    
    if [ -f /swapfile ]; then
        local current_file_bytes=$(sudo stat -c %s /swapfile 2>/dev/null || echo 0)
        info "Existing /swapfile (size: $(numfmt --to=iec $current_file_bytes)). Target: $(numfmt --to=iec $target_bytes)."
        if [ "$current_file_bytes" -lt "$target_bytes" ]; then
            info "Swap file too small. Recreating."; sudo swapoff /swapfile || true; sudo rm -f /swapfile
            sudo fallocate -l "${SWAP_SIZE}" /swapfile || { info "fallocate failed, using dd (slower)..."; sudo dd if=/dev/zero of=/swapfile bs=1M count=$(($(numfmt --from=iec $SWAP_SIZE)/1024/1024)) status=progress || critical_error "dd failed."; }
            sudo chmod 600 /swapfile; sudo mkswap /swapfile; sudo swapon /swapfile
        else info "Existing swap file sufficient."; if ! swapon --show | grep -q /swapfile; then sudo swapon /swapfile; fi; fi
    elif (( current_total_kb * 1024 < target_bytes / 2 )); then
        info "No /swapfile, low total swap. Creating."; sudo fallocate -l "${SWAP_SIZE}" /swapfile || { info "fallocate failed, using dd..."; sudo dd if=/dev/zero of=/swapfile bs=1M count=$(($(numfmt --from=iec $SWAP_SIZE)/1024/1024)) status=progress || critical_error "dd failed."; }
        sudo chmod 600 /swapfile; sudo mkswap /swapfile; sudo swapon /swapfile
    else info "Sufficient swap or /swapfile not used. Skipping creation."; free -h; return 0; fi
    if ! grep -q '^[[:space:]]*/swapfile[[:space:]]' /etc/fstab; then echo "/swapfile none swap sw 0 0" | sudo tee -a /etc/fstab > /dev/null; fi
    info "Swap configured."; free -h
}
configure_needrestart() {
    info "======================================"; info "Configuring needrestart for auto restarts..."
    if [ -f /etc/needrestart/needrestart.conf ]; then
        if grep -q -E "^\s*#?\s*\$nrconf{restart}\s*=\s*'[il]'" /etc/needrestart/needrestart.conf; then
             info "Setting needrestart to auto mode..."; sudo sed -i "s:^\s*#\?\s*\$nrconf{restart}\s*=\s*'[il]':\$nrconf{restart} = 'a':" /etc/needrestart/needrestart.conf
        else info "Needrestart already configured or manually set."; fi
    else info "Needrestart config not found, creating with auto mode."; echo "\$nrconf{restart} = 'a';" | sudo tee /etc/needrestart/needrestart.conf > /dev/null; fi
    echo 'APT::Get::Assume-Yes "true";' | sudo tee /etc/apt/apt.conf.d/99assume-yes > /dev/null
    echo 'DPkg::Options { "--force-confdef"; "--force-confold"; }' | sudo tee /etc/apt/apt.conf.d/90local-dpkg-options > /dev/null
}
update_system() {
    info "======================================"; info "Updating System Packages"
    wait_for_apt_lock; info "Running apt update..."; if ! sudo apt-get update; then warning "apt update failed."; fi
    wait_for_apt_lock; info "Running apt upgrade..."; if ! sudo DEBIAN_FRONTEND=noninteractive apt-get upgrade -y; then warning "apt upgrade failed."; fi
}
install_system_packages() {
    info "======================================"; info "Installing System Packages"
    local pkgs=( git build-essential pkg-config libusb-dev libudev-dev \
                 python3-pip python3-dev python3-venv vim feh screen wireless-tools i2c-tools \
                 libsdl2-dev libsdl2-image-dev libsdl2-mixer-dev libsdl2-ttf-dev \
                 libportmidi-dev libfreetype6-dev libjpeg-dev libpng-dev libtiff5-dev \
                 cmake device-tree-compiler libraspberrypi-dev python3-evdev dphys-swapfile \
                 python3-rpi.gpio python3-spidev python3-smbus network-manager
    )
    info "Installing: ${pkgs[*]}"; wait_for_apt_lock
    if ! sudo DEBIAN_FRONTEND=noninteractive apt-get install -y "${pkgs[@]}"; then critical_error "Failed to install system packages."; fi
    info "System packages installed."
}
enable_spi_i2c() {
    info "======================================"; info "Enabling SPI & I2C Interfaces"
    local cfg="/boot/firmware/config.txt"; if [ ! -f $cfg ]; then cfg="/boot/config.txt"; fi
    if [ ! -f $cfg ]; then warning "config.txt not found. Cannot auto-enable SPI/I2C."; return; fi
    info "Using config: $cfg"
    for iface_param in "dtparam=spi=on" "dtparam=i2c_arm=on"; do
        iface_name=$(echo "$iface_param" | cut -d'=' -f1 | cut -d'=' -f2 | sed 's/dtparam=//; s/_arm//' | tr '[:lower:]' '[:upper:]')
        if grep -q -E "^\s*${iface_param}" "$cfg"; then info "$iface_name interface already enabled."; else
            info "Enabling $iface_name interface..."; sudo sed -i -E "s:^\s*($(echo $iface_param | sed 's/=on/=off/')):#\1:" "$cfg"
            if ! grep -q -E "^\s*${iface_param}" "$cfg"; then echo "$iface_param" | sudo tee -a "$cfg" > /dev/null; fi
            info "$iface_name interface enabled. Reboot required."
        fi
    done
}
setup_user_permissions() {
    info "======================================"; info "Setting User Permissions for Hardware"
    local groups=("video" "i2c" "gpio" "spi" "dialout" "input" "render" "plugdev")
    for group in "${groups[@]}"; do
        if getent group "$group" >/dev/null; then
            if groups "$ACTUAL_USER" | grep -q -w "$group"; then info "User '$ACTUAL_USER' already in '$group'."; else
                info "Adding '$ACTUAL_USER' to '$group'..."; if ! sudo usermod -aG "$group" "$ACTUAL_USER"; then warning "Failed add to '$group'."; else info "User added to '$group'. Effective next login/reboot."; fi
            fi; else info "Group '$group' not found. Skipping."; fi
    done
}

setup_console_cursor_control() {
    info "======================================"; info "Setting Up Console Cursor Control for PiTFT"
    
    # Create udev rules for persistent cursor control permissions
    info "Creating udev rules for cursor control..."
    local cursor_rules="/etc/udev/rules.d/99-pitft-console.rules"
    
    cat << 'EOF' | sudo tee "$cursor_rules" > /dev/null
# PiTFT Console cursor control permissions
SUBSYSTEM=="graphics", KERNEL=="fbcon", ACTION=="add", RUN+="/bin/chmod 666 /sys/class/graphics/fbcon/cursor_blink"
SUBSYSTEM=="vtconsole", KERNEL=="vtcon1", ACTION=="add", RUN+="/bin/chmod 666 /sys/class/vtconsole/vtcon1/bind"

# Alternative approach - set permissions on boot
ACTION=="add", SUBSYSTEM=="graphics", KERNEL=="fbcon", RUN+="/bin/sh -c 'sleep 1; chmod 666 /sys/class/graphics/fbcon/cursor_blink'"
ACTION=="add", SUBSYSTEM=="vtconsole", KERNEL=="vtcon1", RUN+="/bin/sh -c 'sleep 1; chmod 666 /sys/class/vtconsole/vtcon1/bind'"
EOF

    info "Cursor control udev rules created at $cursor_rules"
    
    # Set immediate permissions if the files exist
    if [ -f /sys/class/graphics/fbcon/cursor_blink ]; then
        info "Setting immediate cursor_blink permissions..."
        sudo chmod 666 /sys/class/graphics/fbcon/cursor_blink
    fi
    
    if [ -f /sys/class/vtconsole/vtcon1/bind ]; then
        info "Setting immediate vtcon1 bind permissions..."
        sudo chmod 666 /sys/class/vtconsole/vtcon1/bind
    fi
    
    # Reload udev rules
    info "Reloading udev rules..."
    sudo udevadm control --reload-rules
    sudo udevadm trigger
    
    info "Console cursor control configured. Full effect after reboot."
}

configure_terminal() {
    info "======================================"; info "Configuring Terminal History Search"
    local rc="$ACTUAL_HOME/.inputrc"
    if [ -f "$rc" ] && grep -q "history-search-backward" "$rc"; then info "History search already configured."; else
        info "Setting up history search in $rc..."; { echo '$include /etc/inputrc'; echo '"\e[A": history-search-backward'; echo '"\e[B": history-search-forward'; } | sudo -u "$ACTUAL_USER" tee "$rc" > /dev/null
        sudo chown "$ACTUAL_USER:$ACTUAL_USER" "$rc"; info "Terminal history configured. Effective new shells."; fi
}

setup_adafruit_pitft() {
    info "======================================"; info "Setting up Adafruit PiTFT 2.8c"
    local script_src_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    local adafruit_repo_dir="$script_src_dir/Raspberry-Pi-Installer-Scripts"

    if [ ! -d "$adafruit_repo_dir" ]; then
        info "Adafruit Installer Scripts repo not found locally. Cloning..."
        if ! git clone https://github.com/adafruit/Raspberry-Pi-Installer-Scripts.git "$adafruit_repo_dir"; then
            critical_error "Failed to clone Adafruit Installer Scripts."
        fi
        sudo chown -R "$ACTUAL_USER:$ACTUAL_USER" "$adafruit_repo_dir"
    else info "Adafruit Installer Scripts repo found at $adafruit_repo_dir"; fi

    if [ ! -f "$adafruit_repo_dir/adafruit-pitft.py" ]; then critical_error "adafruit-pitft.py not found."; fi

    info "Running Adafruit PiTFT Installer for 2.8c, console mode, no-reboot..."
    
    # Install script dependencies
    if ! python3 -c "import click; import adafruit_shell" &> /dev/null; then
        info "Installing click & adafruit-python-shell for Adafruit installer script (system-wide)..."
        sudo python3 -m pip install click adafruit-python-shell 
    fi
    
    cd "$adafruit_repo_dir"
    if ! sudo -E env PATH="$PATH" python3 adafruit-pitft.py --display=28c --rotation=270 --install-type=console --reboot=no; then
        warning "Adafruit PiTFT installer script finished with warning/error. Check output. Manual config may be needed."
    else info "Adafruit PiTFT installer script completed."; fi
    cd "$script_src_dir"
    info "Adafruit PiTFT setup process finished. Reboot required for display changes."
}

setup_python_venv() {
    info "======================================"; info "Setting Up Python Environment in $PROJECT_DIR_PATH"
    info "Ensuring project directory exists: $PROJECT_DIR_PATH"
    if ! sudo -u "$ACTUAL_USER" mkdir -p "$PROJECT_DIR_PATH"; then critical_error "Failed create project dir."; fi
    info "Checking/Creating venv at: $VENV_PATH"
    if [ ! -d "$VENV_PATH" ]; then info "Creating venv..."; if ! sudo -u "$ACTUAL_USER" python3 -m venv "$VENV_PATH"; then critical_error "Failed create venv."; fi; info "Venv created."; else info "Venv exists."; fi
    
    local venv_python="$VENV_PATH/bin/python"; local venv_pip="$VENV_PATH/bin/pip"
    info "Upgrading pip, setuptools, wheel in venv..."
    sudo -u "$ACTUAL_USER" "$venv_python" -m pip install --no-cache-dir --upgrade pip setuptools wheel

    # Install integrated Python packages
    info "Installing Python packages into venv..."
    for package in "${PYTHON_PACKAGES[@]}"; do
        info "Installing $package..."
        if ! sudo -u "$ACTUAL_USER" "$venv_pip" install --no-cache-dir "$package"; then
            warning "Failed to install Python package '$package'."
        fi
    done

    info "Python packages installation process finished."
    info "To use the environment: cd $PROJECT_DIR_PATH && source $VENV_DIR_NAME/bin/activate"
    local bashrc="$ACTUAL_HOME/.bashrc"; local hint="echo 'Project env: cd ${PROJECT_DIR_PATH} && source ${VENV_DIR_NAME}/bin/activate'"
    if [ -f "$bashrc" ] && ! grep -Fq "$PROJECT_DIR_PATH" "$bashrc"; then info "Adding venv hint to $bashrc..."
         { echo ""; echo "# Hint for ${PROJECT_DIR_NAME} venv"; echo "$hint"; } | sudo -u "$ACTUAL_USER" tee -a "$bashrc" > /dev/null; sudo chown "$ACTUAL_USER:$ACTUAL_USER" "$bashrc"; fi
}

install_seabreeze() {
    info "======================================"; info "Installing Seabreeze (Special Handling)"
    local venv_pip="$VENV_PATH/bin/pip"
    info "Installing seabreeze[pyseabreeze] (can take several minutes)..."
    if ! sudo -u "$ACTUAL_USER" "$venv_pip" install --no-cache-dir "seabreeze[pyseabreeze]"; then
        warning "Failed to install seabreeze[pyseabreeze]."; echo "Manual install: cd $PROJECT_DIR_PATH && source $VENV_DIR_NAME/bin/activate && pip install seabreeze[pyseabreeze]"
    else info "Seabreeze installation successful."; fi
}

setup_seabreeze_udev() {
    info "======================================"; info "Setting Seabreeze udev Rules"
    local rules="/etc/udev/rules.d/10-oceanoptics.rules"; local setup_cmd="$VENV_PATH/bin/seabreeze_os_setup"
    if [ -f "$rules" ]; then info "Seabreeze udev rules exist. Skipping."; echo "If needed, remove $rules and re-run, or run manually: sudo $setup_cmd"; return; fi
    if [ ! -x "$setup_cmd" ]; then warning "seabreeze_os_setup not found/executable in venv. Manual setup needed."; return; fi
    info "Running seabreeze_os_setup..."; if ! sudo "$setup_cmd"; then
        warning "seabreeze_os_setup failed."; echo "Manual: cd $PROJECT_DIR_PATH && source $VENV_DIR_NAME/bin/activate && sudo seabreeze_os_setup"
    else info "Seabreeze udev rules installed. Reloading..."; sudo udevadm control --reload-rules && sudo udevadm trigger; info "udev reloaded."; fi
}

copy_project_files_custom() {
    info "======================================"; info "Copying Project Files (main.py, assets) to $PROJECT_DIR_PATH"
    local script_src_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

    # Copy main.py
    if [ -f "$script_src_dir/main.py" ]; then info "Copying main.py..."; sudo -u "$ACTUAL_USER" cp "$script_src_dir/main.py" "$PROJECT_DIR_PATH/"; else warning "main.py not found in script source dir."; fi
    # Copy assets directory
    if [ -d "$script_src_dir/assets" ]; then info "Copying assets/ directory..."; sudo -u "$ACTUAL_USER" cp -r "$script_src_dir/assets" "$PROJECT_DIR_PATH/"; else warning "assets/ directory not found."; fi
    # Copy lib directory (if it exists)
    if [ -d "$script_src_dir/lib" ]; then info "Copying lib/ directory..."; sudo -u "$ACTUAL_USER" cp -r "$script_src_dir/lib" "$PROJECT_DIR_PATH/"; fi

    sudo chown -R "$ACTUAL_USER:$ACTUAL_USER" "$PROJECT_DIR_PATH"
}

verify_setup() {
    info "======================================"; info "Verifying Setup (Basic Checks)"
    info "Checking for SPI device nodes (post-reboot)..."; if ls /dev/spidev* >/dev/null 2>&1; then info "SPI devices:"; ls -l /dev/spidev*; else info "SPI device nodes not found (expected until reboot)."; fi
    info "Checking Python pkgs in venv ($VENV_PATH)..."; local pkgs_check=("matplotlib" "pygame" "pygame_menu" "spidev" "RPi.GPIO" "numpy" "pyusb")
    local failed=(); local venv_py="$VENV_PATH/bin/python"
    if [ ! -x "$venv_py" ]; then warning "Venv Python not found. Skipping package check."; return; fi
    for pkg in "${pkgs_check[@]}"; do if sudo -u "$ACTUAL_USER" "$venv_py" -c "import $pkg" >/dev/null 2>&1; then echo "  - $pkg: OK"; else echo "  - $pkg: FAILED"; failed+=("$pkg"); fi; done
    info "Checking seabreeze:"; if sudo -u "$ACTUAL_USER" "$venv_py" -c "import seabreeze" >/dev/null 2>&1; then echo "  - seabreeze: OK"; else echo "  - seabreeze: NOT IMPORTED (check install)"; fi
    if [ ${#failed[@]} -gt 0 ]; then warning "Python pkgs failed import: ${failed[*]}"; else info "Checked Python pkgs imported OK."; fi
    
    # Check cursor control permissions
    info "Checking cursor control permissions..."
    if [ -f /sys/class/graphics/fbcon/cursor_blink ]; then
        local perms=$(stat -c "%a" /sys/class/graphics/fbcon/cursor_blink 2>/dev/null || echo "000")
        if [[ "$perms" == "666" ]]; then echo "  - cursor_blink permissions: OK (666)"; else echo "  - cursor_blink permissions: $perms (may need manual fix)"; fi
    else echo "  - cursor_blink: Not available (normal until display setup)"; fi
    
    if [ -f /sys/class/vtconsole/vtcon1/bind ]; then
        local perms=$(stat -c "%a" /sys/class/vtconsole/vtcon1/bind 2>/dev/null || echo "000")
        if [[ "$perms" == "666" ]]; then echo "  - vtcon1 bind permissions: OK (666)"; else echo "  - vtcon1 bind permissions: $perms (may need manual fix)"; fi
    else echo "  - vtcon1 bind: Not available (normal until display setup)"; fi
}

# === Main Script Logic ===
main() {
    info "====================================="; info "Starting Raspberry Pi Setup Script (Adafruit PiTFT Focus)"
    info "Target OS: Ubuntu 22.04 LTS Server"; info "Project: ~/$PROJECT_DIR_NAME, Venv: $VENV_DIR_NAME"
    echo "====================================="
    check_root; get_actual_user
    check_date_time; check_internet
    
    configure_swap
    configure_needrestart
    update_system
    install_system_packages
    
    enable_spi_i2c
    setup_rtc
    setup_adafruit_pitft
    setup_console_cursor_control  # New: Sets up cursor control permissions
    setup_user_permissions
    configure_terminal
    
    setup_python_venv  # Now uses integrated package list
    copy_project_files_custom
    install_seabreeze
    setup_seabreeze_udev

    verify_setup
    
    info "Attempting to set hardware RTC from system time (if system time is correct)..."
    if sudo hwclock -w; then info "Hardware RTC set to: $(sudo hwclock -r)"; else warning "Failed to write to hardware clock. Ensure time is correct and RTC is working."; fi

    echo ""; info "====================================="; info "Setup script finished!"
    info "IMPORTANT: A REBOOT is REQUIRED for display drivers and some permissions to take full effect: sudo reboot"
    info "After reboot, SSH back in, then:"
    info "  cd $PROJECT_DIR_PATH"
    info "  source $VENV_DIR_NAME/bin/activate"
    info "  python3 main.py"
    info "Test seabreeze: python -m seabreeze.cseabreeze_backend ListDevices"
    info "Test RTC: sudo hwclock -r"
    info "Test cursor control: echo 0 > /sys/class/graphics/fbcon/cursor_blink"
    echo "====================================="
}
main
