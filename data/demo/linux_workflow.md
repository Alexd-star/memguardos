# systemd MemoryMax workflow

On openKylin or other systemd-based Linux distributions, a service can be constrained by setting `MemoryMax=` in the unit file or an override file.

Example:

```ini
[Service]
MemoryMax=512M
CPUQuota=50%
```

Reload systemd with `systemctl daemon-reload` and restart the service.

