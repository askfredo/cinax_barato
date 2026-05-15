# CINAX v3 — Cloud Run Jobs + GCS
# Los CSVs se guardan en Google Cloud Storage en vez de /data local
# El modelo.pkl se incluye en la imagen Docker

import numpy as np
import pandas as pd
import yfinance as yf
import pickle
import os
import time
import warnings
import requests
from datetime import datetime
import pytz
warnings.filterwarnings("ignore") 

# ══════════════════════════════════════════════════════════════
# CONFIGURACIÓN
# ══════════════════════════════════════════════════════════════

RUTA_PKL        = "modelo.pkl"
PCTIL           = 70
DISCORD_WEBHOOK = os.environ.get("DISCORD_WEBHOOK", "")
GCS_BUCKET      = os.environ.get("GCS_BUCKET", "")   # nombre del bucket, sin gs://

# ══════════════════════════════════════════════════════════════
# CONFIG FIJA
# ══════════════════════════════════════════════════════════════

ACTIVO       = "^GSPC"
WINDOW_PCT   = 252
MERCADO_TZ   = pytz.timezone("America/New_York")
DIAS_ENTRADA = {0, 1, 2}
NOMBRES_DIA  = {0:"Lunes", 1:"Martes", 2:"Miércoles", 3:"Jueves", 4:"Viernes"} 

MACRO_TICKERS = [
    "DX-Y.NYB","CL=F","HG=F","XLU","RSP","^VVIX","SMH","HYG",
    "GC=F","SPHB","SPLV","TLT","IEF","LQD",
    "^VIX","^VIX3M","XLK","XLF","XLI","XLP","XLV","^IRX","^TNX",
]

COLS_FIN = [
    "yield_3m_pct", "curve_2_10_pct", "curve_regime_pct",
    "rsi7_pct", "rsi14_pct", "rsi21_pct",
    "rsi14_slope_pct", "rsi_diverge_pct",
    "sma20_vs_200_pct", "dist_sma50_pct", "pos_52w",
    "vol_pct_10d", "vol_pct_20d", "vol_pct_30d", "vol_pct_60d", "vol_ratio_pct",
    "liq_shock_tlt_pct", "bond_eq_corr_pct",
    "mom_pct_5d", "mom_pct_10d", "mom_pct_20d", "mom_pct_60d",
]

DATA_DIR        = "/tmp"   # directorio temporal dentro del contenedor
LOG_FILE        = f"{DATA_DIR}/cinax_paper.log"
SEÑALES_CSV     = f"{DATA_DIR}/cinax_señales.csv"
POSICIONES_CSV  = f"{DATA_DIR}/cinax_posiciones.csv"
INTRA_CSV       = f"{DATA_DIR}/cinax_intra.csv"

GCS_FILES = {
    SEÑALES_CSV:   "cinax_señales.csv",
    POSICIONES_CSV:"cinax_posiciones.csv",
    INTRA_CSV:     "cinax_intra.csv",
}

# ══════════════════════════════════════════════════════════════
# GCS — HELPERS
# Usa google-cloud-storage que viene en el env de Cloud Run
# ══════════════════════════════════════════════════════════════

def _gcs_client():
    from google.cloud import storage
    return storage.Client()

def gcs_descargar_csvs():
    """Al inicio del job, baja los CSVs del bucket a /tmp."""
    if not GCS_BUCKET:
        log("GCS_BUCKET no configurado — modo local sin persistencia", "WARN")
        return
    try:
        client = _gcs_client()
        bucket = client.bucket(GCS_BUCKET)
        for local_path, blob_name in GCS_FILES.items():
            blob = bucket.blob(blob_name)
            if blob.exists():
                blob.download_to_filename(local_path)
                log(f"GCS ↓ {blob_name}", "OK")
            else:
                log(f"GCS: {blob_name} no existe aún (primera vez)", "INFO")
    except Exception as e:
        log(f"Error bajando GCS: {e}", "WARN")

