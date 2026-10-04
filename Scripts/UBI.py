#!/usr/bin/env python3
"""Fail-closed build checks for the XG-040G-MD all-in-UBI profile."""
import argparse
import hashlib
import json
import re
import shutil
import struct
import subprocess
from pathlib import Path

PROFILE = "nokia_xg-040g-md-ubi"
DEVICE = "nokia,xg-040g-md-ubi"
SYMBOL = f"CONFIG_TARGET_DEVICE_airoha_an7581_DEVICE_{PROFILE}"
PATCH = "600-mtd-spinand-add-skyhigh-robust-read-workaround.patch"
REPO = Path(__file__).resolve().parents[1]
CRITICAL = {
    "CONFIG_PACKAGE_luci", "CONFIG_PACKAGE_luci-app-openclash",
    "CONFIG_PACKAGE_luci-app-lucky", "CONFIG_PACKAGE_luci-app-airoha-npu",
    "CONFIG_PACKAGE_kmod-tun", "CONFIG_PACKAGE_fitblk",
    "CONFIG_PACKAGE_airoha-en7581-npu-firmware",
    "CONFIG_PACKAGE_airoha-en8811h-firmware",
}


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def config(path):
    result = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        match = re.fullmatch(r"(CONFIG_\S+)=(.*)", line)
        if match:
            result[match[1]] = match[2]
        else:
            match = re.fullmatch(r"# (CONFIG_\S+) is not set", line)
            if match:
                result[match[1]] = "n"
    return result


def install(source):
    target = source / "target/linux/airoha"
    require(re.search(r"^KERNEL_PATCHVER\s*:?=\s*6\.18\s*$",
                      (target / "Makefile").read_text(encoding="utf-8"), re.M),
            "SkyHigh patch is validated for kernel 6.18 only; review a new kernel before building")
    require(f"define Device/{PROFILE}" in (target / "image/an7581.mk").read_text(encoding="utf-8"),
            "Source does not provide the required UBI profile")
    patch_dir = target / "patches-6.18"
    require(patch_dir.is_dir(), "Missing target kernel patch directory")
    vendored = REPO / "Patches/airoha-6.18" / PATCH
    destination = patch_dir / PATCH
    for directory in (source / "target/linux/generic/backport-6.18",
                      source / "target/linux/generic/pending-6.18",
                      source / "target/linux/generic/hack-6.18", patch_dir):
        for existing in directory.glob("*.patch"):
            if existing == destination:
                continue
            text = existing.read_text(encoding="utf-8", errors="replace").lower()
            require("spinand_read_page_wait" not in text and "robust read" not in text,
                    f"Another Robust Read patch exists: {existing}; review instead of stacking fixes")
    if destination.exists():
        require(sha256(destination) == sha256(vendored), "Different SkyHigh patch already installed")
    else:
        shutil.copyfile(vendored, destination)
    print(f"Installed SkyHigh Robust Read patch: {sha256(vendored)}")


def check_config(source):
    resolved = config(source / ".config")
    for symbol in ("CONFIG_TARGET_airoha", "CONFIG_TARGET_airoha_an7581", SYMBOL,
                   "CONFIG_TARGET_ROOTFS_INITRAMFS", *sorted(CRITICAL - {"CONFIG_PACKAGE_fitblk"})):
        require(resolved.get(symbol) == "y", f"Required selection dropped by defconfig: {symbol}")
    # fitblk is HIDDEN: Kconfig ignores a user's '=y' and selects it via
    # MODULE_DEFAULT_fitblk for a per-device image. 'm' means build the package,
    # not a loadable kernel driver. Verify installation in the final manifest.
    fitblk = resolved.get("CONFIG_PACKAGE_fitblk")
    require(fitblk == "y" or (fitblk == "m"
            and resolved.get("CONFIG_TARGET_PER_DEVICE_ROOTFS") == "y"
            and resolved.get("CONFIG_MODULE_DEFAULT_fitblk") == "m"),
            "fitblk is not selected for the UBI device rootfs")
    selected = [key for key, value in resolved.items()
                if key.startswith("CONFIG_TARGET_DEVICE_") and value == "y"]
    require(selected == [SYMBOL], f"Unexpected devices selected: {selected}")
    requested = config(source / ".config.requested")
    changed = {key: {"requested": value, "resolved": resolved.get(key, "n")}
               for key, value in requested.items() if key.startswith("CONFIG_PACKAGE_")
               and value in ("y", "m") and resolved.get(key, "n") != value}
    (source / "package-selection-changes.json").write_text(
        json.dumps(changed, indent=2) + "\n")
    print(f"UBI profile and critical packages verified; {len(changed)} other package changes recorded")


