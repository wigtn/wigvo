"""ACL 2026 논문용 Figure 생성 스크립트.

Figures:
  - Figure 2: Session A / Session B 레이턴시 히스토그램
  - Figure 3: 발화 길이 vs Session B 레이턴시 Scatter Plot
  - Figure 4: Session B 레이턴시 컴포넌트 분해

Usage:
  python scripts/eval/generate_paper_figures.py
"""

import json
from pathlib import Path

import matplotlib
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker
import numpy as np
from scipy import stats

# ---------------------------------------------------------------------------
# 공통 설정
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent  # docs/paper/eval → project root
DATA_PATH = _PROJECT_ROOT / "scripts" / "eval" / "paper_metrics.json"
FIG_DIR = Path(__file__).parent.parent / "figures"  # docs/paper/figures
FIG_DIR.mkdir(parents=True, exist_ok=True)

# 폰트: Times New Roman (없으면 DejaVu Sans fallback)
_PREFERRED_FONTS = ["Times New Roman", "DejaVu Sans"]
_available = set(matplotlib.font_manager.findSystemFonts(fontpaths=None, fontext="ttf"))
_font_family = "DejaVu Sans"
for f in _PREFERRED_FONTS:
    try:
        matplotlib.font_manager.findfont(f, fallback_to_default=False)
        _font_family = f
        break
    except ValueError:
        continue

plt.rcParams.update({
    "font.family": "serif" if _font_family == "Times New Roman" else "sans-serif",
    "font.serif": [_font_family],
    "font.sans-serif": [_font_family],
    "font.size": 11,
    "axes.titlesize": 12,
    "axes.labelsize": 11,
    "legend.fontsize": 9,
    "xtick.labelsize": 10,
    "ytick.labelsize": 10,
    "figure.facecolor": "white",
    "axes.facecolor": "white",
    "axes.grid": True,
    "grid.alpha": 0.3,
    "grid.color": "#888888",
    "savefig.facecolor": "white",
    "savefig.dpi": 300,
})


def _save(fig: plt.Figure, name: str) -> None:
    """PNG + PDF 저장."""
    for ext in ("png", "pdf"):
        path = FIG_DIR / f"{name}.{ext}"
        fig.savefig(path, dpi=300, bbox_inches="tight")
        print(f"  Saved: {path}")


def _comma(n: float) -> str:
    """숫자를 천 단위 콤마 포맷."""
    if n == int(n):
        return f"{int(n):,}"
    return f"{n:,.0f}"


# ---------------------------------------------------------------------------
# 데이터 로드
# ---------------------------------------------------------------------------

with open(DATA_PATH) as f:
    data = json.load(f)


# ===========================================================================
# Figure 2: Session A / Session B 레이턴시 히스토그램
# ===========================================================================

def figure2() -> None:
    print("Generating Figure 2: Latency Histograms ...")

    sa_raw = np.array(data["session_a"]["latency_raw"])
    sb_raw = np.array(data["session_b"]["e2e_latency_raw"])
    sa = data["session_a"]["latency_ms"]
    sb = data["session_b"]["e2e_latency_ms"]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(10, 4))

    # --- Session A ---
    ax1.hist(
        sa_raw, bins=15, range=(0, 2500),
        color="#2980B9", edgecolor="white", linewidth=0.5,
        hatch="//", alpha=0.85,
    )
    ax1.axvline(sa["p50"], color="black", linestyle="--", linewidth=1.2,
                label=f'P50={_comma(sa["p50"])}ms')
    ax1.axvline(sa["p95"], color="#E74C3C", linestyle="--", linewidth=1.2,
                label=f'P95={_comma(sa["p95"])}ms')
    ax1.set_xlim(0, 2500)
    ax1.set_xlabel("Latency (ms)")
    ax1.set_ylabel("Count")
    ax1.set_title(f'Session A: User \u2192 Recipient (N={sa["n"]} turns)')
    ax1.legend(loc="upper right", framealpha=0.9)
    ax1.text(
        0.97, 0.72,
        f'Mean={_comma(sa["mean"])}ms\nStd={_comma(sa["std"])}ms',
        transform=ax1.transAxes, ha="right", va="top",
        fontsize=9, bbox=dict(boxstyle="round,pad=0.3", fc="white", ec="#cccccc", alpha=0.9),
    )

    # --- Session B ---
    ax2.hist(
        sb_raw, bins=15, range=(0, 11000),
        color="#27AE60", edgecolor="white", linewidth=0.5,
        hatch="\\\\", alpha=0.85,
    )
    ax2.axvline(sb["p50"], color="black", linestyle="--", linewidth=1.2,
                label=f'P50={_comma(sb["p50"])}ms')
    ax2.axvline(sb["p95"], color="#E74C3C", linestyle="--", linewidth=1.2,
                label=f'P95={_comma(sb["p95"])}ms')
    ax2.set_xlim(0, 11000)
    ax2.set_xlabel("Latency (ms)")
    ax2.set_ylabel("Count")
    ax2.set_title(f'Session B: Recipient \u2192 User (N={sb["n"]} turns)')
    ax2.legend(loc="upper right", framealpha=0.9)
    # Compute STT % from V2V data only (T2V has STT > E2E inversion)
    sb_stt_stats = data["session_b"].get("stt_latency_ms", {})
    sb_e2e_stats = data["session_b"].get("e2e_latency_ms", {})
    if sb_stt_stats.get("mean", 0) > 0 and sb_e2e_stats.get("mean", 0) > 0:
        stt_pct = sb_stt_stats["mean"] / sb_e2e_stats["mean"] * 100
        stt_label = f"\nSTT: {stt_pct:.1f}% of latency"
    else:
        stt_label = ""
    ax2.text(
        0.97, 0.72,
        f'Mean={_comma(sb["mean"])}ms\nStd={_comma(sb["std"])}ms{stt_label}',
        transform=ax2.transAxes, ha="right", va="top",
        fontsize=9, bbox=dict(boxstyle="round,pad=0.3", fc="white", ec="#cccccc", alpha=0.9),
    )

    fig.suptitle("End-to-End Latency Distribution over Live PSTN Calls", fontsize=13, y=1.02)
    fig.tight_layout()
    _save(fig, "figure2_latency_histogram")
    plt.close(fig)


