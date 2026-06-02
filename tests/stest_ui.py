#!/usr/bin/env python3
"""
Intent Bus Real-World Stress Suite v2
====================================

A realistic stress harness for Intent Bus that simulates production-style
conditions instead of a pure synthetic benchmark.

Improvements over the earlier version:
- Stable ANSI dashboard (no repeated banner spam)
- Adaptive idle backoff
- Better timeout handling
- Realistic worker jitter / long-tail latency
- Gentle connection pacing
- Safer retry behavior
- Clean shutdown on SIGINT / SIGTERM
- Per-run JSONL stream + final JSON report
- Suitable for Termux, Linux, and macOS terminals

Recommended server-side tuning for load testing:
- Multiple uWSGI processes
- Threads enabled
- SQLite WAL mode
- Busy timeout configured
"""

from __future__ import annotations

import argparse
import json
import os
import random
import secrets
import signal
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from intent_bus import (
    IntentBusError,
    IntentBusLeaseLostError,
    IntentBusNetworkError,
    IntentBusRateLimitError,
    IntentClient,
)


BASE_URL = os.environ.get("BASE_URL", "https://intent-bus.onrender.com/")
DEFAULT_GOAL = "stress_capacity"


# =============================================================================
# CONFIG
# =============================================================================


def get_api_key() -> str:
    path = Path.home() / ".apikey"
    if not path.exists():
        print("🔴 Fatal: Missing ~/.apikey")
        raise SystemExit(1)
    key = path.read_text().strip()
    if not key:
        print("🔴 Fatal: ~/.apikey is empty")
        raise SystemExit(1)
    return key


# Lazy loading to avoid import-time failures
_API_KEY_CACHE = None


def ensure_api_key() -> str:
    global _API_KEY_CACHE
    if _API_KEY_CACHE is None:
        _API_KEY_CACHE = get_api_key()
    return _API_KEY_CACHE


PROFILES: Dict[str, Dict[str, Any]] = {
    # Small, safe baseline. Good for sanity checks.
    "low": {
        "publishers": 2,
        "workers": 4,
        "jobs": 50,
        "payload_kb": 2,
        "timeout": 120,
        "publish_burst_pause": (0.02, 0.12),
        "worker_think": (0.05, 0.25),
        "tail_latency_chance": 0.005,
        "tail_latency_range": (0.5, 1.8),
        "failure_chance": 0.005,
        "network_turbulence": 0.01,
    },
    # Moderate production-like pressure.
    "medium": {
        "publishers": 4,
        "workers": 8,
        "jobs": 400,
        "payload_kb": 4,
        "timeout": 480,
        "publish_burst_pause": (0.01, 0.08),
        "worker_think": (0.05, 0.30),
        "tail_latency_chance": 0.01,
        "tail_latency_range": (1.0, 3.0),
        "failure_chance": 0.01,
        "network_turbulence": 0.02,
    },
    # Heavier load with long-tail and failure injection.
    "high": {
        "publishers": 8,
        "workers": 16,
        "jobs": 1500,
        "payload_kb": 5,
        "timeout": 1200,
        "publish_burst_pause": (0.0, 0.05),
        "worker_think": (0.05, 0.35),
        "tail_latency_chance": 0.015,
        "tail_latency_range": (1.0, 4.0),
        "failure_chance": 0.01,
        "network_turbulence": 0.03,
    },
}


# =============================================================================
# LOGGING
# =============================================================================


class DiagnosticsLogger:
    def __init__(self, profile_name: str):
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.stream_path = f"stress_stream_{profile_name}_{timestamp}.jsonl"
        self.report_path = f"stress_report_{profile_name}_{timestamp}.json"
        self.lock = threading.Lock()

        with open(self.stream_path, "w", encoding="utf-8") as f:
            f.write(json.dumps(
                {"event": "test_started", "profile": profile_name, "ts": time.time()}) + "\n")

    def log_event(self, event_type: str, data: Dict[str, Any]) -> None:
        payload = {"event": event_type, "ts": time.time(), **data}
        with self.lock:
            with open(self.stream_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(payload) + "\n")

    def save_final_report(self, report: Dict[str, Any]) -> str:
        with open(self.report_path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)
        return self.report_path


