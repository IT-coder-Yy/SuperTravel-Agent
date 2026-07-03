from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
POWERPAINT_ROOT = PROJECT_ROOT / "PowerPaint"


REQUIRED_PATHS = [
    (
        "PowerPaint-v1 UNet",
        POWERPAINT_ROOT / "checkpoints" / "ppt-v1" / "unet" / "unet.safetensors",
        "https://huggingface.co/JunhaoZhuang/PowerPaint-v1/tree/main",
    ),
    (
        "PowerPaint-v1 text encoder",
        POWERPAINT_ROOT / "checkpoints" / "ppt-v1" / "text_encoder" / "text_encoder.safetensors",
        "https://huggingface.co/JunhaoZhuang/PowerPaint-v1/tree/main",
    ),
    (
        "Stable Diffusion Inpainting",
        POWERPAINT_ROOT
        / "checkpoints"
        / "hf"
        / "stable-diffusion-v1-5"
        / "stable-diffusion-inpainting"
        / "model_index.json",
        "https://huggingface.co/stable-diffusion-v1-5/stable-diffusion-inpainting/tree/main",
    ),
    (
        "Stable Diffusion v1.5 tokenizer",
        POWERPAINT_ROOT
        / "checkpoints"
        / "hf"
        / "stable-diffusion-v1-5"
        / "stable-diffusion-v1-5"
        / "tokenizer"
        / "tokenizer_config.json",
        "https://huggingface.co/stable-diffusion-v1-5/stable-diffusion-v1-5/tree/main/tokenizer",
    ),
]


OPTIONAL_PATHS = [
    (
        "ControlNet canny",
        POWERPAINT_ROOT / "checkpoints" / "hf" / "lllyasviel" / "sd-controlnet-canny" / "config.json",
        "https://huggingface.co/lllyasviel/sd-controlnet-canny/tree/main",
    ),
    (
        "ControlNet openpose",
        POWERPAINT_ROOT / "checkpoints" / "hf" / "lllyasviel" / "sd-controlnet-openpose" / "config.json",
        "https://huggingface.co/lllyasviel/sd-controlnet-openpose/tree/main",
    ),
    (
        "ControlNet depth",
        POWERPAINT_ROOT / "checkpoints" / "hf" / "lllyasviel" / "sd-controlnet-depth" / "config.json",
        "https://huggingface.co/lllyasviel/sd-controlnet-depth/tree/main",
    ),
    (
        "ControlNet hed",
        POWERPAINT_ROOT / "checkpoints" / "hf" / "lllyasviel" / "sd-controlnet-hed" / "config.json",
        "https://huggingface.co/lllyasviel/sd-controlnet-hed/tree/main",
    ),
    (
        "ControlNet annotators",
        POWERPAINT_ROOT / "checkpoints" / "hf" / "lllyasviel" / "ControlNet" / "annotator" / "ckpts",
        "https://huggingface.co/lllyasviel/ControlNet/tree/main/annotator/ckpts",
    ),
    (
        "DPT hybrid MiDaS",
        POWERPAINT_ROOT / "checkpoints" / "hf" / "Intel" / "dpt-hybrid-midas" / "config.json",
        "https://huggingface.co/Intel/dpt-hybrid-midas/tree/main",
    ),
]


def exists(path: Path) -> bool:
    return path.exists()


def print_group(title: str, items: list[tuple[str, Path, str]]) -> bool:
    print(title)
    ok = True
    for name, path, url in items:
        found = exists(path)
        ok = ok and found
        status = "OK" if found else "MISSING"
        print(f"[{status}] {name}")
        print(f"       path: {path}")
        if not found:
            print(f"       url:  {url}")
    print()
    return ok


def main() -> int:
    required_ok = print_group("Required models", REQUIRED_PATHS)
    print_group("Optional ControlNet models", OPTIONAL_PATHS)
    if required_ok:
        print("Required PowerPaint models are ready.")
        return 0
    print("Required PowerPaint models are incomplete.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