# ===========================================================================
# Figure 3: 발화 길이 vs Session B 레이턴시 Scatter Plot
# ===========================================================================

def figure3() -> None:
    print("Generating Figure 3: Utterance Length Scatter ...")

    scatter_data = data["session_b"]["length_vs_latency"]["scatter"]
    x = np.array([d["char_len"] for d in scatter_data])
    y = np.array([d["latency_ms"] for d in scatter_data])

    # Linear regression
    slope, intercept, r_value, p_value, std_err = stats.linregress(x, y)
    x_fit = np.linspace(0, 130, 200)
    y_fit = slope * x_fit + intercept

    # 95% confidence interval
    n = len(x)
    x_mean = np.mean(x)
    se = np.sqrt(np.sum((y - (slope * x + intercept)) ** 2) / (n - 2))
    t_val = stats.t.ppf(0.975, n - 2)
    ci = t_val * se * np.sqrt(1 / n + (x_fit - x_mean) ** 2 / np.sum((x - x_mean) ** 2))

    # Bucket means
    buckets = data["session_b"]["length_vs_latency"]["buckets"]
    bin_info = [
        ("\u226430", buckets["0-30"]["n"], buckets["0-30"]["mean"], 15),
        ("31\u201360", buckets["31-60"]["n"], buckets["31-60"]["mean"], 45),
        ("61\u2013100", buckets["61-100"]["n"], buckets["61-100"]["mean"], 80),
        ("100+", buckets["100+"]["n"], buckets["100+"]["mean"], 115),
    ]

    fig, ax = plt.subplots(figsize=(7, 5))

    # Scatter
    ax.scatter(x, y, color="#27AE60", alpha=0.5, s=40, marker="o",
               edgecolors="white", linewidths=0.3, label="Individual turns", zorder=2)

    # Regression + CI
    ax.plot(x_fit, y_fit, color="#E74C3C", linewidth=1.8, label="Linear regression", zorder=3)
    ax.fill_between(x_fit, y_fit - ci, y_fit + ci, color="#E74C3C", alpha=0.12, zorder=1)

    # Bin means (diamonds)
    for label, count, mean, bx in bin_info:
        ax.scatter(bx, mean, color="black", marker="D", s=150, zorder=5)
        # 레이블 위치 조정
        offset_x, ha = (4, "left")
        if bx > 100:
            offset_x, ha = (-4, "right")
        ax.annotate(
            f"{label} (N={count})\n{_comma(mean)}ms",
            (bx, mean), xytext=(offset_x, 8), textcoords="offset points",
            fontsize=8, ha=ha, va="bottom",
            bbox=dict(boxstyle="round,pad=0.2", fc="white", ec="#cccccc", alpha=0.85),
        )

    # 3000ms threshold
    ax.axhline(3000, color="#95A5A6", linestyle=":", linewidth=1.2, alpha=0.7)
    ax.text(128, 3100, "conversational threshold", ha="right", va="bottom",
            fontsize=8, color="#7F8C8D", style="italic")

    # Pearson annotation
    p_str = "p < 0.001" if p_value < 0.001 else f"p = {p_value:.4f}"
    ax.text(
        0.97, 0.05,
        f"Pearson r = {r_value:.3f}, {p_str}",
        transform=ax.transAxes, ha="right", va="bottom",
        fontsize=9, bbox=dict(boxstyle="round,pad=0.3", fc="white", ec="#cccccc", alpha=0.9),
    )

    # Bin means legend entry (manual)
    ax.scatter([], [], color="black", marker="D", s=80, label="Bin means")

    ax.set_xlim(0, 130)
    ax.set_ylim(0, 11000)
    ax.set_xlabel("Utterance Length (characters)")
    ax.set_ylabel("Session B E2E Latency (ms)")
    ax.set_title(f"Utterance Length vs. Session B Latency\n(Pearson r = {r_value:.3f})")
    ax.legend(loc="upper left", framealpha=0.9)
    ax.yaxis.set_major_formatter(ticker.FuncFormatter(lambda v, _: f"{int(v):,}"))

    fig.tight_layout()
    _save(fig, "figure3_utterance_scatter")
    plt.close(fig)