# =============================================================================
# METRICS
# =============================================================================


@dataclass
class Metrics:
    published: int = 0
    claimed: int = 0
    fulfilled: int = 0
    failed: int = 0

    err_network: int = 0
    err_lease_lost: int = 0
    err_rate_limit: int = 0
    err_publish: int = 0
    err_unknown: int = 0

    publish_latencies: List[float] = field(default_factory=list)
    worker_latencies: List[float] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def inc(self, name: str, amount: int = 1) -> None:
        with self.lock:
            setattr(self, name, getattr(self, name) + amount)

    def add_latency(self, value: float, kind: str = "publish") -> None:
        with self.lock:
            if kind == "publish":
                self.publish_latencies.append(value)
            else:
                self.worker_latencies.append(value)

    def snapshot(self) -> Dict[str, Any]:
        with self.lock:
            return {
                "published": self.published,
                "claimed": self.claimed,
                "fulfilled": self.fulfilled,
                "failed": self.failed,
                "err_network": self.err_network,
                "err_lease_lost": self.err_lease_lost,
                "err_rate_limit": self.err_rate_limit,
                "err_publish": self.err_publish,
                "err_unknown": self.err_unknown,
                "publish_latencies": list(self.publish_latencies),
                "worker_latencies": list(self.worker_latencies),
            }


# =============================================================================
# UTILS
# =============================================================================


def percentile(values: List[float], pct: float) -> float:
    if not values:
        return 0.0
    values = sorted(values)
    idx = int((len(values) - 1) * pct)
    return values[idx]


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


# =============================================================================
# SCORING
# =============================================================================


def calculate_score(snapshot: Dict[str, Any], elapsed: float) -> Dict[str, Any]:
    total_processed = snapshot["fulfilled"] + snapshot["failed"]
    success_rate = (snapshot["fulfilled"] /
                    total_processed * 100.0) if total_processed else 0.0
    throughput = (snapshot["fulfilled"] / elapsed) if elapsed > 0 else 0.0

    publish_lats = snapshot["publish_latencies"]
    worker_lats = snapshot["worker_latencies"]

    p50 = percentile(worker_lats, 0.50)
    p95 = percentile(worker_lats, 0.95)
    p99 = percentile(worker_lats, 0.99)

    pub_p95 = percentile(publish_lats, 0.95)

    total_errs = (
        snapshot["err_network"]
        + snapshot["err_lease_lost"]
        + snapshot["err_rate_limit"]
        + snapshot["err_publish"]
        + snapshot["err_unknown"]
    )
    err_dist = {
        "network": f"{(snapshot['err_network'] / total_errs * 100):.1f}%" if total_errs else "0%",
        "lease_lost": f"{(snapshot['err_lease_lost'] / total_errs * 100):.1f}%" if total_errs else "0%",
        "rate_limit": f"{(snapshot['err_rate_limit'] / total_errs * 100):.1f}%" if total_errs else "0%",
        "publish": f"{(snapshot['err_publish'] / total_errs * 100):.1f}%" if total_errs else "0%",
        "unknown": f"{(snapshot['err_unknown'] / total_errs * 100):.1f}%" if total_errs else "0%",
    }

    # Grade based on success rate, P95 latency, and lease stability
    grade = "F"
    if success_rate >= 99.0 and p95 < 2.0 and snapshot["err_lease_lost"] == 0:
        grade = "A+"
    elif success_rate >= 95.0 and p95 < 5.0:
        grade = "A"
    elif success_rate >= 85.0:
        grade = "B"
    elif success_rate >= 70.0:
        grade = "C"

    return {
        "grade": grade,
        "success_rate_pct": round(success_rate, 2),
        "throughput_sec": round(throughput, 2),
        "publish_p95": round(pub_p95, 3),
        "worker_p50": round(p50, 3),
        "worker_p95": round(p95, 3),
        "worker_p99": round(p99, 3),
        "error_distribution": err_dist,
    }


