"""Local OCR via RapidOCR, isolated behind one function.

The parser depends on ``ocr_image`` — a small abstraction — not on the vendor,
so the heavy engine loads lazily (once) and tests swap it with a fake.
"""

from functools import lru_cache
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PIL.Image import Image


@lru_cache(maxsize=1)
def _engine() -> object:
    from rapidocr_onnxruntime import RapidOCR

    return RapidOCR()


def ocr_image(image: "Image") -> str:
    """Return the text RapidOCR reads from ``image``; empty string if none."""
    import numpy as np

    result, _elapsed = _engine()(np.array(image.convert("RGB")))  # type: ignore[operator]
    if not result:
        return ""
    return "\n".join(line[1] for line in result)
