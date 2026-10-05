"""Exercise the release check against a real externally stored FIT SquashFS."""
import importlib.util
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location("ubi_image", Path(__file__).resolve().parents[1] / "Scripts/UBI.py")
ubi = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ubi)

TOOLS = ("fdtget", "mksquashfs", "unsquashfs", "mkimage")


@unittest.skipUnless(all(shutil.which(tool) for tool in TOOLS), "FIT/SquashFS tools required")
class ImageManifestIntegrationTests(unittest.TestCase):
    def test_final_fit_package_database_for_apk_and_opkg(self):
        for apk in (True, False):
            with self.subTest(apk=apk), tempfile.TemporaryDirectory() as temporary:
                source = Path(temporary)
                (source / ".config").write_text("CONFIG_USE_APK=y\n" if apk else "# CONFIG_USE_APK is not set\n")
                rootfs = source / "rootfs"
                database = rootfs / ("lib/apk/db/installed" if apk else "usr/lib/opkg/status")
                database.parent.mkdir(parents=True)
                database.write_text(
                    "P:fitblk\nV:1.0-r1\nA:aarch64_generic\n\nP:kmod-usb3\nV:6.18.52-r1\n"
                    if apk else "Package: fitblk\nVersion: 1.0-r1\nStatus: install ok installed\n\n"
                    "Package: old-package\nVersion: 1\nStatus: deinstall ok config-files\n\n"
                    "Package: kmod-usb3\nVersion: 6.18.52-r1\nStatus: install ok installed\n")
                subprocess.run(["mksquashfs", str(rootfs), str(source / "rootfs.squashfs"),
                                "-noappend", "-comp", "xz", "-processors", "1", "-quiet"],
                               check=True, capture_output=True)
                its = source / "image.its"
                its.write_text('''/dts-v1/;
/ {
    description = "UBI manifest integration test";
    #address-cells = <1>;
    images {
        rootfs-1 {
            data = /incbin/("rootfs.squashfs");
            type = "filesystem";
            arch = "arm64";
            compression = "none";
            hash-1 { algo = "sha256"; };
        };
    };
    configurations {
        default = "config-1";
        config-1 { loadables = "rootfs-1"; };
    };
};
''')
                image = source / "sysupgrade.itb"
                subprocess.run(["mkimage", "-E", "-B", "0x1000", "-p", "0x1000",
                                "-f", str(its), str(image)], check=True, capture_output=True)
                # sysupgrade metadata is appended after FIT data by the build.
                with image.open("ab") as firmware:
                    firmware.write(b"appended-sysupgrade-metadata")
                host = source / "staging_dir/host/bin"
                host.mkdir(parents=True)
                (host / "unsquashfs4").symlink_to(shutil.which("unsquashfs"))
                manifest = source / "device.manifest"
                ubi.check_fit(image)
                ubi.image_manifest(source, image, manifest)
                self.assertEqual(manifest.read_text(), "fitblk - 1.0-r1\nkmod-usb3 - 6.18.52-r1\n")


if __name__ == "__main__":
    unittest.main()
