import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

df = pd.read_csv(sys.argv[1] if len(sys.argv) > 1 else "resultados.csv")
df["cenario"] = df.vcpus.astype(str) + " vCPU / " + (df.mem_mb / 1024).astype(str) + " GB"

metrics = [
    ("rps", "Vazão (req/s)", "vazao.png"),
    ("p95_ms", "Tempo de resposta p95 (ms)", "latencia_p95.png"),
    ("cpu_pct", "Utilização de CPU da VM (%)", "cpu.png"),
    ("mem_used_mb", "Memória usada na VM (MB)", "memoria.png"),
]
for col, label, name in metrics:
    fig, ax = plt.subplots(figsize=(7, 4))
    for cenario, g in df.groupby("cenario", sort=False):
        ax.plot(g.concurrency, g[col], marker="o", label=cenario)
    ax.set_xscale("log", base=2)
    ax.set_xticks(sorted(df.concurrency.unique()))
    ax.set_xticklabels(sorted(df.concurrency.unique()))
    ax.set_xlabel("Demanda (clientes concorrentes)")
    ax.set_ylabel(label)
    ax.grid(alpha=0.3)
    ax.legend()
    fig.tight_layout()
    fig.savefig(name, dpi=150)
    plt.close(fig)
print("gráficos gerados")
