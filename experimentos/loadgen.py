import json
import sys
import threading
import time
import urllib.request

url, concurrency, duration = sys.argv[1], int(sys.argv[2]), float(sys.argv[3])

latencies = []
errors = 0
lock = threading.Lock()
deadline = time.monotonic() + duration


def worker():
    global errors
    local = []
    failed = 0
    while time.monotonic() < deadline:
        start = time.monotonic()
        try:
            urllib.request.urlopen(url, timeout=120).read()
            local.append(time.monotonic() - start)
        except Exception:
            failed += 1
    with lock:
        latencies.extend(local)
        errors += failed


threads = [threading.Thread(target=worker) for _ in range(concurrency)]
for t in threads:
    t.start()
for t in threads:
    t.join()

latencies.sort()


def pct(p):
    return latencies[min(len(latencies) - 1, int(len(latencies) * p))] * 1000 if latencies else 0


print(json.dumps({
    "requests": len(latencies),
    "errors": errors,
    "rps": len(latencies) / duration,
    "mean_ms": sum(latencies) / len(latencies) * 1000 if latencies else 0,
    "p50_ms": pct(0.50),
    "p95_ms": pct(0.95),
    "p99_ms": pct(0.99),
}))
