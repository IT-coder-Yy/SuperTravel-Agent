import os


# Unit and contract tests must never depend on an external image provider.
os.environ.setdefault("WIKIMEDIA_IMAGES_ENABLED", "false")
os.environ.setdefault("BAIDU_DIRECT_PLACE_FALLBACK_ENABLED", "false")
