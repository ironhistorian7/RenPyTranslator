"""Validate vendored sources; importing never downloads or overwrites assets."""
from development_setup import verify_vendor


if __name__ == "__main__":
    verify_vendor()
