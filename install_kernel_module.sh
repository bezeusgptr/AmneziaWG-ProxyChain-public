#!/bin/bash
set -e

echo "Installing AmneziaWG from official PPA..."
apt-get update
apt-get install -y software-properties-common python3-launchpadlib gnupg2 linux-headers-$(uname -r)

# Add Amnezia PPA key
apt-key adv --keyserver keyserver.ubuntu.com --recv-keys 57290828 || \
apt-key adv --keyserver hkp://keyserver.ubuntu.com:80 --recv-keys 57290828

# Add focal PPA (works for Debian)
echo "deb https://ppa.launchpadcontent.net/amnezia/ppa/ubuntu focal main" > /etc/apt/sources.list.d/amnezia.list

apt-get update
apt-get install -y amneziawg

echo "Loading module..."
modprobe amneziawg || true
lsmod | grep amneziawg

echo "Installation complete."