def gcs_subir_csvs():
    """Al final del job, sube los CSVs actualizados al bucket."""
    if not GCS_BUCKET:
        return
    try:
        client = _gcs_client()
        bucket = client.bucket(GCS_BUCKET)
        for local_path, blob_name in GCS_FILES.items():
            if os.path.exists(local_path):
                bucket.blob(blob_name).upload_from_filename(local_path)
                log(f"GCS ↑ {blob_name}", "OK")
    except Exception as e:
        log(f"Error subiendo GCS: {e}", "WARN")

# ══════════════════════════════════════════════════════════════
# LOG
# ══════════════════════════════════════════════════════════════

def log(msg, nivel="INFO"):
    ts  = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    sym = {"INFO":"·","SEÑAL":"★","WARN":"!","ERR":"✗","OK":"✓"}.get(nivel, "·")
    txt = f"[{ts}] {sym} {msg}"
    print(txt, flush=True)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(txt + "\n")
    except Exception:
        pass

# ══════════════════════════════════════════════════════════════
# DISCORD
# ══════════════════════════════════════════════════════════════

def discord(mensaje):
    if not DISCORD_WEBHOOK:
        return
    try:
        requests.post(DISCORD_WEBHOOK, json={"content": mensaje}, timeout=10)
    except Exception as e:
        log(f"Discord error: {e}", "WARN")

def discord_resumen_diario(fecha_barra, precio, prob, umbral, señal, cerradas_hoy=None):
    hoy    = fecha_barra.strftime("%Y-%m-%d")
    dia    = NOMBRES_DIA.get(fecha_barra.weekday(), "")
    sp_fmt = f"{precio:,.1f}"

    if señal:
        viernes = next_friday(fecha_barra).strftime("%Y-%m-%d")
        header  = f"🟢 **CINAX — SEÑAL ACTIVA** | {hoy} ({dia})"
        detalle = (f"```\n"
                   f"S&P 500 Close : {sp_fmt}\n"
                   f"Probabilidad  : {prob:.4f}  (umbral {umbral:.4f})\n"
                   f"Entrada       : HOY al CLOSE\n"
                   f"Exit esperado : {viernes} (viernes al CLOSE)\n"
                   f"```")
    else:
        header  = f"⚪ **CINAX — Sin señal** | {hoy} ({dia})"
        detalle = (f"```\n"
                   f"S&P 500 Close : {sp_fmt}\n"
                   f"Probabilidad  : {prob:.4f}  (umbral {umbral:.4f})\n"
                   f"```")

    cierre_txt  = _bloque_cerradas(cerradas_hoy)
    resumen_txt = _bloque_acumulado()
    discord(f"{header}\n{detalle}{cierre_txt}{resumen_txt}")

def discord_seguimiento_posicion(fecha_barra, precio_actual, cerradas_hoy=None):
    if not os.path.exists(POSICIONES_CSV):
        return
    df_pos   = pd.read_csv(POSICIONES_CSV)
    abiertas = df_pos[df_pos["estado"] == "ABIERTA"]
    cierre_txt = _bloque_cerradas(cerradas_hoy)
    if abiertas.empty and not cierre_txt:
        return

    hoy    = fecha_barra.strftime("%Y-%m-%d")
    dia    = NOMBRES_DIA.get(fecha_barra.weekday(), "")
    header = f"📊 **CINAX — Seguimiento** | {hoy} ({dia})"
    pos_txt = ""
    if not abiertas.empty:
        lineas = []
        for _, p in abiertas.iterrows():
            entry_price = float(p["entry_price"])
            ret_actual  = precio_actual / entry_price - 1
            exit_esp    = p["exit_date_esperado"]
            emoji = "🟢" if ret_actual >= 0 else "🔴"
            lineas.append(
                f"{emoji}  entry {p['entry_date']} @ {entry_price:,.1f}"
                f"  →  ahora {precio_actual:,.1f}"
                f"  ret {ret_actual*100:+.2f}%"
                f"  | exit {exit_esp}"
            )
        pos_txt = "\n**Posiciones abiertas:**\n```\n" + "\n".join(lineas) + "\n```"

    resumen_txt = _bloque_acumulado()
    discord(f"{header}{pos_txt}{cierre_txt}{resumen_txt}")

