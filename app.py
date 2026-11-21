from datetime import UTC, datetime, timedelta
import json
import os
from pathlib import Path

import pandas as pd
import streamlit as st

from eaverse.panel import RELEASE_URL, read_public


st.set_page_config(page_title="Eaverse PAPER", page_icon="📈", layout="wide")


@st.cache_data(ttl=300, show_spinner=False)
def load_panel(local_file=None):
    if local_file:
        return json.loads(Path(local_file).read_text())
    return read_public()


def pct(value, signed=False):
    if value is None:
        return "—"
    return (f"{value:+.2%}" if signed else f"{value:.2%}").replace(".", ",")


def number(value):
    return "—" if value is None else f"{value:.2f}".replace(".", ",")


def status(item):
    if item["data_paused"]:
        return "Veri kontrolü bekliyor"
    if item["status"] == "active" and not item["progress"] and not item["cursor"]:
        return "İlk açılışı bekliyor"
    return {
        "active": "PAPER izleniyor",
        "verified_paper": "PAPER doğrulandı",
        "paper_gate_failed": "PAPER eşiği geçilemedi",
    }.get(item["status"], item["status"])


def history_table(origin):
    rows = []
    for key, label in (("discovery", "Keşif"), ("validation", "Yakın dönem kontrolü")):
        block = origin.get(key) or {}
        if block:
            rows.append({
                "Dönem": label,
                "Net getiri": pct(block.get("net"), signed=True),
                "Al-tut": pct(block.get("benchmark_net"), signed=True),
                "Maks. düşüş": pct(block.get("drawdown")),
                "Kapanan işlem": block.get("trades"),
                "Kârlı bölüm": block.get("profitable_folds"),
            })
    return rows


def history_details(origin):
    for key, label in (("discovery", "Keşif alt dönemleri"),
                       ("validation", "Yakın dönem alt dönemleri")):
        folds = (origin.get(key) or {}).get("folds", [])
        if not folds:
            continue
        with st.expander(label):
            st.dataframe([{
                "Bölüm": i,
                "Başlangıç": row.get("start") or "—",
                "Bitiş": row.get("end") or "—",
                "Net getiri": pct(row.get("net"), signed=True),
                "Al-tut": pct(row.get("benchmark_net"), signed=True),
                "Maks. düşüş": pct(row.get("drawdown")),
                "İşlem": row.get("trades"),
                "Sharpe": number(row.get("sharpe")),
                "Sortino": number(row.get("sortino")),
                "Kâr faktörü": number(row.get("profit_factor")),
                "Kazanma oranı": pct(row.get("trade_win_rate")),
            } for i, row in enumerate(folds, 1)], hide_index=True)


def render_paper(item):
    progress = item.get("progress") or {}
    paper = progress.get("stats") or item.get("result") or {}
    target = item["policy"].get("paper_bars")
    count = progress.get("bars")
    cols = st.columns(4)
    cols[0].metric("Net getiri", pct(paper.get("net"), signed=True))
    cols[1].metric("Al-tut", pct(paper.get("benchmark_net"), signed=True))
    cols[2].metric("Maksimum düşüş", pct(paper.get("drawdown")))
    cols[3].metric("Kapanan işlem", paper.get("trades") if paper.get("trades") is not None else "—")
    asof = progress.get("latest_open") or item["cursor"]
    st.caption(f"Sanal başlangıç {item['start']} · Son ölçüm {asof or '—'} · Bar {count if count is not None else '—'}/{target or '—'}")
    if item["data_paused"]:
        st.warning("Son turda veri veya PAPER sürekliliği doğrulanamadı. Son doğrulanmış ölçüm gösteriliyor.")
    if progress.get("curve"):
        series = pd.DataFrame(progress["curve"]).rename(columns={"strategy": "Strateji", "benchmark": "Al-tut"})
        series["date"] = pd.to_datetime(series["date"])
        series[["Strateji", "Al-tut"]] *= 100
        series["Nakit"] = 0.0
        st.line_chart(series, x="date", y=["Strateji", "Al-tut", "Nakit"], x_label="Tarih", y_label="Net getiri (%)")
    if not paper:
        st.info("İlk tamamlanmış sanal getiri barı beklendiği için ölçümler henüz oluşmadı.")
    else:
        st.dataframe([{
            "Sharpe": number(paper.get("sharpe")),
            "Sortino": number(paper.get("sortino")),
            "Yıllık oynaklık": pct(paper.get("volatility")),
            "Ortalama pozisyon payı": pct(paper.get("exposure")),
            "Kazanma oranı": pct(paper.get("trade_win_rate")),
            "Ortalama net işlem": pct(paper.get("trade_expectancy"), signed=True),
            "Kâr faktörü": number(paper.get("profit_factor")),
            "En kötü bar": pct(paper.get("worst_bar"), signed=True),
        }], hide_index=True)
        st.caption("Net değerler modeldeki varsayılan kapatma maliyetini içerir. Sharpe ve Sortino en az 20 getiri barıyla hesaplanır.")
        if progress.get("median_quote_volume_30") is not None:
            currency = "TRY" if item["asset"].startswith("BIST:") else "USD"
            st.caption(f"Son en çok 30 mumun medyan hacim tahmini: {progress['median_quote_volume_30']:,.0f} {currency}.")


