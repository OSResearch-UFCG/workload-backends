import csv
import json
import subprocess
import sys
import time
import urllib.request

# ─── Uso ───────────────────────────────────────────────────────────────────────
#   python3 run_experiments.py <app> [saida.csv]
#   <app> = cpu | io | mem
# Exemplos:
#   python3 run_experiments.py cpu resultados_cpu.csv
#   python3 run_experiments.py io  resultados_io.csv
#   python3 run_experiments.py mem resultados_mem.csv
# IMPORTANTE: rode um de cada vez (nunca os 3 sob carga simultaneamente).
# ──────────────────────────────────────────────────────────────────────────────

VM = "poi"
SSH_PORT = 2222
VM_USER = "osboxes"
SCENARIOS = [(1, 1024), (2, 1024), (2, 2048), (4, 2048)]
CONCURRENCY = [1, 2, 4, 8, 16, 32]
DURATION = 30
VBOX = "VBoxManage"

# Configuração por aplicação. "weight" é o parâmetro de carga por requisição;
# calibre (ver README/instruções) para cada app até ~0,6-1,0 s por requisição.
APPS = {
    "cpu": dict(dir="~/app",     port=8080, param="n",  weight=500000, workers=True),
    "io":  dict(dir="~/app-io",  port=8081, param="mb", weight=50,    workers=False),
    "mem": dict(dir="~/app-mem", port=8082, param="mb", weight=800,    workers=False),
}

if len(sys.argv) < 2 or sys.argv[1] not in APPS:
    sys.exit("Uso: python3 run_experiments.py <cpu|io|mem> [saida.csv]")
APP = sys.argv[1]
CFG = APPS[APP]
OUT = sys.argv[2] if len(sys.argv) > 2 else f"resultados_{APP}.csv"
PORT = CFG["port"]
APP_URL = f"http://127.0.0.1:{PORT}/?{CFG['param']}={CFG['weight']}"
LIGHT_URL = f"http://127.0.0.1:{PORT}/?{CFG['param']}=1"

SSH = ["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no",
       "-p", str(SSH_PORT), f"{VM_USER}@127.0.0.1"]


def guest(cmd, check=True, timeout=300):
    r = subprocess.run(SSH + [cmd], capture_output=True, text=True, timeout=timeout)
    if check and r.returncode:
        raise RuntimeError(f"guest: {cmd}\n{r.stderr}")
    return r.stdout


def vbox(*args, check=True):
    return subprocess.run([VBOX, *args], capture_output=True, text=True, check=check)


def vm_state():
    r = vbox("showvminfo", VM, "--machinereadable", check=False)
    for line in r.stdout.splitlines():
        if line.startswith("VMState="):
            return line.split("=")[1].strip('"')
    return "unknown"


def start_cmd(vcpus):
    env = f"PORT={PORT}"
    if CFG["workers"]:
        env += f" WORKERS={vcpus}"
    return f"cd {CFG['dir']} && ({env} nohup python3 server.py >app.log 2>&1 &); sleep 1"


def provision(vcpus, mem_mb):
    vbox("controlvm", VM, "poweroff", check=False)
    for _ in range(40):
        if vm_state() == "poweroff":
            break
        time.sleep(1)
    vbox("modifyvm", VM, "--cpus", str(vcpus), "--memory", str(mem_mb), "--ioapic", "on")
    vbox("startvm", VM, "--type", "headless")
    for _ in range(60):
        time.sleep(3)
        r = subprocess.run(SSH + ["-o", "ConnectTimeout=5", "true"], capture_output=True)
        if r.returncode == 0:
            break
    else:
        raise RuntimeError("VM não respondeu ao SSH após o boot")
    guest(start_cmd(vcpus), check=False)
    for _ in range(20):
        try:
            urllib.request.urlopen(LIGHT_URL, timeout=10).read()
            return
        except Exception:
            time.sleep(1)
    raise RuntimeError(f"Aplicação '{APP}' não respondeu após iniciar (porta {PORT})")


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
    guest(f"grep MemTotal /proc/meminfo > /tmp/vm.txt; "
          f"nohup vmstat -n 1 {DURATION} >> /tmp/vm.txt 2>&1 &")
    out = subprocess.run(
        ["python3", "loadgen.py", APP_URL, str(c), str(DURATION)],
        capture_output=True, text=True, timeout=DURATION + 300, check=True,
    ).stdout
    time.sleep(2)
    cpu, mem = vm_stats(guest("cat /tmp/vm.txt"))
    r = json.loads(out.strip().splitlines()[-1])
    r.update(vcpus=vcpus, mem_mb=mem_mb, concurrency=c,
             cpu_pct=round(cpu, 1), mem_used_mb=round(mem))
    return r


def main():
    print(f"App em teste: {APP}  |  URL: {APP_URL}", flush=True)
    fields = ["vcpus", "mem_mb", "concurrency", "requests", "errors", "rps",
              "mean_ms", "p50_ms", "p95_ms", "p99_ms", "cpu_pct", "mem_used_mb"]
    with open(OUT, "w", newline="") as f:
        w = csv.DictWriter(f, fields)
        w.writeheader()
        for vcpus, mem_mb in SCENARIOS:
            print(f"== {vcpus} vCPU / {mem_mb} MB", flush=True)
            provision(vcpus, mem_mb)
            for c in CONCURRENCY:
                row = run_level(vcpus, mem_mb, c)
                w.writerow({k: (round(row[k], 2) if isinstance(row[k], float) else row[k])
                            for k in fields})
                f.flush()
                print(f"  c={c:<3} rps={row['rps']:.1f} p95={row['p95_ms']:.0f}ms "
                      f"cpu={row['cpu_pct']}% mem={row['mem_used_mb']}MB err={row['errors']}",
                      flush=True)
    print(f"\nFim. Resultados em {OUT}. Gere os gráficos com: python3 plot.py {OUT}")


if __name__ == "__main__":
    main()