def _bloque_cerradas(cerradas_hoy):
    if cerradas_hoy is None or len(cerradas_hoy) == 0:
        return ""
    lineas = []
    for _, p in cerradas_hoy.iterrows():
        ret   = float(p["retorno"])
        emoji = "✅" if ret > 0 else "❌"
        lineas.append(f"{emoji}  entry {p['entry_date']}  →  exit HOY   ret {ret*100:+.2f}%")
    return "\n**Posiciones cerradas hoy:**\n```\n" + "\n".join(lineas) + "\n```"

def _bloque_acumulado():
    if not os.path.exists(POSICIONES_CSV):
        return ""
    df_all   = pd.read_csv(POSICIONES_CSV)
    cerradas = df_all[df_all["estado"] == "CERRADA"]
    abiertas = df_all[df_all["estado"] == "ABIERTA"]
    if len(cerradas) == 0:
        return ""
    rets = cerradas["retorno"].astype(float)
    wr   = (rets > 0).mean()
    pf   = rets[rets > 0].sum() / (abs(rets[rets < 0].sum()) + 1e-8)
    return (f"\n**Acumulado ({len(cerradas)} trades cerrados):**\n"
            f"```\n"
            f"Win Rate      : {wr:.1%}\n"
            f"Profit Factor : {pf:.2f}\n"
            f"Retorno acum  : {rets.sum()*100:+.1f}%\n"
            f"Abiertas ahora: {len(abiertas)}\n"
            f"```")

# ══════════════════════════════════════════════════════════════
# HORARIO
# ══════════════════════════════════════════════════════════════

def next_friday(date):
    days_ahead = 4 - date.weekday()
    if days_ahead <= 0:
        days_ahead += 7
    return date + pd.Timedelta(days=days_ahead)

# ══════════════════════════════════════════════════════════════
# DATOS  (igual que v2)
# ══════════════════════════════════════════════════════════════

def descargar_datos():
    log("Descargando datos (desde 2000 para warmup)...")
    df_sp = yf.download(ACTIVO, start="2000-01-01", interval="1d",
                        auto_adjust=False, progress=False)
    if isinstance(df_sp.columns, pd.MultiIndex):
        df_sp.columns = df_sp.columns.droplevel(1)
    df_sp.index = pd.to_datetime(df_sp.index)

    log("Descargando macro tickers...")
    macro_data = {}
    for ticker in MACRO_TICKERS:
        try:
            df_t = yf.download(ticker, start="2000-01-01", interval="1d",
                               auto_adjust=False, progress=False)
            if isinstance(df_t.columns, pd.MultiIndex):
                df_t.columns = df_t.columns.droplevel(1)
            df_t.index = pd.to_datetime(df_t.index)
            if not df_t.empty:
                macro_data[ticker] = df_t["Close"]
        except Exception as e:
            log(f"Error descargando {ticker}: {e}", "WARN")

    df_macro = pd.DataFrame(macro_data)
    df_raw   = df_sp.join(df_macro, how="left")
    log(f"Datos OK: {len(df_raw)} filas, última barra: {df_raw.index[-1].date()}", "OK")
    return df_raw

def rsi(series, n):
    delta = series.diff()
    gain  = delta.clip(lower=0)
    loss  = (-delta).clip(lower=0)
    avg_g = gain.ewm(com=n-1, min_periods=n).mean()
    avg_l = loss.ewm(com=n-1, min_periods=n).mean()
    rs    = avg_g / (avg_l + 1e-8)
    return 100 - 100 / (1 + rs)

