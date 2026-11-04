"""Low-cost telemetry and conservative, uncalibrated meeting inference policy.

No process killing, model calls, privileged probes, or artificial load here.
Unknown values remain None. Policy uses current samples, never session peaks.
"""
from __future__ import annotations

import ctypes
import math
import os
import pathlib
import re
import shutil
import subprocess
import sys
import threading
import time

LEVEL_NAMES = ('normal', 'reduced_analysis', 'llm_paused', 'transcription_deferred', 'recording_only')


class ResourceGuard:
    def __init__(self, *, clock=time.monotonic):
        self.clock = clock
        self.lock = threading.RLock()
        self.enabled = True
        self.level = 0
        self.reason = 'awaiting_telemetry'
        self.latest = {}
        self.sampled_at = None
        self.bad_since = self.good_since = None
        self.changed_at = self.clock()
        self.recording_only = False
        self.failure = ''

    def update(self, sample):
        with self.lock:
            now = self.clock()
            sample = dict(sample)
            for key in ('cpu_percent', 'ram_available_mib', 'disk_free_mib', 'swap_out_mib'):
                value = sample.get(key)
                if value is not None and (type(value) not in (int, float) or not math.isfinite(value) or value < 0):
                    sample[key] = None
            if sample.get('memory_pressure') not in ('normal', 'warning', 'critical'):
                sample['memory_pressure'] = None
            self.latest = dict(sample)
            self.sampled_at = now
            cpu = sample.get('cpu_percent')
            ram = sample.get('ram_available_mib')
            disk = sample.get('disk_free_mib')
            pressure = sample.get('memory_pressure')
            swap = sample.get('swap_out_mib')
            unknown = cpu is None or ram is None or disk is None or pressure is None
            total = sample.get('ram_total_mib')
            if not isinstance(total, (int, float)) or isinstance(total, bool) or not math.isfinite(total) or total <= 0:
                total = 0
            # Reserve scales with RAM: on a 32-GiB workstation intervene before
            # the last few GiB, leaving room for screen sharing and the OS.
            critical_ram = max(512, total * .08)
            warning_ram = max(1536, total * .15)
            recovery_ram = max(2048, total * .20)
            severe = ((cpu is not None and cpu >= 95) or (ram is not None and ram < critical_ram) or pressure == 'critical'
                      or (disk is not None and disk < 512) or (swap is not None and swap >= 128))
            moderate = ((cpu is not None and cpu >= 75) or (ram is not None and ram < warning_ram)
                        or pressure == 'warning' or (swap is not None and swap >= 16)
                        or (disk is not None and disk < 1024))
            if unknown or severe:
                self.level = 4
                self.reason = 'telemetry_unavailable' if unknown else 'critical_resource_pressure'
                self.bad_since = self.good_since = None
                self.changed_at = now
            elif moderate:
                self.good_since = None
                if self.bad_since is None:
                    self.bad_since = now
                if now - self.bad_since >= 10:
                    self.level = min(4, self.level + 1)
                    self.reason = 'sustained_resource_pressure'
                    self.bad_since = now
                    self.changed_at = now
            else:
                self.bad_since = None
                # A recovery sample must have real headroom, not just miss a tripwire.
                healthy = (cpu is not None and cpu < 60 and ram is not None and ram >= recovery_ram
                           and pressure == 'normal' and disk is not None and disk >= 1536 and (swap is None or swap < 4))
                if not healthy:
                    self.good_since = None
                elif self.good_since is None:
                    self.good_since = now
                elif now - self.good_since >= 60 and now - self.changed_at >= 60:
                    self.level = max(0, self.level - 1)
                    self.reason = 'healthy' if self.level == 0 else 'recovering_with_cooldown'
                    self.good_since = now
                    self.changed_at = now
                if self.level == 0:
                    self.reason = 'healthy'

    def set_recording_only(self, value):
        if type(value) is not bool:
            raise ValueError('recording_only deve ser booleano.')
        with self.lock:
            self.recording_only = value

    def fail(self, reason):
        with self.lock:
            self.failure = reason

    def snapshot(self, *, analysis_interval_s=180, transcription_interval_s=45) -> dict:
        with self.lock:
            age = None if self.sampled_at is None else max(0, self.clock() - self.sampled_at)
            stale = self.enabled and (age is None or age > 15)
            level, reason = self.level, self.reason
            if stale:
                level, reason = 4, 'telemetry_stale' if age is not None else 'awaiting_telemetry'
            if self.failure:
                level, reason = 4, self.failure
            if self.recording_only:
                level, reason = 4, 'user_recording_only'
            if not self.enabled and not self.recording_only and not self.failure:
                level, reason = 0, 'inactive'
            llm = level == 0  # No user-defined card priority: suspend all rather than pick silently.
            whisper = level < 3
            return dict(level=level, level_name=LEVEL_NAMES[level], reason=reason,
                        recording_only=self.recording_only, telemetry_stale=stale,
                        sample_age_s=None if age is None else round(age, 2), latest=dict(self.latest),
                        requested_analysis_interval_s=analysis_interval_s,
                        requested_transcription_interval_s=transcription_interval_s,
                        effective_analysis_interval_s=analysis_interval_s if llm else None,
                        effective_transcription_interval_s=(transcription_interval_s * (2 if level == 2 else 1)) if whisper else None,
                        llm_allowed=llm, transcription_allowed=whisper,
                        calibration='conservative_defaults_not_hardware_validated')

    def allowed(self, kind):
        return self.snapshot()['llm_allowed' if kind == 'llm' else 'transcription_allowed']


