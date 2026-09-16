"""
步骤 1（按天切片版）：下载 NYC 311 工单数据（2023–2025），逐月保存为 Parquet

数据集：311 Service Requests from 2010 to Present  (erm2-nwe9)
端点：GET https://data.cityofnewyork.us/resource/erm2-nwe9.csv

═══════════════ 为什么不用 $order + $offset ═══════════════
速度实测（--speed）表明，这个数据集上 $order 本身就是瓶颈：
    不带 $order，取 5 行 ……………………  2.7 秒
    带 $order=:id，取 5 行 ……………… 24.4 秒
    带 $order=:id，取 1000 行 …………  超时
    带 $order=unique_key，取 5 行 …… 95.7 秒

而翻页之所以需要 $order，是为了保证 offset 稳定。
如果把时间窗口切到「一天」，单日约 8000 单，一次请求就能取完，
那么既不需要 $order 也不需要 $offset —— 瓶颈直接消失。

本脚本因此按天请求，按月落盘。单请求约 3 秒，三年约一小时。
════════════════════════════════════════════════════════

用法（PowerShell）：
    $env:NYC_APP_TOKEN="你的token"
    python step1_download.py --probe      # ① 自检：确认字段名
    python step1_download.py              # ② 正式下载

用法（cmd）：
    set NYC_APP_TOKEN=你的token
    python step1_download.py
"""

import os
import io
import time
import argparse
import datetime as dt
import requests
import polars as pl

# ─────────────── 配置 ───────────────
# App Token：从环境变量读取，避免提交到公开仓库
APP_TOKEN = os.environ.get("NYC_APP_TOKEN", "")

START_DATE = dt.date(2023, 1, 1)
END_DATE   = dt.date(2026, 1, 1)      # 不含，即取到 2025-12-31

OUT_DIR    = "data/raw"
CHUNK_DAYS = 1        # 每请求覆盖几天。1 最稳；日均量小的话可调到 2–3 提速
ROW_LIMIT  = 50000    # 单请求上限。返回行数触顶会自动二分细分窗口
TIMEOUT    = 120
MAX_RETRY  = 5
SLEEP      = 0.2      # 请求间隔，避免触发限流

DATASET = "erm2-nwe9"
V2_URL  = f"https://data.cityofnewyork.us/resource/{DATASET}.csv"

FIELDS = [
    "unique_key", "created_date", "closed_date",
    "agency", "agency_name", "complaint_type", "descriptor",
    "status", "resolution_description",
    "incident_zip", "incident_address", "street_name",
    "borough", "bbl", "latitude", "longitude",
]

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
# ────────────────────────────────────


def headers():
    h = {"Accept": "text/csv", "User-Agent": USER_AGENT}
    if APP_TOKEN:
        h["X-App-Token"] = APP_TOKEN
    return h


def show_env():
    hp = os.environ.get("HTTP_PROXY") or os.environ.get("http_proxy")
    hs = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    print("─" * 60)
    print(f"App Token  : {'已设置' if APP_TOKEN else '未设置（会被限流，强烈建议设置）'}")
    print(f"HTTP_PROXY : {hp or '未设置'}")
    print(f"HTTPS_PROXY: {hs or '未设置'}")
    print(f"时间范围   : {START_DATE} ~ {END_DATE - dt.timedelta(days=1)}")
    print(f"切片粒度   : {CHUNK_DAYS} 天/请求")
    print("─" * 60)


def ts(d: dt.date) -> str:
    return d.strftime("%Y-%m-%dT00:00:00.000")


# ═══════════════ 单窗口请求（无 $order / 无 $offset） ═══════════════
def fetch_window(lo: dt.date, hi: dt.date, timeout=TIMEOUT, verbose=False):
    """取 [lo, hi) 区间的全部工单。窗口足够小则一次取完。"""
    params = {
        "$select": ",".join(FIELDS),
        "$where": f"created_date >= '{ts(lo)}' AND created_date < '{ts(hi)}'",
        "$limit": ROW_LIMIT,
        # 刻意不加 $order 和 $offset —— 见文件头说明
    }

    last_err = None
    for attempt in range(MAX_RETRY):
        try:
            r = requests.get(V2_URL, params=params, headers=headers(), timeout=timeout)
            if verbose:
                print(f"    HTTP {r.status_code}")
                print(f"    响应前 400 字符：\n{r.text[:400]}\n")
            r.raise_for_status()
            return pl.read_csv(io.BytesIO(r.content), infer_schema_length=0)
        except Exception as e:
            last_err = e
            if attempt < MAX_RETRY - 1:
                wait = 2 ** attempt
                print(f"      重试 {attempt+1}/{MAX_RETRY}（{type(e).__name__}），{wait}s 后继续")
                time.sleep(wait)
    raise last_err


