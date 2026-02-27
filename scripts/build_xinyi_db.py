import os
import sys
import argparse
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional, Sequence, Tuple

from dotenv import load_dotenv


# Ensure project root is on sys.path when running as `python scripts/...`
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# Windows / some terminals may default to non-UTF8 which garbles Chinese output.
# Best-effort: reconfigure stdout/stderr to UTF-8 if supported (Python 3.7+).
try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:
    pass


def _now() -> float:
    return time.time()


def _chunk(seq: Sequence[str], size: int) -> List[List[str]]:
    out: List[List[str]] = []
    buf: List[str] = []
    for x in seq:
        buf.append(x)
        if len(buf) >= size:
            out.append(buf)
            buf = []
    if buf:
        out.append(buf)
    return out


def _is_xinyi_by_address(address: str) -> bool:
    if not address:
        return False
    return "信義區" in address


def _is_other_taipei_district(address: str) -> bool:
    """Exclude results clearly from other Taipei districts (best-effort)."""
    if not address:
        return False
    other = [
        "中正區",
        "大同區",
        "中山區",
        "松山區",
        "大安區",
        "萬華區",
        "文山區",
        "南港區",
        "內湖區",
        "士林區",
        "北投區",
    ]
    return any(d in address for d in other) and ("信義區" not in address)


def _is_in_xinyi_bbox(lat: Optional[float], lng: Optional[float]) -> bool:
    """
    Best-effort bounding box for Taipei Xinyi District.
    Used as fallback when the actor did not return an address.
    """
    if lat is None or lng is None:
        return False
    try:
        lat = float(lat)
        lng = float(lng)
    except Exception:
        return False
    # Slightly relaxed bounds to avoid false negatives near borders.
    return (25.005 <= lat <= 25.075) and (121.535 <= lng <= 121.625)


def build_xinyi_queries() -> List[str]:
    # Core area tokens
    area_tokens = [
        "台北市 信義區",
        "台北 信義區",
        "信義區 台北",
        "信義區",
    ]

    categories = [
        "餐廳",
        "美食",
        "小吃",
        "早餐",
        "早午餐",
        "下午茶",
        "宵夜",
        "咖啡",
        "咖啡廳",
        "甜點",
        "麵店",
        "牛肉麵",
        "火鍋",
        "燒肉",
        "居酒屋",
        "酒吧",
        "日式料理",
        "拉麵",
        "壽司",
        "韓式料理",
        "泰式料理",
        "越南料理",
        "港式",
        "中式料理",
        "義大利麵",
        "義大利餐廳",
        "披薩",
        "素食",
        "清真",
        "無麩質",
    ]

    mrt = [
        "市政府站",
        "永春站",
        "象山站",
        "後山埤站",
        "台北101/世貿站",
        "國父紀念館站",
    ]

    landmarks = [
        "台北101",
        "世貿",
        "信義威秀",
        "ATT 4 FUN",
        "微風南山",
        "新光三越A11",
        "新光三越A8",
        "新光三越A9",
        "信義新天地",
        "松菸",
        "誠品松菸",
        "四四南村",
        "象山",
        "永春市場",
    ]

    roads = [
        "忠孝東路五段",
        "信義路五段",
        "松仁路",
        "松智路",
        "松高路",
        "松壽路",
        "基隆路一段",
        "莊敬路",
        "光復南路",
        "逸仙路",
    ]

    queries: List[str] = []

    # Area x category
    for a in area_tokens:
        for c in categories:
            queries.append(f"{a} {c}")

    # MRT x category
    for s in mrt:
        queries.append(f"{s} 餐廳")
        queries.append(f"{s} 美食")
        queries.append(f"{s} 小吃")
        queries.append(f"{s} 咖啡廳")
        queries.append(f"{s} 火鍋")
        queries.append(f"{s} 燒肉")

    # Landmarks x category
    for lm in landmarks:
        queries.append(f"{lm} 餐廳")
        queries.append(f"{lm} 美食")
        queries.append(f"{lm} 咖啡廳")

    # Roads
    for r in roads:
        queries.append(f"{r} 餐廳")
        queries.append(f"{r} 美食")

    # Deduplicate while preserving order
    seen = set()
    out: List[str] = []
    for q in queries:
        q = (q or "").strip()
        if not q:
            continue
        if q in seen:
            continue
        seen.add(q)
        out.append(q)
    return out


