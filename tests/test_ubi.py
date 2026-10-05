import importlib.util
import json
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("ubi", Path(__file__).resolve().parents[1] / "Scripts/UBI.py")
ubi = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ubi)


class GuardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.source = Path(self.temp.name)

    def write(self, path, text):
        target = self.source / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        return target

    def valid_config(self):
        symbols = {"CONFIG_TARGET_airoha", "CONFIG_TARGET_airoha_an7581", ubi.SYMBOL,
                   "CONFIG_TARGET_ROOTFS_INITRAMFS", *ubi.CRITICAL}
        text = "".join(f"{symbol}=y\n" for symbol in sorted(symbols))
        self.write(".config", text)
        self.write(".config.requested", text)
        return text

    def test_unknown_kernel_is_rejected(self):
        self.write("target/linux/airoha/Makefile", "KERNEL_PATCHVER:=6.19\n")
        with self.assertRaisesRegex(RuntimeError, "6.18 only"):
            ubi.install(self.source)

    def test_patch_install_is_idempotent(self):
        self.write("target/linux/airoha/Makefile", "KERNEL_PATCHVER:=6.18\n")
        self.write("target/linux/airoha/image/an7581.mk", f"define Device/{ubi.PROFILE}\n")
        destination = self.source / "target/linux/airoha/patches-6.18"
        destination.mkdir()
        ubi.install(self.source)
        ubi.install(self.source)
        self.assertTrue((destination / ubi.PATCH).is_file())
        self.write("target/linux/airoha/patches-6.18/another.patch", "+spinand_read_page_wait\n")
        with self.assertRaisesRegex(RuntimeError, "Another Robust Read"):
            ubi.install(self.source)

    def test_legacy_profile_is_rejected(self):
        text = self.valid_config()
        self.write(".config", text + "CONFIG_TARGET_DEVICE_airoha_an7581_DEVICE_nokia_xg-040g-md=y\n")
        with self.assertRaisesRegex(RuntimeError, "Unexpected devices"):
            ubi.check_config(self.source)

    def test_missing_critical_package_is_rejected(self):
        text = self.valid_config().replace("CONFIG_PACKAGE_luci-app-openclash=y\n", "")
        self.write(".config", text)
        with self.assertRaisesRegex(RuntimeError, "luci-app-openclash"):
            ubi.check_config(self.source)

    def test_nopon_build_cannot_skip_its_policy(self):
        self.valid_config()
        with patch.dict(ubi.os.environ, {"WRT_CONFIG": "AIROHA-UBI-NOPON"}):
            with self.assertRaisesRegex(RuntimeError, "No-PON policy missing"):
                ubi.check_config(self.source)

    def test_hidden_fitblk_in_per_device_rootfs_is_accepted(self):
        text = self.valid_config().replace("CONFIG_PACKAGE_fitblk=y", "CONFIG_PACKAGE_fitblk=m")
        for selection in ("m", "y"):
            self.write(".config", text + f"CONFIG_TARGET_PER_DEVICE_ROOTFS=y\nCONFIG_MODULE_DEFAULT_fitblk={selection}\n")
            ubi.check_config(self.source)
        self.write(".config", text)
        with self.assertRaisesRegex(RuntimeError, "fitblk"):
            ubi.check_config(self.source)

    def test_optional_package_loss_is_reported(self):
        text = self.valid_config()
        self.write(".config.requested", text + "CONFIG_PACKAGE_optional-test=y\n")
        ubi.check_config(self.source)
        report = json.loads((self.source / "package-selection-changes.json").read_text())
        self.assertEqual(report["CONFIG_PACKAGE_optional-test"]["resolved"], "n")

    def test_truncated_fit_is_rejected(self):
        image = self.source / "test.itb"
        image.write_bytes(struct.pack(">II", 0xD00DFEED, 1000) + bytes(32))
        with self.assertRaisesRegex(RuntimeError, "Invalid FIT"):
            ubi.check_fit(image)

    def prepared_kernel(self):
        kernel = "build_dir/target-aarch64/linux-airoha_an7581/linux-6.18.54"
        self.write(f"{kernel}/.config", "CONFIG_MTD_SPI_NAND=y\n")
        core = """static int spinand_read_page_wait(struct spinand_device *spinand, u8 *s)
{
 unsigned long timeo = jiffies + msecs_to_jiffies(400);
 spinand_read_status(spinand, &status);
 spinand_read_status(spinand, &status);
 return -ETIMEDOUT;
}
if (spinand->id.data[0] == 0x01)
 spinand_read_page_wait(spinand, &status);
if (spinand->id.data[0] == 0x01)
 spinand_read_page_wait(spinand, &status);
"""
        return self.write(f"{kernel}/drivers/mtd/nand/spi/core.c", core)

    def test_missing_continuous_read_fix_is_rejected(self):
        core = self.prepared_kernel()
        core.write_text(core.read_text().replace("spinand_read_page_wait(spinand, &status);", "", 1))
        with self.assertRaisesRegex(RuntimeError, "normal and continuous"):
            ubi.check_kernel(self.source)

    def valid_images(self):
        self.valid_config()
        self.prepared_kernel()
        target = self.source / "bin/targets/airoha/an7581"
        target.mkdir(parents=True)
        name = f"immortalwrt-airoha-an7581-{ubi.PROFILE}-squashfs-sysupgrade.itb"
        image = target / name
        image.write_bytes(struct.pack(">II", 0xD00DFEED, 40) + bytes(32))
        recovery = target / f"immortalwrt-airoha-an7581-{ubi.PROFILE}-initramfs-recovery.itb"
        recovery.write_bytes(image.read_bytes())
        self.write("bin/targets/airoha/an7581/profiles.json", json.dumps({
            "target": "airoha/an7581", "profiles": {ubi.PROFILE: {
                "supported_devices": [ubi.DEVICE],
                "images": [{"type": "sysupgrade", "name": name, "sha256": ubi.sha256(image)}],
            }}}))
        for name in ("config.buildinfo", "feeds.buildinfo", "version.buildinfo"):
            self.write(f"bin/targets/airoha/an7581/{name}", "test\n")
        self.write(f"bin/targets/airoha/an7581/immortalwrt-{ubi.PROFILE}-squashfs.manifest",
                   "".join(f"{key.removeprefix('CONFIG_PACKAGE_')} - 1.0\n" for key in ubi.CRITICAL))
        return image

    def test_image_checksum_mismatch_is_rejected(self):
        image = self.valid_images()
        image.write_bytes(image.read_bytes() + b"corruption")
        with self.assertRaisesRegex(RuntimeError, "SHA256"):
            ubi.package(self.source)

    def test_wrong_firmware_metadata_is_rejected(self):
        self.valid_images()
        def fwtool(args, check):
            Path(args[2]).write_text(json.dumps({"supported_devices": ["nokia,xg-040g-md"]}))
        with patch.object(ubi.subprocess, "run", side_effect=fwtool):
            with self.assertRaisesRegex(RuntimeError, "metadata"):
                ubi.package(self.source)

    def test_valid_release_keeps_names_and_checksums(self):
        image = self.valid_images()
        def fwtool(args, check):
            Path(args[2]).write_text(json.dumps({"supported_devices": [ubi.DEVICE]}))
        with patch.object(ubi.subprocess, "run", side_effect=fwtool), \
             patch.object(ubi.subprocess, "check_output", return_value="test-commit\n"):
            ubi.package(self.source)
        output = self.source / "upload"
        self.assertTrue((output / image.name).exists())
        for line in (output / "SHA256SUMS").read_text().splitlines():
            digest, name = line.split("  ", 1)
            self.assertEqual(digest, ubi.sha256(output / name))
        self.assertTrue((output / "build-provenance.json").exists())

    def test_fitblk_missing_from_image_is_rejected(self):
        self.valid_images()
        manifest = next((self.source / "bin/targets/airoha/an7581").glob("*.manifest"))
        manifest.write_text(manifest.read_text().replace("fitblk - 1.0\n", ""))
        def fwtool(args, check):
            Path(args[2]).write_text(json.dumps({"supported_devices": [ubi.DEVICE]}))
        with patch.object(ubi.subprocess, "run", side_effect=fwtool):
            with self.assertRaisesRegex(RuntimeError, "missing from image.*fitblk"):
                ubi.package(self.source)


if __name__ == "__main__":
    unittest.main()
