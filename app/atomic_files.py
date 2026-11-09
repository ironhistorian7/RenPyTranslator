"""Retry only transient Windows file replacement failures; never rewrite a batch."""
import time


def replace_cache_file(temporary, destination):
    try:
        temporary.replace(destination)
    except PermissionError as exc:
        if getattr(exc, 'winerror', None) not in (None, 5, 32):
            raise
        print('Cache busy; retrying this batch commit once in 30 seconds.', flush=True)
        time.sleep(30)
        temporary.replace(destination)