def rolling_pctil(series, window):
    return series.rolling(window).apply(
        lambda x: (x[-1] > x[:-1]).mean() if len(x) > 1 else 0.5, raw=True
    )

def build_features(df_raw):
    df = df_raw.copy()
    close = df["Close"]

    df["yield_3m"]      = df.get("^IRX", pd.Series(dtype=float))
    df["yield_10y"]     = df.get("^TNX", pd.Series(dtype=float))
    df["curve_2_10"]    = df["yield_10y"] - df["yield_3m"]
    df["curve_regime"]  = df["curve_2_10"].rolling(20).mean()
    df["rsi7"]          = rsi(close, 7)
    df["rsi14"]         = rsi(close, 14)
    df["rsi21"]         = rsi(close, 21)
    df["rsi14_slope"]   = df["rsi14"].diff(5)
    df["rsi_diverge"]   = df["rsi14"] - df["rsi7"]
    df["sma20"]         = close.rolling(20).mean()
    df["sma50"]         = close.rolling(50).mean()
    df["sma200"]        = close.rolling(200).mean()
    df["sma20_vs_200"]  = close / df["sma200"] - 1
    df["dist_sma50"]    = close / df["sma50"] - 1
    df["high_52w"]      = close.rolling(252).max()
    df["low_52w"]       = close.rolling(252).min()
    df["pos_52w_raw"]   = (close - df["low_52w"]) / (df["high_52w"] - df["low_52w"] + 1e-8)
    df["vol_10d"]       = close.pct_change().rolling(10).std()
    df["vol_20d"]       = close.pct_change().rolling(20).std()
    df["vol_30d"]       = close.pct_change().rolling(30).std()
    df["vol_60d"]       = close.pct_change().rolling(60).std()
    df["vol_ratio"]     = df["vol_10d"] / (df["vol_60d"] + 1e-8)

    tlt = df.get("TLT", pd.Series(dtype=float))
    df["liq_shock_tlt"] = tlt.pct_change(5) if tlt is not None else 0
    tlt_ret   = tlt.pct_change() if tlt is not None else pd.Series(0, index=df.index)
    close_ret = close.pct_change()
    df["bond_eq_corr"]  = tlt_ret.rolling(20).corr(close_ret)

    df["mom_5d"]  = close.pct_change(5)
    df["mom_10d"] = close.pct_change(10)
    df["mom_20d"] = close.pct_change(20)
    df["mom_60d"] = close.pct_change(60)

    w = WINDOW_PCT
    df["yield_3m_pct"]      = rolling_pctil(df["yield_3m"].fillna(method="ffill"), w)
    df["curve_2_10_pct"]    = rolling_pctil(df["curve_2_10"].fillna(method="ffill"), w)
    df["curve_regime_pct"]  = rolling_pctil(df["curve_regime"].fillna(method="ffill"), w)
    df["rsi7_pct"]          = rolling_pctil(df["rsi7"], w)
    df["rsi14_pct"]         = rolling_pctil(df["rsi14"], w)
    df["rsi21_pct"]         = rolling_pctil(df["rsi21"], w)
    df["rsi14_slope_pct"]   = rolling_pctil(df["rsi14_slope"].fillna(0), w)
    df["rsi_diverge_pct"]   = rolling_pctil(df["rsi_diverge"], w)
    df["sma20_vs_200_pct"]  = rolling_pctil(df["sma20_vs_200"], w)
    df["dist_sma50_pct"]    = rolling_pctil(df["dist_sma50"], w)
    df["pos_52w"]           = rolling_pctil(df["pos_52w_raw"], w)
    df["vol_pct_10d"]       = rolling_pctil(df["vol_10d"].fillna(0), w)
    df["vol_pct_20d"]       = rolling_pctil(df["vol_20d"].fillna(0), w)
    df["vol_pct_30d"]       = rolling_pctil(df["vol_30d"].fillna(0), w)
    df["vol_pct_60d"]       = rolling_pctil(df["vol_60d"].fillna(0), w)
    df["vol_ratio_pct"]     = rolling_pctil(df["vol_ratio"].fillna(1), w)
    df["liq_shock_tlt_pct"] = rolling_pctil(df["liq_shock_tlt"].fillna(0), w)
    df["bond_eq_corr_pct"]  = rolling_pctil(df["bond_eq_corr"].fillna(0), w)
    df["mom_pct_5d"]        = rolling_pctil(df["mom_5d"].fillna(0), w)
    df["mom_pct_10d"]       = rolling_pctil(df["mom_10d"].fillna(0), w)
    df["mom_pct_20d"]       = rolling_pctil(df["mom_20d"].fillna(0), w)
    df["mom_pct_60d"]       = rolling_pctil(df["mom_60d"].fillna(0), w)

    df["close"] = close
    df["open"]  = df.get("Open", close)
    df["high"]  = df.get("High", close)
    df["low"]   = df.get("Low", close)

    df.dropna(subset=COLS_FIN, how="all", inplace=True)
    return df