def discover_xinyi_places(
    *,
    tag: str,
    queries: Sequence[str],
    batch_size: int,
    limit_per_query: int,
    language: str,
    workers: int = 1,
    heartbeat_every: float = 5.0,
    progress_every: float = 10.0,
    max_upserts: Optional[int] = None,
) -> Tuple[int, int]:
    from services.apify_client import search_places_bulk
    from services.place_store import upsert_catalog_place
    from services.url_normalizer import canonicalize
    from services.job_store import create_job, update_job

    new_count = 0
    seen_canonical = set()

    max_upserts_int: Optional[int] = None
    if max_upserts is not None:
        try:
            max_upserts_int = max(1, int(max_upserts))
        except Exception:
            max_upserts_int = None

    # Precompute batches so we know total count for progress + ETA
    batches = _chunk(list(queries), max(1, int(batch_size)))
    total_batches = len(batches)
    total_queries = len(queries)
    workers = max(1, int(workers or 1))
    try:
        heartbeat_every = float(heartbeat_every)
    except Exception:
        heartbeat_every = 5.0
    if heartbeat_every < 0:
        heartbeat_every = 0.0
    try:
        progress_every = float(progress_every)
    except Exception:
        progress_every = 10.0
    if progress_every < 0:
        progress_every = 0.0

    print(
        f"[discover] starting discovery: {total_queries} queries in {total_batches} batches "
        f"(batch_size={batch_size}, limit_per_query={limit_per_query}, workers={workers})"
    )

    job_id = create_job(kind="xinyi_discover", tag=tag, total=total_batches, message=f"queries={total_queries}")
    print(f"[discover] job_id={job_id} (query via /api/jobs/{job_id})")

    def _upsert_items(items: List[Dict[str, object]], batch_hint: Sequence[str]) -> int:
        nonlocal new_count
        inserted_here = 0
        for it in items:
            if not isinstance(it, dict):
                continue
            maps_url = it.get("maps_url")  # type: ignore[assignment]
            place_id = it.get("place_id")  # type: ignore[assignment]
            name = it.get("name") or ""
            address = it.get("address") or ""
            lat = it.get("lat")
            lng = it.get("lng")

            if isinstance(address, str) and address and _is_other_taipei_district(address):
                continue

            if not (_is_xinyi_by_address(str(address)) or _is_in_xinyi_bbox(lat if isinstance(lat, (int, float)) else None, lng if isinstance(lng, (int, float)) else None)):
                continue

            if not maps_url and place_id:
                maps_url = f"https://www.google.com/maps/place/?q=place_id:{place_id}"
            if not maps_url:
                continue

            try:
                norm = canonicalize(str(maps_url))
            except Exception:
                continue

            canonical_url = norm.get("canonical_url") or ""
            if not canonical_url:
                continue

            if canonical_url in seen_canonical:
                continue
            seen_canonical.add(canonical_url)

            upsert_catalog_place(
                tag=tag,
                canonical_url=canonical_url,
                maps_url=str(maps_url),
                place_id=str(place_id) if place_id else None,
                name=str(name) if name else None,
                address=str(address) if address else None,
                lat=float(lat) if isinstance(lat, (int, float)) else None,
                lng=float(lng) if isinstance(lng, (int, float)) else None,
                google_rating=it.get("rating"),  # type: ignore[arg-type]
                user_ratings_total=it.get("user_ratings_total"),  # type: ignore[arg-type]
                source_query=str(it.get("source_query") or ",".join(list(batch_hint)[:3])),
            )
            inserted_here += 1
            new_count += 1
        return inserted_here

    t_start = _now()
    done_batches = 0
    failed_batches = 0
    total_raw_places = 0
    abort_reason: Optional[str] = None
    last_event: str = ""
    stats_lock = threading.Lock()

    stop_progress = threading.Event()

    def _progress_thread() -> None:
        if not progress_every or float(progress_every) <= 0:
            return
        while not stop_progress.wait(float(progress_every)):
            with stats_lock:
                done = done_batches
                failed = failed_batches
                raw = total_raw_places
                inserted = new_count
                last = last_event
            elapsed = _now() - t_start
            avg = (elapsed / done) if done > 0 else 0.0
            remaining = max(total_batches - done, 0)
            eta = remaining * avg
            print(
                f"[discover] heartbeat: done={done}/{total_batches}, failed={failed}, "
                f"raw={raw}, inserted={inserted}, elapsed_min={elapsed/60:.1f}, eta_min={eta/60:.1f}, last={last}",
                flush=True,
            )

    prog_th = threading.Thread(target=_progress_thread, name="discover-progress", daemon=True)
    prog_th.start()

    def _is_apify_memory_limit_error(err: Exception) -> bool:
        s = str(err)
        return ("status=402" in s) and ("memory" in s or "Memory" in s or "actor-memory-limit-exceeded" in s)

    try:
        if workers <= 1:
            for bi, batch in enumerate(batches, start=1):
                batch_t0 = _now()
                with stats_lock:
                    last_event = f"batch_start:{bi}"

                done_queries = sum(len(b) for b in batches[: bi - 1])
                progress_pct = (done_queries / total_queries * 100.0) if total_queries > 0 else 0.0

                print(
                    f"[discover] batch {bi}/{total_batches} "
                    f"(queries {done_queries + 1}-{done_queries + len(batch)} of {total_queries}, "
                    f"{progress_pct:.1f}% done) ..."
                )
                items = search_places_bulk(
                    batch,
                    limit_per_query=limit_per_query,
                    language=language,
                    heartbeat_every=heartbeat_every,
                    heartbeat_prefix=f"discover {bi}/{total_batches}",
                )
                total_raw_places += len(items)
                print(f"[discover] raw places returned: {len(items)}")
                inserted_here = _upsert_items(items, batch)
                print(f"[discover] catalog upserts this batch: {inserted_here}")

                # be gentle to Apify
                time.sleep(0.4)

                done_batches = bi
                elapsed = _now() - t_start
                avg_per_batch = elapsed / done_batches if done_batches > 0 else 0.0
                remaining_batches = max(total_batches - done_batches, 0)
                eta_sec = remaining_batches * avg_per_batch

                update_job(
                    job_id=job_id,
                    status="running",
                    done=done_batches,
                    failed=failed_batches,
                    skipped=0,
                    message=f"raw={total_raw_places}, inserted={new_count}, elapsed_min={elapsed/60:.1f}, eta_min={eta_sec/60:.1f}",
                )

                batch_dt = _now() - batch_t0
                print(
                    f"[discover] progress {done_batches}/{total_batches} "
                    f"({(done_batches / total_batches * 100.0) if total_batches else 100.0:.1f}%), "
                    f"batch {batch_dt:.1f}s, avg {avg_per_batch:.1f}s/batch, ETA ~{eta_sec/60:.1f} min, "
                    f"raw={total_raw_places}, inserted={new_count}, failed_batches={failed_batches}"
                )
                with stats_lock:
                    last_event = f"batch_done:{bi}"
                if max_upserts_int is not None and new_count >= max_upserts_int:
                    abort_reason = f"early stop: reached max_upserts={max_upserts_int}"
                    print(f"[discover] {abort_reason}")
                    break
        else:
            # Parallelize network-bound Apify calls; keep DB writes in main thread to avoid SQLite locks.
            print(f"[discover] running in parallel: {workers} workers, {total_batches} batches")
            if heartbeat_every and float(heartbeat_every) > 0:
                print(
                    "[discover] note: per-call Apify heartbeat disabled in parallel mode; using aggregated progress heartbeats instead."
                )
            def _fetch_one(bi: int, batch: List[str]) -> Tuple[int, List[str], List[Dict[str, Any]], float]:
                t0 = _now()
                items = search_places_bulk(
                    batch,
                    limit_per_query=limit_per_query,
                    language=language,
                    # In parallel mode, disable per-request heartbeats to avoid noisy interleaved logs.
                    heartbeat_every=0.0,
                    heartbeat_prefix=f"discover {bi}/{total_batches}",
                )
                dt = _now() - t0
                return (bi, list(batch), items, dt)

            with ThreadPoolExecutor(max_workers=workers) as ex:
                futures = {
                    ex.submit(_fetch_one, bi, batch): (bi, batch) for bi, batch in enumerate(batches, start=1)
                }
                for fut in as_completed(futures):
                    try:
                        bi, batch, items, fetch_dt = fut.result()
                    except Exception as e:
                        failed_batches += 1
                        done_batches += 1
                        elapsed = _now() - t_start
                        avg_per_batch = elapsed / done_batches if done_batches > 0 else 0.0
                        remaining_batches = max(total_batches - done_batches, 0)
                        eta_sec = remaining_batches * avg_per_batch
                        update_job(
                            job_id=job_id,
                            status="running",
                            done=done_batches,
                            failed=failed_batches,
                            skipped=0,
                            message=f"raw={total_raw_places}, inserted={new_count}, elapsed_min={elapsed/60:.1f}, eta_min={eta_sec/60:.1f}, last_error={str(e)[:120]}",
                        )
                        print(
                            f"[discover] batch ERROR (done {done_batches}/{total_batches}, failed_batches={failed_batches}): {e}"
                        )
                        with stats_lock:
                            last_event = f"batch_error:{str(e)[:80]}"

                        # Fail-fast for Apify memory/plan limit errors to avoid spamming 20+ batches with the same 402.
                        if _is_apify_memory_limit_error(e):
                            abort_reason = (
                                "Apify memory/plan limit exceeded. "
                                "This is controlled by your Apify account quota (NOT your local PC). "
                                "Fix: stop other running Actor jobs in Apify Console, then rerun with "
                                "--discover-workers 1 (or 2 on higher plans)."
                            )
                            # Best-effort: cancel pending futures (running ones cannot be cancelled)
                            for ff in futures.keys():
                                ff.cancel()
                            update_job(
                                job_id=job_id,
                                status="error",
                                done=done_batches,
                                failed=failed_batches,
                                skipped=0,
                                message=abort_reason,
                            )
                            print(f"[discover] ABORT: {abort_reason}")
                            break
                        continue

                    total_raw_places += len(items)
                    inserted_here = _upsert_items(items, batch)
                    done_batches += 1
                    with stats_lock:
                        last_event = f"batch_done:{bi}"

                    elapsed = _now() - t_start
                    avg_per_batch = elapsed / done_batches if done_batches > 0 else 0.0
                    remaining_batches = max(total_batches - done_batches, 0)
                    eta_sec = remaining_batches * avg_per_batch

                    update_job(
                        job_id=job_id,
                        status="running",
                        done=done_batches,
                        failed=failed_batches,
                        skipped=0,
                        message=f"raw={total_raw_places}, inserted={new_count}, last_batch={bi}, fetch_s={fetch_dt:.1f}, elapsed_min={elapsed/60:.1f}, eta_min={eta_sec/60:.1f}",
                    )

                    print(
                        f"[discover] batch done {done_batches}/{total_batches} "
                        f"(batch={bi}, fetch={fetch_dt:.1f}s, raw={len(items)}, upserts={inserted_here}) | "
                        f"elapsed {elapsed/60:.1f} min, avg {avg_per_batch:.1f}s/batch, ETA ~{eta_sec/60:.1f} min | "
                        f"raw_total={total_raw_places}, inserted_total={new_count}, failed_batches={failed_batches}"
                    )

                    if max_upserts_int is not None and new_count >= max_upserts_int:
                        # Best-effort: cancel pending futures (running ones cannot be cancelled)
                        for ff in futures.keys():
                            ff.cancel()
                        abort_reason = f"early stop: reached max_upserts={max_upserts_int}"
                        update_job(
                            job_id=job_id,
                            status="done",
                            done=done_batches,
                            failed=failed_batches,
                            skipped=0,
                            message=abort_reason,
                        )
                        print(f"[discover] {abort_reason}")
                        break
    finally:
        stop_progress.set()

    if abort_reason:
        # Keep partial progress in DB, do not mark as "done".
        return new_count, len(seen_canonical)

    # new_count is "insert attempts" in this run; DB will handle true dedupe via UNIQUE.
    update_job(
        job_id=job_id,
        status="done",
        done=total_batches,
        failed=failed_batches,
        message=f"completed: raw={total_raw_places}, inserted={new_count}, failed_batches={failed_batches}",
    )
    return new_count, len(seen_canonical)


