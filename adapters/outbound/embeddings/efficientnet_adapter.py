"""Extractor de embeddings de imagen con EfficientNet-B0 (1280 dims)."""
from __future__ import annotations
import os
import structlog
import torch
import torchvision.transforms as T
from PIL import Image

log = structlog.get_logger(__name__)

_TRANSFORM = T.Compose([
    T.Resize((224, 224)),
    T.ToTensor(),
    T.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
])


def _load_efficientnet():
    try:
        import timm
        model = timm.create_model("efficientnet_b0", pretrained=True, num_classes=0)
        model.eval()
        log.info("efficientnet_loaded")
        return model
    except Exception as e:
        log.warning("efficientnet_load_failed", error=str(e))
        return None


_MODEL = _load_efficientnet()


def extract_image_embedding(image_path: str) -> list[float] | None:
    """Extrae un vector de 1280 dims con EfficientNet-B0. Retorna None si falla."""
    if _MODEL is None or not os.path.exists(image_path):
        return None
    try:
        img = Image.open(image_path).convert("RGB")
        tensor = _TRANSFORM(img).unsqueeze(0)
        with torch.no_grad():
            features = _MODEL(tensor)
        return features.squeeze().numpy().tolist()
    except Exception as e:
        log.error("embedding_extraction_error", path=image_path, error=str(e))
        return None