def fetch_window_safe(lo: dt.date, hi: dt.date, depth=0):
    """
    取 [lo, hi)。若返回行数触顶（说明被截断），把窗口二分后递归重取，
    保证不会静默丢数据。
    """
    df = fetch_window(lo, hi)

    if df.height >= ROW_LIMIT:
        span = (hi - lo).days
        if span <= 1:
            print(f"      ⚠️ {lo} 单日就超过 {ROW_LIMIT} 行，需调高 ROW_LIMIT")
            return df
        mid = lo + dt.timedelta(days=span // 2)
        print(f"      窗口 {lo}~{hi} 触顶（{df.height} 行），二分细取")
        a = fetch_window_safe(lo, mid, depth + 1)
        b = fetch_window_safe(mid, hi, depth + 1)
        return pl.concat([a, b], how="diagonal")

    return df


# ═══════════════ 自检 ═══════════════
def probe():
    show_env()
    print("\n自检：取 2024-01-01 单日数据\n" + "=" * 60)
    t0 = time.time()
    try:
        df = fetch_window(dt.date(2024, 1, 1), dt.date(2024, 1, 2),
                          timeout=60, verbose=False)
    except Exception as e:
        print(f"❌ 失败：{type(e).__name__}: {e}")
        return

    print(f"✅ 成功：{df.height:,} 行，耗时 {time.time()-t0:.1f}s")
    print(f"\n实际列名（{len(df.columns)} 列）：")
    for c in df.columns:
        print(f"    {c}")

    missing = [f for f in FIELDS if f not in df.columns]
    if missing:
        print(f"\n⚠️ 以下请求字段未出现：{missing}")
        print("   到数据集 Data Dictionary 核对真名后修改 FIELDS")
    else:
        print("\n✅ 全部 16 个字段都在")

    print(f"\n单日行数 {df.height:,}，上限 {ROW_LIMIT:,} —— "
          f"{'余量充足' if df.height < ROW_LIMIT * 0.5 else '⚠️ 接近上限，建议 CHUNK_DAYS=1'}")
    print(f"\n首行样例：\n  {df.head(1).to_dicts()[0]}")

    est = (END_DATE - START_DATE).days / CHUNK_DAYS * (time.time() - t0) / 60
    print(f"\n预计全量下载耗时：约 {est:.0f} 分钟")


# ═══════════════ 速度测试 ═══════════════
def speed():
    show_env()
    sel = ",".join(FIELDS)
    whr = ("created_date >= '2024-01-01T00:00:00.000' "
           "AND created_date < '2024-01-02T00:00:00.000'")
    tests = [
        ("① 单日，不带 order，limit 50000", {"$select": sel, "$where": whr, "$limit": 50000}),
        ("② 单日，带 order=:id",            {"$select": sel, "$where": whr, "$limit": 50000,
                                             "$order": ":id"}),
        ("③ 三日，不带 order",              {"$select": sel, "$limit": 50000,
                                             "$where": "created_date >= '2024-01-01T00:00:00.000' "
                                                       "AND created_date < '2024-01-04T00:00:00.000'"}),
    ]
    for name, params in tests:
        t0 = time.time()
        try:
            r = requests.get(V2_URL, params=params, headers=headers(), timeout=180)
            n = max(r.text.count("\n") - 1, 0)
            flag = "✅" if r.status_code == 200 else "❌"
            print(f"{flag} {name:34s} {time.time()-t0:6.1f}s  HTTP {r.status_code}  {n:,} 行")
        except Exception as e:
            print(f"❌ {name:34s} {time.time()-t0:6.1f}s  {type(e).__name__}")
    print("\n① 应远快于 ②。若 ③ 也快，可把 CHUNK_DAYS 调到 3 提速三倍。")


# ═══════════════ 正式下载 ═══════════════
def month_bounds(d: dt.date):
    """返回 d 所在月的 [首日, 次月首日)"""
    first = d.replace(day=1)
    nxt = dt.date(first.year + 1, 1, 1) if first.month == 12 \
        else dt.date(first.year, first.month + 1, 1)
    return first, nxt


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    show_env()

    total, t_start = 0, time.time()
    cur = START_DATE.replace(day=1)

    while cur < END_DATE:
        m_lo, m_hi = month_bounds(cur)
        path = f"{OUT_DIR}/nyc311_{m_lo.year}{m_lo.month:02d}.parquet"

        if os.path.exists(path):
            n = pl.scan_parquet(path).select(pl.len()).collect().item()
            print(f"{m_lo:%Y-%m}  已存在，跳过（{n:,} 行）")
            total += n
            cur = m_hi
            continue

        print(f"{m_lo:%Y-%m}  下载中…")
        frames, day = [], max(m_lo, START_DATE)
        m_end = min(m_hi, END_DATE)
        t_month = time.time()
        failed = []

        while day < m_end:
            nxt = min(day + dt.timedelta(days=CHUNK_DAYS), m_end)
            try:
                df = fetch_window_safe(day, nxt)
                if df.height:
                    frames.append(df)
                got = sum(f.height for f in frames)
                print(f"    {day} … 累计 {got:,} 行", end="\r")
            except Exception as e:
                print(f"\n    ❌ {day} 失败：{type(e).__name__}")
                failed.append(day)
            day = nxt
            time.sleep(SLEEP)

        if failed:
            print(f"\n  ⚠️ {m_lo:%Y-%m} 有 {len(failed)} 个窗口失败，本月不落盘，重跑脚本会重试")
            cur = m_hi
            continue

        if not frames:
            print(f"{m_lo:%Y-%m}  无数据")
            cur = m_hi
            continue

        out = pl.concat(frames, how="diagonal")
        out.write_parquet(path, compression="zstd")
        total += out.height
        print(f"{m_lo:%Y-%m}  完成：{out.height:,} 行，"
              f"{os.path.getsize(path)/1e6:.1f} MB，耗时 {(time.time()-t_month)/60:.1f} 分钟")
        cur = m_hi

    print(f"\n合计 {total:,} 行，用时 {(time.time()-t_start)/60:.1f} 分钟")
    print(f"已存入 {OUT_DIR}/")
    print("下一步：python step2_analyze.py")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe", action="store_true", help="自检：取单日数据，确认字段名与速度")
    ap.add_argument("--speed", action="store_true", help="速度对比：验证不排序确实更快")
    args = ap.parse_args()

    if args.probe:
        probe()
    elif args.speed:
        speed()
    else:
        main()