def render_panel(payload):
    entries = payload["entries"]
    published = datetime.fromisoformat(payload["published_at"])
    st.caption(f"Son araştırma kaydı {published.astimezone(UTC):%d.%m.%Y %H:%M} UTC")
    if datetime.now(UTC) - published > timedelta(hours=12):
        st.warning("Son araştırma kaydı 12 saatten eski.")
    source = st.columns(2)
    source[0].link_button("Araştırma çalışması", payload["run_url"])
    source[1].link_button("Kayıt kaynağı", RELEASE_URL)

    if not entries:
        st.info("Henüz PAPER izlemeye alınmış strateji yok.")
        return
    filter_value = st.radio("Göster", ["Aktif", "Tümü", "Tamamlanan"], horizontal=True)
    subset = [item for item in entries if
              filter_value == "Tümü" or (item["status"] == "active") == (filter_value == "Aktif")]
    if not subset:
        st.info("Bu görünümde strateji yok.")
        return
    st.dataframe([{
        "Varlık": item["asset"], "PAPER": item["sequence"], "Durum": status(item),
        "Başlangıç": item["start"], "Son ölçüm": item["cursor"] or "—",
        "PAPER net": pct(((item.get("progress") or {}).get("stats") or item.get("result") or {}).get("net"), signed=True),
        "Keşif net": pct((item["origin"].get("discovery") or {}).get("net"), signed=True),
        "Yakın dönem net": pct((item["origin"].get("validation") or {}).get("net"), signed=True),
    } for item in subset], hide_index=True)
    focus = st.selectbox("Strateji ayrıntısı", list(range(len(subset))),
                         format_func=lambda i: f"{subset[i]['asset']} · PAPER #{subset[i]['sequence']}")
    item = subset[focus]
    st.subheader(f"{item['asset']} · {status(item)}")
    paper, backtest = st.tabs(["Güncel PAPER", "İzleme öncesi backtest"])
    with paper:
        render_paper(item)
    with backtest:
        origin = item["origin"]
        rows = history_table(origin)
        if rows:
            st.dataframe(rows, hide_index=True)
            st.caption("Getiriler maliyet sonrasıdır. Keşif ve yakın dönem pencereleri ayrı ölçümlerdir. "
                       "Düşüş, en zayıf alt dönemdeki maksimum düşüştür.")
            history_details(origin)
        else:
            st.info("Bu PAPER kaydında izleme öncesi backtest sonucu bulunamadı.")
        st.caption(f"Kaynak {origin.get('source', '—')} · Veri kapanışı {item['cutoff']} · "
                   f"Model maliyeti {origin.get('history_cost_bps', 2 * item['cost'])} baz puan/işlem yönü")
    with st.expander("Sabit strateji kuralları"):
        st.json(item["spec"], expanded=False)


@st.fragment(run_every="5m")
def live_view():
    if st.button("Veriyi yenile"):
        load_panel.clear()
    try:
        snapshot = load_panel(os.getenv("EAVERSE_DASHBOARD_FILE"))
        st.session_state["last_snapshot"] = snapshot
    except (OSError, ValueError, KeyError):
        snapshot = st.session_state.get("last_snapshot")
        st.warning("Güncel GitHub kaydı alınamadı. " +
                   ("Son açılan kayıt gösteriliyor." if snapshot else
                    "Bir sonraki başarılı araştırma turundan sonra tekrar yükleyin."))
    if snapshot:
        render_panel(snapshot)


st.title("Eaverse PAPER izlemesi")
live_view()
