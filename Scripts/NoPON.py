#!/usr/bin/env python3
"""Prepare and verify the Ethernet-only UBI variant of the pinned TCBOOT source."""
import argparse
import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path

SOURCE_COMMIT = "f0de31b737890e456c144fa08de34451b40036d8"
POLICY = "nopon-verification.json"
DTS = "target/linux/airoha/dts/an7581-nokia_xg-040g-md-ubi.dts"
NETWORK = "target/linux/airoha/an7581/base-files/etc/board.d/02_network"
PON_DATA = "target/linux/airoha/base-files/etc/board.d/03_pon_data"
DISABLED = {
    "xpon_mac": "/soc/ethernet@1fb64000",
    "pon_pcs": "/soc/pcs@1fa08000",
    "gdm2": "/soc/ethernet@1fb50000/ethernet@2",
}
PON_PACKAGE = re.compile(
    r"^(?:airoha-pon|luci-(?:app|proto)-pon(?:-|$)|"
    r"kmod-airoha-(?:xpon|pon|en7572|paged-bosa))"
)


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare(source):
    commit = subprocess.check_output(
        ["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
    require(commit == SOURCE_COMMIT, "No-PON build must use the specified TCBOOT source commit")
    if (source / POLICY).exists():
        check_source(source)
        return
    dts = source / DTS
    text = dts.read_text()
    require('"an7581-nokia_xg-040g-md-common.dtsi"' in text
            and '"an758x-nokia_xg-040g-ubi-parts.dtsi"' in text
            and "nwrt" not in text, "No-PON build requires the mainline all-in-UBI DTS")
    text += "\n/* AIR-UBI-NOPON: leave the optical data/control paths disabled. */\n"
    for label in DISABLED:
        text += f'\n&{label} {{\n\tstatus = "disabled";\n}};\n'
    dts.write_text(text)
    network = source / NETWORK
    text = network.read_text()
    stanza = '\tnokia,xg-040g-md-ubi)\n\t\tucidef_set_interfaces_lan_wan "lan2 lan3 lan4" "lan1"\n\t\t;;'
    require(text.count(stanza) == 1, "Unexpected UBI network defaults; review port mapping")
    network.write_text(text.replace(stanza, stanza.replace(
        '"lan2 lan3 lan4" "lan1"', '"lan1 lan2 lan3" "lan4"')))
    feeds = source / "feeds.conf.default"
    lines = feeds.read_text().splitlines()
    removed = [line for line in lines if re.match(r"src-\S+\s+pon_(?:drivers|userspace)\s", line)]
    require(len(removed) == 2, "Expected both PON feeds in the pinned source")
    feeds.write_text("\n".join(line for line in lines if line not in removed) + "\n")
    (source / PON_DATA).unlink(missing_ok=True)
    report = {
        "variant": "ubi-nopon", "source_commit": commit,
        "wan": "lan4", "lan": ["lan1", "lan2", "lan3"],
        "optical_nodes_disabled": list(DISABLED), "removed_feeds": removed,
        "source_hashes": {name: digest(source / name) for name in (DTS, NETWORK, "feeds.conf.default")},
    }
    (source / POLICY).write_text(json.dumps(report, indent=2) + "\n")
    check_source(source)
    print("Prepared pinned bingoguo93 UBI variant: PON disabled; LAN4=WAN")


def check_source(source):
    report = json.loads((source / POLICY).read_text())
    require(report["source_commit"] == SOURCE_COMMIT, "No-PON source provenance changed")
    for name, expected in report["source_hashes"].items():
        require(digest(source / name) == expected, f"No-PON source settings changed: {name}")
    require(not (source / PON_DATA).exists(), "PON identity setup was reintroduced")


def check_config(source):
    check_source(source)
    selected = re.findall(r"^CONFIG_PACKAGE_(\S+)=[ym]$", (source / ".config").read_text(), re.M)
    forbidden = sorted(name for name in selected if PON_PACKAGE.match(name))
    require(not forbidden, f"PON packages selected in Ethernet-only build: {forbidden}")
    print("Resolved No-PON configuration verified")


def check_manifest(path):
    installed = {line.split(" - ", 1)[0] for line in path.read_text().splitlines()}
    forbidden = sorted(name for name in installed if PON_PACKAGE.match(name))
    require(not forbidden, f"PON packages installed in Ethernet-only image: {forbidden}")
    require({"kmod-usb3", "kmod-regulator-userspace-consumer"} <= installed,
            "USB support missing from the XG-040G-MD image")


def check_image(source):
    check_config(source)
    target = source / "bin/targets/airoha/an7581"
    manifests = list(target.glob("*-nokia_xg-040g-md-ubi*.manifest"))
    require(len(manifests) == 1, "Expected one No-PON image manifest")
    check_manifest(manifests[0])
    dtbs = list((source / "build_dir").glob(
        "target-*/linux-airoha_an7581/image-an7581-nokia_xg-040g-md-ubi.dtb"))
    require(dtbs, "Missing compiled UBI device tree")
    fdtget = shutil.which("fdtget")
    require(fdtget, "Install device-tree-compiler to verify the compiled No-PON device tree")
    for dtb in dtbs:
        for node in DISABLED.values():
            status = subprocess.check_output([str(fdtget), "-t", "s", str(dtb), node, "status"], text=True).strip()
            require(status == "disabled", f"PON path active in compiled device tree: {node}")
    report = json.loads((source / POLICY).read_text())
    report["compiled_device_trees"] = {dtb.name: digest(dtb) for dtb in dtbs}
    report["installed_manifest_sha256"] = digest(manifests[0])
    (source / POLICY).write_text(json.dumps(report, indent=2) + "\n")
    print("Installed packages and compiled No-PON device tree verified")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("prepare", "config", "image"))
    parser.add_argument("source", type=Path)
    args = parser.parse_args()
    {"prepare": prepare, "config": check_config, "image": check_image}[args.operation](args.source.resolve())


if __name__ == "__main__":
    main()