# =============================================================================
# DASHBOARD
# =============================================================================


class LiveDashboard:
    def __init__(
        self,
        start_time: float,
        logger: DiagnosticsLogger,
        total_jobs: int,
        profile_name: str,
        metrics: Metrics,
        stop_event: threading.Event,
    ):
        self.start_time = start_time
        self.logger = logger
        self.total_jobs = total_jobs
        self.profile_name = profile_name
        self.metrics = metrics
        self.stop_event = stop_event
        self.last_tick_log = 0.0
        self.last_rate_time = start_time
        self.last_pub = 0
        self.last_ful = 0
        self.pub_rate = 0.0
        self.ful_rate = 0.0

    def run(self) -> None:
        # Hide cursor
        sys.stdout.write("\033[?25l")
        sys.stdout.flush()
        try:
            while not self.stop_event.is_set():
                self.draw()
                time.sleep(0.5)
        finally:
            # Restore cursor
            sys.stdout.write("\033[?25h")
            sys.stdout.flush()

    def draw(self) -> None:
        now = time.time()
        elapsed = now - self.start_time
        snap = self.metrics.snapshot()

        # Calculate per-second rates every 1 second
        if now - self.last_rate_time >= 1.0:
            dt = now - self.last_rate_time
            self.pub_rate = (snap["published"] - self.last_pub) / dt
            self.ful_rate = (snap["fulfilled"] - self.last_ful) / dt
            self.last_pub = snap["published"]
            self.last_ful = snap["fulfilled"]
            self.last_rate_time = now

        # Log a progress snapshot every 5 seconds
        if now - self.last_tick_log >= 5.0:
            self.logger.log_event(
                "progress_tick",
                {
                    "elapsed_sec": round(elapsed, 1),
                    **snap,
                },
            )
            self.last_tick_log = now

        processed = snap["fulfilled"] + snap["failed"]
        pct = (processed / self.total_jobs * 100.0) if self.total_jobs else 0.0
        width = 30
        filled = int(width * pct / 100.0)
        bar = "█" * filled + "░" * (width - filled)
        p95 = percentile(snap["worker_latencies"], 0.95)
        p99 = percentile(snap["worker_latencies"], 0.99)

        lines = [
            f"=== [LIVE] INTENT BUS STRESS TEST | PROFILE: {self.profile_name.upper()} ===",
            f"⏱  Elapsed: {elapsed:7.1f}s   |  📊 Progress: [{bar}] {pct:5.1f}%",
            "",
            "[ QUEUE METRICS ]",
            f"📤 Published:  {snap['published']:<6} ({self.pub_rate:.1f}/s)",
            f"📥 Claimed:    {snap['claimed']:<6}",
            f"✅ Fulfilled:  {snap['fulfilled']:<6} ({self.ful_rate:.1f}/s)",
            f"❌ Failed:     {snap['failed']:<6}",
            "",
            "[ ERROR COUNTERS ]",
            f"🔌 Network Drops: {snap['err_network']}",
            f"🔒 Lease Lost:    {snap['err_lease_lost']}",
            f"🛑 429 Throttled: {snap['err_rate_limit']}",
            f"⚠️ Pub Rejects:   {snap['err_publish']}",
            f"❓ Unknown Errs:   {snap['err_unknown']}",
            "",
            "[ LATENCY ]",
            f"P95 Worker Latency: {p95:.3f}s",
            f"P99 Worker Latency: {p99:.3f}s",
            "======================================================================",
        ]

        # Clear screen and move cursor to top-left
        sys.stdout.write("\033[H\033[J")
        sys.stdout.write("\n".join(lines) + "\n")
        sys.stdout.flush()


# =============================================================================
# WORKERS
# =============================================================================


def build_payload(payload_kb: int) -> Dict[str, Any]:
    blob_bytes = max(128, payload_kb * 512)
    return {
        "blob": secrets.token_hex(blob_bytes),
        "meta": {
            "created_at": time.time(),
            "source": "stress_suite",
            "content_type": "job_payload",
            "attempt_id": secrets.token_hex(8),
        },
    }


