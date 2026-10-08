import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("nopon", Path(__file__).resolve().parents[1] / "Scripts/NoPON.py")
nopon = importlib.util.module_from_spec(spec)
spec.loader.exec_module(nopon)


class NoPONTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.source = Path(self.temp.name)

    def write(self, name, text):
        path = self.source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def prepare(self):
        self.write(nopon.DTS, '#include "an7581-nokia_xg-040g-md-common.dtsi"\n'
                   '#include "an758x-nokia_xg-040g-ubi-parts.dtsi"\n')
        self.write(nopon.NETWORK, '\tnokia,xg-040g-md-ubi)\n'
                   '\t\tucidef_set_interfaces_lan_wan "lan2 lan3 lan4" "lan1"\n\t\t;;\n')
        self.write("feeds.conf.default", "src-git packages normal\n"
                   "src-git pon_drivers optical-drivers\nsrc-git pon_userspace optical-userspace\n")
        self.write(nopon.PON_DATA, "PON board setup\n")
        with patch.object(nopon.subprocess, "check_output", return_value=nopon.SOURCE_COMMIT + "\n"):
            nopon.prepare(self.source)

    def test_preparation_preserves_ethernet_and_is_idempotent(self):
        self.prepare()
        before = (self.source / nopon.DTS).read_text()
        with patch.object(nopon.subprocess, "check_output", return_value=nopon.SOURCE_COMMIT + "\n"):
            nopon.prepare(self.source)
        self.assertEqual((self.source / nopon.DTS).read_text(), before)
        self.assertIn('"lan1 lan2 lan3" "lan4"', (self.source / nopon.NETWORK).read_text())
        self.assertEqual((self.source / "feeds.conf.default").read_text(), "src-git packages normal\n")
        self.assertFalse((self.source / nopon.PON_DATA).exists())

    def test_different_source_commit_is_rejected(self):
        with patch.object(nopon.subprocess, "check_output", return_value="new-upstream\n"):
            with self.assertRaisesRegex(RuntimeError, "source commit"):
                nopon.prepare(self.source)

    def test_pon_dependency_cannot_reappear_after_defconfig(self):
        self.prepare()
        self.write(".config", "CONFIG_PACKAGE_kmod-usb3=m\nCONFIG_PACKAGE_kmod-airoha-xpon=m\n")
        with self.assertRaisesRegex(RuntimeError, "PON packages selected"):
            nopon.check_config(self.source)

    def test_modified_device_tree_is_rejected(self):
        self.prepare()
        self.write(nopon.DTS, (self.source / nopon.DTS).read_text().replace('"disabled"', '"okay"'))
        self.write(".config", "CONFIG_PACKAGE_kmod-usb3=m\n")
        with self.assertRaisesRegex(RuntimeError, "source settings changed"):
            nopon.check_config(self.source)

    def test_installed_pon_package_is_rejected(self):
        manifest = self.write("image.manifest", "kmod-usb3 - 1\nkmod-regulator-userspace-consumer - 1\nairoha-pond - 1\n")
        with self.assertRaisesRegex(RuntimeError, "PON packages installed"):
            nopon.check_manifest(manifest)

    def test_shared_npu_and_phy_packages_are_allowed(self):
        manifest = self.write("image.manifest", "kmod-usb3 - 1\nkmod-regulator-userspace-consumer - 1\n"
                              "airoha-en7581-npu-firmware - 1\nkmod-phy-airoha-en8811h - 1\n")
        nopon.check_manifest(manifest)

    def test_compiled_optical_node_must_be_disabled(self):
        self.prepare()
        self.write(".config", "CONFIG_PACKAGE_kmod-usb3=m\n")
        manifest = self.write("final-image.manifest",
                              "kmod-usb3 - 1\nkmod-regulator-userspace-consumer - 1\n")
        self.write("build_dir/target-test/linux-airoha_an7581/image-an7581-nokia_xg-040g-md-ubi.dtb", "test")
        with patch.object(nopon.shutil, "which", return_value="fdtget"), \
             patch.object(nopon.subprocess, "check_output", return_value="okay\n"):
            with self.assertRaisesRegex(RuntimeError, "PON path active"):
                nopon.check_image(self.source, manifest)
        with patch.object(nopon.shutil, "which", return_value="fdtget"), \
             patch.object(nopon.subprocess, "check_output", return_value="disabled\n"):
            nopon.check_image(self.source, manifest)
        report = json.loads((self.source / nopon.POLICY).read_text())
        self.assertIn("installed_manifest_sha256", report)


if __name__ == "__main__":
    unittest.main()