def predecir(modelo, df_feat):
    ultima = df_feat.iloc[-1]
    x      = ultima[COLS_FIN].fillna(0.5).values.reshape(1, -1)
    prob   = float(modelo.predict_proba(x)[0, 1])
    return prob, df_feat.index[-1], float(ultima["close"])

# ══════════════════════════════════════════════════════════════
# POSICIONES / SEÑALES  (igual que v2)
# ══════════════════════════════════════════════════════════════

def guardar_señal(fecha_barra, precio, prob, umbral, señal):
    nueva = pd.DataFrame([{
        "fecha": str(fecha_barra.date()), "dia": NOMBRES_DIA.get(fecha_barra.weekday(),""),
        "close": round(precio, 2), "prob": round(prob, 4),
        "umbral": round(umbral, 4), "señal": señal
    }])
    if os.path.exists(SEÑALES_CSV):
        df = pd.concat([pd.read_csv(SEÑALES_CSV), nueva], ignore_index=True)
    else:
        df = nueva
    df.to_csv(SEÑALES_CSV, index=False)

def abrir_posicion(fecha_barra, precio, prob, umbral):
    if os.path.exists(POSICIONES_CSV):
        df_pos   = pd.read_csv(POSICIONES_CSV, parse_dates=["entry_date"])
        abiertas = df_pos[df_pos["estado"] == "ABIERTA"]
        if not abiertas.empty:
            log("Ya hay posición abierta — no se duplica", "WARN")
            return
    else:
        df_pos = pd.DataFrame()

    nueva = pd.DataFrame([{
        "entry_date":           str(fecha_barra.date()),
        "entry_price":          round(precio, 2),
        "prob":                 round(prob, 4),
        "umbral":               round(umbral, 4),
        "exit_date_esperado":   str(next_friday(fecha_barra).date()),
        "exit_price":           None,
        "retorno":              None,
        "estado":               "ABIERTA",
    }])
    df_pos = pd.concat([df_pos, nueva], ignore_index=True)
    df_pos.to_csv(POSICIONES_CSV, index=False)
    log(f"Posición abierta @ {precio:.1f} | exit esperado: {next_friday(fecha_barra).date()}", "OK")

