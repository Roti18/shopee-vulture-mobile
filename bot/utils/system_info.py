"""
Informasi sistem: CPU, RAM, uptime.
"""
import psutil


def get_cpu_percent() -> float:
    return psutil.cpu_percent(interval=0.1)


def get_ram_mb() -> tuple[float, float]:
    """Return (used_mb, total_mb)."""
    mem = psutil.virtual_memory()
    return round(mem.used / 1024 / 1024, 1), round(mem.total / 1024 / 1024, 1)


def get_system_summary() -> str:
    cpu = get_cpu_percent()
    used, total = get_ram_mb()
    return f"CPU: {cpu}% | RAM: {used}/{total} MB"
