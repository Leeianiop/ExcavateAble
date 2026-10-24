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

# COCO 80-class names (YOLOv8n-seg is COCO-pretrained).
_COCO_CLASS_NAMES = {
    -1: "artifact",
    0: "person", 1: "bicycle", 2: "car", 3: "motorcycle", 4: "airplane",
    5: "bus", 6: "train", 7: "truck", 8: "boat", 9: "traffic light",
    10: "fire hydrant", 11: "stop sign", 12: "parking meter", 13: "bench",
    14: "bird", 15: "cat", 16: "dog", 17: "horse", 18: "sheep", 19: "cow",
    20: "elephant", 21: "bear", 22: "zebra", 23: "giraffe", 24: "backpack",
    25: "umbrella", 26: "handbag", 27: "tie", 28: "suitcase", 29: "frisbee",
    30: "skis", 31: "snowboard", 32: "sports ball", 33: "kite",
    34: "baseball bat", 35: "baseball glove", 36: "skateboard", 37: "surfboard",
    38: "tennis racket", 39: "bottle", 40: "wine glass", 41: "cup",
    42: "fork", 43: "knife", 44: "spoon", 45: "bowl", 46: "banana",
    47: "apple", 48: "sandwich", 49: "orange", 50: "broccoli", 51: "carrot",
    52: "hot dog", 53: "pizza", 54: "donut", 55: "cake", 56: "chair",
    57: "couch", 58: "potted plant", 59: "bed", 60: "dining table",
    61: "toilet", 62: "tv", 63: "laptop", 64: "mouse", 65: "remote",
    66: "keyboard", 67: "cell phone", 68: "microwave", 69: "oven",
    70: "toaster", 71: "sink", 72: "refrigerator", 73: "book", 74: "clock",
    75: "vase", 76: "scissors", 77: "teddy bear", 78: "hair drier",
    79: "toothbrush",
}


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
        return getattr(cam, "using_hardware", False)
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

    _print_job_banner(result.job_id, result.uploaded_bytes, result.attempt_count, result.elapsed_sec, result.asset_url)

    if args.wait and result.job_id:
        print("\nWaiting for backend reconstruction (polling job status)...")
        with BackendUploadClient(config.upload) as client:
            final = client.poll_job_status(result.job_id, timeout_sec=600)
        final_status = final.get("status") if isinstance(final, dict) else getattr(final, "status", None)
        print(f"Final status: {final_status}")
        if final_status == "completed":
            _print_job_banner(result.job_id, result.uploaded_bytes, result.attempt_count, result.elapsed_sec, result.asset_url, final=final)
    return 0


def _print_job_banner(job_id: str, uploaded_bytes: int, attempt_count: int, elapsed_sec: float, asset_url: str | None, final=None) -> None:
    def _get(obj, key, default=None):
        if isinstance(obj, dict):
            return obj.get(key, default)
        return getattr(obj, key, default)

    bar = "=" * 72
    print(f"\n{bar}")
    print("  UPLOAD COMPLETE")
    print(f"{bar}")
    print(f"  job_id        : {job_id}")
    print(f"  uploaded_MB   : {uploaded_bytes / (1024*1024):.2f}")
    print(f"  attempts      : {attempt_count}")
    print(f"  elapsed       : {elapsed_sec:.1f}s")
    if final is not None:
        print(f"  final status  : {_get(final, 'status', '?')}")
        ply = _get(final, "ply_url")
        glb = _get(final, "glb_url")
        verts = _get(final, "ply_vertices")
        faces = _get(final, "ply_faces")
        if verts is None:
            verts = _get(final, "num_vertices")
        if faces is None:
            faces = _get(final, "num_faces")
        if verts is not None:
            print(f"  PLY vertices  : {verts:,}")
        if faces is not None:
            print(f"  PLY faces     : {faces:,}")
        if ply:
            print(f"  PLY URL       : {ply}")
        if glb:
            print(f"  GLB URL       : {glb}")
    if asset_url:
        print(f"  viewer URL    : {asset_url}")
    print(f"{bar}")
    print(f"  Paste the job_id above into the Dashboard Job Lookup:")
    print(f"  http://localhost:5173")
    print(f"{bar}\n")


def _encode_jpeg(rgb: "np.ndarray", quality: int = 80, max_width: int = 1280) -> tuple[bytes, int, int]:
    from PIL import Image
    import io
    h, w = rgb.shape[:2]
    if w > max_width:
        scale = max_width / w
        new_w, new_h = max_width, int(h * scale)
        img = Image.fromarray(rgb).resize((new_w, new_h), Image.LANCZOS)
    else:
        new_w, new_h = w, h
        img = Image.fromarray(rgb)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    return buf.getvalue(), new_w, new_h