def registrar_barra_intra(df_raw, fecha_barra):
    if not os.path.exists(POSICIONES_CSV):
        return
    df_pos   = pd.read_csv(POSICIONES_CSV, parse_dates=["entry_date","exit_date_esperado"])
    abiertas = df_pos[df_pos["estado"] == "ABIERTA"]
    if abiertas.empty:
        return

    hoy    = fecha_barra.date()
    ultima = df_raw[df_raw.index.date == hoy]
    if ultima.empty:
        return

    close_ = float(ultima["Close"].iloc[-1])
    open_  = float(ultima.get("Open", ultima["Close"]).iloc[-1])
    high_  = float(ultima.get("High", ultima["Close"]).iloc[-1])
    low_   = float(ultima.get("Low",  ultima["Close"]).iloc[-1])

    if os.path.exists(INTRA_CSV):
        df_intra = pd.read_csv(INTRA_CSV)
    else:
        df_intra = pd.DataFrame(columns=["entry_date","fecha_barra","dia_semana",
                                          "open","high","low","close",
                                          "ret_vs_entry","ret_diario"])
    nuevas = []
    for _, pos in abiertas.iterrows():
        entry_date = pos["entry_date"].date()
        exit_date  = pos["exit_date_esperado"].date()
        if hoy <= entry_date or hoy > exit_date:
            continue
        ya_existe = (
            (df_intra["entry_date"].astype(str) == str(entry_date)) &
            (df_intra["fecha_barra"].astype(str) == str(hoy))
        ).any() if len(df_intra) > 0 else False
        if ya_existe:
            continue
        entry_price  = float(pos["entry_price"])
        ret_vs_entry = close_ / entry_price - 1
        nuevas.append({
            "entry_date":   str(entry_date),
            "fecha_barra":  str(hoy),
            "dia_semana":   fecha_barra.strftime("%A"),
            "open":         round(open_, 2),
            "high":         round(high_, 2),
            "low":          round(low_, 2),
            "close":        round(close_, 2),
            "ret_vs_entry": round(ret_vs_entry, 6),
            "ret_diario":   round(close_ / open_ - 1, 6),
        })

    if nuevas:
        df_intra = pd.concat([df_intra, pd.DataFrame(nuevas)], ignore_index=True)
        df_intra.to_csv(INTRA_CSV, index=False)

def cerrar_posiciones_vencidas(df_feat, df_raw):
    if not os.path.exists(POSICIONES_CSV):
        return pd.DataFrame()
    df_pos   = pd.read_csv(POSICIONES_CSV, parse_dates=["entry_date","exit_date_esperado"])
    abiertas = df_pos[df_pos["estado"] == "ABIERTA"]
    if abiertas.empty:
        return pd.DataFrame()

    hoy          = df_feat.index[-1].date()
    cerradas_hoy = []

    for idx_pos, pos in abiertas.iterrows():
        exit_esp = pos["exit_date_esperado"].date()
        if hoy >= exit_esp:
            fechas_post = df_feat.index.date[df_feat.index.date >= exit_esp]
            if len(fechas_post) == 0:
                continue
            fecha_real     = fechas_post[0]
            precio_cierre  = float(df_feat[df_feat.index.date == fecha_real]["close"].iloc[-1])
            precio_entrada = float(pos["entry_price"])
            retorno        = precio_cierre / precio_entrada - 1
            df_pos.at[idx_pos, "exit_price"] = round(precio_cierre, 2)
            df_pos.at[idx_pos, "retorno"]    = round(retorno, 6)
            df_pos.at[idx_pos, "estado"]     = "CERRADA"
            cerradas_hoy.append(df_pos.loc[idx_pos].to_dict())
            log(f"Posición cerrada | entry={pos['entry_date'].date()} "
                f"→ exit={fecha_real} | retorno={retorno:+.2%} | "
                f"{'GANADORA ✓' if retorno > 0 else 'PERDEDORA ✗'}", "OK")

    df_pos.to_csv(POSICIONES_CSV, index=False)
    return pd.DataFrame(cerradas_hoy) if cerradas_hoy else pd.DataFrame()

def resumen_log():
    if not os.path.exists(POSICIONES_CSV):
        return
    df_pos   = pd.read_csv(POSICIONES_CSV)
    cerradas = df_pos[df_pos["estado"] == "CERRADA"]
    abiertas = df_pos[df_pos["estado"] == "ABIERTA"]
    if cerradas.empty:
        log(f"Sin posiciones cerradas aún | Abiertas: {len(abiertas)}")
        return
    rets = cerradas["retorno"].astype(float)
    wr   = (rets > 0).mean()
    pf   = rets[rets > 0].sum() / (abs(rets[rets < 0].sum()) + 1e-8)
    log(f"RESUMEN | Cerradas: {len(cerradas)} | Abiertas: {len(abiertas)} | "
        f"WR: {wr:.1%} | PF: {pf:.2f} | Acum: {rets.sum()*100:+.1f}%")