def check_kernel(source):
    cores = list((source / "build_dir").glob("target-*/linux-airoha_an7581/linux-6.18*/drivers/mtd/nand/spi/core.c"))
    require(cores, "No prepared 6.18 kernel found")
    # Per-device initramfs may clone the kernel tree; check those copies too.
    kernels = []
    for core in sorted(cores):
        text = core.read_text(encoding="utf-8")
        require(text.count("spinand_read_page_wait(spinand, &status)") == 2,
                "Robust Read must be used in normal and continuous reads")
        require(text.count("if (spinand->id.data[0] == 0x01)") == 2,
                "SkyHigh manufacturer guards missing")
        require("static int spinand_read_page_wait" in text, "Robust Read helper missing")
        helper = text.split("static int spinand_read_page_wait", 1)[1].split("\n}\n", 1)[0]
        require(helper.count("spinand_read_status(spinand, &status)") == 2,
                "Double status read missing")
        require("msecs_to_jiffies(400)" in helper and "-ETIMEDOUT" in helper,
                "Read timeout missing")
        require(config(core.parents[4] / ".config").get("CONFIG_MTD_SPI_NAND") == "y",
                "SPI-NAND driver is not built into the boot kernel")
        kernels.append({"kernel_directory": core.parents[4].name,
                        "core_sha256": sha256(core)})
    report = {"kernels": kernels, "normal_and_continuous_reads": True,
              "mtd_spi_nand": "y", "patch_sha256": sha256(REPO / "Patches/airoha-6.18" / PATCH)}
    (source / "skyhigh-verification.json").write_text(json.dumps(report, indent=2) + "\n")
    print("Prepared boot kernel contains SkyHigh double-status-read workaround")


def check_fit(path):
    data = path.read_bytes()
    require(len(data) >= 40, f"Image is empty/truncated: {path}")
    magic, size = struct.unpack(">II", data[:8])
    require(magic == 0xD00DFEED and 40 <= size <= len(data), f"Invalid FIT image: {path}")


def package(source):
    check_config(source)
    check_kernel(source)
    target = source / "bin/targets/airoha/an7581"
    profiles_file = target / "profiles.json"
    profiles = json.loads(profiles_file.read_text(encoding="utf-8"))
    require(profiles.get("target") == "airoha/an7581", "Unexpected image target")
    profile = profiles["profiles"][PROFILE]
    require(DEVICE in profile.get("supported_devices", []), "UBI device identity missing from profiles.json")
    images = [entry for entry in profile["images"] if entry.get("type") == "sysupgrade"]
    require(len(images) == 1, "Expected exactly one UBI sysupgrade image")
    entry = images[0]
    name = entry["name"]
    require(Path(name).name == name and name.endswith(f"{PROFILE}-squashfs-sysupgrade.itb"),
            f"Unexpected upgrade image: {name}")
    image = target / name
    check_fit(image)
    require(sha256(image) == entry["sha256"], "sysupgrade SHA256 does not match profiles.json")
    output = source / "upload"
    output.mkdir(exist_ok=True)
    metadata_file = output / "sysupgrade-metadata.json"
    subprocess.run([str(source / "staging_dir/host/bin/fwtool"), "-i", str(metadata_file), str(image)], check=True)
    metadata = json.loads(metadata_file.read_text(encoding="utf-8"))
    require(DEVICE in metadata.get("supported_devices", []), "Firmware metadata is not for this UBI device")
    shutil.copy2(image, output / image.name)
    recovery = list(target.glob(f"*-{PROFILE}-initramfs-recovery.itb"))
    require(len(recovery) == 1, "Expected one UBI recovery image")
    check_fit(recovery[0])
    shutil.copy2(recovery[0], output / recovery[0].name)
    manifests = list(target.glob(f"*-{PROFILE}*.manifest"))
    require(len(manifests) == 1, "Expected one UBI installed-package manifest")
    installed = {line.split(" - ", 1)[0] for line in manifests[0].read_text(encoding="utf-8").splitlines()}
    required_packages = {key.removeprefix("CONFIG_PACKAGE_") for key in CRITICAL}
    require(required_packages <= installed,
            f"Critical packages missing from image: {sorted(required_packages - installed)}")
    shutil.copy2(manifests[0], output / manifests[0].name)
    for name in ("profiles.json", "config.buildinfo", "feeds.buildinfo", "version.buildinfo"):
        shutil.copy2(target / name, output / name)
    for name in ("package-selection-changes.json", "skyhigh-verification.json"):
        shutil.copy2(source / name, output / name)
    shutil.copy2(source / ".config", output / "build.config")
    provenance = {
        "source_commit": subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip(),
        "ci_commit": subprocess.check_output(["git", "-C", str(REPO), "rev-parse", "HEAD"], text=True).strip(),
        "profile": PROFILE, "sysupgrade": image.name, "recovery": recovery[0].name,
        "patch_sha256": sha256(REPO / "Patches/airoha-6.18" / PATCH),
    }
    (output / "build-provenance.json").write_text(json.dumps(provenance, indent=2) + "\n")
    (output / "SHA256SUMS").write_text("".join(
        f"{sha256(path)}  {path.name}\n" for path in sorted(output.iterdir())
        if path.is_file() and path.name != "SHA256SUMS"))
    print(f"Validated UBI release: {image.name}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("install", "config", "kernel", "package"))
    parser.add_argument("source", type=Path)
    args = parser.parse_args()
    {"install": install, "config": check_config, "kernel": check_kernel,
     "package": package}[args.operation](args.source.resolve())


if __name__ == "__main__":
    main()
