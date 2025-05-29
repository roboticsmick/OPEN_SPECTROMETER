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

If the ssh isn't working, check that your connected via your hotspot. Connect to your hotspot via your Ubuntu laptop. Two hotspot connections should now be showing. Check the IP address for your latop.

```sh
ifconfig
# Example:
# wlp3s0: flags=4163<UP,BROADCAST,RUNNING,MULTICAST>  mtu 1500
#        inet 10.119.124.83  netmask 255.255.255.0  broadcast 10.119.124.255
#        inet6 fe80::3c24:e953:7879:4e71  prefixlen 64  scopeid 0x20<link>
#        ether 54:e4:ed:74:18:cd  txqueuelen 1000  (Ethernet)
#        RX packets 1052664  bytes 1320322796 (1.3 GB)
#        RX errors 0  dropped 0  overruns 0  frame 0
#        TX packets 441016  bytes 54203222 (54.2 MB)
#        TX errors 0  dropped 4 overruns 0  carrier 0  collisions 0
```
Copy for first, leaving the last digit as a 0/24.

```sh
nmap -sn 10.119.124.0/24
```

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



