"""Bounded supervision for untrusted compressed-audio expansion.

Decode is not inference, but must not run outside the disk/memory safety budget.
Errors reject the import explicitly: no silently truncated transcript is accepted.
"""
import pathlib
import subprocess
import tempfile
import time
from resource_guard import ResourceSampler


def decode_supervised(command, chunks, *, sample=None, cancelled=lambda:False,
                      process_changed=lambda proc:None, timeout=1800,
                      max_output_bytes=4*1024**3, poll_seconds=.25):
    chunks=pathlib.Path(chunks)
    sampler=ResourceSampler()
    sample=sample or (lambda:sampler.collect(chunks))
    def check():
        if cancelled():
            raise RuntimeError('Importação cancelada; nenhum resultado parcial será aceito.')
        metrics=sample()
        free=metrics.get('disk_free_mib')
        if free is None or free<512:
            raise RuntimeError('Importação interrompida: disco sem reserva segura de 512 MiB.')
        ram=metrics.get('ram_available_mib')
        if ram is None or ram<512 or metrics.get('memory_pressure') in (None,'critical'):
            raise RuntimeError('Importação interrompida: pressão de memória indisponível ou crítica.')
        if sum(p.stat().st_size for p in chunks.glob('chunk_*.wav'))>max_output_bytes:
            raise RuntimeError('Importação excedeu limite seguro de áudio decodificado (4 GiB); divida o arquivo.')
    check()
    # File-backed bounded diagnostics avoid a PIPE deadlock during failed decoding.
    with tempfile.TemporaryFile() as errors:
        proc=subprocess.Popen(command,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,
                              stderr=errors,close_fds=True,start_new_session=True)
        process_changed(proc)
        started=time.monotonic()
        try:
            while proc.poll() is None:
                check()
                if errors.seek(0,2)>1024*1024:
                    raise RuntimeError('Importação interrompida: diagnóstico excedeu limite seguro.')
                if time.monotonic()-started>timeout:
                    raise RuntimeError('Importação excedeu tempo máximo de decodificação.')
                time.sleep(poll_seconds)
            check()
            return subprocess.CompletedProcess(command,proc.returncode)
        finally:
            if proc.poll() is None:
                proc.terminate()
                try:proc.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=1)
            process_changed(None)