# ===========================================================================
# Figure 4: Session B 레이턴시 컴포넌트 분해
# ===========================================================================

def figure4() -> None:
    print("Generating Figure 4: Latency Breakdown ...")

    sb_e2e = data["session_b"]["e2e_latency_ms"]
    sb_stt = data["session_b"]["stt_latency_ms"]
    sb_trans = data["session_b"]["translation_latency_ms"]

    # P50: 각 컴포넌트 실측 P50 사용
    p50_e2e = sb_e2e["p50"]      # 1963ms
    p50_stt_val = round(sb_stt["p50"])    # 1933ms
    p50_trans_val = round(sb_trans["p50"])  # 449ms

    # P95: 실측 값
    p95_e2e = sb_e2e["p95"]      # 5142ms
    p95_stt_val = round(sb_stt["p95"])    # 5019ms
    p95_trans_val = round(p95_e2e - p95_stt_val)  # 123ms

    categories = ["P50", "P95"]
    stt_vals = [p50_stt_val, p95_stt_val]
    trans_vals = [p50_trans_val, p95_trans_val]
    e2e_vals = [round(p50_e2e), round(p95_e2e)]

    fig, ax = plt.subplots(figsize=(6, 4))

    x = np.arange(len(categories))
    width = 0.5

    # Stacked bars
    bars_stt = ax.bar(
        x, stt_vals, width,
        color="#E67E22", edgecolor="white", linewidth=0.5,
        hatch="//", alpha=0.9, label="STT (Whisper)",
    )
    bars_trans = ax.bar(
        x, trans_vals, width, bottom=stt_vals,
        color="#2980B9", edgecolor="white", linewidth=0.5,
        hatch="xx", alpha=0.9, label="Translation (GPT-4o)",
    )

    # 값 레이블: 각 segment 중앙
    for i in range(len(categories)):
        # STT 레이블
        ax.text(x[i], stt_vals[i] / 2, f"{_comma(stt_vals[i])}ms",
                ha="center", va="center", fontsize=9, fontweight="bold", color="white")
        # Translation 레이블: segment가 작으면 bar 바깥에 표시
        if trans_vals[i] > 300:
            ax.text(x[i], stt_vals[i] + trans_vals[i] / 2, f"{_comma(trans_vals[i])}ms",
                    ha="center", va="center", fontsize=9, fontweight="bold", color="white")
        else:
            ax.annotate(
                f"{_comma(trans_vals[i])}ms",
                (x[i] + width / 2, stt_vals[i] + trans_vals[i] / 2),
                xytext=(30, 0), textcoords="offset points",
                fontsize=8, ha="left", va="center",
                arrowprops=dict(arrowstyle="-", color="#555555", lw=0.8),
            )
        # E2E 합계 (bar 위)
        ax.text(x[i], stt_vals[i] + trans_vals[i] + 150,
                f"E2E: {_comma(e2e_vals[i])}ms",
                ha="center", va="bottom", fontsize=10, fontweight="bold")

    ax.set_xticks(x)
    ax.set_xticklabels(categories)
    ax.set_ylabel("Latency (ms)")
    ax.set_ylim(0, 7500)
    ax.set_title("Session B Latency Decomposition: STT vs. Translation")
    ax.legend(loc="upper left", framealpha=0.9)
    ax.yaxis.set_major_formatter(ticker.FuncFormatter(lambda v, _: f"{int(v):,}"))

    # 우상단 텍스트 — compute from data
    stt_mean_pct = sb_stt["mean"] / sb_e2e["mean"] * 100 if sb_e2e["mean"] > 0 else 0
    ax.text(
        0.97, 0.95,
        f"STT accounts for\n{stt_mean_pct:.1f}% of mean latency",
        transform=ax.transAxes, ha="right", va="top",
        fontsize=9, bbox=dict(boxstyle="round,pad=0.3", fc="white", ec="#cccccc", alpha=0.9),
    )

    fig.tight_layout()
    _save(fig, "figure4_latency_breakdown")
    plt.close(fig)


# ===========================================================================
# Main
# ===========================================================================

if __name__ == "__main__":
    print(f"Data: {DATA_PATH}")
    print(f"Output: {FIG_DIR}/\n")
    figure2()
    print()
    figure3()
    print()
    figure4()
    print("\nDone! All figures generated.")