def cmd_live_stream(args: argparse.Namespace) -> int:
    """Continuously capture + AI-infer + push live frames to the backend (digital twin)."""
    import numpy as np

    config = _load_config(args.config)
    if args.interval is not None:
        config.capture.live_stream_interval_sec = float(args.interval)
    if args.backend:
        config.upload.backend_url = args.backend

    # Live stream uses a low-power resolution (default 1280x720) instead of
    # the full still resolution. Capturing at e.g. 4056x3040 (12MP) is far
    # larger than the 640x640 model input and makes every frame slow on the
    # Pi. --resolution overrides the config value.
    res = getattr(args, "resolution", None)
    if res:
        try:
            rw, rh = (int(v) for v in res.lower().split("x"))
            config.capture.width = rw
            config.capture.height = rh
        except (ValueError, AttributeError):
            print(f"[warn] invalid --resolution '{res}', using config value")
    else:
        config.capture.width = config.capture.live_stream_width
        config.capture.height = config.capture.live_stream_height
    # RAW frames are never used by the live preview and cost extra ISP/bandwidth.
    config.capture.save_raw = config.capture.live_stream_save_raw

    from .capture import CameraCapture
    from .ai import HailoInferenceEngine
    from .pipeline import BackendUploadClient

    interval = max(0.05, config.capture.live_stream_interval_sec)
    backend_url = config.upload.backend_url.rstrip("/")

    print(f"\n=== Live Stream (digital twin) ===")
    print(f"  backend    : {backend_url}")
    print(f"  interval   : {interval:.3f}s  (target {1.0 / interval:.0f} fps)")
    print(f"  resolution : {config.capture.width}x{config.capture.height}")
    print(f"  (Ctrl+C to stop)\n")

    with CameraCapture(config.capture) as cam, \
         HailoInferenceEngine(config.hailo) as ai, \
         BackendUploadClient(config.upload) as client:

        if not client.http_available or client._session is None:
            print("ERROR: httpx not available — cannot stream to backend.")
            return 3

        if not cam.using_hardware:
            print("[warn] Camera hardware unavailable - streaming mock frames instead.")
            print("[warn] Check Picamera2 install, camera ribbon seating, and libcamera access.\n")

        frame_count = 0
        shot_index = 0
        try:
            while True:
                t_frame = time.perf_counter()

                t0 = time.perf_counter()
                shot = cam.capture_single(shot_index)
                shot_index += 1
                rgb = shot.rgb_array
                t_cap = (time.perf_counter() - t0) * 1000
                if rgb is None:
                    print("[skip] capture returned None")
                    time.sleep(interval)
                    continue

                t1 = time.perf_counter()
                seg, depth = ai.run_fused_pipeline(rgb)
                t_infer = (time.perf_counter() - t1) * 1000

                detections = []
                boxes = seg.boxes_xyxy
                scores = seg.scores
                class_ids = seg.class_ids
                h, w = rgb.shape[:2]
                class_names = _COCO_CLASS_NAMES
                if boxes is not None and len(boxes) > 0:
                    for i, box in enumerate(boxes):
                        x1, y1, x2, y2 = float(box[0]), float(box[1]), float(box[2]), float(box[3])
                        bw = (x2 - x1) / w
                        bh = (y2 - y1) / h
                        nx = x1 / w
                        ny = y1 / h
                        cls_id = int(class_ids[i]) if class_ids is not None and i < len(class_ids) else 0
                        conf = float(scores[i]) if scores is not None and i < len(scores) else 0.8
                        detections.append({
                            "label": class_names.get(cls_id, f"class_{cls_id}"),
                            "confidence": round(conf, 3),
                            "bbox": [round(nx, 4), round(ny, 4), round(bw, 4), round(bh, 4)],
                        })

                depth_map = depth.depth_map
                d_min = float(depth_map.min()) if depth_map is not None else 0.0
                d_max = float(depth_map.max()) if depth_map is not None else 0.0
                d_mean = float(depth_map.mean()) if depth_map is not None else 0.0

                t2 = time.perf_counter()
                jpeg_bytes, fw, fh = _encode_jpeg(rgb)
                t_encode = (time.perf_counter() - t2) * 1000

                metadata = {
                    "width": fw,
                    "height": fh,
                    "detections": detections,
                    "depth_min": round(d_min, 4),
                    "depth_max": round(d_max, 4),
                    "depth_mean": round(d_mean, 4),
                }

                t3 = time.perf_counter()
                try:
                    resp = client._session.post(
                        f"{backend_url}/api/v1/live/frame",
                        files={"image": ("frame.jpg", jpeg_bytes, "image/jpeg")},
                        data={"metadata": _json_dumps(metadata)},
                        timeout=5.0,
                    )
                    t_upload = (time.perf_counter() - t3) * 1000
                    if resp.status_code != 200:
                        print(f"[warn] POST live frame: HTTP {resp.status_code}")
                    else:
                        frame_count += 1
                        elapsed = (time.perf_counter() - t_frame) * 1000
                        det_str = f"{len(detections)} det" if detections else "no det"
                        print(
                            f"  frame #{frame_count}  {w}x{h}  {det_str}  "
                            f"[{t_cap:.0f}cap {t_infer:.0f}inf {t_encode:.0f}enc {t_upload:.0f}up]  "
                            f"{elapsed:.0f}ms total"
                        )
                except Exception as exc:
                    print(f"[warn] upload failed: {exc}")

                sleep_time = interval - (time.perf_counter() - t_frame)
                if sleep_time > 0:
                    time.sleep(sleep_time)

        except KeyboardInterrupt:
            print(f"\n\nLive stream stopped after {frame_count} frames.")

    return 0


def _json_dumps(obj) -> str:
    import json
    return json.dumps(obj)


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

    ls = sub.add_parser("live-stream", help="Live stream: capture+AI-infer+push frames continuously (digital twin)")
    ls.add_argument("--interval", "-i", type=float, default=None,
                    help="Seconds between frames (default: config live_stream_interval_sec, 0.05 = 20 fps)")
    ls.add_argument("--backend", "-b", type=str, default=None, help="Override backend URL")
    ls.add_argument("--resolution", "-r", type=str, default=None,
                     help="Capture resolution WxH for live stream (default: config live_stream_width x live_stream_height, 1280x720)")
    ls.set_defaults(func=cmd_live_stream)

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
