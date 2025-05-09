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

## 2 inch RPi Display For RPi

Flash the Raspberry Pi Zeroe 2 W with Ubuntu 22.04 Server LTS

```sh
sudo apt install rpi-imager
```

Setup parameters:
1. Raspberry Pi Device: Raspberry Pi Zeroe 2 W
2. Operating System: Ubuntu 22.04 Server LTS (https://ubuntu.com/tutorials/how-to-install-ubuntu-on-your-raspberry-pi#1-overview)
3. Stoage - Use a Samsung 128GB PRO Plus microSD Card or high quality SD card. Get the best one you can afford from a reputable supplier. Don't be cheap here.
4. Select `Edit Settings`
  1. Set hostname: `rpi`
  2.Tick `Set username and password`
  3. Set username: `pi`
  4. Set password: `spectro`
  5. Tick `Configure wireless LAN`
  6. Enter known wifi name (I use my mobile hotspot name so I can access this easily in the field)
  7. Enter wifi password I use my mobile hotspot password so I can access this easily in the field)
  8. Set Wireless LAN country: `AU`
  9. Tick `Set locale settings`
  10. Timezone: `Australia/Brisbane`
  11. Keyboard Layout: `US`
  12. Select Services Tab
  13. Tick `Enable SSH - Use password authentication`
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

### Add a new wifi connection

1. Insert the SD card into your computer.
2. Navigate to the root filesystem on the SD card. You should see a directory structure similar to a Linux system.
3. Find and edit the network configuration file. On Ubuntu 22.04 Server, this is typically located at /etc/netplan/50-cloud-init.yaml (or similar).
4. Open this file with a text editor. On Ubuntu: 

```sh
sudo vim 50-cloud-init.yaml
```

If you're on Windows, make sure to use an editor that preserves Linux line endings (like Notepad++, VS Code, etc.). 
5. Add your new WiFi network to the existing configuration. Here's an example of how to modify the file:

```sh
# This file is generated from information provided by the datasource.  Changes
# to it will not persist across an instance reboot.  To disable cloud-init's
# network configuration capabilities, write a file
# /etc/cloud/cloud.cfg.d/99-disable-network-config.cfg with the following:
# network: {config: disabled}
network:
    version: 2
    wifis:
        renderer: networkd
        wlan0:
            access-points:
                wifi_name:
                    password: password
                new_wifi_name:
                    password: new_password
            dhcp4: true
            optional: true
```
Make sure the indentation is consistent. 

6 If using the vim editor save (esc -> shift + : -> wq -> enter)
7. Insert the SD card back in the Pi and power it on. 
8. If you did it correctly it will show the new wifi connection and the IP address in the menu.

### Setting up the Raspberry Pi software for the LCD

```sh
cd
nano setup_pi.sh
```

Copy the code into the text file editor.

```sh
chmod +x setup_pi.sh
./setup_pi.sh
```
## Run the main script

```sh
cd pysb-app/
vim main.py
```

Copy the main.py script

```sh
mkdir assets
```

From whereever I have saved the fonts and images:

```sh
scp -r . pi@rpi.local:~/pysb-app/assets/
```

Now run the script:

```py
source venv/bin/activate
python3 main.py
```

## Raspberry Pi breakout PCB

A custom PCB was built to add a USB-C power input, power switch, real time clock (RTC) and battery, leak sensor and button inputs. An I2C and UART breakout was also added.

This complete schematic and board design can be found in the PCB folder. 

![image](https://github.com/user-attachments/assets/a64ad8f9-ed21-4b6f-b43d-9462d401118d)

![image](https://github.com/user-attachments/assets/7c0f146f-47bb-43a3-b808-453892763aac)

Leak sensor is based on the Blue Robotics leak sensor and uses Blue Robotics SOS leak sensor probes.

![image](https://github.com/user-attachments/assets/3c34cd6f-63d9-44bc-a1fa-a32ff59414d8)


