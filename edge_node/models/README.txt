Place your compiled Hailo-8L `.hef` model binaries in this directory.

Expected filenames (see config/config.yaml):
  - yolov8n-seg.hef     — Background segmentation (YOLOv8-seg quantized for Hailo-8L)
  - dpt_lite.hef        — Monocular depth estimation (DPT-Lite / Depth-Anything quantized)

Obtaining pre-compiled .hef models:

  1. Clone the Hailo Model Zoo:
     git clone https://github.com/hailo-ai/hailo_model_zoo
     cd hailo_model_zoo

  2. Download the pre-compiled assets:
     python data\download_hefs.py yolov8n-seg
     python data\download_hefs.py dpt_lite

  3. Copy the resulting .hef files here.

Compiling custom models from .onnx / .pt:
  - Use the Hailo Dataflow Compiler (hailo-ai package):
      hailomz compile --model yolov8n-seg.onnx --hw-arch hailo8l
  - See: https://hailo.ai/developer-zone/documentation/hailort/
