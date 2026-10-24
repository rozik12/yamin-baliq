"""График продаж (PNG) на matplotlib."""
import io

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ACCENT = "#B5472B"
SOFT = "#E8A87C"


def render(title, series, series_title, top, top_title, unit):
    """series: [(подпись, значение)]; top: [(название, значение)]. Возвращает PNG-байты."""
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(9, 9), gridspec_kw={"height_ratios": [3, 2]})
    fig.patch.set_facecolor("white")
    labels = [s[0] for s in series]
    vals = [s[1] for s in series]
    ax1.bar(range(len(vals)), vals, color=ACCENT, width=0.7)
    ax1.set_title(f"{title}\n{series_title}", fontsize=13, fontweight="bold")
    step = max(1, len(labels) // 12)
    ax1.set_xticks(range(0, len(labels), step))
    ax1.set_xticklabels(labels[::step], rotation=45, ha="right", fontsize=9)
    ax1.set_ylabel(unit)
    ax1.grid(axis="y", alpha=0.3)
    for i, v in enumerate(vals):
        if v and len(vals) <= 16:
            ax1.text(i, v, str(v), ha="center", va="bottom", fontsize=8)
    if top:
        names = [t[0] if len(t[0]) <= 26 else t[0][:25] + "…" for t in top][::-1]
        tv = [t[1] for t in top][::-1]
        ax2.barh(names, tv, color=SOFT)
        for i, v in enumerate(tv):
            ax2.text(v, i, f" {v}", va="center", fontsize=9)
        ax2.set_title(top_title, fontsize=12, fontweight="bold")
        ax2.set_xlabel(unit)
    else:
        ax2.axis("off")
    for ax in (ax1, ax2):
        for sp in ("top", "right"):
            ax.spines[sp].set_visible(False)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110)
    plt.close(fig)
    return buf.getvalue()
