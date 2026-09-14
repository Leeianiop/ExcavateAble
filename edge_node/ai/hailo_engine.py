"""
Hailo-8L AI Inference Engine
Wraps HailoRT runtime for running compiled .hef models on the Hailo-8L AI HAT.
Supports background segmentation (YOLOv8-seg) and monocular depth estimation (DPT-Lite).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np

from ..config import HailoConfig

logger = logging.getLogger(__name__)

try:
    from hailo_platform import (
        HEF,
        HailoStreamInterface,
        InferVStreams,
        ConfigureParams,
        InputVStreamParams,
        OutputVStreamParams,
        FormatType,
        HAILO,
    )
    _HAILO_AVAILABLE = True
except ImportError:
    try:
        import hailort as _hr  # noqa: F401
        _HAILO_AVAILABLE = True
    except ImportError:
        _HAILO_AVAILABLE = False
        logger.warning(
            "Hailo SDK not installed. Running AI in mock/simulation mode. "
            "Install from: https://github.com/hailo-ai/hailort"
        )


@dataclass
class InferenceStats:
    preprocess_ms: float = 0.0
    infer_ms: float = 0.0
    postprocess_ms: float = 0.0
    total_ms: float = 0.0

    def __post_init__(self) -> None:
        if self.total_ms == 0.0:
            self.total_ms = self.preprocess_ms + self.infer_ms + self.postprocess_ms


@dataclass
class SegmentationResult:
    mask: np.ndarray
    class_ids: np.ndarray
    scores: np.ndarray
    boxes_xyxy: np.ndarray
    stats: InferenceStats

    @property
    def foreground_mask(self) -> np.ndarray:
        if self.mask.ndim == 3:
            return self.mask.any(axis=0).astype(np.uint8)
        return self.mask.astype(np.uint8)


@dataclass
class DepthResult:
    depth_map: np.ndarray
    stats: InferenceStats

    def normalized(self) -> np.ndarray:
        d = self.depth_map.astype(np.float32)
        if d.max() - d.min() < 1e-6:
            return np.zeros_like(d)
        return (d - d.min()) / (d.max() - d.min())


class HailoInferenceEngine:
    def __init__(self, config: HailoConfig):
        self.config = config
        self._seg_device = None
        self._depth_device = None
        self._seg_hef = None
        self._depth_hef = None
        self._seg_infer = None
        self._depth_infer = None
        self._initialized = False
        self._initialized_models: set[str] = set()

    @property
    def initialized(self) -> bool:
        return self._initialized

    @property
    def hailo_available(self) -> bool:
        return _HAILO_AVAILABLE

    def initialize(self) -> None:
        if self._initialized:
            return

        if not _HAILO_AVAILABLE:
            logger.info("Hailo SDK unavailable — using mock AI inference mode")
            self._initialized = True
            return

        try:
            self._load_segmentation_model()
            self._load_depth_model()
            self._initialized = True
            logger.info(
                f"Hailo inference engine initialized: device_id={self.config.device_id} "
                f"models={sorted(self._initialized_models)}"
            )
        except Exception as exc:
            logger.error(f"Hailo init failed, falling back to mock mode: {exc}")
            self._cleanup()
            self._initialized = True

    def close(self) -> None:
        self._cleanup()
        self._initialized = False
        logger.info("Hailo inference engine closed")

    def run_segmentation(self, rgb_image: np.ndarray) -> SegmentationResult:
        if not self._initialized:
            raise RuntimeError("Engine not initialized; call initialize() first")

        t_start = time.perf_counter()
        if not _HAILO_AVAILABLE or "segmentation" not in self._initialized_models:
            return self._mock_segmentation(rgb_image, t_start)

        t0 = time.perf_counter()
        input_tensor = self._preprocess_seg(rgb_image)
        t_pre = (time.perf_counter() - t0) * 1000

        t1 = time.perf_counter()
        raw_output = self._infer_segmentation(input_tensor)
        t_infer = (time.perf_counter() - t1) * 1000

        t2 = time.perf_counter()
        mask, class_ids, scores, boxes = self._postprocess_seg(raw_output, rgb_image.shape[:2])
        t_post = (time.perf_counter() - t2) * 1000

        stats = InferenceStats(
            preprocess_ms=t_pre,
            infer_ms=t_infer,
            postprocess_ms=t_post,
            total_ms=(time.perf_counter() - t_start) * 1000,
        )
        logger.debug(
            f"Segmentation infer: {stats.infer_ms:.1f}ms (total {stats.total_ms:.1f}ms)"
        )
        return SegmentationResult(
            mask=mask,
            class_ids=class_ids,
            scores=scores,
            boxes_xyxy=boxes,
            stats=stats,
        )

    def run_depth(self, rgb_image: np.ndarray) -> DepthResult:
        if not self._initialized:
            raise RuntimeError("Engine not initialized; call initialize() first")

        t_start = time.perf_counter()
        if not _HAILO_AVAILABLE or "depth" not in self._initialized_models:
            return self._mock_depth(rgb_image, t_start)

        t0 = time.perf_counter()
        input_tensor = self._preprocess_depth(rgb_image)
        t_pre = (time.perf_counter() - t0) * 1000

        t1 = time.perf_counter()
        raw_output = self._infer_depth(input_tensor)
        t_infer = (time.perf_counter() - t1) * 1000

        t2 = time.perf_counter()
        depth_map = self._postprocess_depth(raw_output, rgb_image.shape[:2])
        t_post = (time.perf_counter() - t2) * 1000

        stats = InferenceStats(
            preprocess_ms=t_pre,
            infer_ms=t_infer,
            postprocess_ms=t_post,
            total_ms=(time.perf_counter() - t_start) * 1000,
        )
        logger.debug(
            f"Depth infer: {stats.infer_ms:.1f}ms (total {stats.total_ms:.1f}ms)"
        )
        return DepthResult(depth_map=depth_map, stats=stats)

    def run_fused_pipeline(
        self, rgb_image: np.ndarray
    ) -> tuple[SegmentationResult, DepthResult]:
        seg = self.run_segmentation(rgb_image)
        depth = self.run_depth(rgb_image)
        return seg, depth

    # ------------------------------------------------------------------
    # Segmentation model management
    # ------------------------------------------------------------------
    def _load_segmentation_model(self) -> None:
        hef_path = Path(self.config.segmentation_model_path)
        if not hef_path.exists():
            logger.warning(
                f"Segmentation HEF not found at {hef_path}; seg will run in mock mode"
            )
            return

        hef = HEF(str(hef_path))
        configure_params = ConfigureParams.create_from_hef(
            hef, interface=HailoStreamInterface.PCIe
        )
        device = HAILO.create_device(device_id=self.config.device_id)
        device.configure(configure_params)

        input_vstreams_params = InputVStreamParams.make_from_hef(
            hef, device, quantized=False, format_type=FormatType.FLOAT32
        )
        output_vstreams_params = OutputVStreamParams.make_from_hef(
            hef, device, quantized=False, format_type=FormatType.FLOAT32
        )
        infer = InferVStreams(device, input_vstreams_params, output_vstreams_params)

        self._seg_hef = hef
        self._seg_device = device
        self._seg_infer = infer
        self._initialized_models.add("segmentation")
        logger.info(f"Segmentation model loaded: {hef_path.name}")

    def _preprocess_seg(self, rgb: np.ndarray) -> np.ndarray:
        h, w = self.config.input_height, self.config.input_width
        img_h, img_w = rgb.shape[:2]

        scale = min(w / img_w, h / img_h)
        new_w, new_h = int(img_w * scale), int(img_h * scale)
        pad_w, pad_h = (w - new_w) // 2, (h - new_h) // 2

        try:
            import cv2
            resized = cv2.resize(rgb, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
        except ImportError:
            resized = self._numpy_resize(rgb, new_h, new_w)

        padded = np.zeros((h, w, 3), dtype=np.uint8)
        padded[pad_h:pad_h + new_h, pad_w:pad_w + new_w] = resized

        tensor = padded.astype(np.float32) / 255.0
        return np.transpose(tensor, (2, 0, 1))[np.newaxis, ...]

    def _infer_segmentation(self, tensor: np.ndarray) -> dict:
        input_name = self._seg_hef.get_input_vstream_infos()[0].name
        with self._seg_infer as infer:
            return infer.infer({input_name: tensor})

    def _postprocess_seg(
        self, raw: dict, original_shape: tuple[int, int]
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        outputs = list(raw.values())
        if len(outputs) < 3:
            return (
                np.zeros(original_shape, dtype=np.uint8),
                np.array([], dtype=np.int32),
                np.array([], dtype=np.float32),
                np.zeros((0, 4), dtype=np.float32),
            )

        boxes, scores, class_ids, masks = self._yolov8_seg_decode(
            outputs,
            conf_threshold=self.config.threshold,
            nms_threshold=self.config.nms_threshold,
        )

        if len(masks) == 0:
            seg_mask = np.zeros(original_shape, dtype=np.uint8)
        else:
            try:
                import cv2
                full_masks = []
                for m in masks:
                    m_rs = cv2.resize(m, (original_shape[1], original_shape[0]))
                    full_masks.append((m_rs > 0.5).astype(np.uint8))
                seg_mask = (np.stack(full_masks, axis=0).any(axis=0)).astype(np.uint8)
            except ImportError:
                seg_mask = np.ones(original_shape, dtype=np.uint8)

        return seg_mask, class_ids, scores, boxes

    # ------------------------------------------------------------------
    # Depth model management
    # ------------------------------------------------------------------
    def _load_depth_model(self) -> None:
        hef_path = Path(self.config.depth_model_path)
        if not hef_path.exists():
            logger.warning(
                f"Depth HEF not found at {hef_path}; depth will run in mock mode"
            )
            return

        hef = HEF(str(hef_path))
        configure_params = ConfigureParams.create_from_hef(
            hef, interface=HailoStreamInterface.PCIe
        )
        device = HAILO.create_device(device_id=self.config.device_id)
        device.configure(configure_params)

        input_vstreams_params = InputVStreamParams.make_from_hef(
            hef, device, quantized=False, format_type=FormatType.FLOAT32
        )
        output_vstreams_params = OutputVStreamParams.make_from_hef(
            hef, device, quantized=False, format_type=FormatType.FLOAT32
        )
        infer = InferVStreams(device, input_vstreams_params, output_vstreams_params)

        self._depth_hef = hef
        self._depth_device = device
        self._depth_infer = infer
        self._initialized_models.add("depth")
        logger.info(f"Depth model loaded: {hef_path.name}")

    def _preprocess_depth(self, rgb: np.ndarray) -> np.ndarray:
        h, w = self.config.depth_input_height, self.config.depth_input_width
        try:
            import cv2
            resized = cv2.resize(rgb, (w, h), interpolation=cv2.INTER_CUBIC)
        except ImportError:
            resized = self._numpy_resize(rgb, h, w)
        tensor = resized.astype(np.float32) / 255.0
        mean = np.array([0.485, 0.456, 0.406], dtype=np.float32)
        std = np.array([0.229, 0.224, 0.225], dtype=np.float32)
        tensor = (tensor - mean) / std
        return np.transpose(tensor, (2, 0, 1))[np.newaxis, ...]

    def _infer_depth(self, tensor: np.ndarray) -> dict:
        input_name = self._depth_hef.get_input_vstream_infos()[0].name
        with self._depth_infer as infer:
            return infer.infer({input_name: tensor})

    def _postprocess_depth(self, raw: dict, original_shape: tuple[int, int]) -> np.ndarray:
        output_tensors = list(raw.values())
        if not output_tensors:
            return np.zeros(original_shape, dtype=np.float32)
        out = output_tensors[0]
        if out.ndim == 4:
            out = out[0, 0]
        elif out.ndim == 3:
            out = out[0]

        try:
            import cv2
            depth = cv2.resize(
                out.astype(np.float32),
                (original_shape[1], original_shape[0]),
                interpolation=cv2.INTER_LINEAR,
            )
        except ImportError:
            depth = self._numpy_resize(out, original_shape[0], original_shape[1]).astype(np.float32)

        if depth.min() < depth.max():
            depth = (depth - depth.min()) / (depth.max() - depth.min())
        return depth

    # ------------------------------------------------------------------
    # Mock fallbacks (when Hailo SDK unavailable or HEF missing)
    # ------------------------------------------------------------------
    def _mock_segmentation(
        self, rgb: np.ndarray, t_start: float
    ) -> SegmentationResult:
        time.sleep(0.02)
        h, w = rgb.shape[:2]
        rng = np.random.default_rng(h * w % (2**16))
        yy, xx = np.ogrid[:h, :w]
        cx, cy = w * 0.5 + rng.uniform(-w * 0.1, w * 0.1), h * 0.5 + rng.uniform(-h * 0.1, h * 0.1)
        rx, ry = w * 0.35, h * 0.35
        ellipse = ((xx - cx) ** 2) / rx**2 + ((yy - cy) ** 2) / ry**2
        mask = (ellipse <= 1.0).astype(np.uint8)

        elapsed = (time.perf_counter() - t_start) * 1000
        stats = InferenceStats(
            preprocess_ms=elapsed * 0.15,
            infer_ms=elapsed * 0.6,
            postprocess_ms=elapsed * 0.25,
            total_ms=elapsed,
        )
        return SegmentationResult(
            mask=mask,
            class_ids=np.array([0], dtype=np.int32),
            scores=np.array([0.92], dtype=np.float32),
            boxes_xyxy=np.array([[max(0, cx - rx), max(0, cy - ry), min(w, cx + rx), min(h, cy + ry)]], dtype=np.float32),
            stats=stats,
        )

    def _mock_depth(self, rgb: np.ndarray, t_start: float) -> DepthResult:
        time.sleep(0.015)
        h, w = rgb.shape[:2]
        yy, xx = np.ogrid[:h, :w]
        cx, cy = w / 2, h / 2
        depth = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)
        depth = depth.max() - depth
        depth = depth / depth.max() if depth.max() > 0 else depth

        elapsed = (time.perf_counter() - t_start) * 1000
        stats = InferenceStats(
            preprocess_ms=elapsed * 0.15,
            infer_ms=elapsed * 0.6,
            postprocess_ms=elapsed * 0.25,
            total_ms=elapsed,
        )
        return DepthResult(depth_map=depth.astype(np.float32), stats=stats)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _cleanup(self) -> None:
        for attr in ("_seg_infer", "_depth_infer"):
            obj = getattr(self, attr, None)
            if obj is not None:
                try:
                    obj.close()
                except Exception:
                    pass
                setattr(self, attr, None)
        for attr in ("_seg_device", "_depth_device"):
            obj = getattr(self, attr, None)
            if obj is not None:
                try:
                    obj.close()
                except Exception:
                    pass
                setattr(self, attr, None)
        self._initialized_models.clear()

    @staticmethod
    def _numpy_resize(arr: np.ndarray, new_h: int, new_w: int) -> np.ndarray:
        old_h, old_w = arr.shape[:2]
        row_idx = (np.linspace(0, old_h - 1, new_h)).astype(np.int64)
        col_idx = (np.linspace(0, old_w - 1, new_w)).astype(np.int64)
        if arr.ndim == 3:
            return arr[row_idx[:, None], col_idx[None, :], :]
        return arr[row_idx[:, None], col_idx[None, :]]

    @staticmethod
    def _yolov8_seg_decode(
        outputs: list[np.ndarray],
        conf_threshold: float,
        nms_threshold: float,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray, list[np.ndarray]]:
        if len(outputs) < 3:
            return np.zeros((0, 4)), np.array([]), np.array([]), []
        boxes_out, scores_out, masks_out = outputs[0], outputs[1], outputs[2]

        boxes = boxes_out[0] if boxes_out.ndim == 3 else boxes_out
        scores = scores_out[0] if scores_out.ndim == 3 else scores_out
        masks = masks_out[0] if masks_out.ndim == 4 else masks_out

        if scores.ndim == 2:
            class_ids = scores.argmax(axis=1)
            confidences = scores.max(axis=1)
        else:
            class_ids = np.zeros(len(boxes), dtype=np.int32)
            confidences = scores.flatten()

        keep = confidences >= conf_threshold
        boxes = boxes[keep]
        confidences = confidences[keep]
        class_ids = class_ids[keep]
        masks = masks[keep] if len(masks) else []

        nms_keep = HailoInferenceEngine._nms(boxes, confidences, nms_threshold)
        final_boxes = boxes[nms_keep]
        final_scores = confidences[nms_keep]
        final_class_ids = class_ids[nms_keep]
        final_masks = [masks[i] for i in nms_keep] if len(masks) else []

        return final_boxes, final_scores, final_class_ids, final_masks

    @staticmethod
    def _nms(boxes: np.ndarray, scores: np.ndarray, iou_threshold: float) -> list[int]:
        if len(boxes) == 0:
            return []
        x1 = boxes[:, 0]
        y1 = boxes[:, 1]
        x2 = boxes[:, 2]
        y2 = boxes[:, 3]
        areas = (x2 - x1 + 1e-6) * (y2 - y1 + 1e-6)
        order = scores.argsort()[::-1]
        keep: list[int] = []
        while len(order) > 0:
            i = order[0]
            keep.append(int(i))
            xx1 = np.maximum(x1[i], x1[order[1:]])
            yy1 = np.maximum(y1[i], y1[order[1:]])
            xx2 = np.minimum(x2[i], x2[order[1:]])
            yy2 = np.minimum(y2[i], y2[order[1:]])
            w = np.maximum(0.0, xx2 - xx1 + 1e-6)
            h = np.maximum(0.0, yy2 - yy1 + 1e-6)
            inter = w * h
            iou = inter / (areas[i] + areas[order[1:]] - inter)
            inds = np.where(iou <= iou_threshold)[0]
            order = order[inds + 1]
        return keep

    def __enter__(self) -> "HailoInferenceEngine":
        self.initialize()
        return self

    def __exit__(self, *exc) -> None:
        self.close()