def analyze_catalog(
    *,
    tag: str,
    mode: str,
    max_places: Optional[int],
    force_refresh: bool,
    sleep_seconds: float,
    max_reviews: int,
    workers: int = 1,
    progress_every: float = 10.0,
) -> Dict[str, int]:
    from services.cache_store import get_cached_analysis, set_cached_analysis
    from services.review_store import upsert_place_reviews, list_recent_reviews
    from services.job_store import create_job, update_job
    from services.place_store import list_catalog_places, record_place_from_analysis, update_catalog_analyze_status
    from services.apify_client import scrape_reviews
    from services.url_normalizer import canonicalize

    # Import app module to reuse the existing LLM prompt + enrich_photos implementation.
    import app as app_module

    items = list_catalog_places(tag=tag, limit=max_places or 50000)
    if max_places is not None:
        items = items[: int(max_places)]

    stats = {
        "total": len(items),
        "skipped_cached": 0,  # kept for backward compat in script output
        "skipped_no_new_reviews": 0,
        "analyzed": 0,
        "failed": 0,
    }
    print(f"[analyze] catalog items: {len(items)} (tag={tag}, workers={workers})")

    # Progress is persisted into DB so you don't need to watch terminal.
    job_id = create_job(kind="xinyi_build", tag=tag, total=len(items), message=f"mode={mode}, workers={workers}")
    print(f"[analyze] job_id={job_id} (query via /api/jobs/{job_id})")

    db_lock = threading.Lock()
    stats_lock = threading.Lock()
    t_start = _now()
    done_n = 0
    active_n = 0
    last_event: str = ""

    try:
        progress_every = float(progress_every)
    except Exception:
        progress_every = 10.0
    if progress_every < 0:
        progress_every = 0.0

    stop_progress = threading.Event()

    def _progress_thread() -> None:
        if not progress_every or float(progress_every) <= 0:
            return
        while not stop_progress.wait(float(progress_every)):
            with stats_lock:
                done = done_n
                active = active_n
                last = last_event
                analyzed = stats["analyzed"]
                skipped = stats["skipped_no_new_reviews"]
                failed = stats["failed"]
                total = stats["total"]
            elapsed = _now() - t_start
            avg = (elapsed / done) if done > 0 else 0.0
            remaining = max(total - done, 0)
            eta = remaining * avg
            print(
                f"[analyze] heartbeat: done={done}/{total}, active={active}, "
                f"analyzed={analyzed}, skipped_no_new={skipped}, failed={failed}, "
                f"elapsed_min={elapsed/60:.1f}, eta_min={eta/60:.1f}, last={last}",
                flush=True,
            )

    prog_th = threading.Thread(target=_progress_thread, name="analyze-progress", daemon=True)
    prog_th.start()

    def _process_one(idx: int, row: Dict[str, str]) -> Tuple[str, str]:
        """
        Returns: (result, canonical_url)
          result in {"analyzed","skipped","failed"}
        """
        nonlocal active_n, last_event
        canonical_url = row.get("canonical_url") or ""
        display_name = row.get("name") or row.get("display_name") or canonical_url
        address = row.get("address")
        google_rating = row.get("google_rating")
        user_ratings_total = row.get("user_ratings_total")

        try:
            norm = canonicalize(canonical_url)
            cache_key = norm.get("cache_key") or ""
        except Exception:
            cache_key = ""

        if not cache_key:
            with db_lock:
                update_catalog_analyze_status(
                    tag=tag,
                    canonical_url=canonical_url,
                    status="error",
                    error="missing cache_key",
                )
            return ("failed", canonical_url)

        with db_lock:
            update_catalog_analyze_status(
                tag=tag,
                canonical_url=canonical_url,
                status="running",
                error=None,
            )

        t0 = _now()
        apify_s = 0.0
        llm_s = 0.0
        inserted_new = 0

        try:
            with stats_lock:
                # mark active + last event
                active_n += 1
                last_event = f"start:{idx}"

            # Always scrape newest reviews so we can do incremental updates.
            with stats_lock:
                last_event = f"scrape_reviews:{idx}"
            t_scrape0 = _now()
            reviews = scrape_reviews(
                canonical_url,
                max_reviews=max_reviews,
                language="zh-TW",
                heartbeat_every=0.0,  # aggregated progress handles feedback
                heartbeat_prefix=f"analyze {idx}",
            )
            apify_s = _now() - t_scrape0
            if not reviews:
                raise RuntimeError("no reviews returned from Apify")

            with db_lock:
                inserted_new, _processed = upsert_place_reviews(canonical_url=canonical_url, reviews=reviews)
                cached_entry_allow_stale = get_cached_analysis(cache_key, mode, allow_stale=True)

            # If there is no new review, keep old analysis (even if TTL expired) and skip Gemini.
            if (not force_refresh) and cached_entry_allow_stale is not None and inserted_new == 0:
                with db_lock:
                    update_catalog_analyze_status(
                        tag=tag,
                        canonical_url=canonical_url,
                        status="skipped_no_new_reviews",
                        error=None,
                    )
                total_s = _now() - t0
                print(
                    f"[analyze] place done idx={idx} result=skipped_no_new_reviews apify_s={apify_s:.1f} llm_s=0.0 total_s={total_s:.1f}",
                    flush=True,
                )
                return ("skipped", canonical_url)

            # Use local DB to assemble latest reviews for analysis (keeps old reviews, only adds new).
            with db_lock:
                reviews_for_ai = list_recent_reviews(canonical_url=canonical_url, limit=max_reviews)

            with stats_lock:
                last_event = f"llm:{idx}"
            t_llm0 = _now()
            analysis = app_module.analyse_reviews(reviews_for_ai, model="gemini-3-flash-preview")
            llm_s = _now() - t_llm0
            try:
                restaurant_name = analysis.get("restaurant_name", "")
                analysis = app_module.enrich_photos(analysis, reviews_for_ai, restaurant_name=restaurant_name)
            except Exception:
                pass

            with db_lock:
                set_cached_analysis(
                    cache_key=cache_key,
                    mode=mode,
                    canonical_url=canonical_url,
                    display_name=display_name,
                    result_obj=analysis,
                )

                record_place_from_analysis(
                    canonical_url=canonical_url,
                    display_name=display_name,
                    analysis=analysis,
                    address=address,
                    google_rating=google_rating,
                    user_ratings_total=user_ratings_total,
                )

                update_catalog_analyze_status(
                    tag=tag,
                    canonical_url=canonical_url,
                    status="done",
                    error=None,
                )

            total_s = _now() - t0
            print(
                f"[analyze] place done idx={idx} result=analyzed apify_s={apify_s:.1f} llm_s={llm_s:.1f} total_s={total_s:.1f} inserted_new={inserted_new}",
                flush=True,
            )
            return ("analyzed", canonical_url)
        except Exception as e:
            with db_lock:
                update_catalog_analyze_status(
                    tag=tag,
                    canonical_url=canonical_url,
                    status="error",
                    error=str(e)[:300],
                )
            total_s = _now() - t0
            print(
                f"[analyze] place done idx={idx} result=failed apify_s={apify_s:.1f} llm_s={llm_s:.1f} total_s={total_s:.1f} err={str(e)[:120]}",
                flush=True,
            )
            return ("failed", canonical_url)
        finally:
            with stats_lock:
                if active_n > 0:
                    active_n -= 1
            if sleep_seconds > 0:
                time.sleep(float(sleep_seconds))

    workers = max(1, int(workers or 1))
    # Note: This is IO-bound (Apify + LLM). Threads help a lot; keep workers reasonable to avoid rate limits.
    try:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futures = {ex.submit(_process_one, i, row): row for i, row in enumerate(items, start=1)}
            for fut in as_completed(futures):
                result, canonical_url = fut.result()
                with stats_lock:
                    done_n += 1
                    if result == "analyzed":
                        stats["analyzed"] += 1
                    elif result == "skipped":
                        stats["skipped_no_new_reviews"] += 1
                    else:
                        stats["failed"] += 1
                    last_event = f"done:{canonical_url}"

                # Persist progress for UI polling
                with db_lock:
                    update_job(
                        job_id=job_id,
                        status="running",
                        done=done_n,
                        failed=stats["failed"],
                        skipped=stats["skipped_no_new_reviews"],
                        message=f"last={canonical_url}",
                    )

                if done_n % 10 == 0 or done_n == len(items):
                    print(
                        f"[analyze] progress {done_n}/{len(items)} "
                        f"(analyzed={stats['analyzed']}, skipped_no_new={stats['skipped_no_new_reviews']}, failed={stats['failed']})"
                    )
    finally:
        stop_progress.set()

    with db_lock:
        update_job(
            job_id=job_id,
            status="done",
            done=len(items),
            failed=stats["failed"],
            skipped=stats["skipped_no_new_reviews"],
            message="completed",
        )

    return stats