def _probe(command):
    result = subprocess.run(command, capture_output=True, text=True, timeout=2)
    if result.returncode:
        raise OSError('telemetry probe failed')
    return result.stdout


class ResourceSampler:
    def __init__(self, *, clock=time.monotonic):
        self.clock = clock
        self.previous_cpu = self.previous_swap = self.previous_process = None
        self.previous_children = {}

    def _cpu_ticks(self):
        if sys.platform == 'darwin':
            lib = ctypes.CDLL('/usr/lib/libSystem.B.dylib')
            ticks = (ctypes.c_uint * 4)()
            count = ctypes.c_uint(4)
            host = lib.mach_host_self()
            try:
                if lib.host_statistics(host, 3, ctypes.byref(ticks), ctypes.byref(count)):
                    raise OSError('host_statistics failed')
                return sum(ticks), ticks[2]
            finally:
                lib.mach_port_deallocate(lib.mach_task_self(), host)
        fields = pathlib.Path('/proc/stat').read_text().splitlines()[0].split()[1:]
        ticks = [int(v) for v in fields[:8]]
        return sum(ticks), ticks[3] + ticks[4]

    def collect(self, disk_path, controlled_pids=()):
        now = self.clock()
        sample: dict = dict(cpu_percent=None, assistant_cpu_percent=None, ram_total_mib=None,
                      ram_available_mib=None, memory_pressure=None, swap_used_mib=None,
                      swap_out_mib=None, disk_free_mib=None, thermal_state=None,
                      controlled_cpu_percent=None, controlled_rss_mib=None,
                      sampled_monotonic=now)
        try:
            total, idle = self._cpu_ticks()
            if self.previous_cpu is not None:
                delta = total - self.previous_cpu[0]
                if delta > 0:
                    sample['cpu_percent'] = round(max(0, min(100, 100 * (1 - (idle - self.previous_cpu[1]) / delta))), 1)
            self.previous_cpu = total, idle
        except (OSError, ValueError, AttributeError):
            pass
        process = time.process_time()
        if self.previous_process and now > self.previous_process[0]:
            sample['assistant_cpu_percent'] = round(100 * (process - self.previous_process[1]) / (now - self.previous_process[0]), 1)
        self.previous_process = now, process
        pids = sorted({pid for pid in controlled_pids if type(pid) is int and pid > 0})
        current = {}
        if pids:
            try:
                rows = _probe(['/bin/ps', '-p', ','.join(map(str, pids)), '-o', 'pid=', '-o', 'time=', '-o', 'rss='])
                rss, cpu_delta, measured = 0, 0.0, False
                for line in rows.splitlines():
                    pid, raw_cpu, raw_rss = line.split()
                    pid = int(pid)
                    days, stamp = raw_cpu.split('-', 1) if '-' in raw_cpu else ('0', raw_cpu)
                    cpu = 0.0
                    for field in stamp.split(':'):
                        cpu = cpu * 60 + float(field)
                    cpu += int(days) * 86400
                    rss += int(raw_rss) / 1024
                    current[pid] = (now, cpu)
                    previous = self.previous_children.get(pid)
                    if previous and now > previous[0]:
                        cpu_delta += max(0, cpu - previous[1]) / (now - previous[0]) * 100
                        measured = True
                sample['controlled_rss_mib'] = round(rss, 1)
                if measured:
                    sample['controlled_cpu_percent'] = round(cpu_delta, 1)
            except (OSError, ValueError, subprocess.SubprocessError):
                pass
        self.previous_children = current
        swap_out = None
        try:
            if sys.platform == 'darwin':
                sample['ram_total_mib'] = int(_probe(['/usr/sbin/sysctl', '-n', 'hw.memsize']).strip()) / 1048576
                vm = _probe(['/usr/bin/vm_stat'])
                page = int(re.search(r'page size of ([0-9]+) bytes', vm).group(1))
                values = {name: int(value) for name, value in re.findall(r'^([^:]+):\s*([0-9]+)\.', vm, re.M)}
                sample['ram_available_mib'] = sum(values.get(name, 0) for name in ('Pages free', 'Pages inactive', 'Pages speculative')) * page / 1048576
                swap_out = values.get('Swapouts')
                if swap_out is not None:
                    swap_out *= page / 1048576
                pressure = int(_probe(['/usr/sbin/sysctl', '-n', 'kern.memorystatus_vm_pressure_level']).strip())
                sample['memory_pressure'] = {1: 'normal', 2: 'warning', 4: 'critical'}.get(pressure)
                swap = _probe(['/usr/sbin/sysctl', '-n', 'vm.swapusage'])
                match = re.search(r'used\s*=\s*([\d.]+)([MG])', swap)
                if match:
                    sample['swap_used_mib'] = float(match[1]) * (1024 if match[2] == 'G' else 1)
            else:
                mem = {line.split(':')[0]: int(line.split()[1]) / 1024 for line in pathlib.Path('/proc/meminfo').read_text().splitlines() if ':' in line and len(line.split()) >= 2 and line.split()[1].isdigit()}
                sample.update(ram_total_mib=mem['MemTotal'], ram_available_mib=mem['MemAvailable'], swap_used_mib=mem['SwapTotal'] - mem['SwapFree'])
                psi = pathlib.Path('/proc/pressure/memory').read_text()
                avg = float(re.search(r'some avg10=([\d.]+)', psi)[1])
                sample['memory_pressure'] = 'critical' if avg >= 20 else 'warning' if avg >= 5 else 'normal'
                vm = dict(line.split() for line in pathlib.Path('/proc/vmstat').read_text().splitlines())
                swap_out = int(vm['pswpout']) * os.sysconf('SC_PAGE_SIZE') / 1048576
        except (OSError, ValueError, AttributeError, KeyError, TypeError, subprocess.SubprocessError):
            pass
        if swap_out is not None and self.previous_swap is not None:
            sample['swap_out_mib'] = round(max(0, swap_out - self.previous_swap), 3)
        self.previous_swap = swap_out
        try:
            path = pathlib.Path(disk_path)
            while not path.exists() and path.parent != path:
                path = path.parent
            sample['disk_free_mib'] = round(shutil.disk_usage(path).free / 1048576, 1)
        except OSError:
            pass
        return sample
