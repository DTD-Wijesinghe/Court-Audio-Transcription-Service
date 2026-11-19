# Ubuntu temporary-storage cleanup

The application cleans each job workspace itself. This timer is a crash/reboot safety net and removes only stale directories beneath `TEMP_STORAGE_ROOT` (default `/var/tmp/court-transcription`).

Adjust `User`, `Group`, `WorkingDirectory`, and `ExecStart` in the service if the deployment uses different values. Then install and enable it:

```bash
sudo install -m 0644 court-transcription-temp-cleanup.service /etc/systemd/system/
sudo install -m 0644 court-transcription-temp-cleanup.timer /etc/systemd/system/
sudo install -d -o court-transcription -g court-transcription -m 0700 /var/tmp/court-transcription
sudo systemctl daemon-reload
sudo systemctl enable --now court-transcription-temp-cleanup.timer
systemctl list-timers court-transcription-temp-cleanup.timer
```

Recommended entries in `/etc/court-transcription/backend.env`:

```env
TEMP_STORAGE_ROOT=/var/tmp/court-transcription
TEMP_STORAGE_STALE_HOURS=24
TEMP_STORAGE_MIN_FREE_GB=5
TEMP_STORAGE_WARNING_FREE_GB=8
TEMP_STORAGE_RESERVE_MULTIPLIER=3
MAX_CONCURRENT_AUDIO_JOBS=2
```

Run a manual cleanup and inspect reclaimed bytes with:

```bash
cd /opt/court-transcription/backend
/opt/court-transcription/.venv/bin/python -m court_transcription.cleanup_temp
```
