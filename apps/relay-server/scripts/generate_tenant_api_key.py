"""Generate a one-time institution API key and relay-side SHA-256 digest."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.auth import generate_api_key


def main() -> None:
    api_key, digest = generate_api_key()
    print("Copy the raw key to the trusted S2S client only:")
    print(f"RELAY_API_KEY={api_key}")
    print("Add only this digest to the relay TENANT_API_KEY_HASHES entry:")
    print(digest)


if __name__ == "__main__":
    main()
