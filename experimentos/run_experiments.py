import csv
import json
import subprocess
import sys
import time

HOST = "192.168.1.125"
SCENARIOS = [(1, 1024), (2, 1024), (4, 2048), (4, 4096)]
CONCURRENCY = [1, 2, 4, 8, 16, 32]
DURATION = 20
N = 50000
OUT = sys.argv[1] if len(sys.argv) > 1 else "resultados.csv"
VIRSH = "virsh -c qemu:///session"
SUDO = "echo osboxes.org | sudo -S -p ''"


def sh(target, cmd, check=True, timeout=300):
    r = subprocess.run(["ssh", "-o", "BatchMode=yes", target, cmd], capture_output=True, text=True, timeout=timeout)
    if check and r.returncode:
        raise RuntimeError(f"{target}: {cmd}\n{r.stderr}")
    return r.stdout


def provision(vcpus, mem_mb):
    sh(HOST, f"{VIRSH} destroy poi 2>/dev/null; "
             f"virt-xml --connect qemu:///session poi --edit --vcpus {vcpus} >/dev/null; "
             f"virt-xml --connect qemu:///session poi --edit --memory {mem_mb} >/dev/null; "
             f"{VIRSH} start poi >/dev/null", check=False)
    for _ in range(60):
        time.sleep(3)
        if subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "poi", "true"], capture_output=True).returncode == 0:
            break
    else:
        raise RuntimeError("VM não subiu")
    sh("poi", f"{SUDO} sysctl -q net.ipv4.ip_unprivileged_port_start=0; {SUDO} systemctl stop nginx 2>/dev/null; "
              "pkill -f '^python3 cpu.py'; sleep 0.5; "
              f"cd ~/app && (PORT=80 WORKERS={vcpus} nohup python3 cpu.py >cpu.log 2>&1 &); sleep 1")


def vm_stats(raw):
    cpu, mem = [], []
    total = None
    for line in raw.splitlines():
        if line.startswith("MemTotal"):
            total = int(line.split()[1]) // 1024
            continue
        parts = line.split()
        if len(parts) == 17 and parts[0].isdigit():
            free, buff, cache, idle = int(parts[3]), int(parts[4]), int(parts[5]), int(parts[14])
            cpu.append(100 - idle)
            mem.append(total - (free + buff + cache) // 1024)
    cpu, mem = cpu[1:] or cpu, mem[1:] or mem
    return sum(cpu) / len(cpu), sum(mem) / len(mem)


def run_level(vcpus, mem_mb, c):
    sh("poi", f"grep MemTotal /proc/meminfo > /tmp/vm.txt; nohup vmstat -n 1 {DURATION} >> /tmp/vm.txt 2>&1 &")
    out = sh(HOST, f"python3 /tmp/loadgen.py 'http://127.0.0.1:8080/?n={N}' {c} {DURATION}", timeout=DURATION + 300)
    time.sleep(2)
    cpu, mem = vm_stats(sh("poi", "cat /tmp/vm.txt"))
    r = json.loads(out.strip().splitlines()[-1])
    r.update(vcpus=vcpus, mem_mb=mem_mb, concurrency=c, cpu_pct=round(cpu, 1), mem_used_mb=round(mem))
    return r


subprocess.run(["scp", "-q", "loadgen.py", f"{HOST}:/tmp/loadgen.py"], check=True)
fields = ["vcpus", "mem_mb", "concurrency", "requests", "errors", "rps", "mean_ms", "p50_ms", "p95_ms", "p99_ms", "cpu_pct", "mem_used_mb"]
with open(OUT, "w", newline="") as f:
    w = csv.DictWriter(f, fields)
    w.writeheader()
    for vcpus, mem_mb in SCENARIOS:
        print(f"== {vcpus} vCPU / {mem_mb} MB", flush=True)
        provision(vcpus, mem_mb)
        for c in CONCURRENCY:
            row = run_level(vcpus, mem_mb, c)
            w.writerow({k: (round(row[k], 2) if isinstance(row[k], float) else row[k]) for k in fields})
            f.flush()
            print(f"  c={c:<3} rps={row['rps']:.1f} p95={row['p95_ms']:.0f}ms cpu={row['cpu_pct']}% mem={row['mem_used_mb']}MB err={row['errors']}", flush=True)