# ══════════════════════════════════════════════════════════════
# MAIN  — en Cloud Run Job NO hay while True
# Corre una vez, hace su trabajo, termina.
# ══════════════════════════════════════════════════════════════

def main():
    log("=" * 60)
    log("CINAX v3 — Cloud Run Job (ejecución única)")
    log(f"Config: percentil={PCTIL} | entrada=Lun/Mar/Mié | cierre=viernes al CLOSE")
    log(f"Bucket GCS: {GCS_BUCKET or 'NO configurado'}")
    log(f"Discord: {'configurado ✓' if DISCORD_WEBHOOK else 'NO configurado'}")
    log("=" * 60)

    if not os.path.exists(RUTA_PKL):
        log(f"ERROR: No se encontró el .pkl en: {RUTA_PKL}", "ERR")
        return

    # 1. Bajar CSVs históricos desde GCS
    gcs_descargar_csvs()

    # 2. Cargar modelo
    with open(RUTA_PKL, "rb") as f:
        modelo = pickle.load(f)
    log(f"Modelo cargado: {RUTA_PKL}", "OK")

    # 3. Descargar datos y calcular features
    try:
        df_raw  = descargar_datos()
        df_feat = build_features(df_raw)
    except Exception as e:
        log(f"Error descargando datos: {e}", "ERR")
        discord(f"❌ **CINAX ERROR** al descargar datos: {e}")
        return

    if df_feat.empty:
        log("DataFrame vacío — abortando", "ERR")
        return

    # 4. Predecir
    prob, fecha_barra, precio = predecir(modelo, df_feat)
    X_hist  = df_feat[COLS_FIN].fillna(0.5).values
    probs_h = modelo.predict_proba(X_hist)[:, 1]
    umbral  = float(np.percentile(probs_h, PCTIL))

    dia_semana = fecha_barra.weekday()
    log(f"Fecha barra: {fecha_barra.date()} | Día: {NOMBRES_DIA.get(dia_semana,'?')} | "
        f"Close: {precio:.1f} | Prob: {prob:.4f} | Umbral: {umbral:.4f}")

    # 5. Registrar barra intra para posiciones abiertas
    registrar_barra_intra(df_raw, fecha_barra)

    # 6. Cerrar posiciones vencidas
    cerradas_hoy = cerrar_posiciones_vencidas(df_feat, df_raw)

    # 7. Lógica por día
    if dia_semana in DIAS_ENTRADA:
        señal = prob >= umbral
        if señal:
            log(f"★ SEÑAL LARGA ★", "SEÑAL")
            log(f"  → Exit: {next_friday(fecha_barra).date()} (viernes al CLOSE)", "SEÑAL")
            abrir_posicion(fecha_barra, precio, prob, umbral)
        else:
            log("Sin señal")
        guardar_señal(fecha_barra, precio, prob, umbral, señal)
        resumen_log()
        discord_resumen_diario(
            fecha_barra, precio, prob, umbral, señal,
            cerradas_hoy if len(cerradas_hoy) > 0 else None
        )

    elif dia_semana == 3:  # Jueves
        resumen_log()
        discord_seguimiento_posicion(
            fecha_barra, precio,
            cerradas_hoy if len(cerradas_hoy) > 0 else None
        )

    elif dia_semana == 4:  # Viernes
        resumen_log()
        if len(cerradas_hoy) > 0:
            discord_seguimiento_posicion(fecha_barra, precio, cerradas_hoy)

    # 8. Subir CSVs actualizados a GCS
    gcs_subir_csvs()
    log("Job completado ✓", "OK")


if __name__ == "__main__":
    main()