def publisher_worker(
    namespace: str,
    goal: str,
    jobs: int,
    payload_kb: int,
    metrics: Metrics,
    logger: DiagnosticsLogger,
    stop_event: threading.Event,
    pause_range: tuple[float, float],
    turbulence: float,
    api_key: str,
) -> None:
    client = IntentClient(base_url=BASE_URL, api_key=api_key, timeout=30)

    try:
        for i in range(jobs):
            if stop_event.is_set():
                break

            try:
                payload = build_payload(payload_kb)
                priority = random.randint(50, 500)
                max_attempts = 3

                if random.random() < turbulence:
                    time.sleep(random.uniform(0.2, 1.0))

                t0 = time.perf_counter()
                client.publish(
                    goal=goal,
                    payload=payload,
                    namespace=namespace,
                    priority=priority,
                    max_attempts=max_attempts,
                )
                dt = time.perf_counter() - t0

                metrics.inc("published")
                metrics.add_latency(dt, kind="publish")

            except IntentBusRateLimitError:
                metrics.inc("err_rate_limit")
                time.sleep(random.uniform(0.5, 2.0))
            except IntentBusNetworkError:
                metrics.inc("err_network")
                time.sleep(random.uniform(1.0, 3.0))
            except IntentBusError as e:
                metrics.inc("err_publish")
                logger.log_event("publish_error", {"error": str(e)})
                time.sleep(0.5)
            except Exception as e:
                metrics.inc("err_unknown")
                logger.log_event("publish_unknown_error", {"error": str(e)})
                time.sleep(0.5)

            if pause_range[1] > 0:
                time.sleep(random.uniform(*pause_range))

            if i > 0 and i % max(1, jobs // 10) == 0:
                time.sleep(random.uniform(0.15, 0.6))

    finally:
        client.close()


def runtime_worker(
    namespace: str,
    goal: str,
    worker_id: str,
    metrics: Metrics,
    logger: DiagnosticsLogger,
    stop_event: threading.Event,
    think_range: tuple[float, float],
    tail_latency_chance: float,
    tail_latency_range: tuple[float, float],
    failure_chance: float,
    turbulence: float,
    api_key: str,
) -> None:
    client = IntentClient(base_url=BASE_URL, api_key=api_key, timeout=60)
    idle_backoff = 1.0

    try:
        while not stop_event.is_set():
            try:
                if random.random() < turbulence:
                    time.sleep(random.uniform(0.05, 0.4))

                job = client.claim(
                    goal=goal,
                    namespace=namespace,
                    worker_id=worker_id,
                    capabilities=["stress", "real-world"],
                )

                if not job:
                    time.sleep(idle_backoff)
                    idle_backoff = min(idle_backoff * 1.5, 10.0)
                    continue

                idle_backoff = 1.0
                metrics.inc("claimed")

                work_time = random.uniform(*think_range)
                if random.random() < tail_latency_chance:
                    work_time += random.uniform(*tail_latency_range)

                t0 = time.perf_counter()
                time.sleep(work_time)

                if random.random() < failure_chance:
                    client.fail(
                        intent_id=job.id,
                        claim_token=job.claim_token,
                        error="simulated_worker_failure",
                    )
                    metrics.inc("failed")
                    continue

                result = {
                    "worker": worker_id,
                    "processed_in": round(work_time, 4),
                    "status": "ok",
                    "mode": "real_world",
                    "timestamp": now_iso(),
                }

                client.fulfill(
                    intent_id=job.id,
                    claim_token=job.claim_token,
                    result=result,
                )

                dt = time.perf_counter() - t0
                metrics.inc("fulfilled")
                metrics.add_latency(dt, kind="worker")

                time.sleep(random.uniform(0.01, 0.12))

            except IntentBusLeaseLostError:
                metrics.inc("err_lease_lost")
                time.sleep(random.uniform(0.1, 0.5))
            except IntentBusRateLimitError:
                metrics.inc("err_rate_limit")
                time.sleep(random.uniform(0.5, 2.0))
            except IntentBusNetworkError:
                metrics.inc("err_network")
                time.sleep(random.uniform(1.0, 3.0))
            except Exception as e:
                metrics.inc("err_unknown")
                logger.log_event("worker_exception", {
                                 "worker_id": worker_id, "error": str(e)})
                time.sleep(1.0)

    finally:
        client.close()


# =============================================================================
# ORCHESTRATION
# =============================================================================


def run_profile(profile_name: str, config: Dict[str, Any], goal: str) -> Dict[str, Any]:
    print(
        f"\n🚀 Booting Phase: {profile_name.upper()} "
        f"({config['publishers']} publishers, {config['workers']} workers, {config['jobs']} jobs)\n"
    )

    api_key = ensure_api_key()

    metrics = Metrics()
    logger = DiagnosticsLogger(profile_name)
    stop_event = threading.Event()

    namespace = f"stress-{profile_name}-{int(time.time())}"

    start = time.time()

    dashboard = LiveDashboard(
        start_time=start,
        logger=logger,
        total_jobs=config["jobs"],
        profile_name=profile_name,
        metrics=metrics,
        stop_event=stop_event,
    )

    ui_thread = threading.Thread(target=dashboard.run, daemon=True)
    ui_thread.start()

    def _handle_signal(signum, frame):
        stop_event.set()
        print(f"\n🛑 Received signal {signum}; stopping cleanly...")

    previous_sigint = signal.getsignal(signal.SIGINT)
    previous_sigterm = signal.getsignal(signal.SIGTERM)
    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    try:
        with ThreadPoolExecutor(max_workers=config["publishers"] + config["workers"]) as pool:
            for i in range(config["workers"]):
                pool.submit(
                    runtime_worker,
                    namespace,
                    goal,
                    f"w-{i}",
                    metrics,
                    logger,
                    stop_event,
                    config["worker_think"],
                    config["tail_latency_chance"],
                    config["tail_latency_range"],
                    config["failure_chance"],
                    config["network_turbulence"],
                    api_key,
                )

            base = config["jobs"] // config["publishers"]
            rem = config["jobs"] % config["publishers"]
            publish_futures = []
            for i in range(config["publishers"]):
                jobs_for_this_pub = base + (1 if i < rem else 0)
                publish_futures.append(
                    pool.submit(
                        publisher_worker,
                        namespace,
                        goal,
                        jobs_for_this_pub,
                        config["payload_kb"],
                        metrics,
                        logger,
                        stop_event,
                        config["publish_burst_pause"],
                        config["network_turbulence"],
                        api_key,
                    )
                )

            try:
                while not stop_event.is_set():
                    time.sleep(1.0)
                    elapsed = time.time() - start
                    snap = metrics.snapshot()

                    if elapsed > config["timeout"]:
                        stop_event.set()
                        print(
                            f"\n\n⏳ Hard timeout reached ({config['timeout']}s). "
                            f"Stopping test cleanly."
                        )
                        break

                    if all(f.done() for f in publish_futures) and snap["published"] > 0:
                        if snap["fulfilled"] + snap["failed"] >= snap["published"]:
                            stop_event.set()
                            break

            except KeyboardInterrupt:
                stop_event.set()
                raise

    finally:
        stop_event.set()
        ui_thread.join(timeout=1.5)
        signal.signal(signal.SIGINT, previous_sigint)
        signal.signal(signal.SIGTERM, previous_sigterm)

    elapsed = time.time() - start
    final_snap = metrics.snapshot()
    score = calculate_score(final_snap, elapsed)

    report = {
        "profile": profile_name,
        "duration_sec": round(elapsed, 2),
        "configuration": config,
        "namespace": namespace,
        "goal": goal,
        "metrics": final_snap,
        "evaluation": score,
    }

    report_path = logger.save_final_report(report)
    print(
        f"\n✅ [{profile_name.upper()}] Done. "
        f"Grade: {score['grade']} | Success: {score['success_rate_pct']}% | "
        f"Throughput: {score['throughput_sec']} j/s"
    )
    print(f"📄 Report saved: {report_path}")

    return report


# =============================================================================
# COMPARISON
# =============================================================================


def print_comparison_matrix(results: Dict[str, Dict[str, Any]]) -> None:
    print("\n" + "=" * 90)
    print(f"{'METRIC':<22} | {'LOW':<20} | {'MEDIUM':<20} | {'HIGH':<20}")
    print("=" * 90)

    rows = [
        ("Grade", lambda r: r["evaluation"]["grade"]),
        ("Success Rate", lambda r: f"{r['evaluation']['success_rate_pct']}%"),
        ("Throughput", lambda r: f"{r['evaluation']['throughput_sec']} j/s"),
        ("Worker P50", lambda r: f"{r['evaluation']['worker_p50']}s"),
        ("Worker P95", lambda r: f"{r['evaluation']['worker_p95']}s"),
        ("Worker P99", lambda r: f"{r['evaluation']['worker_p99']}s"),
        ("Publish P95", lambda r: f"{r['evaluation']['publish_p95']}s"),
        ("Network Err", lambda r: r["evaluation"]
         ["error_distribution"]["network"]),
        ("Lease Lost", lambda r: r["evaluation"]
         ["error_distribution"]["lease_lost"]),
        ("Rate Limit", lambda r: r["evaluation"]
         ["error_distribution"]["rate_limit"]),
    ]

    for label, extractor in rows:
        low = extractor(results.get("low", {})) if "low" in results else "N/A"
        med = extractor(results.get("medium", {})
                        ) if "medium" in results else "N/A"
        high = extractor(results.get("high", {})
                         ) if "high" in results else "N/A"
        print(f"{label:<22} | {low:<20} | {med:<20} | {high:<20}")

    print("=" * 90 + "\n")


# =============================================================================
# CLI
# =============================================================================


def cleanup_old_logs(days_old: int = 3) -> None:
    cutoff = time.time() - (days_old * 86400)
    deleted = 0
    for name in os.listdir("."):
        if not name.startswith("stress_"):
            continue
        if not (name.endswith(".json") or name.endswith(".jsonl")):
            continue
        try:
            if os.path.getmtime(name) < cutoff:
                os.remove(name)
                deleted += 1
        except OSError:
            pass
    if deleted:
        print(
            f"🧹 Cleaned up {deleted} stale log files older than {days_old} days.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Intent Bus Real-World Stress Suite v2")

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("-l", "--low", action="store_true",
                       help="Run low-intensity profile")
    group.add_argument("-m", "--medium", action="store_true",
                       help="Run medium-intensity profile")
    group.add_argument("-H", "--high-only", action="store_true",
                       help="Run high-intensity profile")
    group.add_argument("-a", "--all", action="store_true",
                       help="Run low -> medium -> high sequentially")

    parser.add_argument("--cleanup", action="store_true",
                        help="Delete stress logs older than 3 days")
    parser.add_argument("--goal", default=DEFAULT_GOAL,
                        help=f"Goal name to use (default: {DEFAULT_GOAL})")

    args = parser.parse_args()

    if args.cleanup:
        cleanup_old_logs()

    goal_name = args.goal

    phases_to_run = ["low", "medium", "high"] if args.all else []
    if not args.all:
        if args.low:
            phases_to_run.append("low")
        if args.medium:
            phases_to_run.append("medium")
        if args.high_only:
            phases_to_run.append("high")
    results: Dict[str, Dict[str, Any]] = {}

    try:
        for idx, phase in enumerate(phases_to_run):
            results[phase] = run_profile(phase, PROFILES[phase], goal_name)
            if args.all and idx < len(phases_to_run) - 1:
                print("\n♻️  Cooling down for 10 seconds before next phase...\n")
                time.sleep(10)
    except KeyboardInterrupt:
        print("\n🛑 Suite aborted by user. Exiting safely.")
    finally:
        sys.stdout.write("\033[?25h")
        sys.stdout.flush()

    if args.all and len(results) > 1:
        print_comparison_matrix(results)


if __name__ == "__main__":
    main()
