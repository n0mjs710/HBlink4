#!/bin/sh
set -eu
hdrop=/etc/systemd/system/hblink4.service.d/override.conf
ddrop=/etc/systemd/system/hblink4-dash.service.d/override.conf
rm -f "$hdrop" "$ddrop"
systemctl daemon-reload
systemctl restart hblink4
systemctl restart hblink4-dash
systemctl is-active hblink4
systemctl is-active hblink4-dash
echo 'production_units_restored; code/config remain in ~/HBlink4 and backup is ~/HBlink4-backup/pre-a5b90e8-20260929'
