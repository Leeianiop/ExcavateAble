"""
Edge Node CLI — Main Entry Point
Raspberry Pi 5 + Camera Module + Hailo-8L AI HAT
3D Scanning Web App Edge Capture Node

Usage:
    python -m edge_node.main capture --shots 20 --interval 1.2
    python -m edge_node.main capture-and-upload --backend http://10.0.0.5:8000
    python -m edge_node.main health-check
    python -m edge_node.main verify-hailo
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

from .config import EdgeNodeConfig
from .pipeline import BackendUploadClient, CapturePipeline
from .utils import setup_logging

logger = logging.getLogger(__name__)

DEFAULT_CONFIG = Path(__file__).parent / "config" / "config.yaml"


def _load_config(path: str | None) -> EdgeNodeConfig:
    cfg_path = Path(path) if path else DEFAULT_CONFIG
    logger.info(f"Loading config from {cfg_path.resolve()}")
    config = EdgeNodeConfig.from_yaml(cfg_path)
    errors = config.validate()
    if errors:
        for e in errors:
            logger.error(f"Config validation error: {e}")
        sys.exit(2)
    setup_logging(config.logging)
    return config


def cmd_health_check(args: argparse.Namespace) -> int:
    config = _load_config(args.config)
    print("\n=== Edge Node Health Check ===\n")

    print(f"[1/4] Config OK: loaded from {DEFAULT_CONFIG}")
    print(f"      capture: {config.capture.width}x{config.capture.height}, "
          f"{config.capture.num_shots} shots @ {config.capture.shot_interval_sec}s")
    print(f"      hailo: seg={config.hailo.segmentation_model_path}, "
          f"depth={config.hailo.depth_model_path}")

    print(f"[2/4] Camera: initializing mock/probing...")
    try:
        from .capture import CameraCapture
        with CameraCapture(config.capture) as cam:
            status = "OK (mock)" if not _PICAMERA_ACTIVE(cam) else "OK (HW)"
        print(f"      {status}")
    except Exception as exc:
        print(f"      FAIL: {exc}")

    print(f"[3/4] Hailo-8L: initializing...")
    try:
        from .ai import HailoInferenceEngine
        with HailoInferenceEngine(config.hailo) as ai:
            hailo_status = (
                "OK (HW connected)" if ai.hailo_available and ai.initialized
                else "OK (mock/sim mode)"
            )
        print(f"      {hailo_status}")
    except Exception as exc:
        print(f"      FAIL: {exc}")

    print(f"[4/4] Backend connectivity: {config.upload.backend_url}")
    try:
        from .pipeline import BackendUploadClient
        with BackendUploadClient(config.upload) as client:
            print(f"      {'OK (client ready)' if client.http_available else 'OK (stub/no httpx)'}")
    except Exception as exc:
        print(f"      FAIL: {exc}")

    print("\n=== Health Check Complete ===")
    return 0


def _PICAMERA_ACTIVE(cam) -> bool:
    try:
        from .capture.camera import _PICAMERA_AVAILABLE
        return _PICAMERA_AVAILABLE and cam.initialized
    except Exception:
        return False


def cmd_verify_hailo(args: argparse.Namespace) -> int:
    config = _load_config(args.config)
    import numpy as np
    from .ai import HailoInferenceEngine

    print("=== Hailo-8L AI Verification ===\n")
    rng = np.random.default_rng(42)
    test_img = (rng.random((1088, 1920, 3)) * 255).astype(np.uint8)

    with HailoInferenceEngine(config.hailo) as engine:
        print(f"SDK available : {engine.hailo_available}")
        t0 = time.perf_counter()
        seg = engine.run_segmentation(test_img)
        t_seg = (time.perf_counter() - t0) * 1000

        t0 = time.perf_counter()
        depth = engine.run_depth(test_img)
        t_depth = (time.perf_counter() - t0) * 1000

        print(f"Segmentation  : {seg.stats.infer_ms:.1f}ms infer / total {t_seg:.1f}ms")
        print(f"  mask shape : {seg.foreground_mask.shape}, "
              f"fg pixels: {seg.foreground_mask.sum()}")
        print(f"Depth         : {depth.stats.infer_ms:.1f}ms infer / total {t_depth:.1f}ms")
        print(f"  depth range : [{depth.depth_map.min():.3f}, {depth.depth_map.max():.3f}]")

        t0 = time.perf_counter()
        seg2, depth2 = engine.run_fused_pipeline(test_img)
        t_fused = (time.perf_counter() - t0) * 1000
        print(f"\nFused pipeline: {t_fused:.1f}ms total "
              f"(seg+depth overlap, target ≤ 50ms/frame on Hailo-8L)")

    print("\n=== Verification Complete ===")
    return 0


def cmd_capture(args: argparse.Namespace) -> int:
    config = _load_config(args.config)
    if args.shots:
        config.capture.num_shots = int(args.shots)
    if args.interval is not None:
        config.capture.shot_interval_sec = float(args.interval)
    if args.output:
        config.storage.local_dir = args.output

    with CapturePipeline(config) as pipeline:
        bundle = pipeline.run_session()

    print(f"\nCapture complete:")
    print(f"  bundle_id   : {bundle.bundle_id}")
    print(f"  session_id  : {bundle.session_id}")
    print(f"  shots       : {bundle.num_shots}")
    print(f"  total size  : {bundle.total_size_bytes / (1024*1024):.2f} MB")
    print(f"  manifest    : {bundle.manifest_path.resolve()}")
    return 0


def cmd_capture_and_upload(args: argparse.Namespace) -> int:
    config = _load_config(args.config)
    if args.shots:
        config.capture.num_shots = int(args.shots)
    if args.interval is not None:
        config.capture.shot_interval_sec = float(args.interval)
    if args.backend:
        config.upload.backend_url = args.backend
    if args.api_key:
        config.upload.api_key = args.api_key
    if args.output:
        config.storage.local_dir = args.output

    with CapturePipeline(config) as pipeline:
        bundle = pipeline.run_session()

    print(f"Captured bundle {bundle.bundle_id} ({bundle.num_shots} shots, uploading...")

    with BackendUploadClient(config.upload) as client:
        result = client.upload_bundle(bundle)

    if not result.success:
        print(f"UPLOAD FAILED: {result.error}")
        return 3

    print(f"Upload OK:")
    print(f"  job_id       : {result.job_id}")
    print(f"  uploaded_MB    : {result.uploaded_bytes / (1024*1024):.2f}")
    print(f"  attempts   : {result.attempt_count}")
    print(f"  elapsed    : {result.elapsed_sec:.1f}s")
    if result.asset_url:
        print(f"  viewer URL  : {result.asset_url}")

    if args.wait and result.job_id:
        print("\nWaiting for backend reconstruction (polling job status)...")
        with BackendUploadClient(config.upload) as client:
            final = client.poll_job_status(result.job_id, timeout_sec=600)
        print(f"Final status: {final}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="edge-node",
        description="3D Scanning Edge Capture Node (Raspberry Pi 5 + Pi Camera + Hailo-8L)",
    )
    p.add_argument("--config", "-c", type=str, default=None,
                   help=f"Path to YAML config (default: ./edge_node/config/config.yaml)")
    sub = p.add_subparsers(dest="command", required=True)

    hc = sub.add_parser("health-check", help="Validate config, camera, Hailo, backend connectivity")
    hc.set_defaults(func=cmd_health_check)

    vh = sub.add_parser("verify-hailo", help="Run segmentation+depth inference benchmark on synthetic frame")
    vh.set_defaults(func=cmd_verify_hailo)

    cap = sub.add_parser("capture", help="Run capture session, save to disk")
    cap.add_argument("--shots", "-n", type=int, default=None, help="Override num_shots")
    cap.add_argument("--interval", "-i", type=float, default=None, help="Override shot_interval_sec")
    cap.add_argument("--output", "-o", type=str, default=None, help="Override output dir")
    cap.set_defaults(func=cmd_capture)

    up = sub.add_parser("capture-and-upload", help="Capture + upload bundle to backend")
    up.add_argument("--shots", "-n", type=int, default=None, help="Override num_shots")
    up.add_argument("--interval", "-i", type=float, default=None, help="Override shot_interval_sec")
    up.add_argument("--output", "-o", type=str, default=None, help="Override output dir")
    up.add_argument("--backend", "-b", type=str, default=None, help="Override backend URL")
    up.add_argument("--api-key", "-k", type=str, default=None, help="Backend API key")
    up.add_argument("--wait", "-w", action="store_true", help="Poll until reconstruction job completes")
    up.set_defaults(func=cmd_capture_and_upload)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not hasattr(args, "func"):
        parser.print_help()
        return 1
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
