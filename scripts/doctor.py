"""Check local tools and a running ORCAS stack without changing it."""

from __future__ import annotations

import shutil
import subprocess
import sys
import urllib.error
import urllib.request


def main() -> int:
    if not shutil.which("docker"):
        print("Docker is unavailable. Install Docker Engine/Desktop with Compose v2.")
        return 1
    config = subprocess.run(["docker", "compose", "config", "--quiet"], check=False)
    if config.returncode:
        print("Compose configuration is invalid.")
        return config.returncode
    print("Compose configuration: valid")
    failed = False
    for name, url in (
        ("gateway", "http://localhost:8000/ready"),
        ("retrieval", "http://localhost:8001/ready"),
        ("web", "http://localhost:8080/"),
        ("Prometheus", "http://localhost:9090/-/healthy"),
        ("Grafana", "http://localhost:3000/api/health"),
        ("Jaeger", "http://localhost:16686/"),
    ):
        try:
            with urllib.request.urlopen(url, timeout=3) as response:
                print(f"{name}: HTTP {response.status}")
        except (urllib.error.URLError, TimeoutError) as error:
            print(f"{name}: unavailable ({error})")
            failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