def main() -> None:
    load_dotenv(override=True)

    parser = argparse.ArgumentParser(description="Build a Taipei Xinyi restaurant DB (catalog + cached analyses).")
    parser.add_argument("--tag", default="xinyi", help="Catalog tag to use (default: xinyi)")
    parser.add_argument("--mode", default="quick", help="Cache mode key (default: quick)")
    parser.add_argument("--language", default="zh-TW", help="Apify language (default: zh-TW)")
    parser.add_argument("--batch-size", type=int, default=20, help="How many queries per Apify bulk call")
    parser.add_argument(
        "--limit-per-query",
        type=int,
        default=200,
        help="Max places per search string (Apify actor input)",
    )
    parser.add_argument(
        "--queries-limit",
        type=int,
        default=None,
        help="Only use the first N discovery queries (for small-scale testing).",
    )
    parser.add_argument(
        "--discover-query",
        action="append",
        default=None,
        help="Override discovery queries. You can pass this flag multiple times. Example: --discover-query \"鼎泰豐 台北101\"",
    )
    parser.add_argument(
        "--discover-max-upserts",
        type=int,
        default=None,
        help="Stop discovery early after N successful catalog upserts (useful for single-store demo).",
    )
    parser.add_argument(
        "--discover-heartbeat-every",
        type=float,
        default=5.0,
        help="Apify heartbeat interval (seconds) per call during discovery. Set 0 to disable. (default 5)",
    )
    parser.add_argument(
        "--discover-progress-every",
        type=float,
        default=10.0,
        help="Aggregated progress print interval (seconds) during discovery. Set 0 to disable. (default 10)",
    )
    parser.add_argument("--discover-only", action="store_true", help="Only discover and store catalog entries")
    parser.add_argument("--analyze-only", action="store_true", help="Only analyze existing catalog entries")
    parser.add_argument(
        "--discover-workers",
        type=int,
        default=1,
        help="Parallel workers for discovery (Apify bulk calls). Use 5 for faster catalog build. (default 1)",
    )
    parser.add_argument("--max-places", type=int, default=None, help="Limit how many catalog places to analyze")
    parser.add_argument("--force-refresh", action="store_true", help="Bypass cache TTL and re-analyze everything")
    parser.add_argument("--sleep-seconds", type=float, default=0.3, help="Sleep between analyses to avoid rate limits")
    parser.add_argument("--max-reviews", type=int, default=60, help="Max reviews to scrape per place (default 60)")
    parser.add_argument("--workers", type=int, default=6, help="Parallel workers for Apify+LLM (default 6)")
    parser.add_argument(
        "--analyze-progress-every",
        type=float,
        default=10.0,
        help="Aggregated progress print interval (seconds) during analysis. Set 0 to disable. (default 10)",
    )

    args = parser.parse_args()

    from services.cache_store import init_db
    from services.place_store import init_place_db
    from services.review_store import init_review_db
    from services.job_store import init_job_db

    init_db()
    init_place_db()
    init_review_db()
    init_job_db()

    if not args.analyze_only:
        if args.discover_query:
            queries = [str(q).strip() for q in (args.discover_query or []) if str(q).strip()]
            total_queries = len(queries)
            print(f"[discover] using override queries: {total_queries}")
        else:
            queries = build_xinyi_queries()
            total_queries = len(queries)
        if args.queries_limit is not None:
            try:
                n = max(1, int(args.queries_limit))
            except Exception:
                n = 1
            queries = queries[:n]
            print(f"[discover] total queries: {total_queries} (limited to first {len(queries)} via --queries-limit)")
        else:
            print(f"[discover] total queries: {total_queries}")
        inserted, unique_seen = discover_xinyi_places(
            tag=args.tag,
            queries=queries,
            batch_size=args.batch_size,
            limit_per_query=args.limit_per_query,
            language=args.language,
            workers=int(args.discover_workers),
            heartbeat_every=float(args.discover_heartbeat_every),
            progress_every=float(args.discover_progress_every),
            max_upserts=args.discover_max_upserts,
        )
        print(f"[discover] catalog upsert attempts: {inserted}, unique_canonical_seen: {unique_seen}")

    if not args.discover_only:
        stats = analyze_catalog(
            tag=args.tag,
            mode=args.mode,
            max_places=args.max_places,
            force_refresh=bool(args.force_refresh),
            sleep_seconds=float(args.sleep_seconds),
            max_reviews=int(args.max_reviews),
            workers=int(args.workers),
            progress_every=float(args.analyze_progress_every),
        )
        print("[analyze] summary:", stats)


if __name__ == "__main__":
    main()

