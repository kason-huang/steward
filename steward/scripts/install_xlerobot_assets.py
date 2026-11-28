"""Install the pinned MuJoCo-GS-Web XLeRobot model and its referenced mesh files."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.request import urlopen
from xml.etree import ElementTree as ET

from steward.assets.xlerobot import DEFAULT_MODEL, MODEL_REVISION

SOURCE = f"https://raw.githubusercontent.com/Vector-Wangel/MuJoCo-GS-Web/{MODEL_REVISION}"
ROBOT_ASSETS = f"{SOURCE}/assets/robots/xlerobot"


def _fetch(url: str, destination: Path) -> None:
    with urlopen(url, timeout=30) as response:
        destination.write_bytes(response.read())


def main() -> None:
    """Download into a staging directory and publish only a complete model."""
    target = DEFAULT_MODEL.parent
    if not DEFAULT_MODEL.is_file():
        target.parent.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(prefix="xlerobot-", dir=target.parent) as scratch:
            stage = Path(scratch) / target.name
            mesh_dir = stage / "assets"
            mesh_dir.mkdir(parents=True)
            model = stage / DEFAULT_MODEL.name
            _fetch(f"{ROBOT_ASSETS}/{model.name}", model)
            meshes = {node.attrib["file"] for node in ET.parse(model).findall("./asset/mesh")}
            if not meshes or any(Path(mesh).name != mesh for mesh in meshes):
                raise ValueError("expected the pinned XLeRobot model to reference local mesh filenames")
            with ThreadPoolExecutor(max_workers=8) as pool:
                list(pool.map(lambda mesh: _fetch(f"{ROBOT_ASSETS}/assets/{mesh}", mesh_dir / mesh), sorted(meshes)))
            _fetch(f"{SOURCE}/LICENSE", stage / "LICENSE")
            try:
                stage.rename(target)
            except OSError:
                if not DEFAULT_MODEL.is_file():
                    raise
    print(DEFAULT_MODEL)


if __name__ == "__main__":
    main()
